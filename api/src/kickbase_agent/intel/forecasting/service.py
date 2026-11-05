"""Punktebasis mit expliziter Datenlücke bei Einsatz- und Startwahrscheinlichkeiten."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from kickbase_agent.intel.forecasting.dataset import SETTLEMENT, clean_rows, utc
from kickbase_agent.intel.forecasting.features import baseline, feature_vector, recent
from kickbase_agent.intel.forecasting.registry import DEFAULT_REGISTRY_DIR as DEFAULT_REGISTRY_DIR
from kickbase_agent.intel.forecasting.registry import artifact_valid
from kickbase_agent.intel.forecasting.registry import load_active as load_active
from kickbase_agent.intel.forecasting.training import predict


def forecast_player(
    player: Any,
    performances: list[Any],
    *,
    as_of: datetime,
    artifact: dict[str, Any] | None = None,
    settlement: timedelta = SETTLEMENT,
) -> dict[str, Any]:
    pid = str(player.get("id", "") if isinstance(player, dict) else getattr(player, "id", ""))
    rows, excluded = clean_rows(pid, performances, as_of=as_of, settlement=settlement)
    history = recent(rows, as_of)
    reasons = [
        "Punkte und Minuten bedingt auf einen Einsatz; Einsatzwahrscheinlichkeit unbekannt.",
        "Vier Spieltage = konstante Formprojektion, kein separat trainiertes Horizontmodell.",
    ]
    if excluded:
        reasons.append(f"Unklare, künftige oder noch nicht abgeschlossene Zeilen: {excluded}")
    expected = baseline(history, "points") if history else 0.0
    minutes = baseline(history, "minutes") if history else None
    version = "baseline-recency-v1"
    model_cutoff = utc(artifact.get("as_of")) if artifact else None
    model_created = utc(artifact.get("trained_at")) if artifact else None
    if (artifact is not None and artifact_valid(artifact) and len(history) >= 3
            and model_cutoff is not None and model_cutoff <= as_of
            and model_created is not None and model_created <= as_of):
        features = feature_vector(history, as_of)
        expected = predict(artifact["models"]["points"], features)
        minutes = predict(artifact["models"]["minutes"], features)
        version = artifact["model_version"]
        reasons.append("Evaluiertes Einsatzmodell; keine trainierte Startwahrscheinlichkeit.")
    elif artifact is not None:
        reasons.append("Modell nicht qualifiziert oder zu wenig Historie; dokumentierte Basis.")
    if not history:
        reasons.append("Keine jüngere belegte Leistung; Null ist eine konservative Planungsbasis.")
    conditional_points, conditional_minutes = expected, minutes
    fitness = player.get("fitness_status") if isinstance(player, dict) else getattr(
        player, "fitness_status", None,
    )
    probability = player.get("lineup_prob") if isinstance(player, dict) else getattr(
        player, "lineup_prob", None,
    )
    restricted = (type(fitness) is int and fitness > 0) or probability in (4, 5)
    if restricted:
        expected, minutes = 0.0, 0.0 if history else None
        reasons.append("Bank-/Fitnessrisiko: Nullbasis als Heuristik, keine gelernte Chance.")
    return {
        "expected_points": expected, "horizon_points": expected * 4,
        "expected_minutes": minutes, "play_probability": None, "start_probability": None,
        "model_version": version, "confidence": "low" if history else "insufficient",
        "reasons": reasons, "points_basis": "conditional_on_appearance",
        "horizon_basis": "constant_form_projection", "forecast_available": bool(history),
        "observations": len(history),
        "conditional_expected_points": conditional_points,
        "conditional_expected_minutes": conditional_minutes,
        "availability_adjustment": {
            "factor": 0.0 if restricted else 1.0,
            "basis": "conservative_role_heuristic" if restricted else "appearance_assumption",
        },
    }
