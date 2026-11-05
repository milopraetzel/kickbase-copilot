"""`kba kader` — print the squad for a league."""

from __future__ import annotations

import asyncio

import typer

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase.auth import login
from kickbase_agent.kickbase.client import KickbaseClient


def kader_command(
    league_id: str = typer.Option(..., "--league-id", "-l", help="Kickbase league ID"),
) -> None:
    settings = get_settings()

    async def run() -> None:
        token_result = await login(settings.kickbase_email, settings.kickbase_password)
        client = KickbaseClient(token=token_result.token)
        squad = await client.get_squad(league_id=league_id)

        typer.echo(f"Kader für Liga {league_id} ({len(squad)} Spieler):")
        typer.echo("-" * 70)
        typer.echo(f"{'POS':<4} {'FIT':<4} {'KB-ELF':<7} {'NAME':<26} {'MARKTWERT':>14}")
        typer.echo("-" * 70)
        for sp in sorted(squad, key=lambda s: (s.player.position, -s.player.market_value)):
            fit = "🚑" if sp.fitness_status else "  "
            prob = sp.player.lineup_prob
            kb = f"St.{prob}" if prob is not None else "—"
            typer.echo(
                f"{sp.player.position.name[:3]:<4} {fit:<4} {kb:<7} "
                f"{sp.player.last_name[:26]:<26} "
                f"{sp.player.market_value / 1_000_000:>10.1f} M€"
            )

    asyncio.run(run())
