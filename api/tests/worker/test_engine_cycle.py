from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kickbase_agent.db.models import Base, Intent, Report
from kickbase_agent.kickbase.models import Player, SquadPlayer
from kickbase_agent.worker import engine_cycle, intents

NOW = datetime.now(UTC)


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(engine_cycle, "SessionLocal", factory)
    client = SimpleNamespace(
        get_squad=AsyncMock(return_value=[]), get_market=AsyncMock(return_value=[]),
        get_budget=AsyncMock(return_value=3_000_000), get_own_bids=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(engine_cycle, "make_kickbase_client", AsyncMock(
        return_value=(client, SimpleNamespace(league_id="1234567", user_id="2663242")),
    ))
    planner = AsyncMock(return_value="planned")
    monkeypatch.setattr(intents, "run_intent_planner", planner)
    return factory, client, planner


@pytest.mark.asyncio
async def test_changed_cash_replans_but_unchanged_state_stays_quiet(setup):
    factory, client, planner = setup
    assert await engine_cycle.run_engine_cycle() == "planned"
    assert await engine_cycle.run_engine_cycle() == "unchanged"
    client.get_budget.return_value = 1_000_000
    assert await engine_cycle.run_engine_cycle() == "planned"
    assert planner.await_count == 2
    with factory() as session:
        assert len(session.scalars(select(Report)).all()) == 2


@pytest.mark.asyncio
async def test_failed_plan_does_not_commit_state_checkpoint(setup):
    factory, _, planner = setup
    planner.side_effect = RuntimeError("source unavailable")
    with pytest.raises(RuntimeError, match="source unavailable"):
        await engine_cycle.run_engine_cycle()
    with factory() as session:
        assert not session.scalars(select(Report)).all()
    planner.side_effect = None
    assert await engine_cycle.run_engine_cycle() == "planned"


@pytest.mark.asyncio
async def test_periodic_refresh_rechecks_roles_even_without_market_change(setup):
    factory, _, planner = setup
    await engine_cycle.run_engine_cycle()
    with factory() as session:
        row = session.scalar(select(Report))
        row.created_at = NOW - timedelta(hours=2)
        session.commit()
    await engine_cycle.run_engine_cycle()
    assert planner.await_count == 2


def test_store_real_decision_and_owned_ids_without_counting_future_buys(setup):
    factory, _, _ = setup
    player = Player(id="1", first_name="", last_name="Test", position=1,
                    team_id="1", market_value=1_000_000, points=0)
    plan = SimpleNamespace(
        league_id="1234567", team_plan={"notes": ["Drei Käufe noch offen"], "purchase_ids": ["2"]},
        purchase_reviews={}, budget=1_000_000, squad=[SquadPlayer(player=player)],
        matchday_deadline_at=NOW,
    )
    with factory() as session:
        engine_cycle.record_team_plan(session, plan, NOW)
        session.commit()
        row = session.scalar(select(Report))
        assert row.meta["owned_ids"] == ["1"]
        assert row.meta["team_plan"]["purchase_ids"] == ["2"]
        plan.team_plan["purchase_ids"].append("3")
        plan.purchase_reviews["changed"] = {"secret": "must-not-appear-later"}
        assert row.meta["team_plan"]["purchase_ids"] == ["2"]
        assert row.meta["purchase_reviews"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"status": "vetoed"}, {"status": "failed"}, {"status": "executed"}, {"amount": 1_100_000},
    {"veto_requested_at": NOW},
])
async def test_local_intent_events_replan_without_changed_kickbase_state(setup, change):
    factory, _, planner = setup
    with factory() as session:
        row = Intent(kind="bid", player_id="1", amount=1_000_000, status="planned",
                     created_at=NOW, updated_at=NOW,
                     execute_at=NOW, sources={"league_id": "1234567"})
        session.add(row)
        session.commit()
        intent_id = row.id
    assert await engine_cycle.run_engine_cycle() == "planned"
    assert await engine_cycle.run_engine_cycle() == "unchanged"
    with factory() as session:
        row = session.get(Intent, intent_id)
        for key, value in change.items():
            setattr(row, key, value)
        session.commit()
    assert await engine_cycle.run_engine_cycle() == "planned"
    assert planner.await_count == 2


@pytest.mark.asyncio
async def test_source_ttl_and_other_league_intents_do_not_trigger_replanning(setup):
    factory, _, planner = setup
    with factory() as session:
        row = Intent(kind="bid", player_id="1", amount=1_000_000, status="planned",
                     created_at=NOW, updated_at=NOW,
                     execute_at=NOW, sources={"league_id": "1234567", "purchase_review": {}})
        session.add(row)
        session.commit()
        intent_id = row.id
    await engine_cycle.run_engine_cycle()
    with factory() as session:
        row = session.get(Intent, intent_id)
        row.sources = {**row.sources, "purchase_review": {"valid_until": NOW.isoformat()}}
        session.add(Intent(kind="bid", player_id="other", amount=1_000_000,
                           created_at=NOW, updated_at=NOW,
                           status="planned", execute_at=NOW, sources={"league_id": "another"}))
        session.commit()
    assert await engine_cycle.run_engine_cycle() == "unchanged"
    assert planner.await_count == 1
