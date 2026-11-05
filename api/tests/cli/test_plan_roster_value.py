"""Ökonomische Regressionen am echten build_plan-Aufruf; reine Domain-Testdaten.

Keine neue API-Fixture: vorhandener FakePlanClient/Factory-Testadapter liefert
typisierte Domainobjekte. Die echten Kauf-/Swap-Policies bleiben eingebunden.
"""

from datetime import UTC, datetime, timedelta
from itertools import combinations
from types import SimpleNamespace

import kickbase_agent.cli.plan as plan_mod
import kickbase_agent.worker.reconcile as reconcile_mod
from kickbase_agent.intel.models import ExplosionCandidate, Signal
from kickbase_agent.kickbase.models import (
    MarketPlayer,
    MatchdayPerformance,
    Player,
    Position,
    SquadPlayer,
)
from kickbase_agent.scouts.ligainsider.models import Availability, Status

from .test_plan import FakePlanClient, _bid, _patch_world


def _forecast(monkeypatch, scores):
    """Explizite numerische Prognose-Attrappe, unabhängig vom Kauf-Heuristikscore."""
    from kickbase_agent.intel.forecasting.service import forecast_player

    def projected(player, performances, *, as_of, artifact=None):
        base = forecast_player(player, [], as_of=as_of)
        points = scores.get(player.id, 0.6) * 100
        return {
            **base,
            "expected_points": points,
            "horizon_points": points * 4,
            "model_version": "test-numeric-projection",
        }

    monkeypatch.setattr(plan_mod, "forecast_player", projected)


def _played_full_matchdays(count: int = 2) -> list[MatchdayPerformance]:
    """90 Minuten in den letzten `count` Spieltagen — in der Produktionsform von
    `load_performance` (Minuten > 0, Beobachtungs-Flags leer wie in der DB)."""
    now = datetime.now(UTC)
    return [
        MatchdayPerformance(
            season="2026/2027",
            matchday=index + 1,
            match_date=now - timedelta(days=7 * (count - index)),
            points=50,
            minutes=90,
        )
        for index in range(count)
    ]


def _squad(positions):
    return [
        SquadPlayer(
            player=Player(
                id=f"roster-{index}",
                first_name="",
                last_name=f"Kader {index}",
                position=Position(position),
                market_value=10_000_000,
                team_id=str(index),
                lineup_prob=1,
                fitness_status=0,
                mv_change_24h=100_000,
            ),
            is_starter=False,
        )
        for index, position in enumerate(positions)
    ]


async def _prepare(monkeypatch, *, positions, cash=30_000_000, replacement_score=0.6):
    client = FakePlanClient()
    squad = _squad(positions)
    client.budget = cash

    async def get_squad(league_id):
        return squad

    client.get_squad = get_squad
    _patch_world(monkeypatch, client, scores={})
    monkeypatch.setattr(
        plan_mod,
        "get_settings",
        lambda: SimpleNamespace(agent_auto_purge=False, kickbase_squad_limit=18),
    )
    _forecast(monkeypatch, {"replacement": replacement_score})
    monkeypatch.setattr(
        plan_mod, "matchday_deadline", lambda fixtures, now: (3, now + timedelta(days=3))
    )
    # Paket 1 (P0-5, 21.09.2026): Ziele ab 5 M brauchen einen Minutenbeleg. Diese
    # Domain-Welt prüft Kuratierung, Finanzierung und Tausch — nicht die Rolle.
    # Wie `enrich` unten („Starter belegt") sagt sie deshalb ausdrücklich: jeder
    # angefragte Spieler hat die letzten beiden Spieltage durchgespielt. Die
    # numerische Prognose-Attrappe ignoriert diese Zeilen. Tests zum Rollenbeleg
    # selbst überschreiben das (tests/cli/test_plan_paket1.py).
    monkeypatch.setattr(
        plan_mod,
        "load_performance",
        lambda session, ids: {pid: _played_full_matchdays() for pid in ids},
    )

    async def collect(players, *args):
        return [
            ExplosionCandidate(
                player_id=p.id,
                name=p.last_name,
                team_id=p.team_id,
                position=int(p.position),
                current_market_value=p.market_value,
                total_score=0.9,
                lineup_score=replacement_score if p.id == "replacement" else 0.6,
                signals=[
                    Signal(
                        name="trend",
                        score=0.1 if p.id == "roster-1" else 0.9,
                        weight=1,
                        reason="Domain-Test: fallend/steigend",
                    )
                ],
            )
            for p in players
        ]

    async def enrich(candidates, **_):
        return SimpleNamespace(
            candidates=candidates,
            snapshot=None,
            availabilities=[
                Availability(
                    player_id=c.player_id,
                    status=Status.STARTER,
                    multiplier=1,
                    reason="Domain-Test: Starter belegt",
                    confidence=1,
                )
                for c in candidates
            ],
        )

    monkeypatch.setattr(plan_mod, "collect_from_players", collect)
    monkeypatch.setattr(plan_mod, "enrich_candidates", enrich)
    return client


async def test_negative_cash_after_open_bids_is_not_erased(monkeypatch):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 3, 3, 3, 4], cash=2_000_000)
    client.own_bids = [_bid("open-target", "other-club", 8_000_000)]
    plan = await plan_mod.build_plan()
    assert plan.plan_base == -6_000_000
    assert plan.buys == []


async def test_uncovered_starting_eleven_cannot_use_target_resale_leverage(monkeypatch):
    await _prepare(monkeypatch, positions=[1, 2, 2, 2, 3, 3, 3, 4], cash=10_000_000)
    monkeypatch.setattr(plan_mod, "get_settings", lambda: SimpleNamespace(agent_auto_purge=True))
    monkeypatch.setattr(reconcile_mod, "PFAND_EINLOESER_BEREIT", True)
    plan = await plan_mod.build_plan()
    assert plan.need_positions
    assert plan.plan_base == 10_000_000


async def test_equal_eleven_score_does_not_justify_costly_twenty_percent_swap(monkeypatch):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4])
    client.market = [
        MarketPlayer(
            player=Player(
                id="replacement",
                first_name="",
                last_name="Ersatz",
                position=Position.DEFENDER,
                market_value=10_000_000,
                team_id="replacement-club",
                lineup_prob=1,
                fitness_status=0,
                mv_change_24h=100_000,
            ),
            price=10_000_000,
            expires_at=datetime.now(UTC) + timedelta(hours=4),
        )
    ]
    # Isoliert die echte Swap-Policy nach dem separaten normalen Einkaufspfad.
    plan = await plan_mod.build_plan()
    assert plan.swaps == []


def _replacement(mv=10_000_000, pid="replacement"):
    return MarketPlayer(
        player=Player(
            id=pid,
            first_name="",
            last_name="Ersatz",
            position=Position.DEFENDER,
            market_value=mv,
            team_id="replacement-club",
            lineup_prob=1,
            fitness_status=0,
            mv_change_24h=100_000,
        ),
        price=mv,
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )


async def test_financed_improvement_of_complete_eleven_remains_possible(monkeypatch):
    client = await _prepare(
        monkeypatch, positions=[1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4], replacement_score=0.7
    )
    client.market = [_replacement()]
    plan = await plan_mod.build_plan()
    assert len(plan.swaps) == 1
    assert "bedingte Punkte" in plan.swaps[0].bid.rationale
    assert plan.swaps[0].bid.action.purchase_review["purpose"] == "lineup_upgrade"


async def test_equal_eleven_does_not_pretend_replacement_resale_is_cash(monkeypatch):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4])
    client.market = [_replacement(mv=5_000_000)]
    plan = await plan_mod.build_plan()
    assert plan.swaps == []
    assert plan.buys == []  # Gleiche Punkte, zusätzliche Ausgabe; Verkauf noch ungesichert.


async def test_other_planned_purchases_are_paid_before_financing_a_swap(monkeypatch):
    client = await _prepare(
        monkeypatch,
        positions=[1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4],
        cash=15_000_000,  # voller Ersatz finanzierbar, keine Netto-Verkaufsrechnung
        replacement_score=0.7,
    )
    client.market = [_replacement()]
    baseline = await plan_mod.build_plan()
    assert len(baseline.swaps) == 1
    # Eine wirklich gespeicherte Verpflichtung zählt. Ein beliebiger, durch
    # die Kuration verworfener Testvorschlag darf dagegen kein Geld blockieren.
    monkeypatch.setattr(
        plan_mod,
        "load_pending_bid_commitments",
        lambda *args: [
            {
                "player_id": "other-target",
                "amount": 10_000_000,
                "position": 4,
                "listing_type": "kickbase",
                "manual": True,
            }
        ],
    )
    plan = await plan_mod.build_plan()
    assert plan.swaps == []
    assert plan.team_plan["reserved_cash_eur"] <= plan.budget


async def test_local_bench_upgrade_without_improving_best_eleven_is_rejected(monkeypatch):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4])
    client.market = [_replacement()]
    collect = plan_mod.collect_from_players

    async def bench_scores(players, *args):
        candidates = await collect(players, *args)
        return [
            c.model_copy(
                update={
                    "lineup_score": 0.2
                    if c.player_id == "roster-1"
                    else 0.3
                    if c.player_id == "replacement"
                    else 0.9
                }
            )
            for c in candidates
        ]

    monkeypatch.setattr(plan_mod, "collect_from_players", bench_scores)
    _forecast(monkeypatch, {"roster-1": 0.2, "replacement": 0.3})
    plan = await plan_mod.build_plan()
    assert plan.swaps == []
    assert plan.purchase_reviews["replacement"]["eligible"] is False


async def test_multiple_swaps_share_one_financing_budget(monkeypatch):
    client = await _prepare(
        monkeypatch, positions=[1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4], cash=15_000_000
    )
    client.market = [_replacement(pid="replacement-a"), _replacement(pid="replacement-b")]
    collect = plan_mod.collect_from_players

    async def two_fallers(players, *args):
        candidates = await collect(players, *args)
        return [
            c.model_copy(
                update={
                    "lineup_score": 0.7 if c.player_id.startswith("replacement-") else 0.6,
                    "signals": [
                        Signal(
                            name="trend",
                            score=0.1 if c.player_id in {"roster-1", "roster-2"} else 0.9,
                            weight=1,
                            reason="Domain-Test",
                        )
                    ],
                }
            )
            for c in candidates
        ]

    monkeypatch.setattr(plan_mod, "collect_from_players", two_fallers)
    _forecast(monkeypatch, {"replacement-a": 0.7, "replacement-b": 0.7})
    plan = await plan_mod.build_plan()
    assert len(plan.swaps) == 1
    assert plan.team_plan["reserved_cash_eur"] <= plan.budget


def _candidate_scores(monkeypatch, *, fallers, lineups, totals=None):
    collect = plan_mod.collect_from_players

    async def scored(players, *args):
        candidates = await collect(players, *args)
        return [
            c.model_copy(
                update={
                    "lineup_score": lineups.get(c.player_id, 0.6),
                    "total_score": (totals or {}).get(c.player_id, 0.9),
                    "signals": [
                        Signal(
                            name="trend",
                            score=0.1 if c.player_id in fallers else 0.9,
                            weight=1,
                            reason="Domain-Test",
                        )
                    ],
                }
            )
            for c in candidates
        ]

    monkeypatch.setattr(plan_mod, "collect_from_players", scored)
    _forecast(monkeypatch, lineups)


async def test_conditional_swap_proceeds_cannot_fund_independent_auctions(monkeypatch):
    cash = 6_000_000
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4], cash=cash)
    client.market = [
        _replacement(mv=4_000_000, pid="cheap"),
        _replacement(pid="expensive-a"),
        _replacement(pid="expensive-b"),
    ]
    _candidate_scores(
        monkeypatch,
        fallers={"roster-1", "roster-2", "roster-3"},
        lineups={"cheap": 0.7, "expensive-a": 0.7, "expensive-b": 0.7},
        totals={"cheap": 0.99, "expensive-a": 0.98, "expensive-b": 0.97},
    )
    plan = await plan_mod.build_plan()
    assert len(plan.swaps) == 1  # gleich gute teurere Ziele verlieren den Preisvergleich
    assert plan.purchase_reviews["expensive-a"]["eligible"] is False
    for count in range(1, len(plan.swaps) + 1):
        for winners in combinations(plan.swaps, count):
            assert cash - sum(pair.bid.action.amount for pair in winners) >= 0


async def test_rejected_top_buy_score_does_not_hide_better_eleven_alternative(monkeypatch):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4])
    client.market = [
        _replacement(pid="flat-high-total"),
        _replacement(pid="better-lower-total"),
    ]
    _candidate_scores(
        monkeypatch,
        fallers={"roster-1"},
        lineups={"flat-high-total": 0.6, "better-lower-total": 0.8},
        totals={"flat-high-total": 0.99, "better-lower-total": 0.90},
    )
    plan = await plan_mod.build_plan()
    assert [pair.bid.action.player_id for pair in plan.swaps] == ["better-lower-total"]
    assert plan.purchase_reviews["flat-high-total"]["eligible"] is False


async def test_swap_must_improve_eleven_with_other_planned_buys_included(monkeypatch):
    client = await _prepare(monkeypatch, positions=[1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4])
    client.market = [_replacement(pid="ordinary-buy"), _replacement(pid="small-upgrade")]
    _candidate_scores(
        monkeypatch,
        fallers={"roster-1"},
        lineups={"roster-1": 0.5, "ordinary-buy": 0.9, "small-upgrade": 0.55},
    )
    plan = await plan_mod.build_plan()
    assert [pair.bid.action.player_id for pair in plan.swaps] == ["ordinary-buy"]
    assert plan.buys == []
    assert plan.purchase_reviews["small-upgrade"]["selected"] is False
