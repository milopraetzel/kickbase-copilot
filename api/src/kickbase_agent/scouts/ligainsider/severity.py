"""Verletzungs-Schwere → Verkaufs-Empfehlung (die Profi-Regel).

Kickbase-Meta bei Verletzungen (Ligabase-Benchmark + Community-Konsens):
- LANGZEIT (Kreuzband, Bruch, OP, Monate): SOFORT verkaufen — der MW fällt über
  Wochen weiter, Kapital + Kaderplatz sind tot. Geschwindigkeit zählt: wer vor dem
  22-Uhr-Update verkauft, bekommt noch den Vor-Verletzungs-Preis (Sofortverkauf
  zahlt den AKTUELLEN MW).
- KURZZEIT (angeschlagen, fraglich, 1–2 Wochen): HALTEN — der kleine MW-Dip ist
  eingepreist, „Rückkehrer ziehen beim Comeback an" (Ligabase). Panikverkauf am
  Tiefpunkt ist der klassische Anfängerfehler.
- UNKLAR: beobachten, nächste LI-/News-Lage abwarten.

Die Einstufung liest den LigaInsider-Grund-Text (z. B. „Kreuzbandriss",
„Muskelverletzung Oberschenkel", „angeschlagen").
"""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    LONG = "long"        # Monate → sofort verkaufen
    SHORT = "short"      # Tage/1-2 Wochen → halten (Comeback-Anstieg)
    UNKNOWN = "unknown"  # unklar → beobachten


_LONG_MARKERS = (
    "kreuzband", "riss", "bruch", "fraktur", "operation", " op", "op-",
    "syndesmose", "monat", "aufbautraining", "reha", "langzeit",
    "stoffwechsel", "kreuzbandriss",
)
_SHORT_MARKERS = (
    "angeschlagen", "fraglich", "erkältung", "erkrankung", "krank", "infekt",
    "prellung", "belastungssteuerung", "trainingsrückstand", "muskulär",
    "wadenprobleme", "leichte", "tage",
)


def injury_severity(reason: str) -> Severity:
    text = f" {reason.lower()} "
    if any(m in text for m in _LONG_MARKERS):
        return Severity.LONG
    if any(m in text for m in _SHORT_MARKERS):
        return Severity.SHORT
    return Severity.UNKNOWN


def sell_advice(reason: str, severity: Severity | None = None) -> str:
    """Handlungszeile für Kader-Alarm/Recherche — die Profi-Regel in einem Satz.

    `severity` ist ein bereits vorliegendes Urteil (TypeSafe); ohne gilt die Wortsuche."""
    sev = severity or injury_severity(reason)
    if sev is Severity.LONG:
        return "→ SOFORT verkaufen (Langzeit: MW fällt weiter; vor 22:00 = alter Preis)"
    if sev is Severity.SHORT:
        return "→ HALTEN (Kurzzeit: Dip ist eingepreist, Comeback-Anstieg mitnehmen)"
    return "→ beobachten (Dauer unklar — nächste LI-/News-Lage abwarten)"
