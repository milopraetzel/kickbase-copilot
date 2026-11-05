"""Zeitpunktgerechte Joins auf kontrollierten Domain-Snapshots echter Leistungszeilen."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kickbase_agent.intel.forecasting.dataset import load_seed
from kickbase_agent.intel.forecasting.prospective import prospective_partitions
from kickbase_agent.intel.forecasting.training import evaluate_candidate, train_candidate

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forecast_seed_sample.json"
NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)


def snapshot(payload, at, path):
    # Gegenstand ist ein interner datierter Datenstand, keine erfundene API-Antwort.
    data = json.loads(json.dumps(payload))
    data["exported_at"] = at.isoformat()
    for player in data["players"].values():
        player.pop("performance_fetched_at", None)
        for row in player["performance"]:
            observed = datetime.fromisoformat(row["match_date"]) + timedelta(hours=72)
            row["captured_at"] = observed.isoformat()
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def data(tmp_path):
    payload = json.loads(FIXTURE.read_text())
    observations, _ = load_seed(FIXTURE, as_of=NOW)
    starts = {}
    for row in observations:
        starts[row.group] = min(starts.get(row.group, row.at), row.at)
    groups = sorted(starts, key=starts.get)[-16:]
    paths = [snapshot(payload, starts[g] - timedelta(hours=1), tmp_path / f"seed-{i}.json")
             for i, g in enumerate(groups)]
    return payload, paths, observations


def test_historical_snapshots_make_publication_gate_reachable_without_relabeling(data, tmp_path):
    _, paths, _ = data
    artifact = train_candidate(FIXTURE, registry_dir=tmp_path / "model", as_of=NOW,
                               historical_seeds=paths)
    assert artifact["split"]["method"] == "prospective_seed_join_v1"
    assert artifact["evaluation"]["historical_publication_verified_fraction"] == 1.0
    assert "historical_publication_unverified" not in artifact["evaluation"][
        "promotion_gate"]["reasons"]
    assert artifact["evaluation"]["validation"]["groups"] >= 6
    assert artifact["evaluation"]["test"]["groups"] >= 6
    assert artifact["split"]["train_end"] < artifact["split"]["validation_start"]
    assert set(artifact["split"]["train_groups"]).isdisjoint(
        artifact["split"]["validation_groups"] + artifact["split"]["test_groups"],
    )
    assert evaluate_candidate(FIXTURE, artifact) == artifact["evaluation"]
    assert not (tmp_path / "model" / "active.json").exists()


def test_current_label_corrections_cannot_rewrite_historical_features_or_training(data, tmp_path):
    payload, paths, rows = data
    first, _, _ = prospective_partitions(rows, historical_seeds=paths, as_of=NOW)
    artifact = train_candidate(FIXTURE, registry_dir=tmp_path / "one", as_of=NOW,
                               historical_seeds=paths)
    test_groups = set(artifact["split"]["test_groups"])
    for player in payload["players"].values():
        for row in player["performance"]:
            if f"{row['season']}:{row['matchday']:02d}" in test_groups and row["points"] != 0:
                row["points"] += 1000
    changed = tmp_path / "current-corrected.json"
    changed.write_text(json.dumps(payload))
    outcomes, _ = load_seed(changed, as_of=NOW)
    second, _, _ = prospective_partitions(outcomes, historical_seeds=paths, as_of=NOW)
    assert [r.features for r in first["test"]] == [r.features for r in second["test"]]
    assert [r.outcome.points for r in first["test"]] != [r.outcome.points for r in second["test"]]
    other = train_candidate(changed, registry_dir=tmp_path / "two", as_of=NOW,
                            historical_seeds=paths)
    assert artifact["models"] == other["models"]


def test_later_snapshot_never_rewrites_prior_examples_and_changed_archive_invalidates_eval(
    data, tmp_path,
):
    payload, paths, rows = data
    first, _, _ = prospective_partitions(rows, historical_seeds=paths, as_of=NOW)
    for player in payload["players"].values():
        for row in player["performance"]:
            if row["points"] != 0:
                row["points"] += 1000
    future = snapshot(payload, NOW - timedelta(hours=1), tmp_path / "later.json")
    second, _, _ = prospective_partitions(rows, historical_seeds=paths + [future], as_of=NOW)
    assert [r.features for r in first["test"]] == [r.features for r in second["test"]]
    artifact = train_candidate(FIXTURE, registry_dir=tmp_path / "model", as_of=NOW,
                               historical_seeds=paths)
    paths[0].write_text(paths[0].read_text() + "\n")
    with pytest.raises(ValueError, match="Snapshot"):
        evaluate_candidate(FIXTURE, artifact)


def test_too_few_prospective_groups_stays_explicitly_retrospective(data, tmp_path):
    _, paths, _ = data
    # Ein Snapshot direkt vor dem letzten Ziel liefert nur einen Zukunftsspieltag.
    artifact = train_candidate(FIXTURE, registry_dir=tmp_path, as_of=NOW,
                               historical_seeds=paths[-1:])
    assert artifact["prospective_coverage"]["used_for_evaluation"] is False
    assert artifact["evaluation"]["promotion_gate"]["passed"] is False
