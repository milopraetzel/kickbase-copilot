from datetime import UTC, date, datetime, timedelta

import pytest

from kickbase_agent.db.models import PlayerHistory
from kickbase_agent.intel.signals import (
    current_and_last_season,
    form_signal,
    matchup_signal,
    trend_signal,
)
from kickbase_agent.kickbase.models import Fixture, MatchdayPerformance

# Fester Stichtag: Saisonfenster = {2025/2026, 2024/2025} — Tests altern nicht.
TODAY = date(2026, 3, 1)


def _perf(
    points: list[int],
    season: str = "2025/2026",
    minutes: int = 90,
    start_matchday: int = 1,
) -> list[MatchdayPerformance]:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    return [
        MatchdayPerformance(
            season=season,
            matchday=start_matchday + i,
            points=pts,
            minutes=minutes,
            match_date=base + timedelta(days=7 * i),
        )
        for i, pts in enumerate(points)
    ]


def test_current_and_last_season_rolls_over_in_july():
    assert current_and_last_season(date(2026, 8, 20)) == ("2026/2027", "2025/2026")
    assert current_and_last_season(date(2026, 3, 1)) == ("2025/2026", "2024/2025")


def test_form_signal_strong_recent_form_scores_high():
    signal = form_signal(_perf([80, 100, 200, 220, 240]), lookback=3, today=TODAY)
    assert signal.name == "form"
    assert signal.score > 0.7
    assert "Aufwärtstrend" in signal.reason or "Form" in signal.reason


def test_form_signal_bad_form_scores_low():
    signal = form_signal(_perf([200, 180, 40, 30, 20]), lookback=3, today=TODAY)
    assert signal.score < 0.3


def test_form_signal_insufficient_history_is_neutral():
    signal = form_signal(_perf([120]), lookback=3, today=TODAY)
    assert 0.4 <= signal.score <= 0.6
    assert "nicht genug" in signal.reason.lower()


def test_form_ignores_unplayed_future_matchdays():
    """Review-Fund: Zukunfts-Spieltage (0 Pkt/0 Min) zogen die Form ALLER Spieler auf ~0."""
    played = _perf([100, 150, 200, 250, 300])  # klarer Aufwärtstrend
    future = _perf([0, 0, 0], season="2025/2026", minutes=0, start_matchday=6)
    signal = form_signal(played + future, lookback=3, today=TODAY)
    assert signal.score > 0.7  # vorher: recent = 3×0 Punkte → Score ~0


def test_form_ignores_bench_games_at_the_end():
    """Bank ohne Einsatz ist keine Formkrise — nur gespielte Spiele zählen."""
    played = _perf([150, 160, 170])
    bench = _perf([0, 0], minutes=0, start_matchday=4)
    signal = form_signal(played + bench, lookback=3, today=TODAY)
    assert 0.4 <= signal.score <= 0.6  # stabil, nicht abgestürzt


def test_form_ignores_seasons_older_than_last():
    """Saisonfenster: nur aktuelle + letzte Saison — 2013er-Daten sind Geschichte."""
    ancient = _perf([300, 300, 300, 300, 300], season="2013/2014")
    current = _perf([60, 60, 60, 60], season="2025/2026")
    signal = form_signal(ancient + current, lookback=3, today=TODAY)
    assert 0.4 <= signal.score <= 0.6  # sonst: Baseline 193 → "Formkrise" bei stabilen 60


def test_form_shrinks_small_samples_toward_neutral():
    """Unter 3 gespielten Spielen: Score → 0.5 gezogen (n/3-Shrinkage)."""
    signal = form_signal(_perf([100, 220]), lookback=1, today=TODAY)
    # roh: recent 220 vs. Ø 160 → +60/120 → 1.0; geschrumpft: 0.5 + 0.5·(2/3)
    assert signal.score == pytest.approx(0.5 + 0.5 * 2 / 3, abs=0.01)


def _fixture(home: str, away: str, o1: float, ox: float, o2: float, finished: bool = False):
    return Fixture(
        matchday=1, home_team_id=home, away_team_id=away,
        home_win_odds=o1, draw_odds=ox, away_win_odds=o2, is_finished=finished,
    )


def test_matchup_signal_favorite_scores_high():
    # Bayern (Team 2, o1 1.27) daheim gegen Stuttgart (9)
    fx = _fixture("2", "9", 1.27, 7.0, 7.5)
    signal = matchup_signal("2", [fx], team_names={"9": "Stuttgart"})
    assert signal.score > 0.7
    assert "Favorit" in signal.reason
    # Review-Fix: der Text nennt die ECHTE Siegwahrscheinlichkeit (~74 % aus den
    # Quoten), nicht den zentrierten Score (~99 %).
    assert "Siegchance 74%" in signal.reason
    assert "99%" not in signal.reason


def test_matchup_signal_underdog_scores_low():
    # Stuttgart (9) auswärts, klarer Außenseiter (o2 7.5)
    fx = _fixture("2", "9", 1.27, 7.0, 7.5)
    signal = matchup_signal("9", [fx], team_names={"2": "Bayern"})
    assert signal.score < 0.3
    assert "Außenseiter" in signal.reason
    assert "Siegchance 13%" in signal.reason  # echte Quoten-Wahrscheinlichkeit


def test_matchup_signal_even_game_reports_true_win_probability():
    """Review-Fund: bei einem komplett offenen Spiel (Drei-Wege-Gleichverteilung,
    echte Siegchance 33 %) stand im Text „50% Siegchance", weil der ZENTRIERTE
    Score als Prozent formatiert wurde. Der Score bleibt 0.5 (neutral), der Text
    nennt die echten 33 %."""
    fx = _fixture("2", "9", 3.0, 3.0, 3.0)
    signal = matchup_signal("2", [fx], team_names={"9": "Stuttgart"})
    assert signal.score == pytest.approx(0.5)
    assert "Siegchance 33%" in signal.reason
    assert "neutral gewertet" in signal.reason
    assert "50%" not in signal.reason


def test_matchup_signal_no_fixture_neutral():
    signal = matchup_signal("99", [], team_names={})
    assert 0.4 <= signal.score <= 0.6


def test_matchup_signal_skips_finished_and_missing_odds():
    finished = _fixture("2", "9", 1.27, 7.0, 7.5, finished=True)
    signal = matchup_signal("2", [finished])
    assert 0.4 <= signal.score <= 0.6


def _mv_history(player_id: str, values: list[int]) -> list[PlayerHistory]:
    now = datetime.now(UTC)
    return [
        PlayerHistory(
            player_id=player_id,
            recorded_at=now - timedelta(days=len(values) - i),
            market_value=v,
            points=0,
            is_starter=True,
        )
        for i, v in enumerate(values)
    ]


def test_trend_signal_rising_value_scores_high():
    # +500k over 5 days
    history = _mv_history("p1", [10_000_000, 10_100_000, 10_250_000, 10_400_000, 10_500_000])
    signal = trend_signal(history)
    assert signal.score > 0.7


def test_trend_signal_falling_value_scores_low():
    history = _mv_history("p1", [10_500_000, 10_200_000, 10_000_000, 9_800_000, 9_600_000])
    signal = trend_signal(history)
    assert signal.score < 0.3


def test_trend_signal_empty_is_neutral():
    signal = trend_signal([])
    assert 0.4 <= signal.score <= 0.6
