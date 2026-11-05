"""Paket 1, P0-9 (Studie 19.09.2026): Git-Riegel vor dem Deploy.

`deploy_verify` schickte api/ per rsync so, wie es auf der Platte lag — ohne jede
Git-Prüfung. Produktion lief 09.–11.09. ohne Commit, im Repo arbeitet eine zweite
Session, im Stash liegt ein zweimal gestoppter Override. Jetzt: nur ein sauberer
`main`, der `origin/main` entspricht, geht auf die Box; der Deploy hinterlässt
Commit-Hash und Prüfsumme der Schreibstelle als Bericht.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Report
from kickbase_agent.mcp import _util, tools_ops
from kickbase_agent.mcp._util import API_DIR, CmdResult
from kickbase_agent.notify import report_store


class Runner:
    """Ersetzt _util.run_cmd (einziger Mock-Punkt): protokolliert argv, liefert Antworten."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.results: list[CmdResult] = []

    def __call__(self, argv: list[str], timeout: float) -> CmdResult:
        self.calls.append(list(argv))
        return self.results.pop(0) if self.results else ok("")


def ok(out: str) -> CmdResult:
    return CmdResult(rc=0, out=out, err="", duration_s=0.1)


BERLIN = ZoneInfo("Europe/Berlin")
HEAD = "742e2fc0aa11bb22cc33dd44ee55ff6677889900"
EXECUTOR_SHA = hashlib.sha256(
    (API_DIR / "src/kickbase_agent/agent/executor.py").read_bytes()
).hexdigest()


def _verify(worker="2", api_count="2", executor=EXECUTOR_SHA):
    return (
        f"@@marker0\n{worker}\n@@apimarker0\n{api_count}\n"
        f"@@executor\n{executor}  /app/src/kickbase_agent/agent/executor.py\n"
        "@@status\nUp 30 seconds\n@@jobs\n13\n@@errors\n0\n"
    )


VERIFY_OK = _verify()


@pytest.fixture
def runner(monkeypatch):
    r = Runner()
    monkeypatch.setattr(_util, "run_cmd", r)
    daytime = datetime(2026, 9, 22, 10, 0, tzinfo=BERLIN)
    monkeypatch.setattr(tools_ops, "berlin_now", lambda: daytime)
    return r


def _git(status="", branch="main\n", fetch=None, heads=f"{HEAD}\n{HEAD}\n"):
    return [ok(status), ok(branch), fetch or ok(""), ok(heads)]


def _no_transfer(runner):
    return not any(argv[0] in ("rsync", "ssh") for argv in runner.calls)


async def test_modified_tracked_file_refuses_before_any_transfer(runner):
    runner.results = _git(status=" M api/src/kickbase_agent/cli/plan.py\n")
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "refused" and out["step"] == "git"
    assert "api/src/kickbase_agent/cli/plan.py" in out["reason"]
    assert _no_transfer(runner)
    assert runner.calls[0][0] == "git" and "--porcelain" in runner.calls[0]


async def test_untracked_file_inside_api_refuses_because_rsync_would_ship_it(runner):
    runner.results = _git(status="?? api/src/kickbase_agent/worker/heimlich.py\n")
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "refused" and "heimlich.py" in out["reason"]
    assert _no_transfer(runner)


async def test_untracked_file_outside_api_never_reaches_the_box_and_is_only_reported(runner):
    runner.results = [*_git(status="?? docs/NOTIZ.md\n"), ok(""), ok(""), ok(VERIFY_OK)]
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "ok"
    assert out["git"]["ignored_untracked"] == ["docs/NOTIZ.md"]


async def test_branch_other_than_main_refuses(runner):
    runner.results = _git(branch="worktree-paket-1-turnaround\n")
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "refused" and "worktree-paket-1-turnaround" in out["reason"]
    assert _no_transfer(runner)


async def test_unpushed_head_refuses(runner):
    runner.results = _git(heads=f"{HEAD}\n{'0' * 40}\n")
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "refused" and "origin/main" in out["reason"]
    assert HEAD[:7] in out["reason"]
    assert _no_transfer(runner)


async def test_failed_fetch_refuses_instead_of_trusting_a_stale_origin(runner):
    runner.results = _git(fetch=CmdResult(rc=128, out="", err="could not resolve", duration_s=1))
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "refused" and "fetch" in out["reason"]
    assert _no_transfer(runner)


async def test_clean_main_deploys_and_records_commit_and_executor_checksum(
    runner, monkeypatch, world
):
    monkeypatch.setattr(report_store, "SessionLocal", world.sessions)
    runner.results = [
        *_git(), ok("sent"), ok("worker Started"), ok(VERIFY_OK),
        ok("Bericht #7 (deploy, mcp:deploy_verify) gespeichert.\n"),
    ]
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "ok"
    expected = hashlib.sha256(
        (API_DIR / "src/kickbase_agent/agent/executor.py").read_bytes()
    ).hexdigest()
    assert out["git"]["commit"] == HEAD
    assert out["git"]["executor_sha256"] == expected
    assert out["deploy_report"]["box"] == {
        "ok": True, "report_id": 7,
        "output": "Bericht #7 (deploy, mcp:deploy_verify) gespeichert.",
    }
    with Session(world.engine, future=True) as s:
        (row,) = s.execute(select(Report)).scalars()
    assert row.kind == "deploy" and row.source == "mcp:deploy_verify"
    assert row.meta["commit"] == HEAD and row.meta["executor_sha256"] == expected
    assert row.meta["status"] == "ok" and row.meta["markers"] == {"marker": 2}
    assert HEAD in row.body and expected in row.body
    mirror = runner.calls[-1][-1]
    assert "kba report-add --kind deploy --source mcp:deploy_verify" in mirror


async def test_order_is_git_gate_then_rsync(runner):
    runner.results = [*_git(), ok(""), ok(""), ok(VERIFY_OK)]
    await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert [argv[0] for argv in runner.calls[:5]] == ["git", "git", "git", "git", "rsync"]
    assert "fetch" in runner.calls[2] and runner.calls[2][-2:] == ["origin", "main"]


async def test_deploy_rebuilds_worker_and_api_because_both_run_the_same_code(runner):
    """Review-Befund 21.09.: Nur der Worker wurde gebaut. Die via-Whitelist (P0-8) lebt
    im API-Container — der Marker fand sich im Worker, der Bericht sagte „ok", die API
    lief ohne Stolperdraht weiter."""
    runner.results = [*_git(), ok(""), ok(""), ok(VERIFY_OK)]
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "ok"
    compose = runner.calls[5][-1]
    assert compose.endswith("docker compose --env-file api/.env up -d --build worker api")
    verify = runner.calls[6][-1]
    assert "docker exec kickbase-api grep -rlF --include=*.py -- marker /app/src" in verify
    assert out["markers_api"] == {"marker": 2}


async def test_marker_missing_in_the_api_container_is_not_ok(runner):
    runner.results = [*_git(), ok(""), ok(""), ok(_verify(api_count="0"))]
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "check" and out["all_markers_found"] is False
    assert out["markers"] == {"marker": 2} and out["markers_api"] == {"marker": 0}


async def test_executor_checksum_in_the_container_must_match_the_commit(runner):
    runner.results = [*_git(), ok(""), ok(""), ok(_verify(executor="f" * 64))]
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "check"
    assert out["executor_matches_box"] is False
    assert out["git"]["executor_sha256"] == EXECUTOR_SHA


async def test_failed_compose_still_leaves_a_deploy_record(runner, monkeypatch, world):
    """Nach dem rsync liegt neuer Code auf der Box — auch ein gescheiterter Bau ist ein
    Ereignis, das einen Beleg braucht."""
    monkeypatch.setattr(report_store, "SessionLocal", world.sessions)
    runner.results = [
        *_git(), ok("sent"), CmdResult(rc=1, out="", err="build failed", duration_s=3),
    ]
    out = await tools_ops.deploy_verify(["marker"], settle_seconds=0)
    assert out["status"] == "failed" and out["step"] == "compose"
    with Session(world.engine, future=True) as s:
        (row,) = s.execute(select(Report)).scalars()
    assert row.kind == "deploy" and row.meta["status"] == "failed_compose"
    assert row.meta["commit"] == HEAD
