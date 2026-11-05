"""Persist NewsArticle objects to DB with hash-based dedup."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from kickbase_agent.db.models import NewsItem
from kickbase_agent.news.models import NewsArticle


def _as_utc(moment: datetime) -> datetime:
    """Zonenlos gilt als UTC (so liest `load_recent_articles` zurück)."""
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def persist_articles(session: Session, articles: Iterable[NewsArticle]) -> int:
    inserted = 0
    existing_hashes = {row[0] for row in session.query(NewsItem.content_hash).all()}
    for article in articles:
        h = article.content_hash()
        if h in existing_hashes:
            continue
        session.add(
            NewsItem(
                id=str(uuid.uuid4()),
                source=article.source,
                url=article.url,
                title=article.title,
                body=article.body,
                # SQLite verwirft die Zone; gespeichert wird deshalb immer UTC.
                published_at=_as_utc(article.published_at),
                content_hash=h,
            )
        )
        existing_hashes.add(h)
        inserted += 1
    session.commit()
    return inserted


def load_recent_articles(session: Session, since: datetime) -> list[NewsArticle]:
    """Gespeicherte Artikel ab `since`, Zeiten in UTC.

    SQLite speichert die Zeit ohne Zone; `persist_articles` schreibt UTC (der kicker-Feed
    liefert ohnehin GMT). Ohne diese Rückübersetzung läge das Fenster zwei Stunden daneben.
    """
    cutoff = _as_utc(since).replace(tzinfo=None)
    rows = session.query(NewsItem).filter(NewsItem.published_at >= cutoff).all()
    return [
        NewsArticle(
            source=row.source,
            url=row.url,
            title=row.title,
            body=row.body or "",
            published_at=(
                row.published_at
                if row.published_at.tzinfo
                else row.published_at.replace(tzinfo=UTC)
            ),
        )
        for row in rows
    ]
