"""Client-Fabrik: Worker/CLI-Clients kommen MIT Auth-Failure-Refresh (P0-Fund).

Vorher bauten Worker-Jobs `KickbaseClient(token=auth.token)` ohne Refresh-Hook —
lief der Token ab, starben Morgenjob/Wächter/Briefing. Die Fabrik verdrahtet
denselben Mechanismus wie der API-Server (TokenCache + on_auth_failure).
"""

import base64
import json
from types import SimpleNamespace

import pytest

import kickbase_agent.config as cfg
import kickbase_agent.kickbase.factory as factory_mod
import kickbase_agent.server.auth as server_auth
from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.kickbase.factory import AuthState, make_kickbase_client
from kickbase_agent.kickbase.models import League

BASE = "https://api.kickbase.com"


@pytest.fixture(autouse=True)
def _fresh_token_cache(monkeypatch):
    """Prozessweiten TokenCache je Test zurücksetzen (Fabrik teilt ihn mit dem Server)."""
    monkeypatch.setattr(server_auth, "_cache", None)


def _make_jwt(payload: dict) -> str:
    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256'})}.{b64(payload)}.sig"


# ---------------------------------------------------------------- Verdrahtung


@pytest.mark.asyncio
async def test_factory_returns_client_with_refresh_hook_and_auth_state(monkeypatch):
    """Der Client trägt den Refresh-Hook des Caches; AuthState liefert Liga+User."""

    class FakeCache:
        async def get_token(self):
            return "tok-1"

        async def get_user_id(self):
            return "u9"

        async def get_leagues(self):
            return [League.from_api({"i": "L1", "n": "Hauptliga"})]

        async def refresh_token(self):
            return "tok-2"

    fake = FakeCache()
    monkeypatch.setattr(factory_mod, "get_token_cache", lambda: fake)

    client, state = await make_kickbase_client()

    assert isinstance(client, KickbaseClient)
    assert client._token == "tok-1"
    # DER Kern des P0-Fixes: der Hook ist gesetzt und zeigt auf den Cache-Refresh.
    assert client._on_auth_failure is not None
    assert client._on_auth_failure == fake.refresh_token
    assert state.token == "tok-1"
    assert state.user_id == "u9"
    assert state.league_id == "L1"
    assert [lg.name for lg in state.leagues] == ["Hauptliga"]


def test_auth_state_league_id_none_without_leagues():
    assert AuthState(token="t", user_id="u").league_id is None
    league = League.from_api({"i": "77", "n": "Zweitliga"})
    assert AuthState(token="t", user_id="u", leagues=[league]).league_id == "77"


# ------------------------------------------------------- 401-Refresh-Fluss


@pytest.mark.asyncio
async def test_401_refreshes_token_and_next_factory_call_reuses_it(
    monkeypatch, httpx_mock
):
    """Token-Modus, kompletter Fluss gegen gemocktes HTTP: Bootstrap mit dem
    .env-Token, 401 mitten im Request → Refresh via /user/refreshtokens, Retry
    mit frischem Token — und der NÄCHSTE Fabrik-Aufruf startet direkt mit dem
    frischen Token (prozessweiter Cache, kein erneuter Ligen-Abruf)."""
    stale = _make_jwt({"sub": "3266238"})
    monkeypatch.setenv("KICKBASE_TOKEN", stale)
    monkeypatch.setenv("KICKBASE_REFRESH_TOKEN", "rt-1")
    cfg._settings = None  # frische Settings mit Token-Modus

    # 1) Bootstrap: Ligen-Abruf mit dem (noch akzeptierten) .env-Token.
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v4/leagues/selection",
        json={"it": [{"i": "L1", "n": "Hauptliga"}]},
    )
    # 2) Job-Request: der Token ist inzwischen abgelaufen → 401.
    httpx_mock.add_response(
        method="GET", url=f"{BASE}/v4/leagues/L1/squad", status_code=401
    )
    # 3) Refresh-Hook: /user/refreshtokens liefert frisches Paar (rolling rtkn).
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v4/user/refreshtokens",
        json={"tkn": "fresh-access", "rtkn": "rt-2"},
    )
    # 4) Retry desselben Requests mit dem frischen Token.
    httpx_mock.add_response(method="GET", url=f"{BASE}/v4/leagues/L1/squad", json={"it": []})

    client, state = await make_kickbase_client()
    assert state.league_id == "L1"
    assert state.user_id == "3266238"  # aus dem JWT gelesen

    squad = await client.get_squad("L1")  # stirbt NICHT mehr am 401
    assert squad == []

    requests = httpx_mock.get_requests()
    assert requests[1].headers["authorization"] == f"Bearer {stale}"
    assert json.loads(requests[2].content) == {"rtkn": "rt-1"}
    assert requests[3].headers["authorization"] == "Bearer fresh-access"

    # Folge-Job: Fabrik liefert den ERNEUERTEN Token aus dem Prozess-Cache —
    # ohne neuen HTTP-Call (kein erneutes Sterben am abgelaufenen .env-Token).
    client2, state2 = await make_kickbase_client()
    assert state2.token == "fresh-access"
    assert client2._token == "fresh-access"
    assert len(httpx_mock.get_requests()) == 4


@pytest.mark.asyncio
async def test_factory_client_survives_401_in_password_mode_via_relogin(
    monkeypatch, httpx_mock
):
    """Passwort-Modus (conftest-Default): 401 → Relogin → Retry mit neuem Token."""
    login_payload = {
        "tkn": "first-token",
        "u": {"id": "7"},
        "leagues": [{"i": "L1", "n": "Liga"}],
    }
    httpx_mock.add_response(
        method="POST", url=f"{BASE}/v4/user/login", json=login_payload
    )
    httpx_mock.add_response(
        method="GET", url=f"{BASE}/v4/leagues/L1/squad", status_code=401
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v4/user/login",
        json={**login_payload, "tkn": "second-token"},
    )
    httpx_mock.add_response(method="GET", url=f"{BASE}/v4/leagues/L1/squad", json={"it": []})

    client, state = await make_kickbase_client()
    assert state.league_id == "L1"
    assert await client.get_squad("L1") == []

    requests = httpx_mock.get_requests()
    assert requests[1].headers["authorization"] == "Bearer first-token"
    assert requests[3].headers["authorization"] == "Bearer second-token"


@pytest.mark.asyncio
async def test_factory_shares_one_cache_across_calls(monkeypatch):
    """Zwei Fabrik-Aufrufe nutzen denselben Cache (ein Bootstrap, ein Refresh-Stand)."""
    calls = {"n": 0}

    async def fake_auth():
        calls["n"] += 1
        return SimpleNamespace(
            token="tok", user_id="u1", leagues=[League.from_api({"i": "L1", "n": "X"})]
        )

    monkeypatch.setattr(server_auth, "authenticate", fake_auth)

    c1, s1 = await make_kickbase_client()
    c2, s2 = await make_kickbase_client()

    assert calls["n"] == 1  # Bootstrap genau einmal — Cache ist prozessweit
    assert s1.token == s2.token == "tok"
    # Beide Clients hängen am selben Cache-Refresh (gleicher Hook).
    assert c1._on_auth_failure == c2._on_auth_failure
