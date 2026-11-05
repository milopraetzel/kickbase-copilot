"""Steuerzentrale-API: /api/intents, /api/protocol, /api/health.

In-Memory-SQLite (Base.metadata.create_all), TestClient, Session per
dependency_overrides — wie tests/server/test_intel.py. Jede Mutation darf NUR
Intent-Zeilen ändern; transfer_actions bleiben unangetastet.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, Intent, Player, TransferAction
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app


@pytest.fixture(autouse=True)
def _no_decisions(monkeypatch):
    """Protokoll-Tests isolieren: keine echten Wissens-/Commit-/Feed-Eintraege einmischen
    (Zusatzquellen von Protokoll v2 haben eigene Tests in test_protocol_v2.py)."""
    import kickbase_agent.knowledge as kn
    import kickbase_agent.knowledge.commits as kc
    import kickbase_agent.server.routes.live as live
    monkeypatch.setattr(kn, "decision_entries", lambda limit=60: [])
    monkeypatch.setattr(kc, "system_entries", lambda limit=30: [])
    monkeypatch.setattr(live, "cached_feed", lambda max_items=200: [])

# Echte Jetzt-Zeit als Anker (Offsets relativ), damit seconds_left/updated_at real sind.
NOW = datetime.now(UTC).replace(microsecond=0)


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
    # CSRF-Guard (23.08.): mutierende /api-Requests brauchen X-Requested-With: kba —
    # der Cockpit-fetch setzt ihn; die Tests tun es global.
    return TestClient(app, headers={"X-Requested-With": "kba"})


def _intent(**over) -> Intent:
    base = dict(
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
        kind="bid",
        player_id="7291",
        player_name="Onyedika",
        team_id="15",
        position=3,
        amount=14_300_000,
        rationale="Score 0.81, LI: Startelf, KB-Stufe 3",
        sources={"score": 0.81, "li_status": "start", "rule_id": "buy_explosion"},
        listing_type="kickbase",
        deadline=NOW + timedelta(minutes=40),
        execute_at=NOW + timedelta(minutes=25),
        status="planned",
    )
    base.update(over)
    return Intent(**base)


def _action(**over) -> TransferAction:
    base = dict(
        league_id="L1",
        action_type="bid",
        player_id="7291",
        amount=14_300_000,
        status="executed",
        idempotency_key=f"k-{over.get('player_id', '7291')}-{over.get('amount', 0)}-"
        f"{over.get('created_at', NOW).isoformat()}",
        rule_id="intent-bid",
        rationale="Aktion-Begründung",
        dry_run=False,
        raw_response={"secret_like": "SHOULD-NOT-LEAK"},
        payload={"offer_id": "o-1"},
        channel_msg_id="msg-123",
        created_at=NOW - timedelta(minutes=10),
        resolved_at=NOW - timedelta(minutes=9),
    )
    base.update(over)
    return TransferAction(**base)


def _seed(db: Session, *rows) -> None:
    for r in rows:
        db.add(r)
    db.commit()


# ----------------------------------------------------------------- GET /intents


def test_list_intents_default_only_planned_sorted_by_execute_at(client, db):
    _seed(
        db,
        _intent(player_id="b", execute_at=NOW + timedelta(minutes=60)),
        _intent(player_id="a", execute_at=NOW + timedelta(minutes=5)),
        _intent(player_id="x", status="executed", updated_at=NOW - timedelta(minutes=1)),
    )
    r = client.get("/api/intents")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"now", "count", "intents"}
    assert body["count"] == 2
    assert [i["player_id"] for i in body["intents"]] == ["a", "b"]
    assert all(i["status"] == "planned" for i in body["intents"])
    # now ist ISO mit Zeitzone
    assert datetime.fromisoformat(body["now"]).tzinfo is not None


def test_list_intents_all_planned_first_then_rest_by_updated_desc(client, db):
    _seed(
        db,
        _intent(player_id="old", status="vetoed", updated_at=NOW - timedelta(hours=5)),
        _intent(player_id="p2", execute_at=NOW + timedelta(minutes=50)),
        _intent(player_id="new", status="executed", updated_at=NOW - timedelta(minutes=2)),
        _intent(player_id="p1", execute_at=NOW + timedelta(minutes=10)),
    )
    r = client.get("/api/intents?status=all")
    assert r.status_code == 200
    assert [i["player_id"] for i in r.json()["intents"]] == ["p1", "p2", "new", "old"]


def test_list_intents_single_status_filter_and_limit(client, db):
    _seed(
        db,
        _intent(player_id="p1"),
        _intent(player_id="v1", status="vetoed", updated_at=NOW - timedelta(minutes=3)),
        _intent(player_id="v2", status="vetoed", updated_at=NOW - timedelta(minutes=1)),
    )
    r = client.get("/api/intents?status=vetoed&limit=1")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["intents"][0]["player_id"] == "v2"


def test_list_intents_rejects_unknown_status_and_bad_limit(client):
    assert client.get("/api/intents?status=bogus").status_code == 422
    assert client.get("/api/intents?limit=0").status_code == 422


def test_intent_json_fields_and_derived_values(client, db):
    execute_at = datetime.now(UTC) + timedelta(seconds=600)
    deadline = execute_at + timedelta(minutes=15)
    _seed(db, _intent(execute_at=execute_at, deadline=deadline))
    body = client.get("/api/intents").json()
    it = body["intents"][0]

    # alle Modellfelder
    for key in (
        "id", "created_at", "updated_at", "kind", "player_id", "player_name", "team_id",
        "position", "amount", "rationale", "sources", "listing_type", "deadline",
        "execute_at", "status", "emil_note", "adjusted_from", "executed_action_id", "result",
    ):
        assert key in it, key
    # abgeleitete Felder
    assert 590 <= it["seconds_left"] <= 600
    assert 1490 <= it["deadline_seconds_left"] <= 1500
    assert it["amount_fmt"] == "14,30 M"
    assert it["big"] is True
    assert it["club"] == "M'gladbach"
    assert it["pos"] == "MF"
    assert it["img"] is None
    assert it["sources"]["rule_id"] == "buy_explosion"
    # Zeiten: ISO mit Zeitzone (UTC)
    for key in ("created_at", "updated_at", "deadline", "execute_at"):
        assert datetime.fromisoformat(it[key]).tzinfo is not None, key
    assert it["execute_at"].endswith("+00:00")


def test_intent_json_small_amount_and_fallbacks(client, db):
    _seed(
        db,
        _intent(
            kind="sell", amount=0, team_id="999", position=0, deadline=None,
            execute_at=NOW - timedelta(minutes=5),
        ),
    )
    it = client.get("/api/intents").json()["intents"][0]
    assert it["big"] is False
    assert it["seconds_left"] == 0  # nie negativ
    assert it["deadline_seconds_left"] is None
    assert it["club"] == "999"  # unbekanntes Team: team_id als Fallback
    assert it["pos"] == ""
    assert it["amount_fmt"] == "0 €"


def test_get_single_intent_and_404(client, db):
    _seed(db, _intent())
    r = client.get("/api/intents/1")
    assert r.status_code == 200
    assert r.json()["intent"]["id"] == 1
    assert client.get("/api/intents/99").status_code == 404


# ----------------------------------------------------------------- veto


def test_veto_sets_status_note_updated_at(client, db):
    _seed(db, _intent())
    r = client.post("/api/intents/1/veto", json={"note": "  Khannouss bleibt  "})
    assert r.status_code == 200
    it = r.json()["intent"]
    assert it["status"] == "vetoed"
    assert it["emil_note"] == "Khannouss bleibt"
    assert datetime.fromisoformat(it["updated_at"]) >= NOW

    db.expire_all()
    row = db.get(Intent, 1)
    assert row.status == "vetoed"
    assert row.emil_note == "Khannouss bleibt"


def test_veto_without_body_is_fine(client, db):
    _seed(db, _intent())
    r = client.post("/api/intents/1/veto")
    assert r.status_code == 200
    assert r.json()["intent"]["status"] == "vetoed"
    assert r.json()["intent"]["emil_note"] is None


def test_veto_only_from_planned_409_with_explanation(client, db):
    _seed(db, _intent(status="executed", result="executed"))
    r = client.post("/api/intents/1/veto", json={"note": "zu spät"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "executed" in detail and "geplanten" in detail
    db.expire_all()
    assert db.get(Intent, 1).status == "executed"
    assert db.get(Intent, 1).emil_note is None


def test_veto_404_unknown(client):
    assert client.post("/api/intents/42/veto", json={"note": "x"}).status_code == 404


# ----------------------------------------------------------------- adjust


def test_adjust_absolute_amount_sets_adjusted_from_once(client, db):
    _seed(db, _intent(amount=14_300_000))
    r = client.post("/api/intents/1/adjust", json={"amount": 15_000_000})
    assert r.status_code == 200
    it = r.json()["intent"]
    assert it["amount"] == 15_000_000
    assert it["adjusted_from"] == 14_300_000
    assert it["amount_fmt"] == "15,00 M"
    assert it["status"] == "planned"

    # zweites Anpassen: adjusted_from bleibt der URSPRÜNGLICHE Betrag
    r2 = client.post("/api/intents/1/adjust", json={"amount": 16_000_000})
    assert r2.status_code == 200
    assert r2.json()["intent"]["amount"] == 16_000_000
    assert r2.json()["intent"]["adjusted_from"] == 14_300_000


def test_adjust_ceiling_blocks_absurd_amounts(client, db):
    """Tiefenverteidigung (24.08.): `adjust` hatte keine Obergrenze — jeder Betrag
    ab 1 € ging durch, und `execute-now` haette ihn ohne Veto-Fenster losgeschickt.
    Die echte Preisgrenze zieht der Executor (110 % MW); hier entsteht die absurde
    Zeile gar nicht erst."""
    _seed(db, _intent(amount=10_000_000))
    r = client.post("/api/intents/1/adjust", json={"amount": 20_000_000})
    assert r.status_code == 400
    assert "Anpass-Grenze" in r.json()["detail"]
    # Betrag steht unveraendert in der DB
    assert client.get("/api/intents/1").json()["intent"]["amount"] == 10_000_000


def test_adjust_ceiling_leaves_emils_buttons_intact(client, db):
    """+20 % mehrfach bleibt moeglich — die Grenze bezieht sich auf den
    URSPRUENGLICH geplanten Betrag (1,5x), nicht auf den letzten Stand."""
    _seed(db, _intent(amount=10_000_000))
    assert client.post("/api/intents/1/adjust", json={"pct": 20}).status_code == 200
    r2 = client.post("/api/intents/1/adjust", json={"pct": 20})   # 14,4 M
    assert r2.status_code == 200 and r2.json()["intent"]["amount"] == 14_400_000
    r3 = client.post("/api/intents/1/adjust", json={"pct": 20})   # 17,28 M > 15 M
    assert r3.status_code == 400


def test_adjust_pct(client, db):
    _seed(db, _intent(amount=10_000_000))
    r = client.post("/api/intents/1/adjust", json={"pct": 10})
    assert r.status_code == 200
    assert r.json()["intent"]["amount"] == 11_000_000
    assert r.json()["intent"]["adjusted_from"] == 10_000_000

    r2 = client.post("/api/intents/1/adjust", json={"pct": -50})
    assert r2.status_code == 200
    assert r2.json()["intent"]["amount"] == 5_500_000
    assert r2.json()["intent"]["adjusted_from"] == 10_000_000


@pytest.mark.parametrize(
    "payload",
    [
        {},  # nichts
        {"amount": 1, "pct": 5},  # beides
        {"amount": 0},
        {"amount": -5},
        {"pct": -51},
        {"pct": 101},
        {"amount": "viel"},
    ],
)
def test_adjust_validation_422(client, db, payload):
    _seed(db, _intent())
    r = client.post("/api/intents/1/adjust", json=payload)
    assert r.status_code == 422, payload
    db.expire_all()
    assert db.get(Intent, 1).amount == 14_300_000
    assert db.get(Intent, 1).adjusted_from is None


def test_adjust_sell_intent_400(client, db):
    _seed(db, _intent(kind="sell", amount=0))
    r = client.post("/api/intents/1/adjust", json={"pct": 10})
    assert r.status_code == 400
    assert "Sofortverkauf" in r.json()["detail"] or "sell" in r.json()["detail"]


def test_adjust_only_from_planned_409(client, db):
    _seed(db, _intent(status="vetoed"))
    r = client.post("/api/intents/1/adjust", json={"amount": 1_000_000})
    assert r.status_code == 409
    assert client.post("/api/intents/9/adjust", json={"amount": 1}).status_code == 404


def test_adjust_is_atomic_against_concurrent_amount_change(client, db, monkeypatch):
    """Planner ändert den Betrag zwischen Lesen und Schreiben -> 409 statt Überschreiben."""
    from sqlalchemy import update

    from kickbase_agent.server.routes import intents as mod

    _seed(db, _intent(amount=10_000_000))
    real_now = mod._now

    def now_with_race():
        db.execute(update(Intent).where(Intent.id == 1).values(amount=12_000_000))
        db.commit()
        return real_now()

    monkeypatch.setattr(mod, "_now", now_with_race)
    r = client.post("/api/intents/1/adjust", json={"pct": 10})
    assert r.status_code == 409
    assert "12,00 M" in r.json()["detail"]
    db.expire_all()
    row = db.get(Intent, 1)
    assert row.amount == 12_000_000  # der fremde Stand bleibt, kein blindes +10 % auf 10 M
    assert row.adjusted_from is None


def test_veto_is_atomic_against_concurrent_execution(client, db, monkeypatch):
    """Executor führt aus, während Emil klickt -> 409, Status bleibt executed."""
    from sqlalchemy import update

    from kickbase_agent.server.routes import intents as mod

    _seed(db, _intent())
    real_now = mod._now

    def now_with_race():
        db.execute(update(Intent).where(Intent.id == 1).values(status="executed"))
        db.commit()
        return real_now()

    monkeypatch.setattr(mod, "_now", now_with_race)
    r = client.post("/api/intents/1/veto", json={"note": "stop"})
    assert r.status_code == 409
    db.expire_all()
    assert db.get(Intent, 1).status == "executed"
    assert db.get(Intent, 1).emil_note is None


# ----------------------------------------------------------------- execute-now


def test_execute_now_sets_execute_at_now_and_explains(client, db):
    _seed(db, _intent(execute_at=NOW + timedelta(hours=2)))
    before = datetime.now(UTC)
    r = client.post("/api/intents/1/execute-now")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"intent", "note"}
    assert "Executor" in body["note"] and "5 min" in body["note"]
    it = body["intent"]
    assert it["status"] == "planned"  # die API führt NICHT aus
    assert it["seconds_left"] == 0
    exec_at = datetime.fromisoformat(it["execute_at"])
    assert before - timedelta(seconds=1) <= exec_at <= datetime.now(UTC) + timedelta(seconds=1)
    assert it["executed_action_id"] is None
    assert db.execute(select(TransferAction)).scalars().all() == []  # kein Write


def test_execute_now_only_from_planned(client, db):
    _seed(db, _intent(status="failed"))
    assert client.post("/api/intents/1/execute-now").status_code == 409
    assert client.post("/api/intents/77/execute-now").status_code == 404


# ----------------------------------------------------------------- protocol


def test_protocol_mixes_actions_and_intents_newest_first(client, db):
    _seed(
        db,
        Player(id="555", kickbase_player_id="555", name="Musiala", position=3, team="2"),
        _action(
            player_id="555", amount=7_000_000, status="skipped_dry_run", dry_run=True,
            rule_id="shadow-scan", created_at=NOW - timedelta(hours=3), resolved_at=None,
        ),
        _action(
            player_id="9001", action_type="bonus", amount=None, status="executed",
            rule_id="bonus", created_at=NOW - timedelta(hours=6),
            resolved_at=NOW - timedelta(hours=6),
        ),
        _intent(
            player_id="7291", status="vetoed", emil_note="lieber Khannouss",
            updated_at=NOW - timedelta(hours=1),
        ),
        _intent(
            player_id="7292", player_name="Olise", status="planned", amount=15_000_000,
            adjusted_from=14_000_000, updated_at=NOW - timedelta(minutes=30),
        ),
    )
    r = client.get("/api/protocol")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"now", "count", "entries"}
    assert body["count"] == 4
    entries = body["entries"]
    # neueste zuerst
    ts = [e["ts"] for e in entries]
    assert ts == sorted(ts, reverse=True)
    assert [e["kind"] for e in entries] == ["intent", "intent", "action", "action"]

    olise = entries[0]
    assert olise["title"] == "Gebot 15,00 M auf Olise — geplant"
    assert olise["emil"] == "Betrag angepasst: 14,00 M → 15,00 M"
    assert olise["sources"]["rule_id"] == "buy_explosion"
    assert olise["rule_id"] == "buy_explosion"  # aus sources, ohne verknüpfte Aktion
    assert olise["why"].startswith("Score 0.81")
    assert olise["status"] == "planned"
    assert olise["intent_kind"] == "bid"

    veto = entries[1]
    assert veto["title"] == "Gebot 14,30 M auf Onyedika — übersprungen"
    assert veto["emil"] == "Notiz: lieber Khannouss"
    assert veto["status"] == "vetoed"

    shadow = entries[2]
    assert shadow["kind"] == "action"
    assert shadow["title"] == "Gebot 7,00 M auf Musiala — Schatten (nicht ausgeführt)"
    assert shadow["dry_run"] is True
    assert shadow["status"] == "skipped_dry_run"
    assert shadow["why"] == "Aktion-Begründung"
    assert shadow["sources"] is None
    assert shadow["emil"] is None
    assert shadow["rule_id"] == "shadow-scan"
    assert shadow["player_name"] == "Musiala"  # Name aus players-Tabelle
    assert shadow["ts"] == shadow["created_at"]  # ohne resolved_at

    bonus = entries[3]
    assert bonus["title"] == "Prämie kassiert — ausgeführt"
    assert bonus["action_type"] == "bonus"
    assert bonus["amount"] is None and bonus["amount_fmt"] is None


def test_protocol_merges_linked_action_into_intent_entry(client, db):
    _seed(db, _action(player_id="7291", amount=14_300_000, status="executed", rule_id="intent-bid"))
    db.add(
        _intent(
            status="executed", result="executed", executed_action_id=1,
            updated_at=NOW - timedelta(minutes=9),
        )
    )
    db.commit()
    entries = client.get("/api/protocol").json()["entries"]
    assert len(entries) == 1  # Aktion steckt im Intent-Eintrag, kein Duplikat
    e = entries[0]
    assert e["kind"] == "intent"
    assert e["title"] == "Gebot 14,30 M auf Onyedika — ausgeführt"
    assert e["executed_action_id"] == 1
    assert e["action_status"] == "executed"
    assert e["rule_id"] == "intent-bid"
    assert e["dry_run"] is False
    assert e["result"] == "executed"


def test_protocol_action_name_fallbacks_and_limit(client, db):
    _seed(
        db,
        _action(player_id="unknown-1", amount=1_000_000, created_at=NOW - timedelta(hours=2)),
        _intent(player_id="unknown-1", player_name="Neuzugang", status="expired",
                updated_at=NOW - timedelta(hours=1)),
        _action(player_id="nobody", amount=2_000_000, created_at=NOW - timedelta(hours=4),
                resolved_at=NOW - timedelta(hours=4)),
    )
    entries = client.get("/api/protocol").json()["entries"]
    by_pid = {e["player_id"]: e for e in entries if e["kind"] == "action"}
    assert by_pid["unknown-1"]["player_name"] == "Neuzugang"  # Name über Intent gleicher ID
    assert by_pid["nobody"]["player_name"] == "Spieler nobody"

    limited = client.get("/api/protocol?limit=2").json()
    assert limited["count"] == 2
    assert len(limited["entries"]) == 2
    assert client.get("/api/protocol?limit=0").status_code == 422


def test_protocol_and_intents_leak_no_secrets(client, db):
    _seed(
        db,
        _action(raw_response={"token": "SHOULD-NOT-LEAK"}, payload={"pw": "SHOULD-NOT-LEAK"}),
        _intent(),
    )
    for path in ("/api/protocol", "/api/intents?status=all"):
        raw = json.dumps(client.get(path).json())
        assert "SHOULD-NOT-LEAK" not in raw, path
        assert "msg-123" not in raw, path  # channel_msg_id
        for key in ("raw_response", "payload", "idempotency_key", "channel_msg_id", "league_id"):
            assert f'"{key}"' not in raw, (path, key)


def test_protocol_empty(client):
    body = client.get("/api/protocol").json()
    assert body["count"] == 0 and body["entries"] == []


# ----------------------------------------------------------------- health


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}



def test_csrf_guard_rejects_mutation_without_header(db: Session):
    """Ein POST ohne den Custom-Header (z. B. fremdes HTML-Formular mit Browser-
    Credentials) wird mit 403 abgewiesen; GET bleibt frei."""
    from kickbase_agent.server import deps
    from kickbase_agent.server.app import create_app
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: db
    raw = TestClient(app)
    assert raw.get("/api/intents").status_code == 200
    r = raw.post("/api/intents/1/veto", json={"note": "x"})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]


def test_veto_im_executing_fenster_wird_vorgemerkt(client, db):
    """Review 31.08.: Veto trifft den laufenden Versuch — 200 mit Vormerkung
    (status bleibt executing, veto_requested_at gesetzt, erklaerende note)
    statt spurlosem 409; der Executor loest die Vormerkung als vetoed ein."""
    _seed(db, _intent(status="executing"))
    r = client.post("/api/intents/1/veto", json={"note": None})
    assert r.status_code == 200
    body = r.json()
    assert body["intent"]["status"] == "executing"
    assert body["intent"]["veto_requested_at"] is not None
    assert "vorgemerkt" in body["note"]

    db.expire_all()
    assert db.get(Intent, 1).veto_requested_at is not None
