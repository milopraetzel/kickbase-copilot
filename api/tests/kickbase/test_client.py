import json
import time
from pathlib import Path

import httpx
import pytest

from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.kickbase.errors import KickbaseAuthError, KickbaseDataError

FIX = Path(__file__).parent.parent / "fixtures"
SQUAD_URL = "https://api.kickbase.com/v4/leagues/42/squad"
BUDGET_URL = "https://api.kickbase.com/v4/leagues/42/me/budget"


@pytest.mark.asyncio
async def test_get_squad_returns_typed_players(httpx_mock):
    payload = json.loads((FIX / "kickbase_squad_response.json").read_text())
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/squad",
        json=payload,
    )

    client = KickbaseClient(token="tkn")
    squad = await client.get_squad(league_id="42")

    assert len(squad) == 3
    assert squad[0].player.last_name == "Neuer"
    # st ist Fitness-Status, kein Startelf-Flag — is_starter bleibt immer False
    assert squad[0].fitness_status > 0
    assert squad[0].is_starter is False
    assert squad[2].fitness_status == 0


@pytest.mark.asyncio
async def test_get_market_returns_market_players(httpx_mock):
    payload = json.loads((FIX / "kickbase_market_response.json").read_text())
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/market",
        json=payload,
    )

    client = KickbaseClient(token="tkn")
    market = await client.get_market(league_id="42")

    assert len(market) == 2
    assert market[0].price == 5_500_000


@pytest.mark.asyncio
async def test_client_sends_bearer_token(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/squad",
        json={"it": []},
    )

    client = KickbaseClient(token="mytoken")
    await client.get_squad(league_id="42")

    request = httpx_mock.get_request()
    assert request.headers["authorization"] == "Bearer mytoken"


@pytest.mark.asyncio
async def test_rate_limiter_enforces_minimum_interval(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/squad",
        json={"it": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/market",
        json={"it": []},
    )

    client = KickbaseClient(token="t", rate_limit_per_second=10.0)  # 0.1s min interval

    start = time.perf_counter()
    await client.get_squad("42")
    await client.get_market("42")
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.1


@pytest.mark.asyncio
async def test_request_retries_once_with_fresh_token_on_401(httpx_mock):
    httpx_mock.add_response(method="GET", url=SQUAD_URL, status_code=401)
    httpx_mock.add_response(method="GET", url=SQUAD_URL, json={"it": []})

    async def refresh() -> str:
        return "fresh-token"

    client = KickbaseClient(token="stale", on_auth_failure=refresh)
    await client.get_squad("42")

    requests = httpx_mock.get_requests()
    assert requests[0].headers["authorization"] == "Bearer stale"
    assert requests[1].headers["authorization"] == "Bearer fresh-token"


@pytest.mark.asyncio
async def test_request_raises_auth_error_when_401_persists(httpx_mock):
    httpx_mock.add_response(method="GET", url=SQUAD_URL, status_code=401)
    httpx_mock.add_response(method="GET", url=SQUAD_URL, status_code=401)

    async def refresh() -> str:
        return "still-stale"

    client = KickbaseClient(token="stale", on_auth_failure=refresh)

    with pytest.raises(KickbaseAuthError):
        await client.get_squad("42")


@pytest.mark.asyncio
async def test_request_retries_once_with_fresh_token_on_403(httpx_mock):
    """Box-Ausfall 12.10.2026: Ein abgelaufenes Token beantwortet Kickbase mit 403, nicht 401.
    Ohne Neuanmeldung scheiterte jeder Job zehn Stunden lang."""
    httpx_mock.add_response(method="GET", url=SQUAD_URL, status_code=403)
    httpx_mock.add_response(method="GET", url=SQUAD_URL, json={"it": []})

    async def refresh() -> str:
        return "fresh-token"

    client = KickbaseClient(token="expired", on_auth_failure=refresh)
    await client.get_squad("42")

    requests = httpx_mock.get_requests()
    assert [r.headers["authorization"] for r in requests] == [
        "Bearer expired", "Bearer fresh-token"]


@pytest.mark.asyncio
async def test_a_persisting_403_stays_an_http_error(httpx_mock):
    """Bleibt es nach der Neuanmeldung bei 403, ist es kein Tokenproblem: derselbe
    HTTP-Fehler wie bisher (die Wiederholungslogik im Intent-Executor unterscheidet daran)."""
    httpx_mock.add_response(method="GET", url=SQUAD_URL, status_code=403)
    httpx_mock.add_response(method="GET", url=SQUAD_URL, status_code=403)

    async def refresh() -> str:
        return "fresh-token"

    client = KickbaseClient(token="expired", on_auth_failure=refresh)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await client.get_squad("42")
    assert caught.value.response.status_code == 403
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_a_failed_relogin_after_403_keeps_the_rejection(httpx_mock):
    """Codex-Review 12.10.2026: Scheitert die Neuanmeldung (z. B. Timeout), steht die
    Ablehnung trotzdem fest. Ein durchgereichter Timeout ließe den Executor ein abgelehntes
    Gebot für „vielleicht ausgeführt“ halten und bis zur Klärung sperren."""
    httpx_mock.add_response(method="POST", url=SQUAD_URL, status_code=403)

    async def refresh() -> str:
        raise httpx.ReadTimeout("Login hängt")

    client = KickbaseClient(token="expired", on_auth_failure=refresh)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await client._post("/v4/leagues/42/squad", json={})
    assert caught.value.response.status_code == 403
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)


@pytest.mark.asyncio
async def test_a_failed_relogin_after_401_is_an_auth_error(httpx_mock):
    httpx_mock.add_response(method="GET", url=SQUAD_URL, status_code=401)

    async def refresh() -> str:
        raise httpx.ReadTimeout("Login hängt")

    client = KickbaseClient(token="expired", on_auth_failure=refresh)

    with pytest.raises(KickbaseAuthError) as caught:
        await client.get_squad("42")
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)


@pytest.mark.asyncio
async def test_request_raises_auth_error_on_401_without_refresh_hook(httpx_mock):
    httpx_mock.add_response(method="GET", url=SQUAD_URL, status_code=401)

    client = KickbaseClient(token="stale")

    with pytest.raises(KickbaseAuthError):
        await client.get_squad("42")


@pytest.mark.asyncio
async def test_request_backs_off_on_429_then_succeeds(httpx_mock):
    httpx_mock.add_response(method="GET", url=SQUAD_URL, status_code=429)
    httpx_mock.add_response(method="GET", url=SQUAD_URL, json={"it": []})

    client = KickbaseClient(token="t", retry_delays_429=(0.01,))
    squad = await client.get_squad("42")

    assert squad == []
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_get_budget_returns_balance_field(httpx_mock):
    httpx_mock.add_response(method="GET", url=BUDGET_URL, json={"b": 2_500_000, "bs": 0})

    client = KickbaseClient(token="t")

    assert await client.get_budget("42") == 2_500_000


@pytest.mark.asyncio
async def test_get_budget_missing_field_raises_instead_of_zero(httpx_mock):
    """Fehlendes `b` darf NIE als „Konto = 0 €" gelesen werden — die T-15-
    Notbremse würde sonst grundlos den halben Kader verkaufen. Die Meldung
    nennt die tatsächlich gelieferten Payload-Keys (Debugbarkeit)."""
    httpx_mock.add_response(method="GET", url=BUDGET_URL, json={"bs": 0, "day": 3})

    client = KickbaseClient(token="t")

    with pytest.raises(KickbaseDataError) as err:
        await client.get_budget("42")
    assert "'b'" in str(err.value)
    assert "bs" in str(err.value) and "day" in str(err.value)


@pytest.mark.asyncio
async def test_get_budget_empty_payload_raises(httpx_mock):
    httpx_mock.add_response(method="GET", url=BUDGET_URL, json={})

    client = KickbaseClient(token="t")

    with pytest.raises(KickbaseDataError) as err:
        await client.get_budget("42")
    assert "leer" in str(err.value)


@pytest.mark.asyncio
async def test_get_budget_non_numeric_value_raises(httpx_mock):
    """`b: null` oder Text ist genauso „Zustand unbekannt" wie ein fehlendes Feld."""
    httpx_mock.add_response(method="GET", url=BUDGET_URL, json={"b": None})
    httpx_mock.add_response(method="GET", url=BUDGET_URL, json={"b": "kaputt"})

    client = KickbaseClient(token="t")

    with pytest.raises(KickbaseDataError):
        await client.get_budget("42")
    with pytest.raises(KickbaseDataError):
        await client.get_budget("42")


@pytest.mark.asyncio
async def test_get_player_mv_history_parses_snapshots(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/players/777/marketvalue/92",
        json={
            "it": [
                {"dt": 1786579200, "mv": 5_000_000},
                {"dt": 1786665600, "mv": 5_250_000},
            ]
        },
    )

    client = KickbaseClient(token="t")
    history = await client.get_player_mv_history("42", "777", timeframe=92)

    assert len(history) == 2
    assert history[1].value == 5_250_000
    assert history[0].date.year == 2026
