"""Unabhängige Finanzierungsfälle mit internen Verpflichtungen, ohne API-Mocks."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kickbase_agent.decision.funding_recovery import build_funding_recovery

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)


def recovery(*, budget, live, pending=(), manual=frozenset(), owned=()):
    return build_funding_recovery(
        league_id="1234567",
        plan_id="review-test",
        budget=budget,
        owned_player_ids=set(owned),
        live_bids=live,
        pending_bids=list(pending),
        now=NOW,
        market_by_id={
            row["player_id"]: SimpleNamespace(expires_at=NOW + timedelta(hours=2)) for row in live
        },
        manual_live_ids=manual,
    )


def bid(pid, amount):
    return {
        "player_id": pid, "amount": amount, "offer_id": "domain-user",
        "funding_provenance": {
            "original_bid_action_id": 101 if pid == "a" else 102,
            "original_listing_expires_at": (NOW + timedelta(hours=2)).isoformat(),
        },
    }


@pytest.mark.parametrize("status,manual", [("planned", True), ("executing", False)])
def test_pending_protection_also_covers_the_corresponding_live_bid(status, manual):
    result = recovery(
        budget=3_000_000,
        live=[bid("a", 4_000_000)],
        pending=[
            {
                "player_id": "a",
                "amount": 4_000_000,
                "intent_id": 1,
                "status": status,
                "manual": manual,
            }
        ],
    )
    assert result["actions"] == []
    assert result["status"] == "blocked"


def test_recovery_exposes_remaining_manual_commitment_deficit_as_partial():
    result = recovery(
        budget=10_000_000,
        live=[bid("a", 8_000_000), bid("b", 4_000_000)],
        pending=[
            {
                "player_id": "manual-other",
                "amount": 4_000_000,
                "intent_id": 1,
                "status": "planned",
                "manual": True,
            }
        ],
    )
    assert sum(row["amount_eur"] for row in result["actions"]) == 4_000_000
    assert result["released_pending_ids"] == []
    assert result["status"] == "abandon_bids_partial"
    assert result["remaining_deficit_eur"] == 2_000_000


def test_known_manual_live_bid_is_never_abandoned():
    result = recovery(
        budget=3_000_000, live=[bid("manual", 4_000_000)], manual=frozenset({"manual"})
    )
    assert result["actions"] == [] and result["status"] == "blocked"


def test_negative_cash_cannot_be_fixed_by_cancelling_an_offer():
    result = recovery(budget=-1, live=[bid("a", 4_000_000)])
    assert result["status"] == "cash_deficit" and result["actions"] == []
