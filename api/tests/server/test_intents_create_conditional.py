"""POST /api/intents mit Tausch-Bedingung (02.09.2026): after_squad_size + deadline_s.

Neue Datei (L-087). Ein Sofortverkauf darf befristet auf Ersatz warten —
„erst Ersatz, dann Abgang" — und die API nimmt die Bedingung nur für sell
und nur mit Deadline an.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def _sell(**over):
    base = {
        "kind": "sell",
        "player_id": "4333",
        "amount": 0,
        "rationale": "Ullrich: Wertverlierer, Tausch gegen Ersatz-ABW",
        "player_name": "Ullrich",
        "emil_go": "Emil 02.09. 08:05: Verlierer verkaufen, nicht halten",
        "via": "mcp",
    }
    base.update(over)
    return base


def test_bedingter_verkauf_landet_in_sources_mit_deadline(client, db):
    r = client.post("/api/intents", json=_sell(after_squad_size=11, deadline_s=3600))
    assert r.status_code == 200, r.text
    row = db.execute(select(Intent)).scalars().one()
    assert row.sources["after_squad_size"] == 11
    assert row.sources["manual"] is True
    assert row.deadline is not None
    delta = row.deadline.replace(tzinfo=UTC) - row.created_at.replace(tzinfo=UTC)
    assert timedelta(minutes=59) < delta <= timedelta(hours=1)


def test_bedingung_ohne_deadline_wird_abgelehnt(client):
    r = client.post("/api/intents", json=_sell(after_squad_size=11))
    assert r.status_code == 400
    assert "deadline_s" in r.text


def test_bedingung_nur_fuer_sell(client):
    r = client.post(
        "/api/intents",
        json=_sell(kind="bid", amount=5_000_000, after_squad_size=11, deadline_s=3600),
    )
    assert r.status_code == 400
    assert "nur für sell" in r.text


def test_deadline_ohne_bedingung_ist_erlaubt(client, db):
    r = client.post("/api/intents", json=_sell(deadline_s=7200))
    assert r.status_code == 200, r.text
    row = db.execute(select(Intent)).scalars().one()
    assert "after_squad_size" not in row.sources and row.deadline is not None


def test_ohne_beides_bleibt_alles_wie_bisher(client, db):
    r = client.post("/api/intents", json=_sell())
    assert r.status_code == 200, r.text
    row = db.execute(select(Intent)).scalars().one()
    assert row.deadline is None and "after_squad_size" not in row.sources
    assert isinstance(row.created_at, datetime)
