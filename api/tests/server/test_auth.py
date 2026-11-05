import json
from pathlib import Path

import pytest

from kickbase_agent.server.auth import TokenCache

FIX = Path(__file__).parent.parent / "fixtures"


@pytest.mark.asyncio
async def test_token_cache_logs_in_once(httpx_mock):
    payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    payload["leagues"] = payload["srvl"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json=payload,
    )

    cache = TokenCache()
    token_1 = await cache.get_token()
    token_2 = await cache.get_token()

    assert token_1 == token_2
    # Only one POST was made
    requests = httpx_mock.get_requests()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_token_cache_refreshes_on_invalidate(httpx_mock):
    payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    payload["leagues"] = payload["srvl"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json=payload,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json={**payload, "tkn": "new-token"},
    )

    cache = TokenCache()
    await cache.get_token()
    cache.invalidate()
    token_after = await cache.get_token()

    assert token_after == "new-token"
