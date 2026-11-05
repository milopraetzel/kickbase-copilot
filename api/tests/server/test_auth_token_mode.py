"""TokenCache im Token-Modus: nutzt env-Token, refresht via rtkn statt Passwort-Login."""

import base64
import json

import pytest

from kickbase_agent.server.auth import TokenCache


def _make_jwt(payload: dict) -> str:
    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256'})}.{b64(payload)}.sig"


@pytest.fixture
def token_env(monkeypatch):
    jwt = _make_jwt({"sub": "3266238"})
    monkeypatch.setenv("KICKBASE_TOKEN", jwt)
    monkeypatch.setenv("KICKBASE_REFRESH_TOKEN", "rtkn-secret")
    monkeypatch.setenv("KICKBASE_EMAIL", "")
    monkeypatch.setenv("KICKBASE_PASSWORD", "")
    import kickbase_agent.config as cfg

    cfg._settings = None
    yield jwt
    cfg._settings = None


@pytest.mark.asyncio
async def test_token_mode_uses_env_token_without_login(token_env, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/selection",
        json={"it": [{"i": "9001", "n": "Hauptliga", "b": 5_000_000}]},
    )

    cache = TokenCache()
    token = await cache.get_token()
    user_id = await cache.get_user_id()
    leagues = await cache.get_leagues()

    assert token == token_env
    assert user_id == "3266238"
    assert leagues[0].id == "9001"
    # Kein Login-POST — nur der Ligen-Abruf
    assert all("/login" not in str(r.url) for r in httpx_mock.get_requests())


@pytest.mark.asyncio
async def test_token_mode_refresh_uses_rtkn_endpoint(token_env, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/selection",
        json={"it": []},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/refreshtokens",
        json={"tkn": "refreshed-token", "rtkn": "rtkn-rotated"},
    )

    cache = TokenCache()
    await cache.get_token()
    new_token = await cache.refresh_token()

    assert new_token == "refreshed-token"
    posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert len(posts) == 1
    assert posts[0].url.path == "/v4/user/refreshtokens"
    assert "/login" not in str(posts[0].url)
