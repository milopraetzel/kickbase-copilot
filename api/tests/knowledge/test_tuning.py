"""Stellschrauben in einer Datei: Defaults gewinnen bei Zweifel, Schreiben nur mit Beleg."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kickbase_agent.knowledge import parameters as prm
from kickbase_agent.knowledge import tuning as tn


@pytest.fixture(autouse=True)
def _fresh_cache():
    tn.reset_cache()
    yield
    tn.reset_cache()


def _file(tmp_path: Path, values: dict) -> Path:
    path = tmp_path / "tuning.json"
    path.write_text(json.dumps({"values": values}), encoding="utf-8")
    return path


# ----------------------------------------------------------------- Lesen


def test_the_shipped_file_matches_the_code_defaults():
    """Die Datei ist der Startpunkt, nicht schon eine Abweichung."""
    assert tn.load_tuning() == tn.DEFAULTS


def test_a_file_value_wins_over_the_default(tmp_path):
    path = _file(tmp_path, {"expectation.kickbase_thin_p": 0.51})
    assert tn.load_tuning(path)["expectation.kickbase_thin_p"] == 0.51
    # alle anderen bleiben Default
    assert tn.load_tuning(path)["expectation.min_sample"] == tn.DEFAULTS[
        "expectation.min_sample"
    ]


def test_a_missing_file_falls_back_to_the_defaults(tmp_path):
    assert tn.load_tuning(tmp_path / "gibt-es-nicht.json") == tn.DEFAULTS


def test_a_broken_file_falls_back_and_never_raises(tmp_path):
    path = tmp_path / "tuning.json"
    path.write_text("{kaputt", encoding="utf-8")
    assert tn.load_tuning(path) == tn.DEFAULTS


def test_an_unknown_name_in_the_file_is_ignored(tmp_path):
    path = _file(tmp_path, {"erfunden": 1.0})
    assert "erfunden" not in tn.load_tuning(path)


def test_a_non_number_is_ignored(tmp_path):
    path = _file(tmp_path, {"expectation.kickbase_thin_p": "viel"})
    assert tn.load_tuning(path)["expectation.kickbase_thin_p"] == 0.45


def test_a_boolean_is_not_a_number(tmp_path):
    path = _file(tmp_path, {"expectation.min_sample": True})
    assert tn.load_tuning(path)["expectation.min_sample"] == 5


# ----------------------------------------------------------------- Schreiben


def test_writing_requires_a_reason_and_evidence(tmp_path):
    path = _file(tmp_path, {})
    with pytest.raises(tn.TuningError):
        tn.set_tuning("expectation.kickbase_thin_p", 0.5, reason="", evidence="x", path=path)
    with pytest.raises(tn.TuningError):
        tn.set_tuning("expectation.kickbase_thin_p", 0.5, reason="x", evidence="", path=path)


def test_writing_an_unknown_name_is_refused(tmp_path):
    with pytest.raises(tn.TuningError):
        tn.set_tuning("erfunden", 1.0, reason="r", evidence="b", path=_file(tmp_path, {}))


def test_writing_a_non_number_is_refused(tmp_path):
    with pytest.raises(tn.TuningError):
        tn.set_tuning(
            "expectation.kickbase_thin_p", "hoch", reason="r", evidence="b",
            path=_file(tmp_path, {}),
        )


def test_a_written_value_is_readable_again_and_reports_the_old_one(tmp_path):
    path = _file(tmp_path, {})
    change = tn.set_tuning(
        "expectation.kickbase_thin_p", 0.5,
        reason="Zwei verlorene Gebote knapp ueber MW",
        evidence="surprise:action:12 + action:19", path=path,
    )
    assert change["old"] == 0.45 and change["new"] == 0.5
    assert tn.load_tuning(path)["expectation.kickbase_thin_p"] == 0.5


# ------------------------------------------------- Wirkung auf die Formel


def test_the_bid_formula_reads_the_file(monkeypatch, tmp_path):
    from kickbase_agent.trading import expectation as ex

    path = _file(tmp_path, {"expectation.kickbase_thin_p": 0.11})
    monkeypatch.setattr(tn, "_package_path", lambda: path)
    tn.reset_cache()

    result = ex.estimate_bid_success(
        kind="kickbase", amount=1_050_000, market_value=1_000_000
    )
    assert result.p == 0.11


def test_the_bid_formula_survives_a_broken_file(monkeypatch, tmp_path):
    """Ein Tuning-Fehler darf nie ein Absturz und nie ein stilles 0 sein."""
    from kickbase_agent.trading import expectation as ex

    path = tmp_path / "tuning.json"
    path.write_text("kaputt", encoding="utf-8")
    monkeypatch.setattr(tn, "_package_path", lambda: path)
    tn.reset_cache()

    result = ex.estimate_bid_success(
        kind="kickbase", amount=1_050_000, market_value=1_000_000
    )
    assert result.p == 0.45  # Code-Rueckfall


def test_every_tuning_name_has_a_current_value_in_the_protocol():
    """Sonst faende das Protokoll zu einem Decision-Record keinen Ist-Wert."""
    current = prm.current_parameters()
    assert set(tn.DEFAULTS) <= set(current)


# ------------------------------------------------- Decision-Record (Belegpflicht)


def _history(tmp_path: Path) -> Path:
    path = tmp_path / "parameter_history.json"
    path.write_text(json.dumps({"entries": []}), encoding="utf-8")
    return path


def test_a_record_needs_reason_evidence_and_commit(tmp_path):
    path = _history(tmp_path)
    common = dict(parameter="expectation.kickbase_thin_p", old=0.45, new=0.5, path=path)
    with pytest.raises(prm.RecordError):
        prm.record_change(reason="", commit="abc1234", beleg="L-064", **common)
    with pytest.raises(prm.RecordError):
        prm.record_change(reason="r", commit="abc1234", beleg="", **common)
    with pytest.raises(prm.RecordError):
        prm.record_change(reason="r", commit="abc", beleg="L-064", **common)


def test_a_record_for_an_unknown_parameter_is_refused(tmp_path):
    """parameter_history.json ist fuer Stellschrauben, nicht fuer Notizen."""
    with pytest.raises(prm.RecordError):
        prm.record_change(
            parameter="irgendwas", old=None, new="x", reason="r", commit="abc1234",
            beleg="L-1", path=_history(tmp_path),
        )


def test_a_valid_record_lands_newest_first(tmp_path):
    path = _history(tmp_path)
    prm.record_change(
        parameter="expectation.kickbase_thin_p", old=0.45, new=0.5,
        reason="Zwei gleichartige Faelle", commit="abc1234", beleg="surprise:action:12",
        expect="hoehere Zuschlagsquote", review="SP2-Retro", date="2026-08-26", path=path,
    )
    prm.record_change(
        parameter="expectation.kickbase_over_p", old=0.6, new=0.62,
        reason="noch einer", commit="def5678", beleg="L-064",
        einzelfall_ausnahme=True, date="2026-08-27", path=path,
    )
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert [e["date"] for e in entries] == ["2026-08-27", "2026-08-26"]
    assert entries[0]["einzelfall_ausnahme"] is True
    assert entries[1]["beleg"] == "surprise:action:12"


def test_a_record_names_its_author(tmp_path):
    """Emil muss in der Zentrale sehen, WER geändert hat (26.08.). Ohne Angabe
    ist es der Orchestrator — geraten wird nicht, und Alt-Einträge behalten recht."""
    path = _history(tmp_path)
    prm.record_change(
        parameter="expectation.kickbase_thin_p", old=0.45, new=0.5, reason="r",
        commit="abc1234", beleg="L-064", date="2026-08-26", path=path,
    )
    prm.record_change(
        parameter="expectation.kickbase_over_p", old=0.6, new=0.62, reason="r",
        commit="def5678", beleg="surprise:action:12", quelle="selbstumbau",
        date="2026-08-27", path=path,
    )
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert entries[0]["quelle"] == "selbstumbau"
    assert entries[1]["quelle"] == "orchestrator"


def test_the_loader_hands_the_author_through(tmp_path, monkeypatch):
    path = _history(tmp_path)
    path.write_text(json.dumps({"entries": [
        {"date": "2026-08-26", "parameter": "a", "new": "1", "quelle": "selbstumbau"},
        {"date": "2026-08-25", "parameter": "b", "new": "2"},   # Alt-Eintrag
    ]}), encoding="utf-8")
    monkeypatch.setattr(prm, "HISTORY_FILE", path.name)
    monkeypatch.setattr(prm.resources, "files", lambda _pkg: path.parent)

    rows = prm.load_parameter_history()

    assert [r["quelle"] for r in rows] == ["selbstumbau", "orchestrator"]
