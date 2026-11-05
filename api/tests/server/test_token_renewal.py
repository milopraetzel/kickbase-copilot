"""Token-Erneuerung vor Ablauf (Box-Ausfall 12.10.2026).

Kickbase-Tokens gelten genau 7 Tage. Der Worker holte sein Token einmal beim Start; nach
7 Tagen ohne Neustart beantwortete Kickbase jede Anfrage mit 403, und zehn Stunden lang
scheiterte jeder Job. Der Cache erneuert ein Token jetzt von sich aus, bevor es abläuft.
Die Login-Antwort ist die echte Fixture; nur das Token wird durch eines mit Ablaufzeit
ersetzt.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kickbase_agent.kickbase.auth import token_expiry
from kickbase_agent.server import auth as auth_mod
from kickbase_agent.server.auth import RENEW_BEFORE, TokenCache

FIX = Path(__file__).parent.parent / "fixtures"
LOGIN_URL = "https://api.kickbase.com/v4/user/login"


def _jwt(expires: datetime, issued: datetime | None = None) -> str:
    def part(data: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
        return raw.rstrip("=")

    claims = {"exp": int(expires.timestamp()), "iat": int((issued or expires).timestamp())}
    return f"{part({'alg': 'HS256'})}.{part(claims)}.signatur"


def _login(httpx_mock, token: str) -> None:
    payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    payload["leagues"] = payload["srvl"]
    httpx_mock.add_response(method="POST", url=LOGIN_URL, json={**payload, "tkn": token})


def test_token_expiry_reads_the_exp_claim():
    expires = datetime(2026, 10, 19, 17, 26, tzinfo=UTC)
    assert token_expiry(_jwt(expires)) == expires
    assert token_expiry("kein.jwt") is None
    assert token_expiry("") is None


@pytest.mark.asyncio
async def test_a_token_close_to_expiry_is_renewed_before_use(httpx_mock):
    now = datetime.now(UTC)
    old = _jwt(now + RENEW_BEFORE - timedelta(minutes=5))
    new = _jwt(now + timedelta(days=7))
    _login(httpx_mock, old)
    _login(httpx_mock, new)

    cache = TokenCache()
    assert await cache.get_token() == old
    assert await cache.get_token() == new
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_a_fresh_token_is_not_renewed(httpx_mock):
    token = _jwt(datetime.now(UTC) + timedelta(days=6))
    _login(httpx_mock, token)

    cache = TokenCache()
    assert await cache.get_token() == token
    assert await cache.get_token() == token
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_a_failed_early_renewal_keeps_the_still_valid_token(httpx_mock, monkeypatch):
    """Die Erneuerung ist Vorsorge: Scheitert sie, solange das alte Token noch gilt, läuft
    der Job mit dem alten weiter (der nächste Abruf versucht es erneut)."""
    now = datetime.now(UTC)
    old = _jwt(now + timedelta(hours=2))
    _login(httpx_mock, old)
    cache = TokenCache()
    assert await cache.get_token() == old

    async def kaputt():
        raise RuntimeError("Kickbase nicht erreichbar")

    monkeypatch.setattr(auth_mod, "authenticate", kaputt)
    assert await cache.get_token() == old


@pytest.mark.asyncio
async def test_a_failed_renewal_of_an_expired_token_raises(httpx_mock, monkeypatch):
    now = datetime.now(UTC)
    _login(httpx_mock, _jwt(now - timedelta(minutes=1), issued=now - timedelta(days=7)))
    cache = TokenCache()
    await cache.get_token()

    async def kaputt():
        raise RuntimeError("Kickbase nicht erreichbar")

    monkeypatch.setattr(auth_mod, "authenticate", kaputt)
    with pytest.raises(RuntimeError):
        await cache.get_token()
