"""Zwei-DB-Fix (24.08.): POST /api/intents — manuelles Vorhaben statt lokalem MCP-Write.

Der lokale MCP führte Live-Writes gegen die LOKALE DB aus (Audit unsichtbar für
Box-Planner/-Dedupe, Parallel-Session-Falle 23.08.). Jetzt legt er Vorhaben über
die Box-API an; ausgeführt wird ausschließlich vom Box-Worker.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, Intent, TransferAction
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app


@pytest.fixture(autouse=True)
def approved_engine_plan(monkeypatch):
    """Diese Transport-/Auth-Fälle nutzen gültige interne Pläne ohne Netzaufrufe."""
    from kickbase_agent.server.routes import intents as routes

    from .test_engine_intents import domain_plan

    async def build():
        plan = domain_plan()
        plan.purchase_reviews["15589"]["bid_scenarios"].append({
            "amount": 1_200_000, "win_probability": None, "selected": False,
        })
        plan.purchase_reviews.update(domain_plan(player_id="8329").purchase_reviews)
        return plan

    monkeypatch.setattr(routes, "build_plan", build)


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


def _payload(**over):
    base = {
        "kind": "bid",
        "player_id": "15589",
        "amount": 9_043_439,
        "rationale": "Otávio 100% MW — Mandat",
        "player_name": "Otávio",
        "listing_type": "manager",
        "delay_s": 0,
        "emil_go": "mach so (24.08.)",
        "via": "mcp",
    }
    base.update(over)
    return base


def test_create_manual_intent_planned_and_marked(client, db):
    r = client.post("/api/intents", json=_payload())
    assert r.status_code == 200, r.text
    intent = r.json()["intent"]
    assert intent["status"] == "planned" and intent["kind"] == "bid"
    row = db.execute(select(Intent)).scalars().one()
    assert row.sources["manual"] is True and row.sources["via"] == "mcp"
    assert row.sources["emil_go"].startswith("mach so")
    # Kein Kickbase-Write aus der API: transfer_actions bleibt leer.
    assert db.execute(select(TransferAction)).scalars().first() is None


def test_create_dedupes_open_intent_with_409(client):
    assert client.post("/api/intents", json=_payload()).status_code == 200
    r = client.post("/api/intents", json=_payload(amount=9_500_000))
    assert r.status_code == 409
    assert r.json()["detail"]["intent"]["player_id"] == "15589"


def test_create_validates_kind_and_amount(client):
    assert client.post("/api/intents", json=_payload(kind="lineup")).status_code == 400
    assert client.post("/api/intents", json=_payload(amount=0)).status_code == 400
    assert client.post("/api/intents", json=_payload(rationale="  ")).status_code == 400
    # sell darf ohne Betrag kommen (MW zur Ausführungszeit)
    ok = client.post("/api/intents", json=_payload(kind="sell", amount=0))
    assert ok.status_code == 200


def test_no_disposal_intent_for_a_protected_player(client, db):
    """Verfassung (26.08.): ein Vorhaben „Olise verkaufen" entsteht gar nicht.
    Der Executor würde es ohnehin verweigern — aber es stünde bis dahin als
    scheinbar gültiger Plan in Emils Zentrale."""
    for kind in ("sell", "list"):
        r = client.post("/api/intents", json=_payload(kind=kind, player_id="8329"))
        assert r.status_code == 400
        assert "Geschützter Spieler" in r.json()["detail"]
    assert db.execute(select(Intent)).scalars().first() is None
    # Kaufen darf man ihn weiterhin
    assert client.post(
        "/api/intents", json=_payload(kind="bid", player_id="8329")
    ).status_code == 200


def test_squad_need_marker_reaches_the_planned_action(client, db):
    """Kader-Bedarf muss vom MCP über die Box-API bis in die PlannedAction
    durchkommen — sonst gilt beim Ausführen wieder die 110-%-Kappe."""
    from kickbase_agent.worker.intents import action_for

    r = client.post("/api/intents", json=_payload(squad_need=True))
    assert r.status_code == 200
    row = db.execute(select(Intent)).scalars().one()
    assert row.sources["squad_need"] is True
    assert action_for(row, "1234567").squad_need is True


def test_squad_need_is_ignored_for_sells(client, db):
    """Der Marker hebt nur die Gebots-Kappe — bei Verkäufen hat er nichts zu suchen."""
    from kickbase_agent.worker.intents import action_for

    r = client.post("/api/intents", json=_payload(kind="sell", amount=0, squad_need=True))
    assert r.status_code == 200
    row = db.execute(select(Intent)).scalars().one()
    assert "squad_need" not in row.sources
    assert action_for(row, "1234567").squad_need is False


def test_manual_intent_survives_planner_supersede(db):
    """Der Planner räumt fremde offene Vorhaben („nicht mehr im Plan") — manuelle nie."""
    from kickbase_agent.worker.intents import upsert_intents

    now = datetime.now(UTC)
    manual = Intent(
        created_at=now, updated_at=now, kind="bid", player_id="15589", player_name="Otávio",
        team_id="4", position=2, amount=9_043_439, rationale="Mandat",
        sources={"manual": True, "via": "mcp"}, listing_type="manager",
        deadline=None, execute_at=now, status="planned",
    )
    engine_row = Intent(
        created_at=now, updated_at=now, kind="bid", player_id="9999", player_name="Engine",
        team_id="5", position=3, amount=1_000_000, rationale="Plan",
        sources={"rule_id": "buy_explosion"}, listing_type="kickbase",
        deadline=None, execute_at=now, status="planned",
    )
    db.add_all([manual, engine_row])
    db.commit()
    stats = upsert_intents(db, [], now, market_pids=set())
    db.expire_all()
    assert db.get(Intent, manual.id).status == "planned"  # überlebt
    assert db.get(Intent, engine_row.id).status == "superseded"  # geräumt
    assert [r.id for r in stats.superseded] == [engine_row.id]

def test_write_token_gate_when_configured(client, monkeypatch):
    """P1 (Codex-Folge-Review 24.08.): Traefik-Basic-Auth endet am Proxy — im
    Docker-Netz ist Uvicorn direkt erreichbar. Mit gesetztem Token verlangt
    POST /intents den Header; leer = Bestandsverhalten (andere Tests)."""
    from kickbase_agent.config import get_settings

    monkeypatch.setattr(get_settings(), "kba_api_write_token", "s3cret")
    assert client.post("/api/intents", json=_payload()).status_code == 401
    bad = client.post("/api/intents", json=_payload(),
                      headers={"X-KBA-Write-Token": "falsch"})
    assert bad.status_code == 401
    ok = client.post("/api/intents", json=_payload(),
                     headers={"X-KBA-Write-Token": "s3cret"})
    assert ok.status_code == 200, ok.text


def test_emil_go_required_for_big_amounts_and_sells(client):
    """P1 (Gate-Parität mit dem MCP): >= 5 M und jeder Sofortverkauf brauchen
    Emils GO — auch am HTTP-Rand, nicht nur im MCP-Tool."""
    r = client.post("/api/intents", json=_payload(emil_go=""))
    assert r.status_code == 400 and "emil_go" in r.text
    r2 = client.post("/api/intents", json=_payload(kind="sell", amount=0, emil_go="  "))
    assert r2.status_code == 400
    # Kleines Gebot bleibt ohne GO erlaubt — das Veto-Fenster reicht.
    small = client.post("/api/intents", json=_payload(amount=1_200_000, emil_go=""))
    assert small.status_code == 200, small.text


def test_dedupe_counts_executing_intents(client, db):
    """P2: Ein Vorhaben mitten im Write (`executing`) ist kein freier Slot —
    sonst dispatchen zwei Betraege unter verschiedenen Idempotenz-Keys."""
    now = datetime.now(UTC)
    db.add(Intent(
        created_at=now, updated_at=now, kind="bid", player_id="15589",
        player_name="Otávio", team_id="4", position=2, amount=9_043_439,
        rationale="läuft gerade", sources={}, listing_type="manager",
        deadline=None, execute_at=now, status="executing",
    ))
    db.commit()
    assert client.post("/api/intents", json=_payload()).status_code == 409
