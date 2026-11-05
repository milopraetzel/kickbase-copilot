"""Paket 1 der Turnaround-Studie (19.09.2026) am echten build_plan-Aufruf.

P0-1 Erwerbsrealismus: Manager-Listings sind kein autonomes Ziel und kein Platz.
P0-2 Jeder Kaufbeleg trägt seine eigene Listingart und den Verkäufer.
P0-3 Planungsgeld = Konto − Gebote − 10.000 €; Einzelrahmen 1,0 bei Elf-Lücke.
P0-5 Ab 5 M nur mit Rollenbeleg (KB-Stufe ≤ 2, ≥ 60 min in beiden letzten Spieltagen).

Reine Domain-Testdaten über den vorhandenen FakePlanClient; keine API-Fixtures.
"""

from datetime import UTC, datetime, timedelta

import pytest

from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.decision.kickoff_freeze import KICKOFF_MIN_BALANCE
from kickbase_agent.kickbase.models import MatchdayPerformance
from kickbase_agent.worker.kickoff_guard import select_bids_to_cancel

from .test_plan import _bid
from .test_plan_roster_value import _forecast, _prepare, _replacement

# Zehn Spieler, ein Abwehrplatz fehlt: TW 1 · ABW 3 · MF 5 · ST 1 → mit ABW 4-5-1.
TEN = [1, 2, 2, 2, 3, 3, 3, 3, 3, 4]
# Volle Elf in 4-5-1: jeder Kauf ist eine Verbesserung, keine Lückenfüllung.
ELEVEN = [1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4]


def _manager(mv: int, pid: str, seller: str = "rival-7"):
    return _replacement(mv=mv, pid=pid).model_copy(update={"seller_id": seller})


def _played(*minutes: int, first_matchday: int = 1) -> list[MatchdayPerformance]:
    """Gespielte Spieltage, älteste zuerst — in der Form, die `load_performance`
    in Produktion liefert: Minuten > 0, `is_finished` und `minutes_observed` leer.
    `MatchdayPerformance.from_api` setzt `is_finished` nie; in der DB sind beide
    Spalten in allen 22.721 Zeilen NULL (nachgezählt 21.09.2026). Von Hand auf
    True gesetzte Flags hatten verdeckt, dass der Beleg nie bestehen konnte."""
    now = datetime.now(UTC)
    return [
        MatchdayPerformance(
            season="2026/2027",
            matchday=first_matchday + index,
            match_date=now - timedelta(days=7 * (len(minutes) - index)),
            points=50,
            minutes=played,
        )
        for index, played in enumerate(minutes)
    ]


def _with_performance(monkeypatch, rows: dict[str, list[MatchdayPerformance]]) -> None:
    monkeypatch.setattr(plan_mod, "load_performance", lambda *_: rows)


# ------------------------------------------------------------------ P0-1


async def test_manager_listing_is_never_an_autonomous_target(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=1_000_000, pid="kb"), _manager(900_000, "mgr")]

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["kb"]
    review = plan.purchase_reviews["mgr"]
    assert review["eligible"] is False and review["selected"] is False
    assert "Manager-Angebot" in review["reasons"][0]


async def test_live_manager_bid_reserves_cash_but_counts_no_slot(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_manager(2_000_000, "mgr")]
    client.own_bids = [_bid("mgr", "replacement-club", 2_000_000)]

    plan = await plan_mod.build_plan()

    assert plan.team_plan["filled_slots"] == plan.team_plan["owned_filled_slots"] == 10
    assert plan.team_plan["open_bid_exposure_eur"] == 2_000_000
    assert plan.plan_base == 8_000_000
    assert plan.buys == []


async def test_blocked_target_is_excluded_before_the_optimizer(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=1_000_000, pid="kb1"), _replacement(mv=1_200_000, pid="kb2")]

    plan = await plan_mod.build_plan(blocked_target_ids=frozenset({"kb1"}))

    assert [p.action.player_id for p in plan.buys] == ["kb2"]
    assert "kb1" not in plan.team_plan["purchase_ids"]
    assert "gesperrt" in plan.purchase_reviews["kb1"]["reasons"][0]


async def test_standing_kickbase_bid_keeps_its_slot_even_when_blocked_today(monkeypatch):
    """Ein stehendes Kickbase-Gebot ist Wirklichkeit: es bleibt gebundene Option.
    Würde die Sperre es ausschließen, plante die Engine einen Doppelkauf."""
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=1_000_000, pid="kb"), _replacement(mv=1_100_000, pid="other")]
    client.own_bids = [_bid("kb", "replacement-club", 1_000_000)]

    plan = await plan_mod.build_plan(blocked_target_ids=frozenset({"kb"}))

    assert plan.team_plan["filled_slots"] == 11
    assert plan.buys == []


# ------------------------------------------------------------------ P0-2


async def test_every_review_carries_its_own_listing_type_and_seller(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=1_000_000, pid="kb"), _manager(900_000, "mgr")]

    plan = await plan_mod.build_plan()

    selected, rejected = plan.purchase_reviews["kb"], plan.purchase_reviews["mgr"]
    assert (selected["listing_type"], selected["seller_id"]) == ("kickbase", "")
    assert (rejected["listing_type"], rejected["seller_id"]) == ("manager", "rival-7")


# ------------------------------------------------------------------ P0-3


async def test_gap_uses_the_full_cash_frame(monkeypatch):
    """Remberg-Fall: Kader 10, 8,0 M frei, Ziel 7,0 M als Kickbase-Listing vor der
    Frist. Der 0,70-Rahmen lehnte das 47 von 47 Mal ab."""
    client = await _prepare(monkeypatch, positions=TEN, cash=8_000_000)
    client.market = [_replacement(mv=7_000_000, pid="kb")]
    _with_performance(monkeypatch, {"kb": _played(90, 90)})

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["kb"]


# Emils Entscheidung 21.09.2026: Der Einzelrahmen staffelt nach offenen Plätzen. Das
# Review hatte gezeigt: Kader 8, 8,0 M frei, ein Listing zu 7,9 M → Kauf, 90.000 € Rest,
# zwei Plätze bleiben offen.
EIGHT = [1, 2, 2, 2, 3, 3, 3, 4]
NINE = [1, 2, 2, 2, 3, 3, 3, 3, 4]


@pytest.mark.parametrize(
    "positions,price,bought,fraction",
    [
        (EIGHT, 3_700_000, False, 0.45),  # 0,45 × 7.990.000 = 3.595.500
        (EIGHT, 3_500_000, True, 0.45),
        (NINE, 4_900_000, False, 0.60),  # 0,60 × 7.990.000 = 4.794.000
        (NINE, 4_700_000, True, 0.60),
        (TEN, 4_900_000, True, 1.0),  # ein offener Platz: das ganze gedeckte Geld
    ],
)
async def test_single_bid_frame_is_staggered_by_open_slots(
    monkeypatch, positions, price, bought, fraction
):
    client = await _prepare(monkeypatch, positions=positions, cash=8_000_000)
    client.market = [_replacement(mv=price, pid="kb")]

    plan = await plan_mod.build_plan()

    assert ([p.action.player_id for p in plan.buys] == ["kb"]) is bought
    assert plan.team_plan["technical_limits"]["single_bid_budget_fraction"] == fraction
    if not bought:
        assert "Einzelrahmen" in plan.purchase_reviews["kb"]["reasons"][0]


async def test_staggered_frame_leaves_money_for_the_other_open_slots(monkeypatch):
    """Drei offene Plätze, 8,0 M frei: drei bezahlbare Spieler statt eines teuren."""
    client = await _prepare(monkeypatch, positions=EIGHT, cash=8_000_000)
    teuer = _replacement(mv=4_900_000, pid="teuer")
    client.market = [
        teuer,
        _replacement(mv=2_000_000, pid="abw"),
        _replacement(mv=2_000_000, pid="mf").model_copy(update={
            "player": _replacement(mv=2_000_000, pid="mf").player.model_copy(
                update={"position": 3, "team_id": "club-mf"})}),
        _replacement(mv=2_000_000, pid="st").model_copy(update={
            "player": _replacement(mv=2_000_000, pid="st").player.model_copy(
                update={"position": 4, "team_id": "club-st"})}),
    ]

    plan = await plan_mod.build_plan()

    assert "teuer" not in [p.action.player_id for p in plan.buys]
    assert plan.team_plan["filled_slots"] == 11


async def test_cash_equal_to_price_buys_nothing_because_of_the_kickoff_buffer(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN, cash=1_000_000)
    client.market = [_replacement(mv=1_000_000, pid="kb")]

    plan = await plan_mod.build_plan()

    assert plan.buys == []


async def test_small_positive_balance_below_the_buffer_is_explained_not_called_a_deficit(
    monkeypatch,
):
    """Review-Befund 21.09.: Bei +7.000 € Konto stand im Plan nur „fehlen 3.000 €
    Deckung" — das liest sich wie ein Minus. Der Plan muss sagen, dass das Konto im
    Plus ist und nur der Anpfiff-Puffer keinen Kauf zulässt."""
    client = await _prepare(monkeypatch, positions=TEN, cash=7_000)
    client.market = [_replacement(mv=1_000_000, pid="kb")]

    plan = await plan_mod.build_plan()

    assert plan.buys == []
    text = " ".join(plan.curation_notes)
    assert "Anpfiff-Puffer" in text and "7,000" in text


async def test_every_bid_scenario_leaves_the_kickoff_buffer(monkeypatch):
    cash = 1_350_000
    client = await _prepare(monkeypatch, positions=TEN, cash=cash)
    client.market = [_replacement(mv=1_000_000, pid="kb")]

    plan = await plan_mod.build_plan()

    [proposal] = plan.buys
    scenarios = proposal.action.purchase_review["bid_scenarios"]
    assert scenarios and all(cash - s["amount"] >= KICKOFF_MIN_BALANCE for s in scenarios)
    # Gegenprobe mit dem echten T-120-Wächter: kein Szenario würde storniert.
    highest = max(s["amount"] for s in scenarios)
    assert select_bids_to_cancel([{**_bid("kb", "c", highest)}], cash) == []


async def test_upgrade_keeps_the_seventy_percent_frame(monkeypatch):
    client = await _prepare(monkeypatch, positions=ELEVEN, cash=8_000_000, replacement_score=0.95)
    client.market = [_replacement(mv=7_000_000, pid="replacement")]
    _with_performance(monkeypatch, {"replacement": _played(90, 90)})

    plan = await plan_mod.build_plan()

    assert plan.buys == []
    assert "Einzelrahmen" in plan.purchase_reviews["replacement"]["reasons"][0]


async def test_technical_limits_expose_factor_and_buffer(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=1_000_000, pid="kb")]

    limits = (await plan_mod.build_plan()).team_plan["technical_limits"]

    assert limits["single_bid_budget_fraction"] == 1.0
    assert limits["kickoff_buffer_eur"] == KICKOFF_MIN_BALANCE


# ------------------------------------------------------------------ P0-5


@pytest.mark.parametrize(
    "rows,bought,reason",
    [
        ({"kb": _played(95, 94)}, True, None),
        ({"kb": _played(31, 75)}, False, "60 Minuten"),
        ({}, False, "Rollenbeleg fehlt"),
        # Alte Einsätze zählen nicht: die Liga ist bei Spieltag 3/4, er hat nur 1/2.
        ({"kb": _played(90, 90), "roster-0": _played(90, 90, first_matchday=3)},
         False, "Rollenbeleg fehlt"),
    ],
)
async def test_target_from_five_million_needs_a_minutes_proof(monkeypatch, rows, bought, reason):
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=6_000_000, pid="kb")]
    _with_performance(monkeypatch, rows)

    plan = await plan_mod.build_plan()

    assert ([p.action.player_id for p in plan.buys] == ["kb"]) is bought
    if reason:
        assert reason in plan.purchase_reviews["kb"]["reasons"][0]


async def test_future_rows_never_count_as_observed_matchdays(monkeypatch):
    """Eine Zukunftszeile darf weder Beleg sein noch den Bezugsspieltag verschieben."""
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=6_000_000, pid="kb")]
    future = _played(90, first_matchday=9)[0].model_copy(
        update={"match_date": datetime.now(UTC) + timedelta(days=5)}
    )
    _with_performance(monkeypatch, {"kb": [*_played(90, 90), future]})

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["kb"]


async def test_standing_bid_from_five_million_keeps_its_slot_without_a_role_proof(monkeypatch):
    """Review-Befund 21.09.: Der Rollenbeleg entscheidet über NEUE Ziele. Ein Gebot, das
    schon steht (auch aus der App), ist Wirklichkeit — verlöre es seinen Platz, kaufte
    der Plan denselben Platz ein zweites Mal."""
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=6_000_000, pid="kb"), _replacement(mv=1_000_000, pid="fill")]
    client.own_bids = [_bid("kb", "replacement-club", 6_000_000)]
    _with_performance(monkeypatch, {})
    # Das Gebot gilt dem klar stärkeren Spieler — sonst zöge die bestehende Regel
    # „günstigere gleichwertige Alternative" den Füller vor (kein Rollenbeleg-Thema).
    _forecast(monkeypatch, {"kb": 0.9, "fill": 0.2})

    plan = await plan_mod.build_plan()

    assert "Rollenbeleg" not in " ".join(plan.purchase_reviews["kb"]["reasons"])
    assert plan.purchase_reviews["kb"]["selected"] is True
    assert plan.buys == []
    assert plan.team_plan["filled_slots"] == 11
    assert plan.team_plan["reserved_cash_eur"] == 6_000_000


async def test_open_race_is_rejected_from_five_million(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN)
    target = _replacement(mv=6_000_000, pid="kb")
    client.market = [
        target.model_copy(update={"player": target.player.model_copy(update={"lineup_prob": 3})})
    ]
    _with_performance(monkeypatch, {"kb": _played(90, 90)})

    plan = await plan_mod.build_plan()

    assert plan.buys == []
    assert "Startelfstufe" in plan.purchase_reviews["kb"]["reasons"][0]


async def test_fillers_below_five_million_need_no_minutes_proof(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=4_999_999, pid="kb")]

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["kb"]


async def test_starter_who_missed_one_matchday_but_played_seventy_minutes_is_eligible(
    monkeypatch,
):
    """Pfad 2 (Hebel A, Romulo/Schick/Andrich): Fehlt einer der beiden jüngsten Spieltage
    (Sperre/Rotation/Pause), genügt >= 70 min am anderen Spieltag bei KB-1 + LI-STARTER."""
    client = await _prepare(monkeypatch, positions=TEN)
    client.market = [_replacement(mv=6_000_000, pid="kb")]
    _with_performance(
        monkeypatch,
        {
            "roster-0": _played(90, 90, first_matchday=1),
            "kb": _played(81, first_matchday=2),
        },
    )

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["kb"]


async def test_comeback_signal_unlocks_zero_minute_starter_with_prior_and_conviction_bonus(
    monkeypatch,
):
    """Pfad 3 (Hebel A/B/D, Couto/Goretzka): KB-Stufe 1 + LI STARTER + News-Rückkehrer
    erhält ohne Saisonminuten den Starter-Prior (75 / 300 Punkte) und den +5-%-Bonus."""
    from kickbase_agent.intel.forecasting.service import forecast_player

    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    target = _replacement(mv=6_000_000, pid="kb")
    target = target.model_copy(
        update={
            "player": target.player.model_copy(
                update={"mv_change_24h": 120_000, "mv_change_24h_observed": True}
            )
        }
    )
    client.market = [target]
    _with_performance(
        monkeypatch,
        {
            "roster-0": _played(90, 90, first_matchday=1),
            "kb": [],
        },
    )
    monkeypatch.setattr(plan_mod, "forecast_player", forecast_player)
    monkeypatch.setattr(plan_mod, "load_recent_articles", lambda *_: ["artikel"])

    async def fake_roles(*_args, **_kwargs):
        return {}

    async def fake_returns(*_args, **_kwargs):
        return {"kb": object()}

    monkeypatch.setattr(plan_mod, "judge_roles", fake_roles)
    monkeypatch.setattr(plan_mod, "judge_returns", fake_returns)

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["kb"]
    assert plan.projections["kb"]["expected_points"] >= 75.0
    assert plan.projections["kb"]["horizon_points"] >= 300.0
    assert plan.purchase_reviews["kb"]["bid_premium"]["rule"] == "prognose+signal"


async def test_sub_five_million_joker_is_rejected_for_lineup_upgrade_but_allowed_for_gap(
    monkeypatch,
):
    """Hebel A: Unter 5 M kein Elf-Upgrade mit reinen Kurzeinsätzen (< 45 min an beiden
    beobachteten Spieltagen); bei Elf-Lücke bleibt jeder Füller frei."""
    client = await _prepare(monkeypatch, positions=ELEVEN, cash=10_000_000, replacement_score=0.95)
    client.market = [_replacement(mv=4_000_000, pid="replacement")]
    _with_performance(
        monkeypatch,
        {
            "roster-0": _played(90, 90, first_matchday=1),
            "replacement": _played(20, 30, first_matchday=1),
        },
    )

    plan = await plan_mod.build_plan()

    assert plan.buys == [] and plan.swaps == []
    assert "Kurzeinsätzen" in plan.purchase_reviews["replacement"]["reasons"][0]


async def test_sub_five_million_joker_with_zero_minutes_is_also_rejected_for_upgrade(monkeypatch):
    """A8: Unter 5 M darf auch ein Spieler mit (20 min, 0 min) role_proof_gap nicht umgehen."""
    client = await _prepare(monkeypatch, positions=ELEVEN, cash=10_000_000, replacement_score=0.95)
    client.market = [_replacement(mv=4_000_000, pid="joker-0")]
    _with_performance(
        monkeypatch,
        {
            "roster-0": _played(90, 90, first_matchday=1),
            "joker-0": _played(20, 0, first_matchday=1),
        },
    )

    plan = await plan_mod.build_plan()

    assert plan.buys == [] and plan.swaps == []
    assert "Kurzeinsätzen" in plan.purchase_reviews["joker-0"]["reasons"][0]


async def test_daytime_kickbase_auction_hydrates_missing_performance_from_live_api(monkeypatch):
    """Hennes-Behrens-Fall (19.10.2026): Untertags neu gelistete Kickbase-Auktionen haben vor
    dem 22:05-Snapshot 0 DB-Zeilen; build_plan lädt ihre Einsätze live von der API nach."""
    client = await _prepare(monkeypatch, positions=TEN, cash=15_000_000)
    target = _replacement(mv=6_000_000, pid="behrens")
    client.market = [target]

    def fake_db_perf(_session, player_ids):
        return {
            pid: (_played(90, 90, first_matchday=1) if pid != "behrens" else [])
            for pid in player_ids
        }

    monkeypatch.setattr(plan_mod, "load_performance", fake_db_perf)
    monkeypatch.setattr(plan_mod, "_DEFAULT_LOAD_PERFORMANCE", fake_db_perf)

    async def get_player_performance(_league_id, pid):
        return _played(99, 108, first_matchday=1) if pid == "behrens" else []

    client.get_player_performance = get_player_performance

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["behrens"]


async def test_kb3_breakout_starter_with_two_full_matchdays_and_strong_trend_is_eligible(
    monkeypatch,
):
    """Ramon-Hendriks-Fall (19.10.2026): Am Wochenbeginn steht ein echter Stammspieler mit
    2x60+ Minuten (94' und 90') bei Kickbase noch auf Stufe 3, steigt aber bereits >= +150k/Tag."""
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    target = _replacement(mv=6_800_000, pid="hendriks")
    client.market = [
        target.model_copy(
            update={
                "player": target.player.model_copy(
                    update={
                        "lineup_prob": 3,
                        "mv_change_24h": 340_000,
                        "mv_change_24h_observed": True,
                    }
                )
            }
        )
    ]
    _with_performance(
        monkeypatch,
        {
            "roster-0": _played(90, 90, first_matchday=1),
            "hendriks": _played(94, 90, first_matchday=1),
        },
    )

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["hendriks"]


async def test_latest_matchday_breakout_starter_above_five_million_is_eligible(monkeypatch):
    """Pfad 4 (Aktueller Spieltags-Breakout, z. B. Hložek 8,86 M): Am vorletzten Spieltag nur
    33 Minuten, am jüngsten Spieltag aber >= 75 Minuten UND >= 100 Punkte bei KB-Stufe 1/2."""
    now = datetime.now(UTC)
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    target = _replacement(mv=8_860_000, pid="hlozek")
    client.market = [
        target.model_copy(
            update={"player": target.player.model_copy(update={"lineup_prob": 2})}
        )
    ]
    _with_performance(
        monkeypatch,
        {
            "roster-0": _played(90, 90, first_matchday=1),
            "hlozek": [
                MatchdayPerformance(
                    season="2026/2027",
                    matchday=1,
                    match_date=now - timedelta(days=10),
                    points=25,
                    minutes=33,
                ),
                MatchdayPerformance(
                    season="2026/2027",
                    matchday=2,
                    match_date=now - timedelta(days=2),
                    points=138,
                    minutes=80,
                ),
            ],
        },
    )

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["hlozek"]


# ------------------------------------------------------------------ Anti-Lemon Manager-Gate


async def test_manager_listing_top_kb1_starter_at_fair_price_is_selected_without_swap(
    monkeypatch,
):
    """Ein echter KB-1-Stammspieler eines Mitspielers (Preis <= 101,5 % MW, >= +15 Pkt
    Startelf-Gewinn, voller Minutenbeleg, nicht-fallender MW) wird aus freiem Restbudget
    als Direktgebot (max. 101,5 % MW) geplant, ohne vorzeitigen Tausch-Verkauf."""
    from kickbase_agent.decision.purchase_review import purchase_review_veto

    client = await _prepare(monkeypatch, positions=ELEVEN, cash=20_000_000)
    mgr_item = _manager(10_000_000, "ginter", seller="rival-1").model_copy(
        update={"price": 9_950_000}
    )
    client.market = [mgr_item]
    _forecast(monkeypatch, {"ginter": 0.90})  # 360 Pkt vs. 240 Pkt (+120 Pkt >= +15)

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["ginter"]
    assert plan.swaps == []
    [proposal] = plan.buys
    assert proposal.action.amount == 10_000_000
    review = plan.purchase_reviews["ginter"]
    assert review["selected"] is True and review["eligible"] is True
    assert review["listing_type"] == "manager" and review["seller_id"] == "rival-1"
    assert review["max_bid_eur"] <= int(10_000_000 * 1.015)
    assert (
        purchase_review_veto(
            review,
            player_id="ginter",
            amount=proposal.action.amount,
            now=datetime.fromisoformat(review["observed_at"]),
        )
        is None
    )


@pytest.mark.parametrize(
    "update_player,update_market,expected_fragment",
    [
        ({"lineup_prob": 2}, {}, "KB-Stufe-1"),
        ({"lineup_prob": 3}, {}, "KB-Stufe-1"),
        ({"lineup_prob": 5}, {}, "KB-Stufe-1"),
        ({"mv_change_24h": -50_000, "mv_change_24h_observed": True}, {}, "Marktwert fällt"),
        ({"market_value_trend": 2}, {}, "Marktwert fällt"),
        ({}, {"price": 10_500_000}, "101,5 %"),
        (
            {},
            {"expires_at": datetime.now(UTC) - timedelta(hours=40)},
            "älter als 36 Stunden",
        ),
    ],
)
async def test_manager_listing_anti_lemon_filters_reject_bad_or_overpriced_offers(
    monkeypatch, update_player, update_market, expected_fragment
):
    """Kein Kauf von Bank-/Wackelspielern (KB 2–5), fallenden Messern, alten Leichen
    oder Aufpreis-Forderungen (> 101,5 % MW) von Mitspielern."""
    client = await _prepare(monkeypatch, positions=ELEVEN, cash=20_000_000)
    base = _manager(10_000_000, "lemon", seller="rival-1")
    item = base.model_copy(
        update={
            "player": base.player.model_copy(update=update_player),
            **update_market,
        }
    )
    client.market = [item]
    _forecast(monkeypatch, {"lemon": 0.95})

    plan = await plan_mod.build_plan()

    assert plan.buys == [] and plan.swaps == []
    review = plan.purchase_reviews["lemon"]
    assert review["eligible"] is False and review["selected"] is False
    assert "Manager-Angebot" in review["reasons"][0]
    assert expected_fragment in review["reasons"][0]


async def test_manager_listing_never_crowds_out_a_kickbase_auction(monkeypatch):
    """Zero-Crowding-Out (L-108): Reicht das freie Planungsgeld nur für ein Ziel,
    gewinnt IMMER die echte Kickbase-Computer-Auktion vor dem Manager-Listing."""
    client = await _prepare(monkeypatch, positions=ELEVEN, cash=16_000_000)
    kb_item = _replacement(mv=10_000_000, pid="remberg")
    mgr_item = _manager(10_000_000, "katic", seller="rival-2")
    client.market = [kb_item, mgr_item]
    _forecast(monkeypatch, {"remberg": 0.85, "katic": 0.95})

    plan = await plan_mod.build_plan()

    chosen = [p.action.player_id for p in [*plan.buys, *(s.bid for s in plan.swaps)]]
    assert chosen == ["remberg"]
    assert plan.purchase_reviews["katic"]["selected"] is False
    assert "Zero-Crowding-Out" in plan.purchase_reviews["katic"]["reasons"][0]


async def test_manager_listing_from_showcase_seller_is_rejected(monkeypatch):
    """Schaufenster-Filter: Wer > 4 Spieler gleichzeitig auf den Markt stellt,
    fischt nach Optionen/Angeboten und wird abgelehnt."""
    client = await _prepare(monkeypatch, positions=ELEVEN, cash=25_000_000)
    client.market = [
        _manager(10_000_000, f"m{i}", seller="showcase-rival") for i in range(5)
    ]
    _forecast(monkeypatch, {f"m{i}": 0.95 for i in range(5)})

    plan = await plan_mod.build_plan()

    assert plan.buys == []
    assert "Schaufenster" in plan.purchase_reviews["m0"]["reasons"][0]


async def test_manager_listing_sub_5m_kb1_starter_with_full_minutes_is_selected(monkeypatch):
    """Keine pauschale 5-M-Sperre: Ein günstiger KB-1-Stammspieler (2,5 M €) mit
    vollem 60-Minuten-Rollenbeleg wird gekauft; ohne 60-Minuten-Beleg abgelehnt."""
    client = await _prepare(monkeypatch, positions=ELEVEN, cash=4_000_000)
    client.market = [_manager(2_500_000, "juwel", seller="rival-1")]
    _forecast(monkeypatch, {"juwel": 0.95})

    plan = await plan_mod.build_plan()

    assert [p.action.player_id for p in plan.buys] == ["juwel"]
    assert plan.buys[0].action.amount == 2_500_000
    assert plan.purchase_reviews["juwel"]["selected"] is True



