import json
import logging
from pathlib import Path

import httpx
import pytest

from kickbase_agent.kickbase.auth import (
    LeagueNotFoundError,
    _prioritize,
    authenticate,
    login,
)
from kickbase_agent.kickbase.errors import KickbaseAuthError
from kickbase_agent.kickbase.models import League

FIXTURE = Path(__file__).parent.parent / "fixtures" / "kickbase_login_response.json"


def _league(league_id: str, name: str) -> League:
    return League.from_api({"i": league_id, "n": name})


@pytest.mark.asyncio
async def test_login_returns_token_and_leagues(httpx_mock):
    payload = json.loads(FIXTURE.read_text())
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json={**payload, "leagues": payload["srvl"]},
    )

    result = await login("me@example.com", "pw123")

    assert result.token == payload["tkn"]
    assert result.user_id == payload["u"]
    assert len(result.leagues) == 2
    assert result.leagues[0].name == "Kickbase Mit Kumpels"


@pytest.mark.asyncio
async def test_login_parses_v4_user_object_and_srvl_keys(httpx_mock):
    """Das echte v4-Login liefert `u` als Objekt und srvl-Einträge mit i/n-Keys."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json={
            "tkn": "tok-abc",
            "u": {"id": "3266238", "name": "Emil", "email": "e@example.com"},
            "srvl": [{"i": "9001", "n": "Hauptliga", "b": 12_000_000}],
        },
    )

    result = await login("me@example.com", "pw123")

    assert result.token == "tok-abc"
    assert result.user_id == "3266238"
    assert result.leagues[0].id == "9001"
    assert result.leagues[0].name == "Hauptliga"
    assert result.leagues[0].budget == 12_000_000


@pytest.mark.asyncio
async def test_login_401_raises_auth_error_with_env_hint(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        status_code=401,
        json={"err": 1, "errMsg": "AccessDenied"},
    )

    with pytest.raises(KickbaseAuthError, match="KICKBASE_EMAIL"):
        await login("bad@example.com", "wrong")


@pytest.mark.asyncio
async def test_login_raises_on_5xx(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        status_code=503,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await login("me@example.com", "pw123")


# --- Liga-Auswahl: fail-closed statt stillem Fallback -------------------------


def test_prioritize_puts_configured_league_first():
    leagues = [_league("3613629", "Base"), _league("1234567", "BASE")]

    result = _prioritize(leagues, "1234567")

    assert [lg.id for lg in result] == ["1234567", "3613629"]


def test_prioritize_unknown_league_fails_closed_with_details():
    """Tippfehler in KICKBASE_LEAGUE_ID darf NICHT still in leagues[0] landen."""
    leagues = [_league("3613629", "Base"), _league("1111111", "Andere")]

    with pytest.raises(LeagueNotFoundError) as excinfo:
        _prioritize(leagues, "1234567")

    message = str(excinfo.value)
    assert "1234567" in message                # konfigurierte ID wird genannt
    assert "3613629" in message and "Base" in message  # verfügbare Ligen gelistet
    assert "1111111" in message


def test_prioritize_without_config_keeps_first_league_but_warns(caplog):
    leagues = [_league("1", "Erste"), _league("2", "Zweite")]

    with caplog.at_level(logging.WARNING, logger="kickbase_agent.kickbase.auth"):
        result = _prioritize(leagues, "")

    assert result[0].id == "1"
    assert any("KICKBASE_LEAGUE_ID" in rec.getMessage() for rec in caplog.records)


def test_league_not_found_is_auth_error_subclass():
    """Bestehende KickbaseAuthError-Handler fangen den neuen Fehler mit."""
    assert issubclass(LeagueNotFoundError, KickbaseAuthError)


@pytest.mark.asyncio
async def test_authenticate_token_mode_raises_on_unknown_league(monkeypatch, httpx_mock):
    """Die Exception schlägt durch authenticate() durch — Jobs scheitern laut."""
    monkeypatch.setenv("KICKBASE_TOKEN", "tok-xyz")
    monkeypatch.setenv("KICKBASE_LEAGUE_ID", "1234567")
    import kickbase_agent.config as cfg

    cfg._settings = None
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/selection",
        json={"it": [{"i": "3613629", "n": "Base", "b": 5_000_000}]},
    )

    with pytest.raises(LeagueNotFoundError, match="1234567"):
        await authenticate()


@pytest.mark.asyncio
async def test_authenticate_token_mode_puts_configured_league_first(monkeypatch, httpx_mock):
    monkeypatch.setenv("KICKBASE_TOKEN", "tok-xyz")
    monkeypatch.setenv("KICKBASE_LEAGUE_ID", "1234567")
    import kickbase_agent.config as cfg

    cfg._settings = None
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/selection",
        json={
            "it": [
                {"i": "3613629", "n": "Base", "b": 5_000_000},
                {"i": "1234567", "n": "BASE", "b": 7_000_000},
            ]
        },
    )

    result = await authenticate()

    assert result.leagues[0].id == "1234567"
    assert result.leagues[0].name == "BASE"
