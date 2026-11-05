"""Notwendige wirtschaftliche Prüfungen für den Tausch einer bestehenden Elf.

Der Elfindex bleibt ein Index, keine Punkte- oder Preisprognose. Dieser Filter
verhindert lediglich teure Gleichwertigkeit und mehrfach ausgegebenes Geld.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import replace
from math import isclose

from kickbase_agent.decision.lineup import MIN_SQUAD_SIZE, best_lineup
from kickbase_agent.decision.policies import SwapPair
from kickbase_agent.intel.models import ExplosionCandidate


def filter_roster_swaps(
    swaps: list[SwapPair],
    *,
    verified_squad: list[ExplosionCandidate],
    market: list[ExplosionCandidate],
    remaining_cash: int,
    sale_haircut: float,
    planned_buys: list[ExplosionCandidate] | None = None,
    alternative_factory: Callable[[ExplosionCandidate, set[str]], SwapPair | None] | None = None,
) -> tuple[list[SwapPair], list[str]]:
    """Nur bessere oder bei gleicher Elf netto günstigere finanzierbare Tausche.

    remaining_cash enthält bereits Konto minus ALLE offenen und neu geplanten
    Gebote plus vorhandene zulässige Bankreserve. Jeder akzeptierte Tausch
    reserviert höchstens seinen positiven Nettobedarf. Bedingte Überschüsse
    finanzieren keine anderen Auktionen, die auch allein gewonnen werden können.
    Der konservative Abgang
    wird erst nach bestätigtem Erwerb des Ersatzes ausgeführt (bestehender Pfad).
    Offene Starterlücken haben Vorrang; ein Swap schafft keinen zusätzlichen
    Elf-Platz und kann dort noch kein vollständiges Teamupgrade nachweisen.

    planned_buys fließen nur in den bedingten Elfvergleich bei allen Zuschlägen
    ein, nie in den Nachweis heutigen Eigentums oder der vorhandenen Deckung.
    alternative_factory verwendet die Preis-/Schutzregeln des Aufrufers erneut;
    abgelehnte Ziele werden nur für denselben Abgang ausgeschlossen.
    """
    current = {c.player_id: c for c in verified_squad}
    candidates = {c.player_id: c for c in market}
    planned = {c.player_id: c for c in planned_buys or []}
    accepted: list[SwapPair] = []
    notes: list[str] = []
    pending = deque(swaps)
    attempted: dict[str, set[str]] = {}
    accepted_targets: set[str] = set()

    def try_alternative(pair: SwapPair) -> None:
        if alternative_factory is None:
            return
        excluded = attempted[pair.faller.player_id] | accepted_targets
        alternative = alternative_factory(pair.faller, excluded)
        if alternative is not None and str(alternative.bid.action.player_id) not in excluded:
            pending.appendleft(alternative)

    while pending:
        pair = pending.popleft()
        attempted.setdefault(pair.faller.player_id, set()).add(str(pair.bid.action.player_id))
        owned_before = best_lineup(list(current.values()))
        name = pair.bid.player_name or str(pair.bid.action.player_id)
        if len(owned_before.starters) < MIN_SQUAD_SIZE:
            notes.append(
                f"{name}: zuerst die belegten Starterlücken schließen; Tausch zurückgestellt."
            )
            continue
        replacement = candidates.get(str(pair.bid.action.player_id))
        if replacement is None or pair.faller.player_id not in current:
            notes.append(f"{name}: vollständiger Vergleich des vorhandenen Kaders fehlt.")
            continue
        if replacement.player_id in current:
            try_alternative(pair)
            continue
        after_squad = dict(current)
        del after_squad[pair.faller.player_id]
        after_squad[replacement.player_id] = replacement
        owned_after = best_lineup(list(after_squad.values()))
        if len(owned_after.starters) < MIN_SQUAD_SIZE:
            notes.append(f"{name}: nach dem Tausch ist keine vollständige Elf belegt.")
            try_alternative(pair)
            continue
        before = best_lineup(list((current | planned).values()))
        after = best_lineup(list((after_squad | planned).values()))
        # Vorhandener Reserve-Abschlag; keine neue Preis-/Ertragsannahme einführen.
        proceeds = int(pair.sell_amount * sale_haircut)
        amount = pair.bid.action.amount
        if amount is None:
            notes.append(f"{name}: tatsächlicher Gebotsbetrag fehlt.")
            try_alternative(pair)
            continue
        net_cost = amount - proceeds
        equal = isclose(after.expected_score, before.expected_score, abs_tol=1e-12)
        improves = after.expected_score > before.expected_score and not equal
        if not improves and not (equal and net_cost < 0):
            notes.append(
                f"{name}: kein Mehrwert der besten Elf; konservative Zusatzkosten {net_cost:,} € "
                "rechtfertigen keinen Tausch."
            )
            try_alternative(pair)
            continue
        cash_needed = max(0, net_cost)
        if remaining_cash - cash_needed < 0:
            notes.append(
                f"{name}: nach allen Geboten und diesem Tausch fehlen "
                f"{cash_needed - remaining_cash:,} € Finanzierung."
            )
            try_alternative(pair)
            continue
        remaining_cash -= cash_needed
        comparison = (
            f" Gesamtelf-Index {before.expected_score:.3f} → {after.expected_score:.3f} "
            f"bei allen geplanten Zuschlägen (keine Punktprognose); "
            f"konservativer Nettobedarf {net_cost:,} €, "
            f"verbleibende Deckung nach allen Geboten {remaining_cash:,} €."
        )
        accepted.append(
            replace(pair, bid=replace(pair.bid, rationale=pair.bid.rationale + comparison))
        )
        current = after_squad
        accepted_targets.add(replacement.player_id)
    return accepted, notes
