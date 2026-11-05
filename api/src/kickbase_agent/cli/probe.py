"""`kba probe` — record raw Kickbase API responses with secrets redacted.

Fixtures under tests/fixtures/ must only ever be produced from this command's
output, so parsing code is always written against real response shapes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import typer

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase import endpoints

REDACTED = "<redacted>"

# Keys whose values are secrets regardless of content.
_SENSITIVE_KEYS = {"tkn", "token", "rt", "refreshtoken", "em", "email", "pass", "pw"}

PROBE_TARGETS = ("login", "leagues", "me", "squad", "lineup", "market", "player", "mv")


def redact(value: Any, secrets: frozenset[str] = frozenset()) -> Any:
    """Recursively replace sensitive keys and known secret strings."""
    if isinstance(value, dict):
        return {
            key: REDACTED if key.lower() in _SENSITIVE_KEYS else redact(val, secrets)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str) and value in secrets:
        return REDACTED
    return value


def _path_for(what: str, league_id: str | None, player_id: str | None, timeframe: str) -> str:
    if what == "leagues":
        return endpoints.LEAGUES_SELECTION
    if league_id is None:
        raise typer.BadParameter(f"'{what}' braucht --league/-l")
    if what == "me":
        return endpoints.league_me(league_id)
    if what == "squad":
        return endpoints.squad(league_id)
    if what == "lineup":
        return endpoints.lineup(league_id)
    if what == "market":
        return endpoints.market(league_id)
    if player_id is None:
        raise typer.BadParameter(f"'{what}' braucht --player/-p")
    if what == "player":
        return endpoints.player_detail(league_id, player_id)
    return endpoints.player_market_value_history(league_id, player_id) + f"/{timeframe}"


async def _resolve_token(client: httpx.AsyncClient) -> str:
    settings = get_settings()
    if settings.kickbase_token:
        return settings.kickbase_token
    login_response = await client.post(
        endpoints.LOGIN,
        json={
            "em": settings.kickbase_email,
            "pass": settings.kickbase_password,
            "loy": False,
            "rep": {},
        },
    )
    login_response.raise_for_status()
    return login_response.json()["tkn"]


async def _fetch(
    what: str, league_id: str | None, player_id: str | None, timeframe: str
) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.kickbase_base_url, timeout=15.0) as client:
        if what == "login":
            if not (settings.kickbase_email and settings.kickbase_password):
                raise typer.BadParameter(
                    "'probe login' braucht KICKBASE_EMAIL/PASSWORD. "
                    "Im Token-Modus stattdessen z. B. 'kba probe leagues' nutzen."
                )
            login_response = await client.post(
                endpoints.LOGIN,
                json={
                    "em": settings.kickbase_email,
                    "pass": settings.kickbase_password,
                    "loy": False,
                    "rep": {},
                },
            )
            login_response.raise_for_status()
            return login_response.json()

        token = await _resolve_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get(
            _path_for(what, league_id, player_id, timeframe), headers=headers
        )
        response.raise_for_status()
        return response.json()


def probe_command(
    what: str = typer.Argument(help="Eines von: " + "|".join(PROBE_TARGETS)),
    league_id: str | None = typer.Option(None, "--league", "-l", help="Kickbase-Liga-ID"),
    player_id: str | None = typer.Option(None, "--player", "-p", help="Kickbase-Spieler-ID"),
    timeframe: str = typer.Option("92", "--timeframe", "-t", help="MV-Historie-Zeitraum in Tagen"),
) -> None:
    if what not in PROBE_TARGETS:
        raise typer.BadParameter(f"Unbekanntes Ziel '{what}'. Erlaubt: {', '.join(PROBE_TARGETS)}")

    settings = get_settings()
    payload = asyncio.run(_fetch(what, league_id, player_id, timeframe))
    secrets = frozenset({settings.kickbase_email, settings.kickbase_password})
    typer.echo(json.dumps(redact(payload, secrets), indent=2, ensure_ascii=False))
