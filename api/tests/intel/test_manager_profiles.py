"""Reale Transfer-/Performance-Auszüge vom 11.09.2026, ohne Netzwerk reproduzierbar.

Quellen: test_strategy.REAL_EXPORT und transfer-archive-2026-09-11/data/
{own-performance,rival-performance,historical-transfers-deduplicated}.json.
Normalisierung wählt belegte Felder aus; Fehlerfälle entfernen/ändern ausdrücklich
Eingaben, ohne erfundene API-Antworten als Beobachtungen auszugeben.
"""

import json
from copy import deepcopy

import pytest

from kickbase_agent.intel.manager_profiles import build_manager_profiles

REAL = json.loads(r'''
{
  "trades": [
    {
      "feed_id": "12329644480",
      "player_id": "8351",
      "name": "Højlund",
      "manager": "emil",
      "buyer": "emil",
      "seller": "",
      "buyer_id": "",
      "seller_id": "",
      "source": "activity_feed",
      "synthetic_id": false,
      "at": "2026-08-20T07:50:23+00:00",
      "amount": 3620000,
      "market_value": 3415594,
      "premium_pct": 5.9845,
      "position": null,
      "bucket": "<5 Mio.",
      "kind": "kb_purchase",
      "update_boundary": false,
      "mw_day": "2026-08-19",
      "mw_source": "api_history",
      "mw_unknown_reason": null
    },
    {
      "feed_id": "12544110575",
      "player_id": "15472",
      "name": "Daka",
      "manager": "emil",
      "buyer": "emil",
      "seller": "",
      "buyer_id": "",
      "seller_id": "",
      "source": "activity_feed",
      "synthetic_id": false,
      "at": "2026-09-03T11:40:02+00:00",
      "amount": 12886667,
      "market_value": 11612576,
      "premium_pct": 10.9716,
      "position": null,
      "bucket": "5–15 Mio.",
      "kind": "kb_purchase",
      "update_boundary": false,
      "mw_day": "2026-09-02",
      "mw_source": "api_history",
      "mw_unknown_reason": null
    },
    {
      "feed_id": "12588175136",
      "player_id": "3081",
      "name": "Vidović",
      "manager": "emil",
      "buyer": "emil",
      "seller": "Osei",
      "buyer_id": "",
      "seller_id": "",
      "source": "activity_feed",
      "synthetic_id": false,
      "at": "2026-09-06T09:04:28+00:00",
      "amount": 8554338,
      "market_value": 8373531,
      "premium_pct": 2.1593,
      "position": 3,
      "bucket": "5–15 Mio.",
      "kind": "manager_deal",
      "update_boundary": false,
      "mw_day": "2026-09-05",
      "mw_source": "api_history",
      "mw_unknown_reason": null
    },
    {
      "feed_id": "12602258984",
      "player_id": "15472",
      "name": "Daka",
      "manager": "emil",
      "buyer": "",
      "seller": "emil",
      "buyer_id": "",
      "seller_id": "",
      "source": "activity_feed",
      "synthetic_id": false,
      "at": "2026-09-07T07:50:19+00:00",
      "amount": 9787642,
      "market_value": 9787642,
      "premium_pct": 0.0,
      "position": null,
      "bucket": "5–15 Mio.",
      "kind": "kb_sale",
      "update_boundary": false,
      "mw_day": "2026-09-06",
      "mw_source": "api_history",
      "mw_unknown_reason": null
    },
    {
      "feed_id": "12602259134",
      "player_id": "14890",
      "name": "Vogt",
      "manager": "emil",
      "buyer": "",
      "seller": "emil",
      "buyer_id": "",
      "seller_id": "",
      "source": "activity_feed",
      "synthetic_id": false,
      "at": "2026-09-07T07:50:22+00:00",
      "amount": 1625348,
      "market_value": 1625348,
      "premium_pct": 0.0,
      "position": null,
      "bucket": "<5 Mio.",
      "kind": "kb_sale",
      "update_boundary": false,
      "mw_day": "2026-09-06",
      "mw_source": "api_history",
      "mw_unknown_reason": null
    },
    {
      "feed_id": "12602259239",
      "player_id": "8351",
      "name": "Højlund",
      "manager": "emil",
      "buyer": "",
      "seller": "emil",
      "buyer_id": "",
      "seller_id": "",
      "source": "activity_feed",
      "synthetic_id": false,
      "at": "2026-09-07T07:50:25+00:00",
      "amount": 4639845,
      "market_value": 4639845,
      "premium_pct": 0.0,
      "position": null,
      "bucket": "<5 Mio.",
      "kind": "kb_sale",
      "update_boundary": false,
      "mw_day": "2026-09-06",
      "mw_source": "api_history",
      "mw_unknown_reason": null
    }
  ],
  "repeated_trades": [
    {
      "player_id": "3120",
      "name": "Bischof",
      "at": "2025-08-11T19:11:45Z",
      "amount": 20888981,
      "buyer_id": "2227102",
      "seller_id": "",
      "buyer": "Niklas B.",
      "seller": "",
      "market_value": null,
      "mw_source": null,
      "update_boundary": false
    },
    {
      "player_id": "3120",
      "name": "Bischof",
      "at": "2025-08-21T10:50:08Z",
      "amount": 17062715,
      "buyer_id": "",
      "seller_id": "2227102",
      "buyer": "",
      "seller": "Niklas B.",
      "market_value": null,
      "mw_source": null,
      "update_boundary": false
    },
    {
      "player_id": "3120",
      "name": "Bischof",
      "at": "2025-10-04T10:32:43Z",
      "amount": 14100000,
      "buyer_id": "2227102",
      "seller_id": "",
      "buyer": "Niklas B.",
      "seller": "",
      "market_value": null,
      "mw_source": null,
      "update_boundary": false
    },
    {
      "player_id": "3120",
      "name": "Bischof",
      "at": "2025-10-16T14:56:29Z",
      "amount": 9643659,
      "buyer_id": "",
      "seller_id": "2227102",
      "buyer": "",
      "seller": "Niklas B.",
      "market_value": null,
      "mw_source": null,
      "update_boundary": false
    }
  ],
  "own_squad": [
    {
      "pid": "4333",
      "name": "Ullrich",
      "mv": 5620487,
      "position": 2,
      "kb_prob": 3
    },
    {
      "pid": "2855",
      "name": "Ache",
      "mv": 13652487,
      "position": 4,
      "kb_prob": 3
    },
    {
      "pid": "1580",
      "name": "Schwäbe",
      "mv": 15844170,
      "position": 1,
      "kb_prob": 1
    },
    {
      "pid": "6668",
      "name": "Minjae",
      "mv": 12913190,
      "position": 2,
      "kb_prob": 2
    },
    {
      "pid": "3081",
      "name": "Vidović",
      "mv": 6706206,
      "position": 3,
      "kb_prob": 1
    },
    {
      "pid": "8329",
      "name": "Olise",
      "mv": 65065938,
      "position": 3,
      "kb_prob": 2
    },
    {
      "pid": "7320",
      "name": "Scheller",
      "mv": 9456614,
      "position": 2,
      "kb_prob": 1
    },
    {
      "pid": "4406",
      "name": "Dina Ebimbe",
      "mv": 4803289,
      "position": 3,
      "kb_prob": 3
    }
  ],
  "performance": {
    "2663242": {
      "u": "2663242",
      "unm": "emil",
      "st": 3,
      "it": [
        {
          "sid": "42",
          "sn": "2026/2027",
          "pl": 0,
          "ap": 275,
          "tp": 827,
          "mdw": 0,
          "it": [
            {
              "day": 1,
              "cur": false,
              "mdp": 664,
              "md": "2026-08-28T18:30:00Z",
              "tw": false
            },
            {
              "day": 2,
              "cur": false,
              "mdp": 359,
              "md": "2026-09-04T18:30:00Z",
              "tw": false
            },
            {
              "day": 3,
              "cur": true,
              "mdp": -196,
              "md": "2026-09-11T18:30:00Z",
              "tw": false
            },
            {
              "day": 4,
              "cur": false,
              "md": "2026-09-18T18:30:00Z",
              "tw": false
            }
          ]
        }
      ]
    },
    "2227102": {
      "u": "2227102",
      "unm": "Niklas B.",
      "st": 3,
      "it": [
        {
          "sid": "34",
          "sn": "2025/2026",
          "pl": 0,
          "ap": 934,
          "tp": 31780,
          "mdw": 3,
          "it": [
            {
              "day": 1,
              "cur": false,
              "mdp": 1064,
              "tw": false
            },
            {
              "day": 2,
              "cur": false,
              "mdp": 837,
              "tw": true
            },
            {
              "day": 3,
              "cur": true,
              "mdp": 1247,
              "tw": false
            },
            {
              "day": 4,
              "cur": false,
              "mdp": 1162,
              "tw": true
            },
            {
              "day": 5,
              "cur": false,
              "mdp": 854,
              "tw": false
            },
            {
              "day": 6,
              "cur": false,
              "mdp": 693,
              "tw": false
            },
            {
              "day": 7,
              "cur": false,
              "mdp": 1270,
              "tw": true
            },
            {
              "day": 8,
              "cur": false,
              "mdp": 1244,
              "tw": false
            },
            {
              "day": 9,
              "cur": false,
              "mdp": 800,
              "tw": false
            },
            {
              "day": 10,
              "cur": false,
              "mdp": 1199,
              "tw": false
            },
            {
              "day": 11,
              "cur": false,
              "mdp": 1183,
              "tw": false
            },
            {
              "day": 12,
              "cur": false,
              "mdp": 779,
              "tw": false
            },
            {
              "day": 13,
              "cur": false,
              "mdp": 978,
              "tw": false
            },
            {
              "day": 14,
              "cur": false,
              "mdp": 567,
              "tw": false
            },
            {
              "day": 15,
              "cur": false,
              "mdp": 969,
              "tw": false
            },
            {
              "day": 16,
              "cur": false,
              "mdp": 938,
              "tw": false
            },
            {
              "day": 17,
              "cur": false,
              "mdp": 964,
              "tw": false
            },
            {
              "day": 18,
              "cur": false,
              "mdp": 489,
              "tw": false
            },
            {
              "day": 19,
              "cur": false,
              "mdp": 833,
              "tw": false
            },
            {
              "day": 20,
              "cur": false,
              "mdp": 582,
              "tw": false
            },
            {
              "day": 21,
              "cur": false,
              "mdp": 924,
              "tw": false
            },
            {
              "day": 22,
              "cur": false,
              "mdp": 1120,
              "tw": false
            },
            {
              "day": 23,
              "cur": false,
              "mdp": 669,
              "tw": false
            },
            {
              "day": 24,
              "cur": false,
              "mdp": 603,
              "tw": false
            },
            {
              "day": 25,
              "cur": false,
              "mdp": 1080,
              "tw": false
            },
            {
              "day": 26,
              "cur": false,
              "mdp": 911,
              "tw": false
            },
            {
              "day": 27,
              "cur": false,
              "mdp": 1198,
              "tw": false
            },
            {
              "day": 28,
              "cur": false,
              "mdp": 1090,
              "tw": false
            },
            {
              "day": 29,
              "cur": false,
              "mdp": 1022,
              "tw": false
            },
            {
              "day": 30,
              "cur": false,
              "mdp": 1056,
              "tw": false
            },
            {
              "day": 31,
              "cur": false,
              "mdp": 875,
              "tw": false
            },
            {
              "day": 32,
              "cur": false,
              "mdp": 868,
              "tw": false
            },
            {
              "day": 33,
              "cur": false,
              "mdp": 821,
              "tw": false
            },
            {
              "day": 34,
              "cur": false,
              "mdp": 891,
              "tw": false
            }
          ]
        },
        {
          "sid": "42",
          "sn": "2026/2027",
          "pl": 0,
          "ap": 608,
          "tp": 1825,
          "mdw": 0,
          "it": [
            {
              "day": 1,
              "cur": false,
              "mdp": 629,
              "md": "2026-08-28T18:30:00Z",
              "tw": false
            },
            {
              "day": 2,
              "cur": false,
              "mdp": 1196,
              "md": "2026-09-04T18:30:00Z",
              "tw": false
            },
            {
              "day": 3,
              "cur": true,
              "mdp": 0,
              "md": "2026-09-11T18:30:00Z",
              "tw": false
            },
            {
              "day": 4,
              "cur": false,
              "md": "2026-09-18T18:30:00Z",
              "tw": false
            }
          ]
        }
      ]
    }
  },
  "budget": 2920437
}
''')


def build(**overrides):
    inputs = {
        "trades": deepcopy(REAL["trades"]),
        "manager_rows": [
            {"user_id": "2663242", "name": "emil", "rank": 12},
            {"user_id": "2227102", "name": "Niklas B.", "rank": 7},
        ],
        "user_id": "2663242", "user_name": "emil", "budget": REAL["budget"],
        "own_squad_rows": deepcopy(REAL["own_squad"]),
        "manager_performance": deepcopy(REAL["performance"]),
        "generated_at": "2026-09-11T22:00:00Z", "season_start": "2026-08-10T08:31:42Z",
    }
    inputs.update(overrides)
    return {p["user_id"]: p for p in build_manager_profiles(**inputs)}


def test_observed_profit_cashflow_and_current_cost_basis_are_separate():
    own = build()["2663242"]
    summary = own["summary"]
    assert summary["realized_pnl_eur"] == -2079180
    assert summary["purchase_spend_eur"] == 25061005
    assert summary["sale_proceeds_eur"] == 16052835
    assert summary["net_transfer_cashflow_eur"] == -9008170
    assert summary["budget_eur"] == 2920437
    assert summary["complete_round_trips"] == 2
    assert summary["unknown_cost_basis_sales"] == 1
    assert own["coverage"]["realized_pnl_complete"] is False
    holding = next(h for h in own["holdings"] if h["player_id"] == "3081")
    assert holding["buy_amount"] == 8554338
    assert holding["buy_premium_eur"] == 180807
    assert holding["unrealized_pnl_eur"] == -1848132
    assert holding["market_value_change_eur"] == -1667325
    assert summary["unrealized_pnl_eur"] == -1848132
    assert summary["holdings_with_cost_basis"] == 1
    assert own["coverage"]["holdings_without_cost_basis"] == 7
    assert own["coverage"]["unrealized_pnl_complete"] is False
    assert own["financial_timeline"][-1]["realized_pnl_eur"] == -2079180
    assert own["financial_timeline"][-1]["net_transfer_cashflow_eur"] == -9008170


def test_realized_loss_decomposes_into_market_drift_and_execution_premiums():
    own = build()["2663242"]
    daka = next(r for r in own["round_trips"] if r["player_id"] == "15472")
    assert daka["buy_premium_eur"] == 1274091
    assert daka["market_value_change_eur"] == -1824934
    assert daka["sale_premium_eur"] == 0
    assert daka["pnl_eur"] == -3099025
    assert daka["decomposition_complete"] is True
    summary = own["summary"]
    assert summary["decomposed_realized_pnl_eur"] == (
        summary["realized_market_value_change_eur"] - summary["realized_buy_premium_eur"]
        + summary["realized_sale_premium_eur"]
    )


def test_repeated_ownership_pairs_in_chronological_order_for_rival_too():
    trades = deepcopy(REAL["repeated_trades"])
    rival = build(trades=list(reversed(trades)), season_start=None)["2227102"]
    assert [r["pnl_eur"] for r in rival["round_trips"]] == [-3826266, -4456341]
    assert rival["summary"]["realized_pnl_eur"] == -8282607
    assert rival["summary"]["purchases"] == 2
    assert rival["summary"]["sales"] == 2
    assert rival["summary"]["budget_eur"] is None
    assert rival["coverage"]["budget_unknown_reason"] == "rival_budget_not_observed"
    assert rival["coverage"]["season_boundary_known"] is False


def test_current_holding_uses_second_purchase_not_closed_first_purchase():
    rival = build(
        trades=REAL["repeated_trades"], season_start=None,
        generated_at="2025-10-10T12:00:00Z",
        manager_rows=[{"user_id": "2227102", "name": "Niklas B.",
                       "spieler": [{"pid": "3120", "name": "Bischof", "mv": None}]}],
    )["2227102"]
    assert rival["holdings"][0]["buy_amount"] == 14100000
    assert rival["holdings"][0]["bought_at"] == "2025-10-04T10:32:43+00:00"
    assert rival["holdings"][0]["unrealized_pnl_eur"] is None
    assert rival["summary"]["complete_round_trips"] == 1


def test_reset_boundary_prevents_old_buy_from_becoming_new_season_cost_basis():
    rival = build(
        trades=REAL["repeated_trades"], season_start="2025-10-10T00:00:00Z",
    )["2227102"]
    assert rival["summary"]["purchases"] == 0
    assert rival["summary"]["sales"] == 1
    assert rival["summary"]["realized_pnl_eur"] is None
    assert rival["summary"]["unknown_cost_basis_sales"] == 1
    assert rival["round_trips"] == []


def test_unknown_cost_basis_never_uses_api_gain_or_current_market_value():
    sale = next(t for t in REAL["trades"] if t["player_id"] == "14890")
    own = build(
        trades=[sale], own_squad_rows=None,
        manager_rows=[{"user_id": "2663242", "name": "emil", "gewinn": 7415988}],
    )["2663242"]
    assert own["summary"]["realized_pnl_eur"] is None
    assert own["summary"]["sale_proceeds_eur"] == 1625348
    assert own["summary"]["squad_value_eur"] is None
    assert own["summary"]["unrealized_pnl_eur"] is None
    assert own["trades"][0]["realized_pnl_eur"] is None
    assert own["coverage"]["squad_available"] is False


@pytest.mark.parametrize("source", ["api_history", "db_history", "database_daily"])
def test_historical_observed_sources_count_as_priced_purchase(source):
    trade = deepcopy(next(t for t in REAL["trades"] if t["player_id"] == "3081"))
    trade["mw_source"] = source
    own = build(trades=[trade])["2663242"]
    assert own["summary"]["priced_purchases"] == 1
    assert own["summary"]["premium_eur"] == 180807


@pytest.mark.parametrize("change", [
    {"update_boundary": True}, {"mw_source": "current_squad"},
    {"mw_quality": "unknown"}, {"mw_unknown_reason": "missing_observation"},
])
def test_unobserved_or_ambiguous_market_value_never_becomes_premium(change):
    trade = deepcopy(next(t for t in REAL["trades"] if t["player_id"] == "3081"))
    trade.update(change)
    own = build(trades=[trade])["2663242"]
    assert own["summary"]["priced_purchases"] == 0
    assert own["summary"]["premium_eur"] is None
    holding = next(h for h in own["holdings"] if h["player_id"] == "3081")
    assert holding["buy_premium_eur"] is None
    assert holding["unrealized_pnl_eur"] == -1848132


def test_stable_ids_win_over_renames_and_legacy_names_are_trimmed():
    trade = deepcopy(next(t for t in REAL["trades"] if t["player_id"] == "3081"))
    trade.update(buyer_id="2227102", buyer="emil")
    profiles = build(trades=[trade])
    assert profiles["2227102"]["summary"]["purchases"] == 1
    assert profiles["2663242"]["summary"]["purchases"] == 0
    trade.pop("buyer_id")
    trade["buyer"] = " emil  "
    profiles = build(trades=[trade])
    assert profiles["2663242"]["summary"]["purchases"] == 1
    assert profiles["2663242"]["coverage"]["name_fallback_trades"] == 1


def test_ambiguous_legacy_name_is_not_assigned_to_either_manager():
    own = build(manager_rows=[
        {"user_id": "2663242", "name": "emil"}, {"user_id": "2227102", "name": "emil"},
    ])["2663242"]
    assert own["summary"]["purchases"] == 0
    assert own["summary"]["sales"] == 0


def test_duplicate_events_do_not_duplicate_profit_and_inputs_stay_unchanged():
    trades = deepcopy(REAL["trades"])
    before = deepcopy(trades)
    own = build(trades=trades + trades)["2663242"]
    assert trades == before
    assert own["summary"]["realized_pnl_eur"] == -2079180
    assert own["summary"]["purchases"] == 3


def test_performance_scopes_current_season_and_keeps_live_zero():
    profiles = build()
    own, rival = profiles["2663242"], profiles["2227102"]
    assert own["live_points"] == 827
    assert own["completed_points"] == 1023
    assert [r["points"] for r in own["matchday_history"]] == [664, 359, -196]
    assert [r["cumulative_points"] for r in own["matchday_history"]] == [664, 1023, 827]
    assert rival["live_points"] == 1825
    assert rival["completed_points"] == 1825
    assert rival["matchday_history"][2]["points"] == 0
    assert rival["matchday_history"][2]["provisional"] is True
    assert {r["season"] for r in rival["matchday_history"]} == {"2026/2027"}
    past = next(s for s in rival["seasons"] if s["season"] == "2025/2026")
    assert past["completed_points"] == 31780
    assert all(not r["provisional"] for r in past["matchday_history"])
    assert len(own["matchday_history"]) == 3  # künftiger Spieltag 4 hat kein mdp


def test_zero_on_completed_matchday_is_included_not_treated_as_missing():
    perf = deepcopy(REAL["performance"])
    current = perf["2227102"]["it"][-1]
    current["it"][2]["cur"] = False  # derselbe echte Nullwert nach Abschluss
    rival = build(manager_performance=perf)["2227102"]
    assert rival["completed_points"] == 1825
    assert rival["matchday_history"][2]["points"] == 0
    assert rival["coverage"]["missing_matchday_points"] == 0


@pytest.mark.parametrize("missing_row", [False, True])
def test_missing_completed_matchday_breaks_cumulative_and_completed_sum(missing_row):
    perf = deepcopy(REAL["performance"])
    rows = perf["2663242"]["it"][0]["it"]
    if missing_row:
        rows.pop(1)
    else:
        rows[1].pop("mdp")
    own = build(manager_performance=perf)["2663242"]
    assert own["completed_points"] is None
    assert own["live_points"] == 827  # separat belegte API-Gesamtsumme bleibt erhalten
    assert [r["cumulative_points"] for r in own["matchday_history"]] == [664, None, None]
    assert own["coverage"]["missing_matchday_points"] == 1


def test_missing_performance_does_not_infer_completed_points_from_dashboard():
    own = build(manager_performance=None, manager_rows=[{
        "user_id": "2663242", "name": "emil", "dashboard_points": 827,
        "matchday_history": [664, 359, -196],
    }])["2663242"]
    assert own["live_points"] == 827
    assert own["completed_points"] is None
    assert own["matchday_history"] == []
    assert own["coverage"]["performance_available"] is False


@pytest.mark.parametrize("ranking,dashboard,expected,source", [
    (1023, 1825, 1023, "ranking"),
    (0, 1825, 0, "ranking"),
    (None, 1023, 1023, "dashboard"),
    (None, None, 827, "performance"),
])
def test_fresh_ranking_and_dashboard_precede_cached_performance(
    ranking, dashboard, expected, source,
):
    own = build(manager_rows=[{
        "user_id": "2663242", "name": "emil",
        "punkte_saison": ranking, "dashboard_points": dashboard,
    }])["2663242"]
    assert own["live_points"] == expected
    assert own["coverage"]["live_points_source"] == source
    assert own["completed_points"] == 1023
    assert own["matchday_history"][-1]["points"] == -196


def test_fresh_total_never_fills_unknown_current_matchday():
    perf = deepcopy(REAL["performance"])
    perf["2663242"]["it"][0]["it"][2].pop("mdp")
    own = build(manager_performance=perf, manager_rows=[{
        "user_id": "2663242", "name": "emil", "punkte_saison": 827,
    }])["2663242"]
    assert own["live_points"] == 827
    assert own["completed_points"] == 1023
    assert own["matchday_history"][-1]["points"] is None
    assert own["matchday_history"][-1]["cumulative_points"] is None
    assert own["coverage"]["live_points_source"] == "ranking"
