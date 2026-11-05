"""Isolierte Finanzierungsfälle; Beträge sind Testbedingungen, keine API-Antworten."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kickbase_agent.decision.funding_recovery import (
    build_funding_recovery,
    funding_review_veto,
)

NOW = datetime(2026, 9, 13, 9, tzinfo=UTC)


def bid(pid="11665", amount=8_074_347):
    return {"player_id": pid, "offer_id": "2663242", "amount": amount,
            "expires_in_s": 104_283, "seller": "", "funding_provenance": {
                "original_bid_action_id": {"a": 1, "b": 2, "c": 3}.get(pid, 189),
                "original_listing_expires_at": (NOW+timedelta(seconds=104_283)).isoformat(),
            }}


def recovery(bids=None, **changes):
    values = dict(league_id="1234567", plan_id="plan-test", budget=3_120_437,
                  owned_player_ids=["owned"], live_bids=[bid()] if bids is None else bids,
                  pending_bids=[], now=NOW, market_by_id={})
    values.update(changes)
    return build_funding_recovery(**values)


def veto(review, **changes):
    values = dict(league_id="1234567", player_id="11665", offer_id="2663242",
                  amount=8_074_347, now=NOW, budget=3_120_437,
                  live_bids=[bid()], owned_player_ids=["owned"])
    values.update(changes)
    return funding_review_veto(review, **values)


def test_recorded_amounts_produce_concrete_full_abandonment():
    result = recovery()
    assert result["deficit_eur"] == 4_953_910
    assert result["status"] == "abandon_bids"
    assert len(result["actions"]) == 1
    review = result["actions"][0]["funding_review"]
    assert veto(review) is None
    assert datetime.fromisoformat(review["abandon_until"]) == NOW + timedelta(seconds=104_283)


def test_local_reservation_is_released_before_real_bid():
    result = recovery([bid(amount=2_000_000)], pending_bids=[
        {"player_id": "pending", "amount": 2_000_000, "intent_id": 17,
         "status": "planned", "manual": False},
    ])
    assert result["status"] == "release_pending"
    assert result["released_pending_ids"] == [17]
    assert result["actions"] == []


@pytest.mark.parametrize("pending", [
    {"status": "planned", "manual": True}, {"status": "executing", "manual": False},
])
def test_manual_or_executing_reservation_never_justifies_real_cancellation(pending):
    result = recovery([bid(amount=2_000_000)], pending_bids=[
        {"player_id": "pending", "amount": 2_000_000, "intent_id": 17, **pending},
    ])
    assert result["status"] == "blocked"
    assert result["released_pending_ids"] == result["actions"] == []


@pytest.mark.parametrize("changes", [
    {"manual_live_ids": {"11665"}}, {"protected_ids": {"11665"}},
    {"live_bids": [{**bid(), "offer_id": ""}]},
    {"live_bids": [{**bid(), "expires_in_s": 0}]},
    {"live_bids": [{**bid(), "seller": "Manager"}]},
    {"live_bids": [{**bid(), "funding_provenance": None}]},
    {"live_bids": [{**bid(), "expires_in_s": 104_283+86400}]},
])
def test_unverifiable_or_protected_live_bid_is_blocked(changes):
    result = recovery(**changes)
    assert result["status"] == "blocked"
    assert not result["actions"]


def test_cash_debt_requires_sale_receipt_not_a_cancellation():
    result = recovery([], budget=-4_953_910)
    assert result["status"] == "cash_deficit"
    assert result["cash_deficit_eur"] == 4_953_910
    assert result["actions"] == []


def test_minimal_count_and_lowest_projected_points_determine_abandonment():
    result = recovery([bid("a", 8), bid("b", 7), bid("c", 4)], budget=12,
                      points_by_id={"a": 200, "b": 100, "c": 0})
    assert [a["player_id"] for a in result["actions"]] == ["b"]


@pytest.mark.parametrize("changes", [
    {"budget": None}, {"budget": 9_000_000}, {"budget": -1},
    {"owned_player_ids": None}, {"owned_player_ids": ["owned", "new"]},
    {"live_bids": []}, {"live_bids": [bid(amount=8_100_000)]},
    {"live_bids": [{**bid(), "offer_id": "other"}]},
    {"live_bids": [bid(), bid("new", 1_000_000)]},
    {"now": NOW + timedelta(hours=4)}, {"now": NOW - timedelta(seconds=1)},
    {"league_id": "other"}, {"amount": True},
])
def test_fresh_validation_rejects_changed_or_missing_state(changes):
    assert veto(recovery()["actions"][0]["funding_review"], **changes)


def test_multiple_selected_cancellations_allow_already_confirmed_prior_removal():
    bids = [bid("a", 4), bid("b", 4), bid("c", 3)]
    result = recovery(bids, budget=3)
    assert [a["player_id"] for a in result["actions"]] == ["a", "b"]
    second = result["actions"][1]["funding_review"]
    assert veto(second, player_id="b", amount=4, budget=3, live_bids=bids[1:]) is None
    # Ein anderer verschwundener Auftrag kann einen Zuschlag bedeuten: neu lesen/planen.
    assert veto(second, player_id="b", amount=4, budget=3, live_bids=[bids[1]])


def test_malformed_review_does_not_crash():
    review = deepcopy(recovery()["actions"][0]["funding_review"])
    review["owned_player_ids"] = [{}]
    assert veto(review)


@pytest.mark.parametrize("change", [
    {"original_bid_action_id": None}, {"original_bid_action_id": True},
    {"original_listing_expires_at": None},
    {"original_listing_expires_at": (NOW+timedelta(days=5)).isoformat()},
])
def test_auction_provenance_cannot_be_omitted_or_replaced(change):
    review = recovery()["actions"][0]["funding_review"]
    review.update(change)
    assert veto(review)


def test_actual_expiry_wins_over_countdown_and_expired_auction_stays_blocked():
    result = recovery(market_by_id={"11665": SimpleNamespace(expires_at=NOW)})
    assert result["status"] == "blocked"
