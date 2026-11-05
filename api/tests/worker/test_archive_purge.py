"""Aufräumen 03:33: flüchtige Berichte UND das Jev-Protokoll (Spec 2026-10-01).

Ohne diesen Job wüchse `jev_judgments` mit jedem Planlauf; gelöscht wird nur, was älter
als 90 Tage ist — die Jev-Seite zeigt höchstens 30 Tage, die Treffer-Bilanz Wochen.
"""

from __future__ import annotations

from kickbase_agent.worker import __main__ as worker_main


def test_the_nightly_cleanup_purges_reports_and_the_jev_journal(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(worker_main, "purge_old_reports", lambda: calls.append("reports") or 2)
    monkeypatch.setattr(worker_main, "purge_old_judgments", lambda: calls.append("jev") or 5)

    assert worker_main.purge_archives() == {"berichte": 2, "jev": 5}
    assert calls == ["reports", "jev"]
