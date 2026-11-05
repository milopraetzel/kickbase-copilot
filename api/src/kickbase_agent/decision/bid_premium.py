"""Gebotsaufschlag V3 für Kickbase-Auktionen (Emil 25.09.2026: „GO für V3").

Grundlage: ``docs/ANALYSE-GEBOTSPREIS-2026-09-25.md`` — Backtest gegen alle 236
Liga-Auktionen seit 24.08.2026, Prognosen und Zuschlagskurve walk-forward geschätzt.
Ein Gebot zum Marktwert schlug keinen einzigen Siegerpreis; seit 11.09. verlor die
Engine damit jede Kickbase-Auktion.

Der Aufschlag ist der kleinere Wert aus

* **Zuschlagskurve**: Aufschlag, der 80 % vergleichbarer Liga-Siegerpreise geschlagen
  hätte (Quantilsregression auf letzten Tagesschritt und Marktwertgröße), und
* **3-Tage-Prognose + 3 Prozentpunkte**: erwarteter Anstieg (Median-Regression auf den
  letzten Tagesschritt) plus Marge,

höchstens 15 %; nach einem fallenden Tagesschritt (≤ −0,5 %) nur 0,5 %, und 0,5 % ist auch
sonst die Untergrenze (ein Gebot genau zum Marktwert gewinnt keine Auktion). Die Prognose ist
der Deckel, nicht das Gaspedal: Wer den ganzen erwarteten Anstieg bietet, gewinnt genau
die Auktionen, in denen er ihn überschätzt hat (Backtest: −5,7 % Buchwert nach 7 Tagen).

Die Koeffizienten stammen aus sechs Wochen einer Liga (Stand 24.09.2026). Sie sind ein
Parameter mit Decision-Record (``plan.bid_premium``), keine gelernte Zuschlagschance:
Die Engine behauptet damit keine Gewinnwahrscheinlichkeit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BID_PREMIUM_VERSION = "v3-2026-09-25"

#: Zuschlagskurve q80: ln(1 + p) = a + b · Schritt + c · ln(MW / 5 M).
WIN_CURVE_INTERCEPT = 0.190
WIN_CURVE_STEP = 1.794
WIN_CURVE_SIZE = -0.075
WIN_CURVE_SIZE_REFERENCE_EUR = 5_000_000
#: 3-Tage-Median-Prognose des Marktwerts: g3 = a + b · Schritt (jüngstes 21-Tage-Fenster).
GROWTH_INTERCEPT = -0.004
GROWTH_STEP = 2.73
GROWTH_MARGIN = 0.03
PREMIUM_CAP = 0.15
CONVICTION_BONUS = 0.05
CONVICTION_CAP = 0.20
#: Ab diesem Tagesschritt gilt ein Spieler als fallend.
FALLER_STEP = -0.005
FALLER_PREMIUM = 0.005
#: Der Tagesschritt geht begrenzt in beide Formeln ein (wie im Backtest).
STEP_FLOOR = -0.10
STEP_CEILING = 0.20


def last_step(market_value: int, change_24h: int, observed: bool | None) -> float | None:
    """Letzter Tagesschritt MW(heute) / MW(gestern) − 1 aus Kickbases ``tfhmvt``.

    ``tfhmvt`` ist MW(heute) − MW(gestern) in Euro (am 25.09.2026 gegen
    ``player_history`` belegt: Schwäbe −445.772 €, Remberg +263.190 €). Nicht
    geliefert heißt unbekannt, nicht null: dann kein Trend.
    """
    if observed is not True or type(market_value) is not int or type(change_24h) is not int:
        return None
    previous = market_value - change_24h
    if market_value <= 0 or previous <= 0:
        return None
    return change_24h / previous


def _pct(value: float) -> str:
    return f"{100 * value:+.1f} %".replace(".", ",")


@dataclass(frozen=True)
class BidPremium:
    amount: int
    premium: float
    step: float
    win_curve: float | None
    growth_margin: float | None
    rule: str  # faller | kurve | prognose | deckel | boden

    def describe(self) -> str:
        parts = f"letzter Tagesschritt {_pct(self.step)}"
        if self.win_curve is not None and self.growth_margin is not None:
            parts += (
                f"; Zuschlagskurve {_pct(self.win_curve)}, "
                f"3-Tage-Prognose + 3 Pp {_pct(self.growth_margin)}"
            )
        cap_val = CONVICTION_CAP if self.premium > PREMIUM_CAP else PREMIUM_CAP
        cap = f", gedeckelt auf {_pct(cap_val)}" if self.rule.startswith("deckel") else ""
        sig = ", Conviction-Bonus +5,0 %" if self.rule.endswith("+signal") else ""
        return f"Gebotsregel V3: {_pct(self.premium)} auf den Marktwert ({parts}{sig}{cap})."

    def as_dict(self) -> dict:
        def pct(value: float | None) -> float | None:
            return None if value is None else round(100 * value, 3)

        return {
            "version": BID_PREMIUM_VERSION,
            "amount_eur": self.amount,
            "premium_pct": pct(self.premium),
            "step_pct": pct(self.step),
            "win_curve_pct": pct(self.win_curve),
            "growth_margin_pct": pct(self.growth_margin),
            "rule": self.rule,
        }


def bid_premium(
    market_value: int,
    change_24h: int,
    observed: bool | None,
    *,
    conviction_bonus: float = 0.0,
    cap: float = PREMIUM_CAP,
    rival_scarcity_count: int = 0,
    starter_gain_points: float = 0.0,
) -> BidPremium | None:
    """Zielgebot nach V3 inkl. Mitspieler-Positionsknappheit & Top-Starter-Anker."""
    step = last_step(market_value, change_24h, observed)
    if step is None:
        return None
    if step <= FALLER_STEP and starter_gain_points < 80.0:
        premium, win_curve, growth, rule = FALLER_PREMIUM, None, None, "faller"
    else:
        clipped = min(max(step, STEP_FLOOR), STEP_CEILING)
        win_curve = math.expm1(
            WIN_CURVE_INTERCEPT
            + WIN_CURVE_STEP * clipped
            + WIN_CURVE_SIZE * math.log(market_value / WIN_CURVE_SIZE_REFERENCE_EUR)
        )
        growth = GROWTH_INTERCEPT + GROWTH_STEP * clipped + GROWTH_MARGIN
        if starter_gain_points >= 80.0:
            starter_anchor = min(0.065, 0.03 + (starter_gain_points - 80.0) * 0.00025)
            growth = max(growth, starter_anchor)
        premium, rule = (win_curve, "kurve") if win_curve < growth else (growth, "prognose")
        if rival_scarcity_count > 0:
            premium += min(0.045, rival_scarcity_count * 0.012)
        if conviction_bonus > 0:
            premium += conviction_bonus
        if premium > cap:
            premium, rule = cap, "deckel"
        # Untergrenze wie beim Faller (Review 25.09.2026): Sonst ergäbe ein teurer, fast
        # flacher Spieler MW + 0 — das gewinnt nie — und der Aufschlag wäre nicht monoton.
        if premium < FALLER_PREMIUM:
            premium, rule = FALLER_PREMIUM, "boden"
        if conviction_bonus > 0:
            rule = f"{rule}+signal"
    return BidPremium(
        amount=round(market_value * (1 + premium)),
        premium=premium,
        step=step,
        win_curve=win_curve,
        growth_margin=growth,
        rule=rule,
    )
