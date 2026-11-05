"""Die Bank-Ausnahme hängt an frischer Deadline, nicht nur am Review-Zweck."""

from datetime import timedelta

import pytest

from kickbase_agent.agent.executor import ActionExecutor, PreflightState
from kickbase_agent.decision.purchase_review import emergency_bench_fallback_allowed

from .test_curated_bid_guard import (
    MV,
    NOW,
    PID,
    Client,
    action,
    engine_review,
)
from .test_curated_bid_guard import (
    session as session,
)


@pytest.mark.parametrize("hours,allowed", [(97, False), (96, True), (25, True), (12, True),
                                         (0, False), (-1, False), (None, False)])
def test_exact_emergency_deadline_window(hours, allowed):
    deadline = NOW + timedelta(hours=hours) if hours is not None else None
    assert emergency_bench_fallback_allowed(NOW, deadline) is allowed
    assert not emergency_bench_fallback_allowed(NOW.replace(tzinfo=None), deadline)
    assert not emergency_bench_fallback_allowed(NOW, NOW.replace(tzinfo=None))


@pytest.mark.parametrize("hours,li_bench,allowed", [
    (120, False, False), (None, False, False), (-1, False, False),
    (12, False, True), (12, True, True), (120, True, False),
])
async def test_executor_uses_fresh_deadline_even_with_a_claimed_completion_review(
    session, hours, li_bench, allowed,
):
    client = Client()
    proof = engine_review()
    # Der Plan behauptet einen nahen Termin; die frische Quelle entscheidet.
    proof["team_plan"]["matchday_deadline"] = (NOW + timedelta(hours=12)).isoformat()
    if li_bench:
        proof["team_plan"]["forecast_inputs"] = {PID: {"ligainsider_status": "bench"}}

    async def fresh(_):
        return PreflightState(
            on_market=True, current_mv=MV, current_price=MV, budget=99_000_000,
            budget_after_bids=99_000_000, own_bids=[], squad_positions={},
            target_fitness_status=0, target_lineup_prob=1 if li_bench else 5,
            target_mv_change_24h=-144727,
            matchday_deadline=NOW + timedelta(hours=hours) if hours is not None else None,
        )

    result = await ActionExecutor(client, session, preflight=fresh).execute(
        action(purchase_review=proof), now=NOW,
    )
    assert (result.status == "executed") is allowed, result.message
    assert bool(client.writes) is allowed
