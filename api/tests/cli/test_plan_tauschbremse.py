"""Tauschbremse (Emil 12.10.2026, „Beide (Empfohlen)“).

Befund der Woche 05.–12.10.: Die Engine kaufte Itakura und tauschte ihn zwei Tage später
gegen Rothe (−0,16 M), kaufte Khedira und plante acht Minuten später den Tausch gegen
Onyedika — einen Spieler mit weniger Punkten im Schnitt. Zwei Regeln:

1. Haltefrist: Einen selbst gekauften Spieler gibt die Engine in den ersten 7 Tagen nach dem
   Kauf nicht per Tausch wieder ab.
2. Ein Tausch nur, wenn der erwartete Punktgewinn mehr wert ist als der Aufschlag beim Kauf.
   Den Verkauf zahlt Kickbase zum Marktwert (Feed 11.10.: Remberg für genau seinen
   Marktwert); bewertet wird ein Prognosepunkt mit dem Preis, den unsere Elf für ihre Punkte
   gezahlt hat: Teamwert ÷ Prognosepunkte der Elf über den Horizont.

Dieselbe Domain-Welt wie tests/cli/test_plan_dispo.py (keine neuen API-Fixtures).
"""

from datetime import UTC, datetime, timedelta

import pytest

from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.db.models import Base, TransferAction

from .test_plan_dispo import WEAK, _live_swap_bid, _only_swap, _pending_swap_bid, _swap_world
from .test_plan_roster_value import _forecast

DAY = timedelta(days=1)


def _bought(monkeypatch, **ages: timedelta) -> None:
    """Jüngste eigene Käufe je Spieler (wie load_recent_purchases sie liefert)."""
    now = datetime.now(UTC)
    monkeypatch.setattr(plan_mod, "load_recent_purchases",
                        lambda *_args, **_kw: {pid: now - age for pid, age in ages.items()})


# ------------------------------------------------------------------ Regel als Rechnung


@pytest.mark.parametrize(("gain", "premium", "pays"), [
    (10.0, 300_000, True),     # 10 Punkte × 40.000 € = 400.000 € > 300.000 € Aufschlag
    (10.0, 400_000, False),    # genau gleich viel wert: lohnt nicht
    (10.0, 500_000, False),
    (0.5, 30_000, False),      # fast kein Gewinn, kleiner Aufschlag
    (0.0, 0, False),           # ohne Punktgewinn kein Tausch, auch ohne Aufschlag
    (3.0, 0, True),            # Gebot zum Marktwert: jeder echte Gewinn lohnt
])
def test_a_swap_pays_off_only_if_its_points_are_worth_more_than_the_premium(gain, premium, pays):
    assert plan_mod.swap_pays_off(
        gain_points=gain, premium_eur=premium, team_value_eur=120_000_000, team_points=3_000.0,
    ) is pays


def test_without_points_in_the_team_the_rule_cannot_price_a_point():
    assert plan_mod.swap_pays_off(
        gain_points=10.0, premium_eur=1, team_value_eur=120_000_000, team_points=0.0,
    ) is False


# ------------------------------------------------------------------ Haltefrist


async def test_a_player_bought_less_than_seven_days_ago_is_not_swapped_out(monkeypatch):
    """Itakura/Khedira-Fall: frisch gekauft, sofort wieder Verkaufsseite eines Tauschs."""
    await _swap_world(monkeypatch)
    _bought(monkeypatch, **{WEAK: 2 * DAY})

    plan = await plan_mod.build_plan()

    assert plan.swaps == []
    assert any("Tauschbremse" in note and "Kader 10" in note for note in plan.curation_notes)


async def test_after_seven_days_the_player_can_be_swapped(monkeypatch):
    await _swap_world(monkeypatch)
    _bought(monkeypatch, **{WEAK: 8 * DAY})

    plan = await plan_mod.build_plan()

    [pair] = plan.swaps
    assert pair.faller.player_id == WEAK


async def test_the_hold_also_stops_a_regular_swap_of_a_displaced_starter(monkeypatch):
    """Auch der gewöhnliche Tausch (verdrängter Elf-Spieler geht nach dem Zuschlag) hält
    die Frist ein — die Regel gilt für jeden Tausch-Verkauf der Engine."""
    from .test_plan_roster_value import _prepare, _replacement

    positions = [1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4]
    client = await _prepare(monkeypatch, positions=positions, replacement_score=0.7)
    client.market = [_replacement()]
    baseline = await plan_mod.build_plan()
    assert len(baseline.swaps) == 1  # Gegenprobe: ohne Frist ein Tausch
    _bought(monkeypatch, **{f"roster-{i}": DAY for i in range(len(positions))})

    plan = await plan_mod.build_plan()

    assert plan.swaps == []


async def test_an_already_planned_swap_on_a_fresh_purchase_loses_its_credit(monkeypatch):
    """Codex-Review 12.10.2026: Ein schon geplantes, noch nicht abgegebenes Tauschgebot auf
    einen frisch gekauften Spieler läuft nicht weiter — der nächste Plan rechnet den
    gekoppelten Verkauf nicht mehr an, die Deckungsreparatur gibt die Reservierung frei."""
    await _swap_world(monkeypatch)
    first = await plan_mod.build_plan()
    pair, review = _only_swap(first)
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments",
                        lambda *args: _pending_swap_bid(review, pair.bid.action.amount))
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *args: [{"player_id": WEAK, "for_player_id": "st"}])
    _bought(monkeypatch, **{WEAK: 2 * DAY})

    plan = await plan_mod.build_plan()

    assert plan.purchase_reviews["st"].get("selected") is not True
    assert plan.team_plan["funding_recovery"]["released_pending_ids"] == [41]


async def test_a_live_swap_bid_on_a_fresh_purchase_keeps_its_credit(monkeypatch):
    """Ein schon abgegebenes Tauschgebot bleibt mit seinem Verkauf eine Einheit bis zum
    Auktionsende — die Haltefrist greift nur, solange noch kein Gebot steht."""
    client = await _swap_world(monkeypatch)
    first = await plan_mod.build_plan()
    pair, _ = _only_swap(first)
    client.own_bids = _live_swap_bid(pair.bid.action.amount)
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *args: [{"player_id": WEAK, "for_player_id": "st"}])
    _bought(monkeypatch, **{WEAK: 2 * DAY})

    plan = await plan_mod.build_plan()

    assert plan.team_plan["technical_limits"]["swap_credit_eur"] == 11_700_000


# ------------------------------------------------------------------ Punktgewinn gegen Aufschlag


async def test_a_swap_whose_points_do_not_cover_the_premium_is_not_planned(monkeypatch):
    """Fast gleich gute Spieler: Der Gewinn (0,04 Prognosepunkte) ist weniger wert als jeder
    Aufschlag nach Gebotsregel V3 (mindestens +0,5 % auf 12 M)."""
    await _swap_world(monkeypatch)
    _forecast(monkeypatch, {WEAK: 0.8999, "st": 0.9})

    plan = await plan_mod.build_plan()

    assert plan.swaps == []
    assert any("lohnt nicht" in note for note in plan.curation_notes)


async def test_a_clearly_better_starter_still_pays_off(monkeypatch):
    """Gegenprobe (Itakura-Fall): +240 Prognosepunkte sind ein Vielfaches des Aufschlags."""
    await _swap_world(monkeypatch)

    plan = await plan_mod.build_plan()

    [pair] = plan.swaps
    assert pair.bid.action.player_id == "st"
    assert not any("lohnt nicht" in note for note in plan.curation_notes)


# ------------------------------------------------------------------ Kaufzeiten aus dem Audit


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _action(session, pid, at, *, kind="bid", status="executed", dry_run=False, league="L1"):
    session.add(TransferAction(
        league_id=league, action_type=kind, player_id=pid, amount=1_000_000, status=status,
        idempotency_key=f"k-{pid}-{kind}-{at.isoformat()}-{league}-{dry_run}-{status}",
        rule_id="intent-bid", dry_run=dry_run, payload={}, created_at=at,
    ))
    session.commit()


def test_recent_purchases_come_from_real_executed_bids_of_the_league(session):
    now = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)
    _action(session, "khedira", now - DAY)
    _action(session, "khedira", now - 20 * DAY)              # älteres Gebot: jüngstes zählt
    _action(session, "probe", now - DAY, dry_run=True)        # Probelauf ist kein Kauf
    _action(session, "fremd", now - DAY, league="L2")        # andere Liga
    _action(session, "abgelehnt", now - DAY, status="refused")
    _action(session, "verkauft", now - DAY, kind="sell")     # Verkauf ist kein Kauf
    _action(session, "alt", now - 40 * DAY)                  # außerhalb des Lesefensters

    bought = plan_mod.load_recent_purchases(session, "L1", now)

    assert bought == {"khedira": (now - DAY).replace(tzinfo=UTC)}


def test_without_a_readable_database_nothing_is_held():
    assert plan_mod.load_recent_purchases(None, "L1", datetime.now(UTC)) == {}


# ---------------------------------------------- Hebel C: KB 4/5, Cross-Pos & Defizit-Abbau


async def test_a_freshly_bought_player_who_dropped_to_kb_4_or_5_is_not_held_back(monkeypatch):
    """Fällt ein frisch gekaufter Spieler auf KB-Stufe 4 oder 5 (Startelfrolle verloren),
    hebt das die 7-Tage-Tauschbremse auf."""
    client = await _swap_world(monkeypatch)
    squad = await client.get_squad("L1")
    for sp in squad:
        if sp.player.id == WEAK:
            sp.player = sp.player.model_copy(update={"lineup_prob": 4})

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad
    _bought(monkeypatch, **{WEAK: 2 * DAY})

    plan = await plan_mod.build_plan()

    [pair] = plan.swaps
    assert pair.faller.player_id == WEAK


async def test_cross_position_overdraft_swap_replaces_non_starter_across_formations(monkeypatch):
    """Hebel C: Ein neuer Starter (z. B. MF) darf über einen Formationswechsel (4-4-2 -> 4-5-1)
    einen Nicht-Starter einer anderen Position (z. B. ST KB 5) ersetzen."""
    from kickbase_agent.kickbase.models import Position

    # 1 TW, 4 ABW, 4 MF, 2 ST (4-4-2); der zweite Stürmer (roster-10) ist KB 5.
    specs = [(pos, 13_000_000) for pos in [1, 2, 2, 2, 2, 3, 3, 3, 3, 4]] + [(4, 13_000_000)]
    client = await _swap_world(monkeypatch, specs=specs)
    squad = await client.get_squad("L1")
    for sp in squad:
        if sp.player.id == WEAK:
            sp.player = sp.player.model_copy(update={"lineup_prob": 5})

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad
    # Am Markt steht ein Mittelfeld-Starter ("st" mit Position MF) statt eines Stürmers.
    [listing] = client.market
    client.market = [
        listing.model_copy(
            update={"player": listing.player.model_copy(update={"position": Position.MIDFIELDER})}
        )
    ]

    plan = await plan_mod.build_plan()

    [pair] = plan.swaps
    assert pair.faller.player_id == WEAK
    assert pair.bid.action.player_id == "st"
    assert plan.team_plan["formation"] == "4-5-1"


async def test_negative_cash_proposes_deficit_cover_sells_from_non_starters_only(monkeypatch):
    """Hebel C: Bei negativem Kontostand (z. B. −4,57 M €) und > 11 Spielern verkauft
    sell_deficit_cover gezielt fitte Nicht-Starter (KB 4–5 / LI-Bank), bis das Defizit
    gedeckt ist, während verletzte Starter (LI OUT) und die 11er-Formation geschützt bleiben."""
    from kickbase_agent.worker import intents as wi

    specs = (
        [(pos, 13_000_000) for pos in [1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4]]
        + [(4, 3_840_000), (2, 3_840_000)]
    )
    client = await _swap_world(monkeypatch, specs=specs, cash=-4_500_000)
    client.market = []
    squad = await client.get_squad("L1")
    for sp in squad:
        if sp.player.id == "roster-11":
            sp.player = sp.player.model_copy(update={"lineup_prob": 5, "last_name": "Hoeler"})
        elif sp.player.id == "roster-12":
            sp.player = sp.player.model_copy(update={"lineup_prob": 4, "last_name": "Schlotti"})

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad

    plan = await plan_mod.build_plan()

    deficit_sells = [p for p in plan.sells if p.rule_id == "sell_deficit_cover"]
    assert [p.action.player_id for p in deficit_sells] == ["roster-11", "roster-12"]
    now = datetime.now(UTC)
    drafts = wi.plan_intents(plan, [], now)
    sell_drafts = [d for d in drafts if d.kind == "sell"]
    assert len(sell_drafts) == 2
    assert all(d.execute_at == now + wi.DISPO_SMOOTHING_VETO for d in sell_drafts)


async def test_deficit_cover_prefers_falling_non_starter_over_rising_when_both_cover(monkeypatch):
    """Vozar-vs-Yamamoto-Fall (19.10.2026): Decken zwei Bankspieler (KB 5) das Defizit jeweils
    allein, wird der fallende (−73k/d) vor dem steigenden (+257k/d) verkauft."""
    specs = (
        [(pos, 13_000_000) for pos in [1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4]]
        + [(3, 1_760_000), (3, 1_260_000)]
    )
    client = await _swap_world(monkeypatch, specs=specs, cash=-1_100_000)
    client.market = []
    squad = await client.get_squad("L1")
    for sp in squad:
        if sp.player.id == "roster-11":
            sp.player = sp.player.model_copy(
                update={"lineup_prob": 5, "mv_change_24h": 257_000, "last_name": "Yamamoto"}
            )
        elif sp.player.id == "roster-12":
            sp.player = sp.player.model_copy(
                update={"lineup_prob": 5, "mv_change_24h": -73_000, "last_name": "Vozar"}
            )

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad

    plan = await plan_mod.build_plan()

    deficit_sells = [p for p in plan.sells if p.rule_id == "sell_deficit_cover"]
    assert [p.action.player_id for p in deficit_sells] == ["roster-12"]
