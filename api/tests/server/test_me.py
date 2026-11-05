import json
from pathlib import Path

from fastapi.testclient import TestClient

from kickbase_agent.server.app import create_app
from kickbase_agent.server.auth import get_token_cache

FIX = Path(__file__).parent.parent / "fixtures"


def test_me_returns_user_and_leagues(httpx_mock):
    payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    payload["leagues"] = payload["srvl"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json=payload,
    )
    # Kontostand pro Liga (echter Wert aus /me/budget)
    for lid, bal in (("42", 7_500_000), ("84", 3_000_000)):
        httpx_mock.add_response(
            method="GET",
            url=f"https://api.kickbase.com/v4/leagues/{lid}/me/budget",
            json={"b": bal, "bs": 0, "pbas": 0.0},
        )

    # Reset the singleton between tests
    get_token_cache().invalidate()

    client = TestClient(create_app())
    response = client.get("/api/me")

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "user-123"
    assert len(data["leagues"]) == 2
    assert data["leagues"][0]["name"] == "Kickbase Mit Kumpels"
    assert data["leagues"][0]["budget"] == 7_500_000


def test_me_returns_502_with_hint_when_login_rejected(httpx_mock):
    """Abgelehnter Kickbase-Login (z. B. Platzhalter-Creds) → 502 mit Hinweis statt 500."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        status_code=401,
        json={"err": 1, "errMsg": "AccessDenied"},
    )

    get_token_cache().invalidate()

    client = TestClient(create_app())
    response = client.get("/api/me")

    assert response.status_code == 502
    assert "KICKBASE_EMAIL" in response.json()["detail"]
