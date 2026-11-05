"""POST /api/intents kind=delist — eigenes Listing zurückziehen (22.09.2026).

Emils Auftrag vom 21.09.2026 („bau den delist-weg morgen sauber nach"): Der
ActionExecutor kann `delist` seit jeher (client.delist_player), aber kein
Vorhaben löste es aus. Ein Delist baut Risiko ab wie ein Gebots-Rückzug:
kein emil_go-Zwang, kein Engine-Plan, Betrag 0. Dedupe wie überall
(ein offenes Vorhaben je Art/Spieler), Absender-Whitelist gilt weiter.
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


def _delist(**over):
    base = {
        "kind": "delist",
        "player_id": "8329",
        "amount": 0,
        "rationale": "Olise halten — Emil 21.09.: Listing raus, Spieler wieder schützen",
        "player_name": "Olise",
        "via": "mcp",
    }
    base.update(over)
    return base


def test_delist_wird_als_manuelles_vorhaben_angelegt(client, db):
    r = client.post("/api/intents", json=_delist())
    assert r.status_code == 200, r.text
    body = r.json()["intent"]
    assert body["kind"] == "delist" and body["player_id"] == "8329"
    assert body["status"] == "planned" and body["amount"] == 0
    row = db.execute(select(Intent)).scalars().one()
    assert row.kind == "delist" and row.amount == 0
    assert row.sources["manual"] is True and row.sources["via"] == "mcp"
    assert "emil_go" not in row.sources


def test_delist_ohne_emil_go_und_ohne_engine_plan(client, db, monkeypatch):
    """Kein Mannschaftsplan wird gebaut: build_plan darf gar nicht laufen."""
    from kickbase_agent.server.routes import intents as routes

    async def explode():
        raise AssertionError("build_plan darf für delist nicht aufgerufen werden")

    monkeypatch.setattr(routes, "build_plan", explode)
    r = client.post("/api/intents", json=_delist(emil_go=""))
    assert r.status_code == 200, r.text


def test_delist_ist_sofort_faellig_und_delay_verschiebt(client, db):
    before = datetime.now(UTC)
    r = client.post("/api/intents", json=_delist())
    assert r.status_code == 200, r.text
    row = db.execute(select(Intent)).scalars().one()
    execute_at = row.execute_at.replace(tzinfo=UTC) if row.execute_at.tzinfo is None \
        else row.execute_at
    assert execute_at <= datetime.now(UTC) + timedelta(seconds=1)
    assert execute_at >= before - timedelta(seconds=1)
    r2 = client.post("/api/intents", json=_delist(player_id="9999", delay_s=600))
    assert r2.status_code == 200, r2.text
    later = db.get(Intent, r2.json()["intent"]["id"])
    later_at = later.execute_at.replace(tzinfo=UTC) if later.execute_at.tzinfo is None \
        else later.execute_at
    assert later_at >= datetime.now(UTC) + timedelta(seconds=590)


def test_delist_dedupe_409_bei_offenem_vorhaben(client, db):
    assert client.post("/api/intents", json=_delist()).status_code == 200
    r = client.post("/api/intents", json=_delist())
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["intent"]["kind"] == "delist"
    assert len(db.execute(select(Intent)).scalars().all()) == 1


def test_delist_ohne_rationale_400(client, db):
    r = client.post("/api/intents", json=_delist(rationale="  "))
    assert r.status_code == 400
    assert "rationale" in r.text
    assert db.execute(select(Intent)).scalars().first() is None


def test_delist_ohne_player_id_400(client, db):
    r = client.post("/api/intents", json=_delist(player_id=" "))
    assert r.status_code == 400
    assert "player_id" in r.text
    assert db.execute(select(Intent)).scalars().first() is None


def test_delist_via_hermes_403(client, db):
    r = client.post("/api/intents", json=_delist(via="hermes"))
    assert r.status_code == 403
    assert db.execute(select(Intent)).scalars().first() is None


def test_delist_mit_betrag_wird_auf_null_gesetzt(client, db):
    """Ein Delist hat keinen Preis — ein mitgesendeter Betrag ist ein Irrtum
    des Aufrufers und darf nicht als Listing-/Gebotsbetrag im Audit landen."""
    r = client.post("/api/intents", json=_delist(amount=12_000_000))
    assert r.status_code == 400, r.text
    assert "amount" in r.text


def test_delist_fuer_geschuetzten_spieler_bleibt_erlaubt(client, db, monkeypatch):
    """Die Verfassung sperrt Abgaben (sell/list) — ein Delist schützt den Spieler
    gerade, dieselbe Ausnahme wie im Executor (_DISPOSAL_ACTION_TYPES)."""
    from kickbase_agent.server.routes import intents as routes

    monkeypatch.setattr(routes, "protected_player_ids", lambda: {"8329"})
    r = client.post("/api/intents", json=_delist(player_id="8329"))
    assert r.status_code == 200, r.text


def test_delist_vorhaben_json_und_protokoll_titel(client, db):
    r = client.post("/api/intents", json=_delist())
    intent_id = r.json()["intent"]["id"]
    got = client.get(f"/api/intents/{intent_id}")
    assert got.status_code == 200
    assert got.json()["intent"]["kind"] == "delist"
    proto = client.get("/api/protocol?limit=10")
    assert proto.status_code == 200, proto.text
    titles = [e.get("title", "") for e in proto.json().get("entries", proto.json())
              if isinstance(e, dict)]
    assert any("Listing zurückziehen" in t and "Olise" in t for t in titles), titles
