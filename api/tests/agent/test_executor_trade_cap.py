"""Kappen-Stufe „Handel“ der Schreibstelle (Emil 01.10.2026).

„Ich will dass du auf dem normalen Transfermarkt auch bis 130% gehen könntest. aber nicht bei
Angeboten an andere Spieler.“ — Handelsgebote (Kaufzweck `handel`) auf Kickbase-Auktionen dürfen
bis 130 % MW; Manager-Listings und alle anderen Gebote bleiben unverändert (110 %, Kader-Bedarf
135 %). Eigene, unversiegelte Datei: `tests/agent/test_executor.py` ist über `guardrails.lock`
versiegelt und bleibt unverändert — seine 110-%-Fälle sind die Gegenprobe.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import kickbase_agent.config as cfg
from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
from kickbase_agent.db.models import Intent
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.worker import intents as wi
from kickbase_agent.worker.trade_autopilot import (
    Candidate,
    Capacity,
    Slot,
    plan_bids,
    sync_trade_intents,
)

from .test_executor import NOW, FakeClient
from .test_executor import (
    env as env,
)
from .test_executor import (
    session as session,
)

MV = 10_000_000


class _TradeClient(FakeClient):
    """Spieler 99 als fitte Kickbase-Auktion (oder Manager-Listing) mit steigendem Marktwert."""

    def __init__(self, *, seller: str = "") -> None:
        super().__init__()
        self.market = [SimpleNamespace(
            player=SimpleNamespace(id="99", market_value=MV, team_id="11", fitness_status=0,
                                   lineup_prob=2),
            price=MV, seller_id=seller,
        )]

    async def get_player(self, league_id, player_id):
        return SimpleNamespace(id="99", market_value=MV, mv_change_24h=150_000,
                               fitness_status=0, lineup_prob=2)


def _review(amount: int, purpose: str = "handel") -> dict:
    observed = NOW - timedelta(minutes=5)
    return {
        "schema_version": 1, "player_id": "99", "selected": True, "max_bid_eur": amount,
        "market_value_eur": MV, "purpose": purpose,
        "reasons": ["Handels-Radar: +16 % in 3 Tagen, letzter Tag steigend"],
        "observed_at": observed.isoformat(),
        "valid_until": (observed + timedelta(hours=3)).isoformat(),
    }


def _trade_bid(amount: int, *, purpose: str = "handel") -> PlannedAction:
    return PlannedAction(action_type="bid", league_id="TEST", player_id="99", amount=amount,
                         rule_id="intent-trade-bid", purchase_review=_review(amount, purpose))


def test_the_trade_stage_is_130_percent():
    assert DEFAULT_GUARDRAILS.max_bid_premium_trade == 1.30
    assert DEFAULT_GUARDRAILS.max_bid_premium == 1.10  # Standard unverändert


@pytest.mark.asyncio
async def test_a_trade_bid_on_a_kickbase_auction_may_pay_up_to_130_percent(session, env):
    env(dry="0", test_league="TEST")
    client = _TradeClient()

    r = await ActionExecutor(client, session).execute(_trade_bid(12_500_000), now=NOW)

    assert r.status == "executed", r.message
    assert client.calls == [("place_bid", "TEST", "99", 12_500_000)]


@pytest.mark.asyncio
async def test_above_130_percent_even_a_trade_bid_is_refused(session, env):
    env(dry="0", test_league="TEST")
    client = _TradeClient()

    r = await ActionExecutor(client, session).execute(_trade_bid(13_100_000), now=NOW)

    assert r.status == "refused" and "Overpay-Kappe" in r.message
    assert "Stufe Handel" in r.message and "130 %" in r.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_a_trade_bid_on_a_manager_listing_keeps_110_percent(session, env):
    """„aber nicht bei Angeboten an andere Spieler“."""
    env(dry="0", test_league="TEST")
    client = _TradeClient(seller="rival-7")

    r = await ActionExecutor(client, session).execute(_trade_bid(11_500_000), now=NOW)

    assert r.status == "refused" and "Stufe Standard" in r.message and "110 %" in r.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_other_purchase_purposes_keep_110_percent(session, env):
    env(dry="0", test_league="TEST")
    client = _TradeClient()

    r = await ActionExecutor(client, session).execute(
        _trade_bid(11_500_000, purpose="kaufhinweis"), now=NOW
    )

    assert r.status == "refused" and "Stufe Standard" in r.message
    assert client.calls == []


# ------------------------------------------------------------------ Handels-Autopilot


def _autopilot_intent(session, *, planned_at):
    """Vorhaben genau so, wie der Handels-Autopilot es anlegt (plan_bids + Abgleich)."""
    expires = planned_at + timedelta(hours=10)
    cand = Candidate(player_id="99", name="Steiger", mv=MV, max_bid=12_600_000, premium=0.26,
                     r3=0.18, expected=0.52, horizon_days=4, basis="Stufe ≥ 15 %",
                     expires_at=expires)
    slot = Slot("99", "Steiger", "11", 3, MV, MV, expires, fitness=0, lineup_prob=2)
    room = Capacity(0, 0, 0, 0, 0, 99_000_000, -50_000_000)
    [bid], _ = plan_bids([cand], {"99": slot}, now=planned_at, sale_by=None, capacity=room)
    sync_trade_intents(session, now=planned_at, league_id="L1", bids=[bid], sales=[],
                       skipped={}, radar_report_id=None, sale_by=None)
    return session.scalars(select(Intent)).one()


def _armed(env, monkeypatch):
    """Produktionsweg: nur AGENT_AUTO_INTENTS ärmt die Vorhaben, keine Testliga."""
    env(dry="0", test_league="")
    monkeypatch.setenv("AGENT_AUTO_INTENTS", "1")
    cfg._settings = None


@pytest.mark.asyncio
async def test_an_autopilot_intent_runs_through_the_single_write_point(session, env, monkeypatch):
    """Emil 01.10.2026: „alles soll automatisch geschehen“ — das Vorhaben des Autopiloten wird
    vom Intent-Executor über die Schreibstelle ausgeführt, mit Aufschlag bis 130 %."""
    _armed(env, monkeypatch)
    intent = _autopilot_intent(session, planned_at=NOW - timedelta(minutes=10))
    client = _TradeClient()

    status = await wi.execute_intent(session, intent, ActionExecutor(client, session), "L1",
                                     now=NOW)

    assert status == "executed", intent.result
    assert client.calls == [("place_bid", "L1", "99", 12_600_000)]


@pytest.mark.asyncio
async def test_an_autopilot_intent_on_a_manager_listing_is_still_refused(session, env,
                                                                          monkeypatch):
    """Zweite Sicherung: Wird aus der Auktion ein Manager-Listing, gilt an der Schreibstelle
    wieder die Standardstufe (110 %) — „aber nicht bei Angeboten an andere Spieler“."""
    _armed(env, monkeypatch)
    intent = _autopilot_intent(session, planned_at=NOW - timedelta(minutes=10))
    client = _TradeClient(seller="rival-7")

    status = await wi.execute_intent(session, intent, ActionExecutor(client, session), "L1",
                                     now=NOW)

    assert status == "failed" and "Stufe Standard" in (intent.result or "")
    assert client.calls == []


@pytest.mark.asyncio
async def test_an_expired_autopilot_review_never_bids(session, env, monkeypatch):
    """Erneuert der Autopilot die Kaufprüfung nicht mehr (Job steht), verweigert die
    Schreibstelle nach drei Stunden — kein Gebot auf alter Analyse."""
    _armed(env, monkeypatch)
    intent = _autopilot_intent(session, planned_at=NOW - timedelta(hours=3, minutes=1))
    client = _TradeClient()

    status = await wi.execute_intent(session, intent, ActionExecutor(client, session), "L1",
                                     now=NOW)

    assert status == "failed" and "Kaufprüfung abgelaufen" in (intent.result or "")
    assert client.calls == []


# ------------------------------------------------------------------ Stufe Elf-Upgrade (125 %)


def _upgrade_review(amount: int, *, seller: str = "") -> dict:
    observed = NOW - timedelta(minutes=5)
    owned = [str(sp.player.id) for sp in FakeClient().squad if str(sp.player.id) != "99"]
    return {
        "schema_version": 1,
        "engine_version": "team-milp-v1",
        "plan_id": "p-up",
        "player_id": "99",
        "selected": True,
        "eligible": True,
        "max_bid_eur": amount,
        "target_bid_eur": amount,
        "market_value_eur": MV,
        "purpose": "lineup_upgrade",
        "listing_type": "manager" if seller else "kickbase",
        "seller_id": seller,
        "reasons": ["Gebotsregel V3 (+1,5 % seit gestern): Zielgebot 12.000.000 €"],
        "bid_scenarios": [{"amount": amount, "win_probability": None, "selected": True}],
        "observed_at": observed.isoformat(),
        "valid_until": (observed + timedelta(minutes=30)).isoformat(),
        "team_plan": {
            "schema_version": 1,
            "engine_version": "team-milp-v1",
            "plan_id": "p-up",
            "observed_at": observed.isoformat(),
            "budget_eur": 30_000_000,
            "open_bid_exposure_eur": 0,
            "owned_player_ids": owned,
            "open_bids": [],
            "purchases": [
                {"player_id": "99", "amount_eur": amount, "max_bid_eur": amount}
            ],
            "reserved_cash_eur": amount,
            "swap_sales": [],
            "team_value_eur": 120_000_000,
        },
    }


def _upgrade_bid(amount: int, *, seller: str = "") -> PlannedAction:
    return PlannedAction(
        action_type="bid",
        league_id="TEST",
        player_id="99",
        amount=amount,
        rule_id="intent-bid",
        purchase_review=_upgrade_review(amount, seller=seller),
    )


def _upgrade_client(*, seller: str = "") -> _TradeClient:
    client = _TradeClient(seller=seller)
    client.squad = [sp for sp in client.squad if str(sp.player.id) != "99"]
    return client


def test_the_upgrade_stage_is_125_percent():
    assert DEFAULT_GUARDRAILS.max_bid_premium_upgrade == 1.25
    assert DEFAULT_GUARDRAILS.max_bid_premium == 1.10


@pytest.mark.asyncio
async def test_a_lineup_upgrade_on_a_kickbase_auction_may_pay_up_to_125_percent(session, env):
    env(dry="0", test_league="TEST")
    client = _upgrade_client()

    r = await ActionExecutor(client, session, user_id="u1").execute(
        _upgrade_bid(12_000_000), now=NOW
    )

    assert r.status == "executed", r.message
    assert client.calls == [("place_bid", "TEST", "99", 12_000_000)]


@pytest.mark.asyncio
async def test_a_lineup_upgrade_above_125_percent_is_refused(session, env):
    env(dry="0", test_league="TEST")
    client = _upgrade_client()

    r = await ActionExecutor(client, session, user_id="u1").execute(
        _upgrade_bid(12_600_000), now=NOW
    )

    assert r.status == "refused" and "Stufe Elf-Upgrade" in r.message and "125 %" in r.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_a_lineup_upgrade_on_a_manager_listing_keeps_110_percent(session, env):
    env(dry="0", test_league="TEST")
    client = _upgrade_client(seller="rival-7")

    r = await ActionExecutor(client, session, user_id="u1").execute(
        _upgrade_bid(11_500_000, seller="rival-7"), now=NOW
    )

    assert r.status == "refused" and "Stufe Standard" in r.message and "110 %" in r.message
    assert client.calls == []

