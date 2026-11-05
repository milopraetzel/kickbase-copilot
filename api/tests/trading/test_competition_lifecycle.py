"""Registry-Zustände und bestätigte interne Gebotslabels; keine erfundenen API-Payloads."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from kickbase_agent.cli.competition import app, observations_from_actions
from kickbase_agent.db.models import Base
from kickbase_agent.trading import competition as model

from .test_bid_receipt_evidence import AT, bid, settle


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        yield database


def qualified(version="a"):
    # Kontrollierter Registry-Grenzfall, ausdrücklich kein Gütenachweis echter Daten.
    score = {
        "n": 20,
        "evaluated_inputs": 20,
        "won": 10,
        "brier": 0.1,
        "baseline_brier": 0.25,
        "calibration_gap": 0.01,
    }
    return {
        "schema_version": 1,
        "version": "competition-" + version * 16,
        "algorithm_version": "censored-lognormal-v1-gate-v2",
        "status": "candidate",
        "league_id": "1234567",
        "listing_type": "kickbase",
        "created_at": "2026-08-05T00:00:00+00:00",
        "trained_through": "2026-08-01T00:00:00+00:00",
        "eligible_for_promotion": True,
        "rows": 100,
        "splits": {"train": 60, "validation": 20, "test": 20},
        "split_dates": {"validation_start": "2026-08-02", "test_start": "2026-08-03"},
        "validation": score,
        "test": dict(score),
        "market_value_range": [1_000_000, 20_000_000],
        "model": {"weights": [0.0, 0.0, 0.0, 0.0, 0.0], "sigma": 0.3},
    }


def candidate_file(tmp_path, artifact):
    path = tmp_path / f"{artifact['version']}.json"
    path.write_text(json.dumps(artifact))
    return path


def test_explicit_cli_promote_status_rollback_and_unknown_fallback(tmp_path):
    runner = CliRunner()
    directory = tmp_path / "registry"
    context = [
        "--league-id",
        "1234567",
        "--listing-type",
        "kickbase",
        "--registry-dir",
        str(directory),
    ]
    a, b = qualified("a"), qualified("b")
    for artifact in (a, b):
        source = candidate_file(tmp_path, artifact)
        result = runner.invoke(app, ["promote", str(source), *context])
        assert result.exit_code == 0, result.output
        assert (
            model.load_competition("1234567", "kickbase", registry_dir=directory)["version"]
            == artifact["version"]
        )
        assert json.loads(source.read_text())["status"] == "candidate"
    status = runner.invoke(app, ["status", *context])
    assert status.exit_code == 0 and b["version"] in status.output
    result = runner.invoke(app, ["rollback", *context])
    assert result.exit_code == 0, result.output
    assert (
        model.load_competition("1234567", "kickbase", registry_dir=directory)["version"]
        == a["version"]
    )
    assert runner.invoke(app, ["rollback", "--baseline", *context]).exit_code == 0
    assert model.load_competition("1234567", "kickbase", registry_dir=directory) is None


@pytest.mark.parametrize(
    "change",
    [
        {"league_id": "other"},
        {"listing_type": "manager"},
        {"eligible_for_promotion": False},
        {"model": {"weights": [float("nan")] * 5, "sigma": 0.3}},
        {"model": {"weights": [0.0] * 5, "sigma": 0}},
        {
            "validation": {
                "n": 100,
                "evaluated_inputs": 100,
                "won": 50,
                "brier": 0.1,
                "baseline_brier": 0.25,
                "calibration_gap": 0.01,
            }
        },
        {"trained_through": "2026-08-04T00:00:00+00:00"},
    ],
)
def test_invalid_candidate_cannot_replace_active_version(tmp_path, change):
    directory = tmp_path / "registry"
    good = candidate_file(tmp_path, qualified())
    model.promote_competition(
        good, league_id="1234567", listing_type="kickbase", registry_dir=directory
    )
    before = (directory / "1234567-kickbase.json").read_bytes()
    bad = candidate_file(tmp_path, {**qualified("b"), **change})
    with pytest.raises(ValueError):
        model.promote_competition(
            bad, league_id="1234567", listing_type="kickbase", registry_dir=directory
        )
    assert (directory / "1234567-kickbase.json").read_bytes() == before


def test_candidate_mutation_after_promotion_fails_closed(tmp_path):
    source = candidate_file(tmp_path, qualified())
    model.promote_competition(
        source, league_id="1234567", listing_type="kickbase", registry_dir=tmp_path / "registry"
    )
    stored = next((tmp_path / "registry" / "candidates").rglob("*.json"))
    altered = json.loads(stored.read_text())
    altered["model"]["weights"][0] = 0.2
    stored.write_text(json.dumps(altered))
    assert model.load_competition("1234567", "kickbase", registry_dir=tmp_path / "registry") is None


def prospective_bid(session, *, amount=None, my_name="emil"):
    row = bid(session, amount=amount)
    row.dry_run = False
    row.payload = {
        "expectation": {
            "engine_version": "team-milp-v1",
            "model_version": "unknown",
            "p": None,
            "market_value_eur": row.amount,
            "position": 3,
            "kind": "kickbase",
            "observed_at": (AT - timedelta(hours=2)).isoformat(),
        }
    }
    session.commit()
    settle(session, my_name=my_name)
    return row


@pytest.mark.parametrize("result", ["won", "lost"])
def test_confirmed_prospective_labels_do_not_need_an_old_probability(session, result):
    from .test_bid_receipt_evidence import RECEIPT

    row = prospective_bid(
        session,
        amount=RECEIPT["data"]["trp"] - int(result == "lost"),
        my_name="emil" if result == "won" else "different manager",
    )
    [observation] = observations_from_actions([row], as_of=AT + timedelta(days=3))
    assert observation.prospective
    assert observation.kind == ("upper" if result == "won" else "lower")
    assert observation.market_value == row.amount


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_mv",
        "unconfirmed",
        "dry_run",
        "backfilled",
        "observed_after_result",
        "future_result",
        "missing_event",
    ],
)
def test_unreliable_or_future_labels_are_excluded(session, mutation):
    row = prospective_bid(session)
    payload = deepcopy(row.payload)
    cutoff = AT + timedelta(days=3)
    if mutation == "missing_mv":
        payload["expectation"].pop("market_value_eur")
    elif mutation == "unconfirmed":
        payload["outcome"]["source"] = "observed_ownership"
    elif mutation == "dry_run":
        row.dry_run = True
    elif mutation == "backfilled":
        payload["expectation"]["backfilled"] = True
    elif mutation == "observed_after_result":
        payload["expectation"]["observed_at"] = cutoff.isoformat()
    elif mutation == "future_result":
        cutoff = AT - timedelta(minutes=1)
    elif mutation == "missing_event":
        payload["outcome"]["event_id"] = None
    row.payload = payload
    assert observations_from_actions([row], as_of=cutoff) == []


def test_train_ledger_writes_a_candidate_without_activating_anything(
    session, monkeypatch, tmp_path
):
    import kickbase_agent.db.session as database

    prospective_bid(session)
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    output = tmp_path / "trained.json"
    result = CliRunner().invoke(
        app,
        [
            "train-ledger",
            "--league-id",
            "1234567",
            "--listing-type",
            "kickbase",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    candidate = json.loads(output.read_text())
    assert candidate["rows"] == 1 and candidate["status"] == "candidate"
    assert candidate["eligible_for_promotion"] is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ["trained.json"]


def test_corrupted_previous_cannot_replace_valid_active_version(tmp_path):
    directory = tmp_path / "registry"
    for version in ("a", "b"):
        model.promote_competition(
            candidate_file(tmp_path, qualified(version)),
            league_id="1234567",
            listing_type="kickbase",
            registry_dir=directory,
        )
    (directory / "1234567-kickbase-previous.json").write_text("[]")
    with pytest.raises(ValueError):
        model.rollback_competition(
            league_id="1234567", listing_type="kickbase", registry_dir=directory
        )
    assert (
        model.load_competition("1234567", "kickbase", registry_dir=directory)["version"]
        == qualified("b")["version"]
    )
