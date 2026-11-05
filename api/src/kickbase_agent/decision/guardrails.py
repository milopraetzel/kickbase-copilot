"""Guardrails — harte Grenzen, die jede Kauf-/Verkaufsentscheidung einhalten muss.

Defaults sind bewusst konservativ. Sie werden später in agent_settings gespeichert
und über den Settings-Tab editierbar; bis dahin gelten diese Werte.

ZWEI KLASSEN (26.08.26):
- **Stellschrauben** — Aufschläge, Schwellen, Caps. Der Orchestrator darf sie
  anpassen, jede Änderung mit Decision-Record in ``knowledge/parameter_history.json``.
- **Verfassung** — ``protected_player_ids``. Emils Anweisung, kein Parameter:
  Sie wird ausschließlich über die Env/Settings gesetzt (``PROTECTED_PLAYER_IDS``,
  Emils Hoheit wie die .env) und taucht bewusst NICHT in den Parameter-Verträgen
  der Zentrale (``knowledge/parameters.GUARDRAIL_KEYS``) auf.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

#: Michael Olise (Bayern) — Emils ausdrückliche Anweisung vom 25.08.26:
#: „Verkaufe nicht Olise, aber sonst mach einfach die Entscheidung."
OLISE_PLAYER_ID = "8329"

#: Default der Verfassung. Greift, wenn weder Env noch .env etwas anderes sagen —
#: und als Fail-Safe, wenn die Settings gar nicht ladbar sind.
DEFAULT_PROTECTED_PLAYER_IDS: frozenset[str] = frozenset({OLISE_PLAYER_ID})

#: Env-Variable (Emils Hoheit): kommagetrennte Spieler-IDs. Leer = kein Schutz.
PROTECTED_ENV_VAR = "PROTECTED_PLAYER_IDS"


def protected_player_ids() -> frozenset[str]:
    """Geschützte Spieler — Env schlägt Settings, Störungen fallen SCHÜTZEND aus.

    Bewusst eine Funktion statt einer Modul-Konstante: ``Guardrails()`` wird beim
    Import gebaut, ``get_settings()`` verlangt aber ``DATABASE_URL`` — eine
    Auswertung zur Importzeit würde jeden Prozess ohne .env sprengen (genau daran
    scheitert sonst schon das Einsammeln der Tests).

    ``PROTECTED_PLAYER_IDS=""`` (ausdrücklich leer) = kein Schutz, Altverhalten.
    Variable nicht gesetzt ⇒ Settings/.env ⇒ Default (Olise). Jeder Fehler beim
    Laden endet beim Default: ein kaputter Settings-Ladevorgang darf Olise nicht
    freigeben.
    """
    raw = os.environ.get(PROTECTED_ENV_VAR)
    if raw is None:
        try:
            from kickbase_agent.config import get_settings

            raw = get_settings().protected_player_ids
        except Exception:  # noqa: BLE001 — im Zweifel geschützt, nie freigegeben
            return DEFAULT_PROTECTED_PLAYER_IDS
    if raw is None:
        return DEFAULT_PROTECTED_PLAYER_IDS
    return frozenset(part.strip() for part in str(raw).split(",") if part.strip())


@dataclass(frozen=True)
class Guardrails:
    # Kauf
    # Emil (20.08.26): 25 % war zu eng — Kickbase erlaubt Gebote weit über dem
    # Kontostand (Verschuldung bis ~33 % des Teamwerts). 60 % des Budgets lässt
    # einen Top-Transfer zu, verhindert aber Alles-auf-eine-Karte; das Konto
    # schützt der Anpfiff-Guard (min_balance + Notbremse), nicht diese Zahl.
    # Emil (01.09.26, „heb die Einzelkappe an" — Einzelkappe 01.09.): Auf der
    # PFAND-Basis (plan_base = risk_budget/0.35) war 60 % der neue bindende
    # Engpass (Onyeka −0,47 M), obwohl die harte Sicherung längst die exakte
    # Pfand-Summen-Prüfung ist (enforce_pledge_budget: Σ(bid − 0.9×MW) ≤
    # risk_budget). 0.70 ist der SCHÄRFSTE siegelkonforme Wert (L-089): Die
    # versiegelten Blocker-Tests pinnen den 28.08.-Fall (6,49-M-Kandidat bei
    # 9,23-M-Konto MUSS an der Kappe scheitern ⇒ pct < ~0.71); mehr wäre eine
    # Siegel-Entscheidung für Emil. 0.70 × 12,81 M = 8,97 M deckt die
    # Onyeka-Klasse; Anti-Klumpen bleibt.
    max_bid_pct_of_budget: float = 0.70
    # Review-Fund: exakte MW-Gebote verlieren jedes Bieterduell (Tiebreak: wer
    # zuerst UND wer mehr bietet) und platzen beim 22-Uhr-MW-Anstieg.
    # Emil (23.08.26, „mach B"): Ziel-Aufschlag +5 % → +8 %, damit die Zuschlags-
    # quote steigt (Friedrich-Underpay verlor, Hoffmeier-Overpay gewann).
    bid_overpay_fraction: float = 0.08   # +8 % über MW als Ziel-Aufschlag
    bid_psych_offset: int = 13_337       # krummer Betrag schlägt glatte Rivalen-Gebote
    # Conviction-Gebote: bei Überzeugungs-Zielen (starker Tipp, Job-Erbe) mehr
    # bieten — Menschen bieten emotional; wer die guten Spieler WILL, zahlt den
    # Aufschlag kontrolliert statt leer auszugehen. Value-Disziplin bleibt über
    # max_bid_pct + Approval-Schwelle erhalten.
    bid_overpay_conviction: float = 0.09
    # Gebots-Staffelung (pay-as-bid): Beim Listing-Ablauf gewinnt das höchste
    # Gebot und zahlt SEIN Gebot — wer sofort +9 % bietet, zahlt die 9 % auch
    # dann, wenn niemand mitbietet. Deshalb steigt das ERST-Gebot nur +2 % über
    # MW ein (+ Psycho-Offset); der Tiebreak „früher gewinnt bei Gleichstand"
    # bleibt durchs frühe Erstgebot erhalten. Erhöht wird erst bei Bedarf.
    bid_ladder_opening: float = 0.02
    # Unter dieser Listing-Restzeit (Stunden) empfiehlt der Gebots-Wächter
    # (worker/bid_watch.py) die Erhöhung aufs Ziel-Level (Standard +8 % /
    # Conviction +9 %) — nur per Discord-Erinnerung, ausgeführt wird eine
    # Erhöhung erst nach Emils Freigabe (»mach den Trade«).
    bid_ladder_raise_hours: float = 2.0
    # Kader-Bedarf (26.08.26, Ljubičić-Lehre): Für Spieler, ohne die die
    # Formation nicht steht, ist der Ziel-Aufschlag +20 % statt +8 %. Genau bei
    # 120 % MW springt die Zuschlag-Erwartung (trading/expectation.py) von 0.45
    # auf 0.60 — darunter zu bieten heißt, überwiegend Geld zu binden statt
    # Spieler zu bekommen. Beleg: Ljubičić-Listing 26.08., wir 2,93 M (108 % MW)
    # und laut API alleiniger Bieter; Zuschlag ging an Lukas M. mit 157 % MW.
    bid_overpay_need: float = 0.20
    # Interne Liga-Regel BASE: max 200 Transfers je Halbserie (mündlich vereinbart).
    league_trade_limit_per_half: int = 200
    min_balance: int = 0  # projizierter Kontostand nie darunter
    # Overpay-Kappe, GESTAFFELT (26.08.26). Standard bleibt 110 % MW: die
    # allermeisten Gebote sind Value-Wetten, und pay-as-bid bestraft jedes
    # unnötige Overpay sofort. Die höhere Stufe gilt ausschließlich für Gebote,
    # die ausdrücklich als Kader-Bedarf markiert sind (PlannedAction.squad_need
    # bzw. Positions-Not in propose_buys) — dort schlägt „Spieler bekommen" die
    # Preisdisziplin, weil ein Loch in der Aufstellung ~60–90 Punkte je Spieltag
    # kostet. 1.35 ist kein Wunschwert, sondern die obere Stufe der
    # Zuschlag-Erwartung (0.80 ab 135 %): Was die Formel für erreichbar hält,
    # darf der Wächter nicht sperren. Unberührt bleiben die 33-%-Regel
    # (trading/finance.overdraft_floor), max_bid_pct_of_budget und die Tages-Caps.
    max_bid_premium: float = 1.10       # Standard-Gebot ≤ 110 % des Marktwerts
    # Elf-Upgrade (`lineup_upgrade`, inkl. Dispo-Tausch) auf Kickbase-Auktionen:
    # V3-Gebote bis 115–120 % wurden bisher von der 110-%-Standardkappe gekappt.
    max_bid_premium_upgrade: float = 1.25
    max_bid_premium_need: float = 1.35  # markierter Kader-Bedarf ≤ 135 % MW
    # Handelsgebot (Kaufzweck `handel`, Handels-Autopilot) NUR auf Kickbase-Auktionen
    # (Emil 01.10.2026: „auf dem normalen Transfermarkt auch bis 130%, aber nicht bei
    # Angeboten an andere Spieler“). Manager-Listings bleiben bei max_bid_premium.
    max_bid_premium_trade: float = 1.30
    # 0.53 (31.08., Momentum-Doktrin — vorher 0.55): 0.55 lag ÜBER dem
    # ungeboosteten Saisonstart-Maximum (~0.54) — organisch kaufte die Engine
    # nichts, nur Community-geboostete Kandidaten kamen durch (Befund L-086:
    # null Vorhaben am SP1-Morgen). 0.53 öffnet exakt die Momentum-Schicht,
    # die Emil kaufen will; tiefer geht siegelkonform nicht (die versiegelte
    # Zusicherung „0.52er-MF bleibt ohne Not draußen" ist Doktrin: knapp
    # unterdurchschnittliche Kandidaten sind kein Kauf). Review nach SP3.
    buy_score_threshold: float = 0.53
    # Positions-Not-Regel (24.08., Kader-Gesetz): Kann eine Position ihr
    # Formations-Minimum nicht mit Reserve decken (einsetzbare Spieler ≤ Minimum
    # — Verletzte und eigene Listings zählen nicht), sinkt die Kauf-Schwelle für
    # GENAU diese Position um diese Toleranz (0.55 → 0.48). Ein Loch in der
    # Aufstellung kostet ~60–90 Punkte pro Spieltag — mehr als ein leicht
    # schwächerer Kauf je kostet.
    position_need_tolerance: float = 0.07
    # Plan-Hysterese (01.09., Emils Auftrag nach dem Nacht-Flattern vom 31.08./01.09.:
    # Raab/Boniface/Goto wurden je an der Not-Schwelle 0.46 geplant und vom
    # Folgelauf als „nicht mehr im Plan" geräumt — drei Ersetzt-Karten, null
    # Ausführung): Ein einmal geplantes GEBOTS-Vorhaben überlebt, solange sein
    # aktueller Kauf-Score nicht weiter als diese Marge unter seine (positions-
    # abhängige) Schwelle fällt und der Spieler am Markt steht. 0.05 deckt das
    # beobachtete Pendeln (±0.02-0.03 durch Quoten/MW-Zuckungen) mit Puffer;
    # echter Verfall (LI-Veto, Absturz) reißt weiterhin sofort ab.
    plan_hysteresis_margin: float = 0.05
    # Pfand-Hebel (01.09., Spec 2026-08-31-pfand-hebel + Neurechnung d8):
    # Die Plan-Basis der Gebotskappe darf risk_budget / pfand_risk_fraction
    # betragen, weil jeder Zuschlag seinen eigenen sofort verkäuflichen Spieler
    # mitliefert — im Feuer steht nur Overpay + Drift. 0.35 deckt den
    # schlechtesten zur TATZEIT möglichen Fall: Der Executor-Preflight kappt
    # JEDES Live-Gebot (auch Emils +%-Buttons) hart bei MW × 1.35 (Kader-
    # Bedarf), Rückverkauf konservativ 0.90 × MW (Drift-Haircut; empirisch
    # zahlt der Sofortverkauf 100 %) ⇒ Verlustanteil ≤ (1.35 − 0.90)/1.35
    # = 33,3 % < 35 %. Der Spec-Entwurf 0.28 war ungedeckt (nur 120 %-Szenario
    # gerechnet). Gültig NUR zusammen mit der MW-Kappen-Härtung im Preflight
    # (Gebot ohne ermittelbaren Marktwert wird refused) und dem Einlöser.
    pfand_risk_fraction: float = 0.35
    # Nachanker (02.09., Onyeka-Lehre): Liegt ein Gebot zur Ausführung unter dem
    # AKTUELLEN Marktwert (22-Uhr-Sprung), zieht der Executor es auf MW + Psycho-
    # Offset nach — aber nur, wenn das höchstens diesen Faktor des geplanten
    # Betrags kostet (1.10 = +10 %). Größere Lücken sind keine Drift, sondern ein
    # anderer Preis: dort bleibt die bisherige Logik (Manager-Listing refused).
    bid_reanchor_max: float = 1.10

    # Verkauf
    sell_score_threshold: float = 0.35  # Kader-Spieler mit Score darunter
    listing_floor: float = 0.95  # eigene Listings ≥ 95 % MW
    accept_offer_floor: float = 1.03  # Angebote annehmen nur ≥ 103 % MW
    # Trend-Verkaufsregel (24.08.26, Emils Hoffmeier-Punkt −513 k/Tag): Wert-
    # verlierer OHNE Startelf-Pfad werden gelistet, auch wenn der Gesamt-Score
    # über der Verkaufsschwelle liegt — in der Börsen-Hinrunde ist ein fallender
    # Bankspieler totes Kapital. Trend-Signal-Skala: 0.5 + 7-Tage-Δ% × 10
    # (intel/signals.trend_signal). 31.08. (Momentum-Doktrin, Emil: „Verlierer
    # verkaufen"): 0.15 → 0.29 ≈ −2,1 %/7d — Verlierer werden ~1,4 Prozentpunkte
    # Verfall früher angefasst. 0.29 statt 0.30, weil die versiegelte
    # Zusicherung „milder Trend 0.30 bleibt verschont" die Grenze zum
    # Marktrauschen markiert. Whipsaw-Risiko im Review (SP3) messen.
    sell_trend_score_max: float = 0.29
    # „Kein Startelf-Pfad": Elf-Score unter dieser Marke (LI-Bank dämpft ×0.4,
    # Starter liegen ~0.5+) — Starter mit MW-Delle bleiben geschützt.
    sell_trend_lineup_max: float = 0.45
    # Gewinnmitnahme (31.08., Momentum-Doktrin — Emil: „steigen lassen, teurer
    # verkaufen"): Ein Steiger OHNE Startelf-Pfad wird sofort verkauft, sobald
    # sein jüngster Tagesschritt kippt. take_profit_trend_min auf der
    # 7-Tage-Trend-Skala (0.85 ≈ +3,5 %/7d oder mehr); take_profit_trend1d_max
    # auf der Tages-Skala (0.50 = letzter Schritt ≤ 0). Sofortverkauf statt
    # Listing, weil Computer-Angebote empirisch ausbleiben (L-081).
    take_profit_trend_min: float = 0.85
    take_profit_trend1d_max: float = 0.50
    # Momentum-Reserve (31.08.): Anteil der „Kaderspieler ohne Startelf-Pfad"-
    # Marktwerte, der die PLAN-Basis der Gebotskappe erweitert (nur > 48 h vor
    # der Deadline UND nur bei scharfer Notbremse, decision/policies.
    # momentum_reserve + cli/plan.py). 0.90: Der Sofortverkauf zahlt zwar exakt
    # den aktuellen MW (Hoffmeier 27.08.: 2,60 → 9,13 M in Minuten), aber
    # zwischen Zuschlag und Heil-Verkauf können 22:00-Updates die Reserve
    # abwerten — 10 % Puffer decken die realistische Drift von 1–2 Tagen.
    momentum_reserve_haircut: float = 0.90

    # Mengen & Schutz
    # Emil (23.08.26, „mach B"): 3+3 → 5+5, mehr parallele Bewegung in der
    # Börsen-Hinrunde; die 200er-Halbserien-Ampel bleibt die harte Obergrenze.
    # Vollfreigabe (02.09.26, Emil: „wirklich volle Freigabe"): 5+5 → 8+8. Am
    # 02.09. kostete der Kauf-Cap zwei nachweislich richtige Gebote (Daka,
    # Scheller — beide Kickbase-Listings mit Ablauf vor dem Anpfiff, Protokoll
    # 06:35Z „Tages-Cap Käufe erreicht (5/5)"). Ein Kader-Umbau braucht mehr
    # Züge pro Tag als der Normalbetrieb; die harten Grenzen bleiben die
    # 200er-Ampel, das Konto (33-%-Regel) und die Kader-Deckung.
    daily_buy_cap: int = 8
    daily_sell_cap: int = 8
    # Listings zählen NICHT gegen den Verkaufs-Cap (Panne 26.08., L-077): ein
    # Listing gibt keinen Spieler ab, kostet nichts und ist folgenlos zurückzu-
    # nehmen — es ist das Werkzeug, mit dem ein Verkauf überhaupt zustande
    # kommt. Fünf Gegenfinanzierungs-Listings sperrten den einzigen echten
    # Verkauf des Abends. Eigenes, großzügiges Kontingent; Bremse bleibt, damit
    # ein Fehlläufer nicht den halben Kader an den Markt hängt.
    daily_list_cap: int = 12
    approval_threshold: int = 5_000_000  # alles darüber braucht immer Approval
    #: Zusätzlich geschützte Spieler dieses Guardrails-Objekts (Aufrufer-Wunsch).
    #: Die VERFASSUNG steht nicht hier, sondern in ``protected_player_ids()`` —
    #: sonst wäre sie wieder von den Default-Guardrails abhängig und damit in
    #: jedem Produktionspfad leer (genau der Befund vom 26.08.). Fragen tut man
    #: immer ``all_protected_ids``.
    protected_player_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def all_protected_ids(self) -> frozenset[str]:
        """Verfassung (Env/Settings) + explizit gesetzte IDs dieses Objekts."""
        return self.protected_player_ids | protected_player_ids()

    def with_protected(self, ids: list[str]) -> Guardrails:
        return replace(self, protected_player_ids=frozenset(ids) | self.protected_player_ids)


DEFAULT_GUARDRAILS = Guardrails()
