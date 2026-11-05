"""Kleine Ridge-Modelle, datierte Vergleiche und unveränderte letzte Testperiode."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from kickbase_agent.intel.forecasting.dataset import SETTLEMENT, Observation, load_seed
from kickbase_agent.intel.forecasting.features import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    baseline,
    feature_vector,
    recent,
)


@dataclass(frozen=True)
class Example:
    outcome: Observation
    features: list[float]
    baseline_points: float
    baseline_minutes: float
    publication_verified: bool
    feature_seed: str | None = None
    feature_as_of: datetime | None = None


def examples_from(observations: list[Observation]) -> list[Example]:
    histories: dict[str, list[Observation]] = defaultdict(list)
    examples: list[Example] = []
    for row in sorted(observations, key=lambda r: (r.at, r.player_id)):
        history = recent(
            [r for r in histories[row.player_id] if r.at + SETTLEMENT <= row.at], row.at,
        )
        if len(history) >= 3:
            examples.append(Example(
                row, feature_vector(history, row.at), baseline(history, "points"),
                baseline(history, "minutes"),
                all(r.captured_at is not None and r.captured_at <= row.at for r in history),
            ))
        histories[row.player_id].append(row)
    return examples


def split_examples(examples: list[Example]) -> tuple[dict[str, list[Example]], dict[str, Any]]:
    group_starts: dict[str, datetime] = {}
    for item in examples:
        group = item.outcome.group
        group_starts[group] = min(group_starts.get(group, item.outcome.at), item.outcome.at)
    groups = sorted(group_starts, key=lambda g: group_starts[g])
    if len(groups) < 12:
        raise ValueError("Zu wenige belegte Zeitgruppen für Training, Validierung und Holdout.")
    train_end, validation_end = int(len(groups) * .7), int(len(groups) * .85)
    selected = {
        "train": set(groups[:train_end]),
        "validation": set(groups[train_end:validation_end]), "test": set(groups[validation_end:]),
    }
    partitions = {
        name: [e for e in examples if e.outcome.group in chosen]
        for name, chosen in selected.items()
    }
    # Labels müssen vor der nächsten Auswertungsperiode abgeschlossen sein.
    # Ganze Gruppen entfernen, falls ein verlegtes Spiel den Stichtag überlappt.
    for name, following in (("validation", "test"), ("train", "validation")):
        cutoff = min(e.outcome.at for e in partitions[following])
        unsafe = {e.outcome.group for e in partitions[name] if e.outcome.at + SETTLEMENT >= cutoff}
        partitions[name] = [e for e in partitions[name] if e.outcome.group not in unsafe]
    if any(not p for p in partitions.values()):
        raise ValueError("Zeitgruppen nach Finalitätsabstand leer; kein Modell trainiert.")
    split: dict[str, Any] = {"method": "chronological_grouped_70_15_15", "gap_hours": 72}
    for name, rows in partitions.items():
        split[f"{name}_groups"] = sorted({e.outcome.group for e in rows})
        split[f"{name}_start"] = min(e.outcome.at for e in rows).isoformat()
        split[f"{name}_end"] = max(e.outcome.at for e in rows).isoformat()
        split[f"{name}_rows"] = len(rows)
    return partitions, split


def fit_ridge(rows: list[Example], target: str, alpha: float) -> dict[str, Any]:
    x = np.asarray([e.features for e in rows], dtype=float)
    y = np.asarray([getattr(e.outcome, target) for e in rows], dtype=float)
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    normalized = (x - center) / scale
    y_center = float(y.mean())
    coefficients = np.linalg.solve(
        normalized.T @ normalized + alpha * np.eye(x.shape[1]),
        normalized.T @ (y - y_center),
    )
    return {
        "kind": "ridge", "alpha": alpha, "center": center.tolist(), "scale": scale.tolist(),
        "coefficients": coefficients.tolist(), "intercept": y_center,
        "output_bounds": [float(y.min()), float(y.max())],
    }


def predict(model: dict[str, Any], features: list[float]) -> float:
    value = float(np.dot(
        (np.asarray(features) - np.asarray(model["center"])) / np.asarray(model["scale"]),
        model["coefficients"],
    ) + model["intercept"])
    return float(np.clip(value, *model["output_bounds"]))


def metrics(actual: list[float], predicted: list[float]) -> dict[str, float]:
    error = np.asarray(actual) - np.asarray(predicted)
    return {"mae": float(np.abs(error).mean()), "rmse": float(np.sqrt((error ** 2).mean()))}


def comparison(rows: list[Example], models: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"rows": len(rows), "groups": len({e.outcome.group for e in rows})}
    for target, model in models.items():
        actual = [getattr(e.outcome, target) for e in rows]
        forecast = [predict(model, e.features) for e in rows]
        base = [getattr(e, f"baseline_{target}") for e in rows]
        grouped: dict[str, list[float]] = defaultdict(list)
        for item, y, pred, naive in zip(rows, actual, forecast, base, strict=True):
            grouped[item.outcome.group].append(abs(y - pred) - abs(y - naive))
        deltas = np.asarray([np.mean(v) for v in grouped.values()])
        rng = np.random.default_rng(20260913)
        bootstrap = rng.choice(deltas, size=(1000, len(deltas)), replace=True).mean(axis=1)
        result[target] = {
            "candidate": metrics(actual, forecast), "baseline": metrics(actual, base),
            "group_mae_delta_ci95": np.quantile(bootstrap, [.025, .975]).tolist(),
        }
    return result


def promotion_gate(evaluation: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    for period in ("validation", "test"):
        result = evaluation[period]
        # Prozessminimum für mehrere Vergleichswochen, keine universelle ML-Grenze.
        if result["groups"] < 6:
            reasons.append(f"{period}_too_few_groups")
        for target in ("points", "minutes"):
            scores = result[target]
            if scores["candidate"]["mae"] >= scores["baseline"]["mae"]:
                reasons.append(f"{period}_{target}_no_improvement")
        if result["points"]["group_mae_delta_ci95"][1] >= 0:
            reasons.append(f"{period}_points_improvement_uncertain")
    if evaluation["historical_publication_verified_fraction"] < 1.0:
        reasons.append("historical_publication_unverified")
    return {"passed": not reasons, "reasons": reasons, "minimum_evaluation_groups": 6}


def evaluate_models(partitions: dict[str, list[Example]], models: dict[str, Any]) -> dict[str, Any]:
    evaluation: dict[str, Any] = {
        name: comparison(partitions[name], models) for name in ("validation", "test")
    }
    evaluated = partitions["validation"] + partitions["test"]
    evaluation["historical_publication_verified_fraction"] = (
        sum(e.publication_verified for e in evaluated) / len(evaluated)
    )
    evaluation["promotion_gate"] = promotion_gate(evaluation)
    return evaluation


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def artifact_version(artifact: dict[str, Any]) -> str:
    body = {k: v for k, v in artifact.items() if k != "model_version"}
    return "ridge-" + hashlib.sha256(canonical(body)).hexdigest()[:20]


def train_candidate(
    source: str | Path, *, registry_dir: str | Path, as_of: datetime,
    historical_seeds: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    from kickbase_agent.intel.forecasting.prospective import prospective_partitions

    observations, audit = load_seed(source, as_of=as_of)
    partitions, split, coverage = prospective_partitions(
        observations, historical_seeds=historical_seeds or [], as_of=as_of,
    )
    if partitions is None:
        partitions, split = split_examples(examples_from(observations))
    models = {}
    for target in ("points", "minutes"):
        trials = [fit_ridge(partitions["train"], target, alpha) for alpha in (1.0, 10.0, 100.0)]
        models[target] = min(trials, key=lambda model: metrics(
            [getattr(e.outcome, target) for e in partitions["validation"]],
            [predict(model, e.features) for e in partitions["validation"]],
        )["mae"])
    artifact = {
        "schema_version": 1, "feature_version": FEATURE_VERSION, "feature_names": FEATURE_NAMES,
        "trained_at": datetime.now(UTC).isoformat(), "as_of": as_of.isoformat(),
        "source_sha256": audit["source_sha256"], "audit": audit, "split": split,
        "training_protocol": "ridge-snapshot-join-v2", "prospective_coverage": coverage,
        "label_scope": "observed_appearances_only", "min_history": 3, "models": models,
        "evaluation": evaluate_models(partitions, models),
        "untrained_targets": {
            "start": "Keine historischen Startlabels.",
            "play": "Keine verlässlichen Nichteinsatzlabels; Nullwerte nicht imputiert.",
            "market_value_1_3_7d": "Separates Tagesmodell geprüft, derzeit nicht aktiviert; "
            "kein Bestandteil dieses Punkte-/Minutenmodells.",
            "points_4_matchdays": "Nur konstante Projektion des nächsten Einsatzes.",
        },
    }
    artifact["model_version"] = artifact_version(artifact)
    directory = Path(registry_dir) / "candidates"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{artifact['model_version']}.json"
    path.write_bytes(canonical(artifact) + b"\n")
    predictions = directory / f"{artifact['model_version']}-holdout.jsonl"
    with predictions.open("w") as output:
        for item in partitions["test"]:
            result: dict[str, Any] = {
                "player_id": item.outcome.player_id, "group": item.outcome.group,
                "at": item.outcome.at.isoformat(),
                "feature_seed": item.feature_seed,
                "feature_as_of": item.feature_as_of.isoformat() if item.feature_as_of else None,
            }
            for target, model in models.items():
                result[target] = {
                    "actual": getattr(item.outcome, target),
                    "candidate": predict(model, item.features),
                    "baseline": getattr(item, f"baseline_{target}"),
                }
            output.write(json.dumps(result, ensure_ascii=False) + "\n")
    return artifact


def evaluate_candidate(source: str | Path, artifact: dict[str, Any]) -> dict[str, Any]:
    """Unveränderte Gewichte erneut auswerten; kein Fit und keine Holdout-Auswahl."""
    observations, audit = load_seed(source, as_of=datetime.fromisoformat(artifact["as_of"]))
    if audit["source_sha256"] != artifact["source_sha256"]:
        raise ValueError("Datenfingerabdruck passt nicht zum eingefrorenen Modellvergleich.")
    from kickbase_agent.intel.forecasting.prospective import prospective_partitions

    paths = []
    for snapshot in artifact.get("prospective_coverage", {}).get("sources", []):
        path = Path(snapshot["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != snapshot["sha256"]:
            raise ValueError("Snapshot-Fingerabdruck stimmt nicht mit dem eingefrorenen Modell.")
        paths.append(path)
    partitions, split, _ = prospective_partitions(
        observations, historical_seeds=paths, as_of=datetime.fromisoformat(artifact["as_of"]),
    )
    if partitions is None:
        partitions, split = split_examples(examples_from(observations))
    if split != artifact["split"]:
        raise ValueError("Zeitliche Vergleichsperioden stimmen nicht überein.")
    return evaluate_models(partitions, artifact["models"])
