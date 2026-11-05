"""HTTP-Käufe übernehmen ausschließlich serverseitige Mannschaftsbelege.

Die Planobjekte sind interne, isolierte Finanzierungsfälle; keine API-Fixtures.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, Intent, TransferAction
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app
from kickbase_agent.server.routes import intents as routes

NOW = datetime.now(UTC)


def domain_plan(player_id="15589", amount=9_043_439, listing_type="manager"):
    stamp = NOW.isoformat()
    review = {
        "schema_version": 1, "player_id": player_id, "selected": True,
        "max_bid_eur": amount, "market_value_eur": amount,
        "purpose": "lineup_completion", "reasons": ["Finanzierter Ersatz im Gesamtplan"],
        "observed_at": stamp, "valid_until": (NOW + timedelta(hours=1)).isoformat(),
        "engine_version": "team-milp-v1", "plan_id": "domain-plan",
        "position": 2, "listing_type": listing_type,
        "bid_scenarios": [{"amount": amount, "win_probability": None, "selected": True}],
        "team_plan": {
            "plan_id": "domain-plan", "observed_at": stamp, "budget_eur": amount,
            "baseline_filled_slots": 10, "open_bid_exposure_eur": 0,
            "reserved_cash_eur": amount, "open_bids": [], "owned_player_ids": [],
            "forecast_inputs": {player_id: {"player": {"team_id": "4"}}},
            "purchases": [{"player_id": player_id, "amount_eur": amount,
                           "max_bid_eur": amount}],
        },
    }
    return SimpleNamespace(league_id="1234567", purchase_reviews={player_id: review},
                           team_plan=review["team_plan"], curation_notes=[])


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        plan = domain_plan()
        calls = []

        async def build():
            calls.append(True)
            return deepcopy(plan)

        monkeypatch.setattr(routes, "build_plan", build)
        monkeypatch.setattr(routes, "_now", lambda: NOW)
        app = create_app()
        app.dependency_overrides[deps.db_session] = lambda: session
        with TestClient(app, headers={"X-Requested-With": "kba"}) as client:
            yield client, session, plan, calls
    engine.dispose()


def payload(**overrides):
    return {"kind": "bid", "player_id": "15589", "amount": 9_043_439,
            "rationale": "Gewünschter Kauf", "emil_go": "Emils bestehender Auftrag",
            "via": "mcp", **overrides}


# Paket 1 (P0-8): hermes/emil sind seit der via-Whitelist keine Absender mehr
# (403, siehe test_intents_via_whitelist.py). Die Aussage bleibt: KEIN zugelassener
# Absender umgeht die serverseitige Engine-Prüfung.
@pytest.mark.parametrize("via", ["mcp", "web", "claude-session", "owner"])
def test_all_callers_receive_server_engine_review_without_identity_bypass(env, via):
    client, session, plan, calls = env
    response = client.post("/api/intents", json=payload(
        via=via, team_id="forged-club", purchase_review={"fake": True},
    ))
    assert response.status_code == 200, response.text
    row = session.scalar(select(Intent))
    assert row.sources["manual"] is True  # Kein automatischer Finanzierungsrückzug.
    assert row.sources["purchase_review"] == plan.purchase_reviews["15589"]
    assert row.sources["league_id"] == "1234567"
    assert row.listing_type == "manager" and row.position == 2
    assert row.team_id == "4"
    assert row.sources["squad_need"] is True
    assert len(calls) == 1
    assert session.scalar(select(TransferAction)) is None


@pytest.mark.parametrize("change", ["not_selected", "stale", "unfunded", "wrong_price"])
def test_unapproved_requests_cannot_create_an_intent(env, change):
    client, session, plan, _ = env
    review = plan.purchase_reviews["15589"]
    if change == "not_selected":
        plan.purchase_reviews.clear()
    elif change == "stale":
        review["valid_until"] = (NOW - timedelta(seconds=1)).isoformat()
    elif change == "unfunded":
        review["team_plan"]["budget_eur"] -= 1
    body = payload(amount=9_043_438) if change == "wrong_price" else payload()
    response = client.post("/api/intents", json=body)
    assert response.status_code == 409, response.text
    assert session.scalar(select(Intent)) is None


def test_engine_failure_is_explicit_and_cannot_fall_back_to_manual(env, monkeypatch):
    client, session, _, _ = env

    async def broken():
        raise TimeoutError("Keine frischen Daten")

    monkeypatch.setattr(routes, "build_plan", broken)
    response = client.post("/api/intents", json=payload())
    assert response.status_code == 503
    assert session.scalar(select(Intent)) is None


def test_purchase_plan_exposes_actionable_prices_without_creating_orders(env):
    client, session, _, _ = env
    response = client.get("/api/purchase-plan")
    assert response.status_code == 200, response.text
    [option] = response.json()["selected"]
    assert option["player_id"] == "15589" and option["amount_eur"] == 9_043_439
    assert session.scalar(select(Intent)) is None


def test_late_execution_is_refused_and_valid_deadline_is_bound_to_review(env):
    client, session, plan, _ = env
    response = client.post("/api/intents", json=payload(delay_s=3600))
    assert response.status_code == 409
    assert session.scalar(select(Intent)) is None
    response = client.post("/api/intents", json=payload(delay_s=60))
    assert response.status_code == 200
    row = session.scalar(select(Intent))
    assert row.deadline.replace(tzinfo=UTC).isoformat() == (
        plan.purchase_reviews["15589"]["valid_until"]
    )
    from kickbase_agent.worker.intents import action_for

    action = action_for(row, "1234567")
    assert action.rule_id == "intent-bid-manual"
    assert action.purchase_review == plan.purchase_reviews["15589"]


@pytest.mark.parametrize("operation,body", [("execute-now", {}), ("adjust", {"pct": 10})])
def test_legacy_manual_bid_needs_engine_review_before_operator_mutation(env, operation, body):
    client, session, _, _ = env
    row = Intent(created_at=NOW, updated_at=NOW, kind="bid", player_id="15589",
                 amount=9_043_439, rationale="Altbestand", sources={"manual": True},
                 listing_type="manager", status="planned", execute_at=NOW)
    session.add(row)
    session.commit()
    response = client.post(f"/api/intents/{row.id}/{operation}", json=body)
    assert response.status_code == 409, response.text
    assert session.get(Intent, row.id).status == "planned"
