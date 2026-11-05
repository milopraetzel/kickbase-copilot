"""Parameter-Historie + aktuelle Parameter der Entscheidungs-Engine (Lern-Schicht).

Die Engine lernt bisher NICHT automatisch: Gewichte und Guardrails sind feste Werte,
die nach Erkenntnissen von Hand geändert werden (Commit + Grund). Damit das
nachvollziehbar bleibt, liegt jede belegte Änderung in ``parameter_history.json``
(im Paket, Pflege von Hand — nichts erfinden, nur Einträge mit Commit/Kommentar):

    {"date": "2026-08-23", "parameter": "bid_overpay_fraction", "old": "0.05",
     "new": "0.08", "reason": "…", "commit": "da06d03"}   # old=None: neu eingeführt

``current_parameters()`` liefert die heute gültigen Werte (Guardrails-Defaults +
die Modul-Konstanten, die in der Historie vorkommen) unter denselben Namen, damit
UI/MCP Historie und Ist-Wert verknüpfen können. ``OPEN_CALIBRATIONS`` nennt ehrlich,
was noch NICHT gegen Ergebnisse kalibriert ist.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from kickbase_agent.agent.executor import (
    PREFLIGHT_MAX_PRICE_DRIFT,
    PREFLIGHT_MAX_VALUE_DRIFT,
    PROPOSAL_TTL,
)
from kickbase_agent.cli.plan import (
    COMPLETION_BID_FRACTION,
    COMPLETION_BID_FRACTION_MANY_OPEN,
    COMPLETION_BID_FRACTION_TWO_OPEN,
    ROLE_PROOF_MATCHDAYS,
    ROLE_PROOF_MIN_MINUTES,
    ROLE_PROOF_MIN_VALUE,
    SWAP_HOLD_AFTER_PURCHASE,
)
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS, protected_player_ids
from kickbase_agent.decision.kickoff_freeze import (
    BID_FREEZE_MINUTES,
    EXPOSURE_GUARD_HOURS,
    KICKOFF_MIN_BALANCE,
)
from kickbase_agent.decision.lineup import MIN_SQUAD_SIZE
from kickbase_agent.decision.overdraft import (
    PLANNING_OVERDRAFT_UNTIL,
    SWAP_SALE_CREDIT_PERCENT,
)
from kickbase_agent.decision.purchase_review import EMERGENCY_BENCH_WINDOW, REVIEW_LIFETIME
from kickbase_agent.intel.community import MAX_BOOST, TIP_TTL_DAYS
from kickbase_agent.intel.signals import (
    BASE_POINTS_WEIGHT,
    FORM_WEIGHT,
    MATCHUP_WEIGHT,
    TREND_1D_WEIGHT,
    TREND_WEIGHT,
)
from kickbase_agent.trading.trade_budget import FEED_MAX_ITEMS
from kickbase_agent.worker.intents import ACCEPT_VETO_WINDOW

HISTORY_FILE = "parameter_history.json"
HISTORY_KEYS: tuple[str, ...] = (
    "date", "parameter", "old", "new", "reason", "commit", "expect", "review",
    # Autonomer Lernzyklus (26.08.): worauf sich die Aenderung stuetzt, und ob sie
    # ueber die Mechanismus-Ausnahme lief (ein Fall statt zwei). Beide erscheinen
    # automatisch im Protokoll — wird die Ausnahme zur Regel, sieht Emil es.
    "beleg", "einzelfall_ausnahme",
    # WER hat geaendert (26.08., Emils Tabelle in der Zentrale): "orchestrator"
    # (Default, auch fuer Alt-Eintraege) oder "selbstumbau". Geraten wird nie.
    "quelle",
)

#: Guardrails-Felder, die current_parameters() garantiert enthält (Vertrag mit UI/API).
#: ACHTUNG: Diese beiden Tupel sind die STELLSCHRAUBEN — was hier steht, darf der
#: Orchestrator mit Decision-Record anpassen. `protected_player_ids` gehört
#: bewusst NICHT dazu (Verfassung, siehe CONSTITUTION_KEYS).
GUARDRAIL_KEYS: tuple[str, ...] = (
    "max_bid_pct_of_budget",
    "bid_overpay_fraction",
    "bid_overpay_conviction",
    "bid_overpay_need",
    "bid_ladder_opening",
    "max_bid_premium",
    "max_bid_premium_need",
    "max_bid_premium_trade",
    "buy_score_threshold",
    "sell_score_threshold",
    "listing_floor",
    "accept_offer_floor",
    "daily_buy_cap",
    "daily_sell_cap",
    "approval_threshold",
    "league_trade_limit_per_half",
)
#: Weitere Guardrails-Felder (additiv).
_EXTRA_GUARDRAIL_KEYS: tuple[str, ...] = (
    "bid_psych_offset",
    "bid_ladder_raise_hours",
    "min_balance",
    "sell_trend_score_max",
    "sell_trend_lineup_max",
    "position_need_tolerance",
    # Momentum-Doktrin (31.08.): Listen-Kontingent + Gewinnmitnahme + Reserve.
    "daily_list_cap",
    "take_profit_trend_min",
    "take_profit_trend1d_max",
    "momentum_reserve_haircut",
    # Plan-Hysterese (01.09.): geplante Gebote überleben kleine Score-Wackler.
    "plan_hysteresis_margin",
    # Pfand-Hebel (01.09.): Plan-Basis = risk_budget / fraction (0.35 deckt
    # die 135-%-Kappe bei 0.90-Rückverkauf).
    "pfand_risk_fraction",
    # Nachanker (02.09.): Gebot unter aktuellem MW wird bis +10 % nachgezogen.
    "bid_reanchor_max",
    # Elf-Upgrade-Stufe (19.10.): Kickbase-Auktionen mit Engine-Kaufbeleg bis 125 % MW.
    "max_bid_premium_upgrade",
)

#: VERFASSUNG — sichtbar, aber keine Stellschraube (26.08.). Erscheint in
#: current_parameters() als lesbarer Text, damit die Zentrale zeigt, WER
#: geschützt ist; geändert wird die Liste ausschließlich von Emil über die Env
#: (`PROTECTED_PLAYER_IDS`), nie durch einen Decision-Record des Agenten.
CONSTITUTION_KEYS: tuple[str, ...] = ("protected_player_ids",)

#: Offene Kalibrierungen — ehrliche Liste dessen, was noch nicht gegen Ergebnisse
#: gemessen ist (Emils Frage 23.08.: „Verbessert sich die KI anhand der Ergebnisse?").
OPEN_CALIBRATIONS: tuple[str, ...] = (
    "Mannschaftsengine: besetzte Plätze, tatsächliche Einsätze, Kontostand zur Frist, "
    "Punkte, verpasste Alternativen und Notverkäufe im vorwärts protokollierten Betrieb messen.",
    "Backtest: Punkte-Ridge verbessert den historischen Zukunftstest, Minuten-Ridge noch nicht. "
    "Kandidat bleibt inaktiv; bisherige Daten erlauben keine trainierte Startwahrscheinlichkeit.",
    "Auktionsmodell: Gewinnerpreise erlauben einen Kandidatenfit, aber keine nachgewiesene "
    "Zuschlagskalibrierung. Eigene bestätigte Ergebnisse prospektiv getrennt erfassen.",
    "Vier Spieltage sind eine konstante Formprojektion. Gegner, Belastung und "
    "Startwahrscheinlichkeit benötigen zusätzliche datierte Labels und separate Zukunftstests.",
    "Marktwertmodell für 1/3/7 Tage trainiert: Ridge schlechter als die einfache Trendbasis. "
    "Kandidat bleibt inaktiv; Trend und Unsicherheit werden getrennt von Kaufgeld ausgewiesen.",
    "Technische Tages-, Einzelpreis- und Marktwertgrenzen gelten weiter; ihre Wirkung auf "
    "verpasste Mannschaftsalternativen prüfen, nicht als wirtschaftliches Optimum ausgeben.",
    "Gebotsregel V3: Koeffizienten aus sechs Wochen Liga-Käufen (bis 24.09.2026), fest "
    "eingetragen. Prospektiv messen: Zuschlagsquote und Buchwert nach 7 Tagen je Gebot. "
    "Eine Neuschätzung braucht dauerhaft gespeicherte Liga-Transfers und unverkaufte Listings. "
    "Ein geplantes Vorhaben behält seinen Betrag über das 22-Uhr-Update; der wirksame "
    "Aufschlag weicht dann vom geplanten ab (Neubepreisung offen, Frage an Emil).",
    "Jev-Rolle aus kicker-Artikeln: Schwelle 0,95 an 33 Paaren vom 29.09.2026 geprüft "
    "(echte Ausfälle 0,95–1,00, Fehlwerte bis 0,83; Rauschen ±0,05, gebündelt gegen einzeln "
    "bis 0,4). Prospektiv messen: gesperrte Ziele, die doch spielten, und freigegebene, die "
    "fehlten. „bank“ sperrt nicht (0,65–0,89 für denselben Anreißer).",
    "News-Radar (Rückkehr als Kaufsignal): Schwelle 0,95 an 22 Paaren vom 30.09.2026 "
    "geprüft (klare Rückkehrer 0,99–1,00, Frage-Titel 0,96–0,99, Neuzugang höchstens 0,94 — "
    "knapp). Prospektiv messen: Marktwert der gemeldeten Rückkehrer nach 3 und 7 Tagen.",
)



def load_parameter_history() -> list[dict[str, Any]]:
    """Einträge aus ``parameter_history.json`` (im Paket), neueste zuerst.

    Ungültige Einträge (keine dict, fehlende Pflichtfelder) werden übersprungen;
    Reihenfolge bei gleichem Datum = Dateireihenfolge.
    """
    try:
        text = (resources.files(__package__) / HISTORY_FILE).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError, TypeError):
        return []
    raw_entries = data.get("entries", []) if isinstance(data, dict) else data
    entries: list[dict[str, Any]] = []
    for item in raw_entries if isinstance(raw_entries, list) else []:
        if not isinstance(item, dict):
            continue
        if not all(k in item for k in ("date", "parameter", "new")):
            continue
        entries.append(
            {
                "date": str(item["date"]),
                "parameter": str(item["parameter"]),
                "old": None if item.get("old") is None else str(item["old"]),
                "new": str(item["new"]),
                "reason": str(item.get("reason") or ""),
                "commit": str(item.get("commit") or ""),
                # Decision-Record (Emil 23.08.): Erwartung + Pruefung, optional
                "expect": str(item.get("expect") or ""),
                "review": str(item.get("review") or ""),
                # Lernzyklus (26.08.), optional: Beleg + Mechanismus-Ausnahme
                "beleg": str(item.get("beleg") or ""),
                "einzelfall_ausnahme": bool(item.get("einzelfall_ausnahme") or False),
                # Wer geaendert hat; fehlt die Angabe, war es der Orchestrator.
                "quelle": str(item.get("quelle") or "orchestrator"),
            }
        )
    # stabil: gleiches Datum behält die Dateireihenfolge
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def _outcome_slo_text() -> str:
    from kickbase_agent.worker import outcome_guard as og

    return (
        "offene Plaetze erlaubt: 3 (> 7 Tage), 2 (bis 96 h), 1 (bis 72 h), 0 (ab 72 h); "
        f"Deckung Konto - eigene Gebote >= {KICKOFF_MIN_BALANCE} EUR sofort; Konto unter "
        f"Puffer > {int(og.LOW_BALANCE_GRACE.total_seconds() // 3600)} h; Statuswechsel ab "
        f"{int(og.BIG_PLAYER_SHARE * 100)} % Teamwert; eine Karte je Zustandswechsel"
    )


def _injury_severity_text() -> str:
    from kickbase_agent.scouts.ligainsider import severity_judge as sj

    return (
        f"TypeSafe {sj.MODEL} je LigaInsider-Grund (Dauer, bestimmt, gesicherte Diagnose); "
        f"LONG (Sofortverkauf) nur bei P(Monate) >= {sj.LONG_MIN_P_MONTHS} und Diagnose >= "
        f"{sj.LONG_MIN_P_DIAGNOSIS}, SHORT bei P(Tage + 1-3 Wochen) >= {sj.SHORT_MIN_P}, "
        f"sonst UNKNOWN (bestimmt >= {sj.MIN_P_SPECIFIC}, Sicherheit >= {sj.MIN_CONFIDENCE}); "
        f"harte Obergrenze {sj.TIMEOUT_S:g} s, nach Ausfall {sj.FAILURE_PAUSE_S:g} s Pause, "
        "dann Wortsuche; Anpfiff-Kaskade bleibt bei der Wortsuche"
    )


def _news_role_text() -> str:
    from kickbase_agent.intel import role_judge as rj

    return (
        f"TypeSafe {rj.MODEL} je (Kaufkandidat oder eigenes geplantes Vorhaben, kicker-Artikel "
        f"der letzten {int(rj.NEWS_WINDOW.total_seconds() // 3600)} h nach dem letzten Spiel "
        "des Vereins mit seinem Nachnamen): startet, bank, fehlt oder kein_hinweis fuer das "
        f"naechste Bundesligaspiel; es zaehlt der juengste Artikel mit P(Label) >= "
        f"{rj.MIN_P_ROLE}; nur {'/'.join(sorted(rj.BLOCKING))} sperrt (ein klares bank oder "
        "startet hebt ein aelteres fehlt auf); Live-Gebote, manuelle/angepasste Vorhaben und "
        f"Manager-Listings werden nicht beurteilt; hoechstens {rj.MAX_PAIRS} Paare je "
        f"Anfrage, harte Obergrenze {rj.TIMEOUT_S:g} s, nach Ausfall {rj.FAILURE_PAUSE_S:g} s "
        "Pause; ohne Urteil keine neue Sperre"
    )


def _news_radar_text() -> str:
    from kickbase_agent.intel import news_signal as ns
    from kickbase_agent.intel import role_judge as rj
    from kickbase_agent.worker import news_radar as nr

    return (
        f"08:11 rein lesend (frische Anreißer nur im Speicher, Rollenfragen ohne Ausfall-Pause "
        f"für die Engine): Kader-Warnung = eigener Spieler mit 'fehlt' >= {rj.MIN_P_ROLE}; "
        f"Kaufsignal = Kickbase-Listing mit TypeSafe {ns.MODEL} '{ns.RETURN}' >= "
        f"{ns.MIN_P_RETURN} (Rückkehr nach Verletzung oder Sperre, kicker-Anreißer der letzten "
        f"{int(ns.SIGNAL_WINDOW.total_seconds() // 3600)} h nach dem letzten Spiel des Vereins; "
        f"der jüngste eindeutige Artikel zählt, ein jüngerer Ausfall ab {ns.MIN_P_CANCEL} hebt "
        "auf; 'fehlt' im nächsten Spiel wird vermerkt); Tageszusammenfassung höchstens "
        f"{nr.SUMMARY_MAX_SQUAD} Kader- und {nr.SUMMARY_MAX_BUY} Kaufzeilen, der Bericht zeigt "
        "alle; kein Vorhaben, Kauf nur auf Emils Auftrag je Spieler"
    )


def _trade_radar_text() -> str:
    from kickbase_agent.intel import role_judge as rj
    from kickbase_agent.trading import trade_book as tb
    from kickbase_agent.worker import trade_radar as tr

    lead_gap = int(tb.SALE_LEAD_GAP.total_seconds() // 3600)
    lead = int(tb.SALE_LEAD.total_seconds() // 3600)
    return (
        f"08:12 und 22:35 rein lesend: Kaufkandidat = Kickbase-Auktion (kein Manager-Listing, "
        f"kein eigener Spieler, kein laufendes Gebot), fit und KB-Stufe 1-3, MW "
        f"{tr.MIN_MV // 1000} k bis {tr.MAX_MV // 1_000_000} M, Anstieg >= "
        f"{tr.MIN_RISE_3D * 100:.0f} % in 3 Tagen und letzter Tagesschritt > 0 (Verlauf von "
        f"Kickbase, hoechstens {tr.MAX_HISTORY_LOOKUPS} Abrufe in {tr.HISTORY_BUDGET_S:g} s, "
        f"DB nur Rueckfall); Hoechstgebot = MW x (1 + min(Deckel, {tr.PREMIUM_SHARE} x "
        "Median-Anstieg der Steigungsstufe 5-10/10-15/>=15 % ueber die Haltezeit, unter "
        f"{tr.MIN_EVIDENCE} Spielern die Preisklasse, ein Wert je Spieler, Daten der letzten "
        f"{tr.STATS_LOOKBACK_DAYS} Tage)); Haltezeit = Updates bis zum Verkaufstermin "
        f"(T-{lead_gap} h vor der Spieltags-Frist bei Kader ohne Handelsspieler < 11, sonst "
        f"T-{lead} h), hoechstens {tr.FORWARD_DAYS}, ohne Update kein Kandidat; Deckel billig "
        f"(< {tr.CHEAP_LIMIT // 1_000_000} M) {tr.PREMIUM_CAP['billig'] * 100:.0f} %, mittel "
        f"{tr.PREMIUM_CAP['mittel'] * 100:.0f} %; unter {tr.MIN_EVIDENCE} Spielern auch in der "
        f"Klasse {tr.FALLBACK_PREMIUM * 100:.0f} %, Median <= 0 kein Kauf; Jev-Rueckkehrer aus "
        "dem News-Radar zuerst; Jev-Check: Rollenfrage der Engine je Kandidat, klares fehlt >= "
        f"{rj.MIN_P_ROLE} nimmt ihn heraus (Zweck handel, ohne Ausfall-Pause, frische und "
        "gespeicherte Anreisser); Handelsspieler aus dem Handelsbuch (juengstes echtes Gebot "
        "vom Handels-Autopiloten); verkaufen nach dem ersten fallenden Update (tfhmvt, sonst "
        "eigener Tagesschritt; nie mvt), alle am Verkaufstermin; Handelsrahmen "
        f"{tr.EXPOSURE_CAP // 1_000_000} M (Kaufpreise + Handelsgebote); bieten und verkaufen "
        "tut der Handels-Autopilot"
    )


def _trade_autopilot_text() -> str:
    from kickbase_agent.worker import trade_autopilot as ta

    sell_veto = int(ta.SELL_VETO.total_seconds() // 60)
    lead = int(ta.FORCED_SALE_LEAD.total_seconds() // 3600)
    return (
        ":17 und :47: Gebots-Vorhaben aus dem juengsten Radar-Bericht (hoechstens "
        f"{int(ta.RADAR_MAX_AGE.total_seconds() // 3600)} h alt) zum Radar-Hoechstgebot, "
        "hoechstens 130 % MW (Stufe Handel der Schreibstelle), Kaufpruefung Zweck handel "
        "hoechstens 3 h gueltig und laufend erneuert; geraeumt bei Marktwert-Update seit dem "
        "Radar, ohne Radar-Nennung oder ohne Rahmen; Rahmen "
        f"{ta.TRADE_CAP_EUR // 1_000_000} M (Kaufpreise + Handelsgebote), hoechstens "
        f"{ta.MAX_OPEN_TRADES} Handelsspieler/-gebote, {ta.MAX_TRADE_BIDS_PER_DAY} Handelsgebote "
        f"je Tag, {ta.OVERDRAFT_MARGIN // 1_000_000} M Abstand zur 33-%-Grenze, Transfers bis "
        f"{ta.TRADE_TRANSFER_STOP}/200; Deckung zum Anpfiff: Konto minus alle Gebote plus "
        "1,0 x MW des Handels minus (Betrag - 1,0 x MW) je neuem Gebot bleibt ueber "
        f"{KICKOFF_MIN_BALANCE} EUR; kein Handel auf einer Position unter dem Minimum des "
        "Stammkaders; kein neues Handelsgebot, solange die Engine ein Gebot plant, eines "
        f"laeuft oder ihr Zyklus rechnet; Sofortverkauf {sell_veto} min nach dem ersten "
        f"fallenden Update, alle am Verkaufstermin (angelegt {lead} h vorher); Handelsspieler "
        "nie in der Elf; AGENT_TRADE_AUTOPILOT=0 haelt nur das Kaufen an"
    )


def _bid_premium_text() -> str:
    from kickbase_agent.decision import bid_premium as bp

    return (
        f"{bp.BID_PREMIUM_VERSION}: nur Kickbase-Auktionen, solange die Zuschlagschance "
        f"unbekannt ist; Aufschlag = min(exp({bp.WIN_CURVE_INTERCEPT} + {bp.WIN_CURVE_STEP} x "
        f"Schritt {bp.WIN_CURVE_SIZE:+} x ln(MW/5 M)) - 1, {bp.GROWTH_INTERCEPT} + "
        f"{bp.GROWTH_STEP} x Schritt + {bp.GROWTH_MARGIN}), hoechstens {bp.PREMIUM_CAP}, "
        f"mindestens {bp.FALLER_PREMIUM} (Schritt <= {bp.FALLER_STEP}: genau "
        f"{bp.FALLER_PREMIUM}); Schritt = tfhmvt / (MW - tfhmvt), begrenzt "
        f"{bp.STEP_FLOOR}..{bp.STEP_CEILING}; ohne beobachtetes tfhmvt Mindestpreis; nie ueber "
        "Obergrenze, Einzelrahmen und technischen Kappen; gekuerzt, wo es dem Paket einen "
        "Platz oder Punkte naehme oder einen gleich guten, guenstigeren Ersatz gleichstellte; "
        "geplante Vorhaben behalten Betrag und Herleitung, Live-Gebote ihren Betrag"
    )


def _intent_via_whitelist_text() -> str:
    from kickbase_agent.server.routes.intents import INTENT_VIA_WHITELIST

    return ", ".join(sorted(INTENT_VIA_WHITELIST)) + "; leer = mcp (gezaehlt); sonst 403"


def current_parameters() -> dict[str, Any]:
    """Heute gültige Werte: Guardrails-Defaults (Vertrag: GUARDRAIL_KEYS) + die
    Modul-Konstanten der Historie (FEED_MAX_ITEMS, Signal-Gewichte, Community-Boost,
    Preflight/TTL, Autopilot-Zone) unter ihren Code-Namen."""
    g = DEFAULT_GUARDRAILS
    params: dict[str, Any] = {key: getattr(g, key) for key in GUARDRAIL_KEYS}
    params.update({key: getattr(g, key) for key in _EXTRA_GUARDRAIL_KEYS})
    params.update(
        {
            # Verfassung: als Text, nicht als Wert — sie ist nichts, woran man
            # dreht. Sortiert, damit die Anzeige stabil bleibt.
            "protected_player_ids": (
                ", ".join(sorted(protected_player_ids()))
                + " (Verfassung — nur über PROTECTED_PLAYER_IDS änderbar)"
                if protected_player_ids()
                else "(leer — kein Spieler geschützt)"
            ),
            "FEED_MAX_ITEMS": FEED_MAX_ITEMS,
            "BASE_POINTS_WEIGHT": BASE_POINTS_WEIGHT,
            "FORM_WEIGHT": FORM_WEIGHT,
            "MATCHUP_WEIGHT": MATCHUP_WEIGHT,
            "TREND_WEIGHT": TREND_WEIGHT,
            "TREND_1D_WEIGHT": TREND_1D_WEIGHT,
            "MAX_BOOST": MAX_BOOST,
            "TIP_TTL_DAYS": TIP_TTL_DAYS,
            "PROPOSAL_TTL": f"{int(PROPOSAL_TTL.total_seconds() // 60)} min",
            "PREFLIGHT_MAX_PRICE_DRIFT": PREFLIGHT_MAX_PRICE_DRIFT,
            "PREFLIGHT_MAX_VALUE_DRIFT": PREFLIGHT_MAX_VALUE_DRIFT,
            "agent_trading_max_per_player": _trading_max_per_player(),
            # Spieltags-Schutzfenster (decision/kickoff_freeze, 24.08.)
            "kickoff_freeze.BID_FREEZE_MINUTES": BID_FREEZE_MINUTES,
            "exposure_guard_hours": EXPOSURE_GUARD_HOURS,
            "kickoff_freeze.EXPOSURE_GUARD_HOURS": EXPOSURE_GUARD_HOURS,
            "kickoff_freeze.KICKOFF_MIN_BALANCE": KICKOFF_MIN_BALANCE,
            "lineup.emergency_bench_window_hours": int(
                EMERGENCY_BENCH_WINDOW.total_seconds() // 3600
            ),
            # Accept-Pfad (worker/intents, 24.08.)
            "intents.ACCEPT_VETO_WINDOW": (
                f"{int(ACCEPT_VETO_WINDOW.total_seconds() // 60)} min"
            ),
            # Listungspreis + Kader-Floor der Annahme (decision/policies, 16.09.):
            # unter dem eigenen Preis nie autonom (nur Meldung); Floor 11 nur im
            # T-120-Fenster wie im Executor, sonst Positions-Minima (L-095).
            "accept.asking_price_and_squad_floor": (
                f"Listungspreis = Untergrenze; Floor {MIN_SQUAD_SIZE} nur bis "
                f"T-{EXPOSURE_GUARD_HOURS} h, sonst Positions-Minima; ab Preis "
                "manuelles Vorhaben mit emil_go"
            ),
            # Paket 1 der Turnaround-Studie (cli/plan, 21.09.2026)
            "plan.manager_listings_autonomous": (
                "abgelehnt; laufende Manager-Gebote reservieren Geld, zaehlen keinen "
                "Platz; Veto-/Fehlschlag-Ziele vor dem Optimierer ausgeschlossen"
            ),
            "plan.planning_cash_buffer_eur": KICKOFF_MIN_BALANCE,
            "plan.completion_bid_fraction": (
                f"{COMPLETION_BID_FRACTION} bei einem offenen Platz, "
                f"{COMPLETION_BID_FRACTION_TWO_OPEN} bei zwei, "
                f"{COMPLETION_BID_FRACTION_MANY_OPEN} ab drei"
            ),
            "plan.bid_premium": _bid_premium_text(),
            "scout.injury_severity": _injury_severity_text(),
            "plan.news_role_check": _news_role_text(),
            "radar.news_signal": _news_radar_text(),
            "radar.trade_signal": _trade_radar_text(),
            "radar.trade_autopilot": _trade_autopilot_text(),
            "plan.role_proof": (
                f"ab {ROLE_PROOF_MIN_VALUE // 1_000_000} M: KB-Stufe <= 2 und >= "
                f"{ROLE_PROOF_MIN_MINUTES} min in den letzten {ROLE_PROOF_MATCHDAYS} "
                "beobachteten Spieltagen; sonst 'Rollenbeleg fehlt'"
            ),
            # Planungs-Dispo als Tausch-Finanzierung (decision/overdraft, Emil
            # 21.-23.09.2026, befristet).
            "plan.planning_overdraft": (
                f"bis {PLANNING_OVERDRAFT_UNTIL:%d.%m.%Y %H:%M} Berlin nur fuer Tausch-Kaeufe: "
                "besserer Starter ueber das freie Geld hinaus, wenn derselbe Plan den "
                "bedingten Sofortverkauf des ersetzten, ungeschuetzten Spielers koppelt; "
                f"netto Preis - MW x {SWAP_SALE_CREDIT_PERCENT} % <= Konto - Gebote - "
                "10.000 EUR, brutto innerhalb der Kickbase-Kaufkraft (Teamwert/3), "
                "Auktion endet vor T-120; Luecken nur aus echtem Geld; danach 0"
            ),
            # Tauschbremse (Emil 12.10.2026, cli/plan.py).
            "plan.swap_brake": (
                f"kein Tausch-Verkauf in den ersten {SWAP_HOLD_AFTER_PURCHASE.days} Tagen nach "
                "dem eigenen Kauf (Dispo und gewoehnlich, auch geplante Paare ohne Gebot); "
                "Dispo-Tausch nur, wenn Punktgewinn x Teamwert / Prognosepunkte der Elf > "
                "Gebot - Marktwert"
            ),
            # Paket 1, P0-4/P0-8 (21.09.2026): Ergebnis-Waechter und Absender-Stolperdraht.
            # Lazy-Importe vermeiden einen Importzyklus ueber server/ und worker/.
            "guard.outcome_slo": _outcome_slo_text(),
            "api.intent_via_whitelist": _intent_via_whitelist_text(),
            # Spielprofil-Dimensionen (intel/dimensions, 24.08.) — Anzeige-only
            # bis zur SP1-Kalibrierung, dann eigener Decision-Record je Wirkung.
            "intel.dimensions": "6 Skalen, Anzeige-only (bis SP1-Kalibrierung)",
            # Zentrale Invarianten an der Schreibstelle (Codex-P1, 24.08.)
            # Notbremse: Reihenfolge der Notverkaeufe (27.08., L-080)
            "kickoff_guard.purge_cascade_order": (
                "S1 Trading (Momentum) · S2 Bank (MV) · S3 Elf: erst prob >= 4, "
                "dann MV · TW zuletzt"
            ),
            "executor.central_invariants": (
                "Overpay-Kappe · Tages-Caps · Vereinslimit · Starter-Schutz · "
                "Kader-Deckung · T-120-Fenster · 9-Aktionen-Preflight-Gate "
                "(bid/accept/sell/delist/decline/lineup; kickoff-* ausgenommen)"
            ),
            # Pfand-Hebel-Begleitregeln (01.09.) — Ist-Zustand der beiden
            # Preflight-Praezisierungen, damit die Decision-Records ihren
            # heutigen Wert unter demselben Schluessel finden.
            "preflight.pbaa_ableitung": (
                "0 verifizierte eigene Gebote ⇒ after_bids = Konto (exakt); "
                "Gebote vorhanden ohne pbaa ⇒ fail-closed"
            ),
            "preflight.mv_kappen_haertung": (
                "Gebot ohne ermittelbaren Marktwert ⇒ refused "
                "(Overpay-Kappe nicht pruefbar)"
            ),
            "freeze.selbst_verfall": (
                "gebaut, noch nicht verdrahtet: Freeze aus einem vergangenen Tag "
                "wird freigegeben, wenn die Ausloeser heute nicht mehr zutreffen "
                "(Deploy-Ausloeser ausgenommen)"
            ),
            "sell.kader_fenster": (
                "11er-Floor ab T-120 vor dem Anpfiff (autonome Pfade); "
                "sonst Positions-Minima 1 TW/3 ABW/2 MF/1 ST — manuelle Verkaeufe "
                "(mcp-*, -manual) auch im Fenster"
            ),
            "sell.swap_pair": (
                "Wertverlierer MIT Startelf-Pfad (LI nicht Bank, Trend <= "
                "sell_trend_score_max) wird getauscht: Gebot auf den besten Ersatz "
                "derselben Position (Kauf-Score >= Schwelle, Elf-Score >= seinem) + "
                "Sofortverkauf erst nach Besitz genau dieses Ersatzes; gemeinsame "
                "kuratierte Zielliste und Finanzierung begrenzen die Anzahl"
            ),
            "curated_purchases": "Abgelöst durch season_engine; keine starre Zwei-Ziel-Regel.",
            "season_engine": (
                "Gemeinsame Optimierung aller neun Formationen: zuerst besetzbare Plätze, "
                "dann projizierte Punkte, dann verbleibendes Geld. Individuelle Gebote "
                "vergleichen finanzierte Mannschaften und Ersatzwege; unbekannte "
                "Zuschlagschance bleibt unbekannt. Kaufprüfung höchstens "
                f"{int(REVIEW_LIFETIME.total_seconds() // 60)} min gültig. "
                "Kandidatenmodelle werden nur nach getrennt bestandener Prüfung geladen."
            ),
            "plan_financing_and_roster_value": (
                "Echtes Konto minus alle offenen Verpflichtungen; angenommene Verkäufe "
                "finanzieren keine neuen Käufe. Verpflichtungen sind kein Eigentum. "
                "Die gesamte geplante Kaufsumme wird zur Ausführung erneut geprüft."
            ),
            "sell.after_squad_size": (
                "Sofortverkaufs-Vorhaben mit sources.after_squad_size warten, bis der "
                "Kader die Groesse erreicht hat (Tausch: erst Ersatz, dann Abgang)"
            ),
            "starter_coverage_definition": (
                "LI STARTER ohne KB-4/5-Widerspruch oder KB-Stufe 1/2 plus st=0 "
                "ohne LI-Veto; fehlende Fitness, ungueltige Werte, offen, Bank/OUT, "
                "Verletzte und eigene Listings zaehlen nicht"
            ),
            "need_bid_opening": (
                "Engine: individueller Mannschaftsvergleich, kein pauschaler Zielaufschlag. "
                "Legacy-Policies behalten ihre alten Werte außerhalb dieses Pfades."
            ),
            "formation_need_definition": (
                "Alle neun Formationen mit aktuellen Kosten gemeinsam lösen; "
                "besetzbare Plätze und belastbare Einsätze getrennt anzeigen."
            ),
            "sell.listing_failed": (
                "Eigenes Manager-Listing >= 48 h ohne ein einziges Angebot ⇒ "
                "Sofortverkaufs-Vorhaben (nur LI-Bank, Schutzliste + "
                "Kader-Deckung, 45-min-Veto-Karte)"
            ),
        }
    )
    # Stellschrauben aus knowledge/tuning.json (autonomer Lernzyklus, Stufe D):
    # Sie tragen ihre Namen selbst — damit findet das Protokoll zu jedem
    # Decision-Record den heutigen Ist-Wert unter demselben Schluessel.
    params.update(_tuning_parameters())
    return params


def _tuning_parameters() -> dict[str, Any]:
    """Ist-Werte der versionierten Stellschrauben; {} wenn die Datei nicht ladbar ist."""
    try:
        from kickbase_agent.knowledge.tuning import as_parameters

        return dict(as_parameters())
    except Exception:  # noqa: BLE001 — Zusatzinfo, darf nie die Antwort sprengen
        return {}


def _trading_max_per_player() -> int | None:
    """Autopilot-Zone aus den Settings (Env kann den Default 1,5 M überschreiben);
    None, wenn die Settings hier nicht ladbar sind (z. B. ohne Pflicht-Env)."""
    try:
        from kickbase_agent.config import get_settings

        return int(get_settings().agent_trading_max_per_player)
    except Exception:  # noqa: BLE001 — Zusatzinfo, darf nie die Antwort sprengen
        return None


def open_calibrations() -> list[str]:
    return list(OPEN_CALIBRATIONS)


# ------------------------------------------------------- Decision-Record schreiben


class RecordError(ValueError):
    """Der Decision-Record erfuellt seine Pflichten nicht (Grund, Beleg, Commit, Name)."""


#: Kuerzeste akzeptierte Commit-Referenz (git-Kurz-SHA).
MIN_COMMIT_CHARS = 7


def record_change(
    *,
    parameter: str,
    old: Any,
    new: Any,
    reason: str,
    commit: str,
    expect: str = "",
    review: str = "",
    beleg: str = "",
    einzelfall_ausnahme: bool = False,
    quelle: str = "orchestrator",
    date: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Eine Parameter-Aenderung ins Entscheidungs-Protokoll schreiben.

    Die Pflichten sind der Sinn der Sache, nicht Formalismus: **Grund** (warum),
    **Beleg** (worauf gestuetzt) und **Commit** (wo nachlesbar). Fehlt eines,
    entsteht kein Eintrag — dann ist es keine belegte Aenderung, sondern eine
    stille. Der Name muss ausserdem ein bekannter Parameter sein: Ein Record fuer
    etwas, das es nicht gibt, waere ein Protokoll ohne Gegenstand.

    Aufgerufen wird das aus dem Selbstumbau (agent/self_change.py), NACHDEM der
    Commit existiert — deshalb ist `commit` ein Pflichtfeld und keine Zusage.
    """
    name = (parameter or "").strip()
    if not name:
        raise RecordError("Ohne Parameter-Namen kein Record")
    if name not in current_parameters():
        raise RecordError(
            f"Unbekannter Parameter {name!r} — Records gibt es nur fuer echte "
            "Stellschrauben (sonst gehoert die Erkenntnis nach LEARNINGS.md)"
        )
    if not (reason or "").strip():
        raise RecordError("Ohne Grund kein Record")
    if not (beleg or "").strip():
        raise RecordError("Ohne Beleg kein Record (L-ID oder surprise:<ref>)")
    if len((commit or "").strip()) < MIN_COMMIT_CHARS:
        raise RecordError("Ohne Commit-Referenz kein Record")

    entry = {
        "date": date or datetime.now(UTC).date().isoformat(),
        "parameter": name,
        "old": None if old is None else str(old),
        "new": str(new),
        "reason": reason.strip(),
        "expect": expect.strip(),
        "review": review.strip(),
        "beleg": beleg.strip(),
        "einzelfall_ausnahme": bool(einzelfall_ausnahme),
        "quelle": (quelle or "orchestrator").strip() or "orchestrator",
        "commit": commit.strip(),
    }

    target = path or Path(str(resources.files(__package__) / HISTORY_FILE))
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecordError(f"Historie nicht lesbar: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise RecordError("Historie hat nicht die erwartete Form")
    # Neueste zuerst — dieselbe Ordnung, die der Loader garantiert.
    data["entries"].insert(0, entry)
    target.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return entry
