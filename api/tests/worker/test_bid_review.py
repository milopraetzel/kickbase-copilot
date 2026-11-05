"""Gebots-Nachschau: bucht Ergebnisse, schweigt bei zu kleiner Stichprobe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.worker import bid_review

NOW = datetime.now(UTC)


class Channel:
    def __init__(self) -> None:
        self.posts: list[str] = []

    async def send_info(self, text: str) -> None:
        self.posts.append(text)


@pytest.fixture
def db(monkeypatch) -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(bid_review, "SessionLocal", factory)
    with factory() as s:
        yield s


def _client(monkeypatch, squad_ids: list[str], bid_ids: list[str]) -> None:
    client = SimpleNamespace(
        get_squad=lambda lg: _aw(
            [
                SimpleNamespace(
                    player=SimpleNamespace(id=i, first_name='Test', last_name=f'Spieler{i}')
                )
                for i in squad_ids
            ]
        ),
        get_own_bids=lambda lg, uid: _aw([{"player_id": i} for i in bid_ids]),
    )
    auth = SimpleNamespace(league_id="L1", user_id="u1")

    async def fake_factory():
        return client, auth

    monkeypatch.setattr(bid_review, "make_kickbase_client", fake_factory)


def _aw(value):
    async def _inner():
        return value

    return _inner()


def _bid(db: Session, pid: str, p: float, outcome: str | None = None) -> None:
    payload = {"expectation": {"p": p, "why": "t", "kind": "kickbase"}}
    if outcome:
        payload["outcome"] = {"result": outcome, "at": NOW.isoformat(),
                              "evidence_version": 2, "training_eligible": True}
    db.add(TransferAction(
        league_id="L1", action_type="bid", player_id=pid, amount=1_000_000,
        status="executed", idempotency_key=f"k{pid}", rule_id="t",
        created_at=NOW - timedelta(days=1), payload=payload,
    ))
    db.commit()


@pytest.mark.asyncio
async def test_books_result_and_stays_quiet_on_small_sample(db, monkeypatch):
    _bid(db, "99", 0.9)
    _client(monkeypatch, squad_ids=["99"], bid_ids=[])
    channel = Channel()

    result = await bid_review.run_bid_review(channel)

    assert result.startswith("settled=1")
    # Der Zuschlag selbst wird gemeldet — eine KALIBRIERUNG bei n=1 aber nicht.
    assert any("Zuschlag" in p for p in channel.posts)
    assert not any("optimistisch" in p or "vorsichtig" in p for p in channel.posts)


@pytest.mark.asyncio
async def test_reports_when_expectation_was_systematically_wrong(db, monkeypatch):
    """Meldungs-Umbau 31.08.: Die Kalibrierung postet NUR noch im selben Lauf
    wie ein frisch abgeschlossenes Gebot — der Dauerzustand wurde vorher
    stuendlich wiederholt (37x/Woche identischer Text)."""
    for i in range(6):
        _bid(db, f"p{i}", 0.9, outcome="lost")
    _client(monkeypatch, squad_ids=[], bid_ids=[])
    channel = Channel()

    await bid_review.run_bid_review(channel)
    # Nichts frisch abgeschlossen -> Kalibrierung bleibt still.
    assert channel.posts == []

    # Erst ein beobachteter neuer Zuschlag löst eine Meldung aus.
    # Verschwinden allein ist kein verlorenes Gebot.
    _bid(db, "frisch", 0.9)
    _client(monkeypatch, squad_ids=["frisch"], bid_ids=[])
    await bid_review.run_bid_review(channel)
    assert any("zu optimistisch" in p for p in channel.posts)


@pytest.mark.asyncio
async def test_announces_won_bid_immediately(db, monkeypatch):
    """Emils Bitte 25.08.: Der Zuschlag muss gemeldet werden, nicht nur gebucht."""
    _bid(db, "99", 0.9)
    _client(monkeypatch, squad_ids=["99"], bid_ids=[])
    channel = Channel()

    await bid_review.run_bid_review(channel)

    assert channel.posts and "Zuschlag" in channel.posts[0]


@pytest.mark.asyncio
async def test_does_not_announce_unproven_loss(db, monkeypatch):
    _bid(db, "77", 0.9)
    _client(monkeypatch, squad_ids=[], bid_ids=[])
    channel = Channel()

    await bid_review.run_bid_review(channel)

    assert channel.posts == []
