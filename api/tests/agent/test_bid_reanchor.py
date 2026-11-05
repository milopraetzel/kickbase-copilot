"""Gebots-Nachanker im Preflight (02.09.2026, Onyeka-Lehre).

Neue Datei neben der versiegelten tests/agent/test_executor.py (L-087). Die
versiegelten Zusicherungen bleiben unangetastet: Manager-Listing weit unter MW
⇒ refused (5080), Kickbase-Listing weit unter MW ⇒ Versuch. HIER steht die
Präzisierung für kleine Drift: liegt das Gebot zur Ausführung knapp unter dem
aktuellen Marktwert (22-Uhr-Sprung), zieht der Executor es auf MW + Psycho-
Offset nach — statt still zu scheitern wie Onyeka am 01.09. um 22:15.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.config as cfg
from kickbase_agent.agent.executor import ActionExecutor, PlannedAction, PreflightState
from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS

NOW = datetime(2026, 9, 1, 20, 12, tzinfo=UTC)
ONYEKA_MV = 8_356_668
ONYEKA_BID = 8_158_168
OFFSET = DEFAULT_GUARDRAILS.bid_psych_offset


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

    async def place_bid(self, league_id, player_id, price):
        self.calls.append(("place_bid", league_id, player_id, price))
        return {"ok": True}


def _stub(state: PreflightState):
    async def cb(action: PlannedAction) -> PreflightState:
        return state

    return cb


def _bid(amount: int, rule: str = "intent-bid") -> PlannedAction:
    return PlannedAction(
        action_type="bid",
        league_id="TEST",
        player_id="7463",
        amount=amount,
        rule_id=rule,
        rationale="Onyeka: Score 0.66",
        # Diese Tests isolieren die Nachankerung; die Kaufprüfung erlaubt alle
        # hier geprüften Beträge. Eigene Tests prüfen ihre harte Preisobergrenze.
        purchase_review={
            "schema_version": 1, "player_id": "7463", "selected": True,
            "max_bid_eur": 9_000_000, "market_value_eur": ONYEKA_MV,
            "observed_at": (NOW - timedelta(minutes=5)).isoformat(),
            "valid_until": (NOW + timedelta(minutes=20)).isoformat(),
            "purpose": "starter", "reasons": ["Isolierter Nachanker-Test"],
        },
    )


def _state(mv: int, *, manager: bool) -> PreflightState:
    return PreflightState(
        on_market=True, is_manager_listing=manager, current_mv=mv,
        target_fitness_status=0, target_lineup_prob=1, target_mv_change_24h=1,
    )


@pytest.mark.asyncio
async def test_onyeka_fall_wird_nachgezogen_statt_zu_scheitern(session, env):
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(ONYEKA_MV, manager=False)))

    result = await ex.execute(_bid(ONYEKA_BID), now=NOW)

    assert result.status == "executed", result.message
    assert client.calls == [("place_bid", "TEST", "7463", ONYEKA_MV + OFFSET)]
    row = session.query(TransferAction).one()
    assert row.amount == ONYEKA_MV + OFFSET
    assert row.payload["reanchored_from"] == ONYEKA_BID
    assert "Nachanker" in (row.rationale or "")


@pytest.mark.asyncio
async def test_manager_listing_mit_kleiner_drift_wird_ebenfalls_nachgezogen(session, env):
    """Vorher: refused (5080). Jetzt: Anker MW + Offset, weil die Lücke Drift ist."""
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(ONYEKA_MV, manager=True)))

    result = await ex.execute(_bid(ONYEKA_BID), now=NOW)

    assert result.status == "executed", result.message
    assert client.calls == [("place_bid", "TEST", "7463", ONYEKA_MV + OFFSET)]


@pytest.mark.asyncio
async def test_grosse_luecke_bleibt_beim_alten_verhalten_kickbase(session, env):
    """5 M gegen MW 6 M (+20 %) ist keine Drift: Kickbase-Listing versucht unverändert."""
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(6_000_000, manager=False)))

    result = await ex.execute(_bid(5_000_000), now=NOW)

    assert result.status == "executed", result.message
    assert client.calls == [("place_bid", "TEST", "7463", 5_000_000)]
    row = session.query(TransferAction).one()
    assert "reanchored_from" not in (row.payload or {})


@pytest.mark.asyncio
async def test_grosse_luecke_bleibt_beim_alten_verhalten_manager(session, env):
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(6_000_000, manager=True)))

    result = await ex.execute(_bid(5_000_000), now=NOW)

    assert result.status == "refused"
    assert "5080" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_grenze_liegt_bei_zehn_prozent_des_geplanten_betrags(session, env):
    """Gebot 7,5 M gegen MW 8,36 M: Anker 8,37 M > 7,5 M × 1,10 = 8,25 M → kein Nachanker."""
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(ONYEKA_MV, manager=False)))

    result = await ex.execute(_bid(7_500_000), now=NOW)

    assert result.status == "executed", result.message
    assert client.calls == [("place_bid", "TEST", "7463", 7_500_000)]


@pytest.mark.asyncio
async def test_gebot_ueber_marktwert_bleibt_unangetastet(session, env):
    env()
    client = _Client()
    ex = ActionExecutor(client, session, preflight=_stub(_state(ONYEKA_MV, manager=False)))

    result = await ex.execute(_bid(ONYEKA_MV + 200_000), now=NOW)

    assert result.status == "executed", result.message
    assert client.calls == [("place_bid", "TEST", "7463", ONYEKA_MV + 200_000)]
