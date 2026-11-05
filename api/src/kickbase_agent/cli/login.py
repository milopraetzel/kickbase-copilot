"""`kba login` — authenticate (token or password mode) and print user + leagues."""

from __future__ import annotations

import asyncio

import typer

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase.auth import authenticate


def login_command() -> None:
    settings = get_settings()
    mode = "Token" if settings.kickbase_token else "E-Mail/Passwort"

    result = asyncio.run(authenticate())

    typer.echo(f"✅ Authentifiziert ({mode}-Modus)")
    if result.user_id:
        typer.echo(f"User-ID: {result.user_id}")
    typer.echo(f"Token (erste 40 Zeichen): {result.token[:40]}...")
    typer.echo("")
    typer.echo(f"Ligen ({len(result.leagues)}):")
    for lg in result.leagues:
        typer.echo(f"  • {lg.id}  {lg.name}  [{lg.budget / 1_000_000:.1f}M €]")
