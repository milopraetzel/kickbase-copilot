"""Tausch-Verkauf und Starter-Schutz (02.09.2026).

Neue Datei neben der versiegelten tests/agent/test_executor.py (L-087): Der
Starter-Schutz autonomer Pfade bleibt (ohne `swap` refused); ein Tausch-
Verkauf, dessen Ersatz da ist (`swap=True`), darf den Starter abgeben — die
Kader-Deckung prüft weiterhin (a2).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.config as cfg
from kickbase_agent.agent.executor import ActionExecutor, PlannedAction, PreflightState
from kickbase_agent.db.models import Base

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


@pytest.fixture
def env(monkeypatch):
    def _set(dry="0", test_league="TEST"):
        monkeypatch.setenv("AGENT_DRY_RUN", dry)
        monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", test_league)
        monkeypatch.setenv("AGENT_ALLOW_LIVE", "0")
        monkeypatch.setenv("AGENT_AUTO_PURGE", "0")
        monkeypatch.setenv("AGENT_AUTO_BONUS", "0")
        monkeypatch.setenv("AGENT_AUTO_LINEUP", "0")
        monkeypatch.setenv("AGENT_APPROVE_LIVE", "0")
        monkeypatch.setenv("AGENT_AUTO_TRADING", "0")
        monkeypatch.setenv("AGENT_AUTO_INTENTS", "0")
        cfg._settings = None

    yield _set
    cfg._settings = None


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def sell_to_kickbase(self, league_id, player_id):
        self.calls.append(("sell", league_id, player_id))
        return {"sold": True}


def _stub(state: PreflightState):
    async def cb(action: PlannedAction) -> PreflightState:
        return state

    return cb


def _state() -> PreflightState:
    squad = {"gk": 1, "s1": 4}
    squad.update({f"d{i}": 2 for i in range(6)})  # 12er-Kader: Ersatz ist da
    squad.update({f"m{i}": 3 for i in range(4)})
    return PreflightState(
        in_squad=True,
        current_value=9_000_000,
        squad_positions=squad,
        starter_ids=frozenset({"d1"}),
        matchday_deadline=NOW + timedelta(days=2),
    )


def _sell(swap: bool) -> PlannedAction:
    return PlannedAction(
        action_type="sell", league_id="TEST", player_id="d1", amount=9_000_000,
        rule_id="intent-sell", swap=swap,
    )


@pytest.mark.asyncio
async def test_autonomer_starterverkauf_bleibt_ohne_tausch_verboten(session, env):
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state()))
    result = await ex.execute(_sell(swap=False), now=NOW)
    assert result.status == "refused"
    assert "Aufstellung" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_tausch_verkauf_darf_den_starter_abgeben(session, env):
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state()))
    result = await ex.execute(_sell(swap=True), now=NOW)
    assert result.status == "executed", result.message
    assert client.calls == [("sell", "TEST", "d1")]


@pytest.mark.asyncio
async def test_tausch_respektiert_weiter_die_kader_deckung(session, env):
    """Ab T-120 aus einem 11er-Kader: auch der Tausch verkauft nicht unter 11."""
    env()
    state = _state()
    state.squad_positions = {k: v for k, v in state.squad_positions.items() if k != "d5"}
    state.matchday_deadline = NOW + timedelta(minutes=90)
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(state))
    result = await ex.execute(_sell(swap=True), now=NOW)
    assert result.status == "refused"
    assert "Kader-Deckung" in result.message
