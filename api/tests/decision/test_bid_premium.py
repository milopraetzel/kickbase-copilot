"""Gebotsregel V3 (Emil 25.09.2026, docs/ANALYSE-GEBOTSPREIS-2026-09-25.md).

Aufschlag = kleinerer Wert aus Zuschlagskurve (80 % vergleichbarer Liga-Siegerpreise)
und erwartetem 3-Tage-Anstieg + 3 Prozentpunkte, höchstens 15 %; nach fallendem
Tagesschritt nur 0,5 %. Erwartete Werte unabhängig nachgerechnet (25.09.2026).
"""

import pytest

from kickbase_agent.decision.bid_premium import (
    BID_PREMIUM_VERSION,
    PREMIUM_CAP,
    bid_premium,
    last_step,
)


def test_last_step_is_the_change_since_yesterday_relative_to_yesterday():
    # tfhmvt = MW(heute) - MW(gestern): Schwäbe 25.09.2026, gegen player_history belegt.
    assert last_step(12_775_546, -445_772, True) == pytest.approx(-445_772 / 13_221_318)


@pytest.mark.parametrize("observed", [None, False])
def test_unobserved_change_is_no_trend(observed):
    assert last_step(8_000_000, 0, observed) is None
    assert bid_premium(8_000_000, 0, observed) is None


@pytest.mark.parametrize("market_value,change", [(0, 0), (-1, 0), (1_000_000, 1_000_000)])
def test_impossible_previous_value_is_no_trend(market_value, change):
    assert bid_premium(market_value, change, True) is None


def test_rising_player_is_capped_by_the_three_day_forecast():
    premium = bid_premium(8_160_000, 160_000, True)  # +2 % seit gestern

    assert premium.rule == "prognose"
    assert premium.premium == pytest.approx(0.0806)
    assert premium.win_curve == pytest.approx(0.208216, abs=1e-6)
    assert premium.amount == 8_817_696


def test_expensive_player_is_capped_by_the_win_curve():
    premium = bid_premium(52_000_000, 2_000_000, True)  # +4 %, großer Marktwert

    assert premium.rule == "kurve"
    assert premium.premium == pytest.approx(0.089942, abs=1e-6)
    assert premium.growth_margin == pytest.approx(0.1352)
    assert premium.amount == 56_676_989


def test_strong_riser_never_exceeds_fifteen_percent():
    premium = bid_premium(10_800_000, 800_000, True)  # +8 %

    assert premium.rule == "deckel"
    assert premium.premium == PREMIUM_CAP == 0.15
    assert premium.amount == 12_420_000


def test_falling_player_gets_half_a_percent():
    premium = bid_premium(9_900_000, -100_000, True)  # -1 %

    assert premium.rule == "faller"
    assert premium.premium == 0.005
    assert premium.amount == 9_949_500
    assert premium.win_curve is None and premium.growth_margin is None


def test_expensive_near_flat_player_never_bids_exactly_the_market_value():
    # Review 25.09.2026: 60 M bei -0,33 % ergab 0 % (MW + 0 gewinnt nie), ein Faller
    # bei -0,66 % dagegen +0,5 %. Der Faller-Aufschlag ist die Untergrenze für alle.
    premium = bid_premium(60_000_000, -198_656, True)

    assert premium.step == pytest.approx(-0.0033, abs=1e-6)
    assert premium.win_curve < 0
    assert premium.rule == "boden"
    assert premium.premium == 0.005
    assert premium.amount == 60_300_000


def test_flat_player_gets_the_forecast_margin():
    premium = bid_premium(5_000_000, 0, True)

    assert premium.rule == "prognose"
    assert premium.premium == pytest.approx(0.026)
    assert premium.amount == 5_130_000


def test_extreme_step_is_clipped_before_the_formulas():
    premium = bid_premium(1_500_000, 500_000, True)  # +50 %, begrenzt auf +20 %

    assert premium.step == pytest.approx(0.5)
    assert premium.growth_margin == pytest.approx(-0.004 + 2.73 * 0.20 + 0.03)
    assert premium.premium == PREMIUM_CAP
    assert premium.amount == 1_725_000


def test_audit_record_names_version_and_parts():
    premium = bid_premium(13_517_003, 192_130, True)  # Fernández 25.09.2026

    record = premium.as_dict()
    assert record["version"] == BID_PREMIUM_VERSION
    assert record["amount_eur"] == premium.amount == 14_400_523
    assert record["rule"] == "prognose"
    assert record["premium_pct"] == pytest.approx(6.536, abs=1e-3)
    assert record["step_pct"] == pytest.approx(1.442, abs=1e-3)
    assert record["win_curve_pct"] == pytest.approx(15.175, abs=1e-3)
    text = premium.describe()
    assert "V3" in text and "+6,5 %" in text and "+1,4 %" in text


def test_conviction_bonus_lifts_forecast_and_cap_up_to_twenty_percent():
    from kickbase_agent.decision.bid_premium import CONVICTION_BONUS, CONVICTION_CAP

    base = bid_premium(8_160_000, 160_000, True)
    boosted = bid_premium(
        8_160_000, 160_000, True, conviction_bonus=CONVICTION_BONUS, cap=CONVICTION_CAP
    )

    assert boosted.rule == "prognose+signal"
    assert boosted.premium == pytest.approx(base.premium + CONVICTION_BONUS)
    assert "Conviction" in boosted.describe()

    strong = bid_premium(
        10_800_000, 800_000, True, conviction_bonus=CONVICTION_BONUS, cap=CONVICTION_CAP
    )
    assert strong.rule == "deckel+signal"
    assert strong.premium == CONVICTION_CAP == 0.20
    assert strong.amount == 12_960_000

