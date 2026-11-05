import json
from pathlib import Path

from fastapi.testclient import TestClient

from kickbase_agent.server.app import create_app
from kickbase_agent.server.auth import get_token_cache

FIX = Path(__file__).parent.parent / "fixtures"

POSITION_LABELS = {1: "GK", 2: "DEF", 3: "MID", 4: "STR"}


def test_squad_returns_typed_players(httpx_mock):
    login_payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    login_payload["leagues"] = login_payload["srvl"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json=login_payload,
    )
    squad_payload = json.loads((FIX / "kickbase_squad_response.json").read_text())
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/squad",
        json=squad_payload,
    )

    get_token_cache().invalidate()

    client = TestClient(create_app())
    response = client.get("/api/leagues/42/squad")

    assert response.status_code == 200
    data = response.json()
    assert data["league_id"] == "42"
    assert len(data["players"]) == 3
    first = data["players"][0]
    assert first["player"]["last_name"] == "Neuer"
    assert first["player"]["position_label"] == "GK"
    assert first["is_starter"] is False  # st=Fitness, kein Startelf-Flag
