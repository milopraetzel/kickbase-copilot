"""Tests-als-Tor: das Manifest der Sicherungs-Tests und seine Prüfung.

Der Sinn dieser Schicht in einem Satz: Der Agent darf sich selbst umbauen, aber er
darf dabei nicht die Tests entschärfen, die ihn bremsen. Deshalb sind genau die
Sicherungs-Tests prüfsummen-versiegelt — und `kba guardrails-check` verweigert den
Selbst-Deploy, sobald eine dieser Dateien anders aussieht als beim Versiegeln.

ACHTUNG, SELBSTBEZUG: Seit dem 26.08.2026 steht DIESE Datei selbst in
`GUARDED_TESTS`. Sie prüft also auch ihre eigene Unversehrtheit. Das ist kein
Versehen, sondern der Punkt — sonst ließe sich die Prüfung heimlich entschärfen.
Praktische Folge für jede spätere Änderung hier: danach von Hand
`cd api && uv run kba guardrails-seal --reason "…"`, sonst ist das Tor rot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kickbase_agent.agent import guardrail_lock as gl


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Minimaler Fake-Repo-Baum: pyproject + zwei „Sicherungs-Tests"."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "b.py").write_text("assert 1 == 1\n", encoding="utf-8")
    return tmp_path


FILES = ("tests/a.py", "tests/b.py")


# ----------------------------------------------------------------- Manifest


def test_manifest_lists_every_guarded_file_with_its_digest(repo: Path):
    manifest = gl.build_manifest(repo, FILES)
    assert manifest["version"] == gl.LOCK_VERSION
    assert set(manifest["files"]) == set(FILES)
    assert manifest["files"]["tests/a.py"] == gl.digest(repo / "tests" / "a.py")
    assert all(len(v) == 64 for v in manifest["files"].values())


def test_write_and_load_lock_roundtrip(repo: Path):
    path = gl.write_lock(repo, files=FILES)
    assert path == repo / gl.LOCK_FILENAME
    assert gl.load_lock(path)["files"] == gl.build_manifest(repo, FILES)["files"]


def test_load_lock_on_broken_json_raises_lock_error(repo: Path):
    path = repo / gl.LOCK_FILENAME
    path.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(gl.LockError):
        gl.load_lock(path)


def test_load_lock_missing_file_raises_lock_error(repo: Path):
    with pytest.raises(gl.LockError):
        gl.load_lock(repo / "gibt-es-nicht.lock")


# ----------------------------------------------------------------- Prüfung


def test_unchanged_files_produce_no_problems(repo: Path):
    lock = gl.load_lock(gl.write_lock(repo, files=FILES))
    assert gl.verify_manifest(lock, repo, expected=FILES) == []


def test_changed_guard_test_is_detected(repo: Path):
    lock = gl.load_lock(gl.write_lock(repo, files=FILES))
    (repo / "tests" / "a.py").write_text("assert True  # entschaerft\n", encoding="utf-8")
    problems = gl.verify_manifest(lock, repo, expected=FILES)
    assert len(problems) == 1 and "tests/a.py" in problems[0]
    assert "geaendert" in problems[0]


def test_deleted_guard_test_is_detected(repo: Path):
    lock = gl.load_lock(gl.write_lock(repo, files=FILES))
    (repo / "tests" / "b.py").unlink()
    problems = gl.verify_manifest(lock, repo, expected=FILES)
    assert len(problems) == 1 and "fehlt" in problems[0]


def test_entry_quietly_removed_from_the_manifest_is_detected(repo: Path):
    """Der heimtückischste Weg: nicht den Test ändern, sondern seinen Eintrag.

    Ohne diese Prüfung könnte sich der Agent freischalten, indem er einen
    Sicherungs-Test aus dem Manifest streicht statt ihn zu entschärfen.
    """
    path = gl.write_lock(repo, files=FILES)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"].pop("tests/b.py")
    path.write_text(json.dumps(data), encoding="utf-8")
    problems = gl.verify_manifest(gl.load_lock(path), repo, expected=FILES)
    assert len(problems) == 1 and "nicht im Manifest" in problems[0]


# ----------------------------------------------------------------- check()


def _ok_runner(cmd, cwd):
    return 0, "1108 passed"


def _fail_runner(cmd, cwd):
    return 1, "1 failed, 1107 passed"


def test_check_is_green_when_files_match_and_suite_passes(repo: Path):
    gl.write_lock(repo, files=FILES)
    result = gl.check(root=repo, expected=FILES, runner=_ok_runner)
    assert result.ok and result.suite_ok and result.problems == []
    assert result.checked == 2


def test_check_fails_when_the_suite_is_red(repo: Path):
    gl.write_lock(repo, files=FILES)
    result = gl.check(root=repo, expected=FILES, runner=_fail_runner)
    assert not result.ok and not result.suite_ok
    assert "Suite" in result.summary


def test_check_does_not_even_run_the_suite_when_the_manifest_is_broken(repo: Path):
    """Ein manipuliertes Manifest ist ein Abbruchgrund, kein Testlauf-Detail."""
    gl.write_lock(repo, files=FILES)
    (repo / "tests" / "a.py").write_text("# entschaerft\n", encoding="utf-8")
    calls: list = []

    def spy(cmd, cwd):
        calls.append(cmd)
        return 0, ""

    result = gl.check(root=repo, expected=FILES, runner=spy)
    assert not result.ok and calls == []


def test_check_without_lock_file_fails_closed(repo: Path):
    result = gl.check(root=repo, expected=FILES, runner=_ok_runner)
    assert not result.ok and any("Manifest" in p for p in result.problems)


def test_check_can_skip_the_suite_for_a_quick_look(repo: Path):
    gl.write_lock(repo, files=FILES)
    result = gl.check(root=repo, expected=FILES, runner=_fail_runner, run_suite=False)
    assert result.ok and result.suite_ok is None


def test_a_manifest_over_zero_files_is_never_green(repo: Path):
    """Der einzige stille Weg zu „ok": ein leeres Manifest und nichts, was es fordert.

    `check(expected=())` ueber `{"files": {}}` meldete „Sicherungen unversehrt
    (0 Dateien)" — formal richtig, inhaltlich das Gegenteil eines Tores.
    """
    (repo / gl.LOCK_FILENAME).write_text('{"version": 1, "files": {}}', encoding="utf-8")
    result = gl.check(root=repo, expected=(), runner=_ok_runner)
    assert not result.ok and result.checked == 0
    assert any("leer" in p for p in result.problems)


def test_a_runner_that_cannot_start_is_a_verdict_not_a_traceback(repo: Path):
    """Fehlt `uv`, wirft subprocess FileNotFoundError — ein Tor ohne Urteil.

    Genau das passiert in einer Umgebung, in der die Suite gar nicht existiert.
    Kein Urteil ist schlechter als ein Nein: Der Aufrufer bekaeme eine Ausnahme
    statt eines `ok=False`, das er auswerten kann.
    """
    gl.write_lock(repo, files=FILES)

    def missing_binary(cmd, cwd):
        raise FileNotFoundError(f"No such file or directory: {cmd[0]!r}")

    result = gl.check(root=repo, expected=FILES, runner=missing_binary)
    assert not result.ok and result.suite_ok is False
    assert "uv" in result.suite_output


def test_the_real_subprocess_runner_survives_a_missing_binary(tmp_path: Path):
    code, output = gl._subprocess_runner(("kba-gibt-es-nicht",), tmp_path)
    assert code != 0 and "kba-gibt-es-nicht" in output


# ------------------------------------------------- das echte Repo-Manifest


def test_the_real_lock_covers_every_guard_test_and_matches_the_repo():
    """Kein Fake: das mitgelieferte `api/guardrails.lock` muss zum Baum passen.

    Schlägt dieser Test fehl, wurde ein Sicherungs-Test geändert, ohne das
    Manifest bewusst neu zu versiegeln (`kba guardrails-seal --reason ...`).
    """
    root = gl.project_root()
    lock = gl.load_lock(root / gl.LOCK_FILENAME)
    assert set(lock["files"]) == set(gl.GUARDED_TESTS)
    assert gl.verify_manifest(lock, root) == []


def test_the_guarded_set_names_every_promised_area():
    """Jeder zugesagte Bereich ist vertreten — die Zusage ist die Liste, nicht ihre Länge.

    Hieß bis zum 26.08.2026 `..._the_four_promised_areas`. Der Name war da schon
    überholt (mit dem Selbst-Einfrieren war ein fünfter Bereich dazugekommen) und
    wurde es mit dem Selbst-Deploy-Tor endgültig. Bewusst umbenannt statt
    weitergezählt: Ein Name, der eine Zahl behauptet, altert bei jeder
    Erweiterung und lädt dazu ein, die Zahl statt der Liste zu pflegen.

    Die Zusicherung wurde dabei nur strenger, nie lockerer — es kam ein Bereich
    hinzu (Selbst-Deploy-Tor), es fiel keiner weg.
    """
    joined = " ".join(gl.GUARDED_TESTS)
    assert "test_finance" in joined          # 33-%-Regel
    assert "test_executor" in joined         # Interlocks + Preflight + Vereinslimit
    assert "test_kickoff_guard" in joined    # Konto >= 0 / Anpfiff-Notbremse
    assert "test_policies" in joined         # Vereinslimit in der Engine
    assert "test_freeze" in joined           # Selbst-Einfrieren (A2)
    assert "test_deploy_env" in joined       # Selbst-Deploy-Tor (Umgebung)
    assert "test_guardrail_lock" in joined   # die Siegelpruefung selbst


def test_the_deploy_gate_and_its_own_check_are_sealed_together():
    """Ein Tor, dessen Pruefung frei editierbar ist, ist ein Tuerrahmen.

    Beide Eintraege gehoeren zusammen und sind einzeln wertlos: Waere nur
    `test_deploy_env.py` versiegelt, koennte ein Selbstumbau das Tor
    (`agent/deploy_env.py`) entschaerfen und diese Datei hier — die es merken
    wuerde — im selben Zug mit „aufraeumen". Waere nur diese Datei versiegelt,
    fiele das Tor unbemerkt.

    Der zweite Eintrag ist ein SELBSTBEZUG (diese Datei versiegelt sich selbst).
    Wer ihn fuer einen Fehler haelt und entfernt, oeffnet genau die Luecke
    wieder — deshalb steht er hier als benannte Zusage und nicht nur als
    Kommentar.
    """
    assert "tests/agent/test_deploy_env.py" in gl.GUARDED_TESTS
    assert "tests/agent/test_guardrail_lock.py" in gl.GUARDED_TESTS
