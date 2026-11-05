"""Einfrier-Wächter: meldet einmal, dann nie wieder — und schweigt an normalen Tagen."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kickbase_agent.agent import freeze as fz
from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.worker import freeze_job

NOW = datetime(2026, 8, 26, 15, 0, tzinfo=UTC)


class Channel:
    def __init__(self) -> None:
        self.posts: list[str] = []

    async def send_info(self, text: str, **kw) -> None:
        self.posts.append(text)


@pytest.fixture
def db(monkeypatch) -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(freeze_job, "SessionLocal", factory)
    with factory() as s:
        yield s


def _bad_write(db: Session, key: str) -> None:
    db.add(TransferAction(
        league_id="L1", action_type="bid", player_id=key, amount=1_000_000,
        status="unknown", idempotency_key=key, dry_run=False, created_at=NOW,
    ))
    db.commit()


@pytest.mark.asyncio
async def test_quiet_day_says_nothing(db):
    channel = Channel()
    assert await freeze_job.run_freeze_watch(channel, now=NOW) == "quiet"
    assert channel.posts == []


@pytest.mark.asyncio
async def test_two_unclear_writes_freeze_and_alarm(db):
    _bad_write(db, "a")
    _bad_write(db, "b")
    channel = Channel()

    result = await freeze_job.run_freeze_watch(channel, now=NOW)

    assert result.startswith("frozen:")
    assert len(channel.posts) == 1 and "eingefroren" in channel.posts[0]
    assert fz.is_frozen(db)


@pytest.mark.asyncio
async def test_second_run_does_not_alarm_again(db):
    _bad_write(db, "a")
    _bad_write(db, "b")
    channel = Channel()
    await freeze_job.run_freeze_watch(channel, now=NOW)

    assert await freeze_job.run_freeze_watch(channel, now=NOW) == "quiet"
    assert len(channel.posts) == 1


@pytest.mark.asyncio
async def test_works_without_a_channel(db):
    _bad_write(db, "a")
    _bad_write(db, "b")
    assert (await freeze_job.run_freeze_watch(None, now=NOW)).startswith("frozen:")
