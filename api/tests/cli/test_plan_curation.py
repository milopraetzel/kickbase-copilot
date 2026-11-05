"""Kuratierte Käufe am produktiven Plan-Aufruf, mit typisierten Domainobjekten."""

from datetime import timedelta

from kickbase_agent.cli import plan as plan_mod

from .test_plan import _bid
from .test_plan_roster_value import _forecast, _prepare, _replacement

COMPLETE = [1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4]


async def _curation_world(monkeypatch, *, score=0.8, delta=200_000):
    client = await _prepare(monkeypatch, positions=COMPLETE, replacement_score=score)
    item = _replacement()
    item = item.model_copy(
        update={
            "player": item.player.model_copy(
                update={"mv_change_24h": delta, "market_value_trend": 1 if delta > 0 else -1}
            )
        }
    )
    client.market = [item]

    async def get_player(league_id, player_id):
        return next(m.player for m in client.market if m.player.id == player_id)

    client.get_player = get_player
    if hasattr(plan_mod, "load_pending_bid_commitments"):
        monkeypatch.setattr(plan_mod, "load_pending_bid_commitments", lambda *args: [])
    return client


async def test_high_buy_score_does_not_buy_a_weaker_bench_player(monkeypatch):
    await _curation_world(monkeypatch, score=0.3)
    plan = await plan_mod.build_plan()
    assert plan.buys == []
    assert plan.swaps == []


async def test_falling_player_can_improve_the_projected_eleven(monkeypatch):
    await _curation_world(monkeypatch, delta=-200_000)
    plan = await plan_mod.build_plan()
    assert [pair.bid.action.player_id for pair in plan.swaps] == ["replacement"]


async def test_two_live_targets_do_not_impose_an_arbitrary_target_limit(monkeypatch):
    client = await _curation_world(monkeypatch)
    client.own_bids = [_bid("open-a", "a", 100_000), _bid("open-b", "b", 100_000)]
    plan = await plan_mod.build_plan()
    assert len(plan.swaps) == 1
    assert len(plan.team_plan["purchases"]) == 3


async def test_valid_rising_upgrade_keeps_an_explicit_absolute_limit(monkeypatch):
    await _curation_world(monkeypatch)
    plan = await plan_mod.build_plan()
    assert len(plan.swaps) == 1
    proposal = plan.swaps[0].bid
    review = proposal.action.purchase_review
    assert review["selected"] is True
    assert review["player_id"] == "replacement"
    assert proposal.action.amount <= review["max_bid_eur"]
    assert review["reasons"]


async def test_existing_target_gets_a_fresh_review_without_a_duplicate_bid(monkeypatch):
    client = await _curation_world(monkeypatch)
    client.own_bids = [_bid("replacement", "replacement-club", 10_000_000)]
    plan = await plan_mod.build_plan()
    assert plan.buys == [] and plan.swaps == []
    assert plan.purchase_reviews["replacement"]["selected"] is True
    assert plan.team_plan["reserved_cash_eur"] == 10_000_000


async def test_manager_offer_is_no_autonomous_target_but_live_manager_bid_binds_money(
    monkeypatch,
):
    """Paket 1, P0-1 (Studie 19.09.2026): Vorher plante die Engine das Manager-
    Listing als Tausch ein und reservierte 10,1 M. 359 von 528 Zielen waren
    Manager-Listings, seit 13.09. nahm kein Verkäufer an (0 von 8). Jetzt: kein
    autonomes Ziel — das laufende Gebot beim anderen Manager bindet weiter Geld."""
    client = await _curation_world(monkeypatch)
    client.market[0] = client.market[0].model_copy(update={"seller_id": "manager-a"})
    client.own_bids = [{**_bid("other-position", "other", 100_000), "seller": "Manager B"}]
    plan = await plan_mod.build_plan()
    assert plan.swaps == [] and plan.buys == []
    assert "Manager-Angebot" in plan.purchase_reviews["replacement"]["reasons"][0]
    assert plan.team_plan["reserved_cash_eur"] == 100_000
    assert plan.plan_base == 30_000_000 - 100_000


async def test_adjusted_pending_amount_is_reserved_and_deduplicated_with_live(monkeypatch):
    client = await _curation_world(monkeypatch)
    pending = [
        {
            "player_id": "other",
            "amount": 6_000_000,
            "position": 4,
            "listing_type": "kickbase",
            "manual": True,
        }
    ]
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments", lambda *args: pending)
    client.own_bids = [_bid("other", "other", 4_000_000)]
    plan = await plan_mod.build_plan()
    assert plan.plan_base == 24_000_000
    assert len(plan.swaps) == 1


async def test_flat_market_value_does_not_block_a_sporting_improvement(monkeypatch):
    client = await _curation_world(monkeypatch, delta=0)
    client.market[0] = client.market[0].model_copy(
        update={"player": client.market[0].player.model_copy(update={"market_value_trend": 1})}
    )
    plan = await plan_mod.build_plan()
    assert len(plan.swaps) == 1
    assert plan.purchase_reviews["replacement"]["selected"] is True


async def test_missing_fresh_fitness_cannot_produce_an_unexecutable_purchase(monkeypatch):
    client = await _curation_world(monkeypatch)
    fresh = client.market[0].player.model_copy(update={"fitness_status": None})

    async def get_player(league_id, player_id):
        return fresh

    client.get_player = get_player
    plan = await plan_mod.build_plan()
    assert plan.buys == []
    assert plan.swaps == []
    assert plan.purchase_reviews["replacement"]["eligible"] is False


async def test_cheaper_equal_player_limits_premium_and_is_selected_first(monkeypatch):
    client = await _curation_world(monkeypatch)
    cheaper = _replacement(mv=9_000_000, pid="cheaper")
    client.market[0] = client.market[0].model_copy(
        update={"expires_at": cheaper.expires_at + timedelta(minutes=10)}
    )
    client.market.append(cheaper)
    _forecast(
        monkeypatch,
        {
            **{f"roster-{i}": 0.9 for i in range(11)},
            "roster-1": 0.6,
            "replacement": 0.8,
            "cheaper": 0.8,
        },
    )
    # Beide Alternativen besitzen denselben sportlichen Index im Domainmodell.
    collect = plan_mod.collect_from_players

    async def equal_scores(players, *args):
        return [
            c.model_copy(update={"lineup_score": 0.8}) if c.player_id == "cheaper" else c
            for c in await collect(players, *args)
        ]

    monkeypatch.setattr(plan_mod, "collect_from_players", equal_scores)
    plan = await plan_mod.build_plan()
    assert [pair.bid.action.player_id for pair in plan.swaps] == ["cheaper"]
    assert plan.purchase_reviews["replacement"]["eligible"] is False
    review = plan.swaps[0].bid.action.purchase_review
    assert review["max_bid_eur"] == 9_900_000
    assert review["alternative_id"] == "replacement"


async def test_real_gap_can_use_a_low_heuristic_score_player(monkeypatch):
    client = await _curation_world(monkeypatch, score=0.4)
    original_squad = client.get_squad

    async def incomplete_squad(league_id):
        squad = await original_squad(league_id)
        return [s for s in squad if s.player.id != "roster-1"]

    client.get_squad = incomplete_squad
    plan = await plan_mod.build_plan()
    assert plan.need_positions
    assert len(plan.buys) == 1
    assert plan.team_plan["filled_slots"] == 11


async def test_better_sporting_target_is_not_lost_to_earlier_buy_score_budgeting(monkeypatch):
    client = await _curation_world(monkeypatch, score=0.7)
    client.budget = 13_000_000
    client.market = [_replacement(mv=8_000_000), _replacement(mv=8_000_000, pid="better")]
    collect = plan_mod.collect_from_players

    async def different_scores(players, *args):
        return [
            c.model_copy(update={"lineup_score": 0.9, "total_score": 0.85})
            if c.player_id == "better"
            else c
            for c in await collect(players, *args)
        ]

    monkeypatch.setattr(plan_mod, "collect_from_players", different_scores)
    _forecast(monkeypatch, {"replacement": 0.7, "better": 0.9})
    plan = await plan_mod.build_plan()
    assert [pair.bid.action.player_id for pair in plan.swaps] == ["better"]
