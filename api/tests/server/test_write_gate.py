"""Schreib-Gate: mutierende /api-Routen nur durch die Vordertür (Traefik).

Gemessen am 24.08.: `kickbase-api` hängt im geteilten Docker-Netz `n8n_default`
mit sieben fremden Containern; ein POST /api/refresh aus `atoz-chat-preview` kam
bis in die App durch (HTTP 429) — Basic-Auth endet am Proxy, der CSRF-Header ist
für einen Nicht-Browser trivial. Traefik färbt deshalb jeden Request auf dem
/api-Router mit `X-KBA-Write-Token`; die App verlangt ihn auf ALLEN mutierenden
Routen. Nicht nur POST /intents: `adjust` (Betrag) und `execute-now`
(Veto-Fenster) sind der gefährlichere Pfad und müssen für die Zentrale-Buttons
HTTP bleiben.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.config import get_settings
from kickbase_agent.db.models import Base, Intent
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app

TOKEN = "s3cret-token"

#: Alle mutierenden Routen der API — jede einzelne muss das Gate tragen.
MUTATING = [
    ("/api/intents", {"kind": "bid", "player_id": "1", "amount": 1_000_000,
                      "rationale": "r", "emil_go": "go"}),
    ("/api/intents/1/veto", {}),
    ("/api/intents/1/adjust", {"pct": 10}),
    ("/api/intents/1/execute-now", None),
    ("/api/refresh", None),
    ("/api/learning/evidence", {"learning_id": "L-065", "verdict": "confirm",
                                "note": "n", "ref": "bid:1"}),
]


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        now = datetime.now(UTC)
        s.add(Intent(
            created_at=now, updated_at=now, kind="bid", player_id="15589",
            player_name="Otávio", team_id="4", position=2, amount=9_000_000,
            rationale="r", sources={}, listing_type="manager",
            deadline=None, execute_at=now, status="planned",
        ))
        s.commit()
        yield s


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: db
    return TestClient(app, headers={"X-Requested-With": "kba"})


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setattr(get_settings(), "kba_api_write_token", TOKEN)


@pytest.mark.parametrize("path,body", MUTATING)
def test_every_mutating_route_refuses_without_token(client, armed, path, body):
    """Der Nachbar-Container-Fall: kein Traefik, kein Header → 401 überall."""
    r = client.post(path, json=body) if body is not None else client.post(path)
    assert r.status_code == 401, f"{path} ist ungeschützt!"
    assert "Schreib-Gate" in r.text


@pytest.mark.parametrize("path,body", MUTATING)
def test_forged_token_is_refused(client, armed, path, body):
    """Ein geratener Wert hilft nicht — und durch Traefik käme er gar nicht erst
    an (customrequestheaders überschreibt mitgeschickte Werte)."""
    headers = {"X-KBA-Write-Token": "geraten"}
    r = (client.post(path, json=body, headers=headers) if body is not None
         else client.post(path, headers=headers))
    assert r.status_code == 401


@pytest.mark.parametrize("path,body", MUTATING)
def test_correct_token_passes_the_gate(client, armed, path, body):
    """Mit dem Traefik-Header läuft alles wie bisher — 401 darf NICHT kommen."""
    headers = {"X-KBA-Write-Token": TOKEN}
    r = (client.post(path, json=body, headers=headers) if body is not None
         else client.post(path, headers=headers))
    assert r.status_code != 401


def test_reads_are_never_gated(client, armed):
    """Lesen bleibt offen — das Cockpit rendert serverseitig, das Gate schützt
    nur Schreibzugriffe."""
    assert client.get("/api/intents").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_unset_token_keeps_legacy_behaviour(client, monkeypatch):
    """Leeres Setting = Gate aus (lokale Entwicklung); die App warnt dafür beim
    Start laut. Auf der Box ist der Wert gesetzt."""
    monkeypatch.setattr(get_settings(), "kba_api_write_token", "")
    assert client.post("/api/intents/1/veto", json={}).status_code != 401
