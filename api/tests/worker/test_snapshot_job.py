"""Job-Hülle des täglichen Snapshots 22:05: Ergebnis im Log (Befund 01.10.2026).

Ob Kickbases Update um 22:05 schon im Verlauf stand, sieht man nur am Ergebnis des Laufs —
`job_runs` kennt nur ok/failed. Die Hülle schreibt es deshalb ins Log.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from kickbase_agent.jobs.snapshot import SnapshotResult
from kickbase_agent.worker import __main__ as worker_main


async def test_the_snapshot_job_logs_late_updates_and_corrections(monkeypatch, caplog):
    result = SnapshotResult(53, 44, 0, history_rows_corrected=2, history_missing_today=9)
    calls: list[str] = []

    async def factory():
        return object(), SimpleNamespace(league_id="1234567", user_id="u")

    async def snapshot(client, session, league_id):
        calls.append(league_id)
        return result

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(worker_main, "make_kickbase_client", factory)
    monkeypatch.setattr(worker_main, "run_snapshot", snapshot)
    monkeypatch.setattr(worker_main, "SessionLocal", _Session)

    with caplog.at_level(logging.INFO):
        assert await worker_main.run_snapshot_job() == result

    assert calls == ["1234567"]
    assert "9 ohne heutigen Wert" in caplog.text and "2 berichtigt" in caplog.text


async def test_without_a_league_the_snapshot_job_does_nothing(monkeypatch):
    async def factory():
        return object(), SimpleNamespace(league_id=None, user_id=None)

    monkeypatch.setattr(worker_main, "make_kickbase_client", factory)
    assert await worker_main.run_snapshot_job() is None
