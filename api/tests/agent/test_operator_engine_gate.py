"""Operatoren teilen den Engine-Prüfvertrag; bestehende Gebote bleiben unberührt."""

import pytest

from kickbase_agent.agent.executor import ActionExecutor

from .test_curated_bid_guard import (
    NOW,
    Client,
    action,
    engine_review,
    review,
)
from .test_curated_bid_guard import (
    session as session,
)


@pytest.mark.parametrize("rule_id", ["intent-bid-manual", "mcp-bid"])
@pytest.mark.parametrize("proof", [None, "legacy", "engine"])
async def test_operator_buy_requires_full_engine_review(session, rule_id, proof):
    client = Client()
    checked = engine_review() if proof == "engine" else review() if proof == "legacy" else None
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(rule_id=rule_id, purchase_review=checked), now=NOW,
    )
    assert (result.status == "executed") is (proof == "engine"), result.message
    assert bool(client.writes) is (proof == "engine")
