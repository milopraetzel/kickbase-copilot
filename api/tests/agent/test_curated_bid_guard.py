"""Kuratierte Gebote: echte Spieler-Domainwerte, isolierte Transaktionsbedingungen.

Die Spielerwerte stammen aus kickbase_player_detail_response.json. Änderungen an
Status, Preis und Uhrzeit sind ausdrücklich Testbedingungen, keine API-Fixtures.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.agent.executor as executor_module
from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.decision.purchase_review import purchase_review_veto, team_funding_veto
from kickbase_agent.kickbase.models import MarketPlayer, Player

NOW = datetime(2026, 9, 13, 9, tzinfo=UTC)
RAW = json.loads((Path(__file__).parents[1] / "fixtures"
                  / "kickbase_player_detail_response.json").read_text())
PID, MV = RAW["i"], RAW["mv"]


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("AGENT_DRY_RUN", "0")
    monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", "TEST")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


class Client:
    def __init__(self):
        self.writes = []
        self.detail_reads = 0
        self.detail = Player(
            id=PID, first_name=RAW["fn"], last_name=RAW["ln"], position=RAW["pos"],
            team_id=RAW["tid"], market_value=MV, fitness_status=RAW["st"],
            lineup_prob=RAW["prob"], mv_change_24h=RAW["tfhmvt"],
        )
        self.market = [MarketPlayer(
            player=self.detail.model_copy(update={"mv_change_24h": 0}), price=MV,
            expires_at=NOW + timedelta(hours=1),
        )]

    async def get_budget(self, league_id):
        return 99_000_000

    async def get_budget_fields(self, league_id):
        return {"pbaa": 99_000_000}

    async def get_market(self, league_id):
        return self.market

    async def get_player(self, league_id, player_id):
        self.detail_reads += 1
        return self.detail

    async def get_squad(self, league_id):
        return []

    async def get_league_rules(self, league_id):
        return {"max_players_per_club": 11}

    async def get_own_bids(self, league_id, user_id):
        return []

    async def get_matchdays(self):
        return [SimpleNamespace(kickoff=NOW + timedelta(days=10), matchday=4, is_finished=False)]

    async def place_bid(self, league_id, player_id, price):
        self.writes.append((league_id, player_id, price))
        return {}


def action(**overrides):
    values = dict(action_type="bid", league_id="TEST", player_id=PID, amount=MV,
                  rule_id="intent-bid")
    values.update(overrides)
    return PlannedAction(**values)


def review(**overrides):
    values = {
        "schema_version": 1, "player_id": PID, "selected": True, "max_bid_eur": MV,
        "observed_at": (NOW - timedelta(minutes=5)).isoformat(),
        "valid_until": (NOW + timedelta(minutes=20)).isoformat(),
        "market_value_eur": MV, "purpose": "starter", "reasons": ["Belegter Startervergleich"],
    }
    values.update(overrides)
    return values


def engine_review(**overrides):
    """Interner Mannschaftsbeleg; Geldbeträge sind isolierte Testbedingungen."""
    chosen = review(purpose="lineup_completion", engine_version="season-v1",
                    model_version="baseline-v1", plan_id="plan-1", max_bid_eur=MV + 200_000)
    chosen["team_plan"] = {
        "plan_id": "plan-1", "observed_at": chosen["observed_at"],
        "budget_eur": MV + 2_100_000, "open_bid_exposure_eur": 0,
        "reserved_cash_eur": MV + 2_000_000, "owned_player_ids": [], "open_bids": [],
        "baseline_filled_slots": 8,
        "purchases": [{"player_id": PID, "amount_eur": MV, "max_bid_eur": MV + 200_000},
                      {"player_id": "other-1", "amount_eur": 1_000_000,
                       "max_bid_eur": 1_000_000},
                      {"player_id": "other-2", "amount_eur": 1_000_000,
                       "max_bid_eur": 1_000_000}],
    }
    chosen["bid_scenarios"] = [
        {"amount": MV, "win_probability": None, "selected": True},
        {"amount": MV + 1000, "win_probability": None, "selected": False},
        {"amount": MV + 200_000, "win_probability": None, "selected": False},
    ]
    chosen.update(overrides)
    return chosen


@pytest.mark.parametrize("purpose,probability,allowed", [
    ("lineup_completion", 1, True), ("lineup_completion", 4, True),
    ("lineup_completion", 5, True), ("lineup_upgrade", 1, True),
    ("lineup_upgrade", 4, False), ("lineup_completion", None, False),
])
async def test_team_purchase_role_rules_without_positive_market_drift(
    session, purpose, probability, allowed,
):
    client = Client()

    async def imminent_matchday():
        return [SimpleNamespace(kickoff=NOW + timedelta(hours=12), matchday=4,
                                is_finished=False)]

    client.get_matchdays = imminent_matchday
    client.detail = client.detail.model_copy(update={
        "lineup_prob": probability, "mv_change_24h": -26065,
    })
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=engine_review(purpose=purpose)), now=NOW,
    )
    assert (result.status == "executed") is allowed, result.message
    assert bool(client.writes) is allowed
    if allowed:
        expectation = session.get(TransferAction, result.action_id).payload["expectation"]
        assert expectation["p"] is None
        assert expectation["plan_id"] == "plan-1"
        assert expectation["ratio"] == 1


@pytest.mark.parametrize("change", [
    {"team_plan": None}, {"plan_id": "other"}, {"engine_version": ""},
    {"bid_scenarios": []},
    {"bid_scenarios": [{"amount": MV, "win_probability": 1.1, "selected": True}]},
])
def test_incomplete_engine_proof_cannot_fall_back_to_legacy(change):
    assert purchase_review_veto(engine_review(**change), player_id=PID, amount=MV, now=NOW)


def test_economic_cap_does_not_allow_an_unfunded_price_scenario():
    chosen = engine_review()
    assert purchase_review_veto(chosen, player_id=PID, amount=MV, now=NOW) is None
    problem = purchase_review_veto(chosen, player_id=PID, amount=MV + 200_000, now=NOW)
    assert "nicht finanziert" in problem


@pytest.mark.parametrize("budget,bids,owned,allowed", [
    (MV + 2_000_000, [], set(), True),
    (MV + 1_999_999, [], set(), False),
    (MV + 1_000_000, [], {"other-1"}, True),
    (MV + 2_000_000, [{"player_id": "other-1", "amount": 1_000_000}], set(), True),
    (MV + 1_900_000, [{"player_id": "other-1", "amount": 900_000}], set(), False),
    (MV + 2_000_000, [{"player_id": PID, "amount": MV}], set(), True),
    (MV + 2_000_000, [{"player_id": "unplanned", "amount": 1}], set(), False),
    (MV + 2_000_000, [], {PID}, False),
    (None, [], set(), False), (MV + 2_000_000, None, set(), False),
])
def test_shared_funding_counts_every_commitment_exactly_once(budget, bids, owned, allowed):
    problem = team_funding_veto(
        engine_review(), player_id=PID, amount=MV, budget=budget,
        open_bids=bids, owned_player_ids=owned,
    )
    assert (problem is None) is allowed


def test_snapshot_reserves_planned_increase_over_existing_bid():
    chosen = engine_review()
    plan = chosen["team_plan"]
    plan["open_bids"] = [{"player_id": "other-1", "amount_eur": 900_000}]
    plan["open_bid_exposure_eur"] = 900_000
    assert purchase_review_veto(chosen, player_id=PID, amount=MV, now=NOW) is None
    plan["reserved_cash_eur"] -= 100_000
    assert "gemeinsame Geldbindung" in purchase_review_veto(
        chosen, player_id=PID, amount=MV, now=NOW,
    )


async def test_live_financing_change_blocks_entire_remaining_package(session):
    client = Client()

    async def low_budget(league_id):
        return MV + 1_999_999

    client.get_budget = low_budget
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=engine_review()), now=NOW,
    )
    assert result.status == "refused"
    assert "Mannschaftsplan nicht finanziert" in result.message
    assert client.writes == []


async def test_engine_reanchor_uses_evaluated_price_without_psycho_offset(session):
    client = Client()
    client.detail = client.detail.model_copy(update={"market_value": MV + 1000})
    chosen = engine_review()
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=chosen), now=NOW,
    )
    assert result.status == "executed", result.message
    assert client.writes == [("TEST", PID, MV + 1000)]
    audit = session.get(TransferAction, result.action_id)
    assert audit.amount == MV + 1000
    assert audit.payload["expectation"]["p"] is None


async def test_engine_large_market_rise_requests_new_plan_instead_of_bidding_below_market(session):
    client = Client()
    increased = int(MV * (DEFAULT_GUARDRAILS.bid_reanchor_max + 0.01))
    client.detail = client.detail.model_copy(update={"market_value": increased})
    chosen = engine_review(max_bid_eur=increased)
    chosen["team_plan"]["budget_eur"] = increased + 2_000_000
    chosen["team_plan"]["purchases"][0]["max_bid_eur"] = increased
    chosen["bid_scenarios"].append(
        {"amount": increased, "win_probability": None, "selected": False},
    )
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=chosen), now=NOW,
    )
    assert result.status == "refused" and client.writes == []
    assert "Preisdrift zu groß" in result.message


async def test_legacy_autonomous_bid_without_review_is_refused_even_without_preflight(session):
    client = Client()
    result = await ActionExecutor(client, session, preflight=False).execute(action(), now=NOW)
    assert result.status == "refused"
    assert "Kaufprüfung" in result.message
    assert client.writes == []
    assert session.query(TransferAction).one().status == "refused"


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"schema_version": 2}, {"selected": False},
    {"selected": 1}, {"player_id": "43"}, {"max_bid_eur": True},
    {"max_bid_eur": 0}, {"max_bid_eur": str(MV)}, {"max_bid_eur": MV - 1},
    {"market_value_eur": 0}, {"market_value_eur": True},
    {"purpose": " "}, {"reasons": []}, {"reasons": [""]}, {"reasons": "Starter"},
    {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"valid_until": NOW.isoformat()}, {"valid_until": "not a date"},
    {"observed_at": "2026-09-13T08:00:00"},
])
def test_review_contract_rejects_invalid_or_unselected_decision(change):
    assert purchase_review_veto(review(**change), player_id=PID, amount=MV, now=NOW)


def test_manual_without_review_is_allowed_and_valid_review_is_unchanged():
    chosen = review()
    before = dict(chosen)
    assert purchase_review_veto(chosen, player_id=PID, amount=MV, now=NOW) is None
    assert chosen == before
    assert purchase_review_veto(None, player_id=PID, amount=MV, now=NOW, required=False) is None


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize(("lifetime", "allowed"), [
    (timedelta(hours=3), True),
    (timedelta(hours=3, microseconds=1), False),
    (timedelta(days=365), False),
])
def test_review_lifetime_is_capped_from_observation_not_from_execution(required, lifetime, allowed):
    observed = NOW - timedelta(hours=2)
    chosen = review(
        observed_at=observed.isoformat(), valid_until=(observed + lifetime).isoformat(),
    )
    veto = purchase_review_veto(chosen, player_id=PID, amount=MV, now=NOW, required=required)
    assert (veto is None) is allowed
    if not allowed:
        assert "3 Stunden" in veto


async def test_selected_bid_reads_fresh_detail_before_dispatch_when_market_has_no_delta(session):
    client = Client()
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=review()), now=NOW,
    )
    assert result.status == "executed", result.message
    assert client.detail_reads == 1
    assert client.writes == [("TEST", PID, MV)]


async def test_engine_staged_expectation_replaces_legacy_probability_with_review_evidence(session):
    client = Client()
    executor = ActionExecutor(client, session, user_id="2663242")
    chosen = engine_review()
    chosen["bid_scenarios"][0]["win_probability"] = 0.31
    staged = await executor.stage(action(purchase_review=chosen), now=NOW)
    record = session.get(TransferAction, staged.action_id)
    record.payload = {**record.payload, "expectation": {"p": 0.45, "ratio": 9}}
    session.commit()
    result = await executor.execute_staged(staged.action_id, now=NOW + timedelta(minutes=1))
    assert result.status == "executed", result.message
    assert record.payload["expectation"]["p"] == 0.31
    assert record.payload["expectation"]["ratio"] == 1
    assert record.payload["expectation"]["model_version"] == "unknown"
    assert record.payload["expectation"]["points_model_version"] == "baseline-v1"


async def test_engine_bank_filler_is_refused_when_live_squad_already_has_eleven(session):
    client = Client()
    client.detail = client.detail.model_copy(update={"lineup_prob": 5, "mv_change_24h": -1})

    async def imminent_matchday():
        return [SimpleNamespace(kickoff=NOW + timedelta(hours=12), matchday=4,
                                is_finished=False)]

    client.get_matchdays = imminent_matchday

    async def squad(_league_id):
        return [SimpleNamespace(player=client.detail.model_copy(update={"id": f"owned-{i}"}))
                for i in range(11)]

    client.get_squad = squad
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=engine_review()), now=NOW,
    )
    assert result.status == "refused" and client.writes == []
    assert "eigener Kaderlücke" in result.message


@pytest.mark.parametrize("change", [
    {"fitness_status": None}, {"fitness_status": 2}, {"fitness_status": False},
    {"lineup_prob": None}, {"lineup_prob": 0}, {"lineup_prob": 4}, {"lineup_prob": 5},
    {"mv_change_24h": None}, {"mv_change_24h": 0}, {"mv_change_24h": -26065},
    {"mv_change_24h": True}, {"id": "43"},
])
async def test_curated_bid_refuses_bad_or_unknown_fresh_player_fields(session, change):
    client = Client()
    client.detail = client.detail.model_copy(update=change)
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=review()), now=NOW,
    )
    assert result.status == "refused"
    assert client.writes == []


async def test_curated_bid_cannot_disable_preflight(session):
    client = Client()
    result = await ActionExecutor(client, session, preflight=False).execute(
        action(purchase_review=review()), now=NOW,
    )
    assert result.status == "refused"
    assert client.writes == []


async def test_reanchor_never_raises_or_exceeds_review_price_limit(session):
    client = Client()
    client.detail = client.detail.model_copy(update={"market_value": MV + 1000})
    chosen = review()
    planned = action(purchase_review=chosen)
    result = await ActionExecutor(client, session, user_id="2663242").execute(planned, now=NOW)
    assert result.status == "refused"
    assert "Preisobergrenze" in result.message
    assert client.writes == []
    assert planned.amount == MV
    assert chosen["max_bid_eur"] == MV


async def test_reanchor_within_review_limit_keeps_review_in_execution_audit(session):
    client = Client()
    client.detail = client.detail.model_copy(update={"market_value": MV + 1000})
    chosen = review(max_bid_eur=MV + 200_000)
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=chosen), now=NOW,
    )
    assert result.status == "executed", result.message
    expected = MV + 1000 + DEFAULT_GUARDRAILS.bid_psych_offset
    assert client.writes == [("TEST", PID, expected)]
    audit = session.get(TransferAction, result.action_id)
    assert audit.amount == expected
    assert audit.payload["purchase_review"] == chosen


async def test_manual_without_review_is_refused_without_detail_read(session):
    client = Client()
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(rule_id="intent-bid-manual"), now=NOW,
    )
    assert result.status == "refused" and "Mannschaftsprüfung" in result.message
    assert client.writes == []
    assert client.detail_reads == 0


async def test_manual_bid_with_review_cannot_exceed_cap(session):
    client = Client()
    result = await ActionExecutor(client, session, preflight=False).execute(
        action(rule_id="mcp-bid", amount=MV + 1, purchase_review=review()), now=NOW,
    )
    assert result.status == "refused"
    assert "Preisobergrenze" in result.message
    assert client.writes == []


async def test_staged_review_keeps_cap_after_later_price_change(session):
    client = Client()
    executor = ActionExecutor(client, session, user_id="2663242")
    proposed = await executor.stage(action(purchase_review=review()), now=NOW)
    client.detail = client.detail.model_copy(update={"market_value": MV + 1000})
    result = await executor.execute_staged(proposed.action_id, now=NOW + timedelta(minutes=1))
    assert result.status == "refused"
    assert "Preisobergrenze" in result.message
    assert client.writes == []


async def test_staged_bid_audit_records_actual_reanchored_amount(session):
    client = Client()
    executor = ActionExecutor(client, session, user_id="2663242")
    chosen = review(max_bid_eur=MV + 200_000)
    proposed = await executor.stage(action(purchase_review=chosen), now=NOW)
    client.detail = client.detail.model_copy(update={"market_value": MV + 1000})
    result = await executor.execute_staged(proposed.action_id, now=NOW + timedelta(minutes=1))
    assert result.status == "executed", result.message
    audit = session.get(TransferAction, proposed.action_id)
    assert audit.amount == client.writes[0][2]
    assert audit.payload["purchase_review"] == chosen


async def test_review_expiring_during_fresh_reads_is_refused(session, monkeypatch):
    elapsed = iter((0.0, 1800.0))
    monkeypatch.setattr(executor_module, "time", SimpleNamespace(monotonic=lambda: next(elapsed)))
    client = Client()
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=review()), now=NOW,
    )
    assert result.status == "refused"
    assert "abgelaufen" in result.message
    assert client.writes == []


@pytest.mark.parametrize("change", [{"fitness_status": 2}, {"lineup_prob": 5}])
async def test_market_veto_is_not_overruled_by_positive_detail_snapshot(session, change):
    client = Client()
    player = client.market[0].player.model_copy(update=change)
    client.market[0] = client.market[0].model_copy(update={"player": player})
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=review()), now=NOW,
    )
    assert result.status == "refused"
    assert client.writes == []


async def test_engine_bank_filler_allowed_when_eleven_heads_only_fill_ten_slots(session):
    """A3: 11 Köpfe mit 2 Torhütern (2 TW, 5 ABW, 3 MF, 1 ST) besetzen nur 10 Plätze."""
    client = Client()
    client.detail = client.detail.model_copy(update={"lineup_prob": 5, "mv_change_24h": -1})

    async def imminent_matchday():
        return [
            SimpleNamespace(kickoff=NOW + timedelta(hours=12), matchday=4, is_finished=False)
        ]

    client.get_matchdays = imminent_matchday
    positions_with_2_gk = [1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 4]

    async def squad(_league_id):
        return [
            SimpleNamespace(
                player=client.detail.model_copy(
                    update={"id": f"owned-{i}", "position": pos, "team_id": f"club-{i}"}
                )
            )
            for i, pos in enumerate(positions_with_2_gk)
        ]

    client.get_squad = squad
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(purchase_review=engine_review(purpose="lineup_completion")), now=NOW,
    )
    assert result.status == "executed", result.message
    assert client.writes == [("TEST", PID, MV)]


async def test_engine_deficit_swap_bank_filler_is_allowed_when_coupled_sale_opens_slot(session):
    """Emil 30.10.2026: Bei negativem Konto (-2,35 M €) öffnet der gekoppelte Tauschverkauf
    (`swap_sale`) den 11. Platz; ein günstiger Bank-Lückenfüller (KB 4, Skov 500k €) passiert
    deshalb das Executor-Preflight."""
    client = Client()

    async def get_budget(_league_id):
        return -2_352_975

    client.get_budget = get_budget
    client.detail = client.detail.model_copy(
        update={"market_value": 500_000, "fitness_status": 2, "lineup_prob": 5, "mv_change_24h": 0}
    )

    async def get_market(_league_id):
        return [
            SimpleNamespace(
                player=client.detail,
                expires_at=NOW + timedelta(hours=4),
                seller_id="2464959",
                price=500_000,
            )
        ]

    client.get_market = get_market

    async def imminent_matchday():
        return [
            SimpleNamespace(kickoff=NOW + timedelta(hours=6), matchday=8, is_finished=False)
        ]

    client.get_matchdays = imminent_matchday
    eleven_positions = [1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4]

    async def squad(_league_id):
        return [
            SimpleNamespace(
                player=client.detail.model_copy(
                    update={
                        "id": f"owned-{i}",
                        "position": pos,
                        "team_id": f"club-{i}",
                        "market_value": 80_000_000,
                    }
                )
            )
            for i, pos in enumerate(eleven_positions)
        ]

    client.get_squad = squad
    chosen = engine_review(
        purpose="lineup_upgrade",
        market_value_eur=500_000,
        max_bid_eur=500_000,
        bid_scenarios=[{"amount": 500_000, "win_probability": 0.5, "selected": True}],
    )
    chosen["swap_sale"] = {
        "player_id": "owned-2",
        "market_value_eur": 80_000_000,
        "proceeds_eur": 72_000_000,
    }
    chosen["team_plan"]["budget_eur"] = -2_352_975
    chosen["team_plan"]["purchases"] = [
        {"player_id": PID, "amount_eur": 500_000, "max_bid_eur": 500_000}
    ]
    chosen["team_plan"]["reserved_cash_eur"] = 500_000
    chosen["team_plan"]["owned_player_ids"] = [f"owned-{i}" for i in range(11)]
    chosen["team_plan"]["team_value_eur"] = 300_000_000
    chosen["team_plan"].setdefault("forecast_inputs", {})[PID] = {"ligainsider_status": "out"}
    chosen["team_plan"].setdefault("technical_limits", {})["planning_overdraft_eur"] = 90_000_000
    chosen["team_plan"]["technical_limits"]["overdraft_until"] = (
        NOW + timedelta(days=30)
    ).isoformat()
    chosen["team_plan"]["swap_sales"] = [
        {
            "player_id": "owned-2",
            "for_player_id": PID,
            "market_value_eur": 80_000_000,
            "proceeds_eur": 72_000_000,
        }
    ]
    result = await ActionExecutor(client, session, user_id="2663242").execute(
        action(amount=500_000, purchase_review=chosen), now=NOW,
    )
    assert result.status == "executed", result.message
    assert client.writes == [("TEST", PID, 500_000)]

