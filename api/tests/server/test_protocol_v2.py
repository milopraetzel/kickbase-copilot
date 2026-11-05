"""Protokoll v2 (/api/protocol): Zusatzquellen system/summary/transfer/league.

Jede Quelle wird an ihrer Import-Stelle gemockt (Commits via KBA_COMMITS_PATH,
Feed via routes/live.cached_feed) — kein Netz, keine echten Wissens-Dateien.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import kickbase_agent.server.routes.live as live
from kickbase_agent.db.models import Base, Report, TransferAction
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app

NOW = datetime.now(UTC).replace(microsecond=0)

FEED = [
    {"i": "1", "t": 15, "dt": (NOW - timedelta(hours=1)).isoformat(),
     "data": {"byr": "Emil", "pn": "Olise", "trp": 68_017_197}},
    {"i": "2", "t": 15, "dt": (NOW - timedelta(hours=2)).isoformat(),
     "data": {"byr": "Osei", "pn": "Vozar", "trp": 1_280_015}},
    {"i": "3", "t": 22, "dt": (NOW - timedelta(hours=3)).isoformat(),
     "data": {"bn": 50_000, "day": 6}},
]

COMMIT = {
    "sha": "c" * 40,
    "date": (NOW - timedelta(hours=4)).isoformat(),
    "subject": "feat(zentrale): Protokoll v2",
    "body": "Systemaenderungen sichtbar machen.",
}


@pytest.fixture(autouse=True)
def _isolated_sources(monkeypatch, tmp_path):
    """Wissens-Eintraege leer, Commits aus tmp-Datei, Feed-Cache deterministisch."""
    import kickbase_agent.knowledge as kn

    monkeypatch.setattr(kn, "decision_entries", lambda limit=60: [])
    commits_file = tmp_path / "commits.json"
    commits_file.write_text(json.dumps([COMMIT]), encoding="utf-8")
    monkeypatch.setenv("KBA_COMMITS_PATH", str(commits_file))
    monkeypatch.setattr(live, "cached_feed", lambda max_items=200: [dict(i) for i in FEED])
    live.reset_caches()
    yield
    live.reset_caches()


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


def _seed_report(db: Session, kind: str, title: str, body: str) -> None:
    db.add(
        Report(
            created_at=NOW - timedelta(minutes=30), kind=kind, title=title,
            body=body, source="worker:test", meta={"delivered": True},
        )
    )
    db.commit()


def _seed_action(db: Session) -> None:
    db.add(
        TransferAction(
            league_id="L1", action_type="bid", player_id="7291", amount=14_300_000,
            status="executed", idempotency_key="k-1", rule_id="intent-bid",
            rationale="Score 0.81", dry_run=False,
            created_at=NOW - timedelta(minutes=10), resolved_at=NOW - timedelta(minutes=9),
        )
    )
    db.commit()


def _entries(client: TestClient) -> list[dict]:
    r = client.get("/api/protocol")
    assert r.status_code == 200
    return r.json()["entries"]


# ------------------------------------------------------------------ Zusatzquellen


def test_protocol_contains_all_new_kinds(client, db):
    _seed_action(db)
    _seed_report(db, "briefing", "Abend-Briefing SP1", "Zeile 1\nZeile 2 mit Lage.")
    entries = _entries(client)
    kinds = {e["kind"] for e in entries}
    assert {"action", "system", "summary", "transfer", "league"} <= kinds
    # Basisfelder auf JEDEM Eintrag (Vertrag mit der Web-App)
    for e in entries:
        for key in ("kind", "ts", "title", "why", "sources", "status", "emil"):
            assert key in e, (e["kind"], key)


def test_system_entry_from_commits(client, db):
    e = next(x for x in _entries(client) if x["kind"] == "system")
    assert e["title"] == "System: feat(zentrale): Protokoll v2"
    assert e["why"] == "Systemaenderungen sichtbar machen."
    assert e["sources"] == {"commit": "c" * 7}
    assert e["status"] == "deployt"


def test_summary_entry_from_report(client, db):
    _seed_report(db, "recherche", "Morgen-Recherche", "Erste Zeile.\nZweite Zeile.")
    e = next(x for x in _entries(client) if x["kind"] == "summary")
    assert e["title"] == "Morgen-Recherche"
    assert e["why"] == "Erste Zeile. Zweite Zeile."  # Vorschau: Umbrueche -> Leerzeichen
    assert e["sources"]["kind"] == "recherche"
    assert isinstance(e["sources"]["report_id"], int)
    assert e["status"] == "bericht"
    assert e["report_kind"] == "recherche"


def test_summary_only_for_briefing_recherche_schatten(client, db):
    _seed_report(db, "info", "Kurzinfo", "x" * 200)
    _seed_report(db, "planner", "Planner-Lauf", "y" * 200)
    _seed_report(db, "schatten", "Schatten-Scan 22:20", "z" * 200)
    summaries = [e for e in _entries(client) if e["kind"] == "summary"]
    assert [s["sources"]["kind"] for s in summaries] == ["schatten"]


def test_transfer_and_league_entries_from_feed_cache(client, db):
    entries = _entries(client)
    zuschlag = next(e for e in entries if e["kind"] == "transfer" and "Zuschlag" in e["title"])
    assert zuschlag["title"] == "Zuschlag: Olise für 68,02 M"
    assert zuschlag["sources"]["buyer"] == "Emil"
    praemie = next(e for e in entries if e["status"] == "praemie")
    assert praemie["title"] == "Prämie kassiert (+50 k)"
    liga = next(e for e in entries if e["kind"] == "league")
    assert liga["title"] == "Liga: Osei kauft Vozar für 1,28 M"


def test_entries_sorted_newest_first_and_limit_applies(client, db):
    _seed_action(db)
    _seed_report(db, "briefing", "Briefing", "b" * 150)
    entries = _entries(client)
    ts = [e["ts"] for e in entries]
    assert ts == sorted(ts, reverse=True)
    assert entries[0]["kind"] == "action"  # juengster Eintrag (10 min alt)

    limited = client.get("/api/protocol?limit=2").json()
    assert limited["count"] == 2 and len(limited["entries"]) == 2


def test_protocol_survives_broken_sources(client, db, monkeypatch):
    _seed_action(db)

    def boom(*args, **kwargs):
        raise RuntimeError("kaputt")

    import kickbase_agent.knowledge.commits as kc

    monkeypatch.setattr(kc, "system_entries", boom)
    monkeypatch.setattr(live, "cached_feed", boom)
    entries = _entries(client)
    assert [e["kind"] for e in entries] == ["action"]  # Kernprotokoll bleibt


def test_get_endpoints_need_no_csrf_header_but_mutations_still_do(client, db):
    bare = TestClient(client.app)  # ohne X-Requested-With
    assert bare.get("/api/protocol").status_code == 200
    r = bare.post("/api/intents/1/veto", json={})
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]
