"""Parsing der Spieltags-Performance (echte Punkte pro Spieltag)."""

import pytest

from kickbase_agent.kickbase.client import KickbaseClient

URL = "https://api.kickbase.com/v4/leagues/42/players/11955/performance"


@pytest.mark.asyncio
async def test_performance_parses_matchday_points_and_minutes(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=URL,
        json={
            "it": [
                {
                    "ti": "2025/2026",
                    "n": "Bundesliga",
                    "ph": [
                        {"day": 3, "p": 28, "mp": "102'", "md": "2025-09-13T13:30:00Z"},
                        {"day": 4, "p": 248, "mp": "97'", "md": "2025-09-19T18:30:00Z"},
                        {"day": 5, "p": 123, "mp": "", "md": "2025-09-28T15:30:00Z"},
                    ],
                }
            ]
        },
    )
    client = KickbaseClient(token="t")
    perf = await client.get_player_performance("42", "11955")

    assert len(perf) == 3
    assert perf[0].matchday == 3
    assert perf[0].points == 28
    assert perf[0].minutes == 102
    assert perf[2].minutes == 0  # leere Minuten → 0
    # chronologisch aufsteigend
    assert [p.matchday for p in perf] == [3, 4, 5]


@pytest.mark.asyncio
async def test_performance_flattens_multiple_seasons_chronologically(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=URL,
        json={
            "it": [
                {"ti": "2024/2025", "n": "Bundesliga",
                 "ph": [{"day": 33, "p": 90, "mp": "90'", "md": "2025-05-01T13:30:00Z"}]},
                {"ti": "2025/2026", "n": "Bundesliga",
                 "ph": [{"day": 1, "p": 120, "mp": "90'", "md": "2025-08-23T13:30:00Z"}]},
            ]
        },
    )
    client = KickbaseClient(token="t")
    perf = await client.get_player_performance("42", "11955")

    assert [p.season for p in perf] == ["2024/2025", "2025/2026"]
    assert perf[-1].points == 120  # neuester zuletzt


@pytest.mark.asyncio
async def test_performance_only_counts_bundesliga(httpx_mock):
    # Andere Wettbewerbe (Pokal) teilen (Saison, Spieltag) → müssen ignoriert werden.
    httpx_mock.add_response(
        method="GET",
        url=URL,
        json={
            "it": [
                {"ti": "2025/2026", "n": "Bundesliga",
                 "ph": [{"day": 1, "p": 100, "mp": "90'", "md": "2025-08-23T13:30:00Z"}]},
                {"ti": "2025/2026", "n": "DFB-Pokal",
                 "ph": [{"day": 1, "p": 50, "mp": "90'", "md": "2025-08-20T18:00:00Z"}]},
            ]
        },
    )
    client = KickbaseClient(token="t")
    perf = await client.get_player_performance("42", "11955")
    assert len(perf) == 1
    assert perf[0].points == 100


@pytest.mark.asyncio
async def test_performance_handles_empty(httpx_mock):
    httpx_mock.add_response(method="GET", url=URL, json={"it": []})
    client = KickbaseClient(token="t")
    assert await client.get_player_performance("42", "11955") == []
