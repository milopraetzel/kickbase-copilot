"""Tagesgrenzen und Lücken prüfen; Training nutzt unveränderte reale Historien."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from kickbase_agent.cli.market_forecast import app
from kickbase_agent.intel.forecasting.market_value import (
    daily_history,
    forecast_market_value,
    market_examples,
    split_market_examples,
    train_market_candidate,
)

AS_OF = datetime(2026, 9, 13, tzinfo=UTC)
SEED = Path(__file__).parents[1] / "fixtures" / "market_value_seed_sample.json"


def _domain_series(n=100):
    # Reine mathematische Tagesreihe für Algorithmusgrenzen; kein API-Payload.
    return {date(2026, 6, 1) + timedelta(days=i): 1_000_000 + i * 10_000 for i in range(n)}


def _rows(values):
    return [{"date": day.isoformat(), "value": value} for day, value in values.items()]


def test_current_day_and_future_prices_cannot_change_an_earlier_forecast():
    rows = _rows(_domain_series())
    before = forecast_market_value(rows, AS_OF)
    after = forecast_market_value(rows + [
        {"date": "2026-09-13", "value": 999_000_000},
        {"date": "2026-09-14", "value": 500_000},
    ], AS_OF)
    assert after["horizons"] == before["horizons"]
    assert after["excluded"]["current_or_future_day"] == 2
    assert after["status"] == "stale_baseline"
    assert all(v["uncertainty"]["calibrated"] is False for v in after["horizons"].values())


def test_conflicting_same_day_is_removed_and_gap_never_becomes_future_label():
    values = _domain_series(30)
    missing = date(2026, 6, 15)
    rows = _rows(values) + [{"date": missing.isoformat(), "value": 99}]
    cleaned, audit = daily_history(rows, AS_OF)
    assert missing not in cleaned and audit["conflicting_days"] == 1
    examples = market_examples({"p": cleaned})
    before = next(e for e in examples if e.day == missing - timedelta(days=1))
    assert 1 not in before.changes
    assert not any(e.day == missing + timedelta(days=3) for e in examples)


def test_features_use_only_prices_on_or_before_their_origin():
    values = _domain_series(30)
    original = market_examples({"p": values})
    modified = {**values, date(2026, 6, 25): 900_000_000}
    changed = market_examples({"p": modified})
    for before, after in zip(original, changed, strict=True):
        if before.day < date(2026, 6, 25):
            assert before.features == after.features


def test_calendar_splits_never_mix_players_on_same_day_and_purge_seven_day_labels():
    examples = market_examples({"a": _domain_series(), "b": _domain_series()})
    partitions, _ = split_market_examples(examples)
    for name, following in (("train", "validation"), ("validation", "test")):
        next_day = min(e.day for e in partitions[following])
        assert all(e.day + timedelta(days=7) < next_day for e in partitions[name])
        assert not {e.day for e in partitions[name]} & {e.day for e in partitions[following]}
    for rows in partitions.values():
        for day in {e.day for e in rows}:
            assert {e.player_id for e in rows if e.day == day} == {"a", "b"}


def test_missing_adjacent_day_or_naive_timestamp_does_not_invent_a_trend():
    result = forecast_market_value([{"date": "2026-09-10", "value": 1_000_000}], AS_OF)
    assert result["status"] == "insufficient" and result["horizons"] == {}
    with pytest.raises(ValueError, match="Zeitzone"):
        daily_history([], AS_OF.replace(tzinfo=None))
    with pytest.raises(ValueError, match="40 Ursprungstage"):
        split_market_examples(market_examples({"a": _domain_series(20)}))


def test_older_than_yesterday_is_labelled_stale_and_targets_remain_explicit():
    result = forecast_market_value([
        {"date": "2026-09-10", "value": 1_000_000},
        {"date": "2026-09-11", "value": 1_010_000},
    ], AS_OF)
    assert result["status"] == "stale_baseline" and result["age_days"] == 2
    assert result["horizons"]["1"]["target_date"] == "2026-09-12"


def test_real_daily_history_training_creates_candidate_and_report_but_no_active_model(tmp_path):
    result = train_market_candidate(SEED, as_of=AS_OF, output_dir=tmp_path)
    candidate = json.loads(Path(result["candidate_path"]).read_text())
    assert candidate["status"] == "candidate"
    assert candidate["qualification"]["passed"] is False
    assert "historical_daily_publication_time_unverified" in candidate["qualification"]["reasons"]
    assert candidate["audit"]["players_with_daily_history"] == 20
    assert set(candidate["models"]) == {"1", "3", "7"}
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert not (tmp_path / "active.json").exists()
    for horizon in candidate["evaluation"].values():
        for period in horizon.values():
            assert set(period["metrics"]) == {"ridge", "flat", "last_trend"}
            assert period["days"] >= 6
            assert all(score["mae_eur"] >= 0 for score in period["metrics"].values())


def test_market_forecast_cli_is_a_train_only_subapp(tmp_path):
    root = typer.Typer()
    root.add_typer(app, name="market-forecast")
    result = CliRunner().invoke(root, ["market-forecast", "train", str(SEED),
                                      "--output-dir", str(tmp_path), "--as-of", AS_OF.isoformat()])
    assert result.exit_code == 0, result.output
    assert "Kandidat:" in result.output and "Prüfbericht:" in result.output
