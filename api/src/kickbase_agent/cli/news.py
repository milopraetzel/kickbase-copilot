"""`kba news` — fetch & print the latest Kicker BL news."""

from __future__ import annotations

import asyncio

import typer

from kickbase_agent.news.kicker_rss import KickerRSSClient


def news_command(
    limit: int = typer.Option(10, "--limit", "-n", help="Max items to display"),
) -> None:
    async def run() -> None:
        client = KickerRSSClient()
        articles = await client.fetch()
        for article in articles[:limit]:
            typer.echo(f"• [{article.published_at:%d.%m %H:%M}] {article.title}")
            typer.echo(f"    {article.url}")

    asyncio.run(run())
