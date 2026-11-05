"""Gebotsregel V3 am echten build_plan-Aufruf (Emil 25.09.2026: „GO für V3").

Ohne geladenes Zuschlagsmodell bietet die Engine nicht mehr den Mindestpreis (MW + 0 %,
seit 11.09. kein einziger Zuschlag), sondern den V3-Aufschlag — begrenzt auf die
wirtschaftliche Obergrenze des Mannschaftsvergleichs und die technischen Kappen.
Stehende Gebote behalten ihren Betrag. Reine Domain-Testdaten, keine API-Fixtures.
"""

from datetime import UTC, datetime

from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.decision.bid_premium import bid_premium
from kickbase_agent.decision.purchase_review import purchase_review_veto

from .test_plan import _bid
from .test_plan_dispo import _only_swap, _striker, _swap_world
from .test_plan_paket1 import NINE, TEN
from .test_plan_roster_value import _prepare, _replacement


def _trending(listing, change: int, observed: bool | None = True):
    """Marktzeile mit beobachteter 24-h-Änderung (`tfhmvt`), wie `Player.from_api`."""
    player = listing.player.model_copy(
        update={"mv_change_24h": change, "mv_change_24h_observed": observed}
    )
    return listing.model_copy(update={"player": player})


async def test_gap_bid_uses_rule_v3_when_the_trend_is_observed(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_trending(_replacement(mv=1_020_000, pid="kb"), 20_000)]  # +2 %

    plan = await plan_mod.build_plan()

    [buy] = plan.buys
    review = plan.purchase_reviews["kb"]
    assert buy.action.amount == review["target_bid_eur"] == 1_102_212
    assert review["bid_premium"]["rule"] == "prognose"
    assert review["bid_premium"]["amount_eur"] == 1_102_212
    assert any(reason.startswith("Gebotsregel V3") for reason in review["reasons"])
    assert purchase_review_veto(
        review, player_id="kb", amount=1_102_212, now=datetime.now(UTC)
    ) is None


async def test_unobserved_trend_keeps_the_minimum_entry(monkeypatch):
    # Ohne `tfhmvt` setzt `Player.from_api` ausdrücklich False (nicht None).
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_trending(_replacement(mv=1_020_000, pid="kb"), 20_000, observed=False)]

    plan = await plan_mod.build_plan()

    [buy] = plan.buys
    review = plan.purchase_reviews["kb"]
    assert buy.action.amount == 1_020_000
    assert review["bid_premium"] is None
    assert any(reason.startswith("Kleinster sinnvoller Einstieg") for reason in review["reasons"])


async def test_rule_v3_stops_at_the_money_the_plan_has(monkeypatch):
    # 1.060.000 € Konto: 10.000 € Anpfiff-Puffer, 1.050.000 € fürs Gebot; V3 wollte 1.102.212 €.
    client = await _prepare(monkeypatch, positions=TEN, cash=1_060_000)
    client.market = [_trending(_replacement(mv=1_020_000, pid="kb"), 20_000)]

    plan = await plan_mod.build_plan()

    [buy] = plan.buys
    review = plan.purchase_reviews["kb"]
    assert buy.action.amount == review["max_bid_eur"] == 1_050_000
    assert review["bid_premium"]["amount_eur"] == 1_102_212
    assert review["bid_premium"]["plan_bid_eur"] == 1_050_000
    assert review["bid_premium"]["plan_applied"] is False
    assert any("Obergrenze" in reason for reason in review["reasons"])


async def test_standing_bid_keeps_its_amount(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_trending(_replacement(mv=1_020_000, pid="kb"), 20_000)]
    client.own_bids = [_bid("kb", "replacement-club", 1_020_000)]

    plan = await plan_mod.build_plan()

    assert plan.buys == []
    review = plan.purchase_reviews["kb"]
    assert review["target_bid_eur"] == 1_020_000
    assert review["bid_premium"] is None


async def test_rule_v3_never_costs_the_second_open_slot(monkeypatch):
    """Review 25.09.2026, Befund 1: Zwei offene Plätze, 8,40 M Planungsgeld, zwei
    Verteidiger zu 4,08 M (+2 %). Zum Mindestpreis passen beide; V3 für den ersten
    (4,41 M) hätte den zweiten verdrängt. Der Aufschlag darf nur Restgeld kosten."""
    client = await _prepare(monkeypatch, positions=NINE, cash=8_410_000)
    client.market = [
        _trending(_replacement(mv=4_080_000, pid="abw1"), 80_000),
        _trending(_replacement(mv=4_080_000, pid="abw2"), 80_000),
    ]

    plan = await plan_mod.build_plan()

    amounts = sorted(p.action.amount for p in plan.buys)
    assert len(amounts) == 2 and amounts[0] >= 4_080_000
    assert plan.team_plan["filled_slots"] == 11
    assert 8_395_000 <= sum(amounts) <= 8_400_000  # Spielraum genutzt, Puffer bleibt


async def test_next_run_keeps_the_v3_provenance_of_the_planned_bid(monkeypatch):
    """Review 25.09.2026, Befund 2: Ab dem zweiten Lauf ist das Vorhaben eine
    Verpflichtung. Betrag, Herleitung und Grund müssen im Beleg bleiben, sonst steht
    im Audit „Kleinster sinnvoller Einstieg" und die Auswertung findet nichts."""
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_trending(_replacement(mv=1_020_000, pid="kb"), 20_000)]
    first = await plan_mod.build_plan()
    pending = [{
        "player_id": "kb", "amount": 1_102_212, "team_id": "replacement-club",
        "position": 2, "intent_id": 1, "status": "planned", "manual": False,
        "listing_type": "kickbase", "purchase_review": first.purchase_reviews["kb"],
    }]
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments", lambda *args: pending)

    second = await plan_mod.build_plan()

    review = second.purchase_reviews["kb"]
    assert second.buys == []
    assert review["target_bid_eur"] == 1_102_212
    assert review["bid_premium"]["rule"] == "prognose"
    assert review["bid_premium"]["plan_bid_eur"] == 1_102_212
    assert review["bid_premium"]["plan_applied"] is True
    assert any(r.startswith("Vorhandener Betrag nach Gebotsregel V3") for r in review["reasons"])
    assert not any(r.startswith("Kleinster sinnvoller Einstieg") for r in review["reasons"])


def test_a_later_evaluation_without_trend_drops_the_older_v3_entry():
    """Review 25.09.2026: Bewertet der Tausch-Pfad einen Spieler aus dem Hauptpfad neu
    und liefert der zweite Detailabruf kein tfhmvt, darf der Beleg nicht die
    Herleitung aus dem ersten Abruf tragen."""
    premiums = {}
    first = bid_premium(1_020_000, 20_000, True)

    plan_mod.record_premium(premiums, "st", first)
    assert premiums == {"st": first}
    plan_mod.record_premium(premiums, "st", None)
    assert premiums == {}


async def test_overdraft_swap_bids_rule_v3_too(monkeypatch):
    client = await _swap_world(monkeypatch)
    client.market = [_trending(_striker(12_120_000, "st"), 120_000)]  # +1 %

    plan = await plan_mod.build_plan()

    pair, review = _only_swap(plan)
    assert pair.bid.action.amount == review["bid_premium"]["amount_eur"] == 12_765_996
    assert purchase_review_veto(
        review, player_id="st", amount=12_765_996, now=datetime.now(UTC)
    ) is None


def test_standing_reason_names_rule_v3_only_for_the_v3_amount():
    """Re-Review 25.09.2026: Nach Emils +10 % oder bei manuellem Betrag ist das
    stehende Gebot nicht mehr „nach Gebotsregel V3"."""
    prior = {"purchase_review": {"bid_premium": {"amount_eur": 1_102_212, "rule": "prognose"}}}

    same = plan_mod._standing_bid_reason({**prior, "amount": 1_102_212}, "Grund.")
    other = plan_mod._standing_bid_reason({**prior, "amount": 1_212_433}, "Grund.")

    assert same.startswith("Vorhandener Betrag nach Gebotsregel V3")
    assert "nach Gebotsregel V3" not in other
    assert "weicht vom V3-Ziel 1.102.212 € ab" in other
