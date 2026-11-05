"""Proposal → kanalneutrale Nachricht."""

from kickbase_agent.agent.executor import PlannedAction
from kickbase_agent.cli.plan import Plan
from kickbase_agent.decision.models import Proposal
from kickbase_agent.notify.formatting import format_daily_report, format_proposal


def _buy(pid="1", amount=6_100_000, approval=True) -> Proposal:
    return Proposal(
        action=PlannedAction(action_type="bid", league_id="L", player_id=pid, amount=amount),
        rule_id="buy_explosion",
        rationale="Faik Sakar: Score 0.68, Gebot 6.1M €.",
        score=0.68,
        requires_approval=approval,
    )


def test_format_proposal_buy_has_title_body_and_action_id():
    msg = format_proposal(_buy(), action_id=42)
    assert "Kauf" in msg.title
    assert "6.1" in msg.body or "6,1" in msg.body
    assert "Faik Sakar" in msg.body
    assert msg.action_id == 42


def test_format_proposal_sell():
    sell = Proposal(
        action=PlannedAction(action_type="sell", league_id="L", player_id="9", amount=2_100_000),
        rule_id="sell_weak",
        rationale="Neuhaus: schwach — Sofortverkauf 2.1M €.",
        score=0.07,
        requires_approval=False,
    )
    msg = format_proposal(sell, action_id=7)
    assert "Verkauf" in msg.title
    assert "Neuhaus" in msg.body


def test_format_daily_report_counts_and_budget():
    plan = Plan(league_id="1234567", budget=53_800_000, buys=[_buy(), _buy("2")], sells=[])
    text = format_daily_report(plan)
    assert "2" in text  # 2 Käufe
    assert "53.8" in text or "53,8" in text
    assert "1234567" in text
