"""POST /api/learning/evidence — Agenten-Beleg schreiben (Spec §7).

Kein Kickbase-Write, keine Vorhaben: Diese Route legt genau EINE Zeile in
`learning_evidence` an. Sie hängt an denselben zwei Türen wie alle Schreibwege
(CSRF-Header + Schreib-Token, s. tests/server/test_write_gate.py) — geprüft wird
hier der fachliche Vertrag.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, LearningEvidence
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: db
    return TestClient(app, headers={"X-Requested-With": "kba"})


def _payload(**over):
    base = {
        "learning_id": "L-065",
        "verdict": "confirm",
        "note": "Ulrich-Gebot mit 100 % MW abgelehnt",
        "ref": "bid:34",
    }
    base.update(over)
    return base


def test_post_evidence_stores_one_agent_row(client, db):
    r = client.post("/api/learning/evidence", json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()["evidence"]
    assert body["learning_id"] == "L-065" and body["verdict"] == "confirm"
    assert body["source"] == "agent" and body["rule_key"] == ""
    assert body["created"] is True
    row = db.execute(select(LearningEvidence)).scalars().one()
    assert row.source == "agent" and row.ref == "bid:34"


def test_source_cannot_be_forged_by_the_caller(client, db):
    """Emil muss sehen, ob gemessen oder beurteilt wurde — `auto` ist nicht wählbar."""
    r = client.post("/api/learning/evidence", json=_payload(source="auto", rule_key="fake"))
    assert r.status_code == 200, r.text
    row = db.execute(select(LearningEvidence)).scalars().one()
    assert row.source == "agent" and row.rule_key == ""


def test_duplicate_ref_is_reported_but_creates_nothing(client, db):
    assert client.post("/api/learning/evidence", json=_payload()).status_code == 200
    r = client.post("/api/learning/evidence", json=_payload(verdict="refute", note="anders"))
    assert r.status_code == 200
    assert r.json()["evidence"]["created"] is False
    rows = db.execute(select(LearningEvidence)).scalars().all()
    assert len(rows) == 1 and rows[0].verdict == "confirm"


@pytest.mark.parametrize(
    "over",
    [
        {"learning_id": "065"},      # kein Anker-Format
        {"verdict": "vielleicht"},   # nur confirm | refute
        {"note": "   "},             # Beobachtung ist Pflicht
        {"ref": ""},                 # ohne Referenz zaehlt ein Ereignis doppelt
    ],
)
def test_invalid_payloads_are_refused_with_400(client, db, over):
    r = client.post("/api/learning/evidence", json=_payload(**over))
    assert r.status_code in (400, 422), r.text
    assert db.execute(select(LearningEvidence)).scalars().first() is None
