"""Lern-Schicht-API: GET /api/learning — Vertrag, leere DB, echte Daten (Treffer + Tipp).

In-Memory-SQLite + TestClient, Session per dependency_overrides (wie test_intents_api.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import (
    Base,
    CommunityTip,
    Player,
    PlayerHistory,
    TransferAction,
)
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app

#: 22:20 Berlin (CEST) = 20:20 UTC am 20.08. — nach dem 22:00-MW-Update.
EVENING = datetime(2026, 8, 20, 20, 20, tzinfo=UTC)


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
def client(db: Session, tmp_path, monkeypatch) -> TestClient:
    # hermetisch: eigene LEARNINGS-Datei statt der Repo-Datei (Env schlägt Default-Pfade)
    md = tmp_path / "LEARNINGS.md"
    md.write_text(
        "## Meta-Wissen\n- **Steuerzentrale (23.08.):** Vorhaben statt Vorab-Freigabe.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KBA_LEARNINGS_PATH", str(md))
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: db
    return TestClient(app)


def _history(db: Session, pid: str, values: dict[int, int]) -> None:
    for off, mv in values.items():
        day = EVENING.date() + timedelta(days=off)
        db.add(PlayerHistory(player_id=pid, recorded_at=datetime(day.year, day.month, day.day,
                                                                 tzinfo=UTC),
                             market_value=mv, points=0, is_starter=False))
    db.commit()


def test_learning_on_empty_db_returns_contract_with_null_values(client):
    r = client.get("/api/learning")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "now", "evaluation", "learnings", "parameters", "open_calibrations",
        "bilanz", "to_fix", "selbstverbesserungen",
    }
    # Emils Tabelle kommt mit derselben Antwort — sonst faellt sie beim Nachladen
    # aus der Seite. Auf leerer DB stehen nur die Decision-Records aus dem Repo.
    assert isinstance(body["selbstverbesserungen"], list)
    assert all(r["kind"] == "parameter" for r in body["selbstverbesserungen"])
    assert datetime.fromisoformat(body["now"]).tzinfo is not None
    ev = body["evaluation"]
    assert set(ev) >= {"generated_at", "buy_hits", "shadow", "executed", "tips", "flips",
                       "flips_total_delta_eur", "lineup", "coverage"}
    for key in ("buy_hits", "shadow", "executed"):
        assert ev[key] == {
            "1d": {"n": 0, "hit_rate": None, "avg_delta_pct": None, "median_delta_pct": None},
            "7d": {"n": 0, "hit_rate": None, "avg_delta_pct": None, "median_delta_pct": None},
        }
    assert ev["tips"] == {"n": 0, "hit_rate_7d": None, "avg_delta_pct_7d": None, "items": []}
    assert ev["flips"] == [] and ev["flips_total_delta_eur"] == 0
    assert ev["lineup"] == {"status": "ab SP1 (28.08.) messbar", "n": 0}
    assert ev["coverage"]["proposals_total"] == 0
    # Vertrag seit 25.08. zusaetzlich: summary (Menschen-Sicht) + rule (was der Agent
    # anwendet) + details (Fliesstext ohne Marker). Ohne Marker faellt summary auf die
    # ersten Saetze zurueck und rule bleibt leer.
    # Ohne ID gibt es KEINE Bilanz (der Anker fehlt) — und keine erfundenen Zahlen.
    assert body["learnings"] == [{
        "id": None, "art": "fakt",
        "date": "2026-08-23", "title": "Steuerzentrale", "text": "Vorhaben statt Vorab-Freigabe.",
        "order": 0, "section": "Meta-Wissen",
        "summary": "Vorhaben statt Vorab-Freigabe.", "rule": "",
        "details": "Vorhaben statt Vorab-Freigabe.",
        "status": None,
        "evidence": {"confirm": 0, "refute": 0, "auto": 0, "agent": 0, "last_at": None},
        "evidence_items": [],
    }]
    # Der leere Zustand ist ehrlich: nichts bewaehrt, nichts zu korrigieren.
    assert body["to_fix"] == []
    assert body["bilanz"]["active_hypotheses"] == 0
    assert all(v == 0 for v in body["bilanz"]["verteilung"].values())
    params = body["parameters"]
    assert set(params) == {"history", "current"}
    # `beleg` + `einzelfall_ausnahme` seit dem Lernzyklus (26.08.): worauf sich eine
    # Aenderung stuetzt, und ob sie ueber die Mechanismus-Ausnahme lief.
    assert params["history"] and set(params["history"][0]) == {
        "date", "parameter", "old", "new", "reason", "commit", "expect", "review",
        "beleg", "einzelfall_ausnahme", "quelle",
    }
    for key in ("max_bid_pct_of_budget", "bid_overpay_fraction", "daily_buy_cap",
                "approval_threshold", "league_trade_limit_per_half"):
        assert key in params["current"]
    assert params["current"]["daily_buy_cap"] == DEFAULT_GUARDRAILS.daily_buy_cap
    assert isinstance(body["open_calibrations"], list) and body["open_calibrations"]
    assert all(isinstance(s, str) for s in body["open_calibrations"])


def test_learning_reflects_proposals_tips_and_flips(client, db):
    db.add(Player(id="7", kickbase_player_id="7", name="Michael Olise", position=3, team="2",
                  current_market_value=0))
    db.commit()
    _history(db, "7", {0: 1_000_000, 1: 1_100_000, 7: 1_250_000})
    db.add(TransferAction(
        league_id="L1", action_type="bid", player_id="7", amount=1_050_000, status="proposed",
        idempotency_key="k-shadow", rule_id="buy_explosion", rationale="Score 0.7", dry_run=True,
        created_at=EVENING, resolved_at=None,
    ))
    db.add(TransferAction(
        league_id="L1", action_type="bid", player_id="7", amount=1_000_000, status="executed",
        idempotency_key="k-real", rule_id="mcp-bid", rationale="Emil GO", dry_run=False,
        raw_response={"secret_like": "SHOULD-NOT-LEAK"},
        payload={
            "offer_id": "o-1",
            "expectation": {"p": 0.5, "kind": "kickbase"},
            "outcome": {"result": "won", "at": EVENING.isoformat()},
        },
        created_at=EVENING - timedelta(days=1), resolved_at=EVENING - timedelta(days=1),
    ))
    db.add(CommunityTip(player_name="Olise", weight=1.0, source="reddit", note="Hype",
                        created_at=EVENING))
    db.commit()

    r = client.get("/api/learning")
    assert r.status_code == 200
    ev = r.json()["evaluation"]
    # zwei Vorschlagstage (20.08. Schatten, 19.08. echt): 19.08. ohne Basiswert => nur einer messbar
    assert ev["coverage"]["proposals_total"] == 2
    assert ev["buy_hits"]["1d"] == {"n": 1, "hit_rate": 1.0, "avg_delta_pct": 10.0,
                                    "median_delta_pct": 10.0}
    assert ev["buy_hits"]["7d"]["avg_delta_pct"] == 25.0
    assert ev["shadow"]["1d"]["n"] == 1 and ev["executed"]["1d"]["n"] == 0
    tips = ev["tips"]
    assert tips["n"] == 1 and tips["hit_rate_7d"] == 1.0 and tips["avg_delta_pct_7d"] == 25.0
    assert tips["items"][0]["player_name"] == "Olise"
    assert tips["items"][0]["delta_pct_7d"] == 25.0
    flips = ev["flips"]
    assert len(flips) == 1
    assert flips[0]["name"] == "Michael Olise" and flips[0]["buy_price"] == 1_000_000
    assert flips[0]["mv_now"] == 1_250_000 and flips[0]["delta_eur"] == 250_000
    assert ev["flips_total_delta_eur"] == 250_000
    # keine Secrets/Rohdaten im JSON
    assert "SHOULD-NOT-LEAK" not in r.text and "offer_id" not in r.text


# --- Lern-Bilanz (26.08.): Status, Belege, Wochen-Kopf, Korrektur-Liste ------------


LERN_MD = """## Meta-Wissen
- [L-054] **err 5080 (24.08.):** Manager-Listings verlangen 100 % MW.
  **Regel:** Nie unter Marktwert ankern.
  **Art:** hypothese
- [L-065] **Beschaffungs-Bilanz (25.08.):** Manager-Deals scheitern.
  **Regel:** Starter ueber Kickbase-Listings holen.
  **Art:** hypothese
- [L-003] **Feld `lo` (22.08.):** Lineup-Order 0-10.
  **Art:** fakt
"""


@pytest.fixture
def lern_client(db: Session, tmp_path, monkeypatch) -> TestClient:
    md = tmp_path / "LEARNINGS.md"
    md.write_text(LERN_MD, encoding="utf-8")
    monkeypatch.setenv("KBA_LEARNINGS_PATH", str(md))
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: db
    return TestClient(app)


def _evidence(db: Session, learning_id: str, verdict: str, ref: str, days_ago: int) -> None:
    from kickbase_agent.db.models import LearningEvidence

    when = datetime.now(UTC) - timedelta(days=days_ago)
    db.add(LearningEvidence(
        learning_id=learning_id, observed_at=when, verdict=verdict, source="auto",
        ref=ref, note=f"{verdict} {ref}", rule_key="underpay-5080", created_at=when,
    ))
    db.commit()


def test_learning_payload_carries_status_evidence_and_balance(lern_client, db):
    for i in range(4):
        _evidence(db, "L-054", "confirm", f"action:{i}", days_ago=20 - i)
    _evidence(db, "L-065", "refute", "action:9", days_ago=1)
    _evidence(db, "L-065", "refute", "action:10", days_ago=1)

    body = lern_client.get("/api/learning").json()
    by_id = {e["id"]: e for e in body["learnings"]}

    bewaehrt = by_id["L-054"]
    assert bewaehrt["status"]["status"] == "bewährt" and bewaehrt["status"]["tone"] == "gut"
    assert bewaehrt["evidence"] == {
        "confirm": 4, "refute": 0, "auto": 4, "agent": 0,
        "last_at": bewaehrt["evidence"]["last_at"],
    }
    assert len(bewaehrt["evidence_items"]) == 4
    assert bewaehrt["evidence_items"][0]["source"] == "auto"

    widerlegt = by_id["L-065"]
    assert widerlegt["status"]["status"] == "widerlegt"
    assert widerlegt["status"]["needs_action"] is True

    # Ein Fakt bekommt nie eine Hypothesen-Statistik.
    assert by_id["L-003"]["status"]["status"] == "gilt"

    bilanz = body["bilanz"]
    assert bilanz["active_hypotheses"] == 2
    assert bilanz["refuted_week"] == 1 and bilanz["confirmed_week"] == 0
    assert bilanz["verteilung"]["bewährt"] == 1 and bilanz["verteilung"]["widerlegt"] == 1

    assert [e["id"] for e in body["to_fix"]] == ["L-065"]
    assert body["to_fix"][0]["evidence_items"][0]["verdict"] == "refute"


def test_learning_payload_is_honest_without_any_evidence(lern_client):
    body = lern_client.get("/api/learning").json()
    by_id = {e["id"]: e for e in body["learnings"]}
    assert by_id["L-054"]["status"]["status"] == "unbelegt"
    assert by_id["L-054"]["status"]["label"] == "noch keine Erfahrung"
    assert body["to_fix"] == []
    assert body["bilanz"]["verteilung"]["unbelegt"] == 2
