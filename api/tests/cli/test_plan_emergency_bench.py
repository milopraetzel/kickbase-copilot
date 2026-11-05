"""Realer Güther-Befund; Deadline-/LI-Varianten sind isolierte Domainbedingungen."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.kickbase.models import MarketPlayer, Player
from kickbase_agent.scouts.ligainsider.models import Status

from .test_plan_roster_value import _prepare

RAW = json.loads((Path(__file__).parents[1] / "fixtures/engine_20260913"
                  / "linus-guether-detail.json").read_text())
NOW = datetime(2026, 9, 13, 9, 40, 47, tzinfo=UTC)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


@pytest.mark.parametrize("hours,li_bench,allowed", [
    (120, False, False), (120, True, False), (24, False, True), (12, True, True),
    (None, False, False), (-1, False, False),
    # 16.09.2026: Fenster 24 h -> 96 h. Der reale Guether-Befund vom 13.09. lag
    # 128 h vor der Frist und bleibt gesperrt; 25 h sind jetzt eine zulaessige
    # Notbesetzung, weil die Auktion sonst vor dem Fenster endet.
    (128, False, False), (25, False, True),
])
async def test_real_falling_bank_player_only_fills_an_imminent_deadline(
    monkeypatch, hours, li_bench, allowed,
):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 3, 3, 3, 4], cash=3_120_437)
    player = Player.from_api(RAW)
    assert player.id == "13629" and player.lineup_prob == 5
    assert player.mv_change_24h == -144727 and player.points == 0
    if li_bench:
        player = player.model_copy(update={"lineup_prob": 1})
        original = plan_mod.enrich_candidates

        async def enrich(candidates, **kwargs):
            result = await original(candidates, **kwargs)
            result.availabilities = [
                replace(a, status=Status.BENCH) if a.player_id == player.id else a
                for a in result.availabilities
            ]
            return result

        monkeypatch.setattr(plan_mod, "enrich_candidates", enrich)
    client.market = [MarketPlayer(player=player, price=player.market_value,
                                 expires_at=NOW + timedelta(hours=11))]

    async def detail(*args):
        return player

    client.get_player = detail
    monkeypatch.setattr(plan_mod, "datetime", Clock)
    monkeypatch.setattr(plan_mod, "matchday_deadline", lambda *_: (
        (4, NOW + timedelta(hours=hours)) if hours is not None else None
    ))
    plan = await plan_mod.build_plan()
    assert bool(plan.buys) is allowed
    if allowed:
        assert "Notbesetzung" in " ".join(plan.purchase_reviews[player.id]["reasons"])
    else:
        assert not plan.purchase_reviews[player.id]["eligible"]
