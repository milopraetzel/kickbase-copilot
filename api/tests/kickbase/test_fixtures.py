"""Parsing der Bundesliga-Spielpläne (mit Wettquoten) + Team-Namen."""

import pytest

from kickbase_agent.kickbase.client import KickbaseClient

MD_URL = "https://api.kickbase.com/v4/competitions/1/matchdays"
TABLE_URL = "https://api.kickbase.com/v4/competitions/1/table"


@pytest.mark.asyncio
async def test_get_matchdays_parses_odds_and_teams(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=MD_URL,
        json={
            "it": [
                {
                    "day": 1,
                    "it": [
                        {
                            "day": 1, "dt": "2026-08-28T18:30:00Z", "t1": "2", "t2": "9",
                            "st": 0, "bo": {"o1": 1.27, "ox": 7.0, "o2": 7.5},
                        },
                        {
                            "day": 1, "dt": "2026-08-29T13:30:00Z", "t1": "77", "t2": "10",
                            "st": 0, "bo": {"o1": 2.5, "ox": 3.4, "o2": 2.7},
                        },
                    ],
                }
            ]
        },
    )
    client = KickbaseClient(token="t")
    fixtures = await client.get_matchdays("1")

    assert len(fixtures) == 2
    f = fixtures[0]
    assert f.matchday == 1
    assert f.home_team_id == "2"
    assert f.away_team_id == "9"
    assert f.home_win_odds == 1.27
    assert f.away_win_odds == 7.5
    assert f.is_finished is False


@pytest.mark.asyncio
async def test_get_matchdays_handles_missing_odds(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=MD_URL,
        json={"it": [{"day": 5, "it": [
            {"day": 5, "t1": "2", "t2": "9", "st": 2, "dt": "2026-09-20T13:30:00Z"},
        ]}]},
    )
    client = KickbaseClient(token="t")
    fixtures = await client.get_matchdays("1")

    assert fixtures[0].home_win_odds is None
    assert fixtures[0].is_finished is True  # st == 2


@pytest.mark.asyncio
async def test_get_team_names_maps_tid(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=TABLE_URL,
        json={"it": [
            {"tid": "2", "tn": "Bayern", "cpl": 1},
            {"tid": "9", "tn": "Stuttgart", "cpl": 4},
        ]},
    )
    client = KickbaseClient(token="t")
    names = await client.get_team_names("1")

    assert names == {"2": "Bayern", "9": "Stuttgart"}
