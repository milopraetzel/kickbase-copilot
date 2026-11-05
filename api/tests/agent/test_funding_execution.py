"""Deckungsrückzug: isolierte Domänenfälle, keine erfundenen API-Antworten."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import kickbase_agent.worker.intents as intent_worker
from kickbase_agent.agent.executor import (
    ActionExecutor,
    PlannedAction,
    PreflightState,
    abandoned_bid_ids,
    idempotency_key,
)
from kickbase_agent.cli.plan import (
    Plan,
    cash_deficit_alternative,
    load_pending_bid_commitments,
    load_recovery_audit,
)
from kickbase_agent.db.models import Base, Intent, TransferAction
from kickbase_agent.decision.funding_recovery import build_funding_recovery
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.kickbase.models import MarketPlayer, Player, SquadPlayer
from kickbase_agent.worker.auto_trading import TodayCounts
from kickbase_agent.worker.intents import (
    MarketRow,
    action_for,
    plan_intents,
    release_funding_pending,
    upsert_intents,
)

NOW = datetime(2026, 9, 13, 9, tzinfo=UTC)
RAW = json.loads((Path(__file__).parents[1] / "fixtures"
                  / "kickbase_player_detail_response.json").read_text())
PLAYER = Player.from_api(RAW)
PID = PLAYER.id
# Tatsächliche Konto-/Gebotsbeträge; Spieleridentität hier bewusst aus Detail-Fixture.
CASH, BID = 3_120_437, 8_074_347


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("AGENT_DRY_RUN", "0")
    monkeypatch.setenv("AGENT_AUTO_INTENTS", "1")
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", "domain-protected-other")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


class Client:
    def __init__(self):
        self.budget = CASH
        self.bids = [{"player_id": PID, "amount": BID, "offer_id": "domain-offer",
                      "expires_in_s": 21600}]
        self.market = [MarketPlayer(player=PLAYER, price=PLAYER.market_value,
                                    expires_at=NOW + timedelta(hours=6))]
        self.squad = []
        self.writes = []
        self.timeout = False
        self.decode_error = False

    async def get_squad(self, league_id):
        return self.squad

    async def get_market(self, league_id):
        return self.market

    async def get_own_bids(self, league_id, user_id):
        return self.bids

    async def get_budget(self, league_id):
        return self.budget

    async def cancel_bid(self, league_id, player_id, offer_id):
        self.writes.append((league_id, player_id, offer_id))
        if self.timeout:
            raise httpx.ReadTimeout("isolierter unbekannter Schreibausgang")
        if self.decode_error:
            raise json.JSONDecodeError("isolierte unlesbare Antwort", "", 0)
        return {}


def recovery(client, now=NOW):
    for bid in client.bids:
        bid["funding_provenance"] = {
            "original_bid_action_id": 1,
            "original_listing_expires_at": client.market[0].expires_at.isoformat(),
        }
    result = build_funding_recovery(
        league_id="L", plan_id="funding-plan", budget=CASH, owned_player_ids=[],
        live_bids=client.bids, pending_bids=[], now=now,
        market_by_id={PID: client.market[0]},
    )
    for action in result["actions"]:
        action["funding_review"].update(
            original_bid_action_id=1,
            original_listing_expires_at=client.market[0].expires_at.isoformat(),
        )
    return result


def cancel_action(proof):
    return PlannedAction(action_type="cancel_bid", league_id="L", player_id=PID,
                         offer_id="domain-offer", amount=BID, rule_id="intent-funding-cancel",
                         funding_review=proof)


def audit(session, rule="intent-bid", amount=BID, *, expiry=None, observed=NOW):
    row = TransferAction(league_id="L", player_id=PID, action_type="bid", amount=amount,
                         rule_id=rule, status="executed", dry_run=False,
                         idempotency_key="previous-bid", created_at=NOW-timedelta(hours=1),
                         payload={"confirmed_bid": {
                             "source": "own_bids_after_executed_bid", "player_id": PID,
                             "amount_eur": amount, "offer_id": "domain-offer",
                             "expires_at": (expiry or NOW+timedelta(hours=6)).isoformat(),
                             "observed_at": observed.isoformat(),
                         }})
    session.add(row)
    session.commit()


def pending(session, **overrides):
    values = dict(kind="bid", player_id=PID, amount=BID, sources={"league_id": "L"},
                  status="planned", created_at=NOW, updated_at=NOW, execute_at=NOW)
    values.update(overrides)
    row = Intent(**values)
    session.add(row)
    session.commit()
    return row


@pytest.mark.parametrize("failure", [None, "timeout", "decode"])
async def test_exact_funding_cancel_is_audited_and_blocks_rebid(session, failure):
    client = Client()
    client.timeout = failure == "timeout"
    client.decode_error = failure == "decode"
    audit(session)
    proof = recovery(client)["actions"][0]["funding_review"]
    executor = ActionExecutor(client, session, user_id="me")
    result = await executor.execute(cancel_action(proof), now=NOW)
    assert result.status == ("unknown" if failure else "executed"), result.message
    assert client.writes == [("L", PID, "domain-offer")]
    assert PID in abandoned_bid_ids(session, "L", NOW)
    assert not abandoned_bid_ids(session, "other", NOW)
    assert not abandoned_bid_ids(session, "L", NOW + timedelta(hours=7))
    assert session.get(TransferAction, result.action_id).payload["funding_review"] == proof
    rebid = PlannedAction(action_type="bid", league_id="L", player_id=PID,
                          amount=BID, rule_id="intent-bid-manual")
    refused = await executor.execute(rebid, now=NOW + timedelta(minutes=1))
    assert refused.status == "refused"
    assert "Wiedereinstieg" in refused.message
    assert len(client.writes) == 1


async def test_unknown_cancellation_cannot_retry_after_utc_midnight(session):
    client = Client()
    client.timeout = True
    stamp = NOW.replace(hour=23, minute=55)
    client.market[0].expires_at = stamp + timedelta(hours=3)
    proof = recovery(client, stamp)["actions"][0]["funding_review"]
    action = cancel_action(proof)
    audit(session, expiry=client.market[0].expires_at, observed=stamp)
    executor = ActionExecutor(client, session, user_id="me")
    first = await executor.execute(action, now=stamp)
    second = await executor.execute(action, now=stamp + timedelta(minutes=10))
    assert first.status == "unknown" and second.status == "duplicate"
    assert len(client.writes) == 1
    assert idempotency_key(action, "2026-09-13") == idempotency_key(action, "2026-09-14")


@pytest.mark.parametrize("change", [
    "manual_audit", "different_amount", "no_audit", "new_cash", "new_offer", "new_owner",
    "different_expiry", "missing_proof", "expired_proof", "no_preflight",
    "manual_intent", "adjusted_intent", "executing_intent", "old_auction", "no_receipt",
    "same_offer_new_auction", "missing_original_id",
])
async def test_changed_or_manual_context_never_cancels(session, change):
    client = Client()
    proof = recovery(client)["actions"][0]["funding_review"]
    if change != "no_audit":
        audit(session, rule="intent-bid-manual" if change == "manual_audit" else "intent-bid",
              amount=BID + 1 if change == "different_amount" else BID)
    if change == "new_cash":
        client.budget += 1
    elif change == "new_offer":
        client.bids[0]["offer_id"] = "replacement-offer"
    elif change == "new_owner":
        client.squad = [SquadPlayer(player=PLAYER)]
    elif change == "different_expiry":
        client.market[0].expires_at += timedelta(hours=1)
    elif change == "missing_proof":
        proof = None
    elif change == "expired_proof":
        proof["valid_until"] = NOW.isoformat()
    elif change == "same_offer_new_auction":
        saved = session.query(TransferAction).one()
        receipt = dict(saved.payload["confirmed_bid"])
        receipt["expires_at"] = (NOW + timedelta(days=2)).isoformat()
        saved.payload = {"confirmed_bid": receipt}
        session.commit()
    elif change == "missing_original_id":
        proof.pop("original_bid_action_id")
    elif change == "manual_intent":
        pending(session, sources={"league_id": "L", "manual": True})
    elif change == "adjusted_intent":
        pending(session, adjusted_from=BID - 1)
    elif change == "executing_intent":
        pending(session, status="executing")
    elif change in ("old_auction", "no_receipt"):
        saved = session.query(TransferAction).one()
        saved.payload = {} if change == "no_receipt" else {"confirmed_bid": {
            "source": "own_bids_after_executed_bid", "player_id": PID,
            "amount_eur": BID, "offer_id": "previous-auction-offer",
        }}
        session.commit()
    executor = ActionExecutor(client, session, user_id="me")
    if change == "no_preflight":
        executor._preflight = None
    result = await executor.execute(cancel_action(proof), now=NOW)
    assert result.status == "refused", result.message
    assert client.writes == []


def test_audit_distinguishes_manual_current_amount_and_missing_history(session):
    client = Client()
    assert load_recovery_audit(session, "L", client.bids, NOW) == ({PID}, set())
    audit(session, rule="intent-bid-manual")
    assert load_recovery_audit(session, "L", client.bids, NOW) == ({PID}, set())
    row = session.query(TransferAction).one()
    row.rule_id = "intent-bid"
    session.commit()
    assert load_recovery_audit(session, "L", client.bids, NOW) == (set(), set())


@pytest.mark.parametrize("read_fails", [False, True])
async def test_successful_bid_receipt_records_only_a_read_confirmed_offer(session, read_fails):
    from .test_curated_bid_guard import engine_review

    class ReceiptClient(Client):
        async def place_bid(self, league_id, player_id, amount):
            self.writes.append((league_id, player_id, amount))
            return {}

        async def get_own_bids(self, league_id, user_id):
            if read_fails:
                raise RuntimeError("isolierter Lesefehler nach erfolgreichem Gebot")
            return self.bids

    client = ReceiptClient()
    proof = engine_review(player_id=PID, market_value_eur=BID, max_bid_eur=BID)
    proof["team_plan"].update(
        budget_eur=BID, reserved_cash_eur=BID,
        purchases=[{"player_id": PID, "amount_eur": BID, "max_bid_eur": BID}],
    )
    proof["bid_scenarios"] = [{"amount": BID, "win_probability": None, "selected": True}]

    async def fresh_state(action):
        return PreflightState(
            on_market=True, current_mv=BID, current_price=BID, budget=BID,
            own_bids=[], squad_positions={}, target_fitness_status=0,
            target_lineup_prob=1, target_mv_change_24h=0,
            listing_expires_at=NOW + timedelta(hours=6),
        )

    action = PlannedAction(action_type="bid", league_id="L", player_id=PID,
                           amount=BID, rule_id="intent-bid-manual", purchase_review=proof)
    result = await ActionExecutor(client, session, user_id="me", preflight=fresh_state).execute(
        action, now=NOW,
    )
    assert result.status == "executed"
    payload = session.get(TransferAction, result.action_id).payload or {}
    if read_fails:
        assert "confirmed_bid" not in payload
    else:
        assert payload["confirmed_bid"]["offer_id"] == "domain-offer"
        assert payload["confirmed_bid"]["amount_eur"] == BID


@pytest.mark.parametrize("kind", ["auto", "manual", "adjusted", "executing", "other_league"])
def test_only_unchanged_automatic_pending_bids_are_released(session, kind):
    row = pending(session)
    if kind == "manual":
        row.sources = {"league_id": "L", "manual": True}
    elif kind == "adjusted":
        row.adjusted_from = BID - 1
    elif kind == "executing":
        row.status = "executing"
    elif kind == "other_league":
        row.sources = {"league_id": "other"}
    session.commit()
    if kind == "adjusted":
        assert load_pending_bid_commitments(session, "L", NOW)[0]["manual"] is True
    result = release_funding_pending(session, {"released_pending_ids": [row.id]}, "L", NOW)
    assert bool(result) is (kind == "auto")
    assert (row.status == "superseded") is (kind == "auto")


def test_cancel_intent_preserves_veto_and_does_not_use_sell_quota(session):
    client = Client()
    plan = Plan(league_id="L", budget=CASH, buys=[], sells=[],
                team_plan={"funding_recovery": recovery(client)})
    row = MarketRow(player_id=PID, price=BID, our_bid=BID)
    drafts = plan_intents(plan, [row], NOW)
    assert len(drafts) == 1
    assert drafts[0].execute_at == NOW + timedelta(hours=2)
    stats = upsert_intents(session, drafts, NOW, counts=TodayCounts(sells=999))
    assert len(stats.created) == 1
    intent = stats.created[0]
    assert action_for(intent, "L").rule_id == "intent-funding-cancel"
    expected_proof = recovery(client)["actions"][0]["funding_review"]
    assert action_for(intent, "L").funding_review == expected_proof
    plan.team_plan["funding_recovery"] = recovery(client, NOW + timedelta(hours=1))
    updated = plan_intents(plan, [row], NOW + timedelta(hours=1))
    upsert_intents(session, updated, NOW + timedelta(hours=1))
    assert intent.execute_at.replace(tzinfo=UTC) == NOW + timedelta(hours=2)


def test_short_auction_cannot_skip_new_cancel_veto_window(session):
    client = Client()
    client.market[0].expires_at = NOW + timedelta(hours=1)
    plan = Plan(league_id="L", budget=CASH, buys=[], sells=[],
                team_plan={"funding_recovery": recovery(client)})
    drafts = plan_intents(plan, [MarketRow(player_id=PID, price=BID, our_bid=BID)], NOW)
    stats = upsert_intents(session, drafts, NOW)
    assert not stats.created
    assert stats.skipped_blocked


async def test_executor_tick_cancel_preserves_final_sale_slot(session, monkeypatch):
    stamp = datetime.now(UTC) - timedelta(hours=1)
    pending(session, kind="cancel_bid", execute_at=stamp)
    pending(session, kind="sell", player_id="domain-sale", execute_at=stamp+timedelta(minutes=1))
    seen = []
    initial = DEFAULT_GUARDRAILS.daily_sell_cap - 1

    async def factory():
        return None, SimpleNamespace(league_id="L", user_id="me")

    async def execute(db, intent, executor, league_id, *, now, counts):
        seen.append((intent.kind, counts.sells))
        return "failed" if intent.kind == "sell" and counts.sells > initial else "executed"

    monkeypatch.setenv("AGENT_WRITE_SPACING_SECONDS", "0")
    monkeypatch.setattr(intent_worker, "SessionLocal", sessionmaker(bind=session.get_bind()))
    monkeypatch.setattr(intent_worker, "make_kickbase_client", factory)
    monkeypatch.setattr(intent_worker, "execute_intent", execute)
    monkeypatch.setattr(intent_worker, "today_intent_counts",
                        lambda *_: TodayCounts(sells=initial))
    result = await intent_worker.run_intent_executor()
    assert result == "quiet"
    assert seen == [("cancel_bid", initial), ("sell", initial)]


def test_negative_cash_sales_are_only_estimates_and_respect_protection(monkeypatch):
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", "domain-protected-other")
    squad = [SquadPlayer(player=PLAYER)]
    deficit = -min(500_000, PLAYER.market_value)
    result = cash_deficit_alternative(squad, deficit, NOW + timedelta(days=2), {})
    assert result["remaining_squad_size"] == 0
    assert not result["execution_authorized"] and not result["guaranteed_proceeds"]
    assert result["responsible"] == "kickoff_guard"
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", PID)
    protected = cash_deficit_alternative(squad, deficit, None, {})
    assert protected["status"] == "blocked" and protected["sales"] == []
