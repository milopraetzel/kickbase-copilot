"""Tägliches Briefing: Dashboard-Zahlen + geplante Aktionen als Discord-Karte."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import LearningEvidence, PlayerHistory, TransferAction
from kickbase_agent.decision.lineup import Lineup
from kickbase_agent.kickbase.models import RankingEntry, SquadPlayer
from kickbase_agent.scouts.ligainsider.models import Availability, Status
from kickbase_agent.trading.orchestrator import TradingPlan


@dataclass
class Briefing:
    league_name: str
    rank: int
    total_managers: int
    points: int
    team_value: int
    budget: int
    day_change: int
    plan: Plan
    trading: TradingPlan | None = None
    lineup: Lineup | None = None
    # (Icon, Name, Grund) je nicht-verfügbarem Kaderspieler — für die LI-Zeile.
    availability: list[tuple[str, str, str]] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    learnings: list[str] = field(default_factory=list)


_BERLIN = ZoneInfo("Europe/Berlin")
DISCORD_DESCRIPTION_LIMIT = 4096
_LEARNING_SUMMARY_LIMIT = 240


def briefing_window_start(now: datetime, period: str | None) -> datetime:
    """Start des Fensters seit dem jeweils vorherigen Tagesbericht."""
    local = now.astimezone(_BERLIN)
    if period == "morning":
        start = (local - timedelta(days=1)).replace(hour=22, minute=15, second=0, microsecond=0)
    elif period == "evening":
        start = local.replace(hour=8, minute=15, second=0, microsecond=0)
    else:
        return now - timedelta(hours=14)
    return start.astimezone(UTC)


def summarize_report_window(
    session: Session, league_id: str, since: datetime
) -> tuple[list[str], list[str]]:
    """Echte Executor-Aktionen und Lernbelege seit dem vorherigen Bericht verdichten."""
    actions = (
        session.query(TransferAction)
        .filter(
            TransferAction.league_id == league_id,
            func.coalesce(TransferAction.resolved_at, TransferAction.created_at) >= since,
            TransferAction.status == "executed",
            TransferAction.dry_run.is_(False),
        )
        .order_by(func.coalesce(TransferAction.resolved_at, TransferAction.created_at).asc())
        .all()
    )
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.action_type] = counts.get(action.action_type, 0) + 1
    completed: list[str] = []
    if counts.get("bid"):
        completed.append(
            f"{counts['bid']} Gebot platziert; Zuschlag bleibt bis zum Reconcile offen."
        )
    if counts.get("list"):
        completed.append(
            f"{counts['list']} Listing-Aktion ausgeführt; "
            "Live-Listing bleibt bis zum Read-back offen."
        )
    if counts.get("cancel_bid"):
        completed.append(f"{counts['cancel_bid']} eigenes Gebot storniert.")
    disposals = counts.get("sell", 0) + counts.get("accept", 0)
    if disposals:
        completed.append(
            f"{disposals} Verkaufsaktion ausgeführt; Abgang zählt erst nach Live-Reconcile."
        )
    if counts.get("lineup"):
        completed.append(f"{counts['lineup']} Aufstellung gesetzt.")
    if counts.get("bonus"):
        completed.append(f"{counts['bonus']} Prämienaktion ausgeführt.")
    known = {"bid", "list", "cancel_bid", "sell", "accept", "lineup", "bonus"}
    other = sum(count for kind, count in counts.items() if kind not in known)
    if other:
        completed.append(f"{other} weitere Executor-Aktion ausgeführt.")
    if not completed:
        completed.append(
            "Keine neue Transferaktion; Markt, Kader und Startelfpfad wurden neu bewertet."
        )

    evidence = (
        session.query(LearningEvidence)
        .filter(LearningEvidence.observed_at >= since)
        .order_by(LearningEvidence.observed_at.desc())
        .limit(3)
        .all()
    )
    verdict = {"confirm": "bestätigt", "refute": "widerlegt"}
    learned = [
        f"[{verdict.get(row.verdict, row.verdict)}] "
        f"{row.note.strip()[:_LEARNING_SUMMARY_LIMIT]}"
        for row in evidence
        if row.note.strip()
    ]
    if not learned:
        learned.append(
            "Keine neue belegte Erkenntnis; offene Startelf-, Fitness- und "
            "Rollenfragen bleiben offen."
        )
    return completed, learned


def team_value_change(session: Session, player_ids: list[str], today: date) -> int:
    """Teamwert-Δ (heute vs. gestern) — nur Spieler mit Wert an BEIDEN Tagen.

    So verzerren neu dazugekommene oder fehlende Snapshots das Ergebnis nicht.
    """
    def _values_at(day: date) -> dict[str, int]:
        moment = datetime(day.year, day.month, day.day, tzinfo=UTC)
        rows = (
            session.query(PlayerHistory)
            .filter(
                PlayerHistory.player_id.in_(player_ids),
                PlayerHistory.recorded_at == moment,
            )
            .all()
        )
        return {r.player_id: r.market_value for r in rows}

    if not player_ids:
        return 0
    today_vals = _values_at(today)
    yesterday_vals = _values_at(today - timedelta(days=1))
    common = set(today_vals) & set(yesterday_vals)
    return sum(today_vals[p] - yesterday_vals[p] for p in common)


_STATUS_ICON = {Status.OUT: "🔴", Status.BENCH: "🟡"}


def _availability_display(
    squad: list[SquadPlayer], availabilities: list[Availability]
) -> list[tuple[str, str, str]]:
    """Nur nicht-verfügbare Kaderspieler (raus/Bank) als (Icon, Name, Grund)."""
    name_by_id = {sp.player.id: sp.player.last_name or sp.player.id for sp in squad}
    out: list[tuple[str, str, str]] = []
    for a in availabilities:
        icon = _STATUS_ICON.get(a.status)
        if icon:
            out.append((icon, name_by_id.get(a.player_id, a.player_id), a.reason))
    return out


def build_briefing(
    league_name: str,
    user_id: str,
    ranking: list[RankingEntry],
    squad: list[SquadPlayer],
    budget: int,
    day_change: int,
    plan: Plan,
    trading: TradingPlan | None = None,
    lineup: Lineup | None = None,
    availabilities: list[Availability] | None = None,
    completed: list[str] | None = None,
    learnings: list[str] | None = None,
) -> Briefing:
    mine = next((r for r in ranking if r.user_id == user_id), None)
    return Briefing(
        league_name=league_name,
        rank=mine.rank if mine else 0,
        total_managers=len(ranking),
        points=mine.points if mine else 0,
        team_value=sum(sp.player.market_value for sp in squad),
        budget=budget,
        day_change=day_change,
        plan=plan,
        trading=trading,
        lineup=lineup,
        availability=_availability_display(squad, availabilities or []),
        completed=list(completed or []),
        learnings=list(learnings or []),
    )


def _m(value: int) -> str:
    return f"{value / 1_000_000:.1f}M"


def _signed_m(value: int) -> str:
    return f"{'▲' if value >= 0 else '▼'}{abs(value) / 1_000_000:.1f}M"


_NAME_W = 15


def _table(proposals: list, amount_label: str) -> str:
    """Monospace-Tabelle: Spieler · Bewertung% · Betrag (in einem Code-Block)."""
    header = f"{'SPIELER':<{_NAME_W}} {'BEW':>4} {amount_label:>7}"
    if not proposals:
        return f"```\n{header}\n{'—':<{_NAME_W}}\n```"
    rows = [header]
    for p in proposals[:6]:
        name = (p.player_name or p.action.player_id or "?")[:_NAME_W]
        pct = f"{round(p.score * 100)}%"
        amount = _m(p.action.amount or 0)
        rows.append(f"{name:<{_NAME_W}} {pct:>4} {amount:>7}")
    return "```\n" + "\n".join(rows) + "\n```"


_PHASE_LABEL = {"invest": "🟢 Invest", "purge": "🔴 Purge (Fr)", "hold": "⏸️ Halten"}


def _trading_block(trading: TradingPlan) -> list[str]:
    lines = [
        f"__🏭 Trading-Fabrik__  · {_PHASE_LABEL.get(trading.phase, trading.phase)} · "
        f"**{_m(trading.overdraft_available)}€** Kaufkraft (inkl. Overdraft) · "
        f"{trading.open_slots} Slots · {trading.holdings} Holdings"
    ]
    if trading.purge is not None:
        p = trading.purge
        if p.sells:
            names = ", ".join(s[1] for s in p.sells)
            lines.append(
                f"🔴 Purge: verkaufe {len(p.sells)} Trader ({names}) → Konto {_m(p.final_balance)}€"
            )
        if p.shortfall > 0:
            lines.append(f"⚠️ Failsafe: {_m(p.shortfall)}€ Unterdeckung — Eskalation nötig!")
    if trading.buys:
        rows = [f"{'SPIELER':<{_NAME_W}} {'+€/T':>4} {'GEBOT':>7}"]
        for b in trading.buys[:6]:
            per_day = f"{b.expected_gain / 4 / 1_000_000:.1f}"
            rows.append(f"{b.name[:_NAME_W]:<{_NAME_W}} {per_day:>4} {_m(b.bid):>7}")
        lines.append("Kaufen:```\n" + "\n".join(rows) + "\n```")
    if trading.sells:
        rows = [f"{s.name[:_NAME_W]:<{_NAME_W}} {_m(s.market_value):>7}  {s.reason}"
                for s in trading.sells[:6]]
        lines.append("Verkaufen (Wendepunkt):```\n" + "\n".join(rows) + "\n```")
    if not trading.buys and not trading.sells:
        lines.append("-# keine Trades heute")
    return lines


_POS_LABEL = {1: "TW ", 2: "ABW", 3: "MIT", 4: "STU"}


def _lineup_block(lineup: Lineup) -> list[str]:
    """Empfohlene Startelf, gruppiert nach Mannschaftsteil."""
    if not lineup.starters:
        return [
            "__⚽ Empfohlene Aufstellung__",
            "-# Kader unvollständig — bisherige Aufstellung bleibt.",
        ]
    rows = []
    for pos in (1, 2, 3, 4):
        names = [p.name.split()[-1] for p in lineup.starters if p.position == pos]
        if names:
            rows.append(f"{_POS_LABEL[pos]}  {', '.join(names)}")
    lines = [
        f"__⚽ Empfohlene Aufstellung · {lineup.formation}__",
        "```\n" + "\n".join(rows) + "\n```",
    ]
    bench = [p.name.split()[-1] for p in lineup.bench]
    if bench:
        lines.append(f"-# Bank: {', '.join(bench)}")
    return lines


def format_briefing(briefing: Briefing) -> str:
    """Editorialer, tabellarischer Body fürs Discord-Embed."""
    rank = f"{briefing.rank}/{briefing.total_managers}" if briefing.rank else "—"
    change = f"  ({_signed_m(briefing.day_change)})" if briefing.day_change else ""
    lines = [
        f"**`{rank}`** Platz   **`{_m(briefing.budget)}€`** Budget "
        f"  **`{_m(briefing.team_value)}€`** Teamwert{change}",
        "",
        "__✅ Was ich gemacht habe__",
        *[f"• {line}" for line in briefing.completed],
        "",
        "__🧭 Mein Plan__",
        f"__🔥 Geplante Käufe ({len(briefing.plan.buys)})__",
        _table(briefing.plan.buys, "GEBOT"),
        f"__💸 Geplante Verkäufe ({len(briefing.plan.sells)})__",
        _table(briefing.plan.sells, "ERLÖS"),
    ]
    if briefing.trading is not None:
        lines.append("")
        lines += _trading_block(briefing.trading)
    if briefing.lineup is not None:
        lines.append("")
        lines += _lineup_block(briefing.lineup)
    if briefing.availability:
        parts = [f"{icon} {name} ({reason.removeprefix('LI: ')})"
                 for icon, name, reason in briefing.availability[:8]]
        lines.append("__🩺 LigaInsider-Status__")
        lines.append(" · ".join(parts))
    if briefing.plan.profiteurs:
        picks = [
            f"**{p.name.split()[-1]}** {_m(p.market_value)} "
            f"({'Job-Erbe von' if p.same_position else 'profitiert:'} "
            f"{p.injured.split(' (')[0]} raus)"
            for p in briefing.plan.profiteurs[:3]
        ]
        lines.append("__💎 Profiteure (Markt)__")
        lines.append(" · ".join(picks))
    lines.append("")
    lines.append("__🧠 Was ich gelernt habe__")
    lines.extend(f"• {line}" for line in briefing.learnings)
    from kickbase_agent.trading.trade_budget import budget_line
    lines.append("-# " + budget_line(
        briefing.plan.trades_used, briefing.plan.trade_limit,
        getattr(briefing.plan, "trade_count_saturated", False))
        + " · Vorschau — Auto-Transfers nach Backtest")
    text = "\n".join(lines)
    if len(text) <= DISCORD_DESCRIPTION_LIMIT:
        return text
    marker = "\n-# Discord-Zusammenfassung gekürzt; Details stehen in der Zentrale."
    return text[: DISCORD_DESCRIPTION_LIMIT - len(marker)] + marker
