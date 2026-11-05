"""Liga-Transfers aus dem Activity-Feed als Protokoll-Einträge (Emil, 23.08. abends).

Der Feed (``KickbaseClient.get_activity_feed``) liefert je Item ``t`` (Typ),
``dt`` (ISO-Zeitstempel) und ``data``:

- ``t=15`` Transfer: ``byr`` (Käufer) ODER ``slr`` (Verkäufer), ``pn`` Spieler,
  ``trp`` Preis — dieselbe Quelle wie ``trading/wallet.parse_transfers``.
- ``t=22`` Prämie:  ``bn`` Betrag, ``day`` Login-Tag (nur eigene Ereignisse).

``league_events`` ist eine REINE Funktion (kein I/O): sie baut daraus Einträge im
Protokoll-Basisformat ``{kind, ts, title, why, sources, status, emil}``:

- eigener Kauf   → kind ``transfer``, „Zuschlag: <Spieler> für X", status ``zuschlag``
- eigener Verkauf → kind ``transfer``, „Verkauft: <Spieler> für X", status ``verkauft``
- eigene Prämie  → kind ``transfer``, „Prämie kassiert (+X)",       status ``praemie``
- fremder Handel → kind ``league``,  „Liga: <Manager> kauft/verkauft …", status ``liga``

„Eigen" heißt: ``byr``/``slr`` entspricht ``me`` (Anzeigename im Feed,
Groß-/Kleinschreibung egal — Standard „emil", vgl. trading/trade_budget).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kickbase_agent.mcp._util import fmt_money, parse_int

#: Feed-Typen (live belegt, s. tests/worker/test_bonus_job.py + trading-Tests).
FEED_TRANSFER = 15
FEED_BONUS = 22


def _ts(item: dict[str, Any]) -> str | None:
    """``dt`` als normalisierter ISO-String mit Zeitzone — None, wenn unbrauchbar."""
    raw = item.get("dt")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # Feed liefert immer Zonen-Zeit („…Z") — naiv wäre Fremddatum
    return parsed.isoformat()


def _base(kind: str, ts: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": kind,
        "ts": ts,
        "title": "",
        "why": "",
        "sources": {},
        "status": "",
        "emil": "",
        "feed_id": str(item.get("i") or ""),
    }


def _transfer_entry(item: dict[str, Any], ts: str, me: str) -> dict[str, Any] | None:
    data = item.get("data") or {}
    if not isinstance(data, dict):
        return None
    price = parse_int(data.get("trp"))
    player = str(data.get("pn") or "?").strip() or "?"
    buyer = str(data.get("byr") or "").strip()
    seller = str(data.get("slr") or "").strip()
    if not buyer and not seller:
        return None
    money = fmt_money(price)
    mine = me.casefold()

    if buyer and buyer.casefold() == mine:
        entry = _base("transfer", ts, item)
        entry.update(
            title=f"Zuschlag: {player} für {money}",
            why=f"Unser Gebot hat den Zuschlag erhalten — {player} ist jetzt im Kader.",
            sources={"price": price, "price_fmt": money, "buyer": buyer},
            status="zuschlag",
        )
    elif seller and seller.casefold() == mine:
        entry = _base("transfer", ts, item)
        entry.update(
            title=f"Verkauft: {player} für {money}",
            why=f"Verkauf abgeschlossen — {money} sind dem Konto gutgeschrieben.",
            sources={"price": price, "price_fmt": money, "seller": seller},
            status="verkauft",
        )
    elif buyer:
        entry = _base("league", ts, item)
        entry.update(
            title=f"Liga: {buyer} kauft {player} für {money}",
            why=f"Wallet-Signal: {buyer} hat jetzt {money} weniger Kaufkraft.",
            sources={"price": price, "price_fmt": money, "buyer": buyer},
            status="liga",
        )
    else:
        entry = _base("league", ts, item)
        entry.update(
            title=f"Liga: {seller} verkauft {player} für {money}",
            why=f"Wallet-Signal: {seller} hat jetzt {money} mehr Kaufkraft.",
            sources={"price": price, "price_fmt": money, "seller": seller},
            status="liga",
        )
    entry["player_name"] = player
    entry["amount"] = price
    entry["amount_fmt"] = money
    return entry


def _bonus_entry(item: dict[str, Any], ts: str) -> dict[str, Any] | None:
    data = item.get("data") or {}
    if not isinstance(data, dict):
        return None
    amount = parse_int(data.get("bn"))
    day = data.get("day")
    money = fmt_money(amount)
    entry = _base("transfer", ts, item)
    entry.update(
        title=f"Prämie kassiert (+{money})",
        why=(
            f"Tägliche Auflaufprämie (Tag {day}) dem Konto gutgeschrieben."
            if day is not None
            else "Tägliche Auflaufprämie dem Konto gutgeschrieben."
        ),
        sources={"bonus": amount, "bonus_fmt": money} | ({"tag": day} if day is not None else {}),
        status="praemie",
    )
    entry["amount"] = amount
    entry["amount_fmt"] = money
    return entry


def league_events(
    feed_items: list[dict[str, Any]],
    me: str = "emil",
    bids: list[dict[str, Any]] | None = None,  # reserviert für Gebots-Abgleich (s. Docstring)
) -> list[dict[str, Any]]:
    """Feed-Items → Protokoll-Einträge (Transfers + Prämien), neueste zuerst.

    Andere Feed-Typen (t=3 Markt-Events …) und Items ohne parsebares ``dt``
    werden übersprungen. ``bids`` ist für einen späteren Abgleich „Gebot
    verloren an <Manager>" reserviert und wird derzeit nicht ausgewertet.
    """
    events: list[dict[str, Any]] = []
    for item in feed_items or []:
        if not isinstance(item, dict):
            continue
        ts = _ts(item)
        if ts is None:
            continue
        kind = item.get("t")
        entry: dict[str, Any] | None = None
        if kind == FEED_TRANSFER:
            entry = _transfer_entry(item, ts, me)
        elif kind == FEED_BONUS:
            entry = _bonus_entry(item, ts)
        if entry is not None:
            events.append(entry)
    events.sort(key=lambda e: e["ts"], reverse=True)
    return events
