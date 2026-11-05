"""`kba markt` — list transfer market offers."""

from __future__ import annotations

import asyncio

import typer

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase.auth import login
from kickbase_agent.kickbase.client import KickbaseClient


def markt_command(
    league_id: str = typer.Option(..., "--league-id", "-l"),
) -> None:
    settings = get_settings()

    async def run() -> None:
        token = await login(settings.kickbase_email, settings.kickbase_password)
        client = KickbaseClient(token=token.token)
        market = await client.get_market(league_id=league_id)

        typer.echo(f"Transfermarkt Liga {league_id} ({len(market)} Spieler):")
        typer.echo("-" * 80)
        typer.echo(f"{'NAME':<30} {'POS':<5} {'MARKTWERT':>12} {'PREIS':>12} {'ABLAUF':<16}")
        typer.echo("-" * 80)
        for mp in sorted(market, key=lambda m: m.price, reverse=True):
            full_name = f"{mp.player.first_name} {mp.player.last_name}".strip()
            typer.echo(
                f"{full_name[:30]:<30} "
                f"{mp.player.position.name[:5]:<5} "
                f"{mp.player.market_value / 1_000_000:>9.1f} M€ "
                f"{mp.price / 1_000_000:>9.1f} M€ "
                f"{mp.expires_at.strftime('%d.%m. %H:%M'):<16}"
            )

    asyncio.run(run())
