"""pbaa-Präzisierung + Kappen-Härtung (01.09., L-093, Emils GO) — neue Zusicherungen.

Eigene Datei neben der versiegelten tests/agent/test_executor.py (L-087): Die
versiegelte fail-closed-Zusicherung (fehlendes pbaa BEI unbestimmbaren Geboten
⇒ refused) bleibt dort unangetastet bestehen; HIER stehen die Präzisierung
(null verifizierte Gebote ⇒ Konto ist Konto-nach-Geboten) und die
Marktwert-Härtung.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.config as cfg
from kickbase_agent.agent.executor import (
    ActionExecutor,
    PlannedAction,
    PreflightState,
    default_preflight,
)
from kickbase_agent.db.models import Base
from kickbase_agent.kickbase.models import Fixture

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


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


class _NoPbaaClient:
    """Budget-Endpoint ohne pbaa (L-093: bei 0 offenen Geboten der Normalfall)."""

    def __init__(self, *, budget=4_000_000, own_bids=None, bids_raise=False):
        self.budget = budget
        self.own_bids = [] if own_bids is None else own_bids
        self.bids_raise = bids_raise
        self.calls: list[str] = []

    async def get_budget(self, league_id):
        return self.budget

    async def get_budget_fields(self, league_id):
        return {"b": self.budget, "pbas": self.budget + 1}  # kein pbaa

    async def get_market(self, league_id):
        return []  # Ziel nicht am Markt — für den Zustand hier irrelevant

    async def get_squad(self, league_id):
        return []

    async def get_own_bids(self, league_id, user_id):
        self.calls.append("own_bids")
        if self.bids_raise:
            raise RuntimeError("API down")
        return self.own_bids

    async def get_matchdays(self):
        return [Fixture(matchday=3, home_team_id="1", away_team_id="2",
                        kickoff=NOW + timedelta(days=30))]


def _bid(amount=1_000_000):
    return PlannedAction(
        action_type="bid", league_id="TEST", player_id="99", amount=amount, rule_id="r1"
    )


@pytest.mark.asyncio
async def test_null_verifizierte_gebote_leiten_konto_als_after_bids_ab():
    cb = default_preflight(_NoPbaaClient(), user_id="U1")
    state = await cb(_bid())
    assert state.budget_after_bids == 4_000_000  # Konto − 0 = Konto, exakt


@pytest.mark.asyncio
async def test_vorhandene_gebote_ohne_pbaa_bleiben_fail_closed():
    client = _NoPbaaClient(own_bids=[{"amount": 500_000, "player_id": "7"}])
    state = await default_preflight(client, user_id="U1")(_bid())
    assert state.budget_after_bids is None  # _preflight_veto refused dann wie versiegelt


@pytest.mark.asyncio
async def test_scheiternder_gebots_read_bleibt_fail_closed():
    client = _NoPbaaClient(bids_raise=True)
    state = await default_preflight(client, user_id="U1")(_bid())
    assert state.budget_after_bids is None


@pytest.mark.asyncio
async def test_ohne_user_id_keine_ableitung():
    # Genau der Zustand der versiegelten Zusicherung: nichts verifizierbar.
    state = await default_preflight(_NoPbaaClient(), user_id=None)(_bid())
    assert state.budget_after_bids is None


# --- Kappen-Härtung: Gebot ohne ermittelbaren Marktwert -----------------------


def _stub(state: PreflightState):
    async def cb(action):
        return state

    return cb


class _DummyClient:
    async def place_bid(self, *a, **k):  # darf nie erreicht werden
        raise AssertionError("Dispatch trotz Refuse")


@pytest.mark.asyncio
async def test_gebot_ohne_marktwert_wird_gekappt_refused(session, env):
    env()
    state = PreflightState(budget=99_000_000, on_market=True, current_price=1_000_000)
    ex = ActionExecutor(_DummyClient(), session, preflight=_stub(state))
    result = await ex.execute(_bid(), now=NOW)
    assert result.status == "refused"
    assert "Marktwert nicht ermittelbar" in result.message
    # Pflicht (d8-Bedingung 1b): Der Text darf den pbaa-Substring NICHT tragen,
    # sonst hielte der Intent-Retry das Vorhaben für transient heilbar.
    assert "pbaa" not in result.message
