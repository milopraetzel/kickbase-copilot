"""Kader-Zonen: Core (Punkte) vs. Trading (Kapital) + freie Slots."""

from kickbase_agent.kickbase.models import Player, Position, SquadPlayer
from kickbase_agent.trading.zones import classify_squad, free_trading_slots


def _sp(pid: str) -> SquadPlayer:
    return SquadPlayer(
        player=Player(id=pid, first_name="", last_name=f"P{pid}",
                      position=Position.MIDFIELDER, market_value=1_000_000, team_id="1"),
        is_starter=False,
    )


def test_classify_splits_by_trading_ids():
    squad = [_sp("1"), _sp("2"), _sp("3")]
    zones = classify_squad(squad, trading_ids={"2"})

    assert [sp.player.id for sp in zones.core] == ["1", "3"]
    assert [sp.player.id for sp in zones.trading] == ["2"]


def test_classify_all_core_when_no_trading_history():
    squad = [_sp("1"), _sp("2")]
    zones = classify_squad(squad, trading_ids=set())

    assert len(zones.core) == 2
    assert zones.trading == []


def test_free_slots():
    assert free_trading_slots(squad_size=15, max_squad_size=18) == 3
    assert free_trading_slots(squad_size=18, max_squad_size=18) == 0
    assert free_trading_slots(squad_size=20, max_squad_size=18) == 0  # nie negativ
