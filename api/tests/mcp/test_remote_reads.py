"""Regression: Remote-Schreibziel und gelesene Entscheidungsgrundlage müssen übereinstimmen."""

from __future__ import annotations

import pytest

from kickbase_agent.config import get_settings
from kickbase_agent.mcp import tools_read, tools_write


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("KBA_REMOTE_API_URL", "https://box.example/api")
    monkeypatch.setenv("KBA_REMOTE_API_AUTH", "emil:test-password")


@pytest.mark.parametrize(
    ("tool_name", "path", "kwargs", "payload"),
    [
        ("evaluation", "/learning", {}, {"evaluation": {"coverage": {"proposals_total": 42}}}),
        ("audit_log", "/audit?limit=7&status=executed", {"limit": 7, "status": "executed"},
         {"count": 1, "status_filter": "executed", "actions": [{"id": 42}]}),
        ("active_tips", "/tips", {}, {"count": 1, "ttl_days": 4, "tips": [{"id": 42}]}),
    ],
)
async def test_remote_reads_use_box_without_opening_local_db(
    world, remote, monkeypatch, httpx_mock, tool_name, path, kwargs, payload,
):
    def forbidden_local_db():
        pytest.fail("Remote-Lesewerkzeug darf keine lokale DB öffnen")

    monkeypatch.setattr(tools_read, "SessionLocal", forbidden_local_db)
    httpx_mock.add_response(method="GET", url=f"https://box.example/api{path}", json=payload)

    out = await getattr(tools_read, tool_name)(**kwargs)

    assert out == {**payload, "remote": True, "data_source": "box"}
    req = httpx_mock.get_requests()[0]
    assert req.headers["Authorization"].startswith("Basic ")
    assert req.headers["X-Requested-With"] == "kba"


@pytest.mark.parametrize("status_code", [401, 403, 404, 503])
async def test_remote_read_failure_does_not_fall_back_to_local(
    world, remote, httpx_mock, status_code,
):
    httpx_mock.add_response(
        method="GET", url="https://box.example/api/learning", status_code=status_code,
        json={"detail": "unauthorized"},
    )
    out = await tools_read.evaluation()
    assert "error" in out
    assert str(status_code) in out["hint"]
    assert "evaluation" not in out


def test_operator_documented_configuration_selects_remote_target(monkeypatch):
    monkeypatch.delenv("KBA_REMOTE_API_URL")
    monkeypatch.delenv("KBA_REMOTE_API_AUTH")
    monkeypatch.setenv("KBA_API_URL", "https://hermes.example/api/")
    monkeypatch.setenv("KBA_API_AUTH", "hermes:test:password")
    assert get_settings().kba_remote_api_url == "https://hermes.example/api/"
    assert tools_write._remote() == ("https://hermes.example/api", ("hermes", "test:password"))


def test_explicit_empty_remote_configuration_disables_operator_alias(monkeypatch):
    monkeypatch.setenv("KBA_API_URL", "https://hermes.example/api")
    monkeypatch.setenv("KBA_API_AUTH", "hermes:test-password")
    assert tools_write._remote() is None
