"""Gebotsaufgabe: gleiche Benutzer-Offer-ID ist keine eindeutige Auktion."""

from datetime import timedelta

import pytest

from kickbase_agent.worker.reconcile import classify

from .test_reconcile import NOW, _action, _bid_entry


def funding_action(**changes):
    review = {
        "schema_version": 1,
        "purpose": "funding_abandon_bid",
        "player_id": "99",
        "league_id": "L1",
        "offer_id": "2663242",
        "amount_eur": 1_000_000,
        "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
        "valid_until": (NOW + timedelta(hours=1)).isoformat(),
        "abandon_until": (NOW + timedelta(hours=1)).isoformat(),
        "original_listing_expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "original_bid_action_id": 1,
        **changes,
    }
    return _action("cancel_bid", payload={"offer_id": "2663242", "funding_review": review})


def test_same_offer_id_on_another_player_is_not_the_target_bid():
    status, note = classify(
        funding_action(), [_bid_entry("other", "2663242")], set(), set(), now=NOW
    )
    assert status == "executed"
    assert "source=observed_state" in note and "kein API" in note


def test_exact_player_and_offer_id_still_present_proves_no_abandonment():
    status, note = classify(funding_action(), [_bid_entry("99", "2663242")], set(), set(), now=NOW)
    assert status == "failed" and "besteht" in note


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "owned",
        "missing_expiry",
        "naive_expiry",
        "different_target_offer",
        "incomplete_live_data",
    ],
)
def test_ambiguous_disappearance_never_becomes_a_confirmed_cancel(case):
    action = funding_action()
    now, owned, bids = NOW, set(), []
    if case == "expired":
        now += timedelta(hours=2)
    elif case == "owned":
        owned = {"99"}
    elif case == "missing_expiry":
        action.payload["funding_review"].pop("abandon_until")
    elif case == "naive_expiry":
        action.payload["funding_review"]["abandon_until"] = "2026-08-21T13:00:00"
    elif case == "different_target_offer":
        bids = [_bid_entry("99", "different")]
    else:
        bids = [{"offer_id": "2663242"}]
    assert classify(action, bids, owned, set(), now=now)[0] is None


def test_old_cancel_paths_keep_their_existing_contract():
    assert (
        classify(_action("cancel_bid", payload={"offer_id": "o1"}), [], set(), set())[0]
        == "executed"
    )


def test_replan_tolerance_does_not_extend_the_original_auction():
    action = funding_action(
        original_listing_expires_at=(NOW - timedelta(seconds=30)).isoformat(),
        abandon_until=(NOW + timedelta(seconds=60)).isoformat(),
    )
    assert classify(action, [], set(), set(), now=NOW)[0] is None
