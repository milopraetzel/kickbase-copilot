"""Vergleichbare Managerprofile aus belegten Transfers und datierten Spieltagen.

Reine Auswertung, ohne I/O. Transfers entsprechen ``strategy.trades``; ``spieler``
und eigener Kader enthalten ``pid/name/mv``. Einstandspreise stammen ausschließlich
aus Käufen innerhalb der übergebenen Reset-Grenze. Gewinnsummen beziehen sich auf
belegte Teilmengen; ``coverage`` benennt fehlende Einstandspreise ausdrücklich.
Fremde Kontostände werden weder aus Transfers noch aus API-Gewinnfeldern geschätzt.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from statistics import median
from typing import Any


def _date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _pct(part: int, total: int | None) -> float | None:
    return round(100 * part / total, 4) if total else None


def _observed_market_value(trade: dict) -> int | None:
    """Nur historische Beobachtungen außerhalb des unklaren MW-Updatefensters."""
    value = _number(trade.get("market_value"))
    if (
        value is None or value <= 0 or trade.get("update_boundary")
        or trade.get("mw_source") not in {"api_history", "db_history", "database_daily"}
        or trade.get("mw_unknown_reason") or trade.get("mw_quality") == "unknown"
    ):
        return None
    return value


def _sum_known(rows: list[dict], key: str, *, empty: int | None = None) -> int | None:
    values = [r[key] for r in rows if r.get(key) is not None]
    return sum(values) if values else empty


def _season_year(season: dict) -> int | None:
    return _number(str(season.get("sn") or "").split("/")[0])


def _performance(raw: dict | None, now: datetime) -> dict:
    """``cur`` gilt nur im aktuellen Saisonblock, auch Altsaisons tragen es."""
    blocks = [s for s in (raw or {}).get("it", []) if isinstance(s, dict)]
    football_year = now.year - (now.month < 7)
    current = next((s for s in blocks if _season_year(s) == football_year), None)
    if current is None:
        dated: list[tuple[datetime, dict]] = []
        for block in blocks:
            dates = [_date(r.get("md")) for r in block.get("it", [])]
            valid_dates = [d for d in dates if d is not None]
            if valid_dates and min(valid_dates) <= now <= max(valid_dates):
                dated.append((min(valid_dates), block))
        current = max(dated, key=lambda item: item[0])[1] if dated else None

    seasons: list[dict[str, Any]] = []
    for block in blocks:
        is_current = block is current
        year = _season_year(block)
        is_past = year is not None and year < football_year
        if not is_current and not is_past:
            continue
        rows = [r for r in block.get("it", []) if _number(r.get("day")) is not None]
        rows.sort(key=lambda r: int(r["day"]))
        current_days = [int(r["day"]) for r in rows if r.get("cur") and is_current]
        history: list[dict[str, Any]] = []
        cumulative, missing = 0, 0
        for row in rows:
            day, when, points = int(row["day"]), _date(row.get("md")), _number(row.get("mdp"))
            provisional = bool(row.get("cur")) and is_current
            if when is not None and when > now:
                continue
            past = is_past or (when is not None and when <= now)
            past = past or bool(current_days and day < min(current_days))
            if not past and not provisional:
                continue
            previous_day = history[-1]["matchday"] if history else 0
            for missing_day in range(previous_day + 1, day):
                missing += 1
                history.append({
                    "season": str(block.get("sn") or ""), "matchday": missing_day,
                    "points": None, "cumulative_points": None, "provisional": False, "at": None,
                })
            if points is None:
                missing += 1
            else:
                cumulative += points
            history.append({
                "season": str(block.get("sn") or ""), "matchday": day,
                "points": points, "cumulative_points": cumulative if missing == 0 else None,
                "provisional": provisional, "at": row.get("md"),
            })
        completed = [r for r in history if not r["provisional"]]
        completed_total = _sum_known(completed, "points", empty=0 if not completed else None)
        # Ein unbekannter abgeschlossener Spieltag ist keine Nullpunkt-Runde.
        if any(r["points"] is None for r in completed):
            completed_total = None
        live = _number(block.get("tp"))
        if live is None and missing == 0:
            live = cumulative
        seasons.append({
            "season": str(block.get("sn") or ""), "season_id": str(block.get("sid") or ""),
            "current": is_current, "live_points": live, "completed_points": completed_total,
            "matchday_history": history, "missing_matchday_points": missing,
        })
    seasons.sort(key=lambda s: s["season"], reverse=True)
    active = next((s for s in seasons if s["current"]), None)
    return {
        "live_points": active["live_points"] if active else None,
        "completed_points": active["completed_points"] if active else None,
        "matchday_history": active["matchday_history"] if active else [],
        "seasons": seasons,
        "performance_available": raw is not None,
        "current_season_available": active is not None,
        "missing_matchday_points": active["missing_matchday_points"] if active else None,
    }


def _round_trip(buy: dict, sale: dict) -> dict:
    buy_mv, sale_mv = _observed_market_value(buy), _observed_market_value(sale)
    buy_premium = buy["amount"] - buy_mv if buy_mv is not None else None
    sale_premium = sale["amount"] - sale_mv if sale_mv is not None else None
    bought_at, sold_at = _date(buy["at"]), _date(sale["at"])
    assert bought_at is not None and sold_at is not None  # Vor dem Pairing validiert.
    return {
        "player_id": sale["player_id"], "name": sale.get("name", sale["player_id"]),
        "buy_feed_id": buy.get("feed_id"), "sell_feed_id": sale.get("feed_id"),
        "bought_at": buy["at"], "sold_at": sale["at"],
        "buy_amount": buy["amount"], "sell_amount": sale["amount"],
        "pnl_eur": sale["amount"] - buy["amount"],
        "pnl_pct": _pct(sale["amount"] - buy["amount"], buy["amount"]),
        "holding_days": round((sold_at - bought_at).total_seconds() / 86400, 3),
        "buy_market_value": buy_mv, "sell_market_value": sale_mv,
        "buy_premium_eur": buy_premium, "sale_premium_eur": sale_premium,
        "market_value_change_eur": sale_mv - buy_mv
        if buy_mv is not None and sale_mv is not None else None,
        "decomposition_complete": buy_mv is not None and sale_mv is not None,
    }


def _finances(uid: str, trades: list[tuple[dict, str, str]], roster: list | None) -> dict:
    lots: dict[str, dict] = {}
    own_trades: list[dict] = []
    pairs: list[dict] = []
    timeline: list[dict] = []
    buys: list[dict] = []
    sales: list[dict] = []
    unknown_sales, cashflow = 0, 0
    for trade, buyer, seller in trades:
        pid = trade["player_id"]
        pair = None
        if seller == uid:
            sales.append(trade)
            cashflow += trade["amount"]
            buy = lots.pop(pid, None)
            if buy is not None:
                pair = _round_trip(buy, trade)
                pairs.append(pair)
            else:
                unknown_sales += 1
        # Jeder anderweitige Eigentumswechsel widerlegt einen alten offenen Kauf.
        elif buyer != uid:
            lots.pop(pid, None)
        if buyer == uid:
            buys.append(trade)
            cashflow -= trade["amount"]
            lots[pid] = trade
        if uid not in (buyer, seller):
            continue
        own_trades.append({
            **trade, "direction": "purchase" if buyer == uid else "sale",
            "realized_pnl_eur": pair["pnl_eur"] if pair else None,
            "cost_basis_known": buyer == uid or pair is not None,
        })
        timeline.append({
            "at": trade["at"],
            "realized_pnl_eur": _sum_known(pairs, "pnl_eur", empty=None if unknown_sales else 0),
            "net_transfer_cashflow_eur": cashflow,
            "complete_round_trips": len(pairs), "unknown_cost_basis_sales": unknown_sales,
        })

    holdings = []
    for row in roster or []:
        pid = str(row.get("pid") or row.get("player_id") or "")
        value = _number(row.get("mv", row.get("market_value")))
        buy = lots.get(pid)
        amount = buy["amount"] if buy is not None else None
        buy_mv = _observed_market_value(buy) if buy is not None else None
        pnl = value - amount if amount is not None and value is not None else None
        premium = amount - buy_mv if amount is not None and buy_mv is not None else None
        holdings.append({
            **row, "player_id": pid, "name": row.get("name", pid), "market_value": value,
            "buy_amount": amount, "bought_at": buy["at"] if buy is not None else None,
            "buy_feed_id": buy.get("feed_id") if buy is not None else None,
            "buy_market_value": buy_mv, "buy_premium_eur": premium,
            "buy_premium_pct": _pct(premium, buy_mv) if premium is not None else None,
            "unrealized_pnl_eur": pnl,
            "unrealized_pnl_pct": _pct(pnl, amount) if pnl is not None else None,
            "market_value_change_eur": value - buy_mv
            if value is not None and buy_mv is not None else None,
        })
    priced = [(t, mv) for t in buys if (mv := _observed_market_value(t)) is not None]
    premiums = [p for t, mv in priced if (p := _pct(t["amount"] - mv, mv)) is not None]
    squad_known = roster is not None and all(h["market_value"] is not None for h in holdings)
    with_basis = sum(h["buy_amount"] is not None for h in holdings)
    decomposed = [p for p in pairs if p["decomposition_complete"]]
    valued_holdings = [h for h in holdings if h["market_value_change_eur"] is not None]
    summary = {
        "realized_pnl_eur": _sum_known(pairs, "pnl_eur", empty=None if unknown_sales else 0),
        "unrealized_pnl_eur": _sum_known(
            holdings, "unrealized_pnl_eur", empty=0 if roster == [] else None,
        ),
        "purchases": len(buys), "sales": len(sales),
        "purchase_spend_eur": sum(t["amount"] for t in buys),
        "sale_proceeds_eur": sum(t["amount"] for t in sales),
        "net_transfer_cashflow_eur": cashflow,
        "premium_eur": sum(t["amount"] - mv for t, mv in priced) if priced else None,
        "median_premium_pct": round(median(premiums), 4) if premiums else None,
        "priced_purchases": len(priced), "complete_round_trips": len(pairs),
        "unknown_cost_basis_sales": unknown_sales, "holdings_with_cost_basis": with_basis,
        "squad_value_eur": sum(h["market_value"] for h in holdings) if squad_known else None,
        "decomposed_realized_pnl_eur": _sum_known(decomposed, "pnl_eur"),
        "realized_market_value_change_eur": _sum_known(decomposed, "market_value_change_eur"),
        "realized_buy_premium_eur": _sum_known(decomposed, "buy_premium_eur"),
        "realized_sale_premium_eur": _sum_known(decomposed, "sale_premium_eur"),
        "unrealized_market_value_change_eur": _sum_known(
            valued_holdings, "market_value_change_eur",
        ),
        "holdings_buy_premium_eur": _sum_known(valued_holdings, "buy_premium_eur"),
    }
    return {
        "summary": summary, "holdings": holdings, "trades": own_trades,
        "round_trips": pairs, "financial_timeline": timeline,
        "coverage": {
            "squad_available": roster is not None,
            "holdings_without_cost_basis": len(holdings) - with_basis,
            "holdings_missing_market_value": sum(h["market_value"] is None for h in holdings),
            "unknown_cost_basis_sales": unknown_sales,
            "realized_pnl_complete": unknown_sales == 0,
            "unrealized_pnl_complete": roster is not None
            and all(h["unrealized_pnl_eur"] is not None for h in holdings),
            "purchases_without_historical_market_value": len(buys) - len(priced),
            "update_boundary_purchases_excluded": sum(bool(t.get("update_boundary")) for t in buys),
            "round_trips_with_decomposition": sum(p["decomposition_complete"] for p in pairs),
            "holdings_with_decomposition": len(valued_holdings),
            "first_trade_at": own_trades[0]["at"] if own_trades else None,
            "last_trade_at": own_trades[-1]["at"] if own_trades else None,
            "transfer_history_exhaustive": False,
        },
    }


def build_manager_profiles(
    *,
    trades: list[dict],
    manager_rows: list[dict],
    user_id: str,
    user_name: str,
    budget: int | None,
    own_squad_rows: list[dict] | None,
    manager_performance: dict[str, dict] | None,
    generated_at: str,
    season_start: str | None,
) -> list[dict]:
    """Ein Schema für eigene und fremde Manager; Fehlstellen bleiben sichtbar.

    ``season_start`` ist die belegte Liga-Reset-Grenze, nicht der erste Spieltag.
    Fehlt sie, wird der übergebene Zeitraum ausgewertet und als ungesichert markiert.
    IDs haben Vorrang; Namenszuordnung ist nur für eindeutige Legacy-Namen erlaubt.
    ``budget_eur`` ist ausschließlich der beobachtete eigene Kontostand.
    """
    now, start = _date(generated_at), _date(season_start)
    if now is None or (season_start is not None and start is None):
        raise ValueError("generated_at und season_start müssen gültige Zeitpunkte sein")
    managers = {
        str(m["user_id"]).strip(): dict(m)
        for m in manager_rows if str(m.get("user_id") or "").strip()
    }
    uid = str(user_id).strip()
    managers.setdefault(uid, {"user_id": uid, "name": user_name})
    performance = {str(k): v for k, v in (manager_performance or {}).items()}
    for manager_id, raw in performance.items():
        managers.setdefault(manager_id, {"user_id": manager_id, "name": raw.get("unm", manager_id)})
    for trade in trades:
        for side in ("buyer", "seller"):
            manager_id = str(trade.get(f"{side}_id") or "").strip()
            if manager_id:
                managers.setdefault(manager_id, {
                    "user_id": manager_id, "name": str(trade.get(side) or manager_id).strip(),
                })
    names = Counter(str(m.get("name") or "").strip() for m in managers.values())
    by_name = {
        str(m.get("name") or "").strip(): manager_id for manager_id, m in managers.items()
        if str(m.get("name") or "").strip() and names[str(m.get("name") or "").strip()] == 1
    }

    scoped: list[tuple[dict, str, str]] = []
    seen: set[str | tuple] = set()
    excluded = 0
    fallback_counts: Counter = Counter()
    for row in trades:
        at, amount = _date(row.get("at")), _number(row.get("amount"))
        pid = str(row.get("player_id") or "")
        if at is None or at > now or (start is not None and at < start):
            excluded += 1
            continue
        if not pid or amount is None or amount < 0:
            excluded += 1
            continue
        parties = []
        for side in ("buyer", "seller"):
            manager_id = str(row.get(f"{side}_id") or "").strip()
            if not manager_id:
                manager_id = by_name.get(str(row.get(side) or "").strip(), "")
            parties.append(manager_id)
        identity = row.get("feed_id") or (pid, at.isoformat(), amount, *parties)
        if identity in seen:
            continue
        seen.add(identity)
        trade = {**row, "player_id": pid, "at": at.isoformat(), "amount": amount}
        scoped.append((trade, parties[0], parties[1]))
        for side, manager_id in zip(("buyer", "seller"), parties, strict=True):
            if manager_id and not row.get(f"{side}_id"):
                fallback_counts[manager_id] += 1
    scoped.sort(key=lambda item: (item[0]["at"], str(item[0].get("feed_id") or "")))

    profiles = []
    for manager_id, manager in managers.items():
        me = manager_id == uid
        roster = own_squad_rows if me and own_squad_rows is not None else manager.get("spieler")
        financial = _finances(manager_id, scoped, roster if isinstance(roster, list) else None)
        points = _performance(performance.get(manager_id), now)
        # Rangliste und Dashboard werden je Build geladen, Performance ist gecacht.
        # Die frische Gesamtsumme ergänzt keine fehlenden einzelnen Spieltagswerte.
        live, live_source = None, None
        for value, source in (
            (manager.get("punkte_saison"), "ranking"),
            (manager.get("dashboard_points"), "dashboard"),
            (points["live_points"], "performance"),
        ):
            live = _number(value)
            if live is not None:
                live_source = source
                break
        cash = _number(budget) if me else None
        financial["summary"]["budget_eur"] = cash
        financial["coverage"].update({
            "season_start": start.isoformat() if start is not None else None,
            "season_boundary_known": start is not None, "generated_at": now.isoformat(),
            "excluded_trades": excluded, "name_fallback_trades": fallback_counts[manager_id],
            "performance_available": points["performance_available"],
            "current_season_available": points["current_season_available"],
            "missing_matchday_points": points["missing_matchday_points"],
            "live_points_source": live_source,
            "budget_observed": cash is not None,
            "budget_unknown_reason": None if cash is not None
            else "own_budget_unavailable" if me else "rival_budget_not_observed",
        })
        profiles.append({
            "user_id": manager_id, "name": str(manager.get("name") or manager_id).strip(),
            "me": me, "rank": _number(manager.get("rank")), "img": manager.get("img"),
            "live_points": live, "completed_points": points["completed_points"],
            "matchday_history": points["matchday_history"], "seasons": points["seasons"],
            **financial,
        })
    return profiles
