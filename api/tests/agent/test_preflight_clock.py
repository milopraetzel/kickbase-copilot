"""Executor und frischer Spielplan müssen denselben Bezugszeitpunkt verwenden."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import kickbase_agent.agent.executor as executor_module
from kickbase_agent.agent.executor import ActionExecutor

from . import test_curated_bid_guard as shared
from .test_curated_bid_guard import NOW, Client, action, engine_review

session = shared.session


@pytest.mark.parametrize("staged", [False, True])
async def test_default_preflight_uses_execution_time_despite_different_system_clock(
    session, monkeypatch, staged,
):
    class DifferentClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2035, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(executor_module, "datetime", DifferentClock)
    client = Client()
    client.detail = client.detail.model_copy(update={"lineup_prob": 4})
    deadline = NOW + timedelta(hours=12)

    async def matchdays():
        return [SimpleNamespace(kickoff=deadline, matchday=4, is_finished=False)]

    client.get_matchdays = matchdays
    executor = ActionExecutor(client, session, user_id="2663242")
    planned = action(purchase_review=engine_review())
    if staged:
        proposal = await executor.stage(planned, now=NOW)
        result = await executor.execute_staged(proposal.action_id, now=NOW + timedelta(minutes=1))
    else:
        result = await executor.execute(planned, now=NOW)
    assert executor._last_preflight.matchday_deadline == deadline
    assert result.status == "executed", result.message
    assert len(client.writes) == 1

