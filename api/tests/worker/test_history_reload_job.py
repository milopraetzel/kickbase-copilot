"""Paket 1, P0-10: Job-Hülle des wöchentlichen Voll-Nachladens im Worker."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kickbase_agent.worker import __main__ as worker_main


def _world(monkeypatch, result, league_id="1234567"):
    calls: list[dict] = []

    async def factory():
        return object(), SimpleNamespace(league_id=league_id, user_id="u")

    async def reload(client, session, **kwargs):
        calls.append(kwargs)
        return result

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(worker_main, "make_kickbase_client", factory)
    monkeypatch.setattr(worker_main, "run_full_history_reload", reload)
    monkeypatch.setattr(worker_main, "SessionLocal", _Session)
    return calls


async def test_reload_job_passes_the_league_and_returns_the_result(monkeypatch):
    result = {"status": "ok", "players": 3, "failed_ids": []}
    calls = _world(monkeypatch, result)
    assert await worker_main.run_history_reload_job() == result
    assert calls == [{"league_id": "1234567"}]


async def test_failure_series_turns_the_job_red(monkeypatch):
    _world(monkeypatch, {
        "status": "aborted_failures", "players": 430, "done": 10,
        "remaining": 420, "failed_ids": [str(i) for i in range(10)],
    })
    with pytest.raises(RuntimeError, match="10 von 430"):
        await worker_main.run_history_reload_job()


async def test_pause_at_the_block_window_is_no_failure(monkeypatch):
    result = {"status": "paused_window", "players": 430, "done": 200,
              "remaining": 230, "failed_ids": []}
    _world(monkeypatch, result)
    assert await worker_main.run_history_reload_job() == result


async def test_without_a_league_nothing_is_loaded(monkeypatch):
    calls = _world(monkeypatch, {"status": "ok"}, league_id=None)
    assert await worker_main.run_history_reload_job() == {"status": "no_league"}
    assert calls == []
