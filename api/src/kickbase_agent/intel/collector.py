"""Assemble candidates from raw inputs (players + fixtures + history) into scored list."""

from __future__ import annotations

from kickbase_agent.db.models import PlayerHistory
from kickbase_agent.intel.engine import RuleEngine
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.intel.signals import (
    base_points_signal,
    form_signal,
    matchup_signal,
    trend_1d_signal,
    trend_signal,
)
from kickbase_agent.kickbase.models import (
    Fixture,
    MatchdayPerformance,
    Player,
    SquadPlayer,
)


async def collect_from_players(
    players: list[Player],
    fixtures: list[Fixture],
    team_names: dict[str, str],
    mv_history_by_player: dict[str, list[PlayerHistory]],
    perf_by_player: dict[str, list[MatchdayPerformance]] | None = None,
) -> list[ExplosionCandidate]:
    """Bewertet eine beliebige Spieler-Liste (Kader ODER Markt) zu Kandidaten."""
    perf_by_player = perf_by_player or {}
    engine = RuleEngine()
    candidates: list[ExplosionCandidate] = []
    for player in players:
        mv_history = mv_history_by_player.get(player.id, [])
        performances = perf_by_player.get(player.id, [])

        signals = [
            base_points_signal(performances),
            form_signal(performances),
            matchup_signal(player.team_id, fixtures, team_names),
            trend_signal(mv_history),
        ]
        total = engine.score(signals)
        # Aufstellungs-Score OHNE den Marktwert-Trend: MW-Momentum erzeugt keine
        # Spieltagspunkte. Die gewichtete Mittelung der Engine renormiert die
        # Rest-Gewichte automatisch über die verbleibende Gewichtssumme.
        lineup = engine.score([s for s in signals if s.name != "trend"])
        # Wendepunkt-Marker NACH beiden Score-Berechnungen anhängen: trend_1d ist
        # reine Anzeige + Grundlage der Gewinnmitnahme (propose_sells Pass 3) und
        # darf weder Kauf- noch Elf-Score verschieben (Muster: Community-Signal).
        signals.append(trend_1d_signal(mv_history))
        candidates.append(
            ExplosionCandidate(
                player_id=player.id,
                name=f"{player.first_name} {player.last_name}".strip(),
                team_id=player.team_id,
                position=int(player.position),
                current_market_value=player.market_value,
                total_score=total,
                lineup_score=lineup,
                signals=signals,
            )
        )
    return engine.rank(candidates)


async def collect_candidates(
    squad_players: list[SquadPlayer],
    fixtures: list[Fixture],
    team_names: dict[str, str],
    mv_history_by_player: dict[str, list[PlayerHistory]],
    perf_by_player: dict[str, list[MatchdayPerformance]] | None = None,
) -> list[ExplosionCandidate]:
    return await collect_from_players(
        [sp.player for sp in squad_players],
        fixtures,
        team_names,
        mv_history_by_player,
        perf_by_player,
    )
