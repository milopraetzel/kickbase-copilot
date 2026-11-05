"""Spielprofil-Dimensionen — strukturierte Bewertungs-Skalen je Spieler (Emils GO 24.08.).

Sechs Dimensionen nach Kickly-Vorbild, aber aus UNSEREN echten Daten (Spec
docs/superpowers/specs/2026-08-24-cockpit-stufe4-kickly-vollausbau.md §E):

- startelf   LigaInsider-Status (primäre Wahrheit) fusioniert mit der
             Kickbase-Member-Stufe `prob` (Zweitmeinung)
- einsatz    KB-Stufe als Proxy — ehrlich gelabelt, bis echte Einsatzminuten
             je Markt-Spieler verfügbar sind
- punktebasis Ø-Punkte, als Positions-Perzentil gegen die Liga-Wolke, wenn
             die Wolke da ist, sonst absolute Stufen
- upside     Markt-Trend-Signal + Community-Tipp
- risiko     Verletzung/LI-Aus, Rotations-Stufe, fallender Markt — mit Gründen
- effizienz  Ø-Punkte pro Mio € Marktwert

WICHTIG (Decision-Record 24.08.): Die Dimensionen sind bis zur SP1-Kalibrierung
REINE ANZEIGE — sie verändern weder Kauf- noch Elf-Score. Score-Wirkung
(Risiko-Dämpfer, Effizienz-Tiebreaker) kommt erst mit echten Spieltagsdaten
in Leitplanken (knowledge/parameters.OPEN_CALIBRATIONS).

Jede Dimension ist ein ``Dim``: score 0–100 (fürs UI-nativ, Skala wie die
Score-Anzeige seit Stufe 4a), label (Kickly-artige Stufe), tone (Design-Token-
Name: neon/warn/bad/dim — die UI mappt auf Farben, hier keine Hardcodes).
Reine Funktionen ohne API-Calls — voll testbar.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dim:
    key: str
    label_short: str  # Kachel-Titel ("Startelf", "Minuten", …)
    score: int        # 0–100
    label: str        # Stufe ("Hoch", "Rotation", "60–75 Min", …)
    tone: str         # neon | warn | bad | dim
    note: str = ""    # Ein-Satz-Begründung/Quelle


def _clamp(v: float) -> int:
    return max(0, min(100, round(v)))


#: KB-Stufe `prob` → (Einsatz-Label, Score, Ton). Skala-Hypothese 23.08.
#: (1 = sicher … 5 = ausgeschlossen) — Validierung am SP1.
_PROB_EINSATZ: dict[int, tuple[str, int, str]] = {
    1: ("Stammspieler", 90, "neon"),
    2: ("Gesetzt", 75, "neon"),
    3: ("Rotation", 50, "warn"),
    4: ("Joker", 30, "warn"),
    5: ("Kaum Einsatz", 10, "bad"),
}


def startelf_dim(li_status: str | None, kb_prob: int | None) -> Dim:
    """LigaInsider (starter/bench/out/unknown) × KB-Stufe → Startelfchance.

    LI ist die primäre Wahrheit (CLAUDE.md §6); die KB-Stufe verschiebt nur
    innerhalb des LI-Urteils. `out` ist immer 0 („Fällt aus").
    """
    li = (li_status or "unknown").lower()
    if li == "out":
        return Dim("startelf", "Startelf", 0, "Fällt aus", "bad",
                   "LigaInsider: nicht im Kader/verletzt")
    base, label, tone = {
        "starter": (80, "Hoch", "neon"),
        "bench": (25, "Bank", "bad"),
        "unknown": (50, "Offen", "warn"),
    }.get(li, (50, "Offen", "warn"))
    if kb_prob is not None:
        base += {1: 15, 2: 8, 3: 0, 4: -10, 5: -20}.get(int(kb_prob), 0)
    score = _clamp(base)
    if li == "starter" and score >= 85:
        label = "Sehr hoch"
    src = "LigaInsider" + (f" + KB-Stufe {kb_prob}" if kb_prob is not None else "")
    return Dim("startelf", "Startelf", score, label, tone, src)


def einsatz_dim(kb_prob: int | None) -> Dim:
    """Einsatzzeit-PROXY aus der KB-Stufe — ehrlich gelabelt (keine echten Minuten)."""
    if kb_prob is None:
        return Dim("einsatz", "Einsatz", 50, "Unbekannt", "dim",
                   "keine KB-Stufe geliefert")
    label, score, tone = _PROB_EINSATZ.get(int(kb_prob), ("Unbekannt", 50, "dim"))
    return Dim("einsatz", "Einsatz", score, label, tone, f"Proxy aus KB-Stufe {kb_prob}")


def punktebasis_dim(avg_points: float | None,
                    percentile: float | None = None) -> Dim:
    """Ø-Punkte als Basis: Positions-Perzentil (0..1, aus der Liga-Wolke) wenn
    vorhanden, sonst absolute Stufen (Bundesliga-Ø-Skala ~0–120)."""
    if avg_points is None or avg_points <= 0:
        return Dim("punkte", "Punktebasis", 0, "Keine Daten", "dim",
                   "keine Ø-Punkte (Saison/Vorsaison)")
    if percentile is not None:
        score = _clamp(percentile * 100)
        note = f"Ø {avg_points:g} · Top {max(1, round((1 - percentile) * 100))} % der Position"
    else:
        score = _clamp(avg_points / 1.2)  # 120 Ø ≈ 100
        note = f"Ø {avg_points:g} Punkte (absolute Skala)"
    label = "Hoch" if score >= 70 else ("Solide" if score >= 45 else "Dünn")
    tone = "neon" if score >= 70 else ("warn" if score >= 45 else "bad")
    return Dim("punkte", "Punktebasis", score, label, tone, note)


def upside_dim(trend_signal: float | None, tip_weight: float | None) -> Dim:
    """Markt-Trend-Signal (0..1, intel/signals.trend_signal) + Community-Tipp.

    Trend trägt die Basis (0.5 = neutral), ein aktiver Tipp hebt um bis zu 20
    Punkte (Gewicht 0..1) — Nachfrage-Proxy wie der Community-Boost der Engine.
    """
    base = 50.0 if trend_signal is None else float(trend_signal) * 100
    boost = min(20.0, max(0.0, (tip_weight or 0.0) * 20.0))
    score = _clamp(base + boost)
    label = "Hoch" if score >= 70 else ("Mittel" if score >= 45 else "Niedrig")
    tone = "neon" if score >= 70 else ("warn" if score >= 45 else "bad")
    parts = []
    if trend_signal is not None:
        parts.append(f"Markt-Trend {round(float(trend_signal) * 100)}")
    if boost:
        parts.append(f"Tipp +{round(boost)}")
    return Dim("upside", "Upside", score, label, tone, " · ".join(parts) or "neutral")


def risiko_dim(li_status: str | None, injury_reason: str | None,
               kb_prob: int | None, mv24: int | None,
               market_value: int | None) -> Dim:
    """Risiko-Stufe mit Gründen: LI-Aus/Bank, Rotations-Stufe, fallender Markt.

    Score = RISIKO-Höhe 0–100 (100 = maximal riskant); Ton invers (bad = hoch).
    """
    pts, reasons = 0, []
    li = (li_status or "unknown").lower()
    if li == "out":
        pts += 60
        reasons.append(f"Ausfall ({injury_reason})" if injury_reason else "Ausfall laut LI")
    elif li == "bench":
        pts += 30
        reasons.append("kein Stammplatz laut LI")
    elif li == "unknown":
        pts += 15
        reasons.append("keine LI-Startelf-Quelle")
    if kb_prob is not None and int(kb_prob) >= 4:
        pts += 25
        reasons.append(f"KB-Stufe {kb_prob} (Rotation/Bank)")
    if mv24 is not None and market_value:
        daily = mv24 / market_value
        if daily <= -0.01:
            pts += 20
            reasons.append("Marktwert fällt deutlich (24 h)")
        elif daily < 0:
            pts += 8
            reasons.append("Marktwert-Trend leicht negativ")
    score = _clamp(pts)
    label = "Hoch" if score >= 60 else ("Mittel" if score >= 30 else "Niedrig")
    tone = "bad" if score >= 60 else ("warn" if score >= 30 else "neon")
    return Dim("risiko", "Risiko", score, label, tone,
               "; ".join(reasons) or "keine Warnsignale")


def effizienz_dim(avg_points: float | None, market_value: int | None) -> Dim:
    """Punkte pro Mio € (Kickly-Kennzahl): Ø-Punkte / (MW in Mio).

    Stufen empirisch grob: ≥ 12 sehr gut, ≥ 7 gut, ≥ 4 fair, darunter teuer.
    Score = min(100, Wert × 5) — reine Anzeige-Skalierung.
    """
    if not avg_points or avg_points <= 0 or not market_value or market_value <= 0:
        return Dim("effizienz", "Effizienz", 0, "Keine Daten", "dim",
                   "braucht Ø-Punkte und Marktwert")
    per_mio = avg_points / (market_value / 1_000_000)
    score = _clamp(per_mio * 5)
    label = ("Sehr gut" if per_mio >= 12 else
             "Gut" if per_mio >= 7 else
             "Fair" if per_mio >= 4 else "Teuer")
    tone = ("neon" if per_mio >= 7 else "warn" if per_mio >= 4 else "bad")
    return Dim("effizienz", "Effizienz", score, f"{per_mio:.1f} Pkt/Mio", tone,
               f"{label} — Ø {avg_points:g} Punkte auf {market_value / 1e6:.1f} M €")


def player_dimensions(*, li_status: str | None, injury_reason: str | None,
                      kb_prob: int | None, avg_points: float | None,
                      percentile: float | None, trend_signal: float | None,
                      tip_weight: float | None, mv24: int | None,
                      market_value: int | None) -> list[Dim]:
    """Alle sechs Dimensionen eines Spielers — Reihenfolge wie die Scout-Karte."""
    return [
        startelf_dim(li_status, kb_prob),
        einsatz_dim(kb_prob),
        punktebasis_dim(avg_points, percentile),
        upside_dim(trend_signal, tip_weight),
        risiko_dim(li_status, injury_reason, kb_prob, mv24, market_value),
        effizienz_dim(avg_points, market_value),
    ]


def empfehlung(kauf_score: float | None, risiko: Dim,
               buy_threshold: float = 0.55) -> tuple[str, str]:
    """Empfehlungs-Chip der Scout-Karte: (Label, Ton) aus Kauf-Score + Risiko.

    Kaufen = Score über der Engine-Schwelle UND Risiko nicht hoch; Vorsicht =
    über der Schwelle mit hohem Risiko; sonst Beobachten/Meiden. REINE
    Anzeige-Verdichtung — die Kauf-Entscheidung trifft weiter die Engine.
    """
    s = float(kauf_score or 0.0)
    if s >= buy_threshold and risiko.score < 60:
        return ("Kaufen", "neon")
    if s >= buy_threshold:
        return ("Vorsicht", "warn")
    if s >= 0.45 and risiko.score < 60:
        return ("Beobachten", "warn")
    return ("Meiden", "dim")
