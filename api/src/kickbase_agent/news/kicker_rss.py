"""Kicker RSS feed parser."""

from __future__ import annotations

from datetime import UTC, datetime

import feedparser
import httpx
from dateutil import parser as dateparser

from kickbase_agent.config import get_settings
from kickbase_agent.news.models import NewsArticle


class KickerRSSClient:
    def __init__(self, feed_url: str | None = None) -> None:
        self._feed_url = feed_url or get_settings().kicker_rss_url

    async def fetch(self) -> list[NewsArticle]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(self._feed_url)
            response.raise_for_status()
            body = response.content

        parsed = feedparser.parse(body)
        articles: list[NewsArticle] = []
        for entry in parsed.entries:
            articles.append(
                NewsArticle(
                    source="kicker",
                    url=entry.get("link", ""),
                    title=entry.get("title", ""),
                    body=entry.get("description", ""),
                    published_at=self._parse_date(entry.get("published")),
                )
            )
        return articles

    @staticmethod
    def _parse_date(value: str | None) -> datetime:
        if not value:
            return datetime.now(UTC)
        return dateparser.parse(value)
