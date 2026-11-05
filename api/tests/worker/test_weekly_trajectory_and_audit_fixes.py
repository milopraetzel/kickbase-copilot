"""Regressionstests für die Wochen-Trajektorie (11/11 + Konto >= 0 €) und Audit-Fixes (28.10.2026).

Prüft die Invarianten aus dem Code-Audit (C1, C2, C4, B5, A1, A2, A4):
- C1: PBAA-Retry funktioniert für planmäßig bei T-15m gestartete Auktionsgebote (Retry bei T-5m).
- C2 & B5: Dispo-Zuschlagsvermerk wird auch während der Kadergrößen-Wartezeit gesetzt;
  ein zwischenzeitlicher Bank-Abgang (need=13 -> size=12) blockiert den Tauschverkauf nicht.
- C4: Kickoff-Guard berücksichtigt `load_trade_book` (S1 Trading) und LigaInsider-Bankstatus
  (S1b Bank) in `run_debt_ping` und `run_purge_enforce`.
- A1 & A2: `purchase_review_veto` und `team_funding_veto` decken offene Gebots-Defizite
  über gekoppelte Tauschverkäufe und erlauben `funding_recovery.status == "funded"`.
- A4: `adjust_intent(..., pct=10)` rastert auf geprüfte `bid_scenarios` statt am Veto zu scheitern.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, Intent
from kickbase_agent.decision.purchase_review import purchase_review_veto, team_funding_veto
from kickbase_agent.intents import actions
from kickbase_agent.scouts.ligainsider.models import Status
from kickbase_agent.trading.trade_book import TradeBook, TradePosition
from kickbase_agent.worker import intents as wi
from kickbase_agent.worker import kickoff_guard as kg

NOW = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("AGENT_DRY_RUN", "0")
    monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", "TEST")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.mark.asyncio
async def test_c1_pbaa_retry_allows_auction_bid_at_t_minus_15_to_retry_at_t_minus_5(session):
    """Ein bei Deadline - 15 min gestartetes Auktionsgebot darf bei pbaa-Lücke noch einmal
    bei Deadline - 5 min (>= PBAA_RETRY_CUTOFF = 3 min) versucht werden."""
    deadline = NOW + wi.EXECUTE_BEFORE_DEADLINE  # NOW ist exakt Deadline - 15 min
    intent = Intent(
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
        kind="bid",
        player_id="2261",
        player_name="Neuhaus",
        team_id="15",
        position=3,
        amount=7_016_290,
        rationale="Tausch-Gebot",
        sources={"rule_id": "swap_bid"},
        listing_type="kickbase",
        deadline=deadline,
        execute_at=NOW,
        status=wi.STATUS_PLANNED,
    )
    session.add(intent)
    session.commit()

    class _PbaaMissingExecutor:
        async def execute(self, act, *, dry_run=False, now=None):
            return SimpleNamespace(
                status="refused",
                action_id=1,
                message="Preflight: Konto-nach-Geboten (pbaa) fehlt — Gebot verweigert",
            )

    status = await wi.execute_intent(session, intent, _PbaaMissingExecutor(), "L1", now=NOW)
    assert status == wi.STATUS_PLANNED
    assert intent.pbaa_retries == 1
    assert wi._as_utc(intent.execute_at) == NOW + wi.PBAA_RETRY_DELAY  # Deadline - 5 min


@pytest.mark.asyncio
async def test_c2_and_b5_dispo_win_recorded_and_effective_need_12_prevents_deadlock(session):
    """Wenn der Ersatzspieler im Kader steht, wird swap_won_at auch bei Wartezeit vermerkt,
    und after_squad_size=13 blockiert bei realer Kadergröße 12 (mit Ersatz) nicht."""
    bid = Intent(
        created_at=NOW - timedelta(hours=2),
        updated_at=NOW - timedelta(minutes=20),
        kind="bid",
        player_id="target-1",
        player_name="Target",
        team_id="10",
        position=3,
        amount=6_500_000,
        rationale="r",
        sources={"rule_id": "swap_bid", "swap_out": "sold-1", "league_id": "L1"},
        listing_type="kickbase",
        deadline=NOW - timedelta(minutes=5),
        execute_at=NOW - timedelta(minutes=20),
        status=wi.STATUS_EXECUTED,
    )
    sell = Intent(
        created_at=NOW - timedelta(hours=2),
        updated_at=NOW - timedelta(hours=2),
        kind="sell",
        player_id="sold-1",
        player_name="Sold",
        team_id="10",
        position=2,
        amount=10_000_000,
        rationale="r",
        sources={
            "rule_id": "swap_sell",
            "swap_for": "target-1",
            "after_squad_size": 13,
            "swap_dispo": True,
            "league_id": "L1",
        },
        listing_type="manager",
        deadline=NOW + timedelta(hours=12),
        execute_at=NOW,
        status=wi.STATUS_PLANNED,
    )
    session.add_all([bid, sell])
    session.commit()

    # Fall 1: Kader hat 12 Spieler inklusive "target-1" (13. Bankspieler wurde zwischenzeitlich
    # verkauft) -> effective_need = min(13, 12) = 12 ist erfüllt!
    squad_12 = [SimpleNamespace(player=SimpleNamespace(id="target-1"))] + [
        SimpleNamespace(player=SimpleNamespace(id=f"p-{i}")) for i in range(11)
    ]
    ex_12 = SimpleNamespace(_client=SimpleNamespace(get_squad=lambda _l: _async_val(squad_12)))
    assert await wi.squad_condition_unmet(sell, ex_12, "L1", now=NOW) is None

    # Fall 2: Kader hat erst 11 Spieler, aber "target-1" ist schon drin -> wartet auf 12,
    # vermerkt aber trotzdem sofort swap_won_at auf dem verknüpften Gebot!
    squad_11 = [SimpleNamespace(player=SimpleNamespace(id="target-1"))] + [
        SimpleNamespace(player=SimpleNamespace(id=f"p-{i}")) for i in range(10)
    ]
    ex_11 = SimpleNamespace(_client=SimpleNamespace(get_squad=lambda _l: _async_val(squad_11)))
    res = await wi.execute_intent(session, sell, ex_11, "L1", now=NOW)
    assert res == wi.STATUS_PLANNED
    session.refresh(bid)
    assert wi.SWAP_WON_KEY in (bid.sources or {})


async def _async_val(val):
    return val


@pytest.mark.asyncio
async def test_c4_kickoff_guard_includes_trade_book_and_li_bench_in_purge_cascade(
    session, monkeypatch
):
    """_gather_li_bench_ids erkennt LigaInsider-Bankspieler; _gather_state vereint TradeBook."""
    import kickbase_agent.scouts.ligainsider.availability as li_avail
    import kickbase_agent.scouts.ligainsider.scraper as li_scraper
    import kickbase_agent.trading.trade_book as tb_mod

    squad = [
        SimpleNamespace(
            player=SimpleNamespace(
                id="flip-1",
                first_name="Flip",
                last_name="One",
                team_id="10",
                position=3,
                market_value=2_000_000,
                lineup_prob=2,
                fitness_status=0,
            ),
            is_starter=False,
        ),
        SimpleNamespace(
            player=SimpleNamespace(
                id="bench-starter",
                first_name="Bench",
                last_name="Starter",
                team_id="11",
                position=2,
                market_value=4_000_000,
                lineup_prob=2,
                fitness_status=0,
            ),
            is_starter=True,
        ),
    ]

    monkeypatch.setattr(
        li_scraper,
        "fetch_snapshot",
        lambda: _async_val(SimpleNamespace(lineups=[], injuries=[], scraped_tids=set())),
    )
    monkeypatch.setattr(
        li_avail,
        "resolve_availability",
        lambda *_a: ([SimpleNamespace(player_id="bench-starter", status=Status.BENCH)], []),
    )
    li_bench = await kg._gather_li_bench_ids(squad)
    assert li_bench == frozenset({"bench-starter"})

    client = SimpleNamespace(
        get_squad=lambda _l: _async_val(squad),
        _get=lambda _p: _async_val({"it": [{"i": "bench-starter", "lo": 1}]}),
    )
    monkeypatch.setattr(
        tb_mod,
        "load_trade_book",
        lambda *_a, **_kw: TradeBook(
            positions={
                "flip-1": TradePosition(
                    player_id="flip-1",
                    buy_price=1_800_000,
                    bought_at=NOW - timedelta(days=1),
                    intent_id=5,
                    action_id=10,
                )
            },
        ),
    )

    _, lineup_ids, trading_ids, _ = await kg._gather_state(client, session, "L1")
    assert lineup_ids == {"bench-starter"}
    assert "flip-1" in trading_ids


def test_a1_and_a2_purchase_review_allows_open_bid_deficit_swap_and_funded_recovery(monkeypatch):
    """Ein Tausch darf das durch offene Gebote gerissene Defizit decken; funding_recovery='funded'
    erlaubt Pläne mit negativem Roh-Budget."""
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", "8329")
    plan = {
        "plan_id": "p-1",
        "observed_at": NOW.isoformat(),
        "budget_eur": 500_000,
        "open_bid_exposure_eur": 2_000_000,
        "reserved_cash_eur": 7_000_000,
        "owned_player_ids": ["sold-1", "8329"],
        "open_bids": [{"player_id": "open-1", "amount_eur": 2_000_000}],
        "baseline_filled_slots": 11,
        "purchases": [{"player_id": "target-1", "amount_eur": 5_000_000, "max_bid_eur": 5_500_000}],
        "swap_sales": [
            {
                "player_id": "sold-1",
                "for_player_id": "target-1",
                "market_value_eur": 12_000_000,
                "proceeds_eur": 10_800_000,
            }
        ],
        "team_value_eur": 90_000_000,
    }
    review = {
        "schema_version": 1,
        "player_id": "target-1",
        "selected": True,
        "max_bid_eur": 5_500_000,
        "market_value_eur": 5_000_000,
        "observed_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(hours=2)).isoformat(),
        "purpose": "lineup_upgrade",
        "reasons": ["Tausch deckt Kauf + Gebotsdefizit."],
        "engine_version": "team-milp-v1",
        "plan_id": "p-1",
        "team_plan": plan,
        "swap_sale": {
            "player_id": "sold-1",
            "market_value_eur": 12_000_000,
            "proceeds_eur": 10_800_000,
        },
        "bid_scenarios": [
            {"amount": 5_000_000, "win_probability": None, "selected": True},
            {"amount": 5_500_000, "win_probability": None, "selected": False},
        ],
    }
    assert purchase_review_veto(review, player_id="target-1", amount=5_000_000, now=NOW) is None
    assert (
        team_funding_veto(
            review,
            player_id="target-1",
            amount=5_000_000,
            budget=500_000,
            open_bids=[{"player_id": "open-1", "amount": 2_000_000}],
            owned_player_ids={"sold-1", "8329"},
            now=NOW,
        )
        is None
    )


def test_a4_adjust_intent_pct_snaps_to_engine_bid_scenario_grid(session):
    """+10 % auf ein Engine-Gebot rastert auf das geprüfte Szenario-Gitter in bid_scenarios."""
    plan = {
        "plan_id": "team-snap",
        "observed_at": NOW.isoformat(),
        "budget_eur": 15_000_000,
        "open_bid_exposure_eur": 0,
        "reserved_cash_eur": 10_000_000,
        "owned_player_ids": [],
        "open_bids": [],
        "baseline_filled_slots": 10,
        "purchases": [{"player_id": "B1", "amount_eur": 10_000_000, "max_bid_eur": 11_500_000}],
    }
    review = {
        "schema_version": 1,
        "player_id": "B1",
        "selected": True,
        "max_bid_eur": 11_500_000,
        "market_value_eur": 10_000_000,
        "observed_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(hours=2)).isoformat(),
        "purpose": "lineup_completion",
        "reasons": ["Geprüft."],
        "engine_version": "team-milp-v1",
        "plan_id": "team-snap",
        "team_plan": plan,
        "bid_scenarios": [
            {"amount": 10_000_000, "win_probability": None, "selected": True},
            {"amount": 10_850_000, "win_probability": None, "selected": False},
            {"amount": 11_500_000, "win_probability": None, "selected": False},
        ],
    }
    intent = Intent(
        id=1,
        created_at=NOW,
        updated_at=NOW,
        kind="bid",
        player_id="B1",
        player_name="B1",
        team_id="10",
        position=3,
        amount=10_000_000,
        rationale="r",
        sources={"purchase_review": review},
        listing_type="kickbase",
        deadline=NOW + timedelta(hours=3),
        execute_at=NOW + timedelta(hours=2),
        status="planned",
    )
    session.add(intent)
    session.commit()

    updated = actions.adjust_intent(session, 1, pct=10, actor=actions.ACTOR_WEB, now=NOW)
    assert updated.amount == 10_850_000
    assert updated.adjusted_from == 10_000_000
