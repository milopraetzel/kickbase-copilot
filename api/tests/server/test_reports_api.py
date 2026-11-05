"""Steuerzentrale-API: /api/reports (Liste/Filter/Vorschau) + /api/reports/{id} (Volltext).

In-Memory-SQLite (Base.metadata.create_all), TestClient, Session per
dependency_overrides — wie tests/server/test_intents_api.py. Reine Lese-Endpunkte.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, Report
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app
from kickbase_agent.server.routes.reports import PREVIEW_CHARS, preview_of

NOW = datetime.now(UTC).replace(microsecond=0)

BODY = (
    "🤖 **Schatten-Scan** — was der Automat (Phase 2) heute getan HÄTTE\n"
    "KÄUFE (2):\n"
    "• Wirtz: 1,2M · Score 0.81 (✅ auto: Trading ≤1,5M — JA)\n"
    "   • Olise: 6,13M · Score 0.77 (⇒ 🔐 Emil)\n"
    "Bilanz: 1× ✅ wäre autonom · 1× 🔐 wäre Freigabe — 2 neu gestaged."
)


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine, future=True)


@pytest.fixture
def db() -> Session:
    return _session()


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: db
    return TestClient(app)


def _report(**over) -> Report:
    base = dict(
        created_at=NOW - timedelta(hours=1),
        kind="schatten",
        title="Schatten-Scan — was der Automat (Phase 2) heute getan HÄTTE",
        body=BODY,
        source="worker:schatten",
        meta={"delivered": True, "chars": len(BODY)},
    )
    base.update(over)
    return Report(**base)


def _seed(db: Session, *rows) -> None:
    for r in rows:
        db.add(r)
    db.commit()


# ----------------------------------------------------------------- GET /reports


def test_list_reports_newest_first_with_preview_and_summary_fields(client, db):
    _seed(
        db,
        _report(title="alt", created_at=NOW - timedelta(days=2)),
        _report(title="neu", created_at=NOW - timedelta(minutes=5), kind="briefing",
                source="worker:briefing"),
        _report(title="mittel", created_at=NOW - timedelta(hours=3), kind="recherche",
                source="mcp:discord_post", meta={"delivered": False, "chars": 10}),
    )
    r = client.get("/api/reports")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"now", "count", "reports"}
    assert body["count"] == 3
    assert [x["title"] for x in body["reports"]] == ["neu", "mittel", "alt"]
    first = body["reports"][0]
    assert set(first) == {
        "id", "created_at", "kind", "title", "source", "chars", "preview", "delivered",
    }
    assert "body" not in first  # Liste trägt nur die Vorschau
    assert first["kind"] == "briefing" and first["source"] == "worker:briefing"
    assert first["chars"] == len(BODY) and first["delivered"] is True
    assert body["reports"][1]["delivered"] is False
    assert datetime.fromisoformat(first["created_at"]).tzinfo is not None
    assert datetime.fromisoformat(body["now"]).tzinfo is not None


def test_list_reports_preview_is_first_200_chars_without_newlines(client, db):
    _seed(db, _report())
    preview = client.get("/api/reports").json()["reports"][0]["preview"]
    assert "\n" not in preview
    assert preview.startswith("🤖 **Schatten-Scan** — was der Automat (Phase 2) heute getan HÄTTE "
                              "KÄUFE (2): • Wirtz")
    assert len(preview) <= PREVIEW_CHARS
    assert len(preview_of("x" * 500)) == PREVIEW_CHARS
    assert preview_of("a\n\n   b\r\nc  d") == "a b c  d"
    assert preview_of("") == ""


def test_list_reports_filters_by_kind_and_honours_limit(client, db):
    _seed(
        db,
        *[_report(title=f"s{i}", created_at=NOW - timedelta(minutes=i)) for i in range(4)],
        _report(title="b", kind="briefing"),
        _report(title="p", kind="planner"),
    )
    body = client.get("/api/reports", params={"kind": "schatten"}).json()
    assert body["count"] == 4 and {x["kind"] for x in body["reports"]} == {"schatten"}

    body = client.get("/api/reports", params={"kind": "briefing"}).json()
    assert [x["title"] for x in body["reports"]] == ["b"]

    body = client.get("/api/reports", params={"limit": 2}).json()
    assert body["count"] == 2 and len(body["reports"]) == 2

    body = client.get("/api/reports", params={"kind": "schatten", "limit": 3}).json()
    assert [x["title"] for x in body["reports"]] == ["s0", "s1", "s2"]

    # unbekannte Art: leer, kein Fehler
    body = client.get("/api/reports", params={"kind": "gibtsnicht"}).json()
    assert body == {"now": body["now"], "count": 0, "reports": []}


def test_list_reports_default_limit_30_and_validation(client, db):
    _seed(db, *[_report(title=f"r{i}", created_at=NOW - timedelta(minutes=i)) for i in range(35)])
    body = client.get("/api/reports").json()
    assert body["count"] == 30
    assert client.get("/api/reports", params={"limit": 0}).status_code == 422
    assert client.get("/api/reports", params={"limit": 201}).status_code == 422


def test_list_reports_delivered_null_when_meta_missing(client, db):
    _seed(db, _report(meta=None), _report(meta={"chars": 3}))
    rows = client.get("/api/reports").json()["reports"]
    assert [x["delivered"] for x in rows] == [None, None]


def test_list_reports_empty(client):
    body = client.get("/api/reports").json()
    assert body["count"] == 0 and body["reports"] == []


# ------------------------------------------------------------ GET /reports/{id}


def test_get_report_returns_full_body_and_meta(client, db):
    _seed(db, _report())
    rid = client.get("/api/reports").json()["reports"][0]["id"]
    r = client.get(f"/api/reports/{rid}")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"report"}
    rep = body["report"]
    assert rep["id"] == rid and rep["body"] == BODY
    assert rep["kind"] == "schatten" and rep["source"] == "worker:schatten"
    assert rep["title"].startswith("Schatten-Scan")
    assert rep["meta"] == {"delivered": True, "chars": len(BODY)}
    assert rep["delivered"] is True and rep["chars"] == len(BODY)
    assert rep["preview"] == preview_of(BODY)


def test_get_report_404_for_unknown_id(client):
    r = client.get("/api/reports/4711")
    assert r.status_code == 404
    assert r.json()["detail"] == "Bericht 4711 nicht gefunden"
