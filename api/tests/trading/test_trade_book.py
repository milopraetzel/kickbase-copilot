"""Handelsbuch (Handels-Autopilot, Emil 01.10.2026): wem gehören Spieler und Gebote?

Ein Handelsspieler ist ein Kaderspieler, dessen jüngstes echtes Gebot der Handels-Autopilot
abgab (Vorhaben mit Quelle `trade`). Engine, Aufstellung, Wächter und Radar lesen dasselbe
Buch — sonst stellte die Engine einen Handelsspieler in die Elf oder räumte ein Handelsgebot
als „ungedeckt“ ab.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, Intent, TransferAction
from kickbase_agent.trading.trade_book import (
    SALE_LEAD,
    SALE_LEAD_GAP,
    load_trade_book,
    sale_deadline,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


def _bid(session, pid, amount, *, at, trade, status="executed", dry_run=False, outcome=None,
         intent_status="executed", league="L1"):
    """Ein ausgeführtes Gebot samt Vorhaben, so wie der Intent-Executor es hinterlässt."""
    action = TransferAction(
        league_id=league, action_type="bid", player_id=pid, amount=amount, status=status,
        idempotency_key=f"k-{pid}-{at.isoformat()}", rule_id="intent-bid", dry_run=dry_run,
        payload={"outcome": outcome} if outcome else {}, created_at=at,
    )
    session.add(action)
    session.flush()
    intent = Intent(
        created_at=at - H, updated_at=at, kind="bid", player_id=pid, player_name=f"P{pid}",
        amount=amount, sources={"trade": True, "league_id": league} if trade else
        {"league_id": league}, listing_type="kickbase", deadline=at + 15 * timedelta(minutes=1),
        execute_at=at, status=intent_status, executed_action_id=action.id,
    )
    session.add(intent)
    session.commit()
    return action, intent


def _planned(session, pid, amount, *, trade=True, status="planned", adjusted_from=None):
    session.add(Intent(
        created_at=NOW, updated_at=NOW, kind="bid", player_id=pid, player_name=f"P{pid}",
        amount=amount, sources={"trade": True} if trade else {}, listing_type="kickbase",
        execute_at=NOW + H, status=status, adjusted_from=adjusted_from,
    ))
    session.commit()


def test_a_squad_player_bought_by_a_trade_bid_is_a_position(session):
    action, intent = _bid(session, "7", 2_600_000, at=NOW - 20 * H, trade=True)

    book = load_trade_book(session, now=NOW, squad_ids={"7", "8"}, league_id="L1")

    assert set(book.positions) == {"7"}
    pos = book.positions["7"]
    assert pos.buy_price == 2_600_000 and pos.intent_id == intent.id
    assert pos.bought_at == intent.deadline.replace(tzinfo=UTC)
    assert book.exposure == 2_600_000


def test_a_later_engine_bid_takes_the_player_out_of_the_trade_book(session):
    _bid(session, "7", 2_600_000, at=NOW - 48 * H, trade=True)
    _bid(session, "7", 3_000_000, at=NOW - 2 * H, trade=False)

    book = load_trade_book(session, now=NOW, squad_ids={"7"}, league_id="L1")

    assert book.positions == {}


def test_a_lost_trade_auction_never_makes_a_later_buy_a_trade(session):
    """Verloren laut Gebots-Nachschau, später auf anderem Weg gekauft: kein Handelsspieler."""
    _bid(session, "7", 2_600_000, at=NOW - 30 * H, trade=True, outcome={"result": "lost"})

    book = load_trade_book(session, now=NOW, squad_ids={"7"}, league_id="L1")

    assert book.positions == {}


def test_the_confirmed_price_wins_over_the_bid_amount(session):
    _bid(session, "7", 2_600_000, at=NOW - 20 * H, trade=True,
         outcome={"result": "won", "winner_amount": 2_590_000})

    book = load_trade_book(session, now=NOW, squad_ids={"7"}, league_id="L1")

    assert book.positions["7"].buy_price == 2_590_000


def test_dry_runs_and_other_leagues_do_not_count(session):
    _bid(session, "7", 2_600_000, at=NOW - 20 * H, trade=True, dry_run=True)
    _bid(session, "8", 1_000_000, at=NOW - 20 * H, trade=True, league="L2")

    book = load_trade_book(session, now=NOW, squad_ids={"7", "8"}, league_id="L1")

    assert book.positions == {}


def test_live_and_planned_trade_bids_are_separate_from_engine_bids(session):
    _bid(session, "9", 4_200_000, at=NOW - 2 * H, trade=True)      # live Handelsgebot
    _bid(session, "10", 1_000_000, at=NOW - 2 * H, trade=False)    # live Engine-Gebot
    _planned(session, "11", 3_100_000)                               # geplantes Handelsgebot
    _planned(session, "12", 900_000, trade=False)                    # Engine-Vorhaben

    book = load_trade_book(session, now=NOW, squad_ids=set(), league_id="L1",
                           live_bids={"9": 4_200_000, "10": 1_000_000})

    assert book.live_bids == {"9": 4_200_000}
    assert book.pending == {"11": 3_100_000}
    assert book.bid_ids == {"9", "11"}
    assert book.exposure == 7_300_000


def test_only_plain_planned_trade_bids_yield_to_the_engine(session):
    """Befund 03.10.2026: Ein geplantes Handelsgebot weicht einem Kauf der Engine und bindet ihr
    Geld deshalb nicht. Fest bleiben abgegebene, gerade ausgeführte und von Emil angepasste
    Handelsgebote — sie weichen nicht, also binden sie weiter. Der Handelsrahmen zählt alle."""
    _bid(session, "9", 4_200_000, at=NOW - 2 * H, trade=True)       # abgegeben
    _planned(session, "9", 4_500_000)                                 # geplante Erhöhung
    _planned(session, "11", 3_100_000)                                # weicht
    _planned(session, "12", 2_000_000, status="executing")           # geht gerade hinaus
    _planned(session, "13", 1_500_000, adjusted_from=1_200_000)      # Emils Betrag

    book = load_trade_book(session, now=NOW, squad_ids=set(), league_id="L1",
                           live_bids={"9": 4_200_000})

    assert book.firm_bids == {"9": 4_200_000, "12": 2_000_000, "13": 1_500_000}
    assert book.exposure == 4_500_000 + 3_100_000 + 2_000_000 + 1_500_000


def test_a_planned_raise_of_a_live_trade_bid_is_counted_once(session):
    _bid(session, "9", 4_200_000, at=NOW - 2 * H, trade=True)
    _planned(session, "9", 4_500_000)

    book = load_trade_book(session, now=NOW, squad_ids=set(), league_id="L1",
                           live_bids={"9": 4_200_000})

    assert book.exposure == 4_500_000


def test_old_trades_fall_out_of_the_book(session):
    _bid(session, "7", 2_600_000, at=NOW - 40 * 24 * H, trade=True)

    book = load_trade_book(session, now=NOW, squad_ids={"7"}, league_id="L1")

    assert book.positions == {}


@pytest.mark.parametrize(("regular", "lead"), [(10, SALE_LEAD_GAP), (11, SALE_LEAD),
                                                (14, SALE_LEAD)])
def test_the_sale_deadline_comes_earlier_while_the_eleven_has_gaps(regular, lead):
    deadline = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)

    assert sale_deadline(deadline, regular) == deadline - lead
    assert sale_deadline(None, regular) is None


def test_the_sale_leads_match_the_approved_spec():
    assert SALE_LEAD_GAP == timedelta(hours=96)
    assert SALE_LEAD == timedelta(hours=6)


def _sold(session, pid, *, at, kind="sell", status="executed", dry_run=False):
    session.add(TransferAction(
        league_id="L1", action_type=kind, player_id=pid, amount=1, status=status,
        idempotency_key=f"s-{pid}-{at.isoformat()}", rule_id="intent-sell", dry_run=dry_run,
        created_at=at,
    ))
    session.commit()


def test_a_later_sale_ends_the_trade_even_if_the_player_comes_back(session):
    """Review 02.10.2026 (P0): Der Autopilot kaufte und verkaufte X; Emil kauft ihn später in
    der App zurück (keine Audit-Zeile). Ohne Verkaufsprüfung blieb das alte Handelsgebot das
    jüngste Gebot — X wäre auf der Bank gelandet und wieder verkauft worden."""
    _bid(session, "7", 2_600_000, at=NOW - 5 * 24 * H, trade=True)
    _sold(session, "7", at=NOW - 3 * 24 * H)

    assert load_trade_book(session, now=NOW, squad_ids={"7"}, league_id="L1").positions == {}


def test_a_sale_before_the_trade_bid_does_not_matter(session):
    _sold(session, "7", at=NOW - 6 * 24 * H, kind="accept")
    _sold(session, "7", at=NOW - 2 * H, dry_run=True)  # Trockenlauf ist kein Verkauf
    _bid(session, "7", 2_600_000, at=NOW - 5 * 24 * H, trade=True)

    assert set(load_trade_book(session, now=NOW, squad_ids={"7"},
                               league_id="L1").positions) == {"7"}


def test_sale_cover_counts_ninety_percent_until_the_sale_deadline_has_passed():
    from kickbase_agent.trading.trade_book import SALE_GRACE, sale_cover

    sale_by = NOW + 10 * H
    assert sale_cover(held_mv=[4_000_000], bid_target_mv=[2_000_000], now=NOW,
                      sale_by=sale_by) == 4_000_000 + 2_000_000
    late = sale_by + SALE_GRACE
    # Codex-Review 02.10.2026: Nach der Karenz zählt der Handel gar nicht mehr — auch kein
    # laufendes Handelsgebot (gewonnen wäre es ein Handelsspieler, der sofort nicht mehr zählt).
    assert sale_cover(held_mv=[4_000_000], bid_target_mv=[2_000_000], now=late,
                      sale_by=sale_by) == 0
    assert sale_cover(held_mv=[4_000_000], bid_target_mv=[], now=late, sale_by=None) == 4_000_000


def test_the_sale_still_counts_during_the_grace_after_the_sale_deadline():
    """Verkauf am Termin, Planer :17/:47, Executor alle 5 min: In der Karenz ist der Verkauf
    unterwegs, nicht gescheitert."""
    from kickbase_agent.trading.trade_book import SALE_GRACE, sale_cover, sale_overdue

    just_before = NOW + SALE_GRACE - timedelta(minutes=1)
    assert sale_cover(held_mv=[4_000_000], bid_target_mv=[], now=just_before,
                      sale_by=NOW) == 4_000_000
    assert not sale_overdue(just_before, NOW)
    assert sale_overdue(NOW + SALE_GRACE, NOW)
    assert not sale_overdue(NOW + SALE_GRACE, None)


@pytest.mark.parametrize(("positions", "filled"), [
    ([1, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4], 11),
    ([1, 1, 2, 2, 2, 3, 3, 3, 3, 4, 4], 10),  # zwei Torhüter: ein Platz bleibt offen
    ([1, 2, 2, 2, 3, 3, 4], 7),
    ([1, 2, 2, 0, 3], 5),  # unbekannte Position: Köpfe zählen
])
def test_regular_fill_counts_places_in_the_best_formation(positions, filled):
    from kickbase_agent.trading.trade_book import regular_fill

    assert regular_fill(positions) == filled


def test_live_swap_cover_credits_waiting_swap_sell_for_live_bid(session):
    from kickbase_agent.trading.trade_book import live_swap_cover

    sell_row = Intent(
        created_at=NOW,
        updated_at=NOW,
        kind="sell",
        player_id="adeline",
        player_name="Martin Adeline",
        amount=4_409_573,
        sources={
            "rule_id": "swap_sell",
            "swap_dispo": True,
            "swap_for": "mohya",
            "after_squad_size": 12,
        },
        listing_type="kickbase",
        execute_at=NOW,
        status="planned",
    )
    squad_mv = {f"p{i}": 10_000_000 for i in range(11)}
    squad_mv["adeline"] = 4_409_573
    cover_eur, covered_ids = live_swap_cover(
        [sell_row],
        squad_mv=squad_mv,
        live_bids={"mohya": 12_000_000},
        now=NOW,
    )
    assert covered_ids == {"mohya"}
    assert cover_eur == int(4_409_573 * 0.95)

