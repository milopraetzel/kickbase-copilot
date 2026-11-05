"""Reine Strategie-Auswertung: bestätigte Transfers, datierte Werte und Datenlücken.

Kein I/O, keine Settings, keine Preisempfehlung und keine Handelsaktion. Vertrag:
feed_events = rohe t=15-Ereignisse; history_rows = DB-Dicts mit player_id,
recorded_at, market_value (optional source='api_history', quality, captured_at);
performance_rows = DB-Dicts mit player_id, season, matchday, points, minutes,
match_date. Kader/Markt = Cockpit-Dicts mit pid, name, mv, position/pos;
Kader optional protected, status. Manager = read_manager_squads-Ausgabe (spieler),
ergänzt um completed_points und matchday_history. saved_lineup = rohes get_lineup.
None heißt Abruf unbekannt; [] heißt erfolgreich leer. Geld in ganzen Euro,
Prozent als 0–100-Zahl, fehlende Werte null. Quellen bleiben an Chartpunkten.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
BUCKETS = ("500k-Untergrenze", "<5 Mio.", "5–15 Mio.", "15–30 Mio.", "≥30 Mio.", "Unbekannt")


def _date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _number(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _pct(part: int | float, total: int | float | None) -> float | None:
    return round(100 * part / total, 4) if total else None


def _quantile(values: list[float | int], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    left, right = math.floor(index), math.ceil(index)
    return round(ordered[left] + (ordered[right] - ordered[left]) * (index - left), 4)


def _bucket(value: int | None) -> str:
    if value is None:
        return "Unbekannt"
    for ceiling, label in zip((500001, 5000000, 15000000, 30000000), BUCKETS, strict=False):
        if value < ceiling:
            return label
    return "≥30 Mio."


def _stats(rows: list[dict[str, Any]], *, known: bool = True) -> dict[str, Any]:
    priced = [r for r in rows if r["premium_pct"] is not None and not r["update_boundary"]]
    values = [r["premium_pct"] for r in priced]
    result = {
        "n": len(rows),
        "n_priced": len(priced),
        "n_boundary_excluded": sum(r["update_boundary"] for r in rows),
        "n_missing_mw": sum(r["market_value"] is None for r in rows),
        "median_premium_pct": round(median(values), 4) if values else None,
        "q25_premium_pct": _quantile(values, 0.25),
        "q75_premium_pct": _quantile(values, 0.75),
        "spent_eur": sum(r["amount"] for r in rows),
        "premium_eur": sum(r["amount"] - r["market_value"] for r in priced),
    }
    return result if known else dict.fromkeys(result)


def build_strategy_insights(
    *,
    feed_events: list[dict] | None,
    history_rows: list[dict] | None,
    performance_rows: list[dict] | None,
    squad_rows: list[dict] | None,
    market_rows: list[dict] | None,
    manager_rows: list[dict] | None,
    budget: int | None,
    open_bids: list[dict] | None,
    saved_lineup: dict | None,
    generated_at: datetime | str,
    user_id: str,
    user_name: str,
    manager_performance: dict[str, dict] | None = None,
    season_start: str | None = None,
) -> dict[str, Any]:
    """Beobachtungen für d['strategy']; fehlende Quellen liefern partielle Daten.

    Ausgabe: summary, trades, manager_stats, price_buckets, round_trips,
    portfolio, players, insights, coverage und limitations. Kaufnachweis ist
    ausschließlich der Feed; Audit-won, buy_price und Wunschpreise zählen nicht.
    """
    now = _date(generated_at)
    if now is None:
        raise ValueError("generated_at muss ein Datum mit nachvollziehbarer Zeitzone sein")
    inputs = {
        "feed": feed_events,
        "history": history_rows,
        "performance": performance_rows,
        "squad": squad_rows,
        "market": market_rows,
        "managers": manager_rows,
        "budget": budget,
        "open_bids": open_bids,
        "saved_lineup": saved_lineup,
    }
    unknown = [name for name, value in inputs.items() if value is None]
    me = user_name.strip()
    registry: dict[str, dict] = {}
    for manager in manager_rows or []:
        for player in manager.get("spieler") or []:
            if player.get("pid"):
                registry[str(player["pid"])] = dict(player)
    for player in [*(market_rows or []), *(squad_rows or [])]:
        if player.get("pid"):
            registry[str(player["pid"])] = dict(player)
    # Auch verkaufte Spieler bleiben über ihre bestätigte Historie analysierbar.
    for event in feed_events or []:
        raw = event.get("data") or {}
        pid = str(raw.get("pi") or "")
        if event.get("t") == 15 and pid:
            registry.setdefault(pid, {"name": raw.get("pn") or pid})
    own_ids = {str(r["pid"]) for r in squad_rows or [] if r.get("pid")}

    history: dict[tuple[str, str], dict] = {}
    for row in history_rows or []:
        pid = str(row.get("player_id") or row.get("pid") or "")
        when = _date(row.get("recorded_at") or row.get("date"))
        if not pid or when is None or when.date() > now.date():
            continue
        key = pid, when.date().isoformat()
        old = history.get(key)
        source = str(row.get("source") or "db_history")
        if old and old["source"] == "api_history" and source != "api_history":
            continue
        value = _number(row.get("market_value", row.get("value")))
        quality = row.get("quality", "observed")
        valid = value is not None and value > 0 and quality != "unknown"
        history[key] = {
            "date": key[1],
            "value": value if valid else None,
            "raw_value": value,
            "source": source,
            "source_row_id": row.get("id"),
            "quality": "observed" if valid else "unknown",
            "unknown_reason": None
            if valid
            else row.get("unknown_reason", "missing_or_invalid_value"),
            "captured_at": row.get("captured_at"),
        }

    trades, seen, skipped = [], set(), 0
    for event in feed_events or []:
        if event.get("t") != 15:
            continue
        event_id = str(event.get("i") or "")
        if event_id in seen:
            continue
        raw = event.get("data") or {}
        pid = str(raw.get("pi") or "")
        at, amount = _date(event.get("dt")), _number(raw.get("trp"))
        buyer, seller = str(raw.get("byr") or "").strip(), str(raw.get("slr") or "").strip()
        if not event_id or not pid or at is None or amount is None or not (buyer or seller):
            skipped += 1
            continue
        if amount < 0 or at > now:
            skipped += 1
            continue
        seen.add(event_id)
        local = at.astimezone(BERLIN)
        day = (local.date() - timedelta(days=local.hour < 22)).isoformat()
        h = history.get((pid, day), {})
        mv = h.get("value")
        minute = local.hour * 60 + local.minute
        trades.append(
            {
                "feed_id": event_id,
                "player_id": pid,
                "name": str(raw.get("pn") or pid),
                "manager": buyer or seller,
                "buyer": buyer,
                "seller": seller,
                "buyer_id": str(raw.get("buyer_id") or ""),
                "seller_id": str(raw.get("seller_id") or ""),
                "source": event.get("source", "activity_feed"),
                "synthetic_id": bool(event.get("synthetic_id")),
                "at": at.isoformat(),
                "amount": amount,
                "market_value": mv,
                "premium_pct": _pct(amount - mv, mv) if mv else None,
                "position": registry.get(pid, {}).get("position")
                or registry.get(pid, {}).get("pos"),
                "bucket": _bucket(mv),
                "kind": "manager_deal"
                if buyer and seller
                else "kb_purchase"
                if buyer
                else "kb_sale",
                "update_boundary": 21 * 60 + 45 <= minute <= 22 * 60 + 30,
                "mw_day": day,
                "mw_source": h.get("source"),
                "mw_unknown_reason": h.get("unknown_reason") if h else "no_historical_value",
            }
        )
    trades.sort(key=lambda r: (r["at"], r["feed_id"]))
    kb_buys = [r for r in trades if r["kind"] == "kb_purchase"]
    own_buys = [r for r in trades if r["buyer"] == me]
    own_sales = [r for r in trades if r["seller"] == me]
    lots, round_trips, unknown_sales = {}, [], []
    for trade in trades:
        pid = trade["player_id"]
        if trade["seller"] == me:
            buy = lots.pop(pid, None)
            if buy:
                round_trips.append(
                    {
                        "player_id": pid,
                        "name": trade["name"],
                        "buy_feed_id": buy["feed_id"],
                        "sell_feed_id": trade["feed_id"],
                        "bought_at": buy["at"],
                        "sold_at": trade["at"],
                        "buy_amount": buy["amount"],
                        "sell_amount": trade["amount"],
                        "pnl_eur": trade["amount"] - buy["amount"],
                        "holding_days": round(
                            (_date(trade["at"]) - _date(buy["at"])).total_seconds() / 86400, 3
                        ),
                    }
                )
            else:
                unknown_sales.append(trade)
        if trade["buyer"] == me:
            lots[pid] = trade

    holdings = []
    for row in squad_rows or []:
        pid = str(row.get("pid") or "")
        value = _number(row.get("mv"))
        buy = lots.get(pid)
        holdings.append(
            {
                "player_id": pid,
                "name": row.get("name", pid),
                "market_value": value,
                "buy_amount": buy["amount"] if buy else None,
                "bought_at": buy["at"] if buy else None,
                "buy_feed_id": buy["feed_id"] if buy else None,
                "unrealized_pnl_eur": value - buy["amount"] if buy and value is not None else None,
                "protected": row.get("protected"),
                "status": row.get("status", "unknown"),
            }
        )
    all_values_known = squad_rows is not None and all(
        h["market_value"] is not None for h in holdings
    )
    team_value = sum(h["market_value"] for h in holdings) if all_values_known else None
    values = sorted(
        (h["market_value"] for h in holdings if h["market_value"] is not None), reverse=True
    )
    protection_known = all(h["protected"] is not None for h in holdings) and squad_rows is not None
    protected_value = sum(h["market_value"] or 0 for h in holdings if h["protected"])
    bids = [_number(row.get("amount")) for row in open_bids or []]
    exposure = sum(bids) if open_bids is not None and all(v is not None for v in bids) else None
    cash = _number(budget)
    after_bids = cash - exposure if cash is not None and exposure is not None else None
    lineup_rows = saved_lineup.get("it") if isinstance(saved_lineup, dict) else None
    orders = [
        r["lo"]
        for r in lineup_rows or []
        if isinstance(r.get("lo"), int) and not isinstance(r["lo"], bool) and 0 <= r["lo"] <= 10
    ]
    lineup_valid = isinstance(lineup_rows, list) and len(orders) == len(set(orders))
    if not lineup_valid and saved_lineup is not None:
        unknown.append("saved_lineup_slots")
    occupied = sorted(orders) if lineup_valid else None
    empty = sorted(set(range(11)) - set(orders)) if lineup_valid else None
    realized = sum(r["pnl_eur"] for r in round_trips) if feed_events is not None else None
    known_holdings = [h for h in holdings if h["unrealized_pnl_eur"] is not None]
    unrealized = sum(h["unrealized_pnl_eur"] for h in known_holdings) if known_holdings else None
    waterfall = [
        {"label": "Konto", "amount": cash, "kind": "total"},
        {
            "label": "Offene Gebote",
            "amount": -exposure if exposure is not None else None,
            "kind": "change",
        },
        {"label": "Konto bei allen Zuschlägen", "amount": after_bids, "kind": "total"},
    ]

    managers = []
    for row in manager_rows or []:
        name = str(row.get("name") or "").strip()
        bought = [r for r in trades if r["buyer"] == name]
        roster = row.get("spieler")
        mvalues = [_number(p.get("mv")) for p in roster or []]
        complete = isinstance(roster, list) and all(v is not None for v in mvalues)
        mtotal = sum(mvalues) if complete else None
        ordered = sorted((v for v in mvalues if v is not None), reverse=True)
        managers.append(
            {
                "user_id": str(row.get("user_id") or ""),
                "name": name,
                **_stats([r for r in kb_buys if r["buyer"] == name], known=feed_events is not None),
                "purchases": len(bought) if feed_events is not None else None,
                "sales": sum(r["seller"] == name for r in trades)
                if feed_events is not None
                else None,
                "completed_points": row.get("completed_points"),
                "matchday_history": row.get("matchday_history"),
                "live_season_points": row.get("punkte_saison"),
                "rank": row.get("rank"),
                "squad_value": mtotal,
                "squad_size": len(roster) if isinstance(roster, list) else None,
                "top1_share_pct": _pct(ordered[0], mtotal) if ordered else None,
                "top3_share_pct": _pct(sum(ordered[:3]), mtotal),
                "budget_eur": cash if str(row.get("user_id")) == str(user_id) else None,
            }
        )

    curves, performances = defaultdict(list), defaultdict(dict)
    for (pid, _), row in sorted(history.items()):
        curves[pid].append(row)
    future_rows = 0
    for row in performance_rows or []:
        pid = str(row.get("player_id") or "")
        season = str(row.get("season") or "")
        md = _number(row.get("matchday"))
        at = _date(row.get("match_date"))
        points, minutes = _number(row.get("points")), _number(row.get("minutes"))
        captured = _date(row.get("captured_at"))
        cutoff = min(now, captured) if captured else now
        if at is not None and at > cutoff:
            future_rows += 1
            continue
        # Vorgeplante API-Nullzeilen werden durch Liegenlassen im Cache nicht zu
        # gespielten Nullminuten. Ohne Daten am Abruf-Spieltag bleibt der Ausgang offen.
        if (row.get("source") == "api_performance" and captured is not None
                and points == 0 and minutes == 0
                and (at is None or at.date() >= captured.date())):
            future_rows += 1
            continue
        if not pid or not season or md is None or points is None or minutes is None:
            continue
        source = str(row.get("source") or "db_player_performance")
        old = performances[pid].get((season, md))
        if old and old["source"] == "api_performance" and source != "api_performance":
            continue
        performances[pid][season, md] = {
            "season": season,
            "matchday": md,
            "points": points,
            "minutes": minutes,
            "date": at.isoformat() if at else None,
            "source": source,
            "source_row_id": row.get("id"),
            "captured_at": row.get("captured_at"),
            "provisional": (at.date() == now.date()
                            or (captured is not None and at.date() == captured.date()))
            if at else None,
        }
    players = []
    for pid, row in sorted(registry.items()):
        perf = sorted(performances[pid].values(), key=lambda r: (r["season"], r["matchday"]))
        seasons = []
        for season in sorted({r["season"] for r in perf}, reverse=True):
            rows = [r for r in perf if r["season"] == season]
            points = [r["points"] for r in rows]
            point_bins = Counter(
                "<0"
                if p < 0
                else "0"
                if p == 0
                else "1–49"
                if p < 50
                else "50–99"
                if p < 100
                else "100–199"
                if p < 200
                else "≥200"
                for p in points
            )
            seasons.append(
                {
                    "season": season,
                    "n_rows": len(rows),
                    "n_appearances": sum(r["minutes"] > 0 for r in rows),
                    "avg_points": round(sum(points) / len(rows), 2),
                    "avg_minutes": round(sum(r["minutes"] for r in rows) / len(rows), 2),
                    "zero_minute_rows": sum(r["minutes"] == 0 for r in rows),
                    "q25_points": _quantile(points, 0.25),
                    "q75_points": _quantile(points, 0.75),
                    "points_distribution": dict(point_bins),
                    "small_sample": len(rows) < 5,
                    "observed_matchdays": sorted(r["matchday"] for r in rows),
                    "has_provisional_rows": any(r["provisional"] for r in rows),
                }
            )
        curve = curves[pid]
        players.append(
            {
                "player_id": pid,
                "name": row.get("name", pid),
                "owned": pid in own_ids,
                "market_value": _number(row.get("mv")),
                "position": row.get("position", row.get("pos")),
                "status": row.get("status", "unknown"),
                "kb_prob": row.get("kb_prob"),
                "history": curve,
                "performance": perf,
                "season_stats": seasons,
                "coverage": {
                    "history_rows": len(curve),
                    "history_known_rows": sum(r["value"] is not None for r in curve),
                    "history_start": curve[0]["date"] if curve else None,
                    "history_end": curve[-1]["date"] if curve else None,
                    "performance_rows": len(perf),
                    "missing_matchdays_are_unknown": True,
                },
            }
        )

    insights = []
    if empty:
        insights.append(
            {
                "severity": "critical",
                "title": f"{len(empty)} Aufstellungsplätze leer",
                "detail": "In der gespeicherten Elf. Reale Startchancen und Frist separat prüfen.",
                "metric": len(empty),
                "source": "saved_lineup.lo",
            }
        )
    if after_bids is not None and after_bids < 0:
        insights.append(
            {
                "severity": "critical",
                "title": "Finanzierung offen",
                "detail": "Bei allen Zuschlägen ist das Konto negativ; Listings sind keine Erlöse.",
                "metric": after_bids,
                "source": "budget minus open_bids",
            }
        )
    if round_trips:
        insights.append(
            {
                "severity": "warning" if realized < 0 else "info",
                "title": "Bilanz vollständig beobachteter Trades",
                "detail": (
                    f"{len(round_trips)} Kauf-Verkauf-Paare; "
                    f"{len(unknown_sales)} Verkäufe ohne bekannten Einstand."
                ),
                "metric": realized,
                "source": "feed t=15, paired feed_ids",
            }
        )
    if unknown:
        insights.append(
            {
                "severity": "warning",
                "title": "Daten teilweise unbekannt",
                "detail": ", ".join(unknown),
                "metric": len(unknown),
                "source": "input availability",
            }
        )
    result: dict[str, Any] = {
        "version": 2,
        "generated_at": now.isoformat(),
        "status": "partial" if unknown else "ok",
        "summary": {
            "headline": "Mannschaft, Finanzierung und echte Ergebnisse",
            "detail": "Bestätigte Transfers und datierte Beobachtungen; keine Punktprognose.",
            "complete_round_trips": len(round_trips) if feed_events is not None else None,
            "realized_pnl_eur": realized,
            "unrealized_pnl_eur": unrealized,
            "unknown_cost_basis_sales": len(unknown_sales) if feed_events is not None else None,
            "confirmed_purchases": len(own_buys) if feed_events is not None else None,
            "confirmed_sales": len(own_sales) if feed_events is not None else None,
            "holdings_with_cost_basis": len(known_holdings) if feed_events is not None else None,
            "transfer_count": len(trades) if feed_events is not None else None,
            "median_premium_pct": _stats([r for r in kb_buys if (r["market_value"] or 0) > 500000])[
                "median_premium_pct"
            ],
            "median_premium_scope": "Kickbase-Käufe über 500.000 Euro MW, ohne Updatefenster",
        },
        "trades": trades,
        "manager_stats": managers,
        "price_buckets": [
            {
                "label": label,
                **_stats(
                    [r for r in kb_buys if r["bucket"] == label], known=feed_events is not None
                ),
            }
            for label in BUCKETS
        ],
        "purchase_premiums": _stats(kb_buys, known=feed_events is not None),
        "round_trips": round_trips,
        "players": players,
        "insights": insights,
        "portfolio": {
            "budget_eur": cash,
            "open_bid_exposure_eur": exposure,
            "cash_after_all_bids_eur": after_bids,
            "team_value_eur": team_value,
            "net_assets_eur": cash + team_value
            if cash is not None and team_value is not None
            else None,
            "occupied_slots": occupied,
            "empty_slots": empty,
            "lineup_status": "observed" if lineup_valid else "unknown",
            "squad_count": len(holdings) if squad_rows is not None else None,
            "top1_share_pct": _pct(values[0], team_value) if values else None,
            "top3_share_pct": _pct(sum(values[:3]), team_value),
            "protected_share_pct": _pct(protected_value, team_value) if protection_known else None,
            "waterfall": waterfall,
            "holdings": holdings,
        },
        "coverage": {
            "unknown_sources": unknown,
            "feed_first_at": trades[0]["at"] if trades else None,
            "feed_last_at": trades[-1]["at"] if trades else None,
            "feed_transfers": len(trades),
            "malformed_transfers_skipped": skipped,
            "future_performance_rows_excluded": future_rows,
            "history_player_days": len(history),
            "performance_input_rows": len(performance_rows or []),
        },
        "limitations": [
            "Gewinnerpreise zeigen keine verlorenen Fremdgebote oder minimal nötigen Preise.",
            "Aufschläge schließen 21:45–22:30 Berlin aus; MW-Tag vor 22 Uhr ist der Vortag.",
            "Einstand nur aus bestätigtem Feed-Kauf; Audit-won und offene Gebote sind keine Käufe.",
            "Fehlende Spieltagszeilen sind unbekannt; Mittelwerte nur aus beobachteten Zeilen.",
            "Punkte und Minuten bleiben je Saison getrennt; heutige Spiele sind vorläufig.",
            "Marktwerte können historisch korrigiert sein; dies beweist kein damaliges Wissen.",
            "Umbenennungen können die Managerzuordnung stören; fremdes Konto bleibt unbekannt.",
            "Gespeicherte Elf garantiert keine realen Einsätze; Zukunftserlöse zählen nicht.",
            "Wertkonzentration und vergangene Punkte sind beschreibend, keine Prognose.",
        ],
    }

    from kickbase_agent.intel.manager_profiles import build_manager_profiles

    profiles = build_manager_profiles(
        trades=trades, manager_rows=manager_rows or [], user_id=user_id,
        user_name=user_name, budget=budget, own_squad_rows=squad_rows,
        manager_performance=manager_performance, generated_at=now.isoformat(),
        season_start=season_start,
    )
    result["manager_profiles"] = profiles
    profile_by_id = {p["user_id"]: p for p in profiles}
    for manager in result["manager_stats"]:
        profile = profile_by_id.get(manager["user_id"])
        if profile:
            manager["completed_points"] = profile["completed_points"]
            manager["matchday_history"] = profile["matchday_history"]
    return result
