"""Token-basierte Auth: Refresh-Token-Flow + JWT-User-ID (passwortloser Login)."""

import base64
import json

import pytest

from kickbase_agent.kickbase.auth import (
    fetch_leagues,
    refresh_tokens,
    user_id_from_token,
)


def _make_jwt(payload: dict) -> str:
    def b64(data: dict) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64(payload)}.sig"


def test_user_id_from_jwt_reads_known_claims():
    assert user_id_from_token(_make_jwt({"sub": "3266238"})) == "3266238"
    assert user_id_from_token(_make_jwt({"kb.uid": "999"})) == "999"


def test_user_id_from_token_empty_on_non_jwt():
    assert user_id_from_token("not-a-jwt") == ""
    assert user_id_from_token("") == ""


@pytest.mark.asyncio
async def test_refresh_tokens_posts_rtkn_and_returns_bundle(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/refreshtokens",
        json={"tkn": "fresh-access", "tknex": "2026-08-14T22:00:00Z", "rtkn": "fresh-refresh"},
    )

    bundle = await refresh_tokens("old-refresh")

    assert bundle.token == "fresh-access"
    assert bundle.refresh_token == "fresh-refresh"
    request = httpx_mock.get_request()
    assert json.loads(request.content) == {"rtkn": "old-refresh"}


@pytest.mark.asyncio
async def test_refresh_tokens_keeps_old_refresh_when_none_returned(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/refreshtokens",
        json={"tkn": "fresh-access"},
    )

    bundle = await refresh_tokens("keep-me")

    assert bundle.token == "fresh-access"
    assert bundle.refresh_token == "keep-me"


@pytest.mark.asyncio
async def test_fetch_leagues_parses_selection_tolerantly(httpx_mock):
    # Struktur von /v4/leagues/selection ist undokumentiert — tolerant über Kandidat-Keys.
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/selection",
        json={"it": [{"i": "9001", "n": "Hauptliga", "b": 12_000_000}]},
    )

    leagues = await fetch_leagues("some-token")

    assert len(leagues) == 1
    assert leagues[0].id == "9001"
    assert leagues[0].name == "Hauptliga"
