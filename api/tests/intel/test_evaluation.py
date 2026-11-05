"""Treffer-Bilanz (intel/evaluation.py): Vorschläge vs. MW-Verlauf, Schatten vs. echt,
Tipps, Flips, Abdeckung — In-Memory-SQLite, keine Netzaufrufe."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import (
    Base,
    CommunityTip,
    Intent,
    Player,
    PlayerHistory,
    TransferAction,
)
from kickbase_agent.intel.evaluation import (
    MvSeries,
    anchor_day,
    collect_proposals,
    delta_pct,
    evaluate,
    match_player,
    summarize,
)

D = date(2026, 8, 20)
#: 22:20 Berlin (CEST) am Tag D = 20:20 UTC — nach dem 22:00-MW-Update => Anker = D
EVENING = datetime(2026, 8, 20, 20, 20, tzinfo=UTC)
#: 10:00 Berlin am Tag D = 08:00 UTC — vor dem Update => Anker = D-1
MORNING = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _day(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _history(session: Session, pid: str, values: dict[int, int], base: date = D) -> None:
    """values: Tages-Offset relativ zu ``base`` -> Marktwert."""
    for off, mv in values.items():
        session.add(
            PlayerHistory(player_id=pid, recorded_at=_day(base + timedelta(days=off)),
                          market_value=mv, points=0, is_starter=False)
        )
    session.commit()


def _player(session: Session, pid: str, name: str) -> None:
    session.add(Player(id=pid, kickbase_player_id=pid, name=name, position=3, team="2",
                       current_market_value=0))
    session.commit()


_seq = iter(range(1, 10_000))


def _won_payload() -> dict:
    return {
        "expectation": {"p": 0.5, "kind": "kickbase"},
        "outcome": {"result": "won", "at": EVENING.isoformat()},
    }


def _action(session: Session, pid: str, *, status: str = "proposed", rule_id: str = "buy_explosion",
            dry_run: bool = True, created_at: datetime = EVENING, amount: int = 1_000_000,
            action_type: str = "bid", payload: dict | None = None,
            league_id: str = "L1") -> TransferAction:
    row = TransferAction(
        league_id=league_id, action_type=action_type, player_id=pid, amount=amount, status=status,
        idempotency_key=f"k{next(_seq)}", rule_id=rule_id, rationale="r", dry_run=dry_run,
        created_at=created_at, resolved_at=created_at, payload=payload,
    )
    session.add(row)
    session.commit()
    return row


def _intent(session: Session, pid: str, *, status: str = "vetoed", created_at: datetime = EVENING,
            kind: str = "bid", executed_action_id: int | None = None) -> Intent:
    row = Intent(
        created_at=created_at, updated_at=created_at, kind=kind, player_id=pid,
        player_name=f"P{pid}", team_id="2", position=3, amount=1_000_000, rationale="r",
        sources={"rule_id": "buy_explosion"}, listing_type="kickbase", deadline=None,
        execute_at=created_at + timedelta(hours=2), status=status,
        executed_action_id=executed_action_id,
    )
    session.add(row)
    session.commit()
    return row


def _tip(session: Session, name: str, weight: float = 1.0, created_at: datetime = EVENING) -> None:
    session.add(CommunityTip(player_name=name, weight=weight, source="youtube", note="",
                             created_at=created_at))
    session.commit()


# ----------------------------------------------------------------- Bausteine


def test_anchor_day_follows_the_22h_berlin_market_value_update():
    assert anchor_day(EVENING) == D                       # 22:20 Berlin => heute
    assert anchor_day(MORNING) == D - timedelta(days=1)   # 10:00 Berlin => Vortag
    # 21:59 Berlin (19:59 UTC) => noch Vortag; 22:00 Berlin (20:00 UTC) => heute
    assert anchor_day(datetime(2026, 8, 20, 19, 59, tzinfo=UTC)) == D - timedelta(days=1)
    assert anchor_day(datetime(2026, 8, 20, 20, 0, tzinfo=UTC)) == D
    # naive Zeitstempel (SQLite) gelten als UTC
    assert anchor_day(datetime(2026, 8, 20, 20, 20)) == D


def test_mv_series_lookup_with_grace_windows():
    s = MvSeries.from_rows([(D, 100), (D + timedelta(days=1), 110), (D + timedelta(days=9), 150)])
    assert s.on_or_before(D) == 100
    assert s.on_or_before(D + timedelta(days=2)) is None         # Basis: nur der Tag selbst
    assert s.on_or_before(D + timedelta(days=2), grace_days=1) == 110   # explizite Toleranz
    assert s.on_or_before(D + timedelta(days=8), grace_days=3) is None  # 7 Tage alt => zu alt
    assert s.on_or_before(D - timedelta(days=1)) is None         # nichts davor
    assert s.on_or_after(D + timedelta(days=7)) == 150           # 2 Tage später, in Toleranz
    assert s.on_or_after(D + timedelta(days=6)) is None          # 3 Tage später => zu spät
    assert s.on_or_after(D + timedelta(days=10)) is None         # nichts danach
    assert s.latest() == 150
    assert MvSeries.from_rows([]).latest() is None
    # zwei Zeilen am selben Tag: die spätere gewinnt
    assert MvSeries.from_rows([(D, 1), (D, 2)]).values == [2]


def test_delta_and_summary_helpers():
    assert delta_pct(100, 110) == 10.0
    assert delta_pct(100, None) is None and delta_pct(0, 5) is None
    assert summarize([]) == {"n": 0, "hit_rate": None, "avg_delta_pct": None,
                             "median_delta_pct": None}
    out = summarize([10.0, -5.0, 0.0])
    assert out == {"n": 3, "hit_rate": 0.333, "avg_delta_pct": 1.67, "median_delta_pct": 0.0}


def test_match_player_prefers_exact_then_token_subset_then_substring():
    players = [("1", "Jamal Musiala"), ("2", "Niclas Füllkrug"), ("3", "Kim Min-jae")]
    assert match_player("füllkrug", players) == ("2", "Niclas Füllkrug")      # Token-Teilmenge
    assert match_player("NICLAS FULLKRUG", players) == ("2", "Niclas Füllkrug")  # exakt, akzentfrei
    assert match_player("Minjae", players) is None          # kein Token, kein Teilstring
    assert match_player("min jae", players) == ("3", "Kim Min-jae")
    assert match_player("Olise", players) is None
    assert match_player("", players) is None


# ----------------------------------------------------------------- evaluate()


def test_empty_db_yields_zero_values_without_errors(session):
    out = evaluate(session, now=EVENING)
    assert out["generated_at"] == EVENING.isoformat()
    for key in ("buy_hits", "shadow", "executed"):
        assert set(out[key]) == {"1d", "7d"}
        assert out[key]["1d"] == {"n": 0, "hit_rate": None, "avg_delta_pct": None,
                                  "median_delta_pct": None}
    assert out["tips"] == {"n": 0, "hit_rate_7d": None, "avg_delta_pct_7d": None, "items": []}
    assert out["flips"] == [] and out["flips_total_delta_eur"] == 0
    assert out["lineup"] == {"status": "ab SP1 (28.08.) messbar", "n": 0}
    assert out["coverage"]["proposals_total"] == 0
    assert out["coverage"]["with_followup_1d"] == 0 and out["coverage"]["with_followup_7d"] == 0


def test_shadow_proposal_hit_after_one_day_miss_after_seven(session):
    _history(session, "p1", {0: 100, 1: 110, 7: 90})
    _action(session, "p1", status="proposed", rule_id="buy_explosion", dry_run=True)
    out = evaluate(session, now=EVENING)
    assert out["buy_hits"]["1d"] == {"n": 1, "hit_rate": 1.0, "avg_delta_pct": 10.0,
                                     "median_delta_pct": 10.0}
    assert out["buy_hits"]["7d"] == {"n": 1, "hit_rate": 0.0, "avg_delta_pct": -10.0,
                                     "median_delta_pct": -10.0}
    assert out["shadow"] == out["buy_hits"]          # nur Schatten-Vorschläge
    assert out["executed"]["1d"]["n"] == 0
    assert out["coverage"] == {
        "proposals_total": 1, "with_followup_1d": 1, "with_followup_7d": 1, "rows_total": 1,
        "executed_total": 0, "shadow_total": 1, "open_total": 0,
    }


def test_executed_and_shadow_are_separated_and_aggregated(session):
    _history(session, "sh", {0: 100, 1: 120, 7: 130})                # Schatten: +20 / +30
    _history(session, "ex", {0: 200, 1: 190, 7: 210})                # echt: -5 / +5
    _action(session, "sh", status="skipped_dry_run", rule_id="auto-trading-buy", dry_run=True)
    _action(session, "ex", status="executed", rule_id="intent-bid", dry_run=False,
            amount=2_000_000, payload=_won_payload())
    out = evaluate(session, now=EVENING)
    assert out["shadow"]["1d"] == {"n": 1, "hit_rate": 1.0, "avg_delta_pct": 20.0,
                                   "median_delta_pct": 20.0}
    assert out["executed"]["1d"] == {"n": 1, "hit_rate": 0.0, "avg_delta_pct": -5.0,
                                     "median_delta_pct": -5.0}
    assert out["executed"]["7d"]["avg_delta_pct"] == 5.0
    assert out["buy_hits"]["1d"] == {"n": 2, "hit_rate": 0.5, "avg_delta_pct": 7.5,
                                     "median_delta_pct": 7.5}
    assert out["buy_hits"]["7d"]["n"] == 2 and out["buy_hits"]["7d"]["hit_rate"] == 1.0
    # Flip-Bilanz: echtes Gebot 2,0 M, letzter MW 210 => delta negativ
    assert len(out["flips"]) == 1
    flip = out["flips"][0]
    assert (flip["player_id"], flip["name"], flip["buy_price"]) == ("ex", "ex", 2_000_000)
    assert flip["mv_now"] == 210 and flip["delta_eur"] == 210 - 2_000_000
    assert out["flips_total_delta_eur"] == 210 - 2_000_000


def test_missing_followup_or_baseline_does_not_count(session):
    _history(session, "only_base", {0: 100})                  # keine Folgewerte
    _history(session, "only_later", {3: 100, 10: 120})        # kein Basiswert (Anker D)
    _action(session, "only_base")
    _action(session, "only_later")
    _action(session, "no_history_at_all")
    out = evaluate(session, now=EVENING)
    assert out["coverage"]["proposals_total"] == 3
    assert out["coverage"]["with_followup_1d"] == 0 and out["coverage"]["with_followup_7d"] == 0
    assert out["buy_hits"]["1d"]["n"] == 0 and out["buy_hits"]["7d"]["n"] == 0


def test_morning_proposal_anchors_on_previous_days_value(session):
    # 10:00 Berlin am Tag D: sichtbarer MW = Wert von D-1 (22:00-Update des Vortags)
    _history(session, "m", {-1: 100, 0: 120, 6: 150})
    _action(session, "m", created_at=MORNING)
    out = evaluate(session, now=EVENING)
    assert out["buy_hits"]["1d"]["avg_delta_pct"] == 20.0     # D vs D-1
    assert out["buy_hits"]["7d"]["avg_delta_pct"] == 50.0     # D+6 vs D-1


def test_same_player_same_day_counts_once_and_confirmed_acquisition_wins(session):
    _history(session, "dup", {0: 100, 1: 110, 7: 120})
    _action(session, "dup", status="proposed", rule_id="buy_explosion")            # Schatten
    _action(session, "dup", status="skipped_dry_run", rule_id="auto-trading-buy")  # Schatten
    _intent(session, "dup", status="vetoed")                                       # Schatten
    out = evaluate(session, now=EVENING)
    assert out["coverage"]["proposals_total"] == 1 and out["coverage"]["rows_total"] == 3
    assert out["shadow"]["1d"]["n"] == 1 and out["executed"]["1d"]["n"] == 0
    # Nur ein bestätigter Erwerb am selben Tag schlägt die Schatten-Einträge.
    won = _action(
        session,
        "dup",
        status="executed",
        rule_id="intent-bid",
        dry_run=False,
        payload=_won_payload(),
    )
    _intent(session, "dup", status="executed", executed_action_id=won.id)
    out = evaluate(session, now=EVENING)
    assert out["coverage"]["proposals_total"] == 1 and out["coverage"]["rows_total"] == 4
    assert out["executed"]["1d"]["n"] == 1 and out["shadow"]["1d"]["n"] == 0
    # ein anderer Tag ist ein neuer Vorschlag
    _action(session, "dup", created_at=EVENING + timedelta(days=1))
    out = evaluate(session, now=EVENING)
    assert out["coverage"]["proposals_total"] == 2


def test_intent_linked_action_is_not_double_counted_and_open_intents_only_in_buy_hits(session):
    _history(session, "li", {0: 100, 1: 110, 7: 120})
    action = _action(
        session,
        "li",
        status="executed",
        rule_id="intent-bid",
        dry_run=False,
        payload=_won_payload(),
    )
    _intent(session, "li", status="executed", executed_action_id=action.id)
    _history(session, "op", {0: 100, 1: 90, 7: 80})
    _intent(session, "op", status="planned")          # noch offen: weder Schatten noch echt
    proposals, rows = collect_proposals(session)
    assert rows == 2 and len(proposals) == 2
    by_pid = {p.player_id: p for p in proposals}
    assert by_pid["li"].bucket == "executed" and by_pid["li"].rows == 1
    assert by_pid["op"].bucket == "open"
    out = evaluate(session, now=EVENING)
    assert out["buy_hits"]["1d"]["n"] == 2
    assert out["executed"]["1d"]["n"] == 1 and out["shadow"]["1d"]["n"] == 0
    assert out["coverage"]["open_total"] == 1


def test_intent_cannot_inherit_another_players_won_outcome(session):
    _history(session, "intent-player", {0: 100, 1: 110, 7: 120})
    other = _action(
        session,
        "other-player",
        status="executed",
        rule_id="intent-bid",
        dry_run=False,
        payload=_won_payload(),
    )
    _intent(session, "intent-player", status="executed", executed_action_id=other.id)

    proposals, _ = collect_proposals(session)

    by_player = {p.player_id: p.bucket for p in proposals}
    assert by_player["intent-player"] == "open"
    assert by_player["other-player"] == "executed"


def test_multiple_intents_for_same_action_stay_one_verified_proposal(session):
    _history(session, "shared", {0: 100, 1: 110, 7: 120})
    action = _action(session, "shared", status="executed", dry_run=False,
                     payload=_won_payload())
    _intent(session, "shared", status="executed", executed_action_id=action.id)
    _intent(session, "shared", status="executed", executed_action_id=action.id,
            created_at=EVENING + timedelta(days=1))

    proposals, rows = collect_proposals(session)

    assert rows == 2
    assert len(proposals) == 1
    assert proposals[0].bucket == "executed"
    assert proposals[0].rows == 2


def test_vetoed_intent_cannot_downgrade_a_verified_acquisition(session):
    _history(session, "acquired", {0: 100, 1: 110, 7: 120})
    action = _action(session, "acquired", status="executed", dry_run=False,
                     payload=_won_payload())
    _intent(session, "acquired", status="vetoed", executed_action_id=action.id,
            created_at=EVENING + timedelta(days=1))

    proposals, _ = collect_proposals(session)

    assert len(proposals) == 1
    assert proposals[0].anchor == anchor_day(action.created_at)
    assert proposals[0].bucket == "executed"


def test_only_engine_buy_rules_and_bid_actions_count(session):
    _history(session, "x", {0: 100, 1: 110, 7: 120})
    _action(session, "x", rule_id="kickoff-bid-cancel", action_type="cancel_bid")
    _action(session, "x", rule_id="auto-bonus", action_type="bonus")
    _action(session, "x", rule_id="manual", action_type="bid")       # kein Engine-Vorschlag
    _action(session, "x", rule_id=None, action_type="bid")
    _intent(session, "x", kind="list", status="executed")            # kein Kauf
    out = evaluate(session, now=EVENING)
    assert out["coverage"]["proposals_total"] == 0
    for rule in ("buy_explosion", "buy_explosion-conviction", "auto-trading-buy", "intent-bid",
                 "mcp-bid", "trading_buy"):
        _action(session, "x", rule_id=rule, created_at=EVENING + timedelta(days=1))
    out = evaluate(session, now=EVENING)
    assert out["coverage"]["proposals_total"] == 1 and out["coverage"]["rows_total"] == 6


def test_tips_match_players_and_measure_seven_days(session):
    _player(session, "11", "Niclas Füllkrug")
    _player(session, "12", "Jamal Musiala")
    _history(session, "11", {0: 100, 7: 112})
    _history(session, "12", {0: 100, 7: 95})
    _tip(session, "Füllkrug", 1.0)                       # Kauf-Tipp, MW +12 % => Treffer
    _tip(session, "Musiala", -1.0)                       # Meide-Tipp, MW -5 % => Treffer
    _tip(session, "Unbekannter Spieler", 1.0)            # kein Match => zählt nicht
    _tip(session, "Musiala", 1.0, created_at=EVENING - timedelta(days=30))  # keine Historie
    out = evaluate(session, now=EVENING)
    tips = out["tips"]
    assert tips["n"] == 2 and tips["hit_rate_7d"] == 1.0
    assert tips["avg_delta_pct_7d"] == pytest.approx((12.0 - 5.0) / 2)
    assert len(tips["items"]) == 4
    by_name = {(t["player_name"], t["weight"]): t for t in tips["items"]}
    fk = by_name[("Füllkrug", 1.0)]
    assert fk["delta_pct_7d"] == 12.0 and fk["hit_7d"] is True
    assert fk["player_id"] == "11" and fk["matched_name"] == "Niclas Füllkrug"
    assert fk["source"] == "youtube" and fk["created_at"] == EVENING.isoformat()
    mu = by_name[("Musiala", -1.0)]
    assert mu["delta_pct_7d"] == -5.0 and mu["hit_7d"] is True
    unknown = by_name[("Unbekannter Spieler", 1.0)]
    assert unknown["delta_pct_7d"] is None and unknown["player_id"] is None
    assert unknown["hit_7d"] is None


def test_flips_use_player_names_and_skip_unknown_market_values(session):
    _player(session, "f1", "Michael Olise")
    _history(session, "f1", {0: 900_000, 3: 1_200_000})
    _action(session, "f1", status="executed", rule_id="mcp-bid", dry_run=False,
            amount=1_000_000, payload=_won_payload())
    _action(session, "f2", status="executed", rule_id="auto-trading-buy", dry_run=False,
            amount=500_000, created_at=EVENING + timedelta(days=1),
            payload=_won_payload())                                      # keine Historie
    _action(session, "f3", status="executed", rule_id="buy_explosion", dry_run=True,
            amount=700_000)                                               # Dry-Run => kein Flip
    out = evaluate(session, now=EVENING)
    assert [f["player_id"] for f in out["flips"]] == ["f2", "f1"]        # neueste zuerst
    f1 = out["flips"][1]
    assert f1["name"] == "Michael Olise" and f1["buy_price"] == 1_000_000
    assert f1["mv_now"] == 1_200_000 and f1["delta_eur"] == 200_000 and f1["delta_pct"] == 20.0
    assert f1["bought_at"] == EVENING.isoformat()
    f2 = out["flips"][0]
    assert f2["name"] == "f2" and f2["mv_now"] is None and f2["delta_eur"] is None
    assert f2["delta_pct"] is None
    assert out["flips_total_delta_eur"] == 200_000
    assert "Zuschlag" in out["flips_note"]


def test_unconfirmed_executed_bid_is_open_and_not_a_flip(session):
    _history(session, "open", {0: 100, 1: 110, 7: 120})
    _action(session, "open", status="executed", dry_run=False)

    out = evaluate(session, now=EVENING)

    assert out["coverage"]["open_total"] == 1
    assert out["coverage"]["executed_total"] == 0
    assert out["executed"]["1d"]["n"] == 0
    assert out["flips"] == []


def test_unproven_won_marker_without_reconcile_shape_is_not_an_acquisition(session):
    _history(session, "bare-won", {0: 100, 1: 110, 7: 120})
    _action(
        session,
        "bare-won",
        status="executed",
        dry_run=False,
        payload={"outcome": {"result": "won"}},
    )

    out = evaluate(session, now=EVENING)

    assert out["coverage"]["open_total"] == 1
    assert out["coverage"]["executed_total"] == 0
    assert out["flips"] == []


def test_lost_executed_bid_is_shadow_and_not_a_flip(session):
    _history(session, "lost", {0: 100, 1: 110, 7: 120})
    _action(
        session,
        "lost",
        status="executed",
        dry_run=False,
        payload={
            "expectation": {"p": 0.5, "kind": "kickbase"},
            "outcome": {"result": "lost", "at": EVENING.isoformat()},
        },
    )

    out = evaluate(session, now=EVENING)

    assert out["coverage"]["shadow_total"] == 1
    assert out["coverage"]["executed_total"] == 0
    assert out["flips"] == []


def test_later_cancelled_bid_is_shadow_and_not_a_flip(session):
    _history(session, "cancelled", {0: 100, 1: 110, 7: 120})
    _action(
        session,
        "cancelled",
        status="executed",
        dry_run=False,
        payload={"offer_id": "auction-a"},
    )
    _action(
        session,
        "cancelled",
        status="executed",
        dry_run=False,
        action_type="cancel_bid",
        created_at=EVENING + timedelta(minutes=5),
        payload={"offer_id": "auction-a"},
    )

    out = evaluate(session, now=EVENING)

    assert out["coverage"]["shadow_total"] == 1
    assert out["coverage"]["executed_total"] == 0
    assert out["flips"] == []


def test_cancel_for_another_auction_of_same_player_does_not_close_bid(session):
    _history(session, "repeat", {0: 100, 1: 110, 7: 120})
    _action(session, "repeat", status="executed", dry_run=False,
            payload={"offer_id": "auction-a"})
    _action(session, "repeat", status="executed", dry_run=False,
            action_type="cancel_bid", created_at=EVENING + timedelta(days=2),
            payload={"offer_id": "auction-b"})

    out = evaluate(session, now=EVENING)

    assert out["coverage"]["open_total"] == 1
    assert out["coverage"]["shadow_total"] == 0


def test_dry_run_cancel_does_not_close_a_real_bid(session):
    _history(session, "dry-cancel", {0: 100, 1: 110, 7: 120})
    _action(session, "dry-cancel", status="executed", dry_run=False,
            payload={"offer_id": "auction-a"})
    _action(session, "dry-cancel", status="executed", dry_run=True,
            action_type="cancel_bid", created_at=EVENING + timedelta(minutes=5),
            payload={"offer_id": "auction-a"})

    out = evaluate(session, now=EVENING)

    assert out["coverage"]["open_total"] == 1
    assert out["coverage"]["shadow_total"] == 0


def test_cancel_from_another_league_does_not_close_bid(session):
    _history(session, "cross-league", {0: 100, 1: 110, 7: 120})
    _action(session, "cross-league", status="executed", dry_run=False,
            payload={"offer_id": "auction-a"}, league_id="L1")
    _action(session, "cross-league", status="executed", dry_run=False,
            action_type="cancel_bid", created_at=EVENING + timedelta(minutes=5),
            payload={"offer_id": "auction-a"}, league_id="L2")

    out = evaluate(session, now=EVENING)

    assert out["coverage"]["open_total"] == 1
    assert out["coverage"]["shadow_total"] == 0


def test_exact_cancel_overrides_a_contradictory_won_marker(session):
    _history(session, "contradiction", {0: 100, 1: 110, 7: 120})
    _action(session, "contradiction", status="executed", dry_run=False,
            payload=_won_payload() | {"offer_id": "auction-a"})
    _action(session, "contradiction", status="executed", dry_run=False,
            action_type="cancel_bid", created_at=EVENING + timedelta(minutes=5),
            payload={"offer_id": "auction-a"})

    out = evaluate(session, now=EVENING)

    assert out["coverage"]["shadow_total"] == 1
    assert out["coverage"]["executed_total"] == 0
    assert out["flips"] == []
