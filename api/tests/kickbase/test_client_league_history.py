"""Neue GETs gegen archivierte Rohantworten, kein Schreibpfad."""
import json
from pathlib import Path

import pytest

from kickbase_agent.kickbase.client import KickbaseClient

FIX = Path(__file__).parents[1] / "fixtures" / "league_history_20260912"
BASE = "https://api.kickbase.com/v4/leagues/1234567"


@pytest.mark.parametrize("method,args,url,fixture", [
    ("get_league_overview", ("1234567",), BASE + "/overview", "league-overview.json"),
    ("get_manager_transfers", ("1234567", "2663242", 25),
     BASE + "/managers/2663242/transfer?start=25", "manager-2663242-transfer-00025.json"),
    ("get_manager_performance", ("1234567", "2663242"),
     BASE + "/managers/2663242/performance", "own-performance.json"),
])
async def test_new_history_gets_keep_real_payloads(httpx_mock, method, args, url, fixture):
    payload = json.loads((FIX / fixture).read_text())
    httpx_mock.add_response(method="GET", url=url, json=payload)
    client = KickbaseClient(token="test", rate_limit_per_second=0)
    assert await getattr(client, method)(*args) == payload
    assert httpx_mock.get_request().method == "GET"


@pytest.mark.parametrize("start", [-1, True, 1.5, "25"])
async def test_history_offset_must_be_nonnegative_integer(start):
    client = KickbaseClient(token="test", rate_limit_per_second=0)
    with pytest.raises(ValueError):
        await client.get_manager_transfers("1234567", "2663242", start=start)
