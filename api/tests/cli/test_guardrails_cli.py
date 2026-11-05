"""CLI der Sicherungen: das Tor liefert Exit-Codes, die ein Deploy auswerten kann.

Der Exit-Code ist hier die eigentliche Schnittstelle — agent/self_change.py fragt
nichts anderes ab. Ein Fehler, der als Text erscheint, aber Exit 0 liefert, wäre
schlimmer als gar keine Prüfung.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from kickbase_agent.agent import deploy_env as de
from kickbase_agent.agent import guardrail_lock as gl
from kickbase_agent.cli import app
from kickbase_agent.cli import guardrails as cli_guard
from kickbase_agent.db.models import Base

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """Arbeitsplatz: Sicherungs-Test im Baum, Werkzeuge da, Git-Repo drumherum."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.py").write_text("assert True\n", encoding="utf-8")
    monkeypatch.setattr(gl, "GUARDED_TESTS", ("tests/a.py",))
    monkeypatch.setattr(gl, "project_root", lambda start=None: tmp_path)
    monkeypatch.setattr(de, "_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(de, "_probe_run", lambda cmd, cwd: (0, "true\n"))
    return tmp_path


@pytest.fixture
def container(repo: Path, monkeypatch) -> Path:
    """Worker-Image: dieselben Dateien, aber weder git noch Testlaeufer auf PATH."""
    monkeypatch.setattr(de, "_which", lambda name: None)
    return repo


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(cli_guard, "SessionLocal", factory)
    return factory


# ----------------------------------------------------------------- Tor


def test_seal_then_check_is_green(repo):
    sealed = runner.invoke(app, ["guardrails-seal", "--reason", "Erstversiegelung"])
    assert sealed.exit_code == 0 and "1 Sicherungs-Tests" in sealed.stdout

    checked = runner.invoke(app, ["guardrails-check", "--no-suite"])
    assert checked.exit_code == 0 and "unversehrt" in checked.stdout


def test_a_changed_guard_test_makes_the_gate_exit_nonzero(repo):
    runner.invoke(app, ["guardrails-seal", "--reason", "Erstversiegelung"])
    (repo / "tests" / "a.py").write_text("# entschaerft\n", encoding="utf-8")

    result = runner.invoke(app, ["guardrails-check", "--no-suite"])

    assert result.exit_code == 1
    assert "tests/a.py" in result.stdout


def test_without_a_manifest_the_gate_fails_closed(repo):
    result = runner.invoke(app, ["guardrails-check", "--no-suite"])
    assert result.exit_code == 1


def test_json_output_is_machine_readable(repo):
    runner.invoke(app, ["guardrails-seal", "--reason", "x"])
    result = runner.invoke(app, ["guardrails-check", "--no-suite", "--json"])
    payload = json.loads(result.stdout)
    assert payload["ok"] is True and payload["umgebung"]["ok"] is True


# ------------------------------------------------------------- Umgebungs-Tor


def test_an_intact_manifest_in_the_wrong_place_is_still_a_no(container):
    """Der gefaehrliche Fall: Pruefsummen stimmen, deployen kann man hier trotzdem nicht.

    Genau so saehe es aus, wenn jemand `guardrails.lock` und `tests/` ins Image
    kopierte: ein zweites, halbes Tor. Ohne git bleibt der Deploy unmoeglich.
    """
    runner.invoke(app, ["guardrails-seal", "--reason", "Erstversiegelung"])

    result = runner.invoke(app, ["guardrails-check", "--no-suite"])

    assert result.exit_code == 1
    assert "unversehrt" in result.stdout          # die Pruefsummen stimmen
    assert "keinen Selbst-Deploy" in result.stdout  # und trotzdem: nein
    assert "Arbeitsplatz" in result.stdout        # samt Adresse, wo es hingehoert


def test_the_environment_verdict_is_in_the_json_too(container):
    runner.invoke(app, ["guardrails-seal", "--reason", "x"])
    payload = json.loads(
        runner.invoke(app, ["guardrails-check", "--no-suite", "--json"]).stdout
    )
    assert payload["ok"] is False
    assert payload["umgebung"]["checks"]["git"] is False
    assert payload["umgebung"]["checks"]["manifest"] is True


def test_sealing_without_a_reason_is_refused(repo):
    assert runner.invoke(app, ["guardrails-seal"]).exit_code != 0


# ----------------------------------------------------------------- Einfrieren


def test_freeze_status_release_roundtrip(db):
    """Der Rueckgabe-Text wird NOCH in der Session gelesen — sonst detached."""
    assert "Nicht eingefroren" in runner.invoke(app, ["freeze-status"]).stdout

    frozen = runner.invoke(app, ["freeze", "--reason", "Probe"])
    assert frozen.exit_code == 0 and "EINGEFROREN" in frozen.stdout

    released = runner.invoke(app, ["unfreeze", "--by", "emil", "--note", "geprueft"])
    assert released.exit_code == 0
    assert "Entsperrt durch emil" in released.stdout and "Probe" in released.stdout

    assert "Nicht eingefroren" in runner.invoke(app, ["freeze-status"]).stdout


def test_unfreezing_nothing_says_so(db):
    result = runner.invoke(app, ["unfreeze", "--by", "emil"])
    assert result.exit_code == 0 and "nichts eingefroren" in result.stdout
