"""Wöchentlicher lokaler Lernanschluss; keine API-Aufrufe oder Aktivierung."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, Report, TransferAction
from kickbase_agent.worker.competition_training_job import train_from_database

FEED = json.loads(
    (Path(__file__).parents[1] / "fixtures/league_history_20260912/feed-matching.json").read_text()
)
RECEIPT = next(e for e in FEED if e["data"].get("pi") == "10134" and e["data"].get("byr"))
AT = datetime.fromisoformat(RECEIPT["dt"])


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        yield database


@pytest.mark.parametrize("invalid", [False, True])
def test_empty_or_unconfirmed_data_is_skipped(session, tmp_path, invalid):
    if invalid:
        session.add(
            TransferAction(
                league_id="1234567",
                player_id="10134",
                action_type="bid",
                status="executed",
                dry_run=True,
                amount=1_000_000,
                idempotency_key="invalid",
                created_at=AT,
                payload={},
            )
        )
        session.commit()
    result = train_from_database(session, as_of=datetime.now(UTC), registry_dir=tmp_path)
    assert result["status"] == "skipped" and not result["activated"]
    assert list(tmp_path.iterdir()) == []
    assert session.scalar(select(Report)).kind == "model_evaluation"


def test_confirmed_unknown_probability_trains_candidate_and_preserves_active_file(
    session, tmp_path
):
    from kickbase_agent.trading.expectation import settle_bid_expectations

    session.add(
        TransferAction(
            league_id="1234567",
            player_id="10134",
            action_type="bid",
            status="executed",
            dry_run=False,
            amount=RECEIPT["data"]["trp"],
            idempotency_key="confirmed",
            created_at=AT - timedelta(hours=1),
            payload={
                "expectation": {
                    "p": None,
                    "kind": "kickbase",
                    "engine_version": "team-milp-v1",
                    "observed_at": (AT - timedelta(hours=2)).isoformat(),
                    "market_value_eur": 5_000_000,
                    "position": 3,
                }
            },
        )
    )
    session.commit()
    settle_bid_expectations(
        session,
        league_id="1234567",
        squad_pids=set(),
        open_bid_pids=set(),
        receipts=FEED,
        my_name="emil",
        now=AT + timedelta(days=3),
    )
    active = tmp_path / "1234567-kickbase.json"
    active.write_text("existing-reference")
    result = train_from_database(session, as_of=AT + timedelta(days=3), registry_dir=tmp_path)
    assert result["status"] == "candidate" and result["activated"] is False
    [candidate] = result["candidates"]
    assert candidate["rows"] == 1 and candidate["eligible_for_promotion"] is False
    path = Path(candidate["candidate_path"])
    before = path.read_bytes()
    train_from_database(session, as_of=AT + timedelta(days=3), registry_dir=tmp_path)
    assert path.read_bytes() == before and active.read_text() == "existing-reference"
