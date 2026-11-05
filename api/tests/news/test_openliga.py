import json
from datetime import date
from pathlib import Path

import pytest

from kickbase_agent.news.openliga import OpenLigaClient, current_season

FIX = Path(__file__).parent.parent / "fixtures"


@pytest.mark.asyncio
async def test_fetch_matchday_parses_matches(httpx_mock):
    payload = json.loads((FIX / "openliga_matchday.json").read_text())
    httpx_mock.add_response(
        method="GET",
        url="https://api.openligadb.de/getmatchdata/bl1/2025/30",
        json=payload,
    )

    client = OpenLigaClient()
    matches = await client.get_matchday(season=2025, matchday=30)

    assert len(matches) == 2
    assert matches[0].home_team == "FC Bayern München"
    assert matches[0].away_team == "Borussia Dortmund"
    assert matches[0].is_finished is False
    assert matches[1].is_finished is True
    assert matches[1].home_score == 3
    assert matches[1].away_score == 1


@pytest.mark.asyncio
async def test_fetch_matchday_handles_empty(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.openligadb.de/getmatchdata/bl1/2025/99",
        json=[],
    )

    client = OpenLigaClient()
    matches = await client.get_matchday(season=2025, matchday=99)
    assert matches == []


@pytest.mark.asyncio
async def test_get_current_matchday(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.openligadb.de/getcurrentgroup/bl1",
        json={"groupID": 46739, "groupName": "1. Spieltag", "groupOrderID": 1},
    )

    client = OpenLigaClient()

    assert await client.get_current_matchday() == 1


def test_current_season_rolls_over_in_july():
    assert current_season(date(2026, 8, 13)) == 2026
    assert current_season(date(2027, 3, 1)) == 2026
    assert current_season(date(2027, 7, 1)) == 2027
