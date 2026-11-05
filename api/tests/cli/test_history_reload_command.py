"""`kba history-reload` — erster Lauf des Voll-Nachladens direkt nach dem Deploy (P0-10).

Review-Befund 21.09.2026: P0-10 verlangt den ersten Lauf nach dem Deploy; ohne Kommando
wäre der früheste Lauf der nächste Dienstag 04:10.
"""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from kickbase_agent.cli import app
from kickbase_agent.cli import snapshot as snapshot_cli


def _world(monkeypatch, result):
    calls: list[dict] = []

    async def authenticate():
        return SimpleNamespace(token="t", leagues=[SimpleNamespace(id="1234567")])

    async def reload(client, session, **kwargs):
        calls.append(kwargs)
        return result

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(snapshot_cli, "authenticate", authenticate)
    monkeypatch.setattr(snapshot_cli, "KickbaseClient", lambda token: object())
    monkeypatch.setattr(snapshot_cli, "SessionLocal", _Session)
    monkeypatch.setattr(snapshot_cli, "run_full_history_reload", reload)
    return calls


def test_history_reload_runs_once_and_reports_the_counts(monkeypatch):
    calls = _world(monkeypatch, {
        "status": "ok", "players": 430, "history_rows_added": 12_000,
        "history_rows_corrected": 9, "performance_rows_added": 800,
        "performance_rows_updated": 5, "failed_ids": ["17"], "unknown_ids": [],
        "check_only": False,
    })
    out = CliRunner().invoke(app, ["history-reload", "--pause", "0.5"])
    assert out.exit_code == 0, out.output
    assert calls == [{"league_id": "1234567", "pause_seconds": 0.5, "check_only": False}]
    assert "430 Spieler" in out.output and "12000 Tageswerte" in out.output
    assert "9 berichtigt" in out.output
    assert "1 fehlgeschlagen" in out.output


def test_history_reload_check_only_counts_and_says_nothing_was_written(monkeypatch):
    """Befund 01.10.2026: erst zählen, wie viele Zeilen veraltet sind, dann berichtigen
    (Emil gab die Berichtigung am 01.10. frei; der Snapshot 22:05 berichtigt die Zeilen seiner
    Kader- und Marktspieler ohnehin selbst — `--check` ist die Vorschau, kein Riegel)."""
    calls = _world(monkeypatch, {
        "status": "ok", "players": 430, "history_rows_added": 40,
        "history_rows_corrected": 9, "performance_rows_added": 0,
        "performance_rows_updated": 0, "failed_ids": [], "unknown_ids": [],
        "check_only": True,
    })
    out = CliRunner().invoke(app, ["history-reload", "--check"])
    assert out.exit_code == 0, out.output
    assert calls[0]["check_only"] is True
    assert "Prüfung, nichts geschrieben" in out.output
    assert "9 würden berichtigt" in out.output and "40 Tageswerte fehlen" in out.output


def test_history_reload_in_the_block_window_exits_nonzero(monkeypatch):
    _world(monkeypatch, {
        "status": "blocked_window", "players": 0, "history_rows_added": 0,
        "performance_rows_added": 0, "performance_rows_updated": 0,
        "failed_ids": [], "unknown_ids": [],
    })
    out = CliRunner().invoke(app, ["history-reload"])
    assert out.exit_code == 1
    assert "21:25" in out.output


def test_a_check_that_did_not_finish_says_so_and_exits_nonzero(monkeypatch):
    """Review 01.10.: Eine abgebrochene Prüfung las sich wie „0 würden berichtigt“."""
    _world(monkeypatch, {
        "status": "aborted_failures", "players": 430, "done": 10, "remaining": 420,
        "history_rows_added": 0, "history_rows_corrected": 0, "performance_rows_added": 0,
        "performance_rows_updated": 0, "failed_ids": [str(i) for i in range(10)],
        "unknown_ids": [], "check_only": True,
    })
    out = CliRunner().invoke(app, ["history-reload", "--check"])
    assert out.exit_code == 1
    assert "nicht fertig" in out.output and "10 von 430" in out.output
