"""Prognosen aus echten normalisierten Archivzeilen und kontrollierten Grenzfällen."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from kickbase_agent.intel.forecasting.dataset import clean_rows, load_seed
from kickbase_agent.intel.forecasting.registry import load_active, promote, rollback
from kickbase_agent.intel.forecasting.service import forecast_player
from kickbase_agent.intel.forecasting.training import (
    artifact_version,
    evaluate_candidate,
    promotion_gate,
    train_candidate,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forecast_seed_sample.json"
AS_OF = datetime(2026, 9, 13, 12, tzinfo=UTC)


def test_seed_audit_excludes_future_ambiguous_zeros_and_unfinished_round():
    observations, audit = load_seed(FIXTURE, as_of=AS_OF)
    assert observations
    assert audit["excluded"]["future"] > 0
    assert audit["excluded"]["ambiguous_minutes"] > 0
    assert all(r.minutes > 0 and r.points != 0 for r in observations)
    assert all(not (r.season == "2026/2027" and r.matchday >= 3) for r in observations)
    assert audit["start_labels"] == 0
    assert audit["source_sha256"]
    assert audit["raw_rows"] == sum(
        len(p["performance"]) for p in json.loads(FIXTURE.read_text())["players"].values()
    )


def test_service_keeps_missing_play_probability_and_ignores_future_zero_rows():
    players = json.loads(FIXTURE.read_text())["players"]
    pid, data = next(iter(players.items()))
    player = SimpleNamespace(id=pid, fitness_status=0)
    result = forecast_player(player, data["performance"], as_of=AS_OF)
    without_future = [r for r in data["performance"] if r["match_date"] < AS_OF.isoformat()]
    other = forecast_player(player, without_future, as_of=AS_OF)
    assert result["expected_points"] == other["expected_points"]
    assert result["expected_minutes"] > 0
    assert result["horizon_points"] == 4 * result["expected_points"]
    assert result["play_probability"] is None
    assert result["points_basis"] == "conditional_on_appearance"
    assert result["model_version"] == "baseline-recency-v1"
    assert any("Einsatz" in r for r in result["reasons"])


def test_missing_history_has_no_fabricated_minutes_or_trained_forecast():
    result = forecast_player(SimpleNamespace(id="unknown"), [], as_of=AS_OF)
    assert result["expected_points"] == 0
    assert result["forecast_available"] is False
    assert result["expected_minutes"] is None
    assert result["play_probability"] is None


def test_real_seed_trains_numerical_models_with_disjoint_dates_and_unqualified_gate(tmp_path):
    artifact = train_candidate(FIXTURE, registry_dir=tmp_path, as_of=AS_OF)
    assert set(artifact["models"]) == {"points", "minutes"}
    split = artifact["split"]
    assert split["train_end"] < split["validation_start"] < split["test_start"]
    assert set(split["train_groups"]).isdisjoint(split["validation_groups"])
    assert set(split["validation_groups"]).isdisjoint(split["test_groups"])
    assert artifact["evaluation"]["test"]["points"]["candidate"]["mae"] >= 0
    assert artifact["evaluation"]["promotion_gate"]["passed"] is False
    reasons = artifact["evaluation"]["promotion_gate"]["reasons"]
    assert "historical_publication_unverified" in reasons
    assert (tmp_path / "candidates" / f"{artifact['model_version']}.json").exists()
    assert not (tmp_path / "active.json").exists()


def test_holdout_labels_do_not_change_fitted_weights_or_hyperparameter_choice(tmp_path):
    first = train_candidate(FIXTURE, registry_dir=tmp_path / "first", as_of=AS_OF)
    payload = json.loads(FIXTURE.read_text())
    groups = first["split"]["test_groups"]
    for player in payload["players"].values():
        for row in player["performance"]:
            if (f"{row['season']}:{row['matchday']:02d}" in groups
                    and row["minutes"] > 0 and row["points"] != 0):
                row["points"] += 10_000
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload))
    second = train_candidate(changed, registry_dir=tmp_path / "second", as_of=AS_OF)
    assert first["models"] == second["models"]
    assert first["evaluation"]["test"] != second["evaluation"]["test"]


def test_tiny_history_does_not_pretend_to_be_trainable(tmp_path):
    source = tmp_path / "empty.json"
    source.write_text(json.dumps({"players": {}}))
    with pytest.raises(ValueError, match="Zeitgruppen"):
        train_candidate(source, registry_dir=tmp_path / "models", as_of=AS_OF)


def test_failed_candidate_cannot_be_promoted_or_loaded_as_active(tmp_path):
    artifact = train_candidate(FIXTURE, registry_dir=tmp_path, as_of=AS_OF)
    with pytest.raises(ValueError, match="Freigabe"):
        promote(artifact["model_version"], registry_dir=tmp_path)
    assert load_active(tmp_path) is None
    (tmp_path / "active.json").write_text('{"model_version":"../../invalid"}')
    assert load_active(tmp_path) is None
    rollback(registry_dir=tmp_path, to_baseline=True)
    assert load_active(tmp_path) is None


@pytest.mark.parametrize(("fitness", "probability"), [(0, 4), (0, 5), (2, 1)])
def test_bank_or_unfit_roles_use_explicit_conservative_planning_adjustment(fitness, probability):
    pid, data = next(iter(json.loads(FIXTURE.read_text())["players"].items()))
    player = SimpleNamespace(id=pid, fitness_status=fitness, lineup_prob=probability)
    result = forecast_player(player, data["performance"], as_of=AS_OF)
    assert result["expected_points"] == 0
    assert result["expected_minutes"] == 0
    assert result["availability_adjustment"]["basis"] == "conservative_role_heuristic"
    assert result["play_probability"] is None


def test_negative_points_and_explicit_observed_zero_are_retained():
    _, data = next(iter(json.loads(FIXTURE.read_text())["players"].items()))
    raw = next(r for r in data["performance"] if r["points"] < 0 and r["minutes"] > 0)
    rows, _ = clean_rows(raw["player_id"], [raw], as_of=AS_OF)
    assert rows[0].points < 0
    # Kontrollierte Null- und Konfliktfälle auf einer echten Domainzeile.
    zero = {**raw, "points": 0, "points_observed": True}
    rows, _ = clean_rows(raw["player_id"], [zero], as_of=AS_OF)
    assert rows[0].points == 0
    rows, counts = clean_rows(raw["player_id"], [raw, zero], as_of=AS_OF)
    assert rows == []
    assert counts["duplicate_conflict"] == 2


@pytest.mark.parametrize("row_timestamp", [True, False])
def test_later_capture_is_not_available_for_historical_forecast(row_timestamp):
    _, data = next(iter(json.loads(FIXTURE.read_text())["players"].items()))
    raw = next(r for r in data["performance"] if r["points"] > 0 and r["minutes"] > 0)
    capture = (AS_OF + timedelta(hours=1)).isoformat()
    row = {**raw, "captured_at": capture if row_timestamp else None}
    rows, counts = clean_rows(raw["player_id"], [row], as_of=AS_OF, captured_at=capture)
    assert rows == []
    assert counts["not_yet_observed"] == 1


def test_frozen_evaluation_matches_and_rejects_changed_source(tmp_path):
    artifact = train_candidate(FIXTURE, registry_dir=tmp_path, as_of=AS_OF)
    assert evaluate_candidate(FIXTURE, artifact) == artifact["evaluation"]
    other = tmp_path / "other.json"
    other.write_text(FIXTURE.read_text() + "\n")
    with pytest.raises(ValueError, match="fingerabdruck"):
        evaluate_candidate(other, artifact)


def test_registry_qualified_load_prediction_rollback_and_corruption(tmp_path):
    artifact = train_candidate(FIXTURE, registry_dir=tmp_path, as_of=AS_OF)
    # Kontrollierter Registry-Beleg: Die echte Trainingsuhr darf diesen Test
    # nach AS_OF nicht in den korrekt gesperrten Zukunftsmodell-Fall verwandeln.
    # Dieser Fall wird unten mit trained_at > AS_OF separat geprüft.
    artifact["trained_at"] = AS_OF.isoformat()
    # Nur den Registry-Vertrag mit einem kontrolliert bestandenen Prüfbericht
    # ausüben; dies ist kein behauptetes Güteergebnis für den echten Seed.
    evaluation = artifact["evaluation"]
    evaluation["historical_publication_verified_fraction"] = 1.0
    for period in ("validation", "test"):
        evaluation[period]["groups"] = 10
        for target in ("points", "minutes"):
            score = evaluation[period][target]
            score["candidate"]["mae"] = score["baseline"]["mae"] * .9
            score["group_mae_delta_ci95"] = [-2.0, -1.0]
    evaluation["promotion_gate"] = promotion_gate(evaluation)

    def save():
        artifact["model_version"] = artifact_version(artifact)
        path = tmp_path / "candidates" / f"{artifact['model_version']}.json"
        path.write_text(json.dumps(artifact))
        return artifact["model_version"], path

    first, _ = save()
    promote(first, registry_dir=tmp_path)
    active = load_active(tmp_path)
    assert active["model_version"] == first
    pid, data = next(iter(json.loads(FIXTURE.read_text())["players"].items()))
    prediction = forecast_player(SimpleNamespace(id=pid), data["performance"],
                                 as_of=AS_OF, artifact=active)
    assert prediction["model_version"] == first
    assert prediction["play_probability"] is None
    unavailable = json.loads(json.dumps(active))
    unavailable["trained_at"] = (AS_OF + timedelta(days=1)).isoformat()
    unavailable["model_version"] = artifact_version(unavailable)
    historical = forecast_player(SimpleNamespace(id=pid), data["performance"],
                                 as_of=AS_OF, artifact=unavailable)
    assert historical["model_version"] == "baseline-recency-v1"
    artifact["models"]["points"]["intercept"] += 1
    second, path = save()
    promote(second, registry_dir=tmp_path)
    assert load_active(tmp_path)["model_version"] == second
    rollback(registry_dir=tmp_path)
    assert load_active(tmp_path)["model_version"] == first
    promote(second, registry_dir=tmp_path)
    artifact["models"]["points"]["intercept"] += 1
    path.write_text(json.dumps(artifact))
    assert load_active(tmp_path) is None


def test_cli_train_evaluate_refused_promotion_and_baseline_status(tmp_path):
    from typer.testing import CliRunner

    from kickbase_agent.cli.forecast import app

    runner = CliRunner()
    args = ["--registry-dir", str(tmp_path)]
    result = runner.invoke(app, ["train", str(FIXTURE), *args, "--as-of", AS_OF.isoformat()])
    assert result.exit_code == 0, result.output
    version = json.loads(result.stdout)["model_version"]
    result = runner.invoke(app, ["evaluate", version, str(FIXTURE), *args])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["promote", version, *args])
    assert result.exit_code != 0
    result = runner.invoke(app, ["status", *args])
    assert json.loads(result.stdout)["active_loaded"] is False
    result = runner.invoke(app, ["rollback", "--baseline", *args])
    assert result.exit_code == 0
