"""Echte archivierte Transferbelege; Domain-Vorhaben werden passend konstruiert."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.trading.expectation import calibration, settle_bid_expectations

FEED_PATH = Path(__file__).parents[1] / "fixtures/league_history_20260912/feed-matching.json"
FEED = json.loads(FEED_PATH.read_text())
RECEIPT = next(e for e in FEED
               if e["data"].get("pi") == "10134" and e["data"].get("byr"))
AT = datetime.fromisoformat(RECEIPT["dt"])


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def bid(session, *, key="bid", delta=1, amount=None, league="1234567"):
    row = TransferAction(
        league_id=league, player_id="10134", action_type="bid", status="executed",
        amount=amount or RECEIPT["data"]["trp"], idempotency_key=key,
        created_at=AT-timedelta(hours=delta),
        payload={"expectation": {"p": .6, "kind": "kickbase"}},
    )
    session.add(row)
    session.commit()
    return row


def settle(session, my_name="emil"):
    return settle_bid_expectations(
        session, league_id="1234567", squad_pids=set(), open_bid_pids=set(),
        receipts=FEED, my_name=my_name, now=AT + timedelta(days=3),
    )


def test_later_sale_does_not_turn_confirmed_win_into_loss(session):
    row = bid(session)
    assert [s.result for s in settle(session)] == ["won"]
    assert row.payload["outcome"]["training_eligible"] is True
    assert settle(session) == []


def test_receipt_matches_only_latest_bid_version(session):
    old, new = bid(session, key="old", delta=2), bid(session, key="new", delta=1)
    assert len(settle(session)) == 1
    assert old.payload["outcome"]["result"] == "superseded"
    assert new.payload["outcome"]["result"] == "won"
    assert calibration(session, now=AT+timedelta(days=3)).total == 1


def test_foreign_receipt_proves_loss_only_if_it_outbids_ours(session):
    row = bid(session, amount=RECEIPT["data"]["trp"]-1)
    assert [s.result for s in settle(session, "different manager")] == ["lost"]
    assert row.payload["outcome"]["source"] == "confirmed_receipt"


def test_lower_foreign_receipt_does_not_claim_higher_bid_lost(session):
    row = bid(session, amount=RECEIPT["data"]["trp"]+1)
    assert settle(session, "different manager") == []
    assert row.payload["outcome"]["result"] == "unresolved"


def test_cancelled_offer_is_not_auction_training_loss(session):
    row = bid(session)
    session.add(TransferAction(
        league_id="1234567", player_id="10134", action_type="cancel_bid", status="executed",
        idempotency_key="cancel", created_at=AT-timedelta(minutes=30),
    ))
    session.commit()
    assert settle(session) == []
    assert row.payload["outcome"]["result"] == "cancelled"
    assert calibration(session, now=AT+timedelta(days=3)).total == 0


def test_same_player_in_another_league_is_not_settled(session):
    row = bid(session, league="other")
    assert settle(session) == []
    assert "outcome" not in row.payload
