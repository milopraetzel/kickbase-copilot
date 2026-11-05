"""Read-Tools (ohne Nebenwirkung): Liga-Lage, Kader, Markt, Gebote, Spieler, Feed, Audit, Tipps.

Jeder Aufruf baut seinen Client über `make_kickbase_client()` (TokenCache
cached den Login). Rückgaben sind kompakte JSON-dicts: Geld als `*_eur` (int)
plus `*_fmt` („7,41 M"); Fehler landen über `tool_guard` als {"error","hint"}.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from kickbase_agent.cli.research import _dump_data
from kickbase_agent.config import get_settings
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.decision.lineup_service import recommend_lineup as _recommend_lineup
from kickbase_agent.intel.collector import collect_from_players
from kickbase_agent.intel.community import apply_community_boost, load_recent_tips
from kickbase_agent.intel.history import load_history, load_performance
from kickbase_agent.kickbase import endpoints
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.kickbase.models import MarketPlayer, Player, SquadPlayer
from kickbase_agent.knowledge import build_learning
from kickbase_agent.mcp._data import read_audit, read_tips
from kickbase_agent.mcp._remote import read_remote
from kickbase_agent.mcp._util import (
    BERLIN,
    ToolError,
    club_name,
    fmt_money,
    iso,
    lineup_label,
    money_fields,
    parse_int,
    pos_name,
    tool_guard,
)
from kickbase_agent.scouts.ligainsider.teams import TEAMS
from kickbase_agent.trading.finance import max_single_bid, overdraft_floor
from kickbase_agent.trading.finance_cycle import finance_phase
from kickbase_agent.trading.trade_budget import (
    FEED_MAX_ITEMS,
    budget_line,
    get_trade_usage,
    trades_blocked,
)
from kickbase_agent.worker.kickoff_guard import matchday_deadline, next_kickoff

#: Kickbase-Bild-CDN (live verifiziert, s. cli/cockpit/data.py).
_CDN = "https://kickbase.b-cdn.net"
_MARKET_SORTS = ("score", "mv", "mv24", "price", "expires", "prob")
#: Feed-Typen (LEARNINGS): 15 = Transfer (byr/slr + trp + pn), 3 = Markt-Event, 22 = Prämie.
_FEED_TRANSFER, _FEED_MARKET_EVENT, _FEED_BONUS = 15, 3, 22


async def _ctx() -> tuple[Any, str, str]:
    """(client, league_id, user_id) — Liga aus Settings, sonst die priorisierte Account-Liga."""
    client, auth = await make_kickbase_client()
    settings = get_settings()
    league_id = settings.kickbase_league_id or auth.league_id
    if not league_id:
        raise ToolError(
            "Keine Liga konfiguriert.",
            hint="KICKBASE_LEAGUE_ID in api/.env setzen (kba login zeigt die Account-Ligen).",
        )
    user_id = str(auth.user_id or getattr(settings, "kickbase_user_id", "") or "")
    return client, str(league_id), user_id


async def _budget_after_bids(client: Any, league_id: str) -> int | None:
    """`pbaa` aus /me/budget (Konto nach allen offenen Geboten), None wenn nicht lesbar."""
    read_fields = getattr(client, "get_budget_fields", None)
    if read_fields is None:
        return None
    try:
        fields = await read_fields(league_id)
    except Exception:  # noqa: BLE001 — Zusatzinfo, kein Blocker
        return None
    value = fields.get("pbaa") if isinstance(fields, dict) else None
    return None if value is None else parse_int(value)


def _img(image_path: str) -> str:
    return f"{_CDN}/{image_path}" if image_path else ""


def _player_base(p: Player) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": f"{p.first_name} {p.last_name}".strip(),
        "pos": pos_name(p.position),
        "club": club_name(p.team_id),
        "team_id": p.team_id,
        **money_fields("mv", p.market_value),
        **money_fields("mv24", p.mv_change_24h),
        "mv_trend": p.market_value_trend,
        "lineup_prob": p.lineup_prob,
        "img": _img(p.image_path),
    }


# ------------------------------------------------------------------ league_state


@tool_guard
async def league_state() -> dict[str, Any]:
    """Liga-Lage: Konto, Teamwert, Kadergröße, Trade-Ampel (x/200), Finanz-Phase
    (invest Mo–Do / purge Fr / hold Sa–So), Kaufkraft (33-%-Regel) und nächster Anpfiff."""
    t0 = time.monotonic()
    client, league_id, user_id = await _ctx()
    now = datetime.now(UTC)
    budget = await client.get_budget(league_id)
    squad = await client.get_squad(league_id)
    team_value = sum(sp.player.market_value for sp in squad)
    bids = await client.get_own_bids(league_id, user_id)
    exposure = sum(parse_int(b.get("amount")) for b in bids)
    # Kickbase' eigene Größe für die 33-%-Regel: Konto nach Annahme aller offenen
    # Gebote (`pbaa`, Live-Fund 23.08.) — fehlt sie, rechnen wir Konto − Exposure.
    after_bids = await _budget_after_bids(client, league_id)
    if after_bids is None:
        after_bids = budget - exposure
    usage = await get_trade_usage(client, league_id, user_id)
    fixtures = await client.get_matchdays()
    limit = DEFAULT_GUARDRAILS.league_trade_limit_per_half
    deadline = matchday_deadline(fixtures, now)
    kickoff = next_kickoff(fixtures, now)
    kaufkraft = max_single_bid(budget, team_value)
    floor = overdraft_floor(team_value, budget)
    return {
        "league_id": league_id,
        **money_fields("budget", budget),
        **money_fields("budget_after_bids", after_bids),
        **money_fields("team_value", team_value),
        "squad_count": len(squad),
        "open_bids": len(bids),
        **money_fields("open_bid_exposure", exposure),
        # Höchstgebot für EINEN Spieler nach der 33-%-Regel — ohne/mit offenen Geboten.
        **money_fields("kaufkraft", kaufkraft),
        **money_fields("kaufkraft_after_bids", max(0, after_bids - floor)),
        **money_fields("overdraft_floor", floor),
        "phase": finance_phase(now.astimezone(BERLIN).weekday()),
        "trade_budget": {
            "used": usage.used,
            "limit": limit,
            "saturated": usage.count_saturated,
            "blocked": trades_blocked(usage.used, limit, usage.count_saturated),
            "line": budget_line(usage.used, limit, usage.count_saturated),
            "my_name": usage.my_name,
        },
        "next_kickoff": iso(kickoff),
        "matchday_deadline": (
            None if deadline is None else {"matchday": deadline[0], "at": iso(deadline[1])}
        ),
        "now": iso(now),
        "duration_s": round(time.monotonic() - t0, 1),
    }


# ------------------------------------------------------------------ squad


@tool_guard
async def squad() -> dict[str, Any]:
    """Eigener Kader: id, name, pos, club, mv, mv24, lineup_prob (KB-Stufe 1=sicher … 5=Bank),
    fitness_status (0 fit, 2 angeschlagen), in_lineup (`lo`), listed (steht am Markt), img."""
    client, league_id, _user_id = await _ctx()
    raw = await client._get(endpoints.squad(league_id))
    market_raw = await client._get(endpoints.market(league_id))
    market_ids = {str(it.get("i")) for it in market_raw.get("it", [])}
    players: list[dict[str, Any]] = []
    for it in raw.get("it", []):
        sp = SquadPlayer.from_api(it)
        players.append(
            {
                **_player_base(sp.player),
                "fitness_status": sp.fitness_status,
                "in_lineup": sp.lineup_order is not None,
                "lineup_order": sp.lineup_order,
                "listed": sp.player.id in market_ids,
                "points": sp.player.points,
            }
        )
    players.sort(key=lambda p: (-(p["mv_eur"] or 0)))
    team_value = sum(p["mv_eur"] or 0 for p in players)
    return {
        "league_id": league_id,
        "count": len(players),
        **money_fields("team_value", team_value),
        "in_lineup": sum(1 for p in players if p["in_lineup"]),
        "listed": sum(1 for p in players if p["listed"]),
        "players": players,
    }


# ------------------------------------------------------------------ market


async def _scores_for(
    client: Any, players: list[Player]
) -> dict[str, float]:
    """Kauf-Score je Spieler (Basis-Signale + Community-Tipps; ohne LigaInsider-Veto —
    das vollständige Bild liefert research_data)."""
    fixtures = await client.get_matchdays()
    team_names = await client.get_team_names()
    ids = [p.id for p in players]
    with SessionLocal() as session:
        history = load_history(session, ids)
        perf = load_performance(session, ids)
        tips = load_recent_tips(session)
    cands = await collect_from_players(players, fixtures, team_names, history, perf)
    cands = apply_community_boost(cands, tips)
    return {c.player_id: round(c.total_score, 3) for c in cands}


@tool_guard
async def market(limit: int = 60, sort: str = "score") -> dict[str, Any]:
    """Transfermarkt: id, name, pos, club, mv, price, seller (""=Kickbase), expires_in_s,
    lineup_prob, bid_ours, score. sort ∈ score|mv|mv24|price|expires|prob; limit kappt.
    Markt live von Kickbase, Score aus der LOKALEN Signal-DB, auch im Remote-Modus."""
    if sort not in _MARKET_SORTS:
        raise ToolError(
            f"Unbekannte Sortierung {sort!r}.", hint=f"sort ∈ {', '.join(_MARKET_SORTS)}"
        )
    client, league_id, user_id = await _ctx()
    now = datetime.now(UTC)
    raw = await client._get(endpoints.market(league_id))
    squad_ids = {sp.player.id for sp in await client.get_squad(league_id)}
    rows: list[dict[str, Any]] = []
    players: list[Player] = []
    for it in raw.get("it", []):
        mp = MarketPlayer.from_api(it)
        players.append(mp.player)
        seller = str((it.get("u") or {}).get("n") or "").strip()
        our_bid = next(
            (
                parse_int(o.get("uop"))
                for o in (it.get("ofs") or [])
                if str(o.get("u")) == str(user_id)
            ),
            None,
        )
        expires_in_s = (
            parse_int(it["exs"])
            if "exs" in it
            else max(0, int((mp.expires_at - now).total_seconds()))
        )
        mv = mp.player.market_value
        rows.append(
            {
                **_player_base(mp.player),
                **money_fields("price", mp.price),
                "price_vs_mv_pct": round(100 * mp.price / mv, 1) if mv else None,
                "seller": seller,
                # Kickbase-Listing = Auto-Zuschlag nach Countdown (exs); Manager-Listing =
                # Verkäufer entscheidet manuell (exs 0).
                "listing_type": "manager" if seller else "kickbase",
                "expires_in_s": expires_in_s,
                "expires_at": iso(mp.expires_at),
                **money_fields("bid_ours", our_bid),
                "in_squad": mp.player.id in squad_ids,
            }
        )
    scores = await _scores_for(client, players) if sort == "score" else {}
    unobserved_ids = [
        p.id
        for p in players
        if not getattr(p, "mv_change_24h_observed", False) and not p.mv_change_24h
    ]
    history_mv24: dict[str, int] = {}
    if unobserved_ids:
        with SessionLocal() as session:
            hist = load_history(session, unobserved_ids)
        for pid, snaps in hist.items():
            if len(snaps) >= 2:
                history_mv24[pid] = int(snaps[-1].market_value) - int(snaps[-2].market_value)
    for row in rows:
        row["score"] = scores.get(row["id"])
        if not row.get("mv24_eur") and row["id"] in history_mv24:
            row.update(money_fields("mv24", history_mv24[row["id"]]))
    keyfn = {
        "score": lambda r: -(r["score"] or 0.0),
        "mv": lambda r: -(r["mv_eur"] or 0),
        "mv24": lambda r: -(r["mv24_eur"] or 0),
        "price": lambda r: -(r["price_eur"] or 0),
        # Kickbase-Listings nach Countdown, Manager-Listings (exs 0, manuelle Entscheidung) zuletzt
        "expires": lambda r: (r["listing_type"] == "manager", r["expires_in_s"]),
        "prob": lambda r: (r["lineup_prob"] is None, r["lineup_prob"] or 0),
    }[sort]
    rows.sort(key=keyfn)
    return {
        "league_id": league_id,
        "count": len(rows),
        "returned": min(len(rows), max(0, limit)),
        "sort": sort,
        "score_note": (
            "Kauf-Score = Basis-Signale + Community-Tipps, OHNE LigaInsider-Veto "
            "(vollständig: research_data)."
            if sort == "score"
            else None
        ),
        "players": rows[: max(0, limit)],
    }


# ------------------------------------------------------------------ own_bids


@tool_guard
async def own_bids() -> dict[str, Any]:
    """Unsere offenen Gebote (live aus dem Markt): name, amount, seller (""=Kickbase-Listing
    mit Auto-Zuschlag; Manager entscheiden manuell), listing_price, expires_in_s, offer_id."""
    client, league_id, user_id = await _ctx()
    bids = await client.get_own_bids(league_id, user_id)
    rows = []
    for b in sorted(bids, key=lambda x: parse_int(x.get("expires_in_s"))):
        amount = parse_int(b.get("amount"))
        listing = parse_int(b.get("listing_price"))
        rows.append(
            {
                "player_id": str(b.get("player_id", "")),
                "name": b.get("name", ""),
                "club": club_name(b.get("team_id", "")),
                "team_id": str(b.get("team_id", "")),
                **money_fields("amount", amount),
                **money_fields("listing_price", listing),
                "amount_vs_listing_pct": round(100 * amount / listing, 1) if listing else None,
                "seller": b.get("seller", ""),
                "listing_type": "manager" if b.get("seller") else "kickbase",
                "expires_in_s": parse_int(b.get("expires_in_s")),
                "offer_id": str(b.get("offer_id", "")),
            }
        )
    return {
        "league_id": league_id,
        "count": len(rows),
        **money_fields("exposure", sum(r["amount_eur"] or 0 for r in rows)),
        "bids": rows,
    }


# ------------------------------------------------------------------ player


@tool_guard
async def player(player_id: str) -> dict[str, Any]:
    """Spieler-Detail (Member-Felder): prob (Startelf-Stufe 1=sicher … 5=Bank), st/stxt (Fitness
    + Text), tp/ap (Punkte gesamt/Schnitt), ph (letzte 5 Spieltage: hp gespielt?, p Punkte),
    mv, mv24, plpt/plpurl (Kickbase-Partner LigaInsider), shn, g/a/y/r, sec (Minuten·60)."""
    client, league_id, _user_id = await _ctx()
    raw = await client._get(endpoints.player_detail(league_id, str(player_id)))
    # Live-Fund 23.08.26: der Detail-Endpoint liefert den Nachnamen als `ln` (nicht `n`),
    # `tn` = Teamname, `stxt`/`tp`/`ap` nur wenn vorhanden — darum kein Player.from_api.
    last = raw.get("n") or raw.get("ln") or raw.get("nin") or ""
    name = f"{raw.get('fn') or ''} {last}".strip()
    team_id = str(raw.get("tid") or "")
    ph = raw.get("ph")
    prob = raw.get("prob")
    return {
        "id": str(raw.get("i") or player_id),
        "name": name,
        "pos": pos_name(raw.get("pos")),
        "club": raw.get("tn") or club_name(team_id),
        "team_id": team_id,
        **money_fields("mv", parse_int(raw.get("mv"))),
        **money_fields("mv24", parse_int(raw.get("tfhmvt"))),
        "mv_trend": parse_int(raw.get("mvt")),
        "lineup_prob": int(prob) if isinstance(prob, int | float) else None,
        "fitness_status": parse_int(raw.get("st")),
        "fitness_text": raw.get("stxt") or "",
        "status_list": raw.get("stl") if isinstance(raw.get("stl"), list) else [],
        "total_points": raw.get("tp"),
        "avg_points": raw.get("ap"),
        "last_matches": ph[-5:] if isinstance(ph, list) else None,
        "shirt_number": raw.get("shn"),
        "goals": raw.get("g"),
        "assists": raw.get("a"),
        "yellow": raw.get("y"),
        "red": raw.get("r"),
        "seconds_played": raw.get("sec"),
        "plpt": raw.get("plpt"),
        "plpurl": raw.get("plpurl"),
        "img": _img(str(raw.get("pim") or "")),
        "raw_keys": sorted(str(k) for k in raw),
    }


# ------------------------------------------------------------------ feed


def _feed_summary(item: dict[str, Any]) -> str:
    t = item.get("t")
    d = item.get("data") or {}
    if t == _FEED_TRANSFER:
        who = d.get("byr") or d.get("slr") or "?"
        verb = "kauft" if d.get("byr") else "verkauft"
        return f"{who} {verb} {d.get('pn', '?')} für {fmt_money(parse_int(d.get('trp')))}"
    if t == _FEED_BONUS:
        return f"Prämie kassiert: +{fmt_money(parse_int(d.get('bn')))} (Tag {d.get('day', '?')})"
    if t == _FEED_MARKET_EVENT:
        return f"Markt-Event: {d.get('ln', '?')} ({fmt_money(parse_int(d.get('mv')))})"
    return ""


@tool_guard
async def feed(limit: int = 50, types: list[int] | None = None) -> dict[str, Any]:
    """Liga-Activity-Feed: t (15 Transfer byr/slr/trp/pn · 3 Markt-Event · 22 Prämie), dt, data.
    `types` filtert auf Feed-Typen; limit kappt das Ergebnis."""
    client, league_id, _user_id = await _ctx()
    if limit <= 0:
        return {"league_id": league_id, "count": 0, "types": sorted(types or []), "items": []}
    fetch = limit if not types else min(FEED_MAX_ITEMS, max(limit * 4, 200))
    items = await client.get_activity_feed(league_id, max_items=fetch)
    wanted = {int(t) for t in types} if types else None
    out = []
    for it in items:
        if wanted is not None and it.get("t") not in wanted:
            continue
        out.append(
            {
                "i": it.get("i"),
                "t": it.get("t"),
                "dt": it.get("dt"),
                "summary": _feed_summary(it),
                "data": it.get("data") or {},
            }
        )
        if len(out) >= limit:
            break
    return {"league_id": league_id, "count": len(out), "types": sorted(wanted or []), "items": out}


# ------------------------------------------------------------------ audit_log


@tool_guard
async def audit_log(limit: int = 30, status: str | None = None) -> dict[str, Any]:
    """Audit-Log (transfer_actions): neueste zuerst, `status` filtert (executed, refused …).
    Mit Remote-Konfiguration aus der Box-DB; sonst aus der lokalen DB."""
    params: dict[str, str | int] = {"limit": min(1000, max(0, limit))}
    if status is not None:
        params["status"] = status
    remote = await read_remote("audit", **params)
    if remote is not None:
        return remote
    with SessionLocal() as session:
        return read_audit(session, limit, status)


# ------------------------------------------------------------------ active_tips


@tool_guard
async def active_tips() -> dict[str, Any]:
    """Aktive Community-/Recherche-Tipps (±0.08·Gewicht, Verfall nach 4 Tagen).
    Mit Remote-Konfiguration aus der Box-DB; sonst aus der lokalen DB."""
    remote = await read_remote("tips")
    if remote is not None:
        return remote
    with SessionLocal() as session:
        return read_tips(session)


# ------------------------------------------------------------------ research_data


@tool_guard
async def research_data() -> dict[str, Any]:
    """Der Text von `kba research-data` (Kauf-Scores, LI-Status, KB-Stufe, Profiteure, News,
    Community, aktive Tipps). Dauert ~1 Minute (LigaInsider + Feeds).
    Nutzt auch im Remote-Modus die LOKALE Signal-DB, nicht die Box-Historie."""
    t0 = time.monotonic()
    lines: list[str] = []
    await _dump_data(echo=lines.append)
    return {
        "text": "\n".join(lines),
        "lines": len(lines),
        "duration_s": round(time.monotonic() - t0, 1),
    }


# ------------------------------------------------------------------ recommend_lineup


def _avail_map(scout: Any) -> dict[str, Any]:
    return {a.player_id: a for a in getattr(scout, "availabilities", []) or []}


def _cand_row(c: Any, avail: dict[str, Any], prob_by_id: dict[str, int | None],
              img_by_id: dict[str, str]) -> dict[str, Any]:
    a = avail.get(c.player_id)
    prob = prob_by_id.get(c.player_id)
    return {
        "id": c.player_id,
        "name": c.name,
        "pos": pos_name(c.position),
        "club": club_name(c.team_id),
        "team_id": c.team_id,
        **money_fields("mv", c.current_market_value),
        "elf_score": round(getattr(c, "lineup_score", c.total_score), 3),
        "kauf_score": round(c.total_score, 3),
        "li_status": a.status.value if a else "unknown",
        "li_reason": a.reason if a else "",
        "li_confidence": round(a.confidence, 2) if a else None,
        "lineup_prob": prob,
        "lineup_label": lineup_label(prob),
        "img": img_by_id.get(c.player_id, ""),
    }


@tool_guard
async def recommend_lineup() -> dict[str, Any]:
    """Empfohlene Startelf der Engine (Elf-Score = Signale OHNE Markt-Trend, LigaInsider-
    Veto/Dämpfer, beste der 9 gültigen Formationen): formation, expected_score, starters,
    bench, quarantines (unsichere LI-Matches, kein Auto-Veto), je Spieler LI-Status + KB-
    Startelf-Stufe (1 Sicher … 5 Ausgeschlossen). ~20–60 s (LigaInsider-Sweep).
    Nutzt auch im Remote-Modus die LOKALE Signal-DB, nicht die Box-Historie."""
    t0 = time.perf_counter()
    client, league_id, _user_id = await _ctx()
    squad = await client.get_squad(league_id)
    with SessionLocal() as session:
        rec = await _recommend_lineup(client, session, squad)
    lineup, scout = rec.lineup, rec.scout
    avail = _avail_map(scout)
    prob_by_id = {sp.player.id: sp.player.lineup_prob for sp in squad}
    img_by_id = {sp.player.id: _img(sp.player.image_path) for sp in squad}
    starters = [_cand_row(c, avail, prob_by_id, img_by_id) for c in lineup.starters]
    bench = [_cand_row(c, avail, prob_by_id, img_by_id) for c in lineup.bench]
    quarantines = [
        {
            "player_id": getattr(q, "player_id", ""),
            "kb_name": getattr(q, "kb_name", ""),
            "li_name": getattr(q, "li_name", ""),
            "li_reason": getattr(q, "li_reason", ""),
            "confidence": round(getattr(q, "confidence", 0.0), 2),
        }
        for q in (getattr(scout, "quarantines", []) or [])
    ]
    by_status: dict[str, int] = {}
    for a in avail.values():
        by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
    return {
        "league_id": league_id,
        "formation": lineup.formation,
        "expected_score": round(lineup.expected_score, 3),
        "count": len(starters),
        "starters": starters,
        "bench": bench,
        "quarantines": quarantines,
        "li_status_counts": by_status,
        "note": (
            "Elf-Score steuert die Aufstellung (ohne Markt-Trend); Kauf-Score den Handel. "
            "KB-Startelf-Stufe ist Member-Zweitmeinung, LigaInsider bleibt primär."
        ),
        "duration_s": round(time.perf_counter() - t0, 1),
    }


# ------------------------------------------------------------------ team_profile


def _resolve_team(team: str) -> tuple[str, str] | None:
    """Team-ID oder Clubname (kb_name/LI-Slug, Teilstring, ohne Groß/Klein) → (tid, name)."""
    key = str(team or "").strip()
    if not key:
        return None
    if key in TEAMS:
        return key, TEAMS[key].kb_name
    k = key.lower()
    hits = [
        (tid, t.kb_name) for tid, t in TEAMS.items()
        if k == t.kb_name.lower() or k == t.slug.lower()
    ] or [
        (tid, t.kb_name) for tid, t in TEAMS.items()
        if k in t.kb_name.lower() or k in t.slug.lower()
    ]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise ValueError(
            "Mehrdeutiger Club '" + key + "': " + ", ".join(n for _, n in hits)
        )
    return None


_TEAM_SORTS = ("avg_points", "mv", "mv7d", "prob")


@tool_guard
async def team_profile(team: str, min_prob: int | None = None,
                       sort: str = "avg_points") -> dict[str, Any]:
    """Alle Spieler eines Bundesliga-Clubs (auch wenn nicht am Markt) — Scouting-Sicht:
    Ø-Punkte, MW, 7-Tage-MW-Trend (sdmvt), KB-Startelf-Stufe (1 Sicher … 5
    Ausgeschlossen), Fitness, am Markt?, Angebote. `team` = Kickbase-Team-ID oder Clubname
    („Bayern", „Union Berlin", „leverkusen"). `min_prob` filtert auf Stufe ≤ n (z. B. 2 =
    nur Sicher+Erwartet). sort ∈ avg_points | mv | mv7d | prob."""
    if sort not in _TEAM_SORTS:
        raise ValueError("Unbekannte Sortierung '" + sort + "'. sort ∈ " + ", ".join(_TEAM_SORTS))
    resolved = _resolve_team(team)
    if resolved is None:
        known = ", ".join(sorted(t.kb_name for t in TEAMS.values()))
        raise ValueError("Unbekannter Club '" + str(team) + "'. Bekannt: " + known)
    tid, name = resolved
    client, league_id, user_id = await _ctx()
    profile = await client.get_team_profile(tid)
    market_ids: set[str] = set()
    own_bid_ids: set[str] = set()
    try:
        market_raw = await client._get(endpoints.market(league_id))
        for it in market_raw.get("it", []):
            pid = str(it.get("i") or "")
            if pid:
                market_ids.add(pid)
                if any(str(o.get("u")) == str(user_id) for o in (it.get("ofs") or [])):
                    own_bid_ids.add(pid)
    except Exception:  # noqa: BLE001 — Markt-Zusatzabgleich darf team_profile nie brechen
        pass
    rows = []
    for p in profile.players:
        if min_prob is not None and (p.lineup_prob is None or p.lineup_prob > min_prob):
            continue
        rows.append(
            {
                "id": p.id,
                "name": p.last_name,
                "pos": pos_name(p.position),
                **money_fields("mv", p.market_value),
                **money_fields("mv7d", p.mv_change_7d),
                "mv_trend": p.market_value_trend,
                "avg_points": p.avg_points,
                "lineup_prob": p.lineup_prob,
                "lineup_label": lineup_label(p.lineup_prob),
                "fitness_status": p.fitness_status,
                # `/competitions/1/teams/{tid}/teamprofile` kennt keine Liga-ID und
                # liefert `iotm=False` — Liga-Markt ergänzt den echten Marktstatus.
                "on_market": bool(p.on_market or p.id in market_ids),
                # `ofc` zählt NUR eigene Gebote (Messung 26.08., L-079) —
                # deshalb heißt das Feld, was es ist, statt "offers".
                "own_bid": bool(p.offer_count or p.id in own_bid_ids),
                "img": _img(p.image_path),
            }
        )
    keyf = {
        "avg_points": lambda r: -r["avg_points"],
        "mv": lambda r: -(r["mv_eur"] or 0),
        "mv7d": lambda r: -(r["mv7d_eur"] or 0),
        "prob": lambda r: (r["lineup_prob"] or 9, -r["avg_points"]),
    }[sort]
    rows.sort(key=keyf)
    return {
        "team": {"id": profile.team_id or tid, "name": profile.name or name,
                 "img": _img(profile.image_path)},
        "count": len(rows),
        "total_in_squad": len(profile.players),
        "filter": {"min_prob": min_prob, "sort": sort},
        "players": rows,
    }


# ------------------------------------------------------------------ evaluation


@tool_guard
async def evaluation() -> dict[str, Any]:
    """Lern-Schicht (gleicher Inhalt wie GET /api/learning): Treffer-Bilanz der Engine —
    Kaufvorschläge echt + Schatten vs. Marktwert nach 1/7 Tagen (buy_hits/shadow/executed:
    n, hit_rate 0..1, avg/median delta_pct), Tipp-Bilanz (7 Tage), Flip-Bilanz echter Gebote
    (Kaufpreis vs. aktueller MW), Erkenntnisse aus LEARNINGS.md (date/title/text), Parameter-
    Historie (date/parameter/old/new/reason/commit) + aktuelle Parameter, offene
    Kalibrierungen. Mit Remote-Konfiguration aus der Box-DB; sonst lokale DB +
    Paketdaten. Kein Kickbase-Call, kein Fallback auf lokale Daten bei Remote-Fehlern."""
    remote = await read_remote("learning")
    if remote is not None:
        return remote
    with SessionLocal() as session:
        return build_learning(session)
