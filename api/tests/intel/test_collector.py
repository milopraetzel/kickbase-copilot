from datetime import UTC, datetime, timedelta

import pytest

from kickbase_agent.db.models import PlayerHistory
from kickbase_agent.intel.collector import collect_candidates
from kickbase_agent.intel.signals import current_and_last_season
from kickbase_agent.kickbase.models import (
    Fixture,
    MatchdayPerformance,
    Player,
    Position,
    SquadPlayer,
)

# Der Collector läuft mit echter Uhr — die Test-Performances liegen deshalb in der
# jeweils aktuellen Saison, damit das Saisonfenster sie nie ausblendet.
CURRENT_SEASON = current_and_last_season()[0]


def _squad_player(pid: str, name: str, team_id: str, mv: int) -> SquadPlayer:
    return SquadPlayer(
        player=Player(
            id=pid, first_name="", last_name=name, position=Position.MIDFIELDER,
            market_value=mv, team_id=team_id,
        ),
        is_starter=True,
    )


@pytest.mark.asyncio
async def test_collect_candidates_builds_scored_list():
    squad = [
        _squad_player("1", "Wirtz", "4", 20_000_000),
        _squad_player("2", "Musiala", "2", 25_000_000),
    ]
    # Wirtz' Team (4) daheim gegen schwaches Team (7) → Favorit; Musiala (2) neutral.
    fixtures = [
        Fixture(matchday=1, home_team_id="4", away_team_id="7",
                home_win_odds=1.3, draw_odds=6.0, away_win_odds=8.0),
    ]
    team_names = {"4": "Leverkusen", "7": "Bochum", "2": "Bayern"}

    now = datetime.now(UTC)
    mv_history_by_player = {
        "1": [
            PlayerHistory(player_id="1", recorded_at=now - timedelta(days=4 - i),
                          market_value=19_000_000 + i * 200_000, points=0, is_starter=True)
            for i in range(5)
        ],
        "2": [
            PlayerHistory(player_id="2", recorded_at=now - timedelta(days=4 - i),
                          market_value=25_000_000, points=0, is_starter=True)
            for i in range(5)
        ],
    }
    perf_by_player = {
        "1": [
            MatchdayPerformance(season=CURRENT_SEASON, matchday=i + 1,
                                points=100 + i * 40, minutes=90,
                                match_date=now - timedelta(days=7 * (5 - i)))
            for i in range(5)
        ],  # steigende Form
    }

    candidates = await collect_candidates(
        squad_players=squad,
        fixtures=fixtures,
        team_names=team_names,
        mv_history_by_player=mv_history_by_player,
        perf_by_player=perf_by_player,
    )

    assert len(candidates) == 2
    # Wirtz should outscore Musiala (weak opponent + rising form + rising value)
    top = candidates[0]
    assert top.player_id == "1"
    # 4 Engine-Signale + trend_1d (Wendepunkt-Marker, NACH dem Scoring angehängt).
    assert len(top.signals) == 5
    assert {s.name for s in top.signals} == {
        "base_points", "form", "matchup", "trend", "trend_1d",
    }
    # lineup_score = Engine-Signale OHNE Trend (MW-Momentum erzeugt keine
    # Spieltagspunkte); trend_1d geht in KEINEN der beiden Scores ein.
    assert 0.0 <= top.lineup_score <= 1.0
    assert top.lineup_score != top.total_score  # Trend-Signal macht den Unterschied
    engine_signals = [s for s in top.signals if s.name not in ("trend", "trend_1d")]
    expected = sum(s.score * s.weight for s in engine_signals) / sum(
        s.weight for s in engine_signals
    )
    assert top.lineup_score == pytest.approx(expected)
    # Kauf-Score bleibt das gewichtete Mittel der VIER Engine-Signale — trend_1d
    # darf ihn nicht verschieben.
    scored = [s for s in top.signals if s.name != "trend_1d"]
    expected_total = sum(s.score * s.weight for s in scored) / sum(s.weight for s in scored)
    assert top.total_score == pytest.approx(expected_total)
