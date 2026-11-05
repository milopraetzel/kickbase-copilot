"""Trading-Orchestrierung: aus Live-Daten den kompletten Trading-Plan bauen.

Bündelt Markt-Scan (Käufe für freie Slots) und Wendepunkt-Verkauf (bestehende
Trading-Holdings) zu einem TradingPlan — der Datenlieferant fürs Briefing und
später für den Executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from kickbase_agent.config import get_settings
from kickbase_agent.db.models import TransferAction
from kickbase_agent.decision.guardrails import protected_player_ids
from kickbase_agent.intel.history import load_history
from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.trading.finance import max_single_bid
from kickbase_agent.trading.finance_cycle import PurgeResult, finance_phase, plan_purge
from kickbase_agent.trading.pipeline import (
    TradingBuy,
    TradingConfig,
    TradingSell,
    plan_risk_sells,
    plan_trading_buys,
    plan_trading_sells,
)
from kickbase_agent.trading.scanner import momentum, rank_risers
from kickbase_agent.trading.zones import classify_squad, free_trading_slots

TRADING_RULE = "trading_buy"


@dataclass
class TradingPlan:
    buys: list[TradingBuy]
    sells: list[TradingSell]
    open_slots: int
    holdings: int
    phase: str = "invest"          # invest / purge / hold
    overdraft_available: int = 0   # Kaufkraft inkl. erlaubtem Minus
    purge: PurgeResult | None = None  # nur am Purge-Tag (Fr) gesetzt


def resolve_squad_limit(cfg: TradingConfig) -> int:
    """Kaderlimit: expliziter Config-Wert, sonst Settings (kickbase_squad_limit)."""
    if cfg.max_squad_size is not None:
        return cfg.max_squad_size
    return get_settings().kickbase_squad_limit


def load_trading_holdings(session: Session, squad_ids: list[str]) -> set[str]:
    """IDs der Kaderspieler, die als Trading gekauft wurden (rule_id-markiert)."""
    if not squad_ids:
        return set()
    rows = (
        session.query(TransferAction.player_id)
        .filter(
            TransferAction.rule_id == TRADING_RULE,
            TransferAction.action_type == "bid",
            TransferAction.status == "executed",
            TransferAction.player_id.in_(squad_ids),
        )
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


async def build_trading_plan(
    client: KickbaseClient,
    session: Session,
    league_id: str,
    cfg: TradingConfig,
    weekday: int | None = None,
) -> TradingPlan:
    market = await client.get_market(league_id)
    squad = await client.get_squad(league_id)
    budget = await client.get_budget(league_id)
    team_value = sum(sp.player.market_value for sp in squad)

    squad_ids = [sp.player.id for sp in squad]
    market_ids = [mp.player.id for mp in market]
    history = load_history(session, market_ids + squad_ids)

    # Kaufkandidaten: steigende Marktspieler mit genug Verlaufs-Historie
    market_rows = [
        (mp.player.id, mp.player.last_name, mp.player.market_value,
         [h.market_value for h in history.get(mp.player.id, [])])
        for mp in market
        if len(history.get(mp.player.id, [])) >= 3
    ]
    risers = rank_risers(market_rows, cfg.min_pct_per_day, cfg.window)
    open_slots = free_trading_slots(len(squad), resolve_squad_limit(cfg))
    buys = plan_trading_buys(risers, budget, team_value, open_slots, cfg)

    # Verkaufskandidaten: bestehende Trading-Holdings am Wendepunkt
    trading_ids = load_trading_holdings(session, squad_ids)
    zones = classify_squad(squad, trading_ids)
    holding_rows = [
        (sp.player.id, sp.player.last_name, sp.player.market_value,
         [h.market_value for h in history.get(sp.player.id, [])])
        for sp in zones.trading
    ]
    sells = plan_trading_sells(holding_rows, cfg)
    core_risk_rows = [
        (
            sp.player.id,
            sp.player.last_name,
            sp.player.market_value,
            [h.market_value for h in history.get(sp.player.id, [])],
            sp.player.lineup_prob,
        )
        for sp in zones.core
    ]
    sells.extend(
        plan_risk_sells(core_risk_rows, cfg, protected_ids=protected_player_ids())
    )

    # Weekly-Finance-Cycle: Phase + Overdraft-Kaufkraft; freitags der Purge.
    phase = finance_phase(weekday if weekday is not None else datetime.now(UTC).weekday())
    # Kaufkraft inkl. erlaubtem Minus (33%-Regel, bereinigter Teamwert).
    overdraft_available = max_single_bid(budget, team_value)
    purge = None
    if phase == "purge":
        holdings_with_mom = [
            (pid, name, mv, momentum(vals, cfg.window).daily_change)
            for pid, name, mv, vals in holding_rows
        ]
        # Notfall-Pool (Review-Fund): reicht das Trading-Material nicht, wird aus dem
        # restlichen Kader verkauft — billigste (Fringe) zuerst.
        fallback = sorted(
            ((sp.player.id, sp.player.last_name, sp.player.market_value)
             for sp in zones.core),
            key=lambda r: r[2],
        )
        purge = plan_purge(holdings_with_mom, budget, fallback=fallback)

    return TradingPlan(
        buys=buys,
        sells=sells,
        open_slots=open_slots,
        holdings=len(zones.trading),
        phase=phase,
        overdraft_available=overdraft_available,
        purge=purge,
    )
