"""OpenLigaDB client — Bundesliga schedule and results."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from kickbase_agent.config import get_settings
from kickbase_agent.news.models import Match


def current_season(today: date | None = None) -> int:
    """Bundesliga-Saison als Startjahr mit Juli-Rollover: August 2026 → 2026."""
    day = today or datetime.now(UTC).date()
    return day.year if day.month >= 7 else day.year - 1


class OpenLigaClient:
    def __init__(self, base_url: str | None = None) -> None:
        self._base = base_url or get_settings().openliga_base_url

    async def get_current_matchday(self, league: str = "bl1") -> int:
        url = f"{self._base}/getcurrentgroup/{league}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            raw: dict[str, Any] = response.json()
        return int(raw["groupOrderID"])

    async def get_matchday(self, season: int, matchday: int, league: str = "bl1") -> list[Match]:
        url = f"{self._base}/getmatchdata/{league}/{season}/{matchday}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            raw: list[dict[str, Any]] = response.json()
        return [self._parse(entry) for entry in raw]

    @staticmethod
    def _parse(raw: dict[str, Any]) -> Match:
        home_score = None
        away_score = None
        for result in raw.get("matchResults", []):
            if result.get("resultTypeID") == 2:
                home_score = result.get("pointsTeam1")
                away_score = result.get("pointsTeam2")
                break

        return Match(
            match_id=raw["matchID"],
            matchday=raw["group"]["groupOrderID"],
            home_team=raw["team1"]["teamName"],
            away_team=raw["team2"]["teamName"],
            kickoff=datetime.fromisoformat(raw["matchDateTime"]),
            home_score=home_score,
            away_score=away_score,
            is_finished=bool(raw.get("matchIsFinished", False)),
        )
