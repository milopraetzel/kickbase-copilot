"""Prüft Darstellung echter Handlungszustände am normalisierten Domainvertrag."""
from copy import deepcopy
from datetime import UTC, datetime

from intel.test_strategy import REAL_EXPORT
from kickbase_agent.intel.action_plan import build_action_plan


def action_plan(**changes):
    args = {
        "strategy": {"generated_at": "2026-09-11T21:51:22+00:00",
                     "portfolio": {"cash_after_all_bids_eur": 2920437},
                     "deadline": {"next_lock_at": "2026-09-18T18:30:00+00:00",
                                  "next_lock_label": "Fr 18.09. 20:30"}},
        "buys": [], "sells": [], "intents": [], "bids": [],
        "squad_rows": deepcopy(REAL_EXPORT["squad_rows"]), "market_rows": [],
    }
    args.update(changes)
    return build_action_plan(**args)


def test_executed_bid_without_live_bid_is_never_shown_as_purchase():
    result = action_plan(intents=[{"status": "executed", "kind": "bid",
                                  "player_id": "6174", "player_name": "Arthur"}])
    assert not any(step.get("player_id") == "6174" for step in result["steps"])
    assert result["proposals_are_not_executions"] is True
    assert any("bezahlbares Paket" in step["title"] for step in result["steps"])


def test_open_bid_and_pending_intent_are_not_double_counted():
    result = action_plan(
        bids=[{"player_id": "6174", "name": "Arthur", "amount": 7251595}],
        intents=[{"status": "pending", "kind": "bid", "player_id": "6174",
                  "player_name": "Arthur", "amount": 7251595}],
    )
    matching = [s for s in result["steps"] if s.get("player_id") == "6174"]
    assert len(matching) == 1
    assert matching[0]["status"] == "Geboten"
    assert "bestätigtem Kauf" in matching[0]["detail"]


def test_negative_and_unknown_cash_are_not_presented_as_spendable():
    for cash in (None, -4331158):
        result = action_plan(strategy={"generated_at": "2026-09-11T21:51:22+00:00",
                                       "portfolio": {"cash_after_all_bids_eur": cash}})
        assert result["available_cash_eur"] == cash
        assert result["status"] == "offen"
        assert any(s["status"] == "Finanzierung prüfen" for s in result["steps"])
        if cash is not None:
            assert any("4.331.158 € Finanzierung" in s["title"] for s in result["steps"])


def test_many_open_bids_keep_funding_and_deadline_tasks_visible():
    # Sechs parallele Domain-Vorhaben sind eine Zustandsvariante, kein API-Fixture.
    bids = [{"player_id": str(i), "name": f"Vorhaben {i}", "amount": 1} for i in range(6)]
    result = action_plan(bids=bids, strategy={
        "generated_at": "2026-09-11T21:51:22+00:00",
        "portfolio": {"cash_after_all_bids_eur": -4331158},
    })
    assert len(result["steps"]) <= 6
    assert any(s["status"] == "Finanzierung prüfen" for s in result["steps"])
    assert any("4.331.158 € Finanzierung" in s["title"] for s in result["steps"])
    assert any(s["status"] == "Zum Abschluss" for s in result["steps"])


def test_expired_pending_intent_does_not_become_a_current_plan():
    result = action_plan(intents=[{
        "status": "pending", "kind": "bid", "player_id": "6174", "player_name": "Arthur",
        "deadline": "2026-09-09T21:00:00+00:00", "amount": 7251595,
    }])
    assert not any(s.get("player_id") == "6174" for s in result["steps"])


def test_absolute_expiry_overrides_old_positive_countdown():
    row = deepcopy(REAL_EXPORT["squad_rows"][0])
    row.update(fitness_status=0, kb_prob=1, hours_left=1, listing_type="kickbase",
               status="starter", expires_at="2026-09-11T21:00:00+00:00")
    result = action_plan(squad_rows=[], market_rows=[row], strategy={
        "generated_at": "2026-09-11T21:51:22+00:00",
        "portfolio": {"cash_after_all_bids_eur": 100000000},
        "deadline": {"next_lock_at": "2026-09-18T18:30:00+00:00"},
    })
    assert not any(s.get("player_id") == row["pid"] for s in result["steps"])


def test_expiry_during_source_collection_uses_final_observation_time():
    row = deepcopy(REAL_EXPORT["squad_rows"][0])
    row.update(fitness_status=0, kb_prob=1, listing_type="kickbase", status="starter",
               expires_at="2026-09-11T22:00:00+00:00")
    args = dict(squad_rows=[], market_rows=[row], strategy={
        "generated_at": "2026-09-11T21:51:22+00:00",
        "portfolio": {"cash_after_all_bids_eur": 100000000},
        "deadline": {"next_lock_at": "2026-09-18T18:30:00+00:00"},
    })
    before = action_plan(**args)
    assert any(s.get("player_id") == row["pid"] for s in before["steps"])
    after = action_plan(**args, observed_at=datetime(2026, 9, 11, 22, 1, tzinfo=UTC))
    assert not any(s.get("player_id") == row["pid"] for s in after["steps"])
