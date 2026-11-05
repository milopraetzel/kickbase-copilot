"""Intra-Day-Puls: mehrmals täglich recherchieren + entscheiden — LEISE.

Marktwerte updaten nur 1×/Tag (~22:00), aber Transfermarkt-Listings, LigaInsider
(Verletzung/Startelf) und Gegner-Budgets ändern sich laufend. Diese Slots ziehen
frische Daten und posten NUR, wenn es echten Handlungsbedarf gibt (kein Spam).
Solange AGENT_DRY_RUN=1 sind es reine Vorschläge.
"""

from __future__ import annotations

from kickbase_agent.cli.plan import Plan, build_plan
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.lineup_service import recommend_lineup
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify.discord_channel import DiscordChannel
from kickbase_agent.scouts.ligainsider.scraper import clear_cache
from kickbase_agent.trading.orchestrator import TradingPlan, build_trading_plan
from kickbase_agent.trading.pipeline import TradingConfig


def _m(value: int | None) -> str:
    return f"{(value or 0) / 1_000_000:.1f}M"


def format_pulse(label: str, plan: Plan, trading: TradingPlan | None) -> str | None:
    """Kompakte Puls-Nachricht — oder None, wenn nichts zu tun ist (leise)."""
    t_buys = trading.buys if trading else []
    t_sells = trading.sells if trading else []
    if not (plan.buys or plan.sells or t_buys or t_sells):
        return None

    lines = [f"🔔 **Markt-Puls · {label}**"]
    if t_buys:
        picks = " · ".join(f"{b.name} {_m(b.bid)}" for b in t_buys[:5])
        lines.append(f"🏭 Trading kaufen ({len(t_buys)}): {picks}")
    if plan.buys:
        picks = " · ".join(f"{p.player_name or p.action.player_id} {_m(p.action.amount)}"
                           for p in plan.buys[:5])
        lines.append(f"🟢 Kaufchance ({len(plan.buys)}): {picks}")
    if t_sells:
        picks = " · ".join(f"{s.name}" for s in t_sells[:6])
        lines.append(f"🔴 Verkaufen ({len(t_sells)}): {picks}")
    elif plan.sells:
        picks = " · ".join(f"{p.player_name or p.action.player_id}" for p in plan.sells[:6])
        lines.append(f"🔴 Verkaufen ({len(plan.sells)}): {picks}")
    lines.append("-# Dry-Run — Vorschau, nichts ausgeführt")
    return "\n".join(lines)


async def run_pulse(channel: DiscordChannel, label: str) -> bool:
    """Frische Recherche + Entscheidung; postet nur bei Handlungsbedarf. True = gepostet."""
    plan = await build_plan()
    client, _auth = await make_kickbase_client()
    with SessionLocal() as session:
        trading = await build_trading_plan(client, session, plan.league_id, TradingConfig())
    msg = format_pulse(label, plan, trading)
    if msg is None:
        return False
    await channel.send_info(msg)
    return True


_POS = {1: "TW", 2: "ABW", 3: "MIT", 4: "STU"}


async def run_lineup_leak(channel: DiscordChannel) -> None:
    """Freitag-Abend: frischer LI-Scrape → finale Startelf + Verletzungs-Alarm."""
    clear_cache()  # erzwingt frische LigaInsider-Daten (Lineup-Leak)
    plan = await build_plan()
    client, _auth = await make_kickbase_client()
    with SessionLocal() as session:
        rec = await recommend_lineup(client, session, plan.lineup_squad)
    lu = rec.lineup
    out = [a for a in rec.scout.availabilities if a.status.value == "out"]
    lines = ["📋 **Lineup-Leak · Fr** — finale LigaInsider-Startelf"]
    if lu.starters:
        for pos in (1, 2, 3, 4):
            names = [p.name.split()[-1] for p in lu.starters if p.position == pos]
            if names:
                lines.append(f"{_POS[pos]}  {', '.join(names)}")
        lines.insert(1, f"⚽ **{lu.formation}**")
    if out:
        name_by_id = {sp.player.id: (sp.player.last_name or sp.player.id) for sp in plan.squad}
        alerts = " · ".join(f"{name_by_id.get(a.player_id, a.player_id)}" for a in out)
        lines.append(f"🔴 Verletzt/gesperrt: {alerts}")
    await channel.send_info("\n".join(lines))
