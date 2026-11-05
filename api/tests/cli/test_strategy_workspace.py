"""Sicheres HTML und Finanzszenarien am normalisierten UI-Vertrag, ohne API-Fakes."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from kickbase_agent.cli.cockpit.workspace import _payload, render_workspace
from kickbase_agent.cli.cockpit.workspace_theme import JS


def test_untrusted_content_cannot_close_json_script():
    attack = '</script><img src=x onerror="alert(1)">\u2028&'
    output = render_workspace({"stand": attack, "kader": [{"name": attack}]})
    payload = re.search(
        r'<script id="workspace-data" type="application/json">(.*?)</script>', output, re.S
    ).group(1)
    assert "<" not in payload
    assert "\u2028" not in payload
    assert json.loads(payload)["kader"][0]["name"] == attack
    assert '<img src=x onerror="alert(1)">' not in output
    assert output.count("</script>") == 2


def test_missing_and_non_finite_amounts_stay_unknown():
    result = json.loads(_payload({"budget": math.nan, "kader": [{"mv": math.inf}]}))
    assert result["budget"] is None
    assert result["bids"] is None
    assert result["kader"][0]["mv"] is None


def test_only_selected_cockpit_fields_are_embedded():
    result = json.loads(_payload({"budget": 0, "private_config": {"password": "hidden"}}))
    assert result["budget"] == 0
    assert "private_config" not in result


def _browser_runtime(d: dict, actions: str = "") -> dict:
    """Kleine DOM-Grenze für reine lokale Berechnung; kein simuliertes Kickbase-API."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node nicht installiert")
    setup = r'''
const nodes = new Map(), handlers = new Map(), saved = new Map();
let lastURL="";
const localStorage={getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)};
function el(id) {
  if (!nodes.has(id)) nodes.set(id, {innerHTML:'', textContent:'', value:'',
    className:'', dataset:{}, style:{}, addEventListener(){}, focus(){},
    showModal(){this.open=true;}, close(){this.open=false;},
    getBoundingClientRect(){return {top:0}}});
  return nodes.get(id);
}
const document = {getElementById:el, querySelectorAll:()=>[],
  addEventListener:(name,fn)=>handlers.set(name,fn)};
const window = {addEventListener(){},scrollTo(){},scrollBy(){}};
const history = {replaceState(a,b,url){lastURL=url;}};
const location = {hash:'#heute'}, scrollY=0;
function click(button) {
  handlers.get('click')({target:{closest:sel=>sel==='button,a'?button:null}});
}
function input(target) { handlers.get('input')({target}); }
function change(target) { handlers.get('change')({target}); }
function manager(id) {
  handlers.get('click')({target:{closest:sel=>sel==='[data-manager]'?{dataset:{manager:id}}:null}});
}
function openPlayer(id) {
  handlers.get('click')({target:{closest:sel=>sel==='[data-player]'?{dataset:{player:id}}:null}});
}
'''
    script = (
        setup + "\nel('workspace-data').textContent=" + json.dumps(json.dumps(d))
        + ";\n" + JS + "\n" + actions
        + "\nprocess.stdout.write(JSON.stringify({"
        + "total:el('calc-total').textContent,result:el('calc-result').textContent,"
        + "content:el('content').innerHTML,drawer:el('drawer-content').innerHTML, "
        + "eff:el('eff-body').innerHTML,"
        + "points:el('points-history').innerHTML,finance:el('financial-history').innerHTML,"
        + "url:lastURL,saved:Object.fromEntries(saved)}));"
    )
    result = subprocess.run([node, "-"], input=script, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_unknown_open_bids_do_not_appear_financed():
    result = _browser_runtime({"budget": 5_000_000},
                              "input({id:'reserve',value:'0',dataset:{}});")
    assert result["total"] == "—"
    assert "offene Gebote fehlen" in result["result"]


def test_package_includes_existing_bids_and_rejects_missing_or_negative_prices():
    # Reines Szenario: zwei Millionen Konto, eine Million gebunden, weiterer Kauf 800k.
    data = {"budget": 2_000_000, "bids": [{"amount": 1_000_000}],
            "market": [{"pid": "scenario", "name": "Szenario", "mv": 800_000}]}
    select = '''el('package-player').value='scenario';
click({id:'package-add',dataset:{}});
input({id:'reserve',value:'300000',dataset:{}});
'''
    result = _browser_runtime(data, select)
    assert result["total"] == "-100.000 €"
    assert "nicht vollständig finanziert" in result["result"]
    for price in ("", "-1"):
        result = _browser_runtime(
            data, select + "input({id:'',value:" + json.dumps(price)
            + ",dataset:{packageAmount:'0'}});"
        )
        assert result["total"] == "—"
        assert "Jeder Spielerpreis" in result["result"]


def test_completed_standings_override_interim_scores_for_season_gap():
    data = {
        "ranking": [{"user_id": "me", "name": "emil", "me": True, "rank": 2,
                     "points": 700}, {"user_id": "other", "name": "Andere", "rank": 1,
                                     "points": 1400}],
        "strategy": {"manager_stats": [{"user_id": "me", "completed_points": 1000},
                                       {"user_id": "other", "completed_points": 1300}]},
    }
    result = _browser_runtime(data)
    assert '300 <span class="unit">Pkt.</span>' in result["content"]
    assert "Abgeschlossene Spieltage" in result["content"]
    assert "Nächste Kaderfrist · noch nicht bestätigt" in result["content"]


def test_unknown_completed_points_use_explicit_live_standings():
    # Normalisierter UI-Vertrag aus dem Live-Build 11.09., 23:33 Berlin.
    data = {
        "ranking": [{"user_id": "2663242", "name": "emil", "me": True,
                     "rank": 2, "points": 827},
                    {"user_id": "2227102", "name": "Niklas B.", "rank": 1,
                     "points": 1825}],
        "strategy": {"manager_stats": [
            {"user_id": "2663242", "completed_points": None, "dashboard_points": 827},
            {"user_id": "2227102", "completed_points": None, "dashboard_points": 1825},
        ]},
    }
    result = _browser_runtime(data)
    assert '998 <span class="unit">Pkt.</span>' in result["content"]
    assert "Live-Tabelle · Zwischenstand" in result["content"]
    assert "Abgeschlossene Spieltage" not in result["content"]


def test_confirmed_lineup_is_dated_and_separate_from_future_prediction():
    now = datetime.now(UTC)
    article = {
        "player_id": "4406", "player_name": "Dina Ebimbe", "event_type": "confirmed_lineup",
        "role_status": "bench", "matchday": 3, "title": "Belegte Aufstellung",
        "published_at": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=2)).isoformat(),
        "source": "LigaInsider", "url": "https://www.ligainsider.de/aufstellung",
    }
    data = {"kader": [{"pid": "4406", "name": "Dina Ebimbe", "status": "starter"}],
            "strategy": {"news": {"articles": [article]}}}
    result = _browser_runtime(data, "openPlayer('4406');")
    assert "Bestätigt ST3: Bankstart" in result["drawer"]
    assert "LI-Prognose: Startelf" in result["drawer"]
    assert "Keine Aussage über künftige Startelfplätze" in result["drawer"]
    assert 'href="https://www.ligainsider.de/aufstellung"' in result["drawer"]
    article["expires_at"] = (now - timedelta(minutes=1)).isoformat()
    result = _browser_runtime(data, "openPlayer('4406');")
    assert "Bestätigt ST3: Bankstart" not in result["drawer"]
    assert "Beleg veraltet" in result["drawer"]


def test_news_prioritize_current_confirmed_own_player_and_remove_duplicates():
    now = datetime.now(UTC)
    base = {"source": "LigaInsider", "expires_at": (now + timedelta(days=1)).isoformat(),
            "published_at": (now - timedelta(hours=1)).isoformat()}
    confirmed = {**base, "player_id": "4406", "title": "Bestätigte eigene Aufstellung",
                 "event_type": "confirmed_lineup", "role_status": "bench", "matchday": 3,
                 "url": "https://www.ligainsider.de/elf"}
    general = {**base, "title": "Neuere breite Meldung", "url": "https://example.com/news",
               "published_at": (now - timedelta(minutes=1)).isoformat()}
    old = {**confirmed, "title": "Abgelaufene Aufstellung", "url": "https://example.com/old",
           "expires_at": (now - timedelta(minutes=2)).isoformat()}
    data = {"kader": [{"pid": "4406", "name": "Dina Ebimbe"}],
            "strategy": {"news": {"articles": [old, general, confirmed, confirmed]}}}
    content = _browser_runtime(data)["content"]
    assert content.index("Bestätigte eigene Aufstellung") < content.index("Neuere breite Meldung")
    assert content.index("Neuere breite Meldung") < content.index("Abgelaufene Aufstellung")
    assert content.count("Bestätigte eigene Aufstellung") == 1



def _manager_view_data() -> dict:
    """Normalisierte UI-Szenarien, keine nachgebildeten Kickbase-Antworten."""
    profiles = []
    for uid, name, me, cost, gain in (("emil", "emil", True, 1_000_000, 200_000),
                                     ("rival", "Rival", False, 1_500_000, -300_000)):
        profiles.append({
            "user_id": uid, "name": name, "me": me, "completed_points": 300,
            "live_points": 350,
            "summary": {"squad_value_eur": 1_200_000, "budget_eur": 10_000 if me else None,
                        "realized_pnl_eur": gain, "unrealized_pnl_eur": 1_200_000-cost,
                        "purchases": 1, "sales": 0, "holdings_with_cost_basis": 1},
            "holdings": [{"player_id": "player", "name": "Spieler", "market_value": 1_200_000,
                          "buy_amount": cost, "unrealized_pnl_eur": 1_200_000-cost}],
            "trades": [{"player_id": "player", "name": "Spieler", "direction": "purchase",
                        "amount": cost, "at": "2026-09-10T10:00:00Z"}],
            "round_trips": [], "financial_timeline": [],
            "matchday_history": [{"season": "2026/2027", "matchday": 1,
                                  "points": 300, "cumulative_points": 300,
                                  "provisional": False}],
        })
    return {"kader": [{"pid": "player", "name": "Spieler", "mv": 1_200_000}],
            "ranking": [{"user_id": "emil", "name": "emil", "me": True, "rank": 1,
                         "points": 350}, {"user_id": "rival", "name": "Rival", "rank": 2,
                                           "points": 350}],
            "strategy": {"manager_profiles": profiles}}


def test_overview_uses_one_manager_profile_and_keeps_selection_across_tabs():
    actions = """click({dataset:{view:'uebersicht'}});
change({id:'overview-manager',value:'rival'});
click({dataset:{view:'liga'}});
click({dataset:{view:'uebersicht'}});"""
    result = _browser_runtime(_manager_view_data(), actions)
    assert "Rival</h1>" in result["content"]
    assert "1,50 Mio. €" in result["content"]
    assert "Konto nicht einsehbar" in result["content"]
    assert "Konto 10.000" not in result["content"]
    assert result["saved"]["kickbase.overview.manager"] == "rival"
    assert result["url"] == "#uebersicht?manager=rival"
    for section in ("Saisonverlauf", "Handelsverlauf", "Mannschaft", "Alle beobachteten Transfers"):
        assert section in result["content"]


def test_league_manager_click_uses_overview_and_player_costs_keep_that_context():
    result = _browser_runtime(_manager_view_data(), "manager('rival');openPlayer('player');")
    assert "Rival</h1>" in result["content"]
    assert "Bei Rival" in result["drawer"]
    assert "1,50 Mio. €" in result["drawer"]
    assert "1,00 Mio. €" not in result["drawer"]
    assert result["url"] == "#uebersicht?manager=rival"


def test_today_action_plan_preserves_proposal_status_and_reference_price():
    data = {"strategy": {"action_plan": {
        "title": "Abwehr und Angriff finanzieren", "status": "offen",
        "steps": [{"title": "Zuschlag prüfen", "status": "Geboten", "amount_eur": 2_000_000,
                   "href": "legacy.html#zentrale"},
                  {"title": "Startrolle prüfen", "status": "Prüfliste",
                   "reference_price_eur": 750_000, "href": "javascript:alert(1)"}],
    }}}
    result = _browser_runtime(data)
    assert "Abwehr und Angriff finanzieren" in result["content"]
    assert ">Geboten</span>" in result["content"]
    assert ">Prüfliste</span>" in result["content"]
    assert "Marktpreis zur Prüfung: 750.000 €" in result["content"]
    assert "Ausgeführt" not in result["content"]
    assert "javascript:" not in result["content"]
    assert 'href="legacy.html#zentrale"' not in result["content"]
    assert 'id="calculator"' not in result["content"]


def test_chart_unknown_values_interrupt_points_and_financial_lines():
    data = _manager_view_data()
    profile = data["strategy"]["manager_profiles"][0]
    profile["matchday_history"] = [
        {"season": "2026/2027", "matchday": i, "points": v, "cumulative_points": v}
        for i, v in enumerate((664, None, -196), start=1)
    ]
    profile["financial_timeline"] = [
        {"at": f"2026-09-{10+i:02d}T10:00:00Z", "realized_pnl_eur": v}
        for i, v in enumerate((-100_000, None, -300_000))
    ]
    result = _browser_runtime(data, """manager('emil');
click({dataset:{pointsMode:'matchday'}});
click({dataset:{financeMode:'profit'}});""")
    for chart in (result["points"], result["finance"]):
        assert "data-series-segment" not in chart
        assert chart.count("<circle ") == 2
        assert "1 Werte unbekannt" in chart
    assert "ST 2" in result["points"]
    assert ">0 €</text>" in result["finance"]
    assert ">1 €</text>" not in result["finance"]


def test_result_bridge_identifies_its_shared_covered_subset():
    data = _manager_view_data()
    profile = data["strategy"]["manager_profiles"][0]
    profile["summary"].update({
        "complete_round_trips": 5, "realized_market_value_change_eur": 120_000,
        "realized_buy_premium_eur": 30_000, "realized_sale_premium_eur": 10_000,
    })
    profile["coverage"] = {"round_trips_with_decomposition": 2}
    content = _browser_runtime(data, "manager('emil');")["content"]
    assert "2 von 5 Paaren mit vergleichbaren Marktwerten" in content
    assert "Ergebnis der Teilmenge" in content
    assert "100.000 €" in content
    assert "− Kaufaufschläge" in content
    profile["summary"]["realized_buy_premium_eur"] = None
    content = _browser_runtime(data, "manager('emil');")["content"]
    assert "Für die Ergebnisbrücke fehlen vergleichbare Marktwerte" in content
    assert "100.000 €" not in content


@pytest.mark.parametrize("uncertain", [
    {"update_boundary": True}, {"mw_unknown_reason": "Lücke"},
    {"mw_quality": "unknown"}, {"mw_source": "current"},
])
def test_uncertain_historical_values_never_appear_as_priced_premiums(uncertain):
    data = _manager_view_data()
    trade = data["strategy"]["manager_profiles"][0]["trades"][0]
    trade.update({"market_value": 765_432, "premium_pct": 30.64,
                  "mw_source": "api_history"})
    certain = _browser_runtime(data, "manager('emil');openPlayer('player');")
    assert "30,6 %" in certain["drawer"]
    trade.update(uncertain)
    result = _browser_runtime(data, "manager('emil');openPlayer('player');")
    for content in (result["content"], result["drawer"]):
        assert "30,6 %" not in content
        assert "765.432 €" not in content
        assert "MW unsicher" in content or "Updatefenster" in content


def test_historical_player_does_not_promote_last_history_value_to_current_price():
    data = {"strategy": {"players": [{
        "player_id": "historic", "name": "Historischer Spieler", "market_value": None,
        "history": [{"date": "2026-09-01", "value": 765_432}],
    }]}}
    drawer = _browser_runtime(data, "openPlayer('historic');")["drawer"]
    assert "Historischer Spieler" in drawer
    assert "Aktueller Marktwert" in drawer
    assert "765.432 €" not in drawer


def test_the_sidebar_links_the_jev_page():
    html = render_workspace({"stand": "12.10.2026 14:00", "strategy": {}})
    assert 'data-view="jev"' in html
    assert 'href="jev.html"' not in html
    assert 'href="legacy.html' not in html
    assert 'closedby="any"' in html


def test_player_dossier_renders_jev_klartext_and_agent_verdict():
    data = {
        "budget": 2_219_028,
        "teamwert": 137_800_000,
        "kader": [
            {"pid": "51", "name": "Rani Khedira", "team": "Union", "pos": "MIT", "mv": 5_725_190,
             "mv24": -283_115, "elf": True},
        ],
        "market": [
            {"pid": "8247", "name": "Ramon Hendriks", "team": "Stuttgart", "pos": "ABW",
             "mv": 7_215_302, "mv24": 372_357, "fair_eur": 8_500_000, "fair_delta_pct": 17.8,
             "signals": [{"name": "Form", "score": 0.84}]},
        ],
        "intents": [
            {"id": 332, "kind": "bid", "player_id": "8247", "amount": 7_361_699,
             "status": "planned"},
        ],
        "jev": {
            "zahlen": {"urteile_7d": 42, "folgen_7d": 3},
            "spieler": {
                "8247": [
                    {"kind": "rolle", "status": "ok", "answer": "startet", "p": 0.94,
                     "probabilities": {"startet": 0.94, "bank": 0.06}, "outcome": "eindeutig",
                     "subject": "Hendriks bleibt hinten links gesetzt", "source": "kicker",
                     "url": "https://www.kicker.de/hendriks", "at": "2026-10-20T09:00:00Z"},
                ],
            },
        },
        "strategy": {
            "purchase_reviews": {
                "8247": {
                    "selected": True,
                    "target_bid_eur": 7_361_699,
                    "swap_sale": {"player_id": "51", "proceeds_eur": 5_152_671},
                    "bid_scenarios": [{
                        "selected": True,
                        "win": {"points": 4353.2},
                        "loss": {"points": 4189.6},
                    }],
                },
            },
            "team_plan": {
                "formation": "5-4-1",
                "lineup_ids": ["8247"],
                "swap_sales": [{"player_id": "51", "for_player_id": "8247",
                                "proceeds_eur": 5_152_671}],
                "forecasts": {
                    "8247": {
                        "expected_points": 96.5,
                        "horizon_points": 386.0,
                        "expected_minutes": 87.5,
                        "reasons": ["Spielplan-Horizontfaktor (4 Spieltage): ×1.08."],
                        "market_value": {
                            "horizons": {"3": {"change_eur": 950_000, "change_pct": 13.2}},
                        },
                    },
                },
            },
        },
    }
    res = _browser_runtime(data, "openPlayer('8247');")
    assert "Transfermarkt-Radar" in res["content"]
    assert "Mein Kader auf einen Blick" in res["content"]
    assert "42 Checks" in res["content"]
    assert "Kauf-Vorhaben aktiv: Gebot 7,36 Mio. €" in res["drawer"]
    assert "Rani Khedira" in res["drawer"]
    assert "+163,6 erwartete Punkte" in res["drawer"]
    assert "386 Pkt." in res["drawer"]
    assert "Spielplan ×1.08" in res["drawer"]
    assert "Jev Einsatz-Check" in res["drawer"]
    assert "Startelf-Signal · 94 %" in res["drawer"]
    assert "Hendriks bleibt hinten links gesetzt" in res["drawer"]
    res_sell = _browser_runtime(data, "openPlayer('51');")
    assert "Geplanter Tausch-Verkauf" in res_sell["drawer"]
    assert "Ramon Hendriks" in res_sell["drawer"]


def test_efficiency_radar_compares_points_per_million_across_pools_and_metrics():
    data = {
        "budget": 3_000_000,
        "teamwert": 90_000_000,
        "kader": [
            {"pid": "k1", "name": "Kader Schnapper", "team": "Bochum", "pos": "MIT",
             "position": 3, "mv": 4_000_000, "mv24": 100_000, "avg": 80, "punkte": 480,
             "elf": True},
        ],
        "market": [
            {"pid": "m1", "name": "Markt Star", "team": "Bayern", "pos": "STU",
             "position": 4, "mv": 20_000_000, "mv24": 250_000, "avg": 140, "punkte": 700,
             "listing_type": "kickbase"},
        ],
        "manager_squads": [
            {
                "user_id": "rival1", "name": "Konkurrent", "me": False,
                "spieler": [
                    {"pid": "r1", "name": "Rivalen Juwel", "pos": "ABW", "position": 2,
                     "mv": 5_000_000, "avg_points": 90, "punkte": 540, "in_elf": True},
                ],
            },
        ],
        "fair": {
            "curve": {"a": 50000.0, "b": 1.4, "n": 10},
            "cloud": [
                {"player_id": "c1", "name": "Bundesliga Geheimtipp", "ap": 75,
                 "mv": 3_000_000, "pos": "TW", "team_id": "2", "img": ""},
            ],
        },
        "strategy": {
            "team_plan": {
                "lineup_ids": ["k1"],
                "forecasts": {
                    "k1": {"horizon_points": 320.0},
                    "m1": {"horizon_points": 600.0},
                },
            },
        },
    }
    payload = json.loads(_payload(data))
    assert payload["fair"]["cloud"][0]["pid"] == "c1"
    assert payload["fair"]["cloud"][0]["position"] == 1
    assert payload["fair"]["cloud"][0]["team"]

    res = _browser_runtime(data)
    content = res["content"]
    assert "Preis-Leistungs-Radar" in content
    assert "Punkte pro Mio. € Marktwert" in content
    eff_section = content[content.index('id="efficiency-radar"'):]
    eff_tbody = eff_section[eff_section.index("<tbody>"):]
    # c1: 75 / 3M = 25,0; k1: 80 / 4M = 20,0; r1: 90 / 5M = 18,0; m1: 140 / 20M = 7,0
    assert "Bundesliga Geheimtipp · 25,0 / Mio." in eff_section
    assert eff_tbody.index("Bundesliga Geheimtipp") < eff_tbody.index("Kader Schnapper")
    assert eff_tbody.index("Kader Schnapper") < eff_tbody.index("Rivalen Juwel")

    # Sortierwechsel auf Gesamtpunkte pro Mio. €: k1 = 480 / 4M = 120,0 vor r1 = 540 / 5M = 108,0
    res_total = _browser_runtime(data, "click({dataset:{effSort:'total_per_m'}});")
    eff_total = res_total["eff"][res_total["eff"].index("<tbody>"):]
    assert "120,0" in eff_total
    assert "108,0" in eff_total
    assert eff_total.index("Kader Schnapper") < eff_total.index("Rivalen Juwel")
    assert eff_total.index("Markt Star") < eff_total.index("Bundesliga Geheimtipp")

    # Sortierwechsel auf 4-ST-Prognose pro Mio. €: k1 = 320 / 4M = 80,0 vor m1 = 600 / 20M = 30,0
    res_fc = _browser_runtime(data, "click({dataset:{effSort:'forecast_per_m'}});")
    eff_fc = res_fc["eff"][res_fc["eff"].index("<tbody>"):]
    assert "80,0" in eff_fc
    assert "30,0" in eff_fc
    assert eff_fc.index("Kader Schnapper") < eff_fc.index("Markt Star")

    # Schnellfilter auf Transfermarkt: zeigt nur Markt Star
    res_mkt = _browser_runtime(data, "click({dataset:{effPool:'market'}});")
    eff_mkt = res_mkt["eff"]
    assert "Markt Star" in eff_mkt
    assert "Kader Schnapper" not in eff_mkt
    assert "Bundesliga Geheimtipp" not in eff_mkt

    # Suche und Positionsfilter
    res_pos = _browser_runtime(data, "change({id:'eff-pos',value:'2'});")
    assert "Rivalen Juwel" in res_pos["eff"]
    assert "Kader Schnapper" not in res_pos["eff"]

    # Klick auf Spieler öffnet Dossier mit Effizienz-Kennzahlen
    res_drawer = _browser_runtime(data, "openPlayer('k1');")
    assert "Ø Punkte pro Mio. € Marktwert" in res_drawer["drawer"]
    assert "Saisonpunkte pro Mio. € (480 Pkt.)" in res_drawer["drawer"]

    # Eigener Reiter „Alle Spieler" und Bundesliga-Suche in der Übersicht
    res_spieler = _browser_runtime(data, "click({dataset:{view:'spieler'}});")
    assert res_spieler["url"].startswith("#spieler")
    assert "Alle Spieler · Preis-Leistungs-Radar" in res_spieler["content"]
    assert "Bundesliga Geheimtipp" in res_spieler["content"]

    res_ov_search = _browser_runtime(
        data,
        "input({id:'player-search',value:'Geheimtipp',dataset:{}});click({dataset:{view:'uebersicht'}});",
    )
    assert "Treffer in der Bundesliga außerhalb dieses Kaders" in res_ov_search["content"]
    assert "Bundesliga Geheimtipp" in res_ov_search["content"]


def test_ai_factor_renders_score_pillars_and_caps_blocked_players():
    data = {
        "budget": 5_000_000,
        "teamwert": 95_000_000,
        "market": [
            {
                "pid": "top1",
                "name": "Top Einkauf",
                "team": "Leverkusen",
                "pos": "MIT",
                "position": 3,
                "mv": 8_000_000,
                "mv24": 320_000,
                "avg": 115,
                "status": "starter",
                "kb_prob": 1,
            },
            {
                "pid": "inj1",
                "name": "Verletzter Kandidat",
                "team": "Bochum",
                "pos": "ABW",
                "position": 2,
                "mv": 6_000_000,
                "mv24": -210_000,
                "avg": 90,
                "status": "out",
            },
        ],
        "jev": {
            "spieler": {
                "top1": [
                    {
                        "kind": "rolle",
                        "status": "ok",
                        "answer": "startet",
                        "p": 0.96,
                        "outcome": "eindeutig",
                    }
                ],
                "inj1": [
                    {
                        "kind": "rolle",
                        "status": "ok",
                        "answer": "fehlt",
                        "p": 0.98,
                        "outcome": "sperrt",
                    }
                ],
            }
        },
        "strategy": {
            "purchase_reviews": {"top1": {"selected": True, "target_bid_eur": 8_200_000}},
            "team_plan": {
                "forecasts": {
                    "top1": {"horizon_points": 390.0, "expected_minutes": 88.0},
                }
            },
        },
    }
    res_top = _browser_runtime(data, "openPlayer('top1');")
    assert "AI-Faktor (0–100)" in res_top["content"]
    assert "4-Säulen-Aufschlüsselung des AI-Faktors" in res_top["drawer"]
    assert "Startelf &amp; Rolle" in res_top["drawer"]
    assert "4-ST-Punkte &amp; Form" in res_top["drawer"]
    assert "Preis-Leistung" in res_top["drawer"]
    assert "Jev &amp; Dynamik" in res_top["drawer"]
    top_match = re.search(r"AI (\d+)<i>/100</i>", res_top["drawer"])
    assert top_match and int(top_match.group(1)) >= 82

    res_inj = _browser_runtime(data, "openPlayer('inj1');")
    inj_match = re.search(r"AI (\d+)<i>/100</i>", res_inj["drawer"])
    assert inj_match and int(inj_match.group(1)) <= 24


def test_control_hub_buttons_call_backend_endpoints_and_update_state():
    data = {
        "budget": 4_000_000,
        "teamwert": 110_000_000,
        "market": [
            {
                "pid": "8247",
                "name": "Ramon Hendriks",
                "team": "Stuttgart",
                "pos": "ABW",
                "mv": 7_200_000,
            }
        ],
        "intents": [
            {
                "id": 332,
                "kind": "bid",
                "player_id": "8247",
                "player_name": "Ramon Hendriks",
                "amount": 7_300_000,
                "status": "planned",
                "rationale": "Startelf-Upgrade mit +140 Pkt. über 4 ST.",
                "seconds_left": 900,
            }
        ],
    }
    mock_setup = """
const calls = [];
window.__kbaFetch = (url, opts) => {
  calls.push({url, method: opts.method, headers: opts.headers, body: opts.body});
  if (url.endsWith('/execute-now')) {
    return {ok: true, data: {intent: {id: 332, status: 'planned', seconds_left: 0},
      note: 'Execute-Now für #332 gespeichert.'}};
  }
  if (url.endsWith('/adjust')) {
    const b = JSON.parse(opts.body || '{}');
    const amt = b.amount || Math.round(7300000 * (1 + (b.pct || 10) / 100));
    return {ok: true, data: {intent: {id: 332, status: 'planned', amount: amt,
      adjusted_from: 7300000}}};
  }
  if (url.endsWith('/veto')) {
    return {ok: true, data: {intent: {id: 332, status: 'vetoed'},
      note: 'Veto für #332 protokolliert.'}};
  }
  if (url === '/api/refresh') {
    return {ok: true, data: {started: true, note: 'Cockpit-Neuberechnung läuft.'}};
  }
  return {ok: true, data: {intents: [], entries: []}};
};
"""
    res_exec = _browser_runtime(
        data,
        mock_setup + "click({dataset:{intentAct:'exec',intentId:'332'}});",
    )
    assert "KI-Steuerzentrale · Direkte Backend-Anbindung" in res_exec["content"]
    assert "POST /api/intents/{id}/execute-now" in res_exec["content"]
    assert "Execute-Now für #332 gespeichert." in res_exec["content"]
    assert "Sofort fällig (nächster Takt)" in res_exec["content"]

    res_adj = _browser_runtime(
        data,
        mock_setup + "click({dataset:{intentAct:'plus10',intentId:'332'}});",
    )
    assert "8,03 Mio. €" in res_adj["content"]
    assert "Angepasst von 7,30 Mio. €" in res_adj["content"]

    res_custom = _browser_runtime(
        data,
        mock_setup
        + "el('intent-amount-input-332').value='7850000';"
        + "click({dataset:{intentAct:'amount',intentId:'332'}});",
    )
    assert "7,85 Mio. €" in res_custom["content"]

    res_veto = _browser_runtime(
        data,
        mock_setup + "click({dataset:{intentAct:'veto',intentId:'332'}});",
    )
    assert "Veto für #332 protokolliert." in res_veto["content"]
    assert "Veto gespeichert" in res_veto["content"]

    res_refresh = _browser_runtime(
        data,
        mock_setup + "click({id:'kba-refresh',dataset:{}});",
    )
    assert "Cockpit-Neuberechnung läuft." in res_refresh["content"]


def test_lineup_pitch_and_in_app_jev_view():
    data = {
        "formation": "4-5-1",
        "kader": [
            {
                "pid": "10",
                "name": "Marvin Schwäbe",
                "team": "Köln",
                "pos": "TW",
                "position": 1,
                "mv": 12_000_000,
                "status": "starter",
                "elf": True,
            },
            {
                "pid": "20",
                "name": "Steffen Tigges",
                "team": "Köln",
                "pos": "STU",
                "position": 4,
                "mv": 2_500_000,
                "status": "bench",
                "elf": False,
            },
        ],
        "jev": {
            "zahlen": {"urteile_7d": 18, "folgen_7d": 2},
            "bilanz": {"treffer_quote_pct": 88.5},
            "feed": [
                {
                    "player_id": "10",
                    "player": "Marvin Schwäbe",
                    "kind": "einsatz",
                    "answer": "startet",
                    "p": 0.96,
                    "subject": "Schwäbe steht sicher im Tor",
                    "source": "LigaInsider",
                }
            ],
        },
    }
    today_res = _browser_runtime(data)
    assert 'id="lineup-pitch"' in today_res["content"]
    assert "Empfohlene Elf (4-5-1)" in today_res["content"]
    assert "Schwäbe" in today_res["content"]
    assert "Bank (1)" in today_res["content"]

    jev_res = _browser_runtime(data, "click({dataset:{view:'jev'}});")
    assert "Jev-Analyse." in jev_res["content"]
    assert "88,5 %" in jev_res["content"]
    assert "Schwäbe steht sicher im Tor" in jev_res["content"]
    assert "Startelf-Signal · 96 %" in jev_res["content"]




