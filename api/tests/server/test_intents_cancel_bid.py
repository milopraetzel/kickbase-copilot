"""POST /api/intents kind=cancel_bid — hängende Angebote zurückziehen (02.09.2026).

Neue Datei (L-087). Ein Rückzug baut Risiko ab: kein emil_go nötig, aber die
Offer-ID ist Pflicht (der Executor-Dispatch braucht sie), und sie landet in
den Quellen, aus denen action_for die PlannedAction baut.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, Intent
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: db
    return TestClient(app, headers={"X-Requested-With": "kba"})


def _cancel(**over):
    base = {
        "kind": "cancel_bid",
        "player_id": "3350",
        "amount": 0,
        "rationale": "Keidel: Moritz W. nimmt seit 24 h nicht an — Konto freimachen für Daka",
        "player_name": "Keidel",
        "offer_id": "2663242",
        "via": "claude-session",  # Paket 1 (P0-8): hermes ist kein Absender mehr
    }
    base.update(over)
    return base


def test_rueckzug_ohne_emil_go_mit_offer_id_in_sources(client, db):
    r = client.post("/api/intents", json=_cancel())
    assert r.status_code == 200, r.text
    assert r.json()["intent"]["kind"] == "cancel_bid"
    row = db.execute(select(Intent)).scalars().one()
    assert row.kind == "cancel_bid" and row.sources["offer_id"] == "2663242"
    assert row.sources["manual"] is True and row.sources["via"] == "claude-session"


def test_rueckzug_ohne_offer_id_wird_abgelehnt(client):
    r = client.post("/api/intents", json=_cancel(offer_id=""))
    assert r.status_code == 400
    assert "offer_id" in r.text


def test_unbekanntes_kind_bleibt_abgelehnt(client):
    # `delist` ist seit 22.09.2026 eine eigene Art (test_intents_delist.py);
    # ein wirklich unbekanntes kind bleibt 400.
    r = client.post("/api/intents", json=_cancel(kind="purge"))
    assert r.status_code == 400
