"""Datenmodelle des LigaInsider-Scouts (roh geparst → aufgelöste Verfügbarkeit)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    STARTER = "starter"    # fit + in voraussichtlicher Startelf → xPoints ×1.0
    BENCH = "bench"        # fit, aber nicht in der Startelf → xPoints ×0.4
    OUT = "out"            # verletzt/gesperrt/Aufbautraining → xPoints ×0.0 (Hard-Veto)
    UNKNOWN = "unknown"    # keine (sicheren) LI-Daten → ×1.0, kein Veto


# xPoints-Multiplikatoren je Status (Regeln 1+2 aus Emils Spec).
MULTIPLIER: dict[Status, float] = {
    Status.STARTER: 1.0,
    Status.BENCH: 0.4,
    Status.OUT: 0.0,
    Status.UNKNOWN: 1.0,
}


@dataclass(frozen=True)
class LineupEntry:
    """Ein Spieler aus der voraussichtlichen Startelf-Grafik eines Teams."""

    team_tid: str
    player_name: str      # LI-Schreibweise, z.B. "Minjae"


@dataclass(frozen=True)
class InjuryEntry:
    """Ein Ausfall (verletzt/gesperrt) von der zentralen Ausfall-Seite."""

    team_tid: str
    player_name: str      # LI-Schreibweise, z.B. "Assan Ouédraogo"
    reason: str           # "Kreuzbandriss", "Aufbautraining", "Gesperrt", …


@dataclass(frozen=True)
class Availability:
    """Aufgelöste Verfügbarkeit eines Kickbase-Spielers (nach Matching)."""

    player_id: str
    status: Status
    multiplier: float
    reason: str           # menschenlesbar fürs Briefing
    confidence: float     # Match-Güte 0..1


@dataclass(frozen=True)
class Quarantine:
    """Grenzfall: LI-Ausfall nahe an einem Kader-Spieler, aber < Schwelle.

    Wird NICHT genullt (Sicherheitsnetz), sondern per Discord gemeldet, damit
    Emil die Kickbase-ID zuweisen kann.
    """

    player_id: str
    kb_name: str
    li_name: str
    li_reason: str
    confidence: float
