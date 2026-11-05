"""Profiteur-Scout: wer profitiert vom Ausfall eines Teamkollegen?"""

from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.scouts.ligainsider.models import Availability, InjuryEntry, Status
from kickbase_agent.scouts.ligainsider.profiteur import find_profiteurs, profiteur_lines


def _c(pid: str, name: str, tid: str, pos: int, score: float = 0.5) -> ExplosionCandidate:
    return ExplosionCandidate(player_id=pid, name=name, team_id=tid, position=pos,
                              current_market_value=5_000_000, total_score=score, signals=[])


def _avail(pid: str, status: Status) -> Availability:
    return Availability(pid, status, 1.0, "test", 1.0)


# Dortmund (tid=3): Stürmer "Guirassy" verletzt; Markt hat Stürmer "Beier" (Starter)
# und Mittelfeldspieler "Sabitzer" (Starter) desselben Teams + Bayern-Spieler.
INJ = [InjuryEntry("3", "Serhou Guirassy", "Muskelverletzung")]
POOL = [
    _c("inj", "Serhou Guirassy", "3", 4),   # der Verletzte selbst (löst Position auf)
    _c("m1", "Maximilian Beier", "3", 4, 0.6),
    _c("m2", "Marcel Sabitzer", "3", 3, 0.7),
    _c("m3", "Michael Olise", "2", 3, 0.9),
]
MARKET = POOL[1:]  # Beier, Sabitzer, Olise stehen im Markt
AVAILS = [
    _avail("m1", Status.STARTER),
    _avail("m2", Status.STARTER),
    _avail("m3", Status.STARTER),
]


def test_same_position_heir_ranks_first():
    result = find_profiteurs(MARKET, POOL, AVAILS, INJ)
    assert [p.name for p in result][:2] == ["Maximilian Beier", "Marcel Sabitzer"]
    assert result[0].same_position is True          # Beier erbt die Stürmer-Rolle
    assert result[1].same_position is False
    assert "Guirassy" in result[0].injured


def test_other_team_not_flagged():
    result = find_profiteurs(MARKET, POOL, AVAILS, INJ)
    assert all(p.team_tid == "3" for p in result)   # Olise (Bayern) nicht dabei


def test_bench_player_not_a_profiteur():
    avails = [_avail("m1", Status.BENCH), _avail("m2", Status.STARTER)]
    result = find_profiteurs(MARKET[:2], POOL, avails, INJ)
    assert [p.player_id for p in result] == ["m2"]


def test_no_injuries_no_profiteurs():
    assert find_profiteurs(MARKET, POOL, AVAILS, []) == []


def test_lines_are_compact():
    lines = profiteur_lines(find_profiteurs(MARKET, POOL, AVAILS, INJ))
    assert any("Beier" in ln and "Positions-Erbe" in ln for ln in lines)
