"""Gemeinsame Mannschaftspakete mit internen Spieleroptionen, keine API-Fixtures."""

import pytest

import kickbase_agent.decision.team_optimizer as optimizer_mod
from kickbase_agent.decision.team_optimizer import PlayerOption, optimize_team


def _owned(positions):
    return [PlayerOption(str(i), p, f"club-{i}", 100, owned=True) for i, p in enumerate(positions)]


def test_three_affordable_positions_are_completed_together():
    owned = _owned([1, 2, 2, 2, 3, 3, 3, 4])
    market = [
        PlayerOption("d", 2, "d", 60, cost_eur=2_000_000),
        PlayerOption("m", 3, "m", 65, cost_eur=2_000_000),
        PlayerOption("s", 4, "s", 70, cost_eur=2_000_000),
    ]
    result = optimize_team(
        owned + market, cash_available=6_000_000, club_counts={}, max_per_club=11
    )
    assert result.optimal and result.filled_slots == 11
    assert result.owned_filled_slots == 8
    assert set(result.purchase_ids) == {"d", "m", "s"}
    assert result.cost_eur == 6_000_000 and result.remaining_cash == 0
    assert result.formation == "4-4-2"


def test_financing_can_choose_a_different_complete_formation():
    options = _owned([1, 2, 2, 2, 3, 3, 3, 3, 4, 4]) + [
        PlayerOption("expensive-striker", 4, "a", 500, cost_eur=9_000_000),
        PlayerOption("affordable-midfielder", 3, "b", 40, cost_eur=1_000_000),
    ]
    result = optimize_team(options, cash_available=1_000_000, club_counts={}, max_per_club=11)
    assert result.filled_slots == 11 and result.formation == "3-5-2"
    assert result.purchase_ids == ("affordable-midfielder",)


def test_committed_players_are_not_counted_as_owned_and_cannot_be_bought_twice():
    options = _owned([1, 2, 2, 2, 3, 3, 3, 3, 4, 4]) + [
        PlayerOption("open", 4, "a", 200, committed=True),
        PlayerOption("extra", 4, "b", 300, cost_eur=3_000_000),
    ]
    result = optimize_team(options, cash_available=1_000_000, club_counts={"a": 1}, max_per_club=1)
    assert result.filled_slots == 11 and result.owned_filled_slots == 10
    assert result.purchase_ids == () and result.cost_eur == 0
    assert any("kein Besitz" in note for note in result.notes)


def test_slot_priority_preserves_negative_points_instead_of_inventing_positive_forecasts():
    options = _owned([1, 2, 2, 2, 3, 3, 3, 3, 4, 4]) + [
        PlayerOption("negative", 4, "n", -5, cost_eur=100_000),
    ]
    result = optimize_team(options, cash_available=100_000, club_counts={}, max_per_club=11)
    assert result.filled_slots == 11 and result.horizon_points == 995
    assert result.purchase_ids == ("negative",)


def test_joint_club_limit_and_no_borrowing_against_hypothetical_sales():
    options = _owned([1, 2, 2, 2, 3, 3, 3, 4]) + [
        PlayerOption("a", 2, "same", 200, cost_eur=100),
        PlayerOption("b", 3, "same", 190, cost_eur=100),
        PlayerOption("c", 4, "c", 180, cost_eur=100),
    ]
    result = optimize_team(options, cash_available=200, club_counts={"same": 1}, max_per_club=2)
    assert result.filled_slots == 10 and len(result.purchase_ids) == 2
    assert len(set(result.purchase_ids) & {"a", "b"}) == 1
    assert result.remaining_cash == 0
    negative = optimize_team(options, cash_available=-1, club_counts={}, max_per_club=11)
    assert negative.solver_status == "infeasible" and negative.purchase_ids == ()


def test_required_purchase_consumes_cash_even_when_it_does_not_make_the_eleven():
    owned = _owned([1, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4])
    weak = PlayerOption("weak", 4, "w", 1, cost_eur=5_000_000)
    result = optimize_team(
        owned + [weak],
        cash_available=5_000_000,
        club_counts={},
        max_per_club=11,
        required_ids=frozenset({"weak"}),
    )
    assert result.purchase_ids == ("weak",) and "weak" not in result.lineup_ids
    assert result.horizon_points == 1100 and result.remaining_cash == 0


@pytest.mark.parametrize(
    "formation",
    [
        (3, 4, 3),
        (3, 5, 2),
        (3, 6, 1),
        (4, 3, 3),
        (4, 4, 2),
        (4, 5, 1),
        (5, 2, 3),
        (5, 3, 2),
        (5, 4, 1),
    ],
)
def test_each_legal_formation_can_be_funded_exactly(formation):
    defenders, midfielders, strikers = formation
    options = _owned([1, *([2] * defenders), *([3] * midfielders), *([4] * (strikers - 1))])
    options.append(PlayerOption("last", 4, "last", 0, cost_eur=1_000_000))
    plan = optimize_team(options, cash_available=1_000_000, club_counts={}, max_per_club=11)
    assert plan.filled_slots == 11
    assert plan.formation == "-".join(map(str, formation))
    assert plan.remaining_cash == 0


def test_bounded_solver_incumbent_never_claims_optimality(monkeypatch):
    real_milp = optimizer_mod.milp

    def interrupted(*args, **kwargs):
        result = real_milp(*args, **kwargs)
        result.status = 1  # a feasible incumbent from a bounded solver run
        return result

    monkeypatch.setattr(optimizer_mod, "milp", interrupted)
    plan = optimize_team(_owned([1, 2, 3, 4]), cash_available=0, club_counts={}, max_per_club=11)
    assert plan.filled_slots == 4 and not plan.optimal
    assert plan.solver_status == "feasible_time_limit"
