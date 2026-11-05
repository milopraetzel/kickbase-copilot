"""Datenschicht des Kickbase-Cockpits v3 — reine Daten, kein HTML.

`collect()` liefert einen dict; die Keys sind ein CONTRACT, auf den sich die
Renderer-Module (pages_core/pages_info) exakt verlassen:

- stand: str            "20.08.2026 14:30" (lokale Zeit)
- kickoff: str          "Fr 28.08. 20:30" (lokal, deutscher Wochentag) | "—"
- budget: int           Kontostand aus dem Plan
- teamwert: int         Summe der Kader-Marktwerte
- trades_used: int      verbrauchte Transfers der Halbserie
- trade_limit: int      internes Liga-Limit (200er-Regel)
- mine: dict|None       {rank, points, prev_rank, prev_points} — eigene Zeile
- manager_squads: list[dict]  Kader ALLER Mitspieler (Emils Wunsch 28.08.), je Manager
                        {user_id, name, img, me, rank, punkte, punkte_spieltag, teamwert,
                         gewinn (prft: REALISIERT aus Transfers), buchgewinn (Summe mvgl:
                         Buchwert des aktuellen Kaders), punkte_gesamt (ueber ALLE Saisons),
                         punkte_saison (diese Saison), kader, in_elf, auf_bank,
                         gelistet, spieler: [{pid, name,
                         pos, position, team_id, mv, mv24, mv_trend, gewinn, punkte,
                         avg_points, fit, in_elf, lineup_order, gelistet, img}]}
                        — Quelle /v4/leagues/{lg}/managers/{uid}/squad + /dashboard,
                        live geprobt 28.08. Leer, wenn die Abfrage scheitert.
- ranking: list[dict]   {user_id, name, rank, points, prev_rank, prev_points,
                         me: bool} — sortiert nach rank
- bids: list[dict]      {name, player_id, img, amount: int, hours_left: float,
                         expires_in_s: int, expired: bool, seller: str ("" =
                         Kickbase-Listing), listing_price: int,
                         listing_type: "kickbase"|"manager"}
                        expired = Listing abgelaufen, Zuschlag ausstehend;
                        Gebote-Panel der Zentrale (Live-Zone) + /api/bids-Spiegel
- market: list[dict]    {pid, name, pos: "TW"|"ABW"|"MIT"|"STU", mv: int,
                         score: float, hours_left: float, ours: bool,
                         bid_ours: int|None, listing_type: "kickbase"|"manager"
                         (benannter Verkäufer = Manager-Listing, live 23.08.),
                         mv24: int (24-h-MW-Delta aus `tfhmvt`, 0 = nichts
                         gemeldet), mv7d: int|None (MW-Delta über >= 7 Tage aus
                         der DATIERTEN Snapshot-Historie; None = Historie reicht
                         nicht oder ist veraltet — Renderer zeigt „—", nie 0),
                         spark: list[int] (MV-Verlauf
                         letzte 30 Tage, ältester zuerst, kann leer sein)}
                         + DETAIL-KEYS (s. unten) + FAIR-KEYS — ALLE
                         Marktspieler, Score absteigend
- fair: dict            Fair-Value-Radar (Markt-Tab): {"curve": {a, b, n}|None
                        (Potenz-Fit mv ≈ a·ap^b, intel.fair_value), "cloud":
                        [{name, ap, mv, pos, team_id, player_id, img}]} —
                        Kontext-Wolke aller 18 Erstliga-Team-Profile (ap ≤ 0
                        gefiltert); ohne Client/bei Totalausfall
                        {"curve": None, "cloud": []}. market- UND kader-Zeilen
                        tragen FAIR-KEYS: fair_eur: int|None, fair_fmt:
                        str|None, fair_delta_pct: int|None ((fair−mv)/mv·100,
                        gerundet); Zeilen ohne Detail-Ø bekommen ap aus der
                        Wolke gejoint (Key "ap")
- teamwert_spark: list[int]  Teamwert-Verlauf (Tages-Summen, Forward-Fill)
- buys: list[dict]      {name, score, amount: int, approval: bool, rationale}
- sells: list[dict]     {name, score, way: "list"|"sell", amount: int}
- profiteurs: list[dict] {pid, name, mv: int, same_position: bool, injured}
- tips: list[dict]      {player, weight: float, source, note}
- kader: list[dict]     {pid, name, pos, mv: int, score: float, kauf_score,
                         status: "starter"|"bench"|"out"|"unknown", grund,
                         elf: bool, position: int, mv24: int, mv7d: int|None
                         (beide wie bei market), spark: list[int]}
                         + DETAIL-KEYS (s. unten) — Score absteigend
- detail_coverage: dict {kader: n_ok, kader_total: n, market: n_ok,
                         market_total: n} — wie viele Zeilen ihre Detail-Keys
                         tragen (Transparenz, wenn Detail-Calls scheitern)
- formation: str        empfohlene Formation, z. B. "4-3-3" (kann "" sein)
- quarantines: list[str] unsichere LigaInsider-Namens-Matches
- actions: list[dict]   {ts: "20.08 14:30", type, status, rule_id, rationale
                         (VOLL), amount: int|None, dry_run: bool,
                         player: str ("" wenn unbekannt),
                         prev_amount: int|None + prev_ts: str (Re-Bid: das
                         jüngste ältere Gebot auf denselben Spieler),
                         context: str (Gegenpartei: Markt/Kickbase/Mitspieler)}
                         — letzte 20, neueste zuerst
- news: list[dict]      {title, source, ts: "20.08.", url: str|None} — letzte 14
- feed: list[dict]      {who, verb: "kauft"|"verkauft", player, amount: int}
- feed_events: list[dict]  ROHE Activity-Feed-Items (t/dt/data, max. 1000) — Quelle
                        für intel.transfers.league_events im Zentrale-Protokoll
                        (kein zweiter Kickbase-Call: derselbe Feed-Abruf)
- scoring: dict         Erklärseite: weights (Signal→Gewicht), multipliers
                        (LI-Status→Faktor), tip_boost, alle Guardrails-Werte
                        (buy_threshold, sell_threshold, overpay,
                        overpay_conviction, psych_offset, max_bid_pct,
                        approval_threshold, trade_limit, listing_floor,
                        accept_offer_floor, daily_buy_cap, daily_sell_cap)
                        sowie params: list[dict] {key, label, value_fmt, group,
                        pct: float|None, erklaerung} für jeden Guardrail.
- intents: list[dict]   Steuerzentrale (Tabelle `intents`, neueste 50, id absteigend):
                        {id, created_at, updated_at, execute_at, deadline: ISO-UTC
                         ("+00:00") | None, kind, player_id, player_name, team_id,
                         position: int, pos: "TW"|"ABW"|"MIT"|"STU"|"", club: str,
                         amount: int, amount_fmt: "14,30 M", big: bool (≥ Approval-
                         Schwelle), rationale, sources: dict, listing_type, status,
                         emil_note, adjusted_from, executed_action_id, result,
                         seconds_left: int|None, deadline_seconds_left: int|None}
                         — gleiche Keys wie GET /api/intents (Spec §4)
- protocol: list[dict]  letzte 60 transfer_actions, neueste zuerst: {id, ts: ISO-UTC,
                        type, status, rule_id, rationale, amount: int|None,
                        dry_run: bool, player_id, player: str (Name, "" wenn
                        unbekannt), resolved_at: ISO|None, p: float|None (erwartete
                        Zuschlag-Chance 0..1), p_why: str, p_kind: str,
                        backfilled: bool (Erwartung nachtraeglich rekonstruiert,
                        zaehlt in keiner Quote), outcome: "won"|"lost"|"",
                        outcome_at: ISO|""}
- reports: list[dict]   Zone „Berichte" (Tabelle `reports`, neueste 30, OHNE body):
                        {id, created_at: ISO-UTC, kind, title, source, chars: int,
                         preview: str (≤ 200 Zeichen), delivered: bool|None}
                         — gleiche Keys wie GET /api/reports; der Volltext kommt
                         nur per GET /api/reports/{id}
- learnings: list[dict] Zone „Lernen": [{id, art, date, title, text, order, summary,
                        rule, details, status, evidence, evidence_items}] aus
                        knowledge.learnings, angereichert über knowledge.enrich_learnings
                        (leer, wenn das Modul fehlt; ohne Anreicherung bleibt die Rohliste)
- bilanz: dict          Wochen-Kopf der Lern-Bilanz {since, confirmed_week, refuted_week,
                        new_week, active_hypotheses, verteilung{…}} — {} ohne Belege
- to_fix: list[dict]    widerlegte/wackelnde Erkenntnisse (die einzige Liste, die zum
                        Handeln auffordert) — [] heißt: nichts zu korrigieren
- selbstverbesserungen: list[dict]  Emils Tabelle: [{kind, at, wer, quelle, titel, old,
                        new, grund, beleg, fall, learning, commit, einzelfall_ausnahme}]
                        aus knowledge.self_improvement (Parameter-Records + Urteile)
- parameters: dict      {"history": [{date, parameter, old, new, reason, commit, quelle}],
                         "current": {…}} aus knowledge.parameters (leer ohne Modul)
- evaluation: dict      Treffer-Bilanz aus intel.evaluation.evaluate (Vertrag dort:
                        buy_hits/shadow/executed je {1d, 7d}, tips, flips,
                        flips_total_delta_eur, lineup, coverage, generated_at) —
                        {} ohne Modul; None-Werte bleiben None („noch keine Daten")
- open_calibrations: list[str]  offene Kalibrierungen (Konstante/Funktion der
                        Wissens-Module, sonst [])

KI-KEYS (Spieler-Detail → Block „KI-Einschätzung", Emils Wunsch 23.08.):
  Kader- UND Markt-Zeilen: signals: [{name, score: 0..1, weight, reason}] (die
  Engine-Signale des Kandidaten, Kauf-Pfad inkl. Community-Signal), tip: dict|None
  {weight, source, note, boost} (bester Namens-Match der aktiven Tipps, Schwelle
  wie intel.community). Nur Kader-Zeilen: role: "Core"|"Trading" (Trading-
  Holdings laut transfer_actions), buy_price: int|None + bought_at: ISO|None
  (jüngstes ausgeführtes Gebot auf den Spieler → Flip-Bilanz). Markt-Zeilen
  tragen status "out" + grund NUR bei LigaInsider-Ausfall (sonst kein Key —
  Startelf/Bank kennt der Plan für den Markt nicht). Kauf-/Verkaufsvorschläge
  tragen zusätzlich pid (buys/sells) und rationale (sells).

DETAIL-KEYS (Kader- UND Markt-Zeilen, Spec cockpit-v4 §3) — kommen aus dem
Spieler-Detail-Endpoint (Member-Stats) und FEHLEN komplett, wenn das Detail
eines Spielers nicht geladen werden konnte (Renderer: `.get`, dann „—"):

    punkte: int (Saison-Punkte), avg: int (Ø Punkte), tore, vorlagen,
    einsaetze, einwechsel, s11, avg_min (Ø Minuten), gelb, rot: int,
    fit: bool, fit_text: str, fit_since: "05.01.2026" | "",
    form5: [{played: bool, p: int}, …]  (letzte 5, chronologisch wie geliefert),
    spiele: [{day, home, away, home_img, away_img, score: "3:3"|None,
              date: "2026-05-02" | "", cur: bool}, …]  (letzte 3 gespielte
              + das nächste, chronologisch; Clubnamen aus TEAMS, sonst tid),
    nummer: int, vorname: str, nachname: str, club_img: str, mv24: int.

`img`, `club_img`, `home_img`, `away_img` sind VOLLSTÄNDIGE CDN-URLs
(kickbase.b-cdn.net), direkt als <img src> nutzbar.

Alle Zahlenwerte der Erklärseite kommen aus den ECHTEN Konstanten
(intel/signals.py, intel/community.py, scouts/ligainsider/models.py,
decision/guardrails.py) — hier wird nichts hart codiert.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import fields as dc_fields
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from kickbase_agent.cli.cockpit.jev_data import read_jev_safely
from kickbase_agent.cli.plan import build_plan
from kickbase_agent.db.models import (
    CommunityTip,
    Intent,
    NewsItem,
    Player,
    PlayerHistory,
    Report,
    TransferAction,
)
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.decision.lineup_service import recommend_lineup
from kickbase_agent.intel.community import _MATCH_CONFIDENT, MAX_BOOST, load_recent_tips
from kickbase_agent.intel.dimensions import empfehlung, player_dimensions
from kickbase_agent.intel.fair_value import enrich as fair_enrich
from kickbase_agent.intel.fair_value import fit_fair_curve
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.intel.signals import (
    BASE_POINTS_WEIGHT,
    FORM_WEIGHT,
    MATCHUP_WEIGHT,
    TREND_WEIGHT,
)
from kickbase_agent.kickbase import endpoints
from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.kickbase.models import MarketPlayer, MatchdaySummary, PlayerDetail
from kickbase_agent.scouts.ligainsider.matcher import similarity
from kickbase_agent.scouts.ligainsider.models import MULTIPLIER
from kickbase_agent.scouts.ligainsider.teams import TEAMS
from kickbase_agent.trading.orchestrator import load_trading_holdings
from kickbase_agent.worker.kickoff_guard import (
    matchday_deadline,
    next_kickoff,
    running_matchday,
)

# Wissens-Quellen der Zone „Lernen" (knowledge/*, intel/evaluation) entstehen parallel —
# defensiv importiert: fehlt ein Modul, bleiben die Zonen leer statt den Build zu killen.
# Die Namen sind Modul-Globals, damit Tests sie gezielt ersetzen können.
try:
    from kickbase_agent.knowledge.learnings import load_learnings
except ImportError:  # pragma: no cover — Modul (noch) nicht vorhanden
    load_learnings = None
try:
    from kickbase_agent.knowledge.parameters import load_parameter_history
except ImportError:  # pragma: no cover
    load_parameter_history = None
try:
    from kickbase_agent.knowledge.parameters import current_parameters
except ImportError:  # pragma: no cover
    current_parameters = None
try:
    from kickbase_agent.intel.evaluation import evaluate
except ImportError:  # pragma: no cover
    evaluate = None
try:
    from kickbase_agent.knowledge import enrich_learnings
except ImportError:  # pragma: no cover
    enrich_learnings = None
try:
    from kickbase_agent.knowledge.self_improvement import load_self_improvements
except ImportError:  # pragma: no cover
    load_self_improvements = None

logger = logging.getLogger(__name__)

#: Vorschau-Länge eines Berichts in der Zone „Berichte" (Zeichen, Whitespace kollabiert).
REPORT_PREVIEW_CHARS = 200

_POS = {1: "TW", 2: "ABW", 3: "MIT", 4: "STU"}
# Kickbase-Bild-CDN (live verifiziert): Spielerfotos = CDN + "/" + Player.image_path
_CDN = "https://kickbase.b-cdn.net"


def _img(image_path: str) -> str:
    return f"{_CDN}/{image_path}" if image_path else ""
# Bewusst NICHT locale-abhängig (Server können auf C/en_US stehen).
_WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Guardrail-Beschreibungen: key → (Label, Gruppe, Format, Erklärung).
# Format: "pct" (Anteil → Prozent), "eur" (Betrag), "count" (Stückzahl).
_PARAM_INFO: dict[str, tuple[str, str, str, str]] = {
    "max_bid_pct_of_budget": (
        "Max. Einzelgebot (Anteil Budget)", "Kauf", "pct",
        "Kickbase erlaubt Gebote über dem Kontostand (Verschuldung bis ~33 % des "
        "Teamwerts) — die 60 % verhindern Alles-auf-eine-Karte, das Konto selbst "
        "schützt der Anpfiff-Guard.",
    ),
    "bid_overpay_fraction": (
        "Standard-Overpay über MW", "Kauf", "pct",
        "Ein kleiner Aufschlag über dem Marktwert gewinnt Bieterduelle und übersteht "
        "den 22-Uhr-MW-Anstieg — exakte MW-Gebote platzen.",
    ),
    "bid_overpay_conviction": (
        "Conviction-Overpay (Tipp/Job-Erbe)", "Kauf", "pct",
        "Bei Überzeugungszielen (starker Tipp oder direkter Job-Erbe) bieten wir "
        "kontrolliert mehr, weil Rivalen emotional bieten — den Rahmen halten "
        "Gebots-Deckel und Approval.",
    ),
    "bid_psych_offset": (
        "Psychologischer Gebots-Offset", "Kauf", "eur",
        "Ein krummer Extra-Betrag schlägt die glatten Gebote der Rivalen im "
        "Tiebreak — wer 13.337 € drauflegt, gewinnt gegen runde Summen.",
    ),
    "bid_ladder_opening": (
        "Staffel-Erstgebot über MW", "Kauf", "pct",
        "Pay-as-bid: Der Gewinner zahlt SEIN Gebot — wer sofort das volle "
        "Overpay bietet, verschenkt es, wenn niemand mitbietet. Das Erstgebot "
        "steigt niedrig ein, sichert früh den Gleichstand-Tiebreak und spart "
        "Geld; erhöht wird erst kurz vor Listing-Ablauf.",
    ),
    "bid_ladder_raise_hours": (
        "Staffel-Eskalation ab Restzeit (h)", "Kauf", "count",
        "Unter dieser Listing-Restzeit erinnert der Gebots-Wächter per Discord, "
        "das Gebot aufs Ziel-Level (+5 % bzw. +9 % bei Überzeugung) zu erhöhen — "
        "ausgeführt wird die Erhöhung erst nach Emils Freigabe "
        "(»mach den Trade«), nie automatisch.",
    ),
    "max_bid_premium": (
        "Gebots-Deckel Standard (Anteil MW)", "Kauf", "pct",
        "Deckel für gewöhnliche Gebote relativ zum Marktwert — egal wie groß die "
        "Überzeugung, mehr wird nicht geboten. Nur ausdrücklich als Kader-Bedarf "
        "markierte Gebote dürfen darüber (siehe nächster Wert).",
    ),
    "max_bid_premium_need": (
        "Gebots-Deckel bei Kader-Bedarf (Anteil MW)", "Kauf", "pct",
        "Steht ohne diesen Spieler die Formation nicht, gilt der höhere Deckel. "
        "Grund: Bei 120 % Marktwert springt die Zuschlag-Chance von 45 % auf "
        "60 %, bei 135 % auf 80 % — mit dem Standard-Deckel wären diese Stufen "
        "unerreichbar (Ljubičić 26.08.: 108 % geboten, weg für 157 %).",
    ),
    "bid_overpay_need": (
        "Ziel-Aufschlag bei Kader-Bedarf", "Kauf", "pct",
        "Eskalations-Ziel für Spieler, ohne die die Formation nicht steht: "
        "120 % Marktwert statt der üblichen 108 % — genau die Schwelle, ab der "
        "die Zuschlag-Erwartung spürbar steigt.",
    ),
    "buy_score_threshold": (
        "Kauf-Schwelle — ab hier gilt ein Spieler als High Value", "Kauf", "pct",
        "Ab diesem Engine-Score wird ein Marktspieler zum Kaufkandidaten — knapp "
        "über dem ungeboosteten Saisonstart-Maximum, damit Daten und "
        "Community-Boost die Schwelle nur gemeinsam reißen.",
    ),
    "sell_score_threshold": (
        "Verkaufs-Schwelle (Score)", "Verkauf", "pct",
        "Kaderspieler unter diesem Score werden zum Verkauf vorgeschlagen — echte "
        "Formkrise, nicht ein einzelner schwacher Tag.",
    ),
    "listing_floor": (
        "Eigene Listings mind. (Anteil MW)", "Verkauf", "pct",
        "Eigene Listings starten nie unter diesem Anteil des Marktwerts — kein "
        "Verramschen unter Wert.",
    ),
    "accept_offer_floor": (
        "Angebot annehmen ab (Anteil MW)", "Verkauf", "pct",
        "Angebote anderer werden erst über dem Marktwert angenommen — wer unsere "
        "Spieler will, zahlt Aufpreis.",
    ),
    "min_balance": (
        "Konto-Untergrenze (projiziert)", "Schutz & Limits", "eur",
        "Der projizierte Kontostand darf nie darunter fallen — im Minus beim "
        "Anpfiff gäbe es null Punkte für den ganzen Spieltag.",
    ),
    "approval_threshold": (
        "Groß-Vorhaben ab (€)", "Schutz & Limits", "eur",
        "Freigabe-Pflicht seit 25.08.26 ausgesetzt (Vollmandat: „volle Autorität "
        "über das ganze Geld auf dem Account“). Die Marke sagt nur noch, welches "
        "Vorhaben als GROSS gilt: mindestens 2 h Veto-Fenster, damit Emil "
        "widersprechen KANN — freigeben muss er nichts. Der Wert bleibt im Code, "
        "damit die alte Schwelle sofort wieder greift, falls das Mandat endet.",
    ),
    "daily_buy_cap": (
        "Max. Käufe pro Tag", "Schutz & Limits", "count",
        "Höchstens so viele Käufe pro Tag — schützt vor Kaskaden-Fehlkäufen an "
        "einem schlechten Datentag.",
    ),
    "daily_sell_cap": (
        "Max. Verkäufe pro Tag", "Schutz & Limits", "count",
        "Höchstens so viele Verkäufe pro Tag — der Kader wird nie an einem Tag "
        "leergeräumt.",
    ),
    "league_trade_limit_per_half": (
        "Liga-Regel: Trades je Halbserie", "Schutz & Limits", "count",
        "Intern vereinbartes BASE-Limit an Transfers je Halbserie — die Engine "
        "stoppt rechtzeitig davor und lässt eine Notreserve für Notbremsen.",
    ),
}


def _dot(n: int) -> str:
    """13337 → '13.337' (deutsche Tausenderpunkte)."""
    return f"{n:,}".replace(",", ".")


def _fmt_param(kind: str, value: Any) -> tuple[str, float | None]:
    """Wert fertig formatieren; pct nur, wenn ein 0..100-Balken sinnvoll ist."""
    if kind == "pct":
        pct = round(float(value) * 100, 1)
        return f"{pct:.0f} %", pct if 0 <= pct <= 100 else None
    if kind == "eur":
        return _dot(int(value)) + " €", None
    return str(value), None


def _kickoff_text(kickoff: datetime | None) -> str:
    """'Fr 28.08. 20:30' in lokaler Zeit — Wochentag selbst gemappt, nie locale."""
    if kickoff is None:
        return "—"
    local = kickoff.astimezone()
    return f"{_WOCHENTAGE[local.weekday()]} {local:%d.%m. %H:%M}"


def _scoring() -> dict[str, Any]:
    """Erklärseiten-Daten — jeder Wert aus den echten Konstanten importiert."""
    g = DEFAULT_GUARDRAILS
    params: list[dict[str, Any]] = []
    for f in dc_fields(g):
        if f.name == "protected_player_ids":
            continue
        label, group, kind, erklaerung = _PARAM_INFO.get(
            f.name, (f.name, "Schutz & Limits", "count", "")
        )
        value_fmt, pct = _fmt_param(kind, getattr(g, f.name))
        params.append({
            "key": f.name,
            "label": label,
            "value_fmt": value_fmt,
            "group": group,
            "pct": pct,
            "erklaerung": erklaerung,
        })
    return {
        "weights": {
            "base_points": BASE_POINTS_WEIGHT,
            "form": FORM_WEIGHT,
            "matchup": MATCHUP_WEIGHT,
            "trend": TREND_WEIGHT,
        },
        # Elf-Score: gleiche Signale OHNE Markt-Trend (Marktwert erzeugt keine
        # Spieltagspunkte) — Gewichte der übrigen drei renormiert.
        "lineup_weights": {
            "base_points": BASE_POINTS_WEIGHT / (1 - TREND_WEIGHT),
            "form": FORM_WEIGHT / (1 - TREND_WEIGHT),
            "matchup": MATCHUP_WEIGHT / (1 - TREND_WEIGHT),
        },
        "multipliers": {status.value: factor for status, factor in MULTIPLIER.items()},
        "tip_boost": MAX_BOOST,
        "buy_threshold": g.buy_score_threshold,
        "sell_threshold": g.sell_score_threshold,
        "overpay": g.bid_overpay_fraction,
        "overpay_conviction": g.bid_overpay_conviction,
        "psych_offset": g.bid_psych_offset,
        "max_bid_pct": g.max_bid_pct_of_budget,
        "approval_threshold": g.approval_threshold,
        "trade_limit": g.league_trade_limit_per_half,
        "listing_floor": g.listing_floor,
        "accept_offer_floor": g.accept_offer_floor,
        "daily_buy_cap": g.daily_buy_cap,
        "daily_sell_cap": g.daily_sell_cap,
        "params": params,
    }


def _mv_series(kb_pids: set[str], days: int = 30) -> dict[str, list[tuple[str, int]]]:
    """Datierte MV-Reihe je Kickbase-Spieler-ID aus der Snapshot-Historie.

    Liefert je Spieler [("YYYY-MM-DD", mv), …] der letzten `days` Tage (max. ein
    Wert pro Tag — der letzte —, ältester zuerst). EINE Abfrage als Quelle für
    Sparklines (nur die Werte) UND das 7-Tage-Delta (braucht die Tage).
    """
    if not kb_pids:
        return {}
    since = datetime.now(UTC) - timedelta(days=days)
    with SessionLocal() as session:
        rows = session.execute(
            select(
                Player.kickbase_player_id,
                PlayerHistory.recorded_at,
                PlayerHistory.market_value,
            )
            .join(PlayerHistory, PlayerHistory.player_id == Player.id)
            .where(
                Player.kickbase_player_id.in_(kb_pids),
                PlayerHistory.recorded_at >= since,
            )
            .order_by(PlayerHistory.recorded_at)
        ).all()
    out: dict[str, dict[str, int]] = {}
    for pid, ts, mv in rows:
        out.setdefault(pid, {})[f"{ts:%Y-%m-%d}"] = int(mv)  # letzter Wert je Tag
    return {pid: sorted(per_day.items()) for pid, per_day in out.items()}


def _sparklines(kb_pids: set[str], days: int = 30) -> dict[str, list[int]]:
    """Nur die Werte der MV-Reihe (Sparkline-Rohdaten) — dünner Mantel um `_mv_series`."""
    return {
        pid: [v for _, v in serie] for pid, serie in _mv_series(kb_pids, days).items()
    }


def mv7_delta(serie: list[tuple[str, int]], *, today: date | None = None) -> int | None:
    """7-Tage-MW-Delta aus der datierten Reihe: letzter Wert minus dem jüngsten
    Wert, der mindestens 7 Tage vor dem letzten Snapshot liegt.

    Ehrlich None statt Zahl (Regel 9: nie 0 als Pseudo-Wert), wenn
    - die Historie keine 7 Tage zurückreicht, oder
    - `today` gesetzt ist und der letzte Snapshot älter als 2 Tage ist (der
      Snapshot-Job hängt — ein altes Delta als „aktuell" wäre eine Lüge).

    Bewusst NICHT aus dem API-Feld `sdmvt` gejoint: dasselbe Feld wird im Repo
    widersprüchlich gedeutet (kickbase/models.py TeamProfilePlayer:
    „7-Tage-MW-Änderung"; data.read_manager_squads: „Änderung seit gestern").
    Bis eine Live-Probe das klärt, zählt nur die eigene, datierte Historie
    (Beweisregel 3: Produktionspfad statt Feld-Intuition).
    """
    if len(serie) < 2:
        return None
    try:
        last_day = date.fromisoformat(serie[-1][0])
    except ValueError:
        return None
    if today is not None and last_day < today - timedelta(days=2):
        return None
    cutoff = last_day - timedelta(days=7)
    ref: int | None = None
    for day_s, value in serie:
        try:
            day = date.fromisoformat(day_s)
        except ValueError:
            continue
        if day <= cutoff:
            ref = int(value)  # der jüngste Wert, der alt genug ist
        else:
            break
    if ref is None:
        return None
    return int(serie[-1][1]) - ref


def _teamwert_spark(kader_pids: set[str], sparks_days: int = 30) -> list[int]:
    """Teamwert-Verlauf: Tages-Summe der Kader-Marktwerte (Forward-Fill je Spieler)."""
    if not kader_pids:
        return []
    since = datetime.now(UTC) - timedelta(days=sparks_days)
    with SessionLocal() as session:
        rows = session.execute(
            select(
                Player.kickbase_player_id,
                PlayerHistory.recorded_at,
                PlayerHistory.market_value,
            )
            .join(PlayerHistory, PlayerHistory.player_id == Player.id)
            .where(
                Player.kickbase_player_id.in_(kader_pids),
                PlayerHistory.recorded_at >= since,
            )
            .order_by(PlayerHistory.recorded_at)
        ).all()
    by_day: dict[str, dict[str, int]] = {}
    for pid, ts, mv in rows:
        by_day.setdefault(f"{ts:%Y-%m-%d}", {})[pid] = int(mv)
    last: dict[str, int] = {}
    series: list[int] = []
    # Summieren, sobald ≥90 % des Kaders bekannt sind — ein einzelner Neuzugang
    # ohne lange Historie darf die ganze Kurve nicht verhindern.
    threshold = max(1, int(len(kader_pids) * 0.9))
    for day in sorted(by_day):
        last.update(by_day[day])
        if len(last) >= threshold:
            series.append(sum(last.values()))
    return series


def _team_name(team_id: str) -> str:
    """Kickbase-Clubname aus der statischen 18er-Tabelle (leer, wenn unbekannt)."""
    t = TEAMS.get(str(team_id))
    return getattr(t, "kb_name", "") if t else ""


async def _load_details(
    client: KickbaseClient, league_id: str, pids: Iterable[str]
) -> dict[str, PlayerDetail]:
    """Spieler-Details (Member-Stats) für alle pids → {pid: PlayerDetail}.

    Alle Calls werden parallel gestartet; der RateLimiter des Clients
    serialisiert sie (1 req/s ⇒ ~75 s für Kader + Markt, läuft im Worker).
    Ein Fehler bei EINEM Spieler kostet nur dessen Eintrag — nie den Build
    (er fehlt dann im Ergebnis, die Zeile bleibt ohne Detail-Keys).
    """
    unique = list(dict.fromkeys(str(p) for p in pids if p))
    if not unique:
        return {}
    results = await asyncio.gather(
        *(client.get_player_detail(league_id, pid) for pid in unique),
        return_exceptions=True,
    )
    out: dict[str, PlayerDetail] = {}
    for pid, res in zip(unique, results, strict=True):
        if isinstance(res, BaseException):
            logger.warning("Spieler-Detail %s nicht geladen: %r", pid, res)
            continue
        out[pid] = res
    if len(out) < len(unique):
        logger.warning(
            "Spieler-Details: %d/%d geladen — Zeilen ohne Detail zeigen keine Stats.",
            len(out), len(unique),
        )
    return out


def _spiele(matchdays: list[MatchdaySummary], now: datetime) -> list[dict[str, Any]]:
    """Spieltags-Karte: die letzten 3 gespielten Spiele + das nächste, chronologisch.

    Sortiert nach Anpfiff (nicht nach `day`: in der Sommerpause enthält `mdsum`
    das Saisonende (Spieltag 32-34) UND Spieltag 1 der neuen Saison); Einträge
    ohne Anpfiff bleiben stabil in Lieferreihenfolge hinten. „Gespielt" =
    MatchdaySummary.is_played (md vergangen ODER mdst ≠ 0, s. dort).
    """
    ordered = sorted(matchdays, key=lambda m: (m.kickoff is None, m.kickoff or now))
    played = [m for m in ordered if m.is_played(now)]
    upcoming = [m for m in ordered if not m.is_played(now)]
    rows: list[dict[str, Any]] = []
    for m in played[-3:] + upcoming[:1]:
        done = m.is_played(now)
        rows.append({
            "day": m.day,
            "home": _team_name(m.home_team_id) or m.home_team_id,
            "away": _team_name(m.away_team_id) or m.away_team_id,
            "home_img": _img(m.home_img),
            "away_img": _img(m.away_img),
            "score": f"{m.home_goals}:{m.away_goals}" if done else None,
            "date": m.kickoff.astimezone().date().isoformat() if m.kickoff else "",
            "cur": m.is_current,
        })
    return rows


def _detail_fields(d: PlayerDetail, now: datetime | None = None) -> dict[str, Any]:
    """Row-Vertrags-Keys (Spec cockpit-v4 §3) aus einem PlayerDetail — reine Funktion.

    Zeitbezug (`now`) nur für „gespielt/nächstes Spiel"; Datumsfelder in lokaler
    Zeit wie der Rest des Cockpits (`_kickoff_text`).
    """
    now = now or datetime.now(UTC)
    return {
        "punkte": d.total_points,
        "avg": d.avg_points,
        "tore": d.goals,
        "vorlagen": d.assists,
        "einsaetze": d.appearances,
        "einwechsel": d.sub_appearances,
        "s11": d.s11,
        "avg_min": d.avg_minutes,
        "gelb": d.yellow,
        "rot": d.red,
        "fit": d.fitness_status == 0,
        "fit_text": d.fitness_text,
        "fit_since": f"{d.status_since.astimezone():%d.%m.%Y}" if d.status_since else "",
        "form5": [{"played": e.played, "p": e.points} for e in d.last_points[-5:]],
        "spiele": _spiele(d.matchdays, now),
        "nummer": d.shirt_number,
        "vorname": d.first_name,
        "nachname": d.last_name,
        "club_img": _img(d.team_image),
        "mv24": d.mv_change_24h,
    }


def _merge_details(
    rows: list[dict[str, Any]], details: dict[str, PlayerDetail], now: datetime
) -> int:
    """Detail-Keys in die Zeilen (per `pid`) mischen; Rückgabe: Zeilen MIT Detail."""
    hit = 0
    for r in rows:
        d = details.get(str(r.get("pid", "")))
        if d is None:
            continue
        r.update(_detail_fields(d, now))
        hit += 1
    return hit


def _market_rows(
    market_raw: dict[str, Any],
    score_by_pid: dict[str, float],
    bid_by_pid: dict[str, int],
    own_user_id: str,
    observed_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Alle Marktspieler mit Engine-Score, Restzeit und Eigentums-Flags.

    Restzeit aus `exs` (Sekunden): nur Kickbase-Listings tragen einen Countdown.
    Manager-Listings haben kein `exs` — ihr `dt` ist der Listing-ZEITPUNKT, kein
    Ablauf (live geprobt 20.08.) → hours_left 0.0, wie bei den Geboten.
    """
    rows: list[dict[str, Any]] = []
    observed_at = observed_at or datetime.now(UTC)
    for it in market_raw.get("it", []):
        mp = MarketPlayer.from_api(it)
        p = mp.player
        seller_id = str((it.get("u") or {}).get("i") or "")
        rows.append({
            "pid": p.id,
            "img": _img(p.image_path),
            "price": int(it.get("prc", 0) or 0),
            "name": f"{p.first_name} {p.last_name}".strip(),
            "pos": _POS.get(int(p.position), "?"),
            "position": int(p.position),
            "fitness_status": p.fitness_status,
            "team_id": p.team_id,
            "mv": p.market_value,
            "score": score_by_pid.get(p.id, 0.0),
            "hours_left": int(it.get("exs", 0) or 0) / 3600,
            "observed_at": observed_at.isoformat(),
            "expires_at": (observed_at + timedelta(seconds=int(it.get("exs") or 0))).isoformat()
            if not seller_id and it.get("exs") is not None else None,
            "ours": bool(seller_id) and seller_id == str(own_user_id),
            # Benannter Verkäufer = Manager-Listing (wie bids_rows; live 23.08.:
            # abgelaufene Manager-Listings sind unkaufbar, HTTP 500).
            "listing_type": "manager" if seller_id else "kickbase",
            "bid_ours": bid_by_pid.get(p.id),
            # Kickbase-Member-Startelf-Stufe (1=sicher … 5=Bank; None ohne Abo)
            "kb_prob": p.lineup_prob,
            "mv24": p.mv_change_24h,
            "team": _team_name(p.team_id),
        })
    rows.sort(key=lambda r: -r["score"])
    return rows


async def _fair_payload(client: KickbaseClient | None) -> dict[str, Any]:
    """Fair-Value-Radar: Kontext-Wolke aller 18 Erstliga-Team-Profile + Potenz-Fit.

    Sequenziell — der Rate-Limiter des Clients (1 req/s) taktet die 18 Calls
    (~18 s Build-Mehrkosten). Ein Fehler bei EINEM Team kostet nur dessen
    Spieler, nie den Build; ohne Client (Tests/Fallback) bleibt der Vertrag
    stabil: ``{"curve": None, "cloud": []}``. Wolke kompakt (nur die sieben
    Chart-Felder), Spieler ohne Ø-Punkte (ap ≤ 0) fliegen raus.
    """
    cloud: list[dict[str, Any]] = []
    if client is None:
        return {"curve": None, "cloud": []}
    for tid in TEAMS:
        try:
            profile = await client.get_team_profile(tid)
        except Exception as exc:  # noqa: BLE001 — ein fehlender Club darf nie den Build killen
            logger.warning("Fair-Value: Team-Profil %s nicht geladen: %r", tid, exc)
            continue
        for p in profile.players:
            if p.avg_points <= 0:
                continue
            cloud.append({
                "name": p.last_name,
                "ap": p.avg_points,
                "mv": p.market_value,
                "pos": _POS.get(int(p.position), "?"),
                "team_id": p.team_id or tid,
                "player_id": p.id,
                "img": _img(p.image_path),
            })
    return {"curve": fit_fair_curve(cloud), "cloud": cloud}


def _join_fair(
    rows_lists: Iterable[list[dict[str, Any]]], fair: dict[str, Any]
) -> None:
    """market-/kader-Zeilen um FAIR-KEYS ergänzen (in place).

    Zeilen ohne Detail-Ø (``avg`` fehlt, z. B. Detail-Call gescheitert) bekommen
    ``ap`` aus der Wolke per player_id gejoint, damit der faire Preis trotzdem
    berechenbar ist; danach setzt ``intel.fair_value.enrich`` die drei Keys.
    """
    ap_by_pid = {c["player_id"]: c["ap"] for c in fair.get("cloud") or []}
    curve = fair.get("curve")
    for rows in rows_lists:
        for r in rows:
            if r.get("avg") is None and r.get("pid") in ap_by_pid:
                r["ap"] = ap_by_pid[r["pid"]]
        fair_enrich(rows, curve)


def _dim_num(v: Any) -> float | None:
    """Robuste float-Konvertierung für Dimensions-Eingaben (None/Müll → None)."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def percentile_in_cloud(cloud: list[dict], pos: str, ap: float | None) -> int | None:
    """Perzentil-Rang (0..100) der Ø-Punkte innerhalb einer Position der Liga-Wolke.

    Standard-Perzentilrang: (kleinere + halbe gleiche) / n × 100, gerundet.
    None ohne Ø-Punkte, ohne Wolke oder ohne Positions-Kollegen — reine,
    testbare Funktion (Logik wie pages_core._percentile; hier zuhause, weil
    auch die Spielprofil-Dimensionen sie brauchen)."""
    if ap is None:
        return None
    peers: list[float] = []
    for c in cloud:
        if not isinstance(c, dict) or str(c.get("pos") or "") != pos:
            continue
        v = _dim_num(c.get("ap"))
        if v is not None and v > 0:
            peers.append(v)
    if not peers:
        return None
    below = sum(1 for v in peers if v < ap)
    equal = sum(1 for v in peers if v == ap)
    return round(100 * (below + 0.5 * equal) / len(peers))


def _attach_dimensions(rows_lists: Iterable[list[dict[str, Any]]],
                       cloud: list[dict]) -> None:
    """Spielprofil-Dimensionen + Empfehlungs-Chip je Markt-/Kader-Zeile (in place).

    Emils GO 24.08. (Spec Stufe 4 §E): sechs Dimensionen aus intel/dimensions —
    REINE ANZEIGE bis zur SP1-Kalibrierung, keine Score-Wirkung. Kauf-Score für
    den Chip: Markt-Zeilen tragen ihn als `score`, Kader-Zeilen als `kauf_score`.
    """
    for rows in rows_lists:
        for r in rows:
            if not isinstance(r, dict):
                continue
            ap = _dim_num(r.get("avg"))
            if ap is None:
                ap = _dim_num(r.get("ap"))
            pct = percentile_in_cloud(cloud, str(r.get("pos") or ""), ap)
            trend = next(
                (s.get("score") for s in r.get("signals") or []
                 if isinstance(s, dict) and s.get("name") == "trend"),
                None,
            )
            tip = r.get("tip") or {}
            dims = player_dimensions(
                li_status=r.get("status"),
                injury_reason=r.get("grund"),
                kb_prob=r.get("kb_prob"),
                avg_points=ap,
                percentile=(pct / 100 if pct is not None else None),
                trend_signal=_dim_num(trend),
                tip_weight=_dim_num(tip.get("weight")) if isinstance(tip, dict) else None,
                mv24=r.get("mv24"),
                market_value=r.get("mv"),
            )
            r["dims"] = [
                {"key": x.key, "t": x.label_short, "score": x.score,
                 "label": x.label, "tone": x.tone, "note": x.note}
                for x in dims
            ]
            kauf = r.get("kauf_score")
            if kauf is None:
                kauf = r.get("score")
            label, tone = empfehlung(_dim_num(kauf), dims[4])
            r["reco"] = {"label": label, "tone": tone}
            if pct is not None and r.get("pct") is None:
                r["pct"] = pct


def _utc(dt: datetime | None) -> datetime | None:
    """DB-Zeitstempel → aware UTC (naive Werte aus SQLite sind UTC)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _iso(dt: datetime | None) -> str | None:
    """ISO-8601 in UTC mit Offset („2026-08-23T20:15:00+00:00") — JS-Date-parsebar."""
    u = _utc(dt)
    return u.isoformat(timespec="seconds") if u else None


def _fmt_m(amount: int) -> str:
    """14_300_000 → „14,30 M" (MCP-/API-Schreibweise für amount_fmt)."""
    return f"{amount / 1e6:.2f}".replace(".", ",") + " M"


def _intent_row(i: Intent, now: datetime) -> dict[str, Any]:
    """Intent-Zeile als dict — Keys wie GET /api/intents (Spec Steuerzentrale §4)."""
    amount = int(i.amount or 0)
    exec_at, deadline = _utc(i.execute_at), _utc(i.deadline)
    position = int(i.position or 0)
    return {
        "id": i.id,
        "created_at": _iso(i.created_at),
        "updated_at": _iso(i.updated_at),
        "kind": i.kind,
        "player_id": i.player_id,
        "player_name": i.player_name or "",
        "team_id": i.team_id or "",
        "position": position,
        "pos": _POS.get(position, ""),
        "club": _team_name(i.team_id or ""),
        "amount": amount,
        "amount_fmt": _fmt_m(amount),
        "big": amount >= DEFAULT_GUARDRAILS.approval_threshold,
        "rationale": i.rationale or "",
        "sources": dict(i.sources) if isinstance(i.sources, dict) else {},
        "listing_type": i.listing_type or "kickbase",
        "deadline": _iso(i.deadline),
        "execute_at": _iso(i.execute_at),
        "status": i.status,
        "emil_note": i.emil_note,
        "adjusted_from": i.adjusted_from,
        "executed_action_id": i.executed_action_id,
        "result": i.result,
        "seconds_left": (
            max(0, int((exec_at - now).total_seconds())) if exec_at else None
        ),
        "deadline_seconds_left": (
            max(0, int((deadline - now).total_seconds())) if deadline else None
        ),
    }


def read_intents(
    session: Any, limit: int = 50, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Neueste *limit* Vorhaben (alle Status, id absteigend) — nur DB, kein Netz."""
    now = now or datetime.now(UTC)
    rows = session.execute(
        select(Intent).order_by(Intent.id.desc()).limit(limit)
    ).scalars().all()
    return [_intent_row(i, now) for i in rows]


#: Positions-Code der API → Cockpit-Kürzel. Gleiche Reihenfolge wie im eigenen
#: Kader, damit beide Ansichten dieselbe Sprache sprechen.
_MG_POS = {1: "TW", 2: "ABW", 3: "MIT", 4: "STU"}


async def read_manager_squads(client: Any, league_id: str,
                              ranking: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kader ALLER Mitspieler — je Manager ein Aufruf (Emils Wunsch 28.08.).

    Warum das überhaupt geht: `/v4/leagues/{lg}/managers/{uid}/squad` liefert
    fremde Kader vollständig — live geprobt am 28.08. mit zwölf Managern. Das
    Feld `lo` (Platz in der Elf) steht NUR bei aufgestellten Spielern; wer keines
    hat, sitzt auf der Bank. Genau so unterscheidet auch der eigene Kader.

    Kosten: ein Aufruf je Manager plus einer fürs Dashboard, bei zwölf Managern
    also 24 — der Client drosselt auf eine Anfrage pro Sekunde, der Cockpit-Build
    dauert dadurch gut zwanzig Sekunden länger. Das ist der Preis dafür, dass die
    Ansicht überhaupt Daten hat; sie wird einmal je Build gefüllt, nicht je Aufruf.

    Fällt ein einzelner Manager aus (Zeitüberschreitung, gesperrtes Profil),
    fehlt genau er — der Rest der Liga bleibt sichtbar. Eine halbe Liga ist
    nützlicher als eine leere Seite.
    """
    out: list[dict[str, Any]] = []
    for r in ranking:
        uid = str(r.get("user_id") or "")
        if not uid:
            continue
        try:
            roh = await client.get_manager_squad(league_id, uid)
        except Exception:                      # noqa: BLE001 — ein Manager darf fehlen
            continue
        try:
            kopf = await client.get_manager_dashboard(league_id, uid)
        except Exception:                      # noqa: BLE001
            kopf = {}
        spieler = []
        for it in roh.get("it") or []:
            mv = int(it.get("mv") or 0)
            spieler.append({
                "pid": str(it.get("pi") or ""),
                "name": str(it.get("pn") or ""),
                "pos": _MG_POS.get(int(it.get("pos") or 0), ""),
                "position": int(it.get("pos") or 0),
                "team_id": str(it.get("tid") or ""),
                "mv": mv,
                # Am 11.09. gegen eigene Detailwerte geprüft: sdmvt ist NICHT
                # die 24h-Änderung. Erst datierte Historie/Detailwerte ergänzen sie.
                "mv24": None,
                "api_value_change_sdmvt": _num_or_none(it.get("sdmvt")),
                "mv_trend": int(it.get("mvt") or 0),
                # `mvgl` = Marktwert-Gewinn seit dem Kauf — die einzige Zahl, die
                # zeigt, ob ein Kader teuer eingekauft oder gut getimt wurde.
                "gewinn": int(it.get("mvgl") or 0),
                "punkte": int(it.get("p") or 0),
                "avg_points": int(it.get("ap") or 0),
                "fit": int(it.get("st") or 0),
                # Kein `lo` = Bank. Das IST die Unterscheidung, nicht eine Deutung.
                "in_elf": "lo" in it and it["lo"] is not None,
                "lineup_order": it.get("lo"),
                "gelistet": it.get("prc") is not None,
                "img": _img(it.get("pim")),
            })
        spieler.sort(key=lambda x: (not x["in_elf"], x["position"], -x["mv"]))
        elf = [s for s in spieler if s["in_elf"]]
        out.append({
            "user_id": uid,
            "name": str(roh.get("unm") or r.get("name") or ""),
            "img": _img(roh.get("uim")),
            "me": bool(r.get("me")),
            "rank": r.get("rank"),
            # tp kann während eines Spieltags von nachlaufenden zu aktuellen
            # Punkten wechseln (11.09.: Emil 1023 -> 827). Ohne datierte,
            # abgeschlossene Spieltagsbelege ist keine bereinigte Summe bekannt.
            "completed_points": None,
            "dashboard_points": _num_or_none(kopf.get("tp")),
            "matchday_history": kopf.get("ph") or [],
            "punkte_gesamt": _num_or_none(kopf.get("tp")),
            "punkte_saison": _num_or_none(r.get("points")),
            # prft bleibt API-Metrik; bestätigte realisierte Gewinne berechnen wir
            # separat aus tatsächlichen Käufen und Verkäufen im Liga-Feed.
            "teamwert": _num_or_none(kopf.get("tv")),
            "gewinn": _num_or_none(kopf.get("prft")),
            "kader": len(spieler),
            "in_elf": len(elf),
            "auf_bank": len(spieler) - len(elf),
            "gelistet": sum(1 for s in spieler if s["gelistet"]),
            # Buchgewinn: was der AKTUELLE Kader gegenueber seinen Kaufpreisen
            # wert ist. Beantwortet "hat er gut eingekauft?" — waehrend `prft`
            # beantwortet "hat er gut gehandelt?".
            "buchgewinn": sum(s["gewinn"] for s in spieler),
            "spieler": spieler,
        })
    return out


def _num_or_none(v: Any) -> int | None:
    """Zahlen der Dashboard-Antwort kommen teils als float (tv: 181653877.0)."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def read_protocol(session: Any, limit: int = 60) -> list[dict[str, Any]]:
    """Letzte *limit* transfer_actions (neueste zuerst) mit Spielername aus `players`."""
    rows = session.execute(
        select(TransferAction).order_by(TransferAction.id.desc()).limit(limit)
    ).scalars().all()
    pids = {a.player_id for a in rows if a.player_id}
    names: dict[str, str] = dict(
        session.execute(
            select(Player.kickbase_player_id, Player.name).where(
                Player.kickbase_player_id.in_(pids)
            )
        ).all()
    ) if pids else {}
    return [
        {
            "id": a.id,
            "ts": _iso(a.created_at),
            "type": a.action_type,
            "status": a.status,
            "rule_id": a.rule_id or "",
            "rationale": a.rationale or "",
            "amount": a.amount,
            "dry_run": bool(a.dry_run),
            "player_id": a.player_id or "",
            "player": names.get(a.player_id or "", ""),
            "resolved_at": _iso(a.resolved_at),
            # Erwartung und Ergebnis lagen schon immer im payload — nur geladen
            # hat sie nie jemand (Befund 27.08.). Damit fehlten der Oberflaeche
            # zwei der fuenf Felder, die eine Entscheidung ausmachen: Was, Warum,
            # ERWARTUNG, ERGEBNIS, Konsequenz. Genau deshalb las sich das
            # Protokoll wie eine Liste von Taten ohne Urteil.
            **_erwartung_ergebnis(a.payload),
        }
        for a in rows
    ]


def _erwartung_ergebnis(payload: dict | None) -> dict[str, Any]:
    """`expectation` und `outcome` aus dem Audit-payload als flache Felder.

    Beide sind optional: Altbestand vor dem 25.08. hat keine Erwartung, und ein
    laufendes Gebot hat noch kein Ergebnis. Fehlt eines, bleibt es None — die
    Oberflaeche zeigt dann „offen“ statt einer erfundenen Zahl.
    """
    p = payload or {}
    exp = p.get("expectation") or {}
    out = p.get("outcome") or {}
    return {
        "p": exp.get("p"),                 # 0..1 — erwartete Zuschlag-Chance
        "p_why": exp.get("why") or "",     # der Satz, der die Zahl begruendet
        "p_kind": exp.get("kind") or "",   # kickbase | manager
        # NACHGETRAGEN: Die Zahl wurde nicht zur Tatzeit geschrieben, sondern
        # spaeter rekonstruiert — teils als der Ausgang schon bekannt war. Eine
        # solche "Erwartung" ist keine Vorhersage und darf keine Trefferquote
        # stuetzen; die Marke lag seit dem 26.08. im payload, wurde aber von
        # niemandem gelesen.
        "backfilled": bool(exp.get("backfilled")),
        "outcome": out.get("result") or "",        # won | lost | ""
        "outcome_at": out.get("at") or "",
    }


# ------------------------------------------------ KI-Einschätzung (Spieler-Detail)


def _signal_rows(c: ExplosionCandidate | None) -> list[dict[str, Any]]:
    """Engine-Signale eines Kandidaten als reine Daten (Name, Wert 0..1, Gewicht, Grund)."""
    if c is None:
        return []
    return [
        {
            "name": s.name,
            "score": round(float(s.score), 3),
            "weight": round(float(s.weight), 3),
            "reason": s.reason,
        }
        for s in c.signals
    ]


def _tip_row(name: str, tips: list[CommunityTip]) -> dict[str, Any] | None:
    """Bester aktiver Tipp zu einem Spielernamen — gleiche Schwelle/Metrik wie der
    Community-Boost (intel.community), damit Anzeige und Engine nie auseinanderlaufen.
    ``boost`` = mechanische Verschiebung des Kauf-Scores (MAX_BOOST × Gewicht)."""
    best: CommunityTip | None = None
    best_sim = 0.0
    for t in tips:
        s = similarity(name, t.player_name)
        if s >= _MATCH_CONFIDENT and s > best_sim:
            best, best_sim = t, s
    if best is None:
        return None
    weight = float(best.weight)
    return {
        "weight": round(weight, 2),
        "source": best.source,
        "note": best.note,
        "boost": round(MAX_BOOST * weight, 4),
    }


def read_buy_prices(session: Any, pids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Jüngstes AUSGEFÜHRTES Gebot je Spieler → {pid: {amount, at: ISO}} (Flip-Bilanz).

    Nur ``status='executed'`` zählt — Schatten-/verweigerte Gebote haben nie Geld
    bewegt. DB-weit (nicht nur die letzten 60 Protokoll-Zeilen)."""
    wanted = {str(p) for p in pids if p}
    if not wanted:
        return {}
    rows = session.execute(
        select(TransferAction)
        .where(
            TransferAction.action_type == "bid",
            TransferAction.status == "executed",
            TransferAction.player_id.in_(wanted),
        )
        .order_by(TransferAction.id.desc())
    ).scalars().all()
    out: dict[str, dict[str, Any]] = {}
    for a in rows:
        pid = str(a.player_id or "")
        if pid in out or not a.amount:
            continue
        out[pid] = {"amount": int(a.amount), "at": _iso(a.created_at)}
    return out


# ------------------------------------------------------------ Berichte + Lernen


def _preview(body: str, limit: int = REPORT_PREVIEW_CHARS) -> str:
    """Erste *limit* Zeichen eines Berichts: Zeilen-Marker (#, >, -, •, *) und Fett-/Code-
    Zeichen weg, Whitespace kollabiert — Unterstriche in Bezeichnern bleiben."""
    flat = re.sub(r"(?m)^\s*[#>\-*•]+\s*", " ", body or "")
    flat = re.sub(r"[*`]+", "", flat)
    flat = re.sub(r"\s+", " ", flat).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def read_reports(session: Any, limit: int = 30) -> list[dict[str, Any]]:
    """Neueste *limit* Berichte OHNE Volltext (Zone „Berichte"; Keys wie GET /api/reports).

    ``delivered`` kommt aus ``meta`` (Discord-Zustellung), sonst None = unbekannt."""
    rows = session.execute(
        select(Report).order_by(Report.created_at.desc(), Report.id.desc()).limit(limit)
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        meta = r.meta if isinstance(r.meta, dict) else {}
        delivered = meta.get("delivered")
        out.append({
            "id": r.id,
            "created_at": _iso(r.created_at),
            "kind": r.kind or "",
            "title": r.title or "",
            "source": r.source or "",
            "chars": len(r.body or ""),
            "preview": _preview(r.body or ""),
            "delivered": bool(delivered) if isinstance(delivered, bool | int) else None,
        })
    return out


async def _call_source(fn: Callable[..., Any] | None, session: Any, default: Any) -> Any:
    """Wissens-Quelle aufrufen, ohne ihre genaue Signatur zu kennen (Parallel-Entwicklung):

    ein ``session``/``db``-Parameter (oder ein einziger Pflicht-Positionsparameter)
    bekommt die DB-Session, sonst wird ohne Argumente gerufen; Coroutinen werden
    awaited. Jede Exception wird geloggt und liefert *default* — die Zone „Lernen"
    darf den Cockpit-Build nie zum Absturz bringen."""
    if fn is None:
        return default
    try:
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}
        kwargs: dict[str, Any] = {}
        for key in ("session", "db"):
            if key in params:
                kwargs[key] = session
                break
        else:
            first = next(iter(params.values()), None)
            if (first is not None and first.default is inspect.Parameter.empty
                    and first.kind in (first.POSITIONAL_ONLY, first.POSITIONAL_OR_KEYWORD)):
                kwargs[first.name] = session
        result = fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return default if result is None else result
    except Exception as exc:  # noqa: BLE001 — Fremdmodul, Build muss weiterlaufen
        logger.warning("Wissens-Quelle %s nicht lesbar: %r", getattr(fn, "__name__", fn), exc)
        return default


def _as_list(v: Any) -> list[Any]:
    if isinstance(v, list | tuple):
        return list(v)
    return [v] if v else []


async def _open_calibrations(evaluation: dict[str, Any], parameters: dict[str, Any]) -> list[str]:
    """Offene Kalibrierungen: Konstante/Funktion der Wissens-Module, sonst Feld in der
    Evaluation/den Parametern, sonst leer — nie geraten."""
    for mod_name in ("kickbase_agent.knowledge.parameters", "kickbase_agent.knowledge.learnings",
                     "kickbase_agent.intel.evaluation"):
        try:
            mod = __import__(mod_name, fromlist=["_"])
        except ImportError:
            continue
        for attr in ("open_calibrations", "OPEN_CALIBRATIONS"):
            val = getattr(mod, attr, None)
            if val is None:
                continue
            if callable(val):
                val = await _call_source(val, None, [])
            items = [str(x) for x in _as_list(val) if x]
            if items:
                return items
    for src in (evaluation, parameters):
        items = [str(x) for x in _as_list((src or {}).get("open_calibrations")) if x]
        if items:
            return items
    return []


async def read_knowledge(session: Any) -> dict[str, Any]:
    """Zone „Lernen": learnings · parameters · evaluation · open_calibrations — alle
    Quellen defensiv (fehlendes Modul/Fehler → leer), reine Daten ohne HTML."""
    learnings = [x for x in _as_list(await _call_source(load_learnings, session, []))
                 if isinstance(x, dict)]
    history = [x for x in _as_list(await _call_source(load_parameter_history, session, []))
               if isinstance(x, dict)]
    current = await _call_source(current_parameters, session, {})
    parameters = {
        "history": history,
        "current": dict(current) if isinstance(current, dict) else {},
    }
    evaluation = await _call_source(evaluate, session, {})
    if not isinstance(evaluation, dict):
        evaluation = {}
    enriched, bilanz, to_fix = _learning_balance(session, learnings)
    return {
        "learnings": enriched,
        "bilanz": bilanz,
        "to_fix": to_fix,
        "selbstverbesserungen": _self_improvements(session, history),
        "parameters": parameters,
        "evaluation": evaluation,
        "open_calibrations": await _open_calibrations(evaluation, parameters),
    }


def _learning_balance(
    session: Any, learnings: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Erkenntnisse um Belege, Status und Wochen-Bilanz anreichern (Spec §6, Stufe 5).

    Die Zone bleibt OHNE API vollstaendig — deshalb entsteht die Bilanz hier im
    Build und nicht erst im Browser. Faellt die Anreicherung aus, bleibt die ROHE
    Liste stehen: eine Erkenntnis ohne Bilanz ist immer noch eine Erkenntnis, eine
    verschwundene waere Datenverlust.
    """
    if enrich_learnings is None or not learnings:
        return learnings, {}, []
    try:
        enriched, bilanz, to_fix = enrich_learnings(session, learnings, datetime.now(UTC))
    except Exception:
        logger.exception("Lern-Bilanz nicht berechenbar — zeige die Erkenntnisse ohne Bilanz")
        return learnings, {}, []
    return list(enriched), dict(bilanz or {}), list(to_fix or [])


def _self_improvements(session: Any, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parameter-Aenderungen + Urteile des Lernzyklus als EINE Zeitleiste."""
    if load_self_improvements is None:
        return []
    try:
        return list(load_self_improvements(session, history=history))
    except Exception:
        logger.exception("Selbstverbesserungen nicht lesbar")
        return []


async def collect() -> dict[str, Any]:
    """Sammelt alle Cockpit-Daten (Kickbase-API nur lesend + lokale DB)."""
    plan = await build_plan()
    # Zentrale Fabrik: Client kommt mit Token-Refresh (Hermes-Review P0).
    client, auth = await make_kickbase_client()
    lg = plan.league_id

    ranking = await client.get_ranking(lg)
    bids = await client.get_own_bids(lg, auth.user_id)
    # Jüngster Feed; der separate Historiencache ergänzt ältere Managertransfers.
    feed_raw = await client.get_activity_feed(lg, max_items=1000)
    try:
        saved_lineup = await client.get_lineup(lg)
    except Exception:  # noqa: BLE001 — Empfehlung ersetzt keinen echten Aufstellungsabruf
        saved_lineup = None
    fixtures = await client.get_matchdays()
    # Markt roh (ein Call für Preis/Restzeit UND Verkäufer — `u` fehlt im Modell).
    market_observed_at = datetime.now(UTC)
    market_raw = await client._get(endpoints.market(lg))
    kickoff = next_kickoff(fixtures, datetime.now(UTC))

    with SessionLocal() as session:
        rec = await recommend_lineup(client, session, plan.lineup_squad)
        tips = load_recent_tips(session)
        actions = session.execute(
            select(TransferAction).order_by(TransferAction.id.desc()).limit(20)
        ).scalars().all()
        news = session.execute(
            select(NewsItem).order_by(NewsItem.published_at.desc()).limit(14)
        ).scalars().all()
        # Steuerzentrale: Vorhaben + Protokoll nur aus der DB (keine API-Aufrufe).
        intents = read_intents(session)
        protocol = read_protocol(session)
        # Zonen „Berichte" + „Lernen" (DB + Wissens-Module, alles defensiv).
        reports = read_reports(session)
        knowledge = await read_knowledge(session)
        # KI-Einschätzung: Trading-Holdings (Rolle) + Kaufpreise (Flip-Bilanz) je Kaderspieler.
        squad_ids = [sp.player.id for sp in plan.squad]
        trading_ids = load_trading_holdings(session, squad_ids)
        buy_prices = read_buy_prices(session, squad_ids)
        # Jev-Seite (Spec 2026-10-01): Protokoll, Trennschärfe, Treffer-Bilanz — defensiv.
        jev = read_jev_safely(session)

    # --- Liga-Tabelle -----------------------------------------------------
    ranking_rows = [
        {
            "user_id": r.user_id,
            "name": r.name,
            "rank": r.rank,
            "points": r.points,
            "prev_rank": r.prev_rank,
            "prev_points": r.prev_points,
            "me": r.user_id == auth.user_id,
            # Manager-Profilbild (`uim`, Live-Probe 24.08.) — gleiche CDN wie pim.
            "img": _img(r.image_path),
        }
        for r in sorted(ranking, key=lambda x: x.rank)
    ]
    me_row = next((r for r in ranking_rows if r["me"]), None)
    mine = (
        {
            "rank": me_row["rank"],
            "points": me_row["points"],
            "prev_rank": me_row["prev_rank"],
            "prev_points": me_row["prev_points"],
        }
        if me_row
        else None
    )

    # --- Gebote + Markt ---------------------------------------------------
    # Defensiv (.get): ein unerwartetes Markt-Item darf das Gebote-Panel nie killen.
    bids_rows = [
        {
            "name": b.get("name") or "",
            "player_id": str(b.get("player_id") or ""),
            "amount": int(b.get("amount") or 0),
            "hours_left": int(b.get("expires_in_s") or 0) / 3600,
            "expires_in_s": int(b.get("expires_in_s") or 0),
            "expired": int(b.get("expires_in_s") or 0) <= 0,
            "seller": b.get("seller") or "",
            "listing_price": int(b.get("listing_price") or 0),
            # Manager-Listing = benannter Verkäufer (kein Auto-Zuschlag, live geprobt 20.08.)
            "listing_type": "manager" if (b.get("seller") or "") else "kickbase",
            "img": _img(str(b.get("image_path") or "")),
        }
        for b in bids
        if isinstance(b, dict)
    ]
    score_by_pid = {c.player_id: c.total_score for c in plan.market_candidates}
    bid_by_pid = {b["player_id"]: int(b["amount"]) for b in bids}
    market_rows = _market_rows(
        market_raw, score_by_pid, bid_by_pid, auth.user_id, observed_at=market_observed_at,
    )
    # KI-Einschätzung (Markt): Signale des Kauf-Pfads + passender Tipp je Zeile.
    market_cand_by_id = {c.player_id: c for c in plan.market_candidates}
    for r in market_rows:
        cand = market_cand_by_id.get(r["pid"])
        r["signals"] = _signal_rows(cand)
        r["tip"] = _tip_row(cand.name if cand else r["name"], tips)
        # LigaInsider kennt der Plan für den Markt nur als Ausfall (Score ×0 + Grund);
        # Startelf/Bank bleiben hier unbekannt → kein status-Key (Overlay zeigt „–").
        if cand is not None and cand.injury_reason:
            r["status"], r["grund"] = "out", cand.injury_reason

    # --- Engine-Vorschläge ------------------------------------------------
    buys = [
        {
            "pid": p.action.player_id or "",
            "name": p.player_name or "?",
            "score": p.score,
            "amount": int(p.action.amount or 0),
            "approval": p.requires_approval,
            "rationale": p.rationale or "",
        }
        for p in plan.buys
    ]
    sells = [
        {
            "pid": p.action.player_id or "",
            "name": p.player_name or "?",
            "score": p.score,
            "way": p.action.action_type,
            "amount": int(p.action.amount or 0),
            "rationale": p.rationale or "",
        }
        for p in plan.sells
    ]
    profiteurs = [
        {
            "pid": p.player_id,
            "name": p.name,
            "mv": p.market_value,
            "same_position": p.same_position,
            "injured": p.injured,
        }
        for p in plan.profiteurs
    ]
    tips_rows = [
        {"player": t.player_name, "weight": t.weight, "source": t.source, "note": t.note}
        for t in tips
    ]

    # --- Kader + Aufstellung ----------------------------------------------
    avail_by_id = {a.player_id: a for a in rec.scout.availabilities}
    lineup_ids = {c.player_id for c in rec.lineup.starters}
    # Zwei Scores je Spieler: Elf-Score (ohne Markt-Trend) steuert die Aufstellung,
    # Kauf-Score (mit Trend) den Handel — die Kader-Ansicht zeigt beide.
    elf_by_id = {
        c.player_id: getattr(c, "lineup_score", c.total_score)
        for c in rec.scout.candidates
    }
    kauf_by_id = {c.player_id: c.total_score for c in rec.scout.candidates}
    squad_cand_by_id = {c.player_id: c for c in rec.scout.candidates}

    from kickbase_agent.decision.guardrails import protected_player_ids

    protected = protected_player_ids()

    def _kader_row(sp: Any) -> dict[str, Any]:
        pid = sp.player.id
        cand = squad_cand_by_id.get(pid)
        avail = avail_by_id.get(pid)
        bought = buy_prices.get(pid)
        return {
            "pid": pid,
            "img": _img(sp.player.image_path),
            "name": sp.player.last_name or sp.player.first_name,
            "pos": _POS.get(int(sp.player.position), "?"),
            "mv": sp.player.market_value,
            "score": elf_by_id.get(pid, 0.0),
            "kauf_score": kauf_by_id.get(pid, 0.0),
            "status": avail.status.value if avail else "unknown",
            "grund": avail.reason if avail else "",
            "elf": pid in lineup_ids,
            "position": int(sp.player.position),
            "kb_prob": sp.player.lineup_prob,
            "fitness_status": sp.player.fitness_status,
            "team_id": sp.player.team_id,
            "protected": pid in protected,
            "mv24": sp.player.mv_change_24h,
            "team": _team_name(sp.player.team_id),
            # KI-Einschätzung: Signale, Tipp, Rolle (Core/Trading), Kaufpreis (Flip-Bilanz).
            "signals": _signal_rows(cand),
            "tip": _tip_row(
                cand.name if cand else f"{sp.player.first_name} {sp.player.last_name}".strip(),
                tips,
            ),
            "role": "Trading" if pid in trading_ids else "Core",
            "buy_price": bought["amount"] if bought else None,
            "bought_at": bought["at"] if bought else None,
        }

    kader = sorted((_kader_row(sp) for sp in plan.squad), key=lambda r: -r["score"])

    # --- Marktwert-Verläufe (Sparklines + 7-Tage-Delta aus EINER Abfrage) --
    spark_pids = {r["pid"] for r in kader} | {r["pid"] for r in market_rows}
    serien = _mv_series(spark_pids)
    sparks = {pid: [v for _, v in serie] for pid, serie in serien.items()}
    # Momentum-Doktrin (31.08.): 7-Tage-Richtung je Zeile — None, wenn die
    # Historie nicht reicht (Renderer zeigt dann „—", nie eine erfundene 0).
    heute = datetime.now(UTC).date()
    mv7 = {pid: mv7_delta(serie, today=heute) for pid, serie in serien.items()}
    for r in kader:
        r["spark"] = sparks.get(r["pid"], [])
        r["mv7d"] = mv7.get(r["pid"])
    for r in market_rows:
        r["spark"] = sparks.get(r["pid"], [])
        r["mv7d"] = mv7.get(r["pid"])
    # Teamwert-Verlauf: gemeinsame Tagesachse, je Spieler letzter bekannter
    # Wert (Forward-Fill) — ehrlicher Trend trotz einzelner Snapshot-Lücken.
    teamwert_spark = _teamwert_spark({r["pid"] for r in kader}, sparks_days=30)

    # --- Spieler-Details (Member-Stats für Kader UND Markt, Spec v4 §1/§3) --
    # Ein Detail-Call je Spieler (Rate-Limiter 1 req/s); Ausfälle einzelner
    # Spieler lassen nur deren Detail-Keys weg — detail_coverage macht das
    # sichtbar. Die Formleiste (form5) kommt seit v4 aus `ph` des Details,
    # nicht mehr aus der lokalen performance-Tabelle.
    details = await _load_details(
        client, lg, [r["pid"] for r in kader] + [r["pid"] for r in market_rows]
    )
    now = datetime.now(UTC)
    detail_coverage = {
        "kader": _merge_details(kader, details, now),
        "kader_total": len(kader),
        "market": _merge_details(market_rows, details, now),
        "market_total": len(market_rows),
    }

    # --- Fair-Value-Radar (Markt-Tab): Liga-Wolke + Potenz-Fit + FAIR-KEYS --
    # Nach dem Detail-Merge, damit `avg`/`punkte` für Konfidenz und Ø da sind.
    fair = await _fair_payload(client)
    _join_fair((market_rows, kader), fair)
    # Spielprofil-Dimensionen + Empfehlungs-Chip (Stufe 4, Emils GO 24.08.)
    _attach_dimensions((market_rows, kader), fair.get("cloud") or [])

    # --- Audit-Log, News, Liga-Feed ---------------------------------------
    # Spielernamen zu den Aktionen: die players-Tabelle kennt jeden je
    # gesehenen Spieler (Snapshot), auch längst verkaufte.
    action_pids = {a.player_id for a in actions if a.player_id}
    with SessionLocal() as session:
        name_by_pid: dict[str, str] = dict(
            session.execute(
                select(Player.kickbase_player_id, Player.name).where(
                    Player.kickbase_player_id.in_(action_pids)
                )
            ).all()
        ) if action_pids else {}
        # Re-Bid-Vorgeschichte: je Gebot das jüngste ÄLTERE Gebot auf denselben
        # Spieler (DB-weit, nicht nur die 20 geladenen Zeilen).
        prev_bids = session.execute(
            select(TransferAction)
            .where(
                TransferAction.action_type == "bid",
                TransferAction.player_id.in_(action_pids),
            )
            .order_by(TransferAction.id)
        ).scalars().all() if action_pids else []

    def _prev_bid(a: TransferAction) -> tuple[int | None, str]:
        if a.action_type != "bid" or not a.player_id:
            return None, ""
        older = [
            p for p in prev_bids
            if p.player_id == a.player_id and p.id < a.id and p.amount
        ]
        if not older:
            return None, ""
        last = older[-1]
        return last.amount, f"{last.created_at:%d.%m %H:%M}"

    # Gegenpartei je Aktions-Typ — Mitspieler-Käufer kennt nur der Liga-Feed.
    context_by_type = {
        "bid": "auf dem Transfermarkt",
        "cancel_bid": "Gebot am Transfermarkt storniert",
        "sell": "Sofortverkauf an Kickbase",
        "list": "am Markt gelistet (für Mitspieler + Computer-Angebote)",
        "delist": "Listing zurückgezogen",
        "accept": "Angebot eines Mitspielers angenommen",
        "decline": "Angebot eines Mitspielers abgelehnt",
    }

    actions_rows = []
    for a in actions:
        prev_amount, prev_ts = _prev_bid(a)
        actions_rows.append({
            "ts": f"{a.created_at:%d.%m %H:%M}",
            "type": a.action_type,
            "status": a.status,
            "rule_id": a.rule_id or "",
            "rationale": a.rationale or "",
            "amount": a.amount,
            "dry_run": bool(a.dry_run),
            "player": name_by_pid.get(a.player_id or "", ""),
            "prev_amount": prev_amount,
            "prev_ts": prev_ts,
            "context": context_by_type.get(a.action_type, ""),
        })
    news_rows = [
        {
            "title": n.title,
            "source": getattr(n, "source", "") or "",
            "ts": f"{n.published_at:%d.%m.}" if n.published_at else "",
            "url": getattr(n, "url", None) or None,
        }
        for n in news
    ]
    feed_rows = []
    for it in feed_raw:
        if it.get("t") != 15:
            continue
        data = it.get("data") or {}
        byr = data.get("byr")
        feed_rows.append({
            "who": str(byr or data.get("slr") or "?"),
            "verb": "kauft" if byr else "verkauft",
            "player": str(data.get("pn", "?")),
            "amount": int(data.get("trp", 0) or 0),
        })
        if len(feed_rows) >= 10:
            break

    # Fremde Kader (Emils Wunsch 28.08.). Bewusst als Letztes und in einem
    # eigenen try: Zwei Dutzend Aufrufe bei einer Anfrage pro Sekunde sind der
    # langsamste Teil des Builds — schlaegt er fehl, soll die ganze uebrige Seite
    # trotzdem entstehen.
    try:
        manager_squads = await read_manager_squads(client, lg, ranking_rows)
    except Exception:                          # noqa: BLE001
        manager_squads = []

    strategy = await _collect_strategy(
        client=client, league_id=lg, expected_managers=len(ranking_rows), ranking_rows=ranking_rows,
        feed_events=feed_raw, squad_rows=kader,
        market_rows=market_rows, manager_rows=manager_squads,
        budget=plan.budget, open_bids=bids_rows, saved_lineup=saved_lineup,
        user_id=str(auth.user_id), user_name=str((me_row or {}).get("name") or ""),
    )

    deadline_now = datetime.now(UTC)
    next_lock = matchday_deadline(fixtures, deadline_now)
    running = running_matchday(fixtures, deadline_now)
    strategy["planning_notes"] = list(getattr(plan, "roster_value_notes", []))
    strategy["purchase_reviews"] = getattr(plan, "purchase_reviews", None) or {}
    strategy["curation_notes"] = list(getattr(plan, "curation_notes", []))
    strategy["team_plan"] = getattr(plan, "team_plan", None) or {}
    for player in strategy.get("players", []):
        player["purchase_review"] = strategy["purchase_reviews"].get(str(player["player_id"]))
    strategy["deadline"] = {
        "next_matchday": next_lock[0] if next_lock else None,
        "next_lock_at": next_lock[1].isoformat() if next_lock else None,
        "next_lock_label": _kickoff_text(next_lock[1]) if next_lock else None,
        "running_matchday": running,
        "locked_for_current_matchday": running is not None,
    }
    from kickbase_agent.agent.rules import load_agent_rules
    from kickbase_agent.intel.action_plan import build_action_plan

    strategy["action_plan"] = build_action_plan(
        strategy=strategy, buys=buys, sells=sells, intents=intents,
        bids=bids_rows, squad_rows=kader, market_rows=market_rows,
        observed_at=deadline_now,
    )
    return {
        "strategy": strategy,
        "rules": load_agent_rules().to_dict(),
        "stand": datetime.now().astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M"),
        "kickoff": _kickoff_text(kickoff),
        "budget": plan.budget,
        "teamwert": sum(sp.player.market_value for sp in plan.squad),
        "teamwert_spark": teamwert_spark,
        "trades_used": plan.trades_used,
        "trades_buys": getattr(plan, "trades_buys", 0),
        "trades_sells": getattr(plan, "trades_sells", 0),
        "trade_limit": plan.trade_limit,
        "mine": mine,
        "ranking": ranking_rows,
        "manager_squads": manager_squads,
        "bids": bids_rows,
        "market": market_rows,
        "fair": fair,
        "buys": buys,
        "sells": sells,
        "profiteurs": profiteurs,
        "tips": tips_rows,
        "kader": kader,
        "detail_coverage": detail_coverage,
        "formation": rec.lineup.formation,
        "quarantines": [q.li_name for q in rec.scout.quarantines],
        "actions": actions_rows,
        "news": news_rows,
        "feed": feed_rows,
        "feed_events": [it for it in feed_raw if isinstance(it, dict)],
        "scoring": _scoring(),
        "intents": intents,
        "protocol": protocol,
        "reports": reports,
        "learnings": knowledge["learnings"],
        "parameters": knowledge["parameters"],
        "evaluation": knowledge["evaluation"],
        "open_calibrations": knowledge["open_calibrations"],
        # Lern-Bilanz (Stufe 5): Wochen-Kopf, Korrekturliste und Emils Tabelle.
        # Wer hier einen Schluessel vergisst, baut eine Seite, die „noch keine
        # Daten" behauptet, obwohl die Daten vorliegen — genau das ist am
        # 26.08. passiert. tests/cli/test_cockpit_insights.py haelt beide
        # Vertraege seitdem gegeneinander.
        "bilanz": knowledge["bilanz"],
        "to_fix": knowledge["to_fix"],
        "selbstverbesserungen": knowledge["selbstverbesserungen"],
        "jev": jev,
    }


async def _collect_strategy(**kwargs: Any) -> dict:
    """Einzelne Zusatzquellen dürfen die bewährten Cockpit-Werkzeuge nicht blockieren."""
    from kickbase_agent.cli.cockpit.strategy_data import load_strategy_sources
    from kickbase_agent.intel.strategy import build_strategy_insights

    client = kwargs.pop("client")
    league_id = kwargs.pop("league_id")
    expected_managers = kwargs.pop("expected_managers")
    ranking_rows = kwargs.pop("ranking_rows", [])
    observed_ids = {str(row.get("user_id")) for row in kwargs["manager_rows"]}
    # Ein Kaderfehler darf bekannte Managerpunkte und ihre Historie nicht entfernen.
    kwargs["manager_rows"] = kwargs["manager_rows"] + [
        {**row, "spieler": None, "punkte_saison": row.get("points")}
        for row in ranking_rows if str(row.get("user_id")) not in observed_ids
    ]
    from kickbase_agent.cli.cockpit.league_history import load_league_history

    try:
        league_history = await load_league_history(
            client, league_id, kwargs["manager_rows"], kwargs["feed_events"],
        )
        kwargs["feed_events"] = league_history["feed_events"]
    except Exception as exc:  # noqa: BLE001 — frischer Feed bleibt nutzbar
        logger.warning("Liga-Historie fehlt: %s", type(exc).__name__)
        league_history = {"coverage": {"errors": [{"type": type(exc).__name__}]}}
    try:
        history, performance, sources = await load_strategy_sources(
            client, league_id, kwargs["squad_rows"], kwargs["market_rows"],
            kwargs["manager_rows"], kwargs["feed_events"],
        )
    except Exception as exc:  # noqa: BLE001 — Datenlücke transparent halten
        logger.warning("Strategie-Zusatzquellen fehlen: %s", type(exc).__name__)
        history, performance = None, None
        sources = {"errors": [{"source": "history_performance", "type": type(exc).__name__}]}
    result = build_strategy_insights(
        **kwargs, history_rows=history, performance_rows=performance,
        generated_at=datetime.now(UTC),
        manager_performance=league_history.get("manager_performance"),
        season_start=(league_history.get("overview") or {}).get("dt"),
    )
    result["league_history"] = league_history.get("coverage", {})
    if result["league_history"].get("errors"):
        sources.setdefault("errors", []).extend(result["league_history"]["errors"])
    observed_squads = sum(isinstance(m.get("spieler"), list) for m in kwargs["manager_rows"])
    if observed_squads < expected_managers:
        sources.setdefault("errors", []).append({
            "source": "manager_squads", "type": "partial_coverage",
            "expected": expected_managers, "observed": observed_squads,
        })
    from kickbase_agent.cli.cockpit.strategy_data import enrich_manager_changes

    enrich_manager_changes(
        kwargs["manager_rows"], kwargs["market_rows"] + kwargs["squad_rows"],
        result.get("players", []), result["generated_at"],
    )
    result["source_refresh"] = sources
    try:
        from kickbase_agent.cli.cockpit.strategy_data import _cache_path
        from kickbase_agent.news.strategy_news import fetch_strategy_news

        players = kwargs["squad_rows"] + kwargs["market_rows"] + [
            p for manager in kwargs["manager_rows"] for p in manager.get("spieler") or []
        ]
        result["news"] = await fetch_strategy_news(
            players=players, cache_path=_cache_path(league_id).with_name("strategy-news.json"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Strategie-News fehlen: %s", type(exc).__name__)
        result["news"] = {"articles": [], "errors": [{"type": type(exc).__name__}]}
    # Kuratierte Recherche bleibt datiert; ein Deploy macht alte Meldungen nicht frisch.
    import json
    from importlib.resources import files

    try:
        curated = json.loads(
            files("kickbase_agent.knowledge").joinpath("editorial_news.json").read_text()
        )
        relevant = {str(p["player_id"]) for p in result.get("players", [])}
        articles = result["news"]["articles"]
        seen = {(str(a.get("player_id")), a.get("url")) for a in articles}
        for article in curated:
            key = (str(article.get("player_id")), article.get("url"))
            if key[0] in relevant and key not in seen:
                articles.append(article)
                seen.add(key)
    except (OSError, ValueError, TypeError):
        logger.warning("Kuratierte Spielerrecherche nicht lesbar", exc_info=True)
    if sources.get("errors") or result.get("news", {}).get("errors"):
        result["status"] = "partial"
    return result
