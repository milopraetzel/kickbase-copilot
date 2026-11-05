from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, NewsItem
from kickbase_agent.news.models import NewsArticle
from kickbase_agent.news.store import load_recent_articles, persist_articles


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def test_persist_articles_deduplicates(session):
    article = NewsArticle(
        source="kicker",
        url="https://kicker.de/a",
        title="A",
        body="b",
        published_at=datetime.now(UTC),
    )

    inserted_first = persist_articles(session, [article])
    inserted_second = persist_articles(session, [article])

    assert inserted_first == 1
    assert inserted_second == 0
    assert session.query(NewsItem).count() == 1


def test_persist_articles_inserts_distinct(session):
    a = NewsArticle(
        source="kicker",
        url="https://kicker.de/a",
        title="A",
        body="",
        published_at=datetime.now(UTC),
    )
    b = NewsArticle(
        source="kicker",
        url="https://kicker.de/b",
        title="B",
        body="",
        published_at=datetime.now(UTC),
    )

    inserted = persist_articles(session, [a, b])
    assert inserted == 2
    assert session.query(NewsItem).count() == 2


def test_load_recent_articles_returns_only_the_window_in_utc(session):
    """SQLite verliert die Zone; der kicker-Feed liefert GMT — zurück kommt UTC."""
    now = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)
    fresh = NewsArticle(source="kicker", url="https://kicker.de/neu", title="Neu", body="b",
                        published_at=now - timedelta(hours=95))
    stale = NewsArticle(source="kicker", url="https://kicker.de/alt", title="Alt", body="",
                        published_at=now - timedelta(hours=97))
    persist_articles(session, [fresh, stale])

    loaded = load_recent_articles(session, now - timedelta(hours=96))

    assert [a.title for a in loaded] == ["Neu"]
    assert loaded[0].published_at == fresh.published_at
    assert loaded[0].content_hash() == fresh.content_hash()


def test_persist_stores_utc_whatever_offset_the_feed_uses(session):
    """Ein Feed mit +02:00 darf nicht zwei Stunden in der Zukunft landen."""
    berlin = timezone(timedelta(hours=2))
    article = NewsArticle(source="kicker", url="https://kicker.de/z", title="Z", body="",
                          published_at=datetime(2026, 9, 28, 18, 41, tzinfo=berlin))
    persist_articles(session, [article])

    [loaded] = load_recent_articles(session, datetime(2026, 9, 28, tzinfo=UTC))

    assert loaded.published_at == datetime(2026, 9, 28, 16, 41, tzinfo=UTC)


def test_the_window_start_may_come_in_any_zone(session):
    """Derselbe Zeitpunkt in Berliner Zeit darf das Fenster nicht verschieben."""
    now = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)
    inside = NewsArticle(source="kicker", url="https://kicker.de/i", title="Innen", body="",
                         published_at=now - timedelta(hours=95))
    persist_articles(session, [inside])
    since = (now - timedelta(hours=96)).astimezone(timezone(timedelta(hours=2)))

    assert [a.title for a in load_recent_articles(session, since)] == ["Innen"]
