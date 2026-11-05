"""Engine-Plan und Handelsbuch (Handels-Autopilot, Emil 01.10.2026: „sie bleiben Bank“).

Handelsspieler zählen keinen Elf-Platz, abgegebene Handelsgebote binden Geld, sind aber weder
Engine-Ziel noch Engine-Verpflichtung — und die Deckungsreparatur gibt für ein geplantes Minus
aus dem Handel nie eigene Gebote auf. Nur geplante Handelsgebote binden nichts: Sie weichen
einem Kauf der Engine (Befund 03.10.2026).
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.trading.trade_book import TradeBook, TradePosition

from .test_plan_roster_value import _prepare, _replacement

ELEVEN = [1, 2, 2, 2, 3, 3, 3, 3, 4, 4, 2]
BERLIN = ZoneInfo("Europe/Berlin")


def _book(monkeypatch, *, positions=(), live=None, pending=None, firm=None) -> TradeBook:
    book = TradeBook(
        positions={pid: TradePosition(pid, 3_000_000, datetime.now(UTC) - timedelta(days=1), 1, 1)
                   for pid in positions},
        live_bids=dict(live or {}), pending=dict(pending or {}), firm=dict(firm or {}),
    )
    monkeypatch.setattr(plan_mod, "load_trade_book", lambda *args, **kwargs: book)
    return book


async def test_a_trade_player_never_fills_a_place_in_the_eleven(monkeypatch):
    await _prepare(monkeypatch, positions=ELEVEN)
    _book(monkeypatch, positions={"roster-10"})

    plan = await plan_mod.build_plan()

    assert plan.team_plan["baseline_filled_slots"] == 10  # ohne den Handelsspieler
    assert plan.trade_player_ids == frozenset({"roster-10"})
    assert [sp.player.id for sp in plan.lineup_squad] == [f"roster-{i}" for i in range(10)]
    assert "roster-10" not in {str(p.action.player_id) for p in plan.sells}
    assert any("1 Handelsspieler auf der Bank" in note for note in plan.curation_notes)
    # Ohne ihn hat die Elf eine Lücke: Verkauf spätestens T−96 h (nicht T−50 h).
    sale_by = (plan.matchday_deadline_at - timedelta(hours=96)).astimezone(BERLIN)
    assert any(f"Verkauf spätestens {sale_by:%d.%m. %H:%M}" in note
               for note in plan.curation_notes)


async def test_without_trade_the_eleven_is_complete(monkeypatch):
    await _prepare(monkeypatch, positions=ELEVEN)
    _book(monkeypatch)

    plan = await plan_mod.build_plan()

    assert plan.team_plan["baseline_filled_slots"] == 11
    assert plan.lineup_squad == plan.squad


async def test_a_live_trade_bid_binds_money_but_is_no_engine_target(monkeypatch):
    client = await _prepare(monkeypatch, positions=ELEVEN[:10], cash=10_000_000)
    client.market = [_replacement(mv=1_000_000, pid="gap"),
                     _replacement(mv=4_000_000, pid="traded")]
    client.own_bids = [{"player_id": "traded", "amount": 5_000_000, "team_id": "c9"}]
    _book(monkeypatch, live={"traded": 5_000_000})

    plan = await plan_mod.build_plan()

    assert plan.plan_base == 10_000_000 - 5_000_000  # Engine plant nur mit dem Rest
    assert "traded" not in plan.purchase_reviews
    assert "traded" not in {str(p.action.player_id) for p in plan.buys}
    # Das Live-Gebot steht im gemeinsamen Plan (die Schreibstelle zählt es frisch mit).
    assert plan.team_plan["open_bid_exposure_eur"] == 5_000_000
    assert plan.trade_bound == {"traded": 5_000_000}


async def test_trading_into_the_minus_never_triggers_the_funding_repair(monkeypatch):
    """Konto 2 M, Handelsgebot 5 M (ins Minus, Emil: „bis 20 M“): Die Engine gibt dafür weder
    ihre Reservierungen frei noch das Handelsgebot auf — das deckt der Verkauf."""
    client = await _prepare(monkeypatch, positions=ELEVEN, cash=2_000_000)
    client.market = [_replacement(mv=4_000_000, pid="traded")]
    client.own_bids = [{"player_id": "traded", "amount": 5_000_000, "team_id": "c9"}]
    _book(monkeypatch, live={"traded": 5_000_000})

    plan = await plan_mod.build_plan()

    recovery = plan.team_plan["funding_recovery"]
    assert recovery["status"] == "funded" and recovery["actions"] == []
    assert "blocked_bid_alternatives" not in recovery


async def test_a_negative_account_from_trade_players_is_covered_by_their_sale(monkeypatch):
    """Konto −1 M nach einem Handelskauf: Der Handelsspieler (MW 10 M) wird verkauft — keine
    Verkaufsalternative aus der Elf, keine Notbremsen-Notiz."""
    await _prepare(monkeypatch, positions=ELEVEN, cash=-1_000_000)
    _book(monkeypatch, positions={"roster-10"})

    plan = await plan_mod.build_plan()

    recovery = plan.team_plan["funding_recovery"]
    assert recovery["status"] == "funded"
    assert "sale_alternative" not in recovery


def _pending_trade_bid(monkeypatch, pid, amount, *, status="planned", manual=False):
    """Das Handelsvorhaben, wie load_pending_bid_commitments es liefert."""
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments", lambda *_: [
        {"player_id": pid, "amount": amount, "team_id": "c1", "position": 2,
         "intent_id": 1, "status": status, "manual": manual, "listing_type": "kickbase",
         "purchase_review": None, "trade": True},
    ])


async def test_a_planned_trade_bid_neither_binds_engine_money_nor_hides_the_player(monkeypatch):
    """Befund 03.10.2026: Ein nur GEPLANTES Handelsgebot (noch nicht bei Kickbase) band das
    Geld der Engine und nahm ihr den Spieler aus der Auswahl — danach wählte sie nichts mehr
    („fehlen 14,8 M Deckung“). Geplante Handelsgebote weichen der Engine: Sie kauft den
    Spieler für ihre Elf-Lücke mit dem vollen Geld."""
    client = await _prepare(monkeypatch, positions=ELEVEN[:10], cash=10_000_000)
    client.market = [_replacement(mv=2_000_000, pid="planned")]
    _pending_trade_bid(monkeypatch, "planned", 2_600_000)
    _book(monkeypatch, pending={"planned": 2_600_000})

    plan = await plan_mod.build_plan()

    assert plan.plan_base == 10_000_000
    assert "planned" in plan.purchase_reviews
    assert "planned" in {str(p.action.player_id) for p in plan.buys}
    assert plan.trade_bound == {}  # nichts als gebunden gezählt (Maßstab für „Plan überholt“)


async def test_a_firm_trade_bid_binds_engine_money_and_hides_the_player(monkeypatch):
    """Gegenprobe zum Test davor (dieselbe Welt): Ein Handelsgebot in Ausführung oder mit Emils
    Betrag weicht nicht — es bindet Geld wie ein abgegebenes, und die Engine plant den Spieler
    nicht (sonst stünden zwei Vorhaben auf demselben Spieler)."""
    client = await _prepare(monkeypatch, positions=ELEVEN[:10], cash=10_000_000)
    client.market = [_replacement(mv=2_000_000, pid="held")]
    _pending_trade_bid(monkeypatch, "held", 2_600_000, status="executing")
    _book(monkeypatch, pending={"held": 2_600_000}, firm={"held": 2_600_000})

    plan = await plan_mod.build_plan()

    assert plan.plan_base == 10_000_000 - 2_600_000
    assert "held" not in plan.purchase_reviews
    # Der Planer vergleicht später damit, was jetzt fest gebunden ist (Plan überholt).
    assert plan.trade_bound == {"held": 2_600_000}
