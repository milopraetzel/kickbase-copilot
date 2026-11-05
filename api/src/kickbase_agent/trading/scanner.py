"""Marktwert-Momentum: Steiger finden, Kurven-Wendepunkt (Verkaufssignal) erkennen."""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class Momentum:
    daily_change: float  # robuster Median der Tagesänderungen (€/Tag)
    pct_per_day: float   # relativer Tagesanstieg
    rising: bool
    flattening: bool     # Anstieg lässt nach → Trading-Verkaufssignal
    spike: bool = False  # einmaliger Sprung erkannt (kein nachhaltiges Momentum)


@dataclass(frozen=True)
class Riser:
    player_id: str
    name: str
    market_value: int
    momentum: Momentum


def momentum(values: list[int], window: int = 5) -> Momentum:
    """Robustes Momentum aus einer Marktwert-Reihe (ältester → neuester).

    Nutzt den **Median** der Tagesänderungen statt der reinen Differenz — ein
    einmaliger Sprung (z. B. +5M an einem Tag, sonst flach) verzerrt den Median
    nicht und wird als `spike` markiert statt als Steiger gewertet.
    """
    if len(values) < 2:
        return Momentum(0.0, 0.0, False, False)

    n = min(window, len(values) - 1)
    deltas = [values[-i] - values[-i - 1] for i in range(1, n + 1)]  # jüngste zuerst
    daily_change = statistics.median(deltas)
    base = values[-1 - n] or 1
    pct_per_day = daily_change / base

    # Spike: die größte Einzeländerung dominiert die Summe → nicht nachhaltig.
    total = sum(deltas)
    biggest = max(deltas, key=abs) if deltas else 0
    spike = total != 0 and abs(biggest) > abs(total) * 0.8 and len(deltas) >= 3

    # Wendepunkt: jüngste Änderung deutlich schwächer als die früheren.
    flattening = False
    if len(deltas) >= 3:
        recent = deltas[0]  # jüngste Tagesänderung
        earlier = statistics.median(deltas[1:])
        flattening = recent < earlier * 0.5

    return Momentum(daily_change, pct_per_day, daily_change > 0, flattening, spike)


def rank_risers(
    market: list[tuple[str, str, int, list[int]]],
    min_pct_per_day: float = 0.005,
    window: int = 3,
) -> list[Riser]:
    """Markt-Kandidaten (id, name, mv, mv_verlauf) → nach Momentum sortierte Steiger.

    Filtert Spieler ohne echten Anstieg (unter min_pct_per_day) heraus.
    """
    risers: list[Riser] = []
    for player_id, name, mv, values in market:
        m = momentum(values, window)
        # Steiger nur mit echtem, nachhaltigem Momentum — Spikes fliegen raus.
        if m.rising and not m.spike and m.pct_per_day >= min_pct_per_day:
            risers.append(Riser(player_id, name, mv, m))
    risers.sort(key=lambda r: r.momentum.daily_change, reverse=True)
    return risers
