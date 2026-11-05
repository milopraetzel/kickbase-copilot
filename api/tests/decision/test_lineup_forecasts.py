"""Eigene Aufstellung folgt den Punkteprognosen; kein bedingter Erwerb ist Besitz."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kickbase_agent.decision import lineup_service as service
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.kickbase.models import Player, SquadPlayer
from kickbase_agent.scouts.ligainsider.models import Status


def _candidate(pid, position, score=0.5):
    return ExplosionCandidate(player_id=pid, name=pid, position=position, team_id="1",
                              current_market_value=1_000_000, total_score=score,
                              lineup_score=score, signals=[])


def _candidates():
    return [_candidate(str(i), pos, 1.0 if i == 0 else 0.1)
            for i, pos in enumerate([1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 1])]


def test_forecast_lineup_prefers_points_over_old_index_and_ignores_unowned_candidate():
    candidates = _candidates()
    forecasts = {c.player_id: {"expected_points": 100.0 if c.player_id == "11" else 10.0}
                 for c in candidates}
    lineup = service._forecast_lineup([*candidates, _candidate("not-owned", 1, 1.0)], forecasts)
    assert {c.player_id for c in lineup.starters} == set(forecasts) - {"0"}
    assert lineup.expected_score == 200.0
    assert len(lineup.starters) == 11


@pytest.mark.asyncio
async def test_partial_owned_roster_keeps_forecast_basis_without_legacy_fallback(monkeypatch):
    candidates = _candidates()[:8]
    squad = [SquadPlayer(player=Player(
        id=c.player_id, first_name="", last_name=c.name, position=c.position,
        team_id="1", market_value=1_000_000,
    )) for c in candidates]
    client = SimpleNamespace(get_matchdays=AsyncMock(return_value=[]),
                             get_team_names=AsyncMock(return_value={}))
    monkeypatch.setattr(service, "load_history", lambda *args: {})
    monkeypatch.setattr(service, "load_performance", lambda *args: {})
    monkeypatch.setattr(service, "collect_candidates", AsyncMock(return_value=candidates))
    monkeypatch.setattr(service, "enrich_candidates", AsyncMock(return_value=SimpleNamespace(
        candidates=candidates, availabilities=[SimpleNamespace(player_id="1", status=Status.BENCH)],
    )))
    monkeypatch.setattr(service, "load_active", lambda: None)
    monkeypatch.setattr(service, "forecast_player", lambda *args, **kwargs: {
        "expected_points": 50.0, "horizon_points": 200.0, "expected_minutes": 75.0,
        "forecast_available": True, "reasons": [],
    })
    monkeypatch.setattr(service, "best_lineup", lambda *args: pytest.fail("Legacy fallback"))
    result = await service.recommend_lineup(client, None, squad)
    assert result.points_basis == "conditional_points_with_role_heuristic"
    # Seit L-109 (18.09.2026): kein leeres Ergebnis mehr, sondern eine Teil-Elf mit
    # allen unterbringbaren Spielern — weiter auf Prognosebasis, nie über den Index.
    placed = {c.player_id for c in result.lineup.starters}
    benched = {c.player_id for c in result.lineup.bench}
    assert placed | benched == {c.player_id for c in candidates}
    assert placed and len(placed) < 11
    assert result.lineup.open_slots == 11 - len(placed)
    assert [pid for pid in result.lineup.slot_ids if pid] == [
        c.player_id for c in result.lineup.starters
    ]
    assert result.forecasts["1"]["expected_points"] == 0
    assert result.forecasts["1"]["horizon_points"] == 0
    assert result.forecasts["1"]["expected_minutes"] == 0
    assert result.forecasts["1"]["availability_adjustment"]["factor"] == 0
