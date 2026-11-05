"""Kauf-, Verkaufs- und Angebots-Policies — reine Funktionen über bewertete Spieler.

Jede Funktion nimmt bewertete Kandidaten + Guardrails und gibt geprüfte Proposals
zurück. Keine Funktion ruft eine API oder trifft eine Zufallsentscheidung; gleiche
Eingabe → gleiche Ausgabe (voll testbar, im Backtest wiederholbar).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from kickbase_agent.agent.executor import PlannedAction
from kickbase_agent.decision.guardrails import Guardrails
from kickbase_agent.decision.lineup import _FORMATIONS, can_field_any_formation
from kickbase_agent.decision.models import Proposal
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.scouts.ligainsider.severity import Severity

# Formations-Wahrheit aus decision/lineup.py: jede gültige Kickbase-Formation ist
# 1 Torwart + 10 Feldspieler (d-m-s). Daraus abgeleitete MINDESTBESETZUNG
# (Minimum je Position über alle 9 Formationen): 1 TW, 3 ABW, 2 MF, 1 ST.
# Sie ist notwendig, aber nicht hinreichend — z. B. reicht 3 ABW nur zusammen
# mit ≥4 MF (alle 3er-Ketten verlangen m ≥ 4). Hinreichend prüft
# `_can_field_any_formation` gegen die konkreten 9 Formationen.
_MIN_KEEPERS = 1
_MIN_PER_POSITION = {
    2: min(d for d, _, _ in _FORMATIONS),  # Abwehr: 3
    3: min(m for _, m, _ in _FORMATIONS),  # Mittelfeld: 2
    4: min(s for _, _, s in _FORMATIONS),  # Sturm: 1
}
# Folgt aus jeder Formation (1 + 10), steht aber explizit hier: der Rest-Kader
# nach einem Verkauf darf nie unter eine komplette Startelf fallen.
_MIN_SQUAD_SIZE = 11

_POS_LABEL = {1: "TW", 2: "ABW", 3: "MF", 4: "ST"}


def _can_field_any_formation(counts: dict[int, int]) -> bool:
    """Delegiert an die eine Formations-Wahrheit in decision/lineup.py."""
    return can_field_any_formation(counts)


def formation_need_counts(deployable_counts: dict[int, int]) -> dict[int, int]:
    """Fehlende Starter je Position für die nächstliegende gültige Formation.

    Eine komplette 5-4-1 mit genau einem TW und einem ST hat keinen Bedarf. Das
    alte Reserve-Gate markierte beide Positionen trotzdem und erzeugte unnötige
    Premiumgebote. Bei gleicher Gesamtzahl fehlender Plätze gilt stabil die
    Reihenfolge aus ``_FORMATIONS``; nach jedem geplanten Bedarfskauf wird der
    konkrete Slot in ``propose_buys`` verbraucht.
    """
    best: dict[int, int] | None = None
    best_total: int | None = None
    for defenders, midfielders, strikers in _FORMATIONS:
        required = {1: 1, 2: defenders, 3: midfielders, 4: strikers}
        deficits = {
            pos: required_count - deployable_counts.get(pos, 0)
            for pos, required_count in required.items()
            if deployable_counts.get(pos, 0) < required_count
        }
        total = sum(deficits.values())
        if best_total is None or total < best_total:
            best, best_total = deficits, total
    return best or {}


def positions_in_need(deployable_counts: dict[int, int]) -> set[int]:
    """Positionen mit echtem Defizit in der nächstliegenden Formation."""
    return set(formation_need_counts(deployable_counts))


def _fmt_m(value: int) -> str:
    return f"{value / 1_000_000:.1f}M €"


def _fmt_m2(value: int) -> str:
    # 2 Nachkommastellen für die Staffelung: Erstgebot (+2 %) und Ziel (+5 %)
    # liegen oft so nah beieinander, dass 1 Nachkommastelle beide gleich zeigt.
    return f"{value / 1_000_000:.2f}M €"


def propose_buys(
    candidates: list[ExplosionCandidate],
    budget: int,
    league_id: str,
    guardrails: Guardrails,
    already_bought_today: int = 0,
    club_counts: dict[str, int] | None = None,
    max_per_club: int | None = None,
    conviction_ids: set[str] | None = None,
    manager_listing_prices: dict[str, int] | None = None,
    need_positions: set[int] | None = None,
    need_counts: dict[int, int] | None = None,
    purchase_reviews: dict[str, dict] | None = None,
) -> list[Proposal]:
    """Kaufvorschläge für starke Markt-Kandidaten, unter Budget- und Mengengrenzen.

    need_positions (positions_in_need, 24.08.): für Positionen ohne Reserve
    überm Formations-Minimum gilt die um position_need_tolerance gesenkte
    Kauf-Schwelle — ein Aufstellungs-Loch kostet mehr Punkte als ein leicht
    schwächerer Kauf.

    club_counts/max_per_club (Review-Fund Vereinslimit): zählt Kaderspieler UND
    offene Gebote pro Club — auch die eigenen Vorschläge dieses Laufs zählen mit,
    damit nie zwei Gebote gemeinsam das Liga-Limit sprengen (tote Gebote).

    Gebots-Staffelung: Normale Trading-Gebote eröffnen mit +2 % und bleiben in
    der niedrigen Staffel. Echte Starterlücken und geprüfte Ersatzkäufe erhalten
    direkt das autonome Need-Ziel (+20 %, max. +35 %). Discord ist weder
    Freigabe- noch Ausführungspfad.

    manager_listing_prices (pid → Listing-Preis, NUR Manager-Listings; 24.08.,
    Codex-Review + err-5080-Beweis): bei Manager-Listings entscheidet ein MENSCH
    über den Zuschlag, und Kickbase lehnt Gebote unter dem MARKTWERT hart ab
    (HTTP 500, err 5080 „UnderpayNotAllowed" — Leiter-Beweis 24.08.: 90/95 % ✗,
    100 % ✓). Der Listing-Preis allein ist Deko. Das Erstgebot ankert deshalb
    bei max(Listing-Preis, MW) + Psycho-Offset statt an der niedrigen
    Kickbase-Staffel; nur Kickbase-Listings (Auto-Zuschlag) erlauben Underpay.

    max_bid_premium (Guardrail, z. B. 1.10): Erstgebot UND Eskalations-Ziel
    werden hart auf MW × Premium gekappt — der fixe Psycho-Offset darf bei
    kleinen Marktwerten die 110 % nicht reißen (die Kappe gilt AUCH über dem
    Manager-Anker: verlangt der Verkäufer mehr, bieten wir bewusst darunter;
    sie liegt nie unter 100 % MW und kollidiert damit nie mit err 5080).

    Kader-Bedarf (26.08.): Für Positionen aus `need_positions` — ohne sie steht
    die Formation nicht — gilt die höhere Stufe: Ziel-Aufschlag
    `bid_overpay_need` (+20 % ⇒ 120 % MW, wo die Zuschlag-Erwartung von 0.45 auf
    0.60 springt) und Kappe `max_bid_premium_need` (135 %). Das Gebot trägt dann
    `squad_need=True`, damit der Executor-Preflight dieselbe Stufe anwendet.
    Alle anderen Gebote bleiben unverändert bei +8 % / Kappe 110 %.
    """
    max_single_bid = int(budget * guardrails.max_bid_pct_of_budget)
    remaining_budget = budget
    slots = guardrails.daily_buy_cap - already_bought_today
    proposals: list[Proposal] = []
    counts = dict(club_counts or {})
    remaining_needs = dict(need_counts or {})

    floor = guardrails.buy_score_threshold - (
        guardrails.position_need_tolerance if need_positions else 0.0
    )
    ordered = sorted(candidates, key=lambda x: (
        x.position not in (need_positions or set()),
        -x.lineup_score, x.current_market_value, x.player_id,
    )) if purchase_reviews is not None else sorted(
        candidates, key=lambda x: x.total_score, reverse=True
    )
    for c in ordered:
        if purchase_reviews is None and len(proposals) >= slots:
            break
        review = (purchase_reviews or {}).get(c.player_id)
        if purchase_reviews is not None and (
            not review or not review.get("eligible") or review.get("swap_only")
        ):
            continue
        if c.total_score < floor:
            if purchase_reviews is None:
                break  # nur die alte Reihenfolge ist nach Kaufindex sortiert
            continue
        threshold = guardrails.buy_score_threshold
        squad_need = bool(
            need_positions
            and c.position in need_positions
            and (not need_counts or remaining_needs.get(c.position, 0) > 0)
        )
        if squad_need:
            threshold -= guardrails.position_need_tolerance  # Positions-Not
        if c.total_score < threshold:
            continue  # unter der Schwelle SEINER Position (kein break: Not-
            # Kandidaten mit niedrigerem Score können noch folgen)
        if max_per_club is not None and counts.get(c.team_id, 0) >= max_per_club:
            continue  # Vereinslimit erreicht (Kader + offene Gebote + Vorschläge)
        # Gebots-Staffelung (pay-as-bid): das ERST-Gebot steigt niedrig ein
        # (bid_ladder_opening) — es sichert früh den „früher gewinnt"-Tiebreak,
        # ohne bei Zuschlag ohne Konkurrenz das volle Overpay zu zahlen. Das
        # ZIEL-Level (Overpay, Conviction-Ziele mit höherem Aufschlag gegen
        # emotionale Rivalen-Gebote) wird nur in der rationale genannt; die
        # Erhöhung empfiehlt der Gebots-Wächter (worker/bid_watch.py) kurz vor
        # Listing-Ablauf — ausgeführt erst nach Emils Freigabe. Der krumme
        # Offset schlägt glatte Rivalen-Gebote in beiden Stufen.
        # Kader-Bedarf schlägt Conviction: Wer gebraucht wird, damit die
        # Formation überhaupt steht, ist teurer wert als ein Überzeugungs-Kauf.
        if squad_need:
            overpay = guardrails.bid_overpay_need
        elif conviction_ids and c.player_id in conviction_ids:
            overpay = guardrails.bid_overpay_conviction
        else:
            overpay = guardrails.bid_overpay_fraction
        bid = (
            int(c.current_market_value * (1 + guardrails.bid_ladder_opening))
            + guardrails.bid_psych_offset
        )
        target_bid = int(c.current_market_value * (1 + overpay)) + guardrails.bid_psych_offset
        # Vollautomation + echte Starterlücke (09.09.): Die niedrige +2-%-Staffel
        # wartete auf eine Discord-Erinnerung, obwohl Emil keine Einzelfreigaben
        # mehr gibt und Discord im Digest-Modus still ist. Für ausdrücklich
        # verifizierten Kaderbedarf ist die vorhandene +20-%-Bedarfsstufe deshalb
        # das direkte Gebot; die harte 135-%-Kappe und alle Exposure-Netze bleiben.
        if squad_need:
            bid = target_bid
        # Manager-Preisanker: Anker = max(Listing-Preis, MW) — Kickbase lehnt
        # Gebote unter dem Marktwert auf Manager-Listings mit err 5080
        # „UnderpayNotAllowed" ab (24.08.), der Listing-Preis allein ist Deko.
        # Die niedrige Einstiegs-Staffel gilt nur für Kickbase-Listings
        # (Auto-Zuschlag ans höchste Gebot), nicht für Menschen-Deals.
        listing_price = (manager_listing_prices or {}).get(c.player_id)
        anchor = None
        if listing_price is not None:
            anchor = max(listing_price, c.current_market_value)
            bid = max(bid, anchor + guardrails.bid_psych_offset)
            target_bid = max(target_bid, bid)
        # Harte Preis-Obergrenze (Review-Fund P0: max_bid_premium existierte,
        # wurde aber nie erzwungen): der FIXE Psycho-Offset (+13.337) reißt bei
        # kleinen Marktwerten die prozentuale Kappe (z. B. MW 100k → 115.337
        # statt ≤ 110.000). Erstgebot UND Eskalations-Ziel werden auf die Kappe
        # GESETZT, nicht geskippt — die Kappe IST das Maximalgebot.
        # Gestaffelt (26.08.): Kader-Bedarf darf bis 135 % MW gehen, alles
        # andere bleibt bei 110 %.
        premium_cap = int(
            c.current_market_value
            * (guardrails.max_bid_premium_need if squad_need else guardrails.max_bid_premium)
        )
        bid = min(bid, premium_cap)
        target_bid = min(target_bid, premium_cap)
        if review is not None:
            individual_cap = int(review["max_bid_eur"])
            bid = min(bid, individual_cap)
            target_bid = min(target_bid, individual_cap)
        # Normale Value-/Trading-Gebote bleiben in der niedrigen Staffel. Nur
        # starterverifizierter Kaderbedarf steigt direkt auf das Ziel-Level ein.
        # Budget-Kappung und Konto-Untergrenze prüfen den Betrag, der jetzt
        # platziert wird: niedriges Erstgebot im Normalfall, direktes Ziel-Level
        # bei verifiziertem Kaderbedarf.
        if bid > max_single_bid:
            continue
        if purchase_reviews is None and remaining_budget - bid < guardrails.min_balance:
            continue
        proposals.append(
            Proposal(
                action=PlannedAction(
                    action_type="bid",
                    league_id=league_id,
                    player_id=c.player_id,
                    amount=bid,
                    rule_id="buy_explosion",
                    squad_need=squad_need,
                ),
                rule_id="buy_explosion",
                rationale=(
                    f"{c.name}: Score {c.total_score:.2f}, Erstgebot {_fmt_m2(bid)} "
                    f"(≤ {int(guardrails.max_bid_pct_of_budget * 100)}% Budget) — "
                    f"Ziel bei Eskalation {_fmt_m2(target_bid)}."
                    + (
                        f" Manager-Listing: Anker max(Listing, MW) = {_fmt_m2(anchor)} "
                        "(nie unter MW — err 5080)."
                        if anchor is not None
                        else ""
                    )
                    + (
                        f" Positions-Not {_POS_LABEL.get(c.position, c.position)}: "
                        f"Schwelle {threshold:.2f} statt "
                        f"{guardrails.buy_score_threshold:.2f}; Kader-Bedarf ⇒ "
                        f"Kappe {int(guardrails.max_bid_premium_need * 100)}% MW "
                        f"statt {int(guardrails.max_bid_premium * 100)}%."
                        if squad_need
                        else ""
                    )
                ),
                score=c.total_score,
                requires_approval=bid > guardrails.approval_threshold,
                player_name=c.name,
            )
        )
        remaining_budget -= bid
        if purchase_reviews is None:
            counts[c.team_id] = counts.get(c.team_id, 0) + 1
        if squad_need and need_counts:
            remaining_needs[c.position] = max(0, remaining_needs.get(c.position, 0) - 1)
    return proposals


def momentum_reserve(
    squad: list[ExplosionCandidate],
    guardrails: Guardrails,
    *,
    bench_ids: set[str] | frozenset[str] | None,
) -> int:
    """Liquidierbare Momentum-Reserve: Marktwert-Summe der Kaderspieler ohne Startelf-Pfad.

    Momentum-Doktrin (Emil, 31.08.): Bei Kader-Minimum ist der Agent „nicht knapp
    bei Kasse, sondern unbeweglich" — ein Kauf hebt den Kader über das Minimum,
    DANACH wird genau diese Reserve frei (propose_sells schlägt die Spieler dann
    von selbst vor). Die Reserve erweitert deshalb die PLAN-Basis der Gebotskappe
    (cli/plan.py), nie das echte Konto; die scharfen Netze (T-120-Storno,
    T-15-Notbremse, 33-%-Preflight) bleiben unberührt.

    NACHTRAG 31.08. abends (Fehlerfund per Nachrechnung, Beweisregel 4): Der
    erste Wurf gated allein über den Elf-SCORE (< sell_trend_lineup_max). Nach
    SP1 liegt der aber wegen dünner Punkte-Historie auch bei ECHTEN LI-Startern
    unter 0.45 (Schwäbe 0.34, Khedira 0.21 …) — die Reserve zählte 51 M statt
    ~7 M und der Planner baute 20-M-Gebote. Ein Score-Proxy ist kein
    Status-Gate: `bench_ids` verlangt jetzt den expliziten LigaInsider-Status
    „kein Stammplatz". Es zählt nur, wer (a) laut LI Bank ist UND Elf-Score
    unter der Schwelle hat, oder (b) langzeitverletzt ist (ohnehin Sofort-
    verkaufskandidat). `bench_ids=None` heißt: keine LI-Daten ⇒ Reserve 0
    (fail-safe konservativ). Geschützte und Halten-Regel-Fälle zählen nie.
    """
    # Positions-Reserve-Gate (01.09., 9c-Fund „Vogt-Fiktion"): Ein Spieler
    # zählt nur, wenn seine POSITION über dem Formations-Minimum besetzt ist —
    # der einzige Stürmer (oder Torwart) ist nie liquidierbar, egal wie
    # pfadlos: Sein Verkauf wird auch nach einem generischen Zukauf von der
    # Kader-Deckung geblockt, sein Marktwert wäre Schein-Deckung im
    # Pfand-Risiko-Budget.
    minimums = {1: _MIN_KEEPERS, **_MIN_PER_POSITION}
    position_counts = Counter(c.position for c in squad)
    total = 0
    for c in squad:
        if c.player_id in guardrails.all_protected_ids:
            continue
        if c.injury_severity == Severity.LONG:
            total += c.current_market_value  # Sofortverkaufskandidat = liquide
            continue
        if c.injury_severity is not None:
            continue  # Halten-Regel: eingepreister Dip ist keine Reserve
        if bench_ids is None or c.player_id not in bench_ids:
            continue  # ohne LI-Bank-Status ist niemand „pfadlos" — Starter nie Kasse
        if c.lineup_score >= guardrails.sell_trend_lineup_max:
            continue  # Zweitgurt: hoher Elf-Score schützt auch Bank-Spieler
        if position_counts.get(c.position, 0) <= minimums.get(c.position, 0):
            continue  # Positions-Reserve-Gate: letzte Besetzung ist keine Kasse
        total += c.current_market_value
    return int(total * guardrails.momentum_reserve_haircut)


def pledge_risk(amount: int, market_value: int, haircut: float) -> int:
    """Pfand-Risiko eines Gebots: Verlust, falls es gewinnt und sofort zurückverkauft wird.

    Rückverkaufserlös = haircut × aktueller MW (0.90 = Drift-Puffer; empirisch
    zahlt der Sofortverkauf 100 % — Hoffmeier 27.08.). Ein Gebot unter dem
    Erlös hat Risiko 0 (Gewinn beim Rückverkauf), nie negativ.
    """
    return max(0, amount - int(market_value * haircut))


def enforce_pledge_budget(
    proposals: list[Proposal],
    mv_by_pid: dict[str, int],
    risk_budget: int,
    haircut: float,
) -> tuple[list[Proposal], int]:
    """Exakte Pfand-Buchführung über die Kaufvorschläge — reine Funktion.

    Pfand-Hebel (01.09.): Die Plan-Basis erlaubt in Summe mehr Gebots-Volumen
    als Bargeld da ist, weil jeder Zuschlag seinen eigenen verkäuflichen
    Spieler mitliefert. Die harte Grenze ist deshalb nicht das Volumen, sondern
    die Summe der Pfand-Risiken: Σ pledge_risk ≤ risk_budget garantiert
    „alle Gebote schlagen zu, alles wird zurückverkauft ⇒ Konto ≥ Puffer"
    per Konstruktion. Greedy in Vorschlags-Reihenfolge (propose_buys sortiert
    nach Score); Vorschläge ohne bekannten Marktwert fliegen konservativ raus
    (ohne MW kein Pfand). Rückgabe: (behaltene Vorschläge, Anzahl verworfen).
    """
    kept: list[Proposal] = []
    dropped = 0
    used = 0
    for p in proposals:
        mv = mv_by_pid.get(str(p.action.player_id))
        if mv is None or mv <= 0:
            dropped += 1
            continue
        risk = pledge_risk(int(p.action.amount or 0), mv, haircut)
        if used + risk > risk_budget:
            dropped += 1
            continue
        used += risk
        kept.append(p)
    return kept, dropped


def propose_sells(
    squad: list[ExplosionCandidate],
    league_id: str,
    guardrails: Guardrails,
    already_sold_today: int = 0,
    *,
    list_slots: int | None = None,
    bench_ids: set[str] | frozenset[str] | None = None,
    showcase_bench: bool = False,
    showcase_exclude_ids: set[str] | frozenset[str] = frozenset(),
) -> list[Proposal]:
    """Verkaufsvorschläge für schwache, ungeschützte Kaderspieler.

    Verletzten-Regel (Profi-Benchmark, docs/STRATEGIE-SAISON-26-27.md):
    - Langzeit-Ausfall (injury_severity "long", z. B. Kreuzband/Bruch/OP) →
      Sofortverkauf ("sell"): vor dem 22:00-Update gibt es noch den alten Preis,
      danach fällt der MW über Wochen weiter.
    - Kurzzeit ("short", z. B. Grippe/angeschlagen) → HALTEN: weder verkaufen noch
      listen — der kleine Dip ist eingepreist, Rückkehrer ziehen beim Comeback an.
      Unbekannte Schwere ("unknown") läuft konservativ wie Kurzzeit. Da das
      Proposal-Format keine "hold"-Aktion kennt, werden diese Spieler schlicht
      aus den Vorschlägen ausgeschlossen (Halten = nichts tun).
    - Ein Score von 0.0 allein ist KEIN Sofortverkaufsgrund (mehr): Nicht-Verletzte
      unter der Schwelle werden gelistet ("list") und kassieren das Computer-Angebot
      (~102–103,5 % MW) statt den Sofortverkaufs-Abschlag.

    Kader-Deckung (Review-Fund P0 — 3 Vorschläge aus einem 11er-Kader, darunter der
    EINZIGE Torwart): `squad` ist der volle Kader. Ein Verkaufsvorschlag ist nur
    erlaubt, wenn der Rest-Kader danach — inklusive der früheren Vorschläge
    desselben Laufs — noch mindestens 11 Spieler hat UND eine gültige
    Kickbase-Formation (decision/lineup.py, 1 TW + d-m-s) vollständig stellen kann.
    Abgeleitete Mindestbesetzung über alle 9 Formationen: 1 TW, 3 ABW, 2 MF, 1 ST;
    der einzige Torwart ist damit nie verkaufbar. Die Regel gilt bewusst AUCH für
    den Langzeit-Sofortverkauf: wer die MW-Rettung mit einer nicht mehr stellbaren
    Startelf bezahlt, reißt ein 0-Punkte-Loch in jeden Spieltag — erst Ersatz
    kaufen, dann verkaufen (der Verkauf wird im nächsten Lauf erneut vorgeschlagen).
    Ein Kader, der schon VOR dem Verkauf keine Formation stellen kann, wird nicht
    weiter geschwächt (keine Vorschläge, bis Zukäufe die Deckung herstellen).

    Trend-Verkaufsregel (Pass 2, 24.08. — Emils Hoffmeier-Punkt −513 k/Tag;
    verschärft 02.09. als Fable 5.1): Wertverlierer werden GELISTET, auch wenn
    Gesamt-Score, Elf-Score oder LI-Status noch freundlich aussehen — MW-Verlust
    ist Kapitalverlust und kein akzeptables passives Halten. Ein Verkauf/Accept
    bleibt an Kaderdeckung und Ersatz gekoppelt; das Listing/Rotationssignal
    entsteht aber für jeden fallenden ungeschützten Spieler. Schutzliste,
    Kader-Deckung und Tages-Slots gelten unverändert.

    Gewinnmitnahme (Pass 3, 31.08. — Momentum-Doktrin): Steiger OHNE
    Startelf-Pfad, deren jüngster Tagesschritt nicht mehr steigt (trend_1d ≤
    take_profit_trend1d_max), werden SOFORT verkauft — der volle aktuelle
    Marktwert ist real, das Computer-Angebot empirisch nicht (L-081: vier
    Listings, sechs Tage, null Angebote). Emils Regel bleibt gewahrt: Wer einen
    Startelf-Pfad hat, punktet weiter und wird nie zur Kasse gemacht.

    list_slots (31.08., L-077 vollendet): None = Alt-Verhalten (Listings zählen
    gegen den Verkaufs-Cap — so rechnen bestehende Aufrufer und die versiegelten
    Tests). Als Zahl gesetzt, bekommen list-Vorschläge ein EIGENES Kontingent
    (daily_list_cap) und der Verkaufs-Cap zählt nur sell/accept — der
    ActionExecutor trennt die Töpfe seit L-077 genauso.

    bench_ids (Review 31.08., L-090 nachgezogen): Der Elf-Score ist ein Proxy,
    kein Status — nach SP1 liegen auch ECHTE LI-Starter unter 0.45 (Schwäbe
    0.34, Khedira 0.21; derselbe Fund, der `momentum_reserve` das Gate
    einbrachte). Mit gesetzten `bench_ids` verlangen Pass 2 UND Pass 3
    zusätzlich den expliziten LigaInsider-Status „Bank"; der Score bleibt
    Zweitgurt. Pass 3 (Sofortverkauf, neu und geld-riskant) ist ohne LI-Daten
    (`bench_ids=None`) komplett AUS (fail-safe wie die Reserve); Pass 2
    (Listing, Bestand) behält bei None sein Alt-Verhalten — die versiegelten
    Tests rufen ohne `bench_ids` und bleiben bit-identisch.
    """
    sell_slots = guardrails.daily_sell_cap - already_sold_today
    combined = list_slots is None
    lists_used = 0
    sells_used = 0
    proposals: list[Proposal] = []
    proposed_ids: set[str] = set()

    def has_slot(action_type: str) -> bool:
        if combined:
            return len(proposals) < sell_slots
        if action_type == "list":
            return lists_used < (list_slots or 0)
        return sells_used < sell_slots

    def exhausted() -> bool:
        if combined:
            return len(proposals) >= sell_slots
        return lists_used >= (list_slots or 0) and sells_used >= sell_slots
    # Laufender Rest-Kader: jeder akzeptierte Vorschlag reduziert Zählung + Größe,
    # damit nie zwei Vorschläge GEMEINSAM die Deckung sprengen.
    remaining_counts = Counter(c.position for c in squad)
    remaining_size = len(squad)

    def covered_after(c: ExplosionCandidate) -> Counter | None:
        """Positions-Zählung nach dem Verkauf — None, wenn die Deckung risse."""
        after = remaining_counts.copy()
        after[c.position] -= 1
        if remaining_size - 1 < _MIN_SQUAD_SIZE or not _can_field_any_formation(after):
            return None
        return after

    def add(
        c: ExplosionCandidate,
        rule_id: str,
        rationale: str,
        action_type: str,
        *,
        amount_override: int | None = None,
    ) -> None:
        nonlocal remaining_counts, remaining_size, lists_used, sells_used
        if not has_slot(action_type):
            return  # Kontingent dieses Aktionstyps ist erschöpft
        after = covered_after(c)
        if after is None:
            # Kader-Deckung: ohne diesen Spieler wäre keine gültige Formation
            # (oder keine 11) mehr stellbar — überspringen, auch bei Langzeit-
            # Ausfall (siehe Docstring). Schwächere Blocker verhindern nicht,
            # dass verkaufbare Kandidaten dahinter noch vorgeschlagen werden.
            return
        if action_type == "list":
            lists_used += 1
        else:
            sells_used += 1
        amount = amount_override if amount_override is not None else c.current_market_value
        proposals.append(
            Proposal(
                action=PlannedAction(
                    action_type=action_type,
                    league_id=league_id,
                    player_id=c.player_id,
                    amount=amount,
                    rule_id=rule_id,
                ),
                rule_id=rule_id,
                rationale=rationale,
                score=c.total_score,
                requires_approval=amount > guardrails.approval_threshold,
                player_name=c.name,
            )
        )
        proposed_ids.add(c.player_id)
        remaining_counts = after
        remaining_size -= 1

    for c in sorted(squad, key=lambda x: x.total_score):
        if exhausted():
            break
        if c.total_score > guardrails.sell_score_threshold:
            break  # sortiert aufsteigend — ab hier sind alle stärker
        if c.player_id in guardrails.all_protected_ids:
            continue
        if c.injury_severity is not None and c.injury_severity != Severity.LONG:
            # Kurzzeit-/unklarer Ausfall → halten (Panikverkauf am Tiefpunkt ist
            # der klassische Anfängerfehler; unklar wird wie Kurzzeit behandelt).
            continue
        if c.injury_severity == Severity.LONG:
            action_type = "sell"
            verb = (
                f"Sofortverkauf {_fmt_m(c.current_market_value)} JETZT — Langzeit-Ausfall "
                f"({c.injury_reason}), vor dem 22:00-Update gilt noch der alte Preis"
            )
        else:
            action_type = "list"
            verb = f"listen zu {_fmt_m(c.current_market_value)} — Computer-Angebot ~103% abwarten"
        add(
            c,
            "sell_weak",
            (
                f"{c.name}: Score {c.total_score:.2f} unter Verkaufsschwelle "
                f"{guardrails.sell_score_threshold:.2f} — {verb}."
            ),
            action_type,
        )

    # Pass 2: Fable-5.1-Trend-Rotation über der Score-Schwelle.
    for c in sorted(squad, key=lambda x: x.total_score):
        if exhausted():
            break
        if c.player_id in proposed_ids or c.player_id in guardrails.all_protected_ids:
            continue
        if c.injury_severity == Severity.LONG:
            continue  # Langzeit-Fälle laufen über Pass 1 als Sofortverkauf
        trend = next((s.score for s in c.signals if s.name == "trend"), None)
        if trend is None or trend > guardrails.sell_trend_score_max:
            continue
        trend_reason = next(
            (s.reason for s in c.signals if s.name == "trend"), "Marktwert fällt."
        )
        add(
            c,
            "sell_trend",
            (
                f"{c.name}: Wertverlierer — {trend_reason} "
                "MW-Verlust ist Kapitalverlust, nicht passives Halten; "
                f"listen zu {_fmt_m(c.current_market_value)} und nach Ersatz/Deckung "
                "aktiv rotieren (Computer-Angebot ~103% abwarten)."
            ),
            "list",
        )

    # Pass 3 (31.08., Momentum-Doktrin): Gewinnmitnahme am Wendepunkt — Steiger
    # ohne Startelf-Pfad, deren jüngster Tagesschritt kippt, sofort verkaufen.
    # Der Sofortverkauf zahlt den vollen AKTUELLEN Marktwert; ein Listing wäre
    # nach L-081 (vier Listings, sechs Tage, null Angebote) bloße Hoffnung.
    for c in sorted(
        squad,
        key=lambda x: -next((s.score for s in x.signals if s.name == "trend"), 0.5),
    ):
        if exhausted():
            break
        if c.player_id in proposed_ids or c.player_id in guardrails.all_protected_ids:
            continue
        if c.injury_severity is not None:
            continue  # Verletzte laufen über Pass 1 (long) bzw. Halten-Regel
        trend = next((s.score for s in c.signals if s.name == "trend"), None)
        if trend is None or trend < guardrails.take_profit_trend_min:
            break  # absteigend sortiert — ab hier steigt keiner mehr stark genug
        if bench_ids is None or c.player_id not in bench_ids:
            # Sofortverkauf verlangt den expliziten LI-Status „Bank" (L-090):
            # ohne LI-Daten ist niemand „pfadlos" — ein Neuzugang vor dem
            # Aufstellungs-Refresh wäre sonst schutzlos (Review 31.08.).
            continue
        if c.lineup_score >= guardrails.sell_trend_lineup_max:
            continue  # Startelf-Pfad: Gewinner werden nie zur Kasse gemacht
        trend_1d = next((s.score for s in c.signals if s.name == "trend_1d"), None)
        if trend_1d is None or trend_1d > guardrails.take_profit_trend1d_max:
            continue  # noch kein Wendepunkt — weiter halten (Emil: teurer verkaufen)
        gipfel_reason = next(
            (s.reason for s in c.signals if s.name == "trend"), "Marktwert stieg stark."
        )
        add(
            c,
            "take_profit",
            (
                f"{c.name}: Gewinnmitnahme am Wendepunkt — {gipfel_reason} "
                f"Jüngster Tagesschritt kippt (trend_1d {trend_1d:.2f} ≤ "
                f"{guardrails.take_profit_trend1d_max:.2f}), kein Startelf-Pfad "
                f"(Elf-Score {c.lineup_score:.2f}) → Sofortverkauf "
                f"{_fmt_m(c.current_market_value)} zum vollen Marktwert."
            ),
            "sell",
        )

    # Pass 4 (20.10., Schaufenster für Bankspieler): Explizite LI-Bankspieler
    # außerhalb der geschützten Startelf werden mit einem Anker-Aufschlag
    # (125 % MW) gelistet, damit Mitspieler Übergebote (≥ 115 % MW) abgeben können.
    if showcase_bench and bench_ids is not None:
        for c in sorted(squad, key=lambda x: -x.current_market_value):
            if not has_slot("list"):
                break
            if (
                c.player_id in proposed_ids
                or c.player_id in guardrails.all_protected_ids
                or c.player_id in showcase_exclude_ids
                or c.player_id not in bench_ids
            ):
                continue
            if c.current_market_value <= 0 or c.injury_severity == Severity.LONG:
                continue
            asking = int(c.current_market_value * SHOWCASE_MARKUP)
            add(
                c,
                "list_showcase",
                (
                    f"{c.name}: Schaufenster-Listing zu {_fmt_m(asking)} (125% MW) — "
                    "Bankspieler für Mitspieler-Übergebote (≥ 115% MW) anbieten."
                ),
                "list",
                amount_override=asking,
            )
    return proposals


@dataclass(frozen=True)
class IncomingOffer:
    """Eingehendes Angebot auf ein EIGENES Listing (Markt-`ofs[]` unseres it-Eintrags).

    `market_value` ist der aktuelle Spieler-MW (Floor-Referenz), `amount` der
    Angebotsbetrag, `offer_id` das `uoid` für den accept-Call (Kickbase keyed
    Offers per User — dieselben Feldnamen wie get_own_bids, live geprobt 20.08.).
    """

    player_id: str
    player_name: str
    team_id: str
    position: int
    market_value: int
    amount: int
    offer_id: str
    bidder_id: str = ""
    # Unser eigener Listungspreis (`prc` des eigenen Markt-Eintrags, 16.09.).
    # 0 = unbekannt/nicht übergeben (Altverhalten: nur der MW-Floor zählt).
    listing_price: int = 0

    @property
    def meets_asking_price(self) -> bool:
        """Angebot erreicht den von Emil gesetzten Listungspreis."""
        return self.listing_price > 0 and self.amount >= self.listing_price


def _position_minimums_kept(counts: dict[int, int]) -> bool:
    """Positions-Minima 1 TW/3 ABW/2 MF/1 ST — dieselbe Prüfung wie im Executor
    außerhalb des T-120-Fensters (Kader ist dann ein Portfolio, das nachgefüllt
    wird; L-095)."""
    if counts.get(1, 0) < 1:
        return False
    return all(counts.get(pos, 0) >= need for pos, need in _MIN_PER_POSITION.items())


#: Windfall-Schwelle (Emil 20.10.2026): Auf gelistete Bank-/Schaufensterspieler
#: (`player_id in bench_ids`) werden Mitspieler-Übergebote ab 115 % MW auch dann
#: autonom angenommen, wenn unser Schaufenster-Anker (`listing_price`) höher stand.
ACCEPT_WINDFALL_FLOOR = 1.15
#: Schaufenster-Ankerpreis (125 % MW) für eigene Bankspieler außerhalb der Startelf.
SHOWCASE_MARKUP = 1.25


def _is_bench_windfall(
    offer: IncomingOffer,
    bench_ids: set[str] | frozenset[str] | None,
) -> bool:
    """Ist das Angebot ein Übergebot (≥ 115 % MW) auf einen expliziten Bankspieler?"""
    return (
        bench_ids is not None
        and offer.player_id in bench_ids
        and offer.market_value > 0
        and offer.amount >= offer.market_value * ACCEPT_WINDFALL_FLOOR
    )


def offers_below_asking(
    offers: list[IncomingOffer],
    squad_positions: dict[str, int],
    *,
    bench_ids: set[str] | frozenset[str] | None = None,
) -> list[IncomingOffer]:
    """Bestes Angebot je Spieler, das UNTER unserem Listungspreis liegt (16.09.).

    Diese Angebote werden nie autonom angenommen — der Preis ist Emils
    Entscheidung (Ausnahme seit 20.10.: Windfall-Übergebote ≥ 115 % MW auf
    explizite Bankspieler). Sie werden aber gemeldet, damit er sie sehen und
    selbst annehmen kann. Nur Spieler im Kader, nur Einträge mit uoid, nur wenn
    ein Listungspreis bekannt ist. Reihenfolge: größter Betrag zuerst.
    """
    best: dict[str, IncomingOffer] = {}
    for o in offers:
        if o.player_id not in squad_positions or not o.offer_id or o.listing_price <= 0:
            continue
        if o.amount >= o.listing_price or _is_bench_windfall(o, bench_ids):
            continue
        cur = best.get(o.player_id)
        if cur is None or o.amount > cur.amount:
            best[o.player_id] = o
    return sorted(best.values(), key=lambda x: -x.amount)


def propose_accepts(
    offers: list[IncomingOffer],
    squad_positions: dict[str, int],
    league_id: str,
    guardrails: Guardrails,
    *,
    min_squad_size: int = _MIN_SQUAD_SIZE,
    bench_ids: set[str] | frozenset[str] | None = None,
) -> list[Proposal]:
    """Annahme-Vorschläge für Angebote auf eigene Listings (Accept-Pfad, 24.08.).

    Abgrenzung zu `evaluate_incoming_offer`: Diese Funktion behandelt Angebote
    auf Spieler, die WIR aktiv gelistet haben — die Annahme ist der geplante
    Abschluss des Listings, deshalb zählt hier NUR der Preis-Floor
    (`accept_offer_floor`, ≥ 103 % MW), keine MW-Prognose (Konto-Disziplin
    schlägt Kurspflege; ein 22-Uhr-MW-Sprung nach dem Anlegen ist bewusst
    akzeptiertes Restrisiko). `evaluate_incoming_offer` bleibt für
    unaufgeforderte Angebote auf NICHT gelistete Spieler.

    Regeln:
    - höchstes Angebot je Spieler zählt (mehrere `ofs`-Einträge → Maximum),
    - Reihenfolge: größter Betrag zuerst (Konto-Wirkung — der Khannouss-Dreher
      vor den Kleinen),
    - Schutzliste gilt,
    - **Listungspreis gilt (16.09., erweitert 20.10.):** Ist `listing_price`
      bekannt, wird ein Angebot darunter bei Startern NIE vorgeschlagen (Olise
      zu 100 M heißt: nicht für 67 M). Bei expliziten Bankspielern (`bench_ids`)
      wird ein Windfall-Übergebot ab 115 % MW (`ACCEPT_WINDFALL_FLOOR`) auch
      unterhalb des Schaufenster-Ankers angenommen.
    - Kader-Deckung wie propose_sells: `min_squad_size` > 0 (Default 11 — das
      T-120-Fenster, in dem jeder Leerplatz 100 Punkte kostet) verlangt nach
      JEDEM Accept (inkl. der früheren Vorschläge dieses Laufs) ≥ 11 Spieler
      und eine stellbare Formation; `min_squad_size=0` (außerhalb des Fensters,
      Kader = Portfolio) verlangt nur die Positions-Minima 1 TW/3 ABW/2 MF/1 ST
      — dieselbe Zweiteilung wie im Executor-Preflight (L-095). Der einzige
      einsetzbare Stürmer (Vogt-Fall) wird in beiden Fällen nie verkauft.
    Tages-Caps deckelt der Intent-Pfad (upsert_intents/cap_reason zählen
    accepts als Verkäufe).
    """
    best: dict[str, IncomingOffer] = {}
    for o in offers:
        if o.player_id not in squad_positions or not o.offer_id:
            continue  # nicht (mehr) im Kader bzw. ohne uoid nicht annehmbar
        cur = best.get(o.player_id)
        if cur is None or o.amount > cur.amount:
            best[o.player_id] = o

    proposals: list[Proposal] = []
    remaining_counts = Counter(squad_positions.values())
    remaining_size = len(squad_positions)
    for o in sorted(best.values(), key=lambda x: -x.amount):
        if o.player_id in guardrails.all_protected_ids:
            continue
        if o.market_value <= 0 or o.amount < o.market_value * guardrails.accept_offer_floor:
            continue  # unter dem Floor verfallen lassen — morgen neu listen
        windfall = _is_bench_windfall(o, bench_ids)
        if o.listing_price > 0 and o.amount < o.listing_price and not windfall:
            continue  # unter Emils Preis — nie autonom, nur melden
        after = remaining_counts.copy()
        after[squad_positions[o.player_id]] -= 1
        if min_squad_size > 0:
            covered = (
                remaining_size - 1 >= min_squad_size and _can_field_any_formation(after)
            )
        else:
            covered = _position_minimums_kept(after)
        if not covered:
            continue  # Deckung risse — Angebot verfallen lassen
        pct = o.amount / o.market_value * 100
        if o.listing_price > 0 and o.amount >= o.listing_price:
            asking = f" und ≥ Listungspreis {_fmt_m2(o.listing_price)}"
        elif windfall and o.listing_price > 0:
            asking = f" (Bank-Übergebot ≥ 115% MW unter Anker {_fmt_m2(o.listing_price)})"
        else:
            asking = ""
        proposals.append(
            Proposal(
                action=PlannedAction(
                    action_type="accept",
                    league_id=league_id,
                    player_id=o.player_id,
                    amount=o.amount,
                    offer_id=o.offer_id,
                    rule_id="accept_offer",
                ),
                rule_id="accept_offer",
                rationale=(
                    f"{o.player_name}: Angebot {_fmt_m2(o.amount)} = {pct:.0f}% MW{asking} "
                    f"auf unser Listing — annehmen (Kader-Deckung geprüft)."
                ),
                score=pct / 100,
                requires_approval=o.amount > guardrails.approval_threshold,
                player_name=o.player_name,
            )
        )
        remaining_counts = after
        remaining_size -= 1
    return proposals


def propose_declines(
    offers: list[IncomingOffer],
    squad_positions: dict[str, int],
    league_id: str,
    guardrails: Guardrails,
    *,
    bench_ids: set[str] | frozenset[str] | None = None,
) -> list[Proposal]:
    """Ablehnungs-Vorschläge für Lowball-Angebote auf eigene Listings (21.10.2026).

    Lehnt Angebote aktiv ab (`decline_offer`), wenn das höchste Angebot je Spieler:
    - unter dem Mindest-Floor (`accept_offer_floor`, 103 % MW) liegt, ODER
    - auf einem expliziten Schaufenster-Bankspieler (`player_id in bench_ids`,
      `listing_price >= 120 % MW`) unterhalb der Windfall-Schwelle (115 % MW)
      liegt.
    Wirkung: Der Mitspieler erhält sofort das Signal, dass sein Gebot zu niedrig
    war, und sein Gebots-Slot wird für eine Nachbesserung frei. Manuelle
    Starter-Listings Emils (`player_id not in bench_ids` ab 103 % MW) werden
    bewusst nicht automatisch abgelehnt, sondern über `offers_below_asking`
    gemeldet. Ohne LigaInsider-Daten (`bench_ids is None`) läuft der Pfad
    fail-safe leer.
    """
    if bench_ids is None:
        return []
    best: dict[str, IncomingOffer] = {}
    for o in offers:
        if o.player_id not in squad_positions or not o.offer_id or o.market_value <= 0:
            continue
        cur = best.get(o.player_id)
        if cur is None or o.amount > cur.amount:
            best[o.player_id] = o

    proposals: list[Proposal] = []
    for o in sorted(best.values(), key=lambda x: x.amount):
        if o.listing_price > 0 and o.amount >= o.listing_price:
            continue
        if _is_bench_windfall(o, bench_ids):
            continue
        below_floor = o.amount < o.market_value * guardrails.accept_offer_floor
        showcase_lowball = (
            o.player_id in bench_ids
            and o.listing_price >= int(o.market_value * 1.20)
            and o.amount < o.market_value * ACCEPT_WINDFALL_FLOOR
        )
        if not (below_floor or showcase_lowball):
            continue
        pct = o.amount / o.market_value * 100
        proposals.append(
            Proposal(
                action=PlannedAction(
                    action_type="decline",
                    league_id=league_id,
                    player_id=o.player_id,
                    amount=o.amount,
                    offer_id=o.offer_id,
                    rule_id="decline_lowball",
                ),
                rule_id="decline_lowball",
                rationale=(
                    f"{o.player_name}: Angebot {_fmt_m2(o.amount)} ({pct:.0f}% MW) "
                    "unter Annahmeschwelle — aktiv ablehnen (Signal an Mitspieler: "
                    "Slot frei für höheres Gebot)."
                ),
                score=pct / 100,
                requires_approval=False,
                player_name=o.player_name,
            )
        )
    return proposals


@dataclass(frozen=True)
class OwnListing:
    """Eigenes aktives Manager-Listing am Transfermarkt (`u.i == user_id`)."""

    player_id: str
    player_name: str
    team_id: str
    position: int
    market_value: int
    listing_price: int


def propose_delists(
    own_listings: list[OwnListing],
    squad_positions: dict[str, int],
    league_id: str,
    guardrails: Guardrails,
    *,
    bench_ids: set[str] | frozenset[str] | None = None,
    starter_ids: set[str] | frozenset[str] | None = None,
) -> list[Proposal]:
    """Rückzugs-Vorschläge (`delist`) für eigene Listings, die nicht mehr stehen dürfen.

    Zieht ein eigenes Agenten-Listing (`listing_price <= 130 % MW` — Emils
    manuelle Mondpreis-Anker wie 100 M bleiben unangetastet) aktiv zurück, wenn:
    - der Spieler geschützt ist (`guardrails.all_protected_ids`), ODER
    - ein früherer Bank-/Schaufensterspieler inzwischen LigaInsider-Stammkraft
      ist (`player_id not in bench_ids`) bzw. in unserer Startelf steht
      (`starter_ids`), ODER
    - sein Abgang die Positions-Minima (1 TW / 3 ABW / 2 MF / 1 ST) reißen würde.
    Ohne LigaInsider-Daten (`bench_ids is None`) läuft der Pfad fail-safe leer.
    """
    if bench_ids is None:
        return []
    counts = Counter(squad_positions.values())
    proposals: list[Proposal] = []
    for item in own_listings:
        if item.player_id not in squad_positions or item.market_value <= 0:
            continue
        if item.listing_price <= 0 or item.listing_price > int(item.market_value * 1.30):
            continue
        after = counts.copy()
        after[squad_positions[item.player_id]] -= 1
        pos_risk = not _position_minimums_kept(after)
        is_protected = item.player_id in guardrails.all_protected_ids
        is_starter = item.player_id not in bench_ids or (
            starter_ids is not None and item.player_id in starter_ids
        )
        if not (is_protected or is_starter or pos_risk):
            continue
        reason = (
            "geschützter Spieler"
            if is_protected
            else "Positions-Minimum gefährdet"
            if pos_risk
            else "inzwischen Stammspieler"
        )
        proposals.append(
            Proposal(
                action=PlannedAction(
                    action_type="delist",
                    league_id=league_id,
                    player_id=item.player_id,
                    amount=0,
                    rule_id="delist_starter",
                ),
                rule_id="delist_starter",
                rationale=(
                    f"{item.player_name}: Eigenes Listing ({_fmt_m2(item.listing_price)}) "
                    f"zurückziehen — {reason}."
                ),
                score=1.0,
                requires_approval=False,
                player_name=item.player_name,
            )
        )
    return proposals


@dataclass(frozen=True)
class FailedListing:
    """EIGENES Manager-Listing, das seit >= 48 h ohne ein einziges Angebot steht.

    Quelle ist der rohe Markt-Payload (worker.intents.failed_listings_from_raw):
    Verkäufer `u.i` = wir, `ofs[]` leer, `dt` = Listing-Zeitpunkt älter als
    ZOMBIE_LISTING_AGE. Empirie L-081: vier Listings, bis zu sechs Tage, null
    Angebote — „Listen" ist eine Absichtserklärung, kein Verkauf.
    """

    player_id: str
    player_name: str
    team_id: str
    position: int
    market_value: int
    listed_at: datetime
    age_hours: float


def propose_failed_listing_sells(
    failed: list[FailedListing],
    squad_positions: dict[str, int],
    league_id: str,
    guardrails: Guardrails,
    *,
    bench_ids: set[str] | frozenset[str] | None,
) -> list[Proposal]:
    """Sofortverkaufs-Vorschläge für gescheiterte eigene Listings (Doktrin-Satz 3).

    Momentum-Doktrin Satz 3 (Emils GO 01.09.): „Listings, die nach 48 h kein
    Angebot gesehen haben, gelten als gescheitert (L-081) und werden in
    Sofortverkauf überführt." Bis heute stand das nur als GEPLANT in der
    Strategie — der Handgriff lag bei den (abgeschalteten) Mac-Loops.

    Guards, bewusst deckungsgleich mit propose_accepts/Pass 3:
    - `bench_ids` ist PFLICHT (None → keine Vorschläge): Sofortverkauf ist
      geld-riskant, ohne LigaInsider-Status läuft der Pfad fail-safe leer
      (L-090 — ein Score-Proxy ersetzt keinen Status). Nur explizite
      LI-Bank-Spieler werden verkauft; ein gelisteter ELF-Spieler wird bewusst
      weitergehalten (L-080: Verkauf aus der Elf kostet Punkte + 100).
    - Schutzliste (Verfassung) gilt.
    - Kader-Deckung wie propose_sells/propose_accepts: nach JEDEM Vorschlag
      (inkl. der früheren desselben Laufs) bleiben >= 11 Spieler und eine
      stellbare Formation — der einzige Stürmer (Vogt-Fall) fällt nie.
    - Reihenfolge: größter Marktwert zuerst (Konto-Wirkung).
    - MV <= 0 → skip (der Executor verweigert MV-lose Verkäufe ohnehin
      fail-closed, der Vorschlag wäre totes Vorhaben).
    Tages-Caps deckelt der Intent-Pfad (upsert_intents zählt sells).
    """
    if bench_ids is None:
        return []
    proposals: list[Proposal] = []
    remaining_counts = Counter(squad_positions.values())
    remaining_size = len(squad_positions)
    for f in sorted(failed, key=lambda x: -x.market_value):
        if f.player_id not in squad_positions or f.player_id not in bench_ids:
            continue
        if f.player_id in guardrails.all_protected_ids:
            continue
        if f.market_value <= 0:
            continue
        after = remaining_counts.copy()
        after[squad_positions[f.player_id]] -= 1
        if remaining_size - 1 < _MIN_SQUAD_SIZE or not _can_field_any_formation(after):
            continue  # Deckung risse — Listing bewusst weiterhalten
        proposals.append(
            Proposal(
                action=PlannedAction(
                    action_type="sell",
                    league_id=league_id,
                    player_id=f.player_id,
                    amount=f.market_value,
                    rule_id="listing_failed",
                ),
                rule_id="listing_failed",
                rationale=(
                    f"{f.player_name}: Listing seit {f.age_hours:.0f} h ohne ein "
                    f"einziges Angebot — gescheitert (L-081), Sofortverkauf "
                    f"{_fmt_m(f.market_value)} zum vollen Marktwert statt weiter "
                    f"totes Kapital (LI: Bank, Kader-Deckung geprüft)."
                ),
                score=0.0,
                requires_approval=f.market_value > guardrails.approval_threshold,
                player_name=f.player_name,
            )
        )
        remaining_counts = after
        remaining_size -= 1
    return proposals


@dataclass(frozen=True)
class SwapPair:
    """Tausch-Paar (02.09., Emils Auftrag): Gebot auf den Ersatz + späterer Verkauf des Verlierers.

    `faller` ist der Kaderspieler, der Wert verliert, obwohl er spielt; `bid` das
    Kauf-Proposal für den Ersatz derselben Position; `sell_amount` der Marktwert
    des Verlierers zur Planungszeit (Sofortverkauf zahlt den aktuellen MW).
    """

    faller: ExplosionCandidate
    bid: Proposal
    sell_amount: int


def propose_swaps(
    squad: list[ExplosionCandidate],
    market: list[ExplosionCandidate],
    guardrails: Guardrails,
    *,
    bench_ids: set[str] | frozenset[str] | None,
    taken_pids: set[str] | frozenset[str],
    budget: int,
    league_id: str,
    manager_listing_prices: dict[str, int] | None = None,
    max_pairs: int = 3,
    purchase_reviews: dict[str, dict] | None = None,
) -> list[SwapPair]:
    """Tausch-Vorschläge für Wertverlierer MIT Startelf-Pfad (Doktrin-Satz 3, 02.09.).

    Emils Befund: Sechs Elf-Spieler verloren ~1,9 M pro Tag, und keine Regel
    fasste sie an — Pass 2 verkauft nur ohne Startelf-Pfad (Emils Regel „Gewinner
    nie verkaufen" war auf VERLIERER übertragen worden). Ein Verlierer, der
    spielt, wird nicht gehalten, sondern GETAUSCHT: erst Ersatz derselben
    Position, dann Abgang (der Verkauf wartet per after_squad_size im Worker).

    Regeln:
    - `bench_ids` PFLICHT (None → nichts): Verlierer ist, wer laut LigaInsider
      NICHT Bank ist (Elf/unbekannt), Trend-Signal ≤ sell_trend_score_max,
      nicht geschützt, nicht verletzt. Schlimmster Trend zuerst.
    - Ersatz: Marktkandidat derselben Position, nicht in `taken_pids` (Kader,
      offene Gebote, Vorschläge dieses Laufs), Kauf-Score ≥ Schwelle, Elf-Score
      ≥ dem des Verlierers, nicht verletzt — bester Kauf-Score gewinnt, jeder
      Ersatz nur einmal.
    - Preis wie propose_buys: Erstgebot MW × (1 + bid_ladder_opening) + Offset,
      Manager-Listing ankert bei max(Listing, MW) + Offset (err 5080), Kappe
      max_bid_premium, Einzelgebot ≤ budget × max_bid_pct_of_budget.
    """
    if bench_ids is None:
        return []

    def trend_of(c: ExplosionCandidate) -> float | None:
        return next((s.score for s in c.signals if s.name == "trend"), None)

    fallers: list[tuple[float, ExplosionCandidate]] = []
    for c in squad:
        if c.player_id in bench_ids or c.player_id in guardrails.all_protected_ids:
            continue
        if c.injury_severity or c.current_market_value <= 0:
            continue
        tr = trend_of(c)
        if tr is None or tr > guardrails.sell_trend_score_max:
            continue
        fallers.append((tr, c))
    fallers.sort(key=lambda x: x[0])

    max_single = int(budget * guardrails.max_bid_pct_of_budget)
    used: set[str] = set(taken_pids)
    pairs: list[SwapPair] = []
    for _tr, f in fallers:
        if len(pairs) >= max_pairs:
            break
        options = [
            m
            for m in market
            if m.position == f.position
            and m.player_id not in used
            and not m.injury_severity
            and m.current_market_value > 0
            and m.total_score >= guardrails.buy_score_threshold
            and m.lineup_score >= f.lineup_score
            and (
                purchase_reviews is None
                or (purchase_reviews.get(m.player_id) or {}).get("eligible")
            )
        ]
        if not options:
            continue
        m = max(options, key=lambda x: x.total_score)
        mv = m.current_market_value
        cap = int(mv * guardrails.max_bid_premium_need)
        opening = int(mv * (1 + guardrails.bid_overpay_need)) + guardrails.bid_psych_offset
        listing_price = (manager_listing_prices or {}).get(m.player_id)
        if listing_price is not None:
            opening = max(opening, max(listing_price, mv) + guardrails.bid_psych_offset)
        opening = min(opening, cap)
        review = (purchase_reviews or {}).get(m.player_id)
        if review is not None:
            cap = min(cap, int(review["max_bid_eur"]))
            opening = min(opening, cap)
        # Ein Tausch finanziert sich aus dem eigenen Verkauf (Sofortverkauf zum
        # MW des Verlierers direkt nach dem Zuschlag): Die Kappe der Plan-Basis
        # gilt deshalb ZUZÜGLICH des Verkaufserlöses. Ohne das wäre der Tausch
        # bei Plan-Basis 0 (Konto durch offene Gebote gebunden) unmöglich —
        # genau der Zustand vom 02.09. 13:25 („keiner passt in die Kappe 0 M").
        if opening > max_single + int(f.current_market_value):
            continue
        target = min(
            int(mv * (1 + guardrails.bid_overpay_need)) + guardrails.bid_psych_offset, cap
        )
        trend_reason = next(
            (s.reason for s in f.signals if s.name == "trend"), "Marktwert fällt."
        )
        bid = Proposal(
            action=PlannedAction(
                action_type="bid",
                league_id=league_id,
                player_id=m.player_id,
                amount=opening,
                rule_id="swap_bid",
                squad_need=True,
            ),
            rule_id="swap_bid",
            rationale=(
                f"{m.name}: Tausch für {f.name} — Kauf-Score {m.total_score:.2f}, "
                f"Elf-Score {m.lineup_score:.2f} ≥ {f.lineup_score:.2f}; Erstgebot "
                f"{_fmt_m(opening)}, Ziel {_fmt_m(target)}. {f.name} fällt "
                f"({trend_reason}) und geht, sobald {m.name} im Kader steht."
            ),
            score=m.total_score,
            requires_approval=opening > guardrails.approval_threshold,
            player_name=m.name,
        )
        pairs.append(SwapPair(faller=f, bid=bid, sell_amount=int(f.current_market_value)))
        used.add(m.player_id)
    return pairs


def evaluate_incoming_offer(
    player_id: str,
    market_value: int,
    offer_price: int,
    offer_id: str,
    league_id: str,
    mv_prediction_7d: float,
    guardrails: Guardrails,
) -> Proposal | None:
    """Reaktion auf ein eingehendes Angebot: annehmen, ablehnen (geschützt) oder ignorieren."""
    premium = offer_price / market_value if market_value else 0.0

    if player_id in guardrails.all_protected_ids:
        return Proposal(
            action=PlannedAction(
                action_type="decline",
                league_id=league_id,
                player_id=player_id,
                offer_id=offer_id,
                rule_id="protect_player",
            ),
            rule_id="protect_player",
            rationale=f"Geschützter Spieler — Angebot {_fmt_m(offer_price)} abgelehnt.",
            score=0.0,
            requires_approval=False,
        )

    # Annehmen nur, wenn deutlich über MW UND der Marktwert nicht weiter steigt.
    if premium >= guardrails.accept_offer_floor and mv_prediction_7d <= 0:
        return Proposal(
            action=PlannedAction(
                action_type="accept",
                league_id=league_id,
                player_id=player_id,
                offer_id=offer_id,
                amount=offer_price,
                rule_id="accept_good_offer",
            ),
            rule_id="accept_good_offer",
            rationale=(
                f"Angebot {_fmt_m(offer_price)} = {premium * 100:.0f}% MW, "
                f"7-Tage-Prognose {mv_prediction_7d * 100:+.1f}% — annehmen."
            ),
            score=premium,
            requires_approval=offer_price > guardrails.approval_threshold,
        )
    return None  # sonst: Angebot aussitzen (verfällt)


def buy_blocker_reason(
    candidates: list[ExplosionCandidate],
    budget: int,
    guardrails: Guardrails,
    need_positions: set[int] | None = None,
) -> str | None:
    """Warum kam KEIN Kaufvorschlag zustande? Ein Satz — oder None, wenn doch einer ginge.

    Lehre vom 28.08.: Der Planner legte null Vorhaben an, während das Kader-Gesetz
    verletzt war (0 aufstellbare Stuermer). Der Grund — kein Kandidat passte in die
    an das KONTO gekoppelte Gebotskappe (60 % von 9,23 M = 5,54 M), obwohl die
    Kaufkraft 56 M auswies — war nur durch Code-Lesen und einen Box-Lauf zu
    ermitteln. Stilles Nichtstun ist laut Mandat ein Mandatsbruch; diese Funktion
    macht das Nichtstun begruendbar.

    Reine Diagnose: aendert nichts am Geld-Verhalten, spiegelt bewusst NICHT die
    volle Gebotsformel (die lebt in propose_buys), sondern unterscheidet die drei
    Faelle, die in der Praxis auftreten — leeres Universum, zu schwache Scores,
    zu wenig Konto. Bei Geldmangel wird der BILLIGSTE Kandidat ueber der Schwelle
    genannt: an ihm laesst sich ablesen, wie viel Konto fehlt.
    """
    if not candidates:
        return "Kauf-Universum leer — kein lebendes Listing am Markt (Zombie-Filter)."

    def _threshold(c: ExplosionCandidate) -> float:
        if need_positions and c.position in need_positions:
            return guardrails.buy_score_threshold - guardrails.position_need_tolerance
        return guardrails.buy_score_threshold

    over = [c for c in candidates if c.total_score >= _threshold(c)]
    if not over:
        best = max(candidates, key=lambda c: c.total_score)
        return (
            f"Kein Kandidat ueber der Kauf-Schwelle — bester Score "
            f"{best.total_score:.2f} ({best.name}) < {guardrails.buy_score_threshold:.2f}. "
            "Marktlage, nicht Geldmangel."
        )

    cap = int(budget * guardrails.max_bid_pct_of_budget)
    cheapest = min(over, key=lambda c: c.current_market_value)
    opening = (
        int(cheapest.current_market_value * (1 + guardrails.bid_ladder_opening))
        + guardrails.bid_psych_offset
    )
    if opening > cap:
        return (
            f"{len(over)} Kandidat(en) ueber der Schwelle, aber keiner passt in die "
            f"Gebotskappe {_fmt_m2(cap)} ({int(guardrails.max_bid_pct_of_budget * 100)} % "
            f"der Plan-Basis {_fmt_m2(budget)} = Konto − Gebote + Momentum-Reserve). "
            f"Billigster: {cheapest.name} — Erstgebot {_fmt_m2(opening)}, es fehlen "
            f"{_fmt_m2(opening - cap)}."
        )
    return None
