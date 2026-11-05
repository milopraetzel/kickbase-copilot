"""Fair-Value-Modell: „fairster Preis" eines Spielers aus seiner Punktausbeute.

Methodik (Idee inspiriert vom BaseXI Fair Value Radar, base-xi.de — Berechnung
und Daten sind komplett eigene): Über die Liga-weite Wolke etablierter Spieler
wird eine Potenz-Kurve mv ≈ a · ap^b gefittet (Least Squares im log-log-Raum,
reine Stdlib, kein numpy). Der faire Preis eines Spielers ist sein Kurvenwert,
wobei bei dünner Datenlage (wenige Gesamtpunkte) nur ein Teil der Abweichung
zum aktuellen Marktwert angerechnet wird — die Kurve „zieht" dann schwächer.

Ausdrücklich KEIN Zukunftsblick: bewertet wird ausschließlich die bisherige
Saison (Ø-Punkte vs. Marktwert), keine kommenden Gegner, Formkurven o. Ä.

Row-Keys (siehe ``enrich``): die Funktionen lesen ``avg``/``ap`` (Ø-Punkte),
``punkte``/``tp`` (Saison-Gesamtpunkte) und ``mv`` — kompatibel mit den
Cockpit-Zeilen (Detail-Keys) UND der Team-Profil-Wolke.
"""

from __future__ import annotations

import math
from typing import Any

#: Mindestzahl an Stützpunkten für einen belastbaren Fit.
MIN_FIT_POINTS = 12

#: Fit-Filter „etabliert": Marktwert-Untergrenze …
FIT_MIN_MV = 3_000_000
#: … plus Saison-Gesamtpunkte (falls bekannt) bzw. Ø-Punkte als Ersatz.
FIT_MIN_TP = 600
FIT_MIN_AP = 60

#: Konfidenz-Dämpfung: unterhalb dieser Gesamtpunkte wird nur der angegebene
#: Anteil der Abweichung Kurve − Marktwert angerechnet (aufsteigend geprüft).
CONFIDENCE_STEPS: tuple[tuple[int, float], ...] = ((200, 0.5), (600, 0.75), (1000, 0.9))
#: Dämpfung, wenn die Gesamtpunkte unbekannt sind (kein Detail geladen).
CONFIDENCE_UNKNOWN = 0.75


def _num(v: Any) -> float | None:
    """Zahl aus einem Row-Wert — bool/None/Strings zählen als „fehlt"."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    return None


def _row_ap(p: dict[str, Any]) -> float | None:
    """Ø-Punkte einer Zeile: Detail-Key ``avg`` zuerst, sonst Wolken-Key ``ap``."""
    ap = _num(p.get("avg"))
    if ap is None:
        ap = _num(p.get("ap"))
    return ap


def _row_tp(p: dict[str, Any]) -> int | None:
    """Saison-Gesamtpunkte einer Zeile: Detail-Key ``punkte`` zuerst, sonst ``tp``."""
    tp = _num(p.get("punkte"))
    if tp is None:
        tp = _num(p.get("tp"))
    return int(tp) if tp is not None else None


def _established(p: dict[str, Any]) -> bool:
    """Fit-Filter: nur etablierte Spieler stützen die Kurve (MW ≥ 3 M und genug
    Saison-Punkte; ohne bekannte Gesamtpunkte ersatzweise Ø-Punkte ≥ 60)."""
    mv, ap, tp = _num(p.get("mv")), _row_ap(p), _row_tp(p)
    if mv is None or mv < FIT_MIN_MV or ap is None or ap <= 0:
        return False
    if tp is not None:
        return tp >= FIT_MIN_TP
    return ap >= FIT_MIN_AP


def fit_fair_curve(players: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Potenz-Regression mv = a · ap^b über die etablierten Spieler der Wolke.

    Least Squares im log-log-Raum (ln mv = ln a + b · ln ap), Summen von Hand —
    kein numpy. Rückgabe ``{"a": float, "b": float, "n": int}`` oder ``None``
    bei weniger als ``MIN_FIT_POINTS`` Stützpunkten bzw. degenerierter Lage
    (alle Stützpunkte auf demselben Ø-Punkte-Wert).
    """
    xs: list[float] = []
    ys: list[float] = []
    for p in players:
        if not _established(p):
            continue
        ap, mv = _row_ap(p), _num(p.get("mv"))
        if ap is None or mv is None or ap <= 0 or mv <= 0:
            continue
        xs.append(math.log(ap))
        ys.append(math.log(mv))
    n = len(xs)
    if n < MIN_FIT_POINTS:
        return None
    sx, sy = sum(xs), sum(ys)
    mx, my = sx / n, sy / n
    # Varianz/Kovarianz statt Roh-Summen: numerisch stabil, und die Entartung
    # „alle auf demselben Ø-Wert" ist als var ≈ 0 sauber erkennbar.
    var = sum((x - mx) ** 2 for x in xs) / n
    if var < 1e-9:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / n
    b = cov / var
    a = math.exp(my - b * mx)
    return {"a": a, "b": b, "n": n}


def _confidence(tp: int | None) -> float:
    """Anteil der Kurven-Abweichung, der bei ``tp`` Gesamtpunkten angerechnet wird."""
    if tp is None:
        return CONFIDENCE_UNKNOWN
    for limit, weight in CONFIDENCE_STEPS:
        if tp < limit:
            return weight
    return 1.0


def fair_value(
    ap: float, tp: int | None, mv: int, curve: dict[str, Any] | None
) -> int | None:
    """Fairer Preis in € — Kurvenwert a · ap^b, konfidenz-gedämpft Richtung Marktwert.

    Die Dämpfung mischt NICHT zwei Preise, sie dämpft die ABWEICHUNG: bei wenigen
    Gesamtpunkten (dünne Datenlage) gilt fair = mv + w · (a·ap^b − mv) mit
    w aus ``CONFIDENCE_STEPS``. ``None`` bei ap ≤ 0, mv ≤ 0 (kein ehrlicher
    Anker) oder fehlender Kurve.
    """
    if curve is None or ap is None or ap <= 0 or mv is None or mv <= 0:
        return None
    a, b = _num(curve.get("a")), _num(curve.get("b"))
    if a is None or b is None or a <= 0:
        return None
    raw = a * ap**b
    adjusted = mv + _confidence(tp) * (raw - mv)
    return max(0, round(adjusted))


def _fmt_eur(v: float) -> str:
    """1_234_567 → „1,23 M€" / 900_000 → „900 T€" — wie theme.money, ohne cli-Import."""
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " M€"
    return f"{v / 1e3:.0f} T€"


def enrich(players: list[dict[str, Any]], curve: dict[str, Any] | None) -> None:
    """Reihen in place um ``fair_eur``/``fair_fmt``/``fair_delta_pct`` ergänzen.

    None-sicher: fehlt Ø-Punkte, Marktwert oder die Kurve, werden alle drei Keys
    auf ``None`` gesetzt (Renderer zeigen „–"). ``fair_delta_pct`` ist die
    gerundete Abweichung (fair − mv) / mv · 100 in Prozentpunkten.
    """
    for p in players:
        ap, tp, mv = _row_ap(p), _row_tp(p), _num(p.get("mv"))
        fair = (
            fair_value(ap, tp, int(mv), curve)
            if ap is not None and mv is not None and mv > 0
            else None
        )
        p["fair_eur"] = fair
        p["fair_fmt"] = _fmt_eur(fair) if fair is not None else None
        p["fair_delta_pct"] = (
            round((fair - mv) / mv * 100) if fair is not None and mv else None
        )
