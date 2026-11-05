"""Briefing: Aufbau aus Rohdaten + Formatierung + Teamwert-Δ."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.agent.executor import PlannedAction
from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import Base, PlayerHistory
from kickbase_agent.decision.lineup import Lineup
from kickbase_agent.decision.models import Proposal
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.kickbase.models import Player, Position, RankingEntry, SquadPlayer
from kickbase_agent.worker.briefing import (
    build_briefing,
    format_briefing,
    team_value_change,
)


def _sp(pid: str, mv: int) -> SquadPlayer:
    return SquadPlayer(
        player=Player(id=pid, first_name="", last_name=f"P{pid}",
                      position=Position.MIDFIELDER, market_value=mv, team_id="1"),
        is_starter=False,
    )


def _prop(pid: str, kind: str = "bid") -> Proposal:
    return Proposal(
        action=PlannedAction(action_type=kind, league_id="L", player_id=pid, amount=3_000_000),
        rule_id="r", rationale=f"{kind} {pid}", score=0.7, requires_approval=False,
        player_name=f"Spieler{pid}",
    )


def test_build_briefing_picks_own_rank_and_sums_team_value():
    ranking = [
        RankingEntry(user_id="2663242", name="emil", rank=10, points=0, team_value=0),
        RankingEntry(user_id="999", name="Rivale", rank=1, points=500, team_value=0),
    ]
    squad = [_sp("1", 20_000_000), _sp("2", 5_000_000)]
    plan = Plan(league_id="L", budget=53_800_000, buys=[_prop("3")], sells=[])

    b = build_briefing("BASE", "2663242", ranking, squad, 53_800_000, 1_200_000, plan)

    assert b.rank == 10
    assert b.total_managers == 2
    assert b.team_value == 25_000_000
    assert b.budget == 53_800_000
    assert b.day_change == 1_200_000


def test_format_briefing_contains_key_numbers_and_table():
    plan = Plan(league_id="L", budget=53_800_000, buys=[_prop("3")], sells=[_prop("9", "sell")])
    b = build_briefing("BASE", "u", [], [_sp("1", 99_200_000)], 53_800_000, 1_200_000, plan)
    text = format_briefing(b)

    assert "99.2M" in text
    assert "53.8M" in text
    assert "▲1.2M" in text  # editorialer Aufwärtspfeil
    assert "Geplante Käufe (1)" in text
    assert "Geplante Verkäufe (1)" in text
    assert "Spieler3" in text  # Tabellen-Spielername
    assert "70%" in text  # Score als Prozent
    assert "```" in text  # Monospace-Tabelle


def test_format_briefing_shows_recommended_lineup():
    plan = Plan(league_id="L", budget=1, buys=[], sells=[])
    def _c(pid: str, name: str, pos: int, score: float) -> ExplosionCandidate:
        return ExplosionCandidate(player_id=pid, name=name, team_id="1", position=pos,
                                  current_market_value=1, total_score=score, signals=[])

    starters = (
        [_c("k", "Manuel Neuer", 1, 0.5)]
        + [_c(f"d{i}", f"Def N{i}", 2, 0.4) for i in range(4)]
        + [_c(f"m{i}", f"Mid N{i}", 3, 0.3) for i in range(4)]
        + [_c(f"s{i}", f"Stu N{i}", 4, 0.2) for i in range(2)]
    )
    lineup = Lineup(formation="4-4-2", starters=starters, bench=[], expected_score=4.2)
    b = build_briefing("BASE", "u", [], [_sp("1", 1)], 1, 0, plan, lineup=lineup)
    text = format_briefing(b)

    assert "Empfohlene Aufstellung · 4-4-2" in text
    assert "Neuer" in text          # Nachname des Torwarts
    assert "TW" in text and "STU" in text


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def test_team_value_change_today_vs_yesterday(session):
    for day, mv in ((12, 10_000_000), (13, 10_500_000)):
        session.add(PlayerHistory(player_id="1", recorded_at=datetime(2026, 8, day, tzinfo=UTC),
                                  market_value=mv, points=0, is_starter=False))
    session.commit()

    change = team_value_change(session, ["1"], date(2026, 8, 13))

    assert change == 500_000


def test_team_value_change_no_yesterday_is_zero(session):
    session.add(PlayerHistory(player_id="1", recorded_at=datetime(2026, 8, 13, tzinfo=UTC),
                              market_value=10_000_000, points=0, is_starter=False))
    session.commit()

    assert team_value_change(session, ["1"], date(2026, 8, 13)) == 0
