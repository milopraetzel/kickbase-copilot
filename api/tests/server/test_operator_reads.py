"""Operator-API: echte Betriebszeilen statt zusammengeführtem Protokoll oder Mac-Daten."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, CommunityTip, TransferAction
from kickbase_agent.server.app import create_app
from kickbase_agent.server.deps import db_session


@pytest.fixture
def operator_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def operator_client(operator_db):
    app = create_app()
    app.dependency_overrides[db_session] = lambda: operator_db
    with TestClient(app) as client:
        yield client


def test_audit_filters_before_limiting_and_preserves_executor_evidence(
    operator_client, operator_db,
):
    now = datetime.now(UTC)
    for i, status in enumerate(["executed", "executed", "refused"]):
        operator_db.add(TransferAction(
            id=i + 1, league_id="L1", action_type="bid", player_id="99", amount=2_000_000,
            status=status, dry_run=False, rule_id="intent-bid", idempotency_key=f"action:{i}",
            rationale="Reproduzierbarer Audit-Beleg", created_at=now + timedelta(seconds=i),
            raw_response={"token": "must-not-leak"}, payload={"private": "must-not-leak"},
        ))
    operator_db.commit()

    response = operator_client.get("/api/audit", params={"limit": 1, "status": "executed"})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1 and body["status_filter"] == "executed"
    assert body["actions"][0]["id"] == 2
    assert body["actions"][0]["dry_run"] is False
    assert body["actions"][0]["amount_eur"] == 2_000_000
    assert "must-not-leak" not in response.text
    assert operator_client.get("/api/audit?limit=0").json()["actions"] == []


@pytest.mark.parametrize("limit", [-1, 1001])
def test_audit_rejects_invalid_limit(operator_client, limit):
    assert operator_client.get("/api/audit", params={"limit": limit}).status_code == 422


def test_tips_uses_worker_ttl_and_operational_database(operator_client, operator_db):
    now = datetime.now(UTC)
    operator_db.add_all([
        CommunityTip(player_name="Aktuell", weight=0.5, source="test", note="aktiver Tipp",
                     created_at=now - timedelta(days=1)),
        CommunityTip(player_name="Verfallen", weight=0.5, source="test", note="alter Tipp",
                     created_at=now - timedelta(days=5)),
    ])
    operator_db.commit()

    response = operator_client.get("/api/tips")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1 and body["ttl_days"] == 4
    assert [tip["player_name"] for tip in body["tips"]] == ["Aktuell"]
