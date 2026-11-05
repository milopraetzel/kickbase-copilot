"""Individuelle Gebote aus finanzierbaren Mannschaftsalternativen.

Der Preisbereich folgt dem Mannschaftsnutzen. Eine Konkurrenzprognose darf nur
der Aufrufer mit belegter Modellversion liefern; unbekannt bleibt unbekannt.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

#: Genauigkeit, mit der ein zu teures Zielgebot auf den paketneutralen Betrag gekürzt wird.
TARGET_STEP_EUR = 5_000


@dataclass(frozen=True)
class TeamValue:
    filled_slots: int
    points: float
    remaining_cash: int

    def key(self) -> tuple[int, float, int]:
        return self.filled_slots, round(self.points, 6), self.remaining_cash

    def valid(self) -> bool:
        return (
            type(self.filled_slots) is int and 0 <= self.filled_slots <= 11
            and math.isfinite(self.points)
            and type(self.remaining_cash) is int and self.remaining_cash >= 0
        )


@dataclass(frozen=True)
class BidDecision:
    amount: int | None
    max_bid_eur: int | None
    scenarios: list[dict]
    probability_status: str
    reason: str


def choose_bid(
    *,
    market_value: int,
    minimum_bid: int,
    maximum_bid: int,
    evaluate_win: Callable[[int], TeamValue | None],
    evaluate_loss: Callable[[], TeamValue],
    probability: Callable[[int], float | None] | None = None,
    additional_prices: Iterable[int] = (),
    target_amount: int | None = None,
    target_reason: str | None = None,
) -> BidDecision:
    """Restmannschaft je Preis lösen, dann Erfolg und Ersatzweg vergleichen.

    Die Preisgrenze wird eurogenau bis zum technischen Maximum gesucht. Dazu muss
    ``evaluate_win`` bei steigendem Preis einen monoton nicht besseren optimalen
    Mannschaftswert liefern. Der tatsächliche Betrag ist der beste *geprüfte*
    Preis (bis 25 gleichmäßig verteilte Werte plus Budgetbruchpunkte), keine
    Behauptung eines globalen Optimums der unbekannten Auktionsfunktion.

    ``target_amount`` (Gebotsregel V3, Emil 25.09.2026) ist der Einstieg, solange die
    Zuschlagschance unbekannt ist — begrenzt auf [Mindestpreis, Obergrenze], gekürzt,
    wo ein höherer Betrag dem übrigen Paket Plätze oder Punkte nähme, und selbst ein
    geprüftes Szenario. Liefert ein geladenes Modell Wahrscheinlichkeiten, entscheidet es.
    """
    if any(type(n) is not int or n <= 0 for n in (market_value, minimum_bid, maximum_bid)):
        return BidDecision(None, None, [], "unknown", "Ungültige Preisgrundlage.")
    if minimum_bid > maximum_bid:
        return BidDecision(None, None, [], "unknown", "Mindestpreis über zulässiger Kappe.")
    loss = evaluate_loss()
    if not loss.valid():
        return BidDecision(None, None, [], "unknown", "Ersatzplan ist nicht finanziert.")
    evaluated: dict[int, TeamValue | None] = {}

    def win_at(amount: int) -> TeamValue | None:
        if amount not in evaluated:
            value = evaluate_win(amount)
            evaluated[amount] = value if value is not None and value.valid() else None
        return evaluated[amount]

    def useful(amount: int) -> bool:
        value = win_at(amount)
        return value is not None and value.key() >= loss.key()

    if not useful(minimum_bid):
        return BidDecision(
            None, None, [], "unknown", "Die beste finanzierbare Alternative ist besser."
        )
    low, high = minimum_bid, maximum_bid
    while low < high:
        middle = (low + high + 1) // 2
        if useful(middle):
            low = middle
        else:
            high = middle - 1
    cap = low
    entry, note = minimum_bid, ""
    has_target = type(target_amount) is int and target_amount > 0
    if has_target:
        # Das Zielgebot darf nur Restgeld kosten (Review 25.09.2026): Plätze und Punkte
        # des Pakets zum Mindestpreis bleiben erhalten, sonst verdrängt ein Aufschlag
        # bei knappem Geld ein zweites Ziel. Und es bleibt echt besser als der Ersatzweg:
        # Hebt es ein Ziel genau auf den Preis eines gleich guten Ersatzes, darf der
        # Planer den Ersatz wählen, auf den dann nur der Marktwert geboten wird.
        want = min(max(target_amount, minimum_bid), cap)
        base = win_at(minimum_bid)
        assert base is not None
        floor = (base.filled_slots, round(base.points, 6))
        strict = base.key() > loss.key()

        def holds(amount: int) -> bool:
            value = win_at(amount)
            return (
                value is not None
                and (value.filled_slots, round(value.points, 6)) >= floor
                and (not strict or value.key() > loss.key())
            )

        if holds(want):
            entry = want
            if target_amount > cap:
                limit = f"{cap:,}".replace(",", ".")
                note = (
                    f" Begrenzt auf die Obergrenze {limit} € (Mannschaftsvergleich, "
                    "Einzelrahmen oder technische Kappe)."
                )
            elif target_amount < minimum_bid:
                note = " Zielgebot unter dem Mindestpreis; Einstieg zum Mindestpreis."
        else:
            kept, lost = minimum_bid, want
            while lost - kept > TARGET_STEP_EUR:
                middle = (kept + lost) // 2
                if holds(middle):
                    kept = middle
                else:
                    lost = middle
            entry = kept
            shortened = f"{entry:,}".replace(",", ".")
            note = (
                f" Auf {shortened} € gekürzt: Ein höherer Betrag hätte das übrige Paket "
                "einen Platz oder Punkte gekostet oder einen gleich guten, günstigeren "
                "Ersatz gleichgestellt."
            )
    prices = {minimum_bid, cap, entry}
    prices.update(minimum_bid + (cap - minimum_bid) * n // 24 for n in range(25))
    prices.update(p for p in additional_prices if type(p) is int and minimum_bid <= p <= cap)
    scenarios: list[dict] = []
    known = probability is not None
    for amount in sorted(prices):
        win = win_at(amount)
        if win is None or win.key() < loss.key():
            continue
        p = probability(amount) if probability is not None else None
        if p is not None and (isinstance(p, bool) or not math.isfinite(p) or not 0 <= p <= 1):
            p = None
        known = known and p is not None
        scenarios.append({
            "amount": amount, "premium_eur": amount - market_value,
            "premium_pct": round(100 * (amount / market_value - 1), 3),
            "win_probability": p, "win": asdict(win), "loss": asdict(loss),
            "expected_slots": None if p is None else p * win.filled_slots
            + (1 - p) * loss.filled_slots,
            "expected_points": None if p is None else p * win.points + (1 - p) * loss.points,
            "expected_cash": None if p is None else p * win.remaining_cash
            + (1 - p) * loss.remaining_cash,
        })
    if not known:
        # Ohne Zuschlagsmodell keine behauptete Chance. Einstieg ist das Zielgebot des
        # Aufrufers (V3), sonst der kleinste sinnvolle Preis.
        reason = (
            "Kleinster sinnvoller Einstieg; zusätzliche Zuschlagschance noch nicht kalibriert."
        )
        if has_target:
            if not any(s["amount"] == entry for s in scenarios):
                entry = minimum_bid
                note = " Zielgebot nicht als Mannschaftsalternative tragfähig; Mindestpreis."
            reason = (
                (target_reason or "Zielgebot als Einstieg.").strip()
                + note
                + " Zuschlagschance nicht kalibriert."
            )
        for scenario in scenarios:
            scenario["selected"] = scenario["amount"] == entry
        return BidDecision(entry, cap, scenarios, "unknown", reason)
    best = max(scenarios, key=lambda s: (
        round(s["expected_slots"], 8), round(s["expected_points"], 6),
        s["expected_cash"], -s["amount"],
    ))
    best_key = (round(best["expected_slots"], 8), round(best["expected_points"], 6),
                best["expected_cash"])
    if best_key < loss.key():
        return BidDecision(None, cap, scenarios, "model",
                           "Kein zusätzlicher erwarteter Teamnutzen.")
    for scenario in scenarios:
        scenario["selected"] = scenario is best
    return BidDecision(
        best["amount"], cap, scenarios, "model",
        "Höchster erwarteter Mannschaftsnutzen unter den geprüften Preisen und Ersatzwegen.",
    )
