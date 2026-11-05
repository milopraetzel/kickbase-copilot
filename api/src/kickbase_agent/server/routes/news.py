"""GET /api/news — latest Bundesliga news."""

from __future__ import annotations

from fastapi import APIRouter

from kickbase_agent.news.kicker_rss import KickerRSSClient
from kickbase_agent.server.schemas import NewsArticleOut

router = APIRouter()


@router.get("/news", response_model=list[NewsArticleOut])
async def get_news(limit: int = 20) -> list[NewsArticleOut]:
    client = KickerRSSClient()
    articles = await client.fetch()
    return [
        NewsArticleOut(
            source=a.source,
            url=a.url,
            title=a.title,
            body=a.body,
            published_at=a.published_at,
        )
        for a in articles[:limit]
    ]
