"""Handels-Autopilot und Engine nebeneinander (Spec 2026-10-01-handels-autopilot.md, Punkt 6).

Handelsvorhaben (Quelle `trade`) gehören dem Handels-Autopiloten: Der Engine-Planer räumt sie
nie als „nicht mehr im Plan“ oder „nicht mehr geprüft“, überschreibt sie nicht mit eigenen
Entwürfen, und die Deckungsreparatur gibt sie nicht frei — ihr Geld deckt der Verkauf.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, Intent
from kickbase_agent.worker.intents import (
    RESULT_NOT_IN_PLAN,
    IntentDraft,
    release_funding_pending,
    upsert_intents,
)

NOW = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
H = timedelta(hours=1)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


def _intent(session, pid, kind="bid", *, trade=True, amount=3_000_000):
    row = Intent(created_at=NOW - H, updated_at=NOW - H, kind=kind, player_id=pid,
                 player_name=pid, amount=amount,
                 sources={"trade": True, "league_id": "L1"} if trade else {"league_id": "L1"},
                 listing_type="kickbase", execute_at=NOW + 5 * H, status="planned")
    session.add(row)
    session.commit()
    return row


def _status(session, pid, kind="bid"):
    row = session.scalars(select(Intent).where(Intent.player_id == pid,
                                               Intent.kind == kind)).one()
    session.refresh(row)
    return row.status, row.result


def test_the_engine_planner_never_clears_trade_intents(session):
    _intent(session, "tb")
    _intent(session, "ts", "sell")
    _intent(session, "eng", trade=False)

    upsert_intents(session, [], NOW, market_pids={"tb", "eng"})

    assert _status(session, "tb") == ("planned", None)
    assert _status(session, "ts", "sell") == ("planned", None)
    assert _status(session, "eng") == ("superseded", RESULT_NOT_IN_PLAN)


def test_reviewed_engine_runs_do_not_call_trade_bids_uncurated(session):
    """Im geprüften Modus räumt die Engine jedes Gebot ohne eigenen Kaufbeleg — außer dem
    Handelsgebot, dessen Kaufprüfung der Autopilot führt."""
    _intent(session, "tb")

    upsert_intents(session, [], NOW, market_pids={"tb"}, purchase_reviews={})

    assert _status(session, "tb") == ("planned", None)


def test_an_engine_draft_does_not_overwrite_a_trade_intent(session):
    _intent(session, "tb", "sell", amount=3_200_000)
    draft = IntentDraft(kind="sell", player_id="tb", player_name="tb", team_id="9", position=2,
                        amount=1, rationale="Engine-Verkauf", sources={"rule_id": "sell_weak"},
                        listing_type="kickbase", deadline=None, execute_at=NOW)

    upsert_intents(session, [draft], NOW)

    row = session.scalars(select(Intent)).one()
    session.refresh(row)
    assert (row.amount, row.sources["trade"], row.execute_at.replace(tzinfo=UTC)) == (
        3_200_000, True, NOW + 5 * H)


def test_the_funding_repair_never_releases_a_trade_reservation(session):
    trade = _intent(session, "tb")
    engine = _intent(session, "eng", trade=False)

    released = release_funding_pending(
        session, {"released_pending_ids": [trade.id, engine.id]}, "L1", NOW,
    )

    assert released == [engine.id]
    assert _status(session, "tb") == ("planned", None)
