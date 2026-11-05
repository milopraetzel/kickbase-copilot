"""Rohpunkte-Scout: verlässliche Punkte-Grundlast (Base-Points > Tore)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from kickbase_agent.intel.signals import base_points_signal
from kickbase_agent.kickbase.models import MatchdayPerformance

# Fester Stichtag: Saisonfenster = {2025/2026, 2024/2025} — Tests altern nicht.
TODAY = date(2026, 3, 1)


def _perf(
    points_and_minutes: list[tuple[int, int]], season: str = "2025/2026"
) -> list[MatchdayPerformance]:
    base = datetime(2025, 9, 1, tzinfo=UTC)
    return [
        MatchdayPerformance(season=season, matchday=i + 1, points=p, minutes=m,
                            match_date=base + timedelta(days=7 * i))
        for i, (p, m) in enumerate(points_and_minutes)
    ]


def test_reliable_base_sammler_scores_high():
    # Konstanter Sechser: immer 70-90 Pkt → hohe verlässliche Untergrenze
    perf = _perf([(75, 90), (82, 90), (70, 90), (88, 90), (78, 90)])
    signal = base_points_signal(perf, today=TODAY)
    assert signal.name == "base_points"
    assert signal.score > 0.6


def test_goal_dependent_striker_scores_low():
    # Tor-abhängig: meist wenig, selten ein Ausreißer → niedrige Untergrenze
    perf = _perf([(8, 90), (12, 90), (210, 90), (6, 90), (15, 90)])
    signal = base_points_signal(perf, today=TODAY)
    assert signal.score < 0.3


def test_ignores_bench_games():
    # Spiele ohne Einsatz (0 Min) zählen nicht für die Grundlast
    perf = _perf([(0, 0), (0, 0), (80, 90), (85, 90), (78, 90)])
    signal = base_points_signal(perf, today=TODAY)
    assert signal.score > 0.6  # nur die 3 gespielten zählen → hohe Grundlast


def test_ignores_seasons_older_than_last():
    """Saisonfenster: Vor-Vorsaison und älter fließen nicht in die Grundlast."""
    ancient = _perf([(200, 90), (210, 90), (190, 90), (205, 90)], season="2013/2014")
    signal = base_points_signal(ancient, today=TODAY)
    assert signal.score == 0.5  # keine Einsätze im Fenster → neutral
    assert "Keine Einsätze" in signal.reason


def test_no_played_games_is_neutral():
    signal = base_points_signal([], today=TODAY)
    assert signal.score == 0.5


def test_small_sample_shrinks_toward_neutral():
    """Unter min_games gespielten Spielen: Score → 0.5 gezogen (n/3-Shrinkage)."""
    signal = base_points_signal(_perf([(80, 90), (70, 90)]), today=TODAY)
    # roh: Untergrenze 70 → 0.7; geschrumpft: 0.5 + 0.2·(2/3)
    assert signal.score == pytest.approx(0.5 + 0.2 * 2 / 3, abs=0.01)
    assert "vorsichtig" in signal.reason
