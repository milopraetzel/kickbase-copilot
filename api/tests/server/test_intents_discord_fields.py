"""Discord-Karten-Spalten (Spec 2026-08-31 §4.3) bleiben API-intern.

`discord_msg_id` und `discord_card_synced_at` existieren nur für den Worker
(Karte posten/nachziehen) — die HTTP-API nimmt beide Felder NICHT ins JSON auf
(Konvention aus dem Routen-Docstring: kein channel_msg_id nach außen).
Setup wie tests/server/test_intents_api.py (In-Memory-SQLite, TestClient).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, Intent
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app

NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture(autouse=True)
def _no_decisions(monkeypatch):
    """Protokoll isolieren (wie test_intents_api): keine Wissens-/Feed-Einträge."""
    import kickbase_agent.knowledge as kn
    import kickbase_agent.knowledge.commits as kc
    import kickbase_agent.server.routes.live as live
    monkeypatch.setattr(kn, "decision_entries", lambda limit=60: [])
    monkeypatch.setattr(kc, "system_entries", lambda limit=30: [])
    monkeypatch.setattr(live, "cached_feed", lambda max_items=200: [])


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
    return TestClient(app, headers={"X-Requested-With": "kba"})


def _seed_intent_with_card(db: Session) -> None:
    db.add(
        Intent(
            created_at=NOW - timedelta(hours=1),
            updated_at=NOW - timedelta(minutes=5),
            kind="bid",
            player_id="7291",
            player_name="Onyedika",
            team_id="15",
            position=3,
            amount=4_200_000,
            rationale="Score 0.81",
            sources={"score": 0.81},
            listing_type="kickbase",
            deadline=NOW + timedelta(minutes=40),
            execute_at=NOW + timedelta(minutes=25),
            status="planned",
            discord_msg_id="112233445566778899",
            discord_card_synced_at=NOW - timedelta(minutes=5),
        )
    )
    db.commit()


def test_discord_card_fields_never_leak_into_api_json(client, db):
    """Weder Feldnamen noch Werte tauchen in /api/intents, /api/intents/{id}
    oder /api/protocol auf — die Message-ID ist ein internes Betriebsdetail."""
    _seed_intent_with_card(db)
    for path in ("/api/intents", "/api/intents?status=all", "/api/intents/1", "/api/protocol"):
        r = client.get(path)
        assert r.status_code == 200, path
        raw = json.dumps(r.json())
        assert "112233445566778899" not in raw, path
        for key in ("discord_msg_id", "discord_card_synced_at"):
            assert f'"{key}"' not in raw, (path, key)


def test_intent_columns_roundtrip_in_db(db):
    """Die Spalten selbst funktionieren (Modell + create_all): setzen, lesen, nullen."""
    _seed_intent_with_card(db)
    row = db.get(Intent, 1)
    assert row.discord_msg_id == "112233445566778899"
    assert row.discord_card_synced_at is not None
    row.discord_msg_id = None
    db.commit()
    db.expire_all()
    assert db.get(Intent, 1).discord_msg_id is None
