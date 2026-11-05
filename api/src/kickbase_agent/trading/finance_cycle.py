"""Weekly-Finance-Cycle — die Handbremse lösen, aber pünktlich bremsen.

Mo–Do investiert der Agent aggressiv (Konto darf ins Minus, siehe finance.py).
Freitag 17:00 stellt der Purge das Konto vor dem Spieltag wieder glatt: er verkauft
Trading-Material — schwächste Steiger zuerst — bis der Kontostand über dem Puffer
liegt. Reicht das Material nicht (shortfall > 0), eskaliert der Worker per Discord.
"""

from __future__ import annotations

from dataclasses import dataclass

# Wochentage: 0=Montag … 6=Sonntag
_INVEST_DAYS = {0, 1, 2, 3}
_PURGE_DAY = 4  # Freitag

DEFAULT_PURGE_BUFFER = 5_000


def finance_phase(weekday: int) -> str:
    """'invest' (Mo–Do) · 'purge' (Fr) · 'hold' (Sa/So, Spieltag)."""
    if weekday == _PURGE_DAY:
        return "purge"
    if weekday in _INVEST_DAYS:
        return "invest"
    return "hold"


@dataclass
class PurgeResult:
    sells: list[tuple[str, str, int]]  # (player_id, name, market_value)
    raised: int
    final_balance: int
    shortfall: int  # > 0 → Trading-Material reicht nicht, Failsafe nötig


def plan_purge(
    holdings: list[tuple[str, str, int, float]],
    budget: int,
    buffer: int = DEFAULT_PURGE_BUFFER,
    fallback: list[tuple[str, str, int]] | None = None,
) -> PurgeResult:
    """Verkaufe Trading-Holdings (schwächstes Momentum zuerst), bis Konto ≥ Puffer.

    holdings: (player_id, name, market_value, momentum_€/Tag)
    budget:   aktueller Kontostand (nach Invest-Phase meist negativ)
    fallback: (Review-Fund) Notfall-Pool aus dem restlichen Kader — reicht das
              Trading-Material nicht, wird hieraus weiterverkauft. Ein Minus beim
              Anpfiff kostet ALLE Punkte des Spieltags; jeder Verkauf ist billiger.
    """
    needed = buffer - budget
    sells: list[tuple[str, str, int]] = []
    raised = 0
    if needed > 0:
        for player_id, name, mv, _momentum in sorted(holdings, key=lambda h: h[3]):
            if raised >= needed:
                break
            sells.append((player_id, name, mv))
            raised += mv
        if raised < needed:
            for player_id, name, mv in fallback or []:
                if raised >= needed:
                    break
                sells.append((player_id, name, mv))
                raised += mv
    return PurgeResult(
        sells=sells,
        raised=raised,
        final_balance=budget + raised,
        shortfall=max(0, needed - raised),
    )
