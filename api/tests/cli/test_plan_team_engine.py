"""Echte Planintegration: Prognosen, gemeinsame Finanzierung, datierte Preisprüfung."""

from datetime import UTC, datetime, timedelta

import pytest

from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.decision.purchase_review import purchase_review_veto
from kickbase_agent.kickbase.models import Position, SquadPlayer

from .test_plan import FakePlanClient, _bid, _mp, _patch_world
from .test_plan_roster_value import _prepare, _replacement


async def test_three_gaps_produce_one_funded_package_and_valid_reviews(monkeypatch):
    client = FakePlanClient()
    owned_positions = [1, 2, 2, 2, 3, 3, 3, 4]
    owned = [
        _mp(f"owned-{i}", 1_000_000, str(i)).player.model_copy(update={"position": Position(pos)})
        for i, pos in enumerate(owned_positions)
    ]

    async def squad(_league):
        return [SquadPlayer(player=p, is_starter=True) for p in owned]

    client.get_squad = squad
    client.market = [
        _mp(f"new-{pos}", 1_000_000, f"club-{pos}").model_copy(
            update={
                "player": _mp(f"new-{pos}", 1_000_000, f"club-{pos}").player.model_copy(
                    update={"position": Position(pos), "mv_change_24h": -100_000}
                )
            }
        )
        for pos in (2, 3, 4)
    ]
    _patch_world(
        monkeypatch, client, {p.id: 0.2 for p in [*owned, *(m.player for m in client.market)]}
    )
    monkeypatch.setattr(
        plan_mod, "matchday_deadline", lambda *_: (1, datetime.now(UTC) + timedelta(days=3))
    )
    plan = await plan_mod.build_plan()
    assert len(plan.buys) == 3
    assert plan.team_plan["baseline_filled_slots"] == 8
    assert plan.team_plan["filled_slots"] == 11
    # Gebotsregel V3: alle drei fallen (-100.000 € seit gestern) → je +0,5 %.
    assert plan.team_plan["reserved_cash_eur"] == 3_015_000
    assert plan.momentum_reserve_eur == 0
    for proposal in plan.buys:
        review = proposal.action.purchase_review
        assert review["team_plan"] == plan.team_plan
        assert review["purpose"] == "lineup_completion"
        assert review["bid_scenarios"][0]["win_probability"] is None
        assert (
            purchase_review_veto(
                review,
                player_id=proposal.action.player_id,
                amount=proposal.action.amount,
                now=datetime.now(UTC),
            )
            is None
        )


async def test_loss_fallback_keeps_only_later_auctions_and_no_fictitious_cash(monkeypatch):
    client = await _prepare(
        monkeypatch, positions=[1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4], cash=10_000_000
    )
    end = datetime.now(UTC) + timedelta(hours=2)
    client.market = [
        _replacement(mv=1_000_000, pid="first").model_copy(update={"expires_at": end}),
        _replacement(mv=1_100_000, pid="same-time").model_copy(update={"expires_at": end}),
        _replacement(mv=1_200_000, pid="later").model_copy(
            update={"expires_at": end + timedelta(minutes=15)}
        ),
    ]
    # One missing defender, every forecast equal; first is the cheapest complete team.
    original = client.get_squad

    async def missing(_league):
        return [s for s in await original(_league) if s.player.id != "roster-1"]

    client.get_squad = missing
    plan = await plan_mod.build_plan()
    assert [p.action.player_id for p in plan.buys] == ["first"]
    [fallback] = plan.team_plan["fallbacks"]
    assert fallback["purchase_ids"] == ("later",)
    assert fallback["guaranteed"] is False
    # Paket 1 (P0-3): Restgeld bezieht sich auf das Planungsgeld Konto − 10.000 €
    # (10.000.000 − 10.000 − 1.200.000), nicht mehr auf das nackte Konto.
    assert fallback["remaining_cash"] == 8_790_000
    assert fallback["known_at"] > end.isoformat()


async def test_unknown_deadline_blocks_new_actions_but_explains_gap(monkeypatch):
    client = FakePlanClient()
    client.market = [_mp("x", 1_000_000, "club")]
    _patch_world(monkeypatch, client, {"x": 0.9})
    monkeypatch.setattr(plan_mod, "matchday_deadline", lambda *_: None)
    plan = await plan_mod.build_plan()
    assert plan.buys == []
    assert "unbekannt" in plan.purchase_reviews["x"]["reasons"][0]
    assert plan.team_plan["filled_slots"] == 0


async def test_snapshot_records_actual_forecast_inputs_without_future_results(monkeypatch):
    from kickbase_agent.kickbase.models import MatchdayPerformance

    now = datetime.now(UTC)
    client = FakePlanClient()
    client.market = [_mp("x", 1_000_000, "club")]
    _patch_world(monkeypatch, client, {"x": 0.1})
    played = MatchdayPerformance(
        season="2026/2027",
        matchday=1,
        match_date=now - timedelta(days=7),
        points=50,
        minutes=90,
        points_observed=True,
        minutes_observed=True,
        is_finished=True,
        captured_at=now - timedelta(days=1),
    )
    future = played.model_copy(
        update={"matchday": 2, "match_date": now + timedelta(days=2), "points": 10_000}
    )
    monkeypatch.setattr(plan_mod, "load_performance", lambda *_: {"x": [played, future]})
    plan = await plan_mod.build_plan()
    assert plan.projections["x"]["horizon_points"] == 200
    inputs = plan.team_plan["forecast_inputs"]["x"]
    assert [r["matchday"] for r in inputs["performances"]] == [1]
    assert inputs["player"]["id"] == "x"
    assert inputs["captured_at"] == plan.team_plan["observed_at"]


async def test_owned_player_is_not_double_reserved_by_a_stale_pending_intent(monkeypatch):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 3, 3, 3, 4])
    monkeypatch.setattr(
        plan_mod,
        "load_pending_bid_commitments",
        lambda *_: [{"player_id": "roster-1", "amount": 20_000_000, "team_id": "1", "position": 2}],
    )
    plan = await plan_mod.build_plan()
    assert plan.plan_base == client.budget
    assert plan.team_plan["reserved_cash_eur"] == 0


async def test_full_roster_blocks_buys_until_a_real_departure(monkeypatch):
    client = await _prepare(
        monkeypatch, positions=[1, *([2] * 7), *([3] * 7), 4, 4, 4], replacement_score=0.9
    )
    client.market = [_replacement(mv=1_000_000)]
    plan = await plan_mod.build_plan()
    assert plan.buys == [] and plan.swaps == []
    assert plan.team_plan["technical_limits"]["new_package_capacity"] == 0


async def test_lost_commitment_releases_one_real_roster_slot_for_the_fallback(monkeypatch):
    from types import SimpleNamespace

    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 3, 3, 3, 3, 3, 4], cash=10_000_000)
    monkeypatch.setattr(plan_mod, "get_settings", lambda: SimpleNamespace(kickbase_squad_limit=11))
    end = datetime.now(UTC) + timedelta(hours=2)
    client.market = [
        _replacement(mv=1_000_000, pid="open").model_copy(update={"expires_at": end}),
        _replacement(mv=1_200_000, pid="later").model_copy(
            update={"expires_at": end + timedelta(minutes=15)}
        ),
    ]
    client.own_bids = [_bid("open", "replacement-club", 1_000_000)]
    plan = await plan_mod.build_plan()
    assert plan.buys == []
    assert plan.team_plan["technical_limits"]["new_package_capacity"] == 0
    [fallback] = plan.team_plan["fallbacks"]
    assert fallback["purchase_ids"] == ("later",)
    assert fallback["filled_slots"] == 11


@pytest.mark.parametrize("updated", [{"position": Position.GOALKEEPER}, {"team_id": "0"}])
async def test_fresh_position_or_club_change_recomputes_the_package(monkeypatch, updated):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 3, 3, 3, 3, 3, 4], cash=10_000_000)
    client.max_per_club = 1
    client.market = [
        _replacement(mv=1_000_000, pid="initial-choice"),
        _replacement(mv=1_200_000, pid="alternative").model_copy(
            update={
                "player": _replacement(mv=1_200_000, pid="alternative").player.model_copy(
                    update={"team_id": "alternative-club"}
                )
            }
        ),
    ]
    original = client.get_player

    async def fresh(league, pid):
        player = await original(league, pid)
        return player.model_copy(update=updated) if pid == "initial-choice" else player

    client.get_player = fresh
    plan = await plan_mod.build_plan()
    assert [p.action.player_id for p in plan.buys] == ["alternative"]
    first = next(c for c in plan.market_candidates if c.player_id == "initial-choice")
    for key, value in updated.items():
        assert getattr(first, key) == value


async def test_existing_amount_stays_selected_when_model_prefers_a_higher_scenario(monkeypatch):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 3, 3, 3, 3, 3, 4], cash=10_000_000)
    client.market = [_replacement(mv=1_000_000, pid="open")]
    client.own_bids = [_bid("open", "replacement-club", 1_000_000)]
    monkeypatch.setattr(plan_mod, "load_competition", lambda *_: {"version": "test-competition-v1"})
    monkeypatch.setattr(
        plan_mod,
        "predict_probability",
        lambda _, **kw: min(0.9, 0.1 + (kw["amount"] - 1_000_000) / 500_000),
    )
    plan = await plan_mod.build_plan()
    review = plan.purchase_reviews["open"]
    assert review["bid_model_version"] == "test-competition-v1"
    assert [s["amount"] for s in review["bid_scenarios"] if s["selected"]] == [1_000_000]
    assert (
        purchase_review_veto(review, player_id="open", amount=1_000_000, now=datetime.now(UTC))
        is None
    )
