"""Kader-Zweiteilung: Core (behalten für Punkte) vs. Trading (Kapital, zum Verkauf).

Ein Spieler ist 'Trading', wenn der Agent ihn als Trading-Kauf angeschafft hat
(markiert über die rule_id in transfer_actions). Alles andere ist Core. Am
Saisonstart ohne Trading-Historie ist der ganze Kader Core.
"""

from __future__ import annotations

from dataclasses import dataclass

from kickbase_agent.kickbase.models import SquadPlayer


@dataclass(frozen=True)
class SquadZones:
    core: list[SquadPlayer]      # Punkte-Kader (S11 + Bank) — Ersatz-Duell
    trading: list[SquadPlayer]   # reines Handelsmaterial — Wendepunkt-Verkauf


def classify_squad(squad: list[SquadPlayer], trading_ids: set[str]) -> SquadZones:
    core: list[SquadPlayer] = []
    trading: list[SquadPlayer] = []
    for sp in squad:
        (trading if sp.player.id in trading_ids else core).append(sp)
    return SquadZones(core=core, trading=trading)


def free_trading_slots(squad_size: int, max_squad_size: int) -> int:
    """Freie Kaderplätze für neue Trading-Käufe (nie negativ)."""
    return max(0, max_squad_size - squad_size)
