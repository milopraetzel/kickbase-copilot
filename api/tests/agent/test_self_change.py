"""Selbstumbau: der Weg, den eine Änderung des Agenten an sich selbst gehen muss.

Jeder Test hier fragt dasselbe: Wo bricht der Weg ab — und bleibt dabei etwas
Halbfertiges zurück? Die teuerste Antwort wäre „ein Commit ist drin, der Deploy
aber nicht geprüft".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.agent import deploy_env as de
from kickbase_agent.agent import guardrail_lock as gl
from kickbase_agent.agent import self_change as sc
from kickbase_agent.agent import self_deploy as sd
from kickbase_agent.db.models import Base

NOW = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)
#: 19:40 UTC = 21:40 Berlin — mitten im Deploy-Sperrfenster (die Zeitzone ist hier
#: keine Kleinigkeit: genau daran sind schon alle Crons zwei Stunden verrutscht).
DEPLOY_BLOCKED = datetime(2026, 8, 26, 19, 40, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv("AGENT_SELF_CHANGE", "1")
    import kickbase_agent.config as cfg

    cfg._settings = None
    yield
    cfg._settings = None


class FakeRun:
    def __init__(self, fail_on: str | None = None, sha: str = "abc1234def") -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on
        self.sha = sha

    def __call__(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        if self.fail_on and self.fail_on in joined:
            return 1, "boom"
        if "rev-parse" in joined:
            return 0, self.sha
        return 0, "ok"

    def did(self, needle: str) -> bool:
        return any(needle in " ".join(c) for c in self.calls)


def _green(**kw):
    return gl.GuardrailResult(ok=True, checked=7, suite_ok=True)


def _red(**kw):
    return gl.GuardrailResult(ok=False, checked=7, problems=["tests/x.py wurde geaendert"])


def _env_ok(**kw):
    """Arbeitsplatz mit Git-Checkout — der Ort, an dem ein Selbst-Deploy laeuft."""
    return de.EnvironmentReport(
        ok=True, root="/repo/api", checks={"manifest": True, "suite": True, "git": True}
    )


def _env_container(**kw):
    """Worker-Image: kein Manifest, keine Suite, kein git (Befund 26.08.)."""
    return de.EnvironmentReport(
        ok=False,
        root="/app",
        checks={"manifest": False, "suite": False, "git": False},
        problems=[
            "Manifest guardrails.lock nicht lesbar",
            "Sicherungs-Test tests/agent/test_executor.py fehlt im Baum",
            "git nicht gefunden",
        ],
    )


def _healthy():
    return sd.HealthSnapshot(container_up=True, jobs_registered=24,
                             log_errors=[], failed_jobs=[])


def _sick():
    return sd.HealthSnapshot(container_up=False, jobs_registered=0,
                             log_errors=["Traceback"], failed_jobs=["Reconcile"])


def _request(tmp_path: Path) -> sc.ChangeRequest:
    tuning = tmp_path / "tuning.json"
    tuning.write_text(json.dumps({"values": {}}), encoding="utf-8")
    history = tmp_path / "parameter_history.json"
    history.write_text(json.dumps({"entries": []}), encoding="utf-8")
    return sc.ChangeRequest(
        art="parameter",
        bereich="erwartung",
        summary="kickbase_thin_p 0.45 -> 0.40",
        surprise_ref="surprise:action:12",
        stellschraube="expectation.kickbase_thin_p",
        neuer_wert=0.40,
        reason="Zwei gleichartige Faelle in 21 Tagen",
        beleg="surprise:action:12",
        recherche="Activity-Feed 26.08.: Zuschlaege lagen bei 130-160 % MW",
        expect="weniger falsch-sichere Gebote",
        review="SP2-Retro",
        tuning_path=tuning,
        history_path=history,
    )


async def _run(req, session, **kw):
    defaults = dict(
        run=FakeRun(), guardrails=_green, environment=_env_ok, deploy=None,
        health=_healthy, post=None, session=session, now=NOW,
    )
    defaults.update(kw)
    return await sc.run_self_change(req, **defaults)


# ----------------------------------------------------------------- Riegel


@pytest.mark.asyncio
async def test_without_the_flag_nothing_happens(session, tmp_path):
    """Default AUS. Emil schaltet scharf, nicht der Agent."""
    run = FakeRun()
    result = await _run(_request(tmp_path), session, run=run)
    assert not result.ok and result.schritt == "flag"
    assert run.calls == []


@pytest.mark.asyncio
async def test_a_frozen_agent_does_not_rebuild_itself(session, tmp_path, armed):
    from kickbase_agent.agent import freeze as fz

    fz.freeze(session, reason="Tagesverlust", source=fz.SOURCE_LOSS, now=NOW)
    run = FakeRun()

    result = await _run(_request(tmp_path), session, run=run)

    assert not result.ok and result.schritt == "eingefroren"
    assert run.calls == []


@pytest.mark.asyncio
async def test_red_guardrails_stop_before_the_commit(session, tmp_path, armed):
    run = FakeRun()
    result = await _run(_request(tmp_path), session, run=run, guardrails=_red)
    assert not result.ok and result.schritt == "sicherungen"
    assert not run.did("commit")
    assert any("geaendert" in p for p in result.problems)


@pytest.mark.asyncio
async def test_the_deploy_window_is_respected(session, tmp_path, armed):
    """21:25-22:25 bleibt gesperrt: Commit ja, Deploy vertagt."""
    run = FakeRun()
    result = await _run(_request(tmp_path), session, run=run, now=DEPLOY_BLOCKED)
    assert result.schritt == "vertagt" and result.sha
    assert run.did("commit") and not result.deployed


# ----------------------------------------------------------------- Glücksfall


@pytest.mark.asyncio
async def test_a_clean_parameter_change_walks_the_whole_way(session, tmp_path, armed):
    run = FakeRun()
    req = _request(tmp_path)
    deployed: list[int] = []

    async def deploy():
        deployed.append(1)
        return True

    result = await _run(req, session, run=run, deploy=deploy)

    assert result.ok and result.schritt == "fertig" and result.sha.startswith("abc1234")
    assert deployed == [1]
    # Branch, genau EIN Marker-Commit, danach der Protokoll-Commit
    assert run.did("checkout -b")
    subjects = [c[-1] for c in run.calls if "commit" in " ".join(c)]
    assert sum(1 for s in subjects if sd.SELF_MARKER in s) == 1
    # die Zahl steht in der Datei
    values = json.loads(req.tuning_path.read_text(encoding="utf-8"))["values"]
    assert values["expectation.kickbase_thin_p"] == 0.40
    # ... und der Decision-Record im Protokoll, mit dem echten Commit
    entry = json.loads(req.history_path.read_text(encoding="utf-8"))["entries"][0]
    assert entry["parameter"] == "expectation.kickbase_thin_p"
    assert entry["commit"] == run.sha and entry["beleg"] == "surprise:action:12"


@pytest.mark.asyncio
async def test_the_commit_subject_carries_the_marker(session, tmp_path, armed):
    run = FakeRun()
    await _run(_request(tmp_path), session, run=run)
    subject = next(c[-1] for c in run.calls if "commit" in " ".join(c))
    assert sd.is_self_commit(subject) and "erwartung" in subject


# ----------------------------------------------------------------- Rückwärtsgang


@pytest.mark.asyncio
async def test_a_sick_box_triggers_the_rollback(session, tmp_path, armed):
    run = FakeRun()
    posts: list[str] = []

    async def deploy():
        return True

    async def post(text, **kw):
        posts.append(text)

    result = await _run(
        _request(tmp_path), session, run=run, deploy=deploy, health=_sick, post=post
    )

    assert not result.ok and result.schritt == "gesundheit"
    assert result.rolled_back and run.did("revert")
    assert any("rot" in p for p in posts)


@pytest.mark.asyncio
async def test_a_failed_deploy_rolls_back_too(session, tmp_path, armed):
    run = FakeRun()

    async def deploy():
        return False

    result = await _run(_request(tmp_path), session, run=run, deploy=deploy)

    assert not result.ok and result.schritt == "deploy"
    assert result.rolled_back


@pytest.mark.asyncio
async def test_a_rollback_that_fails_freezes_the_agent(session, tmp_path, armed):
    from kickbase_agent.agent import freeze as fz

    run = FakeRun(fail_on="revert")

    async def deploy():
        return True

    result = await _run(
        _request(tmp_path), session, run=run, deploy=deploy, health=_sick
    )

    assert not result.ok and not result.rolled_back
    assert fz.is_frozen(session)


# ----------------------------------------------------------------- Code-Weg


@pytest.mark.asyncio
async def test_the_code_route_runs_the_injected_edit(session, tmp_path, armed):
    run = FakeRun()
    touched: list[str] = []

    def apply_edit():
        touched.append("trading/expectation.py")
        return touched

    req = sc.ChangeRequest(
        art="code", bereich="erwartung", summary="Stufen neu geschnitten",
        surprise_ref="surprise:action:12", reason="Mechanismus belegt",
        beleg="surprise:action:12", recherche="Feed 26.08.",
    )

    result = await _run(req, session, run=run, apply=apply_edit)

    assert result.ok and touched
    # Code-Aenderungen schreiben KEINEN Parameter-Record (das waere ein Protokoll
    # ohne Stellschraube) — die Erkenntnis gehoert nach LEARNINGS.md.
    subjects = [c[-1] for c in run.calls if "commit" in " ".join(c)]
    assert len(subjects) == 1


@pytest.mark.asyncio
async def test_the_code_route_without_an_edit_is_refused(session, tmp_path, armed):
    req = sc.ChangeRequest(
        art="code", bereich="erwartung", summary="nichts", surprise_ref="s",
        reason="r", beleg="b", recherche="r",
    )
    result = await _run(req, session, apply=None)
    assert not result.ok and result.schritt == "aenderung"


@pytest.mark.asyncio
async def test_a_change_without_evidence_is_refused(session, tmp_path, armed):
    req = _request(tmp_path)
    req.beleg = ""
    result = await _run(req, session)
    assert not result.ok and result.schritt == "beleg"


# ----------------------------------------------------------------- Umgebung
#
# Der Selbst-Deploy braucht Manifest, Testsuite und ein Git-Repo. Im Worker-Image
# gibt es keines davon (Dockerfile kopiert nur pyproject/src/alembic/entrypoint;
# `python:3.12-slim` hat kein git). Ohne dieses Tor liefe der Weg dort in ein
# `FileNotFoundError: 'git'` — BEVOR `guardrails-check` je gefragt wird.


@pytest.mark.asyncio
async def test_an_environment_without_git_stops_before_the_first_git_call(
    session, tmp_path, armed
):
    run = FakeRun()
    asked: list[int] = []

    def guardrails(**kw):
        asked.append(1)
        return _green()

    result = await _run(
        _request(tmp_path), session, run=run,
        environment=_env_container, guardrails=guardrails,
    )

    assert not result.ok and result.schritt == "umgebung"
    # Kein Branch, kein Commit — gar kein git-Aufruf.
    assert run.calls == []
    # Und das Sicherungs-Tor wurde nicht einmal gefragt: Es koennte hier nichts
    # pruefen, weil weder Manifest noch Suite existieren.
    assert asked == []
    assert any("git" in p for p in result.problems)


@pytest.mark.asyncio
async def test_the_environment_gate_alarms_instead_of_returning_silently(
    session, tmp_path, armed
):
    """Ein Abbruch, den niemand sieht, ist ein stiller Ausfall."""
    posts: list[str] = []

    async def post(text, **kw):
        posts.append(text)

    result = await _run(
        _request(tmp_path), session, environment=_env_container, post=post
    )

    assert not result.ok and result.schritt == "umgebung"
    assert posts and "Selbst-Deploy" in posts[0]
    assert "Arbeitsplatz" in posts[0]


@pytest.mark.asyncio
async def test_the_environment_gate_does_not_freeze_the_agent(session, tmp_path, armed):
    """Bewusst KEIN Einfrieren: Es ist nichts halbfertig, und Handel bleibt gesund.

    Einfrieren sperrt Geld-Writes. Die falsche Umgebung sagt nichts ueber den
    Handel — wer hier einfriert, macht aus einem verweigerten Umbau einen
    verlorenen Spieltag. Eingefroren wird, wenn der Rueckwaertsgang klemmt (A2).
    """
    from kickbase_agent.agent import freeze as fz

    await _run(_request(tmp_path), session, environment=_env_container)

    assert not fz.is_frozen(session)


@pytest.mark.asyncio
async def test_the_environment_is_checked_before_the_evidence(session, tmp_path, armed):
    """Erst die Welt, dann der Antrag: „hier geht das nicht" ist die groessere Wahrheit."""
    req = _request(tmp_path)
    req.beleg = ""
    result = await _run(req, session, environment=_env_container)
    assert result.schritt == "umgebung"


@pytest.mark.asyncio
async def test_the_default_gate_refuses_a_container_like_tree(
    session, tmp_path, armed, monkeypatch
):
    """Ohne injizierte Umgebung greift die ECHTE Pruefung — sonst waere sie Deko.

    Nachbau von `/app`: pyproject und src, kein Manifest, keine Suite, kein git.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(gl, "project_root", lambda start=None: tmp_path)
    monkeypatch.setattr(de, "_which", lambda name: None)
    run = FakeRun()

    result = await sc.run_self_change(
        _request(tmp_path), run=run, guardrails=_green, deploy=None,
        health=_healthy, post=None, session=session, now=NOW,
    )

    assert not result.ok and result.schritt == "umgebung"
    assert run.calls == []
    assert len(result.problems) >= 3
