"""POST /api/refresh — Cockpit-Build auf Zuruf (Cooldown, Background, CSRF)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kickbase_agent.server import routes
from kickbase_agent.server.app import create_app
from kickbase_agent.server.routes import refresh as rf


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Echter Refresh-Pfad (inkl. finally: token++/error) — nur der teure
    Cockpit-Build ist ersetzt. Vorher patchte der Test `_build` selbst weg und
    prüfte damit an der eigentlichen Logik vorbei (Fund 25.08.)."""
    import kickbase_agent.worker.cockpit_job as cj

    html = tmp_path / "index.html"
    monkeypatch.setenv("COCKPIT_HTML_PATH", str(html))
    monkeypatch.setattr(
        rf, "_running", {"active": False, "token": 0, "error": None, "started_at": None}
    )

    async def fake_build() -> str:
        html.write_text("neu")
        return str(html)

    monkeypatch.setattr(cj, "run_cockpit_build", fake_build)
    app = create_app()
    return TestClient(app, headers={"X-Requested-With": "kba"}), html


def test_refresh_requires_csrf_header(client):
    c, _ = client
    raw = TestClient(c.app)
    assert raw.post("/api/refresh").status_code == 403


def test_refresh_starts_build_when_stale_or_missing(client):
    c, html = client
    r = c.post("/api/refresh")
    assert r.status_code == 200 and r.json()["started"] is True
    assert html.read_text() == "neu"  # TestClient fuehrt BackgroundTasks aus


def test_refresh_cooldown_when_fresh(client, monkeypatch):
    c, html = client
    html.write_text("frisch")
    r = c.post("/api/refresh")
    assert r.status_code == 429
    assert "Cooldown" in r.json()["detail"]
    assert r.json()["age_s"] < rf.REFRESH_COOLDOWN_S


def test_refresh_running_guard(client):
    c, _ = client
    rf._running["active"] = True
    r = c.post("/api/refresh")
    assert r.status_code == 429 and r.json().get("running") is True


def test_refresh_without_target_is_503(client, monkeypatch):
    c, _ = client
    monkeypatch.delenv("COCKPIT_HTML_PATH")
    r = c.post("/api/refresh")
    assert r.status_code == 503


def test_refresh_route_registered():
    assert hasattr(routes, "refresh")


# --- Emils Fund 25.08.: „Aktualisieren" muss BEWEISBAR wirken, nicht blind warten ---


def test_status_reports_idle_and_age(client):
    c, html = client
    html.write_text("da")
    st = c.get("/api/refresh/status").json()
    assert st["running"] is False and st["target_configured"] is True
    assert st["age_s"] is not None and st["error"] is None


def test_token_increments_after_finished_build(client):
    """Der Token ist der Beweis „ein Build ist fertig geworden" — daran laedt die
    Seite neu, statt nach 2,5 min blind zu reloaden."""
    c, html = client
    before = c.get("/api/refresh/status").json()["token"]
    assert c.post("/api/refresh").status_code == 200
    after = c.get("/api/refresh/status").json()["token"]
    assert after == before + 1


def test_cooldown_answer_marks_page_as_fresh(client):
    """429 wegen Cooldown sagt jetzt `fresh` — die Seite laedt sofort neu,
    statt sinnlos zu warten."""
    c, html = client
    html.write_text("frisch")
    body = c.post("/api/refresh").json()
    assert body["fresh"] is True and "token" in body


def test_failed_build_is_visible_in_status(client, monkeypatch):
    """Ein kaputter Build darf nicht still enden — Status nennt den Grund,
    damit die Seite nicht ewig „Baue…" zeigt."""
    import kickbase_agent.worker.cockpit_job as cj

    c, _ = client

    async def boom() -> str:
        raise RuntimeError("LigaInsider blockt")

    monkeypatch.setattr(cj, "run_cockpit_build", boom)
    assert c.post("/api/refresh").status_code == 200
    st = c.get("/api/refresh/status").json()
    assert st["running"] is False
    assert "LigaInsider" in (st["error"] or "")
    assert st["token"] == 1  # auch der Fehlversuch zaehlt als beendeter Build
