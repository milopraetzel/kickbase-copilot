from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, League, NewsItem, Player, PlayerHistory, User


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def test_user_crud(session):
    user = User(id="u1", email="me@example.com", kickbase_token="tkn")
    session.add(user)
    session.commit()
    loaded = session.get(User, "u1")
    assert loaded is not None
    assert loaded.email == "me@example.com"


def test_league_requires_user_fk(session):
    session.add(User(id="u1", email="me@example.com", kickbase_token="tkn"))
    league = League(
        id="l1",
        user_id="u1",
        kickbase_league_id="42",
        name="Kumpels",
        budget=50_000_000,
    )
    session.add(league)
    session.commit()
    assert session.get(League, "l1").name == "Kumpels"


def test_player_history_tracks_timeseries(session):
    session.add(Player(id="p1", kickbase_player_id="12345", name="Wirtz", position=3, team="9"))
    session.commit()
    entry = PlayerHistory(
        player_id="p1",
        recorded_at=datetime.now(UTC),
        market_value=20_800_000,
        points=112,
        is_starter=True,
    )
    session.add(entry)
    session.commit()
    rows = session.query(PlayerHistory).all()
    assert len(rows) == 1
    assert rows[0].market_value == 20_800_000


def test_news_item_dedup_via_hash_uniqueness(session):
    n1 = NewsItem(
        id="n1",
        source="kicker",
        url="https://kicker.de/a",
        title="Title A",
        body="body",
        published_at=datetime.now(UTC),
        content_hash="abc123",
    )
    n2 = NewsItem(
        id="n2",
        source="kicker",
        url="https://kicker.de/a",
        title="Title A",
        body="body",
        published_at=datetime.now(UTC),
        content_hash="abc123",
    )
    session.add(n1)
    session.commit()
    session.add(n2)
    with pytest.raises(IntegrityError):
        session.commit()
