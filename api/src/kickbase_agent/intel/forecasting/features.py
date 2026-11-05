"""Identische historische Merkmale für Training und laufende Prognose."""

from __future__ import annotations

from datetime import datetime, timedelta

from kickbase_agent.intel.forecasting.dataset import Observation

FEATURE_VERSION = "appearance-lags-v1"
FEATURE_NAMES = [
    "points_last", "points_mean3", "points_mean8", "points_recency5",
    "minutes_last", "minutes_mean3", "minutes_mean8", "days_since", "history_count",
]


def recent(history: list[Observation], as_of: datetime) -> list[Observation]:
    return [r for r in history if as_of - timedelta(days=370) <= r.at < as_of][-8:]


def weighted(values: list[float]) -> float:
    values = values[-5:]
    return sum((i + 1) * v for i, v in enumerate(values)) / sum(range(1, len(values) + 1))


def baseline(history: list[Observation], target: str) -> float:
    return weighted([getattr(r, target) for r in history])


def feature_vector(history: list[Observation], as_of: datetime) -> list[float]:
    def mean(name: str, size: int) -> float:
        return sum(getattr(r, name) for r in history[-size:]) / len(history[-size:])

    return [
        history[-1].points, mean("points", 3), mean("points", 8), baseline(history, "points"),
        history[-1].minutes, mean("minutes", 3), mean("minutes", 8),
        min(370.0, (as_of - history[-1].at).total_seconds() / 86400), float(len(history)),
    ]
