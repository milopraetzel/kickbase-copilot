"""Der Operator bekommt dieselbe datierte Auswertung wie die Web-App."""
import json

from fastapi.testclient import TestClient

from kickbase_agent import config
from kickbase_agent.server.app import create_app


def test_strategy_read_serves_snapshot_without_live_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HTML_PATH", str(tmp_path / "index.html"))
    config._settings = None
    snapshot = {"generated_at": "2026-09-11T20:00:00+00:00", "status": "partial",
                "summary": {"realized_pnl_eur": -8880870}}
    (tmp_path / "strategy.json").write_text(json.dumps(snapshot))
    response = TestClient(create_app()).get("/api/strategy")
    assert response.status_code == 200
    assert response.json()["summary"] == snapshot["summary"]
    assert response.json()["generated_at"] == snapshot["generated_at"]
    assert response.headers["cache-control"] == "no-store"


def test_strategy_missing_or_broken_is_unavailable_not_empty_success(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HTML_PATH", str(tmp_path / "index.html"))
    config._settings = None
    client = TestClient(create_app())
    assert client.get("/api/strategy").status_code == 503
    for content in ("{broken", "[]", "{}"):
        (tmp_path / "strategy.json").write_text(content)
        assert client.get("/api/strategy").status_code == 503
