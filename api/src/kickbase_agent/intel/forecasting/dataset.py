"""Belegte Einsatzlabels bereinigen, ohne historische Nullen zu erfinden."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Konservative Wartezeit für Seed-Training, kein behaupteter API-Finalitätsvertrag.
SETTLEMENT = timedelta(hours=72)
# Live-Planungsfenster nach Spielende (2 h Spiel + 4 h Korrekturpuffer = 6 h ab Anstoß).
LIVE_SETTLEMENT = timedelta(hours=6)


@dataclass(frozen=True)
class Observation:
    player_id: str
    season: str
    matchday: int
    at: datetime
    points: float
    minutes: float
    captured_at: datetime | None = None

    @property
    def group(self) -> str:
        return f"{self.season}:{self.matchday:02d}"


def utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(UTC)


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def clean_rows(
    player_id: str,
    rows: list[Any],
    *,
    as_of: datetime,
    captured_at: Any = None,
    settlement: timedelta = SETTLEMENT,
) -> tuple[list[Observation], dict[str, int]]:
    cutoff = utc(as_of)
    if cutoff is None:
        raise ValueError("as_of muss ein datierter Zeitpunkt mit Zeitzone sein.")
    excluded: Counter[str] = Counter()
    found: dict[tuple[str, int], Observation] = {}
    conflicts: set[tuple[str, int]] = set()
    for value in rows:
        row = value.model_dump() if hasattr(value, "model_dump") else value
        if not isinstance(row, dict):
            excluded["invalid_row"] += 1
            continue
        at = utc(row.get("match_date"))
        capture = utc(row.get("captured_at")) or utc(captured_at)
        if capture is not None and capture > cutoff:
            excluded["not_yet_observed"] += 1
            continue
        limit = min(cutoff, capture) if capture is not None else cutoff
        if at is None:
            excluded["undated"] += 1
            continue
        if at >= limit:
            excluded["future"] += 1
            continue
        if row.get("is_finished") is False or at + settlement > limit:
            excluded["unfinished_or_unsettled"] += 1
            continue
        points, minutes = number(row.get("points")), number(row.get("minutes"))
        # Der Bestand hat keine verlässlichen Nichteinsatzlabels. Auch explizite
        # Nullminuten sind für dieses bedingte Regressionsmodell kein Einsatz.
        if minutes is None or minutes <= 0 or row.get("minutes_observed") is False:
            excluded["ambiguous_minutes"] += 1
            continue
        if (points is None or row.get("points_observed") is False
                or (points == 0 and row.get("points_observed") is not True)):
            excluded["ambiguous_points"] += 1
            continue
        season, matchday = row.get("season"), row.get("matchday")
        if not isinstance(season, str) or type(matchday) is not int or matchday <= 0:
            excluded["invalid_identity"] += 1
            continue
        key = (season, matchday)
        observation = Observation(player_id, season, matchday, at, points, minutes, capture)
        if key in conflicts:
            excluded["duplicate_conflict"] += 1
        elif key in found:
            previous = found[key]
            if (previous.at, previous.points, previous.minutes) != (at, points, minutes):
                conflicts.add(key)
                del found[key]
                excluded["duplicate_conflict"] += 2
            else:
                excluded["duplicate"] += 1
        else:
            found[key] = observation
    return sorted(found.values(), key=lambda r: r.at), dict(excluded)


def load_seed(path: str | Path, *, as_of: datetime) -> tuple[list[Observation], dict[str, Any]]:
    content = Path(path).read_bytes()
    payload = json.loads(content)
    observations: list[Observation] = []
    excluded: Counter[str] = Counter()
    raw_rows = 0
    for pid, player in payload["players"].items():
        rows = player.get("performance") or []
        raw_rows += len(rows)
        clean, counts = clean_rows(
            str(pid), rows, as_of=as_of, captured_at=player.get("performance_fetched_at"),
        )
        observations.extend(clean)
        excluded.update(counts)
    observations.sort(key=lambda r: (r.at, r.player_id))
    return observations, {
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "as_of": as_of.isoformat(), "raw_rows": raw_rows,
        "accepted_rows": len(observations), "excluded": dict(excluded),
        "players": len({r.player_id for r in observations}), "start_labels": 0,
        "nonappearance_labels": 0, "settlement_hours": 72,
        "label_scope": "observed_appearances_only",
        "limits": [
            "Keine belegten historischen Start-/Nichteinsatzlabels.",
            "Nachgeladene Historie; spätere Punktekorrekturen nicht rekonstruierbar.",
            "Spielerauswahl des Seeds ist keine vollständige historische Spielergesamtheit.",
            "72h-Wartezeit ist eine konservative Finalitätsnäherung.",
        ],
    }
