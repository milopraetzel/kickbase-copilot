"""Unveränderte damalige Eingaben mit später abgeschlossenen Ergebnissen verbinden."""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kickbase_agent.intel.forecasting.dataset import SETTLEMENT, Observation, load_seed, utc
from kickbase_agent.intel.forecasting.features import baseline, feature_vector, recent

if TYPE_CHECKING:
    from kickbase_agent.intel.forecasting.training import Example


def prospective_partitions(
    outcomes: list[Observation], *, historical_seeds: Sequence[str | Path], as_of: datetime,
) -> tuple[dict[str, list[Example]] | None, dict[str, Any] | None, dict[str, Any]]:
    """Snapshots streng vor Zielspiel; keinerlei Rückdatierung heutiger Werte.

    Pro Quelle bleibt nur eine Seed-Datei gleichzeitig im Speicher. Training wird
    ebenfalls aus einem vor der ersten Validierung vorhandenen Seed rekonstruiert.
    Die zwölf Zeitgruppen sind zweimal das unveränderte Gate-Minimum von sechs.
    """
    from kickbase_agent.intel.forecasting.training import Example, examples_from

    stamps: dict[datetime, dict[str, str]] = {}
    for path in sorted({Path(p).resolve() for p in historical_seeds}):
        content = path.read_bytes()
        payload = json.loads(content)
        stamp = utc(payload.get("exported_at"))
        if stamp is None or stamp >= as_of:
            continue
        entry = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(),
                 "exported_at": stamp.isoformat()}
        if stamp in stamps and stamps[stamp]["sha256"] != entry["sha256"]:
            raise ValueError("Widersprüchliche immutable Snapshots am selben Zeitpunkt.")
        stamps[stamp] = entry
    dates = sorted(stamps)
    coverage: dict[str, Any] = {
        "sources": [stamps[d] for d in dates], "source_count": len(dates),
        "used_for_evaluation": False, "prospective_examples": 0, "prospective_groups": 0,
        "limits": [
            "Prospektiv bezeichnet datierte Eingaben, noch keinen früheren Live-Modellbetrieb.",
            "Nur beobachtete Einsätze; Start-/Nichteinsatzlabels bleiben unbekannt.",
            "Aktuelle abgeschlossene Ergebnisse können spätere Punktekorrekturen enthalten.",
        ],
    }
    by_seed: dict[datetime, list[Observation]] = defaultdict(list)
    excluded: Counter[str] = Counter()
    for outcome in outcomes:
        index = bisect_left(dates, outcome.at) - 1
        if index < 0:
            excluded["no_prior_snapshot"] += 1
        else:
            by_seed[dates[index]].append(outcome)
    examples: list[Example] = []
    for stamp, targets in sorted(by_seed.items()):
        snapshot, _ = load_seed(stamps[stamp]["path"], as_of=stamp)
        by_player: dict[str, list[Observation]] = defaultdict(list)
        for row in snapshot:
            by_player[row.player_id].append(row)
        for outcome in targets:
            history = recent(by_player[outcome.player_id], stamp)
            if len(history) < 3:
                excluded["insufficient_known_history"] += 1
                continue
            if any(r.captured_at is None or r.captured_at > stamp for r in history):
                excluded["unknown_feature_capture"] += 1
                continue
            examples.append(Example(
                outcome, feature_vector(history, stamp), baseline(history, "points"),
                baseline(history, "minutes"), True,
                feature_seed=stamps[stamp]["path"], feature_as_of=stamp,
            ))
    starts: dict[str, datetime] = {}
    for example in examples:
        row = example.outcome
        starts[row.group] = min(starts.get(row.group, row.at), row.at)
    groups = sorted(starts, key=lambda group: starts[group])
    coverage.update(prospective_examples=len(examples), prospective_groups=len(groups),
                    excluded=dict(excluded))
    if len(groups) < 12:
        coverage["reason"] = "Zu wenige prospektive Zeitgruppen für je sechs Val-/Testgruppen."
        return None, None, coverage
    boundary = len(groups) // 2
    validation_groups, test_groups = set(groups[:boundary]), set(groups[boundary:])
    validation = [e for e in examples if e.outcome.group in validation_groups]
    test = [e for e in examples if e.outcome.group in test_groups]
    test_start = min(e.outcome.at for e in test)
    unsafe = {e.outcome.group for e in validation if e.outcome.at + SETTLEMENT >= test_start}
    validation = [e for e in validation if e.outcome.group not in unsafe]
    if len({e.outcome.group for e in validation}) < 6:
        coverage["reason"] = "Finalitätsabstand lässt weniger als sechs Validierungsgruppen."
        return None, None, coverage
    first_validation = min(e.outcome.at for e in validation)
    training_stamp = dates[bisect_left(dates, first_validation) - 1]
    training_path = stamps[training_stamp]["path"]
    known, _ = load_seed(training_path, as_of=training_stamp)
    train = examples_from(known)
    unsafe = {e.outcome.group for e in train if e.outcome.at + SETTLEMENT >= first_validation}
    # Auch ein verlegtes Einzelspiel darf seine Saison-/Spieltaggruppe nicht
    # zwischen Trainingsbasis und Zukunftstest aufteilen.
    excluded_groups = unsafe | validation_groups | test_groups
    train = [e for e in train if e.outcome.group not in excluded_groups]
    if not train:
        coverage["reason"] = "Keine bereits vor der Validierung bekannte Trainingsbasis."
        return None, None, coverage
    partitions = {"train": train, "validation": validation, "test": test}
    split: dict[str, Any] = {
        "method": "prospective_seed_join_v1", "gap_hours": 72,
        "training_seed": training_path, "training_available_at": training_stamp.isoformat(),
    }
    for name, rows in partitions.items():
        split[f"{name}_groups"] = sorted({e.outcome.group for e in rows})
        split[f"{name}_start"] = min(e.outcome.at for e in rows).isoformat()
        split[f"{name}_end"] = max(e.outcome.at for e in rows).isoformat()
        split[f"{name}_rows"] = len(rows)
    coverage.update(used_for_evaluation=True, reason="Datierte Eingaben für Val/Test belegt.")
    return partitions, split, coverage
