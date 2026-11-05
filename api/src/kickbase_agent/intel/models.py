"""Pydantic models for signals and candidates."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Signal(BaseModel):
    """Single scoring signal with human-readable reasoning."""

    model_config = ConfigDict(frozen=True)

    name: str                            # e.g. "form", "matchup"
    score: float = Field(ge=0.0, le=1.0) # normalized 0-1
    weight: float = Field(gt=0.0)        # relative importance
    reason: str                          # one-sentence why


class ExplosionCandidate(BaseModel):
    """Player ranked by rule engine for market-value explosion potential."""

    model_config = ConfigDict(frozen=True)

    player_id: str
    name: str
    team_id: str
    position: int
    current_market_value: int
    total_score: float = Field(ge=0.0, le=1.0)  # gewichtetes Mittel ALLER Signale (Kaufpfad)
    # Aufstellungs-Score: gleiche Signale OHNE den Marktwert-Trend — MW-Momentum
    # erzeugt keine Spieltagspunkte. Fehlt er beim Bau (Alt-Aufrufer), fällt er
    # auf total_score zurück (siehe Validator).
    lineup_score: float = Field(default=0.5, ge=0.0, le=1.0)
    signals: list[Signal]
    # LigaInsider-Ausfall, gesetzt von scouts.ligainsider.availability bei Status OUT:
    # severity ("long" | "short" | "unknown") steuert den Verkaufspfad — Kurzzeit
    # wird gehalten (Comeback-Anstieg), nur Langzeit geht sofort raus.
    injury_reason: str = ""
    injury_severity: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _lineup_score_defaults_to_total(cls, data: Any) -> Any:
        """Ohne expliziten lineup_score gilt: Aufstellungs-Ranking == Gesamt-Ranking."""
        if isinstance(data, dict) and data.get("lineup_score") is None:
            data = {**data, "lineup_score": data.get("total_score")}
        return data


class PlayerStory(BaseModel):
    """Claude-generated narrative for a player."""

    model_config = ConfigDict(frozen=True)

    player_id: str
    headline: str    # 1-line hook ("Musiala vor Ausbruch?")
    narrative: str   # 2-3 sentences
    confidence: str  # "hoch" | "mittel" | "niedrig"
