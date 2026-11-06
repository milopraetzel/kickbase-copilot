"""Tausch-Finanzierung im Kaufbeleg (Planungs-Dispo, Emil 22./23.09.2026).

Ein Engine-Kaufbeleg darf mehr binden als das Konto, WENN er an einen gekoppelten
Sofortverkauf eines ungeschützten eigenen Spielers gebunden ist, netto aus echtem Geld
gedeckt bleibt, die Kickbase-33-%-Grenze hält und die Frist (05.10.2026) nicht vorbei
ist. Alle Beträge sind interne Planbelege (keine API-Fixtures).
"""

from datetime import UTC, datetime, timedelta

import pytest

import kickbase_agent.decision.purchase_review as pr
from kickbase_agent.decision.overdraft import PLANNING_OVERDRAFT_UNTIL
from kickbase_agent.decision.purchase_review import (
    purchase_review_veto,
    team_funding_veto,
    team_selection_veto,
)

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
#: 05.10.2026 00:00 Berlin — ab hier gilt kein Dispo mehr.
FRIST = PLANNING_OVERDRAFT_UNTIL.astimezone(UTC)
T = "t-target"
S = "s-sold"
OLISE = "8329"


@pytest.fixture(autouse=True)
def _olise_geschuetzt(monkeypatch):
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", OLISE)


def swap_review(*, now=NOW, budget=3_000_000, amount=10_000_000, sold=S,
                sold_mv=8_000_000, team_value=60_000_000, swap=True, **overrides):
    """Tausch auf Dispo: Kauf 10 M, Verkauf 8 M × 0,90 = 7,2 M, netto 2,8 M ≤ 3 M Konto."""
    proceeds = sold_mv * 90 // 100
    plan = {
        "plan_id": "plan-swap", "observed_at": now.isoformat(), "budget_eur": budget,
        "open_bid_exposure_eur": 0, "reserved_cash_eur": amount,
        "owned_player_ids": sorted({S, OLISE, "o-2", "o-3"}), "open_bids": [],
        "baseline_filled_slots": 11,
        "purchases": [{"player_id": T, "amount_eur": amount, "max_bid_eur": amount + 500_000}],
    }
    review = {
        "schema_version": 1, "player_id": T, "selected": True,
        "max_bid_eur": amount + 500_000, "market_value_eur": amount,
        "observed_at": now.isoformat(), "valid_until": (now + timedelta(hours=2)).isoformat(),
        "purpose": "lineup_upgrade", "reasons": ["Besserer Starter per Tausch."],
        "engine_version": "team-milp-v1", "plan_id": "plan-swap", "team_plan": plan,
        "bid_scenarios": [
            {"amount": amount, "win_probability": None, "selected": True},
            {"amount": amount + 500_000, "win_probability": None, "selected": False},
        ],
    }
    if swap:
        sale = {"player_id": sold, "market_value_eur": sold_mv, "proceeds_eur": proceeds}
        plan["swap_sales"] = [{**sale, "for_player_id": T}]
        plan["team_value_eur"] = team_value
        review["swap_sale"] = sale
    review.update(overrides)
    return review


def veto(review, *, now=NOW, amount=10_000_000):
    return purchase_review_veto(review, player_id=T, amount=amount, now=now)


def fresh(review, *, budget=3_000_000, bids=(), owned=(S, OLISE, "o-2", "o-3"),
          amount=10_000_000, now=NOW):
    return team_funding_veto(review, player_id=T, amount=amount, budget=budget,
                             open_bids=list(bids), owned_player_ids=set(owned), now=now)


# ------------------------------------------------------------ Grundfall


def test_gekoppelter_tausch_auf_dispo_ist_erlaubt():
    review = swap_review()
    assert veto(review) is None
    assert fresh(review) is None


def test_derselbe_kauf_ohne_kopplung_bleibt_ein_veto():
    review = swap_review(swap=False)
    assert "nicht finanziert" in veto(review)
    assert "nicht finanziert" in fresh(review)


def test_kopplung_an_einen_geschuetzten_spieler_ist_ein_veto():
    review = swap_review(sold=OLISE)
    assert "geschützt" in veto(review)
    assert "geschützt" in fresh(review)


def test_netto_nicht_gedeckt_ist_ein_veto():
    # Verkauf 5 M × 0,90 = 4,5 M; netto 5,5 M > 3 M Konto.
    review = swap_review(sold_mv=5_000_000)
    assert "netto nicht gedeckt" in veto(review)
    assert "netto nicht gedeckt" in fresh(review)
    # Ein Euro Deckung mehr im frischen Konto reicht genau.
    assert fresh(review, budget=5_500_000) is None
    assert "netto nicht gedeckt" in fresh(review, budget=5_499_999)


def test_33_prozent_grenze_gerissen_ist_ein_veto():
    # Teamwert 18 M → Grenze −6 M; Konto nach allen Zuschlägen 3 M − 10 M = −7 M.
    review = swap_review(team_value=18_000_000)
    assert "33-%" in veto(review)
    assert "33-%" in fresh(review)
    # Teamwert 21 M → Grenze −7 M: genau auf der Grenze erlaubt.
    assert veto(swap_review(team_value=21_000_000)) is None


def test_fehlender_teamwert_ist_ein_veto():
    review = swap_review()
    del review["team_plan"]["team_value_eur"]
    assert "Teamwert" in veto(review)
    assert "Teamwert" in fresh(review)


def test_nach_dem_5_oktober_ist_der_dispo_vorbei():
    kurz_davor = FRIST - timedelta(seconds=1)
    assert veto(swap_review(now=kurz_davor), now=kurz_davor) is None
    review = swap_review(now=FRIST)
    assert "Planungs-Dispo abgelaufen" in veto(review, now=FRIST)
    assert "Planungs-Dispo abgelaufen" in fresh(review, now=FRIST)


def test_aus_echtem_geld_gedeckter_tausch_gilt_auch_nach_der_frist():
    """Ohne Dispo-Bedarf ist der Tausch ein normaler Kauf — die Frist sperrt nur Dispo."""
    review = swap_review(now=FRIST, budget=10_000_000)
    assert veto(review, now=FRIST) is None
    assert fresh(review, budget=10_000_000, now=FRIST) is None


def test_verkaufsspieler_nicht_mehr_im_frischen_kader_ist_ein_veto():
    assert "nicht mehr im eigenen Kader" in fresh(swap_review(), owned=(OLISE, "o-2"))


def test_negatives_konto_bleibt_ein_veto():
    review = swap_review(budget=-1)
    assert veto(review) is not None
    assert fresh(review, budget=-1) is not None


def test_ohne_frische_zeit_nimmt_der_executorpfad_die_uhr(monkeypatch):
    """Der Executor ruft team_funding_veto ohne `now` auf (versiegelte Datei).

    Die Uhr steht in beiden Hälften fest: Die erste las bis 05.10.2026 die echte Uhr und wurde
    mit dem Ende der Dispo-Frist rot (Zeitbombe, gefunden 05.10.2026)."""

    class _Uhr(datetime):
        jetzt = FRIST - timedelta(hours=1)

        @classmethod
        def now(cls, tz=None):
            return cls.jetzt if tz is not None else cls.jetzt.replace(tzinfo=None)

    monkeypatch.setattr(pr, "datetime", _Uhr)
    review = swap_review()
    assert team_funding_veto(review, player_id=T, amount=10_000_000, budget=3_000_000,
                             open_bids=[], owned_player_ids={S, OLISE}) is None
    _Uhr.jetzt = FRIST
    assert "abgelaufen" in team_funding_veto(
        review, player_id=T, amount=10_000_000, budget=3_000_000,
        open_bids=[], owned_player_ids={S, OLISE},
    )


# ------------------------------------------------------------ Belegtreue


@pytest.mark.parametrize("change", [
    lambda r: r.pop("swap_sale"),
    lambda r: r["swap_sale"].update(player_id="o-2"),
    lambda r: r["swap_sale"].update(proceeds_eur=7_200_001),
    lambda r: r["team_plan"]["swap_sales"][0].update(proceeds_eur=7_300_000),
    # Plan und Kaufbeleg einig, aber mehr als 95 % des Marktwerts angerechnet.
    lambda r: (r["swap_sale"].update(proceeds_eur=7_600_001),
               r["team_plan"]["swap_sales"][0].update(proceeds_eur=7_600_001)),
    lambda r: r["team_plan"]["swap_sales"][0].update(for_player_id="elsewhere"),
    lambda r: r["team_plan"]["swap_sales"][0].update(player_id=T),
    lambda r: r["team_plan"].update(swap_sales=[{"player_id": S}]),
    lambda r: r["team_plan"].update(swap_sales="kaputt"),
])
def test_widerspruechliche_tauschbelege_sind_ein_veto(change):
    review = swap_review()
    change(review)
    assert "Tausch" in veto(review)


def test_ein_verkaufsspieler_deckt_nur_einen_kauf():
    review = swap_review()
    plan = review["team_plan"]
    plan["purchases"].append({"player_id": "t-2", "amount_eur": 1, "max_bid_eur": 1})
    plan["reserved_cash_eur"] += 1
    plan["swap_sales"].append({**plan["swap_sales"][0], "for_player_id": "t-2"})
    assert "Tausch" in veto(review)


def test_anrechnung_nie_hoeher_als_der_gekoppelte_kauf():
    """Verkauf 20 M deckt nur den eigenen 10-M-Kauf, nicht den Rest des Pakets."""
    review = swap_review(sold_mv=20_000_000, budget=1_000_000)
    plan = review["team_plan"]
    plan["purchases"].append({"player_id": "other", "amount_eur": 1_500_000,
                              "max_bid_eur": 1_500_000})
    plan["reserved_cash_eur"] += 1_500_000
    assert "netto nicht gedeckt" in veto(review)
    plan["purchases"][-1].update(amount_eur=1_000_000, max_bid_eur=1_000_000)
    plan["reserved_cash_eur"] -= 500_000
    assert veto(review) is None


def test_gemeinsame_anpassungen_rechnen_den_tausch_mit():
    review = swap_review()
    assert team_selection_veto({T: review}, {T: 10_000_000}, now=NOW) is None
    # +10 % sprengt netto: 11 M − 7,2 M = 3,8 M > 3 M.
    assert team_selection_veto({T: review}, {T: 11_000_000}, now=NOW) is not None
    assert team_selection_veto({T: review}, {T: 10_000_000}, now=FRIST) is not None


def test_belege_ohne_tausch_verhalten_sich_wie_bisher():
    """Unveränderter Altvertrag: gedeckte Belege frei, ungedeckte mit altem Text."""
    review = swap_review(swap=False, budget=10_000_000)
    assert veto(review) is None and fresh(review, budget=10_000_000) is None
    review = swap_review(swap=False)
    assert veto(review) == (
        "Mannschaftsplan nicht finanziert — alle Zuschläge überschreiten das Konto."
    )
    assert fresh(review) == (
        "Mannschaftsplan nicht finanziert: 10000000 € für alle Verpflichtungen "
        "bei 3000000 € Konto; keine geplanten Verkaufserlöse eingerechnet."
    )
    assert team_selection_veto({T: review}, {T: 10_000_000}) == (
        "Mannschaftsplan: gemeinsame Gebotsanpassungen überschreiten das belegte Konto."
    )
