"""Strategiedaten bleiben auf der gewählten Quelle und ihrem Datenstand."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kickbase_agent.intel.action_plan import build_action_plan
from kickbase_agent.intel.strategy import build_strategy_insights
from kickbase_agent.mcp import tools_strategy
from kickbase_agent.mcp.tools_strategy import strategy_insights


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("KBA_REMOTE_API_URL", "https://box.example/api")
    monkeypatch.setenv("KBA_REMOTE_API_AUTH", "test:test")


async def test_strategy_selects_player_and_keeps_timestamp(remote, httpx_mock):
    at = "2026-09-11T20:00:00+00:00"
    snapshot = build_strategy_insights(
        feed_events=[], history_rows=[], performance_rows=[],
        squad_rows=[{"pid": "8329", "name": "Olise", "mv": 65000000}],
        market_rows=[], manager_rows=[], budget=0, open_bids=[], saved_lineup=None,
        generated_at=at, user_id="2663242", user_name="emil",
    )
    httpx_mock.add_response(url="https://box.example/api/strategy", json={
        **snapshot,
        "news": {"articles": [{"player_id": "8329", "title": "Quelle"},
                              {"player_id": "4333", "title": "Andere"}]},
    })
    result = await strategy_insights("8329")
    assert result["generated_at"] == at
    assert result["remote"] is True and result["data_source"] == "box"
    assert result["player"]["name"] == "Olise"
    assert result["player"]["player_id"] == "8329"
    assert len(result["news"]) == 1


async def test_strategy_remote_unavailable_cannot_use_local_data(remote, httpx_mock):
    httpx_mock.add_response(url="https://box.example/api/strategy", status_code=503)
    result = await strategy_insights()
    assert "error" in result and "summary" not in result


@pytest.mark.parametrize("local", [False, True], ids=["box", "local_snapshot"])
async def test_strategy_keeps_profiles_history_gaps_and_proposals(
    local, remote, httpx_mock, monkeypatch, tmp_path,
):
    snapshot = build_strategy_insights(
        feed_events=[], history_rows=[], performance_rows=[], squad_rows=[],
        market_rows=[], manager_rows=[{"user_id": "2465162", "name": "Osei"}],
        budget=0, open_bids=[], saved_lineup=None,
        generated_at="2026-09-11T22:10:00+00:00", user_id="2663242", user_name="emil",
    )
    # Interner Fehlerzustand des Cockpits, keine fingierte Kickbase-Antwort.
    snapshot["league_history"] = {
        "reset_at": None,
        "managers": [{"user_id": "2465162", "history_stale": True}],
        "errors": [{"source": "history", "manager_id": "2465162", "type": "TimeoutError"}],
    }
    snapshot["action_plan"] = build_action_plan(
        strategy=snapshot, buys=[], sells=[], intents=[], bids=[],
        squad_rows=[], market_rows=[],
    )
    if local:
        (tmp_path / "strategy.json").write_text(json.dumps(snapshot))
        monkeypatch.setattr(tools_strategy, "read_remote", AsyncMock(return_value=None))
        monkeypatch.setattr(tools_strategy, "get_settings", lambda: SimpleNamespace(
            cockpit_html_path=str(tmp_path / "index.html"),
        ))
    else:
        httpx_mock.add_response(url="https://box.example/api/strategy", json=snapshot)

    result = await strategy_insights()

    for key in ("manager_profiles", "league_history", "action_plan"):
        assert result[key] == snapshot[key]
    rival = next(p for p in result["manager_profiles"] if p["user_id"] == "2465162")
    assert rival["summary"]["budget_eur"] is None
    assert rival["coverage"]["budget_unknown_reason"] == "rival_budget_not_observed"
    assert result["league_history"]["managers"][0]["history_stale"] is True
    assert result["action_plan"]["steps"]
