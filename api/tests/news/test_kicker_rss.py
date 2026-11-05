from datetime import UTC
from pathlib import Path

import pytest

from kickbase_agent.news.kicker_rss import KickerRSSClient

FIX = Path(__file__).parent.parent / "fixtures"


@pytest.mark.asyncio
async def test_parses_rss_items(httpx_mock):
    body = (FIX / "kicker_rss.xml").read_bytes()
    httpx_mock.add_response(
        method="GET",
        url="https://newsfeed.kicker.de/news/bundesliga",
        content=body,
        headers={"content-type": "application/rss+xml"},
    )

    client = KickerRSSClient()
    articles = await client.fetch()

    assert len(articles) == 2
    first = articles[0]
    assert first.source == "kicker"
    assert "Musiala" in first.title
    assert first.url == "https://kicker.de/musiala-verletzung-123"


@pytest.mark.asyncio
async def test_content_hash_is_stable(httpx_mock):
    body = (FIX / "kicker_rss.xml").read_bytes()
    httpx_mock.add_response(
        method="GET",
        url="https://newsfeed.kicker.de/news/bundesliga",
        content=body,
    )

    client = KickerRSSClient()
    articles = await client.fetch()
    assert articles[0].content_hash() == articles[0].content_hash()
    assert articles[0].content_hash() != articles[1].content_hash()


def test_missing_date_falls_back_to_utc_now():
    # Nie eine zonenlose Ortszeit: news_items speichert UTC (Jev-Rolle, 29.09.2026).
    assert KickerRSSClient._parse_date(None).tzinfo is UTC
