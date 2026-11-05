"""Live-Panel der Zentrale: offene Gebote + „Wege vor uns" + Feed-Cache.

- ``GET /api/bids``      offene Gebote LIVE von Kickbase (~3 Calls hinter dem
                         1-req/s-Limiter ⇒ ~3 s) + Kaufkraft nach der 33-%-Regel.
                         60-s-Cache auf Modulebene, damit Polling die API nie flutet.
- ``GET /api/pathways``  bedingte Pfade je offenem Gebot (intel/pathways) —
                         Basis ist der /api/bids-Cache, Kandidaten aus der DB.
- ``cached_feed()``      60-s-Cache des Activity-Feeds für ``GET /api/protocol``
                         (Liga-Ereignisse) — lädt beim ersten Aufruf live, fällt
                         ohne Kickbase-Zugang IMMER auf ``[]`` zurück, nie Crash.

Nur GET, kein Kickbase-Write, keine DB-Mutation. Fehler werden nie als
Traceback sichtbar: die Routen antworten mit 502 + ``detail``.

Loop-Hinweis: Die async Routen laufen im Server-Event-Loop und nutzen den
geteilten TokenCache (kickbase/factory). ``cached_feed()`` wird dagegen aus dem
SYNC ``get_protocol`` (FastAPI-Threadpool) gerufen — dort läuft ein EIGENER
``asyncio.run``-Loop, und der geteilte TokenCache (asyncio.Lock, an den
Server-Loop gebunden) ist tabu. Der Standalone-Abruf meldet sich deshalb frisch
an (nur Token-Modus, 1 Zusatz-Request) — im Passwort-Modus füllt stattdessen
der /api/bids-Abruf den Feed-Cache mit.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.config import get_settings
from kickbase_agent.db.models import Intent
from kickbase_agent.intel.pathways import build_pathways
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.mcp._util import club_name, money_fields, parse_int
from kickbase_agent.server.deps import db_session

router = APIRouter()
log = logging.getLogger(__name__)

#: Lebensdauer der Modul-Caches (Gebote-Payload + Feed) in Sekunden.
CACHE_TTL_S = 60.0
#: Feed-Fenster für Protokoll-Ereignisse (get_activity_feed max_items).
FEED_CACHE_MAX_ITEMS = 200
#: Jüngste Kauf-Vorhaben, aus denen die Kandidaten-Namen der Pfade kommen.
RECENT_BID_INTENTS = 8

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


# ------------------------------------------------------------------ Cache


def _cache_get(key: str) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        stamp, value = entry
        if time.monotonic() - stamp > CACHE_TTL_S:
            return None
        return value


def _cache_put(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)


def reset_caches() -> None:
    """Alle Modul-Caches leeren (Tests + gezielter Neuabruf)."""
    with _cache_lock:
        _cache.clear()


# ------------------------------------------------------------------ Kickbase-Abruf


async def _fetch_bids_payload() -> dict[str, Any]:
    """Gebote + Kontolage live lesen (3 Calls) und als /api/bids-Payload aufbereiten."""
    client, auth = await make_kickbase_client()
    settings = get_settings()
    league_id = str(settings.kickbase_league_id or auth.league_id or "")
    if not league_id:
        raise RuntimeError("Keine Liga konfiguriert (KICKBASE_LEAGUE_ID in api/.env setzen).")
    raw_bids = await client.get_own_bids(league_id, str(auth.user_id or ""))
    budget = int(await client.get_budget(league_id))
    squad = await client.get_squad(league_id)
    team_value = sum(int(sp.player.market_value or 0) for sp in squad)

    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for b in sorted(raw_bids, key=lambda x: parse_int(x.get("expires_in_s"))):
        amount = parse_int(b.get("amount"))
        listing = parse_int(b.get("listing_price"))
        exs = parse_int(b.get("expires_in_s"))
        rows.append(
            {
                "player_id": str(b.get("player_id", "")),
                "name": b.get("name", ""),
                # Volle CDN-URL (wie Cockpit-Rows) — leer = Client zeigt Initialen.
                "img": (
                    f"https://kickbase.b-cdn.net/{b.get('image_path')}"
                    if b.get("image_path") else ""
                ),
                "club": club_name(b.get("team_id", "")),
                "team_id": str(b.get("team_id", "")),
                **money_fields("amount", amount),
                **money_fields("listing_price", listing),
                "amount_vs_listing_pct": round(100 * amount / listing, 1) if listing else None,
                "seller": b.get("seller", ""),
                # Manager-Listing: Verkäufer entscheidet manuell, kein Countdown.
                "listing_type": "manager" if b.get("seller") else "kickbase",
                "expires_in_s": exs,
                "expires_at": (now + timedelta(seconds=exs)).isoformat() if exs > 0 else None,
                "offer_id": str(b.get("offer_id", "")),
            }
        )
    exposure = sum(r["amount_eur"] or 0 for r in rows)
    # Kaufkraft = Konto − offene Gebote + ⅓ Teamwert (33-%-Regel, CLAUDE.md §6).
    buying_power = budget - exposure + team_value // 3

    # Feed-Cache im selben Rutsch füllen (1 Zusatz-Call) — so bekommt das
    # Protokoll auch im Passwort-Modus Liga-Ereignisse; Fehler sind egal.
    if not _cache_get("feed"):
        try:
            feed = await client.get_activity_feed(league_id, max_items=FEED_CACHE_MAX_ITEMS)
            _cache_put("feed", list(feed))
        except Exception:  # noqa: BLE001 — Feed ist Zusatz, nie ein Gebote-Blocker
            log.warning("Feed-Refresh im /api/bids-Abruf fehlgeschlagen", exc_info=True)

    return {
        "league_id": league_id,
        "fetched_at": now.isoformat(),
        "cache_ttl_s": int(CACHE_TTL_S),
        "count": len(rows),
        "bids": rows,
        **money_fields("exposure", exposure),
        **money_fields("budget", budget),
        **money_fields("team_value", team_value),
        **money_fields("buying_power", buying_power),
    }


async def _bids_payload_cached() -> dict[str, Any]:
    """Payload aus dem 60-s-Cache; bei Bedarf live nachladen und cachen."""
    payload = _cache_get("bids")
    if payload is not None:
        return payload
    payload = await _fetch_bids_payload()
    _cache_put("bids", payload)
    return payload


def _detail_502(prefix: str, exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=f"{prefix}: {type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ Feed-Cache


async def _fetch_feed_standalone(max_items: int) -> list[dict[str, Any]]:
    """Feed mit FRISCHER Anmeldung lesen (eigener Event-Loop, s. Modul-Docstring).

    Nutzt bewusst NICHT den geteilten TokenCache: dessen asyncio.Lock ist an den
    Server-Loop gebunden und würde aus einem ``asyncio.run``-Loop crashen.
    """
    from kickbase_agent.kickbase.auth import authenticate
    from kickbase_agent.kickbase.client import KickbaseClient

    result = await authenticate()
    settings = get_settings()
    league_id = str(
        settings.kickbase_league_id or (result.leagues[0].id if result.leagues else "") or ""
    )
    if not league_id:
        return []
    client = KickbaseClient(token=result.token)
    return list(await client.get_activity_feed(league_id, max_items=max_items))


def cached_feed(max_items: int = FEED_CACHE_MAX_ITEMS) -> list[dict[str, Any]]:
    """Activity-Feed aus dem 60-s-Cache — sync, für ``GET /api/protocol``.

    Erster Aufruf lädt live (nur Token-Modus, s. Modul-Docstring); jeder Fehler
    und der Passwort-Modus liefern ``[]``. Auch ein Fehlschlag wird 60 s
    gecacht, damit ein gestörter Kickbase-Zugang das Protokoll nicht bei jedem
    Aufruf um einen Timeout verlangsamt.
    """
    items = _cache_get("feed")
    if items is not None:
        return list(items)
    fetched: list[dict[str, Any]] = []
    try:
        if get_settings().kickbase_token:
            fetched = asyncio.run(_fetch_feed_standalone(max_items))
    except Exception:  # noqa: BLE001 — Protokoll darf am Feed nie scheitern
        log.warning("Feed-Abruf fürs Protokoll fehlgeschlagen", exc_info=True)
        fetched = []
    _cache_put("feed", list(fetched))
    return list(fetched)


# ------------------------------------------------------------------ Routen


@router.get("/bids")
async def get_bids() -> dict[str, Any]:
    """Offene Gebote live (60-s-Cache): Zeilen wie das MCP-Tool ``own_bids``
    (name, amount, listing_price, seller, listing_type, expires_in_s) plus
    ``expires_at`` (ISO) sowie exposure/budget/team_value/buying_power je als
    ``*_eur`` + ``*_fmt``. ``cached`` sagt, ob die Antwort aus dem Cache kam."""
    cached = _cache_get("bids") is not None
    try:
        payload = await _bids_payload_cached()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — nie ein Traceback Richtung Web-App
        log.warning("GET /api/bids fehlgeschlagen", exc_info=True)
        raise _detail_502("Offene Gebote nicht lesbar", exc) from exc
    return {"now": datetime.now(UTC).isoformat(), "cached": cached, **payload}


@router.get("/pathways")
async def get_pathways(session: Session = Depends(db_session)) -> dict[str, Any]:
    """„Wege vor uns": je offenem Gebot ein Zuschlag- und ein Kein-Zuschlag-Pfad
    plus die Extremfälle (Format: intel/pathways.build_pathways). Antwort:
    ``{now, count, pathways}``."""
    try:
        payload = await _bids_payload_cached()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — nie ein Traceback Richtung Web-App
        log.warning("GET /api/pathways fehlgeschlagen (Gebote-Abruf)", exc_info=True)
        raise _detail_502("Wege nicht berechenbar — offene Gebote nicht lesbar", exc) from exc

    planned_rows = (
        session.execute(
            select(Intent)
            .where(Intent.status == "planned")
            .order_by(Intent.execute_at.asc(), Intent.id.asc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    recent_bid_rows = (
        session.execute(
            select(Intent)
            .where(Intent.kind == "bid")
            .order_by(Intent.updated_at.desc(), Intent.id.desc())
            .limit(RECENT_BID_INTENTS)
        )
        .scalars()
        .all()
    )
    planned = [
        {
            "kind": i.kind,
            "player_name": i.player_name,
            "amount_eur": int(i.amount or 0),
        }
        for i in planned_rows
    ]
    candidates = [i.player_name for i in recent_bid_rows if i.player_name]
    pathways = build_pathways(
        payload.get("bids") or [],
        parse_int(payload.get("budget_eur")),
        parse_int(payload.get("team_value_eur")),
        planned,
        candidates,
    )
    return {
        "now": datetime.now(UTC).isoformat(),
        "count": len(pathways),
        "pathways": pathways,
    }
