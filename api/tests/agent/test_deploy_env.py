"""Umgebungs-Tor: Läuft der Selbst-Deploy überhaupt dort, wo er gestartet wurde?

Das Tor vor dem Selbstumbau (`kba guardrails-check`) prüft Prüfsummen und Suite.
Beides setzt still voraus, dass Manifest, Testsuite und ein Git-Repo überhaupt da
sind. Im Worker-Image sind sie es nicht: Der Dockerfile kopiert `pyproject.toml`,
`src/`, `alembic.ini` und den Entrypoint — weder `guardrails.lock` noch `tests/`;
`python:3.12-slim` bringt weder `git` noch `rsync` noch `ssh` mit (geprüft 26.08.).
Ein Tor, das in der Zielumgebung gar nicht laufen kann, ist kein Tor.

Diese Tests halten die Grenze fest: In einer Umgebung ohne Manifest, ohne Suite
oder ohne Git-Repo gibt es keinen Selbst-Deploy — Abbruch mit Begründung, nie ein
stilles Weiter und nie ein Traceback statt eines Urteils.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kickbase_agent.agent import deploy_env as de
from kickbase_agent.agent import guardrail_lock as gl

FILES = ("tests/a.py", "tests/b.py")


def _full(name: str) -> str | None:
    """`which`, das jedes Werkzeug findet — die Werkbank, nicht der Container."""
    return f"/usr/bin/{name}"


def _nothing(name: str) -> str | None:
    """`which` im Worker-Image: weder git noch rsync noch uv."""
    return None


def _in_a_repo(cmd, cwd=None) -> tuple[int, str]:
    return 0, "true\n"


def _not_a_repo(cmd, cwd=None) -> tuple[int, str]:
    return 128, "fatal: not a git repository (or any of the parent directories): .git"


@pytest.fixture
def workbench(tmp_path: Path, monkeypatch) -> Path:
    """Arbeitsplatz-Nachbau: pyproject, Sicherungs-Tests, versiegeltes Manifest."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "b.py").write_text("assert 1 == 1\n", encoding="utf-8")
    monkeypatch.setattr(gl, "GUARDED_TESTS", FILES)
    gl.write_lock(tmp_path, files=FILES)
    return tmp_path


@pytest.fixture
def container(tmp_path: Path, monkeypatch) -> Path:
    """Worker-Image-Nachbau: /app hat pyproject und src — sonst nichts."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(gl, "GUARDED_TESTS", FILES)
    return tmp_path


# ----------------------------------------------------------------- grün


def test_a_workbench_with_manifest_suite_and_git_passes(workbench: Path):
    report = de.check_environment(root=workbench, which=_full, run=_in_a_repo)
    assert report.ok and report.problems == []
    assert report.checks == {"manifest": True, "suite": True, "git": True}


def test_the_real_repository_carries_a_self_deploy():
    """Kein Nachbau: Der Checkout, in dem diese Suite läuft, MUSS das Tor bestehen.

    Schlägt das fehl, ist entweder das Manifest weg, ein Sicherungs-Test fehlt oder
    hier ist gar kein Git-Repo — dann darf sich der Agent hier nicht umbauen.
    """
    report = de.check_environment()
    assert report.ok, report.problems


# ----------------------------------------------------------------- rot


def test_without_a_manifest_the_environment_is_refused(workbench: Path):
    (workbench / gl.LOCK_FILENAME).unlink()
    report = de.check_environment(root=workbench, which=_full, run=_in_a_repo)
    assert not report.ok and report.checks["manifest"] is False
    assert any("Manifest" in p for p in report.problems)


def test_an_empty_manifest_is_no_manifest(workbench: Path):
    (workbench / gl.LOCK_FILENAME).write_text('{"version": 1, "files": {}}', encoding="utf-8")
    report = de.check_environment(root=workbench, which=_full, run=_in_a_repo)
    assert not report.ok and report.checks["manifest"] is False


def test_without_the_test_suite_the_environment_is_refused(workbench: Path):
    (workbench / "tests" / "b.py").unlink()
    report = de.check_environment(root=workbench, which=_full, run=_in_a_repo)
    assert not report.ok and report.checks["suite"] is False
    assert any("tests/b.py" in p for p in report.problems)


def test_without_a_test_runner_the_environment_is_refused(workbench: Path):
    """`uv` fehlt: Eine Suite, die man nicht starten kann, ist keine Suite."""
    runner = gl.SUITE_COMMAND[0]
    report = de.check_environment(
        root=workbench, which=lambda n: None if n == runner else _full(n), run=_in_a_repo
    )
    assert not report.ok and report.checks["suite"] is False
    assert any(runner in p for p in report.problems)


def test_without_git_the_environment_is_refused(workbench: Path):
    report = de.check_environment(
        root=workbench, which=lambda n: None if n == "git" else _full(n), run=_in_a_repo
    )
    assert not report.ok and report.checks["git"] is False
    assert any("git" in p for p in report.problems)


def test_a_directory_that_is_no_repository_is_refused(workbench: Path):
    """git ist da, der Baum ist trotzdem kein Repo — genau /opt auf der Box."""
    report = de.check_environment(root=workbench, which=_full, run=_not_a_repo)
    assert not report.ok and report.checks["git"] is False
    assert any("Git-Repo" in p for p in report.problems)


def test_the_worker_image_fails_every_single_check(container: Path):
    """Der belegte Fall: /app hat kein Manifest, keine Suite, kein git."""
    report = de.check_environment(root=container, which=_nothing, run=_in_a_repo)
    assert not report.ok
    assert report.checks == {"manifest": False, "suite": False, "git": False}
    assert len(report.problems) >= 3


# ----------------------------------------------------------------- kein Traceback


def test_a_crashing_git_probe_is_a_verdict_not_a_traceback(workbench: Path):
    def explode(cmd, cwd=None):
        raise OSError("Exec format error")

    report = de.check_environment(root=workbench, which=_full, run=explode)
    assert not report.ok and report.checks["git"] is False


def test_a_tree_without_a_project_root_is_a_verdict_not_a_traceback(tmp_path: Path):
    """Ohne pyproject.toml findet `project_root()` nichts — auch das ist ein Nein."""
    report = de.check_environment(root=None, which=_full, run=_in_a_repo, start=tmp_path)
    assert not report.ok and any("Projektwurzel" in p for p in report.problems)


# ----------------------------------------------------------------- Ausgabe


def test_the_report_is_readable_for_humans_and_machines(container: Path):
    report = de.check_environment(root=container, which=_nothing, run=_in_a_repo)
    payload = report.as_dict()
    assert payload["ok"] is False and payload["checks"]["git"] is False
    assert payload["problems"] and payload["summary"] == report.summary
    assert "Selbst-Deploy" in report.summary


def test_the_alarm_names_the_place_where_a_self_deploy_belongs(container: Path):
    report = de.check_environment(root=container, which=_nothing, run=_in_a_repo)
    alarm = de.format_environment_alarm(report)
    assert "Selbst-Deploy" in alarm and "Arbeitsplatz" in alarm
    assert str(container) in alarm
