"""Tests fuer das zentrale KI-Regelwerk (Web-App + KI-Chat synchron) und Engine-Verdrahtung."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
from kickbase_agent.agent.rules import load_agent_rules, save_agent_rules
from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.cli.cockpit.workspace import render_workspace
from kickbase_agent.db.models import Base
from kickbase_agent.decision.lineup import best_partial_lineup
from kickbase_agent.decision.lineup_service import _forecast_lineup
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.trading.trade_book import (
    remaining_updates_until_friday,
    sale_cover,
)


def test_rules_presets_and_custom_overrides(tmp_path: Path) -> None:
    rules_file = tmp_path / "agent_rules.json"
    initial = load_agent_rules(rules_file)
    assert initial.onboarding_completed is False

    saved = save_agent_rules(
        {
            "preset": "veto_copilot",
            "max_bid_million": 12.5,
            "trade_budget_cap_million": 22.0,
            "friday_buffer_haircut_pct": 4.0,
            "allow_sells": "bench_only",
            "protected_players": "Olise, Musiala",
        },
        updated_by="test_chat",
        path=rules_file,
    )
    assert saved.mode == "veto_copilot"
    assert saved.allow_bids is True
    assert saved.max_bid_million == 12.5
    assert saved.trade_budget_cap_million == 22.0
    assert saved.friday_buffer_haircut_pct == 4.0
    assert saved.allow_sells == "bench_only"
    assert saved.protected_players == ["Olise", "Musiala"]
    assert saved.is_player_protected("999", "Michael Olise") is True
    assert saved.is_player_protected("999", "Harry Kane") is False
    assert saved.onboarding_completed is True
    assert saved.is_custom_active() is True

    reloaded = load_agent_rules(rules_file)
    assert reloaded.to_dict() == saved.to_dict()


def test_workspace_includes_regeln_tab_and_backend_sync() -> None:
    html = render_workspace(
        {
            "stand": "05.11.2026 08:45",
            "strategy": {},
            "rules": {
                "mode": "veto_copilot",
                "max_bid_million": 18.0,
                "protected_players": ["Olise"],
            },
        }
    )
    assert 'data-view="regeln"' in html
    assert "Regelwerk</button>" in html
    assert "loadRulesFromBackend" in html
    assert 'apiCall("/api/rules", "GET")' in html
    assert "arr(D.kader)" in html
    assert "arr(d.kader)" not in html


@pytest.mark.asyncio
async def test_executor_enforces_agent_rules_end_to_end() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    class _DummyClient:
        async def get_squad(self, _lg: str):
            return [
                SimpleNamespace(
                    player=SimpleNamespace(
                        id="p-musiala",
                        first_name="Jamal",
                        last_name="Musiala",
                        position=3,
                        market_value=30_000_000,
                        lineup_prob=1,
                        fitness_status=0,
                    )
                )
            ]

    with Session(engine, future=True) as session:
        # 1. Gebote deaktiviert -> Executor lehnt Gebot ab
        save_agent_rules({"preset": "veto_copilot", "allow_bids": False}, updated_by="test")
        ex = ActionExecutor(_DummyClient(), session, preflight=False)
        res_bid = await ex.execute(
            PlannedAction("bid", "L1", player_id="99", amount=5_000_000),
            dry_run=True,
        )
        assert res_bid.status == "refused"
        assert "Regelwerk" in res_bid.message

        # 2. Verkäufe deaktiviert -> Executor lehnt Verkauf ab
        save_agent_rules({"preset": "veto_copilot", "allow_sells": "none"}, updated_by="test")
        res_sell = await ex.execute(
            PlannedAction("sell", "L1", player_id="p-other", amount=2_000_000),
            dry_run=True,
        )
        assert res_sell.status == "refused"
        assert "Regelwerk" in res_sell.message

        # 3. Geschützter Spieler per Name in agent_rules.json -> Verkauf abgelehnt
        save_agent_rules(
            {
                "preset": "full_autopilot",
                "protected_players": ["Musiala"],
            },
            updated_by="test",
        )
        res_prot = await ex.execute(
            PlannedAction("sell", "L1", player_id="p-musiala", amount=30_000_000),
            dry_run=True,
        )
        assert res_prot.status == "refused"
        assert "geschützt" in res_prot.message.lower()


def test_sale_cover_applies_friday_haircut_across_remaining_updates() -> None:
    now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)  # Mittwoch 13:00 Berlin
    sale_by = datetime(2026, 11, 6, 17, 30, tzinfo=UTC)  # Freitag 18:30 Berlin -> 2 Updates
    updates = remaining_updates_until_friday(now, sale_by)
    assert updates == 2

    full_cover = sale_cover(
        held_mv=[10_000_000],
        bid_target_mv=[],
        now=now,
        sale_by=sale_by,
        updates_until_friday=0,
        haircut_per_update_pct=0.0,
    )
    haircut_cover = sale_cover(
        held_mv=[10_000_000],
        bid_target_mv=[],
        now=now,
        sale_by=sale_by,
        updates_until_friday=updates,
        haircut_per_update_pct=4.0,
    )
    assert full_cover == 10_000_000
    assert haircut_cover == 9_200_000


def test_lineup_tie_breaker_uses_current_market_value_and_bench_quality() -> None:
    """Befund 2.D (Bugs 1–3): Bankspieler mit 0.0 expected_points werden innerhalb einer
    Position UND beim Formationsvergleich (3-4-3 vs. 4-4-2) sowie in best_partial_lineup
    nach lineup_score und current_market_value statt alphabetisch nach player_id gewählt."""
    def _cand(pid: str, pos: int, mv: int, score: float) -> ExplosionCandidate:
        return ExplosionCandidate(
            player_id=pid,
            name=f"P-{pid}",
            team_id="1",
            position=pos,
            current_market_value=mv,
            total_score=score,
            lineup_score=score,
            signals=[],
        )

    # 1 TW, 3 Starter-ABW + 1 starker Bank-ABW (15 M, score=0.45), 4 Starter-MF,
    # 2 Starter-ST + 1 schwacher 500k-Bank-ST (id="a-low-st", alphabetisch vor "z-high-abw").
    cands = [
        _cand("tw1", 1, 10_000_000, 0.8),
        _cand("abw1", 2, 10_000_000, 0.8),
        _cand("abw2", 2, 10_000_000, 0.8),
        _cand("abw3", 2, 10_000_000, 0.8),
        _cand("z-high-abw", 2, 15_000_000, 0.45),
        _cand("mf1", 3, 10_000_000, 0.8),
        _cand("mf2", 3, 10_000_000, 0.8),
        _cand("mf3", 3, 10_000_000, 0.8),
        _cand("mf4", 3, 10_000_000, 0.8),
        _cand("st1", 4, 10_000_000, 0.8),
        _cand("st2", 4, 10_000_000, 0.8),
        _cand("a-low-st", 4, 500_000, 0.05),
    ]
    forecasts = {
        c.player_id: {
            "expected_points": 0.0 if c.player_id in {"z-high-abw", "a-low-st"} else 80.0
        }
        for c in cands
    }
    rec = _forecast_lineup(cands, forecasts)
    assert rec is not None
    assert rec.formation == "4-4-2"
    assert "z-high-abw" in {s.player_id for s in rec.starters}
    assert "a-low-st" not in {s.player_id for s in rec.starters}

    # Auch best_partial_lineup bevorzugt bei 0.0 Punkten den wertvolleren Bankspieler:
    partial = best_partial_lineup(
        [_cand("a-cheap", 4, 500_000, 0.1), _cand("z-star", 4, 20_000_000, 0.4)],
        score=lambda _c: 0.0,
    )
    assert partial.starters[0].player_id == "z-star"


def test_comeback_prior_scales_with_market_value_and_rival_scarcity_counts() -> None:
    assert plan_mod._comeback_expected_points(2_000_000) == 55.0
    assert plan_mod._comeback_expected_points(6_000_000) == 75.0
    assert plan_mod._comeback_expected_points(25_000_000) > 105.0

    scarcity = plan_mod.estimate_rival_scarcity(
        [SimpleNamespace(seller_id="rival-1", player=SimpleNamespace(position=4))],
        position=4,
        rival_squads=[
            {"user_id": "rival-2", "spieler": [{"pos": 2, "prob": 1}]},
        ],
    )
    assert scarcity == 2


@pytest.mark.asyncio
async def test_kickbase_client_reuses_persistent_http_pool(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/me/budget",
        json={"b": 1_000_000, "p": 0, "bs": 0},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/me/budget",
        json={"b": 2_000_000, "p": 0, "bs": 0},
    )
    async with KickbaseClient(token="tkn", rate_limit_per_second=0) as client:
        b1 = await client.get_budget("42")
        pool_first = client._http
        b2 = await client.get_budget("42")
        pool_second = client._http
        assert b1 == 1_000_000 and b2 == 2_000_000
        assert pool_first is not None and pool_first is pool_second
    assert client._http is None

