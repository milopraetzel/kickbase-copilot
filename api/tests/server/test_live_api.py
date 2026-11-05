"""Live-Panel-API: /api/bids (60-s-Cache) + /api/pathways + cached_feed.

Kickbase komplett gemockt (Fabrik-Patch auf routes/live) — kein Netz. Die
Gebots-Rohzeilen entsprechen exakt KickbaseClient.get_own_bids (player_id, name,
team_id, amount, offer_id, expires_in_s, seller, listing_price; tests/mcp/conftest).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import kickbase_agent.server.routes.live as live
from kickbase_agent.db.models import Base, Intent
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app

NOW = datetime.now(UTC).replace(microsecond=0)

BIDS_RAW = [
    {"player_id": "99", "name": "Hidden Gem", "team_id": "11", "amount": 5_120_000,
     "offer_id": "o-1", "expires_in_s": 3600, "seller": "", "listing_price": 5_500_000},
    {"player_id": "88", "name": "Cheap Striker", "team_id": "5", "amount": 3_000_000,
     "offer_id": "o-2", "expires_in_s": 0, "seller": "Felix K.", "listing_price": 3_200_000},
]
FEED_RAW = [
    {"i": "1", "t": 15, "dt": "2026-08-23T10:00:00Z",
     "data": {"byr": "Osei", "pn": "Vozar", "trp": 1_280_015}},
]


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_own_bids(self, league_id, user_id):
        self.calls.append("own_bids")
        return [dict(b) for b in BIDS_RAW]

    async def get_budget(self, league_id):
        self.calls.append("budget")
        return 10_000_000

    async def get_squad(self, league_id):
        self.calls.append("squad")

        def sp(mv: int) -> SimpleNamespace:
            return SimpleNamespace(player=SimpleNamespace(market_value=mv))

        return [sp(60_000_000), sp(30_000_000)]  # Teamwert 90 M

    async def get_activity_feed(self, league_id, max_items=30):
        self.calls.append("feed")
        return [dict(i) for i in FEED_RAW]


@pytest.fixture(autouse=True)
def _fresh_caches():
    live.reset_caches()
    yield
    live.reset_caches()


@pytest.fixture
def fake_client(monkeypatch) -> FakeClient:
    client = FakeClient()
    factory_calls = {"n": 0}

    async def fake_factory():
        factory_calls["n"] += 1
        return client, SimpleNamespace(league_id="L1", user_id="u1")

    monkeypatch.setattr(live, "make_kickbase_client", fake_factory)
    client.factory_calls = factory_calls  # type: ignore[attr-defined]
    return client


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine, future=True)


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: db
    # BEWUSST ohne X-Requested-With: alle Live-Endpunkte sind GET — der
    # CSRF-Guard (nur mutierende Methoden) bleibt unberuehrt.
    return TestClient(app)


# ------------------------------------------------------------------ GET /api/bids


def test_bids_payload_fields(client, fake_client):
    r = client.get("/api/bids")
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is False
    assert body["league_id"] == "L1"
    assert body["count"] == 2
    assert body["cache_ttl_s"] == 60

    manager, kickbase = body["bids"][0], body["bids"][1]
    # Sortierung nach Restzeit: Manager-Listing (exs 0) zuerst
    assert manager["name"] == "Cheap Striker"
    assert manager["listing_type"] == "manager"
    assert manager["seller"] == "Felix K."
    assert manager["expires_at"] is None
    assert kickbase["name"] == "Hidden Gem"
    assert kickbase["listing_type"] == "kickbase"
    assert kickbase["amount_eur"] == 5_120_000
    assert kickbase["amount_fmt"] == "5,12 M"
    assert kickbase["listing_price_eur"] == 5_500_000
    assert kickbase["amount_vs_listing_pct"] == 93.1
    assert kickbase["offer_id"] == "o-1"
    expires = datetime.fromisoformat(kickbase["expires_at"])
    assert timedelta(minutes=55) < expires - NOW < timedelta(minutes=65)

    assert body["exposure_eur"] == 8_120_000
    assert body["budget_eur"] == 10_000_000
    assert body["team_value_eur"] == 90_000_000
    # Kaufkraft = Konto - Gebote + 1/3 Teamwert
    assert body["buying_power_eur"] == 10_000_000 - 8_120_000 + 30_000_000
    for key in ("exposure_fmt", "budget_fmt", "team_value_fmt", "buying_power_fmt"):
        assert isinstance(body[key], str) and body[key]


def test_bids_cache_second_call_hits_no_client(client, fake_client):
    first = client.get("/api/bids").json()
    calls_after_first = list(fake_client.calls)
    second = client.get("/api/bids").json()
    assert fake_client.calls == calls_after_first  # kein weiterer Kickbase-Call
    assert fake_client.factory_calls["n"] == 1
    assert second["cached"] is True
    assert second["bids"] == first["bids"]
    assert second["fetched_at"] == first["fetched_at"]


def test_bids_error_becomes_502_with_detail(client, monkeypatch):
    async def broken_factory():
        raise RuntimeError("Box nicht erreichbar")

    monkeypatch.setattr(live, "make_kickbase_client", broken_factory)
    r = client.get("/api/bids")
    assert r.status_code == 502
    assert "Offene Gebote nicht lesbar" in r.json()["detail"]
    assert "RuntimeError" in r.json()["detail"]


def test_bids_fetch_fills_feed_cache_for_protocol(client, fake_client, monkeypatch):
    async def must_not_run(max_items):
        raise AssertionError("Standalone-Feed-Abruf darf nicht noetig sein")

    monkeypatch.setattr(live, "_fetch_feed_standalone", must_not_run)
    client.get("/api/bids")
    assert "feed" in fake_client.calls
    assert live.cached_feed() == FEED_RAW


# ------------------------------------------------------------------ GET /api/pathways


def test_pathways_uses_bids_cache_and_db_candidates(client, db, fake_client):
    db.add(
        Intent(
            created_at=NOW, updated_at=NOW, kind="bid", player_id="7291",
            player_name="Kimmich", team_id="2", position=3, amount=19_000_000,
            rationale="", sources=None, listing_type="kickbase", deadline=None,
            execute_at=NOW + timedelta(minutes=30), status="planned",
        )
    )
    db.commit()

    client.get("/api/bids")  # Cache fuellen
    calls_before = list(fake_client.calls)
    r = client.get("/api/pathways")
    assert r.status_code == 200
    assert fake_client.calls == calls_before  # Pathways nutzt den Cache, keine neuen Calls

    body = r.json()
    assert set(body) == {"now", "count", "pathways"}
    kinds = [p["kind"] for p in body["pathways"]]
    assert kinds == ["win", "lose", "win", "lose", "all_win", "all_lose"]
    lose = next(p for p in body["pathways"] if p["kind"] == "lose")
    assert any("Kimmich" in step for step in lose["then"])
    win = next(p for p in body["pathways"] if p["player_name"] == "Hidden Gem")
    assert win["if"] == "Zuschlag Hidden Gem (5,12 M)"
    assert win["konto_eur"] == 10_000_000 - 5_120_000


def test_pathways_fetches_live_when_cache_empty(client, db, fake_client):
    r = client.get("/api/pathways")
    assert r.status_code == 200
    assert fake_client.factory_calls["n"] == 1
    assert r.json()["count"] == 6


def test_pathways_error_becomes_502(client, db, monkeypatch):
    async def broken_factory():
        raise RuntimeError("kaputt")

    monkeypatch.setattr(live, "make_kickbase_client", broken_factory)
    r = client.get("/api/pathways")
    assert r.status_code == 502
    assert "Wege nicht berechenbar" in r.json()["detail"]


# ------------------------------------------------------------------ cached_feed


def test_cached_feed_password_mode_returns_empty_without_network(monkeypatch):
    async def must_not_run(max_items):
        raise AssertionError("Ohne KICKBASE_TOKEN darf kein Login versucht werden")

    monkeypatch.setattr(live, "_fetch_feed_standalone", must_not_run)
    assert live.cached_feed() == []  # Konftest setzt KICKBASE_TOKEN=""


def test_cached_feed_token_mode_fetches_once_then_serves_cache(monkeypatch):
    monkeypatch.setenv("KICKBASE_TOKEN", "tkn-x")
    import kickbase_agent.config as cfg

    cfg._settings = None  # Settings-Singleton mit neuem Env neu laden
    fetches = {"n": 0}

    async def fake_fetch(max_items):
        fetches["n"] += 1
        return [dict(i) for i in FEED_RAW]

    monkeypatch.setattr(live, "_fetch_feed_standalone", fake_fetch)
    assert live.cached_feed() == FEED_RAW
    assert live.cached_feed() == FEED_RAW
    assert fetches["n"] == 1


def test_cached_feed_failure_is_empty_and_negative_cached(monkeypatch):
    monkeypatch.setenv("KICKBASE_TOKEN", "tkn-x")
    import kickbase_agent.config as cfg

    cfg._settings = None
    fetches = {"n": 0}

    async def broken_fetch(max_items):
        fetches["n"] += 1
        raise RuntimeError("offline")

    monkeypatch.setattr(live, "_fetch_feed_standalone", broken_fetch)
    assert live.cached_feed() == []
    assert live.cached_feed() == []  # Fehlschlag ist 60 s gecacht -> kein Retry-Hammer
    assert fetches["n"] == 1
