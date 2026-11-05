"""Tages-Marktwerte: zeitlich getrennte Modellkandidaten und ehrliche Trendbasis.

Die Historie benennt Kalendertage, keine Intraday-Veröffentlichungszeiten. Deshalb
bleibt der aktuelle Tag ausgeschlossen. Horizonte zählen ab dem letzten belegten
abgeschlossenen Tag; fehlende Tage und Zielwerte werden niemals aufgefüllt.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

HORIZONS = (1, 3, 7)
FEATURES = ("return_1d", "return_3d", "return_7d", "daily_volatility_7d", "log_market_value")


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return None


def _cutoff(as_of: datetime) -> date:
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("as_of braucht eine ausdrückliche Zeitzone.")
    return as_of.astimezone(UTC).date()


def daily_history(history: list[Any], as_of: datetime) -> tuple[dict[date, int], dict[str, int]]:
    cutoff = _cutoff(as_of)
    values: dict[date, int] = {}
    conflicts: set[date] = set()
    excluded = {"invalid": 0, "current_or_future_day": 0, "conflicting_days": 0}
    for raw in history:
        row = raw.model_dump() if hasattr(raw, "model_dump") else raw
        if not isinstance(row, dict):
            row = {"recorded_at": getattr(raw, "recorded_at", None),
                   "market_value": getattr(raw, "market_value", None)}
        day = _day(row.get("recorded_at", row.get("date")))
        value = row.get("market_value", row.get("value"))
        if day is None or type(value) is not int or value <= 0:
            excluded["invalid"] += 1
            continue
        if day >= cutoff:
            excluded["current_or_future_day"] += 1
            continue
        if day in values and values[day] != value:
            conflicts.add(day)
        values[day] = value
    for day in conflicts:
        del values[day]
    excluded["conflicting_days"] = len(conflicts)
    return dict(sorted(values.items())), excluded


def _features(values: dict[date, int], anchor: date) -> list[float] | None:
    days = [anchor - timedelta(days=n) for n in range(8)]
    if any(day not in values for day in days):
        return None
    current = values[anchor]
    return [current / values[days[h]] - 1 for h in HORIZONS] + [
        float(np.std([values[days[i]] / values[days[i + 1]] - 1 for i in range(7)])),
        float(np.log(current)),
    ]


def forecast_market_value(history: list[Any], as_of: datetime) -> dict[str, Any]:
    """Dokumentierte Trendbasis; lädt und aktiviert keinen Modellkandidaten."""
    values, excluded = daily_history(history, as_of)
    result: dict[str, Any] = {
        "method": "last_observed_daily_change", "status": "insufficient",
        "as_of": as_of.isoformat(), "anchor_date": None, "market_value_eur": None,
        "age_days": None, "horizons": {}, "excluded": excluded,
        "limitations": ["Tageswerte ohne belegte Intraday-Veröffentlichungszeit.",
                        "Horizonte ab letztem abgeschlossenen Beobachtungstag.",
                        "Historische Fehlerbereiche sind keine garantierten Intervalle."],
    }
    if not values:
        return result
    anchor = max(values)
    current = values[anchor]
    prior = values.get(anchor - timedelta(days=1))
    result.update(anchor_date=anchor.isoformat(), market_value_eur=current,
                  age_days=(_cutoff(as_of) - anchor).days)
    if prior is None:
        return result
    result["status"] = "baseline" if result["age_days"] == 1 else "stale_baseline"
    for h in HORIZONS:
        delta = max(-current, h * (current - prior))
        residuals = []
        for origin, value in values.items():
            before, after = origin - timedelta(days=1), origin + timedelta(days=h)
            if before in values and after in values:
                naive = max(-value, h * (value - values[before]))
                residuals.append(values[after] - value - naive)
        interval = None
        if len(residuals) >= 20:
            low, high = np.quantile(residuals, [.1, .9])
            interval = [int(max(-current, delta + low)), int(max(-current, delta + high))]
        result["horizons"][str(h)] = {
            "days": h, "target_date": (anchor + timedelta(days=h)).isoformat(),
            "change_eur": delta, "change_pct": 100 * delta / current,
            "projected_market_value_eur": current + delta,
            "uncertainty": {"kind": "historical_residual_p10_p90", "change_eur": interval,
                            "sample_size": len(residuals), "calibrated": False},
        }
    return result


@dataclass(frozen=True)
class MarketExample:
    player_id: str
    day: date
    market_value: int
    features: list[float]
    changes: dict[int, int]
    last_daily_change: int


def market_examples(histories: dict[str, dict[date, int]]) -> list[MarketExample]:
    result = []
    for pid, values in histories.items():
        for day, value in sorted(values.items()):
            features = _features(values, day)
            if features is None:
                continue
            changes = {h: values[day + timedelta(days=h)] - value for h in HORIZONS
                       if day + timedelta(days=h) in values}
            if changes:
                result.append(MarketExample(pid, day, value, features, changes,
                                            value - values[day - timedelta(days=1)]))
    return result


def split_market_examples(examples: list[MarketExample]) -> tuple[dict[str, list], dict]:
    days = sorted({e.day for e in examples})
    if len(days) < 40:
        raise ValueError("Mindestens 40 Ursprungstage für getrennte Tagesperioden erforderlich.")
    train_end, validation_end = int(len(days) * .6), int(len(days) * .8)
    validation_start, test_start = days[train_end], days[validation_end]
    partitions = {
        "train": [e for e in examples if e.day + timedelta(days=7) < validation_start],
        "validation": [e for e in examples if validation_start <= e.day
                       and e.day + timedelta(days=7) < test_start],
        "test": [e for e in examples if e.day >= test_start],
    }
    if any(not rows for rows in partitions.values()):
        raise ValueError("Tagesperioden nach Sieben-Tage-Purge leer.")
    split = {"method": "whole_origin_calendar_day_60_20_20", "purge_days": 7}
    for name, rows in partitions.items():
        split[name] = {"rows": len(rows), "days": len({e.day for e in rows}),
                       "start": min(e.day for e in rows).isoformat(),
                       "end": max(e.day for e in rows).isoformat()}
    return partitions, split


def _fit(rows: list[MarketExample], horizon: int, alpha: float) -> dict:
    x = np.asarray([e.features for e in rows])
    y = np.asarray([e.changes[horizon] / e.market_value for e in rows])
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-12] = 1
    z = (x - center) / scale
    intercept = float(y.mean())
    coefficients = np.linalg.solve(z.T @ z + alpha * np.eye(len(FEATURES)),
                                    z.T @ (y - intercept))
    return {"alpha": alpha, "center": center.tolist(), "scale": scale.tolist(),
            "coefficients": coefficients.tolist(), "intercept": intercept,
            "return_bounds": [max(-1.0, float(y.min())), float(y.max())]}


def _predict(model: dict, rows: list[MarketExample]) -> np.ndarray:
    x = np.asarray([e.features for e in rows])
    relative = ((x - model["center"]) / model["scale"]) @ np.asarray(model["coefficients"])
    relative = np.clip(relative + model["intercept"], *model["return_bounds"])
    return relative * np.asarray([e.market_value for e in rows])


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    errors = actual - predicted
    return {"mae_eur": float(np.mean(abs(errors))),
            "rmse_eur": float(np.sqrt(np.mean(errors ** 2)))}


def _evaluate(model: dict, rows: list[MarketExample], horizon: int) -> dict:
    actual = np.asarray([e.changes[horizon] for e in rows])
    prediction = _predict(model, rows)
    trend = np.asarray([max(-e.market_value, horizon * e.last_daily_change) for e in rows])
    forecasts = {"ridge": prediction, "flat": np.zeros(len(rows)), "last_trend": trend}
    result: dict[str, Any] = {
        "rows": len(rows), "days": len({e.day for e in rows}),
        "metrics": {key: _metrics(actual, pred) for key, pred in forecasts.items()},
    }
    result["day_block_mae_delta_ci95"] = {}
    for key in ("flat", "last_trend"):
        deltas = np.abs(actual - prediction) - np.abs(actual - forecasts[key])
        grouped = [float(np.mean([delta for e, delta in zip(rows, deltas, strict=True)
                                  if e.day == day])) for day in sorted({e.day for e in rows})]
        rng = np.random.default_rng(20260913)
        bootstrap = rng.choice(grouped, (1000, len(grouped)), replace=True).mean(axis=1)
        result["day_block_mae_delta_ci95"][key] = np.quantile(bootstrap, [.025, .975]).tolist()
    moving = np.asarray([e.features[3] > 0 for e in rows])
    result["moving_history_subset"] = {
        "rows": int(moving.sum()),
        "metrics": {key: _metrics(actual[moving], pred[moving])
                    for key, pred in forecasts.items()} if moving.any() else {},
    }
    return result


def train_market_candidate(source: str | Path, *, as_of: datetime,
                           output_dir: str | Path) -> dict:
    source = Path(source)
    raw = source.read_bytes()
    seed = json.loads(raw)
    histories: dict[str, dict[date, int]] = {}
    excluded: dict[str, int] = {}
    for pid, entry in seed.get("players", {}).items():
        values, audit = daily_history(entry.get("history", []), as_of)
        if values:
            histories[str(pid)] = values
        for key, count in audit.items():
            excluded[key] = excluded.get(key, 0) + count
    examples = market_examples(histories)
    partitions, split = split_market_examples(examples)
    models, evaluation = {}, {}
    reasons = ["historical_daily_publication_time_unverified", "candidate_only_no_activation"]
    for h in HORIZONS:
        parts = {name: [e for e in rows if h in e.changes] for name, rows in partitions.items()}
        if len(parts["train"]) < 200 or any(len(parts[k]) < 100 for k in ("validation", "test")):
            raise ValueError(f"Zu wenige belegte Zielwerte für {h} Tage.")
        candidates = [_fit(parts["train"], h, alpha) for alpha in (1.0, 10.0, 100.0)]
        actual = np.asarray([e.changes[h] for e in parts["validation"]])
        model = min(candidates, key=lambda m: _metrics(actual, _predict(m, parts["validation"]))[
            "mae_eur"])
        models[str(h)] = model
        evaluation[str(h)] = {name: _evaluate(model, parts[name], h)
                              for name in ("validation", "test")}
        for name, metrics in evaluation[str(h)].items():
            if metrics["days"] < 6:
                reasons.append(f"{h}d_{name}_too_few_days")
            for base in ("flat", "last_trend"):
                if metrics["day_block_mae_delta_ci95"][base][1] >= 0:
                    reasons.append(f"{h}d_{name}_improvement_over_{base}_uncertain")
    artifact = {
        "schema_version": 1, "kind": "market_value_ridge_candidate", "status": "candidate",
        "as_of": as_of.isoformat(), "feature_names": list(FEATURES),
        "horizons_days": list(HORIZONS),
        "source": str(source.resolve()), "source_sha256": hashlib.sha256(raw).hexdigest(),
        "audit": {"players_in_seed": len(seed.get("players", {})),
                  "players_with_daily_history": len(histories),
                  "players_with_examples": len({e.player_id for e in examples}),
                  "daily_rows": sum(map(len, histories.values())), "examples": len(examples),
                  "daily_start": min(min(v) for v in histories.values()).isoformat(),
                  "daily_end": max(max(v) for v in histories.values()).isoformat(),
                  "excluded": excluded, "temporal_scope": "retrospective_daily_history"},
        "split": split, "models": models, "evaluation": evaluation,
        "qualification": {"passed": False, "reasons": reasons},
        "limitations": ["Nachgeladene Tageshistorie; damalige Veröffentlichung nicht belegt.",
                        "Keine Intraday-, News-, Saisonrollen- oder Liquiditätsprognose.",
                        "Ungewisser Marktwert ist kein verfügbarer Finanzierungserlös."],
    }
    canonical = json.dumps(artifact, sort_keys=True, allow_nan=False).encode()
    version = "mv-ridge-" + hashlib.sha256(canonical).hexdigest()[:16]
    artifact["model_version"] = version
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"mv-candidate-{version}.json"
    report = directory / f"mv-report-{version}.json"
    candidate.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False))
    summary = {k: v for k, v in artifact.items() if k != "models"}
    summary["candidate_path"] = str(candidate.resolve())
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    return {**summary, "report_path": str(report.resolve())}
