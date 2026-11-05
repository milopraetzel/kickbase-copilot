"""GET /api/freeze — Einfrier-Zustand für Operator und Zentrale (02.09.2026).

Neue Datei (L-087). Lesend: Hermes sieht im Bericht, ob und warum eingefroren
ist. Das Lösen läuft NICHT über die API (kein Schalter für die eigene
Notbremse) — von Hand `kba unfreeze --by emil`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.agent import freeze as fz
from kickbase_agent.db.models import Base
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app

NOW = datetime(2026, 9, 2, 7, 12, tzinfo=UTC)


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


def test_ohne_freeze_meldet_frozen_false(client):
    r = client.get("/api/freeze")
    assert r.status_code == 200 and r.json() == {"frozen": False}


def test_mit_freeze_kommen_grund_und_zeitpunkt(client, db):
    fz.freeze(db, reason="Realisierter Tagesverlust 12,0 M €", source=fz.SOURCE_LOSS, now=NOW)

    body = client.get("/api/freeze").json()

    assert body["frozen"] is True and body["source"] == "verlust"
    assert "12,0 M" in body["reason"] and body["since"].startswith("2026-09-02T07:12")


def test_nach_dem_loesen_wieder_frei(client, db):
    fz.freeze(db, reason="x", source=fz.SOURCE_WRITES, now=NOW)
    fz.release(db, by="emil", now=NOW, note="geprüft")

    assert client.get("/api/freeze").json() == {"frozen": False}


def test_kein_release_endpunkt_ueber_die_api(client):
    """Bewusste Lücke: die eigene Notbremse löst kein Agent per HTTP."""
    r = client.post("/api/freeze/release", json={"by": "hermes", "note": "weiter"})
    assert r.status_code in (404, 405)
