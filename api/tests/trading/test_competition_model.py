"""Echte archivierte Auktionsbeobachtungen; kontrollierte Freigabe-Grenzfälle."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kickbase_agent.trading.competition import (
    BidObservation,
    load_competition,
    predict_probability,
    train_competition,
)

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)
FIXTURE = Path(__file__).parents[1] / "fixtures" / "competition_observations.json"


@pytest.fixture
def candidate():
    raw = json.loads(FIXTURE.read_text())["observations"]
    rows = [BidObservation(**{**r, "at": datetime.fromisoformat(r["at"])}) for r in raw]
    return train_competition(rows, league_id="1234567", listing_type="kickbase", as_of=NOW)


def probability(artifact, **kwargs):
    args = dict(amount=5_000_000, market_value=5_000_000, position=3,
                league_id="1234567", listing_type="kickbase", as_of=NOW + timedelta(minutes=1))
    return predict_probability(artifact, **(args | kwargs))


def qualify(candidate):
    # Kontrollierter Registry-Prüfzustand, kein Gütenachweis des Seedmodells.
    candidate["status"] = "promoted"
    candidate["eligible_for_promotion"] = True
    for name in ("validation", "test"):
        candidate[name] = dict(n=20, evaluated_inputs=20, won=10, brier=.1,
                               baseline_brier=.25, calibration_gap=.02)
    return candidate


def test_real_archive_has_no_prospective_probability_validation(candidate):
    assert candidate["rows"] == 95
    assert candidate["model"]["sigma"] > 0
    assert candidate["validation"]["n"] == candidate["test"]["n"] == 0
    assert candidate["eligible_for_promotion"] is False
    assert probability(candidate) is None


def test_status_string_alone_never_activates_failed_model(candidate, tmp_path):
    candidate["status"] = "promoted"
    assert probability(candidate) is None
    candidate["eligible_for_promotion"] = True
    assert probability(candidate) is None
    (tmp_path / "1234567-kickbase.json").write_text(json.dumps(candidate))
    assert load_competition("1234567", "kickbase", registry_dir=tmp_path) is None


@pytest.mark.parametrize("change", [
    {"amount": True}, {"market_value": True}, {"position": True},
    {"market_value": 100_000_000}, {"league_id": "1"}, {"listing_type": "manager"},
    {"as_of": NOW - timedelta(hours=1)},
])
def test_invalid_unseen_or_historically_unavailable_context_is_unknown(candidate, change):
    assert probability(qualify(candidate), **change) is None


def test_qualified_probability_needs_an_explicit_registry_reference_to_load(candidate, tmp_path):
    artifact = qualify(candidate)
    probabilities = [probability(artifact, amount=a) for a in (4_000_000, 5_000_000, 6_000_000)]
    assert all(p is not None and 0 <= p <= 1 for p in probabilities)
    assert probabilities == sorted(probabilities)
    (tmp_path / "1234567-kickbase.json").write_text(json.dumps(artifact))
    # Eine manuell gesetzte Statuszeichenfolge ersetzt keine hashgebundene Freigabe.
    assert load_competition("1234567", "kickbase", registry_dir=tmp_path) is None


def test_future_period_amounts_do_not_change_fitted_parameters(candidate):
    raw = json.loads(FIXTURE.read_text())["observations"]
    rows = [BidObservation(**{**r, "at": datetime.fromisoformat(r["at"])}) for r in raw]
    test_start = candidate["split_dates"]["test_start"]
    changed = [replace(r, amount=r.amount * 2) if r.at.date().isoformat() >= test_start else r
               for r in rows]
    other = train_competition(changed, league_id="1234567", listing_type="kickbase", as_of=NOW)
    assert other["model"] == candidate["model"]
    assert other["version"] != candidate["version"]


def test_partial_evaluation_coverage_cannot_pass_promotion(candidate):
    artifact = qualify(candidate)
    artifact["test"]["evaluated_inputs"] = 25
    assert probability(artifact) is None
