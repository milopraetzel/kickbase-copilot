import json
from pathlib import Path

from fastapi.testclient import TestClient

from kickbase_agent.server.app import create_app
from kickbase_agent.server.auth import get_token_cache

FIX = Path(__file__).parent.parent / "fixtures"


def test_market_endpoint_returns_listed_players(httpx_mock):
    login_payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    login_payload["leagues"] = login_payload["srvl"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json=login_payload,
    )
    market_payload = json.loads((FIX / "kickbase_market_response.json").read_text())
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/market",
        json=market_payload,
    )

    get_token_cache().invalidate()
    client = TestClient(create_app())
    response = client.get("/api/leagues/42/market")

    assert response.status_code == 200
    body = response.json()
    assert body["league_id"] == "42"
    assert len(body["players"]) == 2
    first = body["players"][0]
    assert first["price"] == 5_500_000
    assert "expires_at" in first
    assert first["player"]["position_label"] in {"GK", "DEF", "MID", "STR"}
