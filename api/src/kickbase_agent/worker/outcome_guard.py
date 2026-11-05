"""Ergebnis-Wächter (Turnaround-Studie 19.09.2026, P0-4): Zustände melden, nicht Läufe.

Anlass: 94 Stunden bei −8,6 M Konto ohne einen einzigen Write und 1049 „ok"-Läufe
bei Kader 8. Jeder Job war für sich grün — das ERGEBNIS (elf Spieler, Konto im Plus
beim Anpfiff) war es nicht, und niemand hat es gemerkt. Dieser Wächter prüft deshalb
Ergebnisse (SLOs) statt Job-Erfolge:

(a) offene Kaderplätze über dem Gleitpfad zur Spieltags-Frist,
(b) — als Anreicherung von (a) — bezahlbare Kickbase-Auktionen, die vor der Frist
    enden und auf die noch kein Gebot liegt,
(c) Konto minus Summe aller eigenen Live-Gebote (inklusive App-Gebote) unter dem
    Anpfiff-Puffer: sofort und fristunabhängig. Einzige Ausnahme bis Sa 31.10.2026
    24:00 Berlin (Planungs-Dispo als Tausch-Finanzierung, Emil 22./23.09.2026): ein
    Live-Gebot, dessen gekoppelter Sofortverkauf als Vorhaben wartet und dessen
    Verkaufsspieler noch im Kader steht, wird mit Marktwert × 0,90 (höchstens dem
    Gebotsbetrag) gedeckt; dann muss zusätzlich die Kickbase-33-%-Grenze halten.
    Ohne solchen Tausch gilt die Puffer-Regel unverändert — auch vor der Frist,
(d) Konto selbst länger als 24 h unter dem Puffer,
(e) rein meldend: Statuswechsel (Fitness, KB-Stufe, LigaInsider) eines Spielers mit
    mehr als 30 % Teamwert.

Handels-Autopilot (Emil 01.10.2026: „sie bleiben Bank“, „ins Minus bis 20 M“): Handels-
spieler zählen in (a) keinen Platz, und in (c)/(d) deckt sich der Handel über seinen Verkauf
spätestens am Verkaufstermin — Handelsspieler und Ziele laufender Handelsgebote zählen mit
Marktwert × 0,90 (`trade_cover`). Gewollter Handel ins Minus ist kein Alarm; reicht auch der
Verkauf nicht, meldet der Wächter wie bisher.

Bewusst KEINE neue Stufe in `schedule_kickoff_guards` (versiegelte Anpfiff-Kaskade)
und KEIN Teil von `run_job_watchdog` (der prüft Job-Läufe, nicht Ergebnisse). Der
Wächter schreibt nie nach Kickbase; er liest und meldet.

Zustellung (Emil 23.09.2026: „Discord soll wieder senden. Aber nur einmal am Tag eine
Zusammenfassung"): Jeder neue Befund wird als Bericht `blocker` mit Quelle
`FINDINGS_SOURCE` gespeichert und erscheint in der nächsten Tageszusammenfassung
(08:15, worker/daily_summary.py). Sofort über `send_blocker` geht nur, was Warten
Punkte kostet: Deckung und Gleitpfad innerhalb von 24 h vor einer BEKANNTEN
Spieltags-Frist (`deliver_now`). Statuswechsel, Dauer-Minus und Dispo-Glättung
kommen nur in die Zusammenfassung. Ein zurückgestellter Befund, der später in das
24-h-Fenster rückt, wird dann sofort zugestellt (getrennte Schlüsselmengen).

Zustand über Läufe hinweg (Beginn des Konto-Minus, letzter beobachteter
Spielerstatus, bereits gemeldete Schlüssel) liegt als `meta` in der bestehenden
Berichtstabelle (`Report`, Quelle `worker:outcome_guard`) — keine neue Tabelle,
keine Migration. Geschrieben wird nur bei ÄNDERUNG, nie je Lauf.

Auslegungen (nicht wörtlich in der Studie, hier festgelegt):
- Gleitpfad: erlaubte offene Plätze = 3, solange die Frist mehr als 7 Tage entfernt
  ist; 2 zwischen 7 Tagen und 96 h; 1 zwischen 96 h und 72 h; 0 ab 72 h. Die Studie
  nennt nur „3 bis T-7 Tage, 2 bis T-96 h, 0 ab T-72 h" — die Stufe 1 dazwischen ist
  die Auslegung. Frist unbekannt → kein Gleitpfad-Alarm.
- Dedupe-Schlüssel (SLO, Kadergröße, Berliner Kalendertag): ein andauernder roter
  Zustand erinnert höchstens einmal täglich; ändert sich die Kadergröße, gibt es
  sofort eine neue Karte. Statuswechsel tragen zusätzlich Spieler und neuen Status.
- „Länger als 24 h" ist streng größer; die Uhr startet mit dem ersten Wächterlauf,
  der das Konto unter dem Puffer sieht.
- Fehlende Werte (None, LigaInsider „unknown") sind keine Beobachtung und damit
  kein Statuswechsel; Vergleichsbasis bleibt der zuletzt BEOBACHTETE Wert.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from kickbase_agent.agent.rules import load_agent_rules
from kickbase_agent.config import get_settings
from kickbase_agent.db.models import Intent, Report
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.kickoff_freeze import KICKOFF_MIN_BALANCE
from kickbase_agent.decision.lineup import MIN_SQUAD_SIZE
from kickbase_agent.decision.overdraft import (
    PLANNING_OVERDRAFT_UNTIL,
    overdraft_active,
    swap_sale_proceeds,
)
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify.report_store import save_report
from kickbase_agent.trading.finance import overdraft_floor
from kickbase_agent.trading.trade_book import (
    load_trade_book,
    regular_fill,
    remaining_updates_until_friday,
    sale_cover,
    sale_deadline,
)
from kickbase_agent.worker.kickoff_guard import matchday_deadline

_log = logging.getLogger(__name__)
_BERLIN = ZoneInfo("Europe/Berlin")

#: Feste Quelle der Zustandszeilen in `reports`.
SOURCE = "worker:outcome_guard"
#: Quelle der Befund-Berichte (kind `blocker`), aus denen die Tageszusammenfassung
#: liest. Bewusst NICHT `SOURCE`: `load_memory` nimmt die jüngste `SOURCE`-Zeile.
FINDINGS_SOURCE = "worker:outcome_guard:befund"

SLO_SQUAD_PATH = "kader_gleitpfad"
SLO_COVERAGE = "deckung"
SLO_BALANCE_STALE = "konto_dauerhaft"
SLO_STATUS_CHANGE = "statuswechsel"
SLO_DISPO_SMOOTHING = "dispo_glaettung"

#: Pflichtsatz jeder Karte (Studie P0-4) — die Karte darf nie als Aufforderung zum
#: Deckungsverkauf gelesen werden.
NO_COVER_SALE_SENTENCE = "kein Verkauf zur Deckung geplanter Gebote"

#: Eine Auktion muss so lange VOR der Frist enden, damit der Zuschlag noch in die
#: Elf kommt (später Elf-Lücken-Check läuft bei T-8).
AUCTION_END_MARGIN = timedelta(minutes=12)
#: Höchstzahl genannter Auktionen je Karte.
MAX_LISTED_AUCTIONS = 3
#: Ab diesem Anteil am Teamwert ist ein Statuswechsel meldepflichtig.
BIG_PLAYER_SHARE = 0.30
#: (d): so lange darf das Konto unter dem Puffer liegen, bevor die zweite Karte kommt.
LOW_BALANCE_GRACE = timedelta(hours=24)
#: Dispo-Glättung (Review 23.09.2026, Befund 2b): so lange nach dem Scheitern des
#: gekoppelten Verkaufs hat der Planner (alle 10 min) Zeit, die Glättung anzulegen.
DISPO_SMOOTHING_GRACE = timedelta(minutes=15)
#: Gemeldete Schlüssel älter als so viele Tage fliegen aus dem Gedächtnis.
_SENT_KEY_DAYS = 3
#: Emil 23.09.2026: Diese Befunde kosten beim Warten Punkte (Konto im Minus beim
#: Anpfiff = null Punkte, leerer Elf-Platz = 100 Punkte) und gehen deshalb im
#: Fenster vor der Frist sofort raus statt in die Tageszusammenfassung.
URGENT_SLOS = frozenset({SLO_COVERAGE, SLO_SQUAD_PATH})
#: So lange vor einer bekannten Spieltags-Frist gilt die Sofortzustellung.
URGENT_WINDOW = timedelta(hours=24)

_STATUS_LABELS = (("fitness", "Fitness"), ("kb", "KB-Stufe"), ("li", "LigaInsider"))


@dataclass(frozen=True)
class SquadEntry:
    """Ein Kaderspieler, reduziert auf das, was der Wächter bewertet."""

    player_id: str
    name: str
    market_value: int
    fitness: int | None = None
    kb_prob: int | None = None
    li_status: str | None = None
    team_id: str = ""
    #: 1 TW · 2 ABW · 3 MF · 4 ST; 0 = unbekannt (dann zählt der Wächter Köpfe).
    position: int = 0


@dataclass(frozen=True)
class OwnBid:
    """Eigenes Live-Gebot aus `get_own_bids` — auch in der App abgegebene."""

    player_id: str
    name: str
    amount: int


@dataclass(frozen=True)
class Listing:
    """Marktangebot; `seller_id == ""` heißt Kickbase-Auktion mit echtem Ablauf."""

    player_id: str
    name: str
    price: int
    expires_at: datetime
    seller_id: str = ""
    #: None = nicht beobachtet. Genannt werden nur Ziele, die nicht belegt ausfallen.
    fitness: int | None = None
    kb_prob: int | None = None
    #: Marktwert des Spielers (der Listingpreis kann seit dem Einstellen abweichen). Nur er
    #: zählt, wenn ein Handelsgebot über den späteren Verkauf gedeckt wird.
    market_value: int | None = None


@dataclass(frozen=True)
class SwapSale:
    """Wartender Dispo-Tauschverkauf (Vorhaben `swap_sell` mit `swap_dispo`)."""

    player_id: str
    for_player_id: str
    #: Kadergröße, ab der der Executor verkauft (`sources.after_squad_size`); None =
    #: unlesbar — dann verkauft der Executor nie, der Erlös deckt nichts.
    after_squad_size: int | None


@dataclass(frozen=True)
class DispoDebt:
    """Jüngster Dispo-Verkauf eines Spielers, der nicht mehr offen ist (Befund 2b)."""

    player_id: str
    for_player_id: str
    status: str
    result: str
    updated_at: datetime


@dataclass(frozen=True)
class GuardState:
    """Alles, was ein Lauf gelesen hat — Eingabe der reinen Bewertung."""

    now: datetime
    squad: tuple[SquadEntry, ...]
    budget: int
    own_bids: tuple[OwnBid, ...] = ()
    market: tuple[Listing, ...] = ()
    matchday: int | None = None
    deadline: datetime | None = None
    #: Spieler mit bereits angelegtem (geplantem) Gebots-Vorhaben.
    planned_bid_ids: frozenset[str] = frozenset()
    #: Wartende Dispo-Tauschverkäufe (Planungs-Dispo bis 31.10.2026) und -Glättungen.
    swap_sales: tuple[SwapSale, ...] = ()
    #: Gescheiterte/abgeräumte/übersprungene Dispo-Verkäufe (jüngster je Spieler).
    dispo_debts: tuple[DispoDebt, ...] = ()
    #: Handelsbuch (Handels-Autopilot): Handelsspieler im Kader, Spieler mit laufendem
    #: Handelsgebot und der Verkaufstermin aller Handelsspieler.
    trade_ids: frozenset[str] = frozenset()
    trade_bid_ids: frozenset[str] = frozenset()
    sale_by: datetime | None = None


@dataclass(frozen=True)
class GuardMemory:
    """Zustand aus früheren Läufen (liegt als `meta` in `reports`)."""

    low_balance_since: datetime | None = None
    player_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Sofort über Discord zugestellt.
    sent_keys: frozenset[str] = frozenset()
    #: Nur für die Tageszusammenfassung gespeichert (noch nicht zugestellt).
    deferred_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Finding:
    """Ein roter Zustand: SLO, Dedupe-Schlüssel und fertiger Kartentext."""

    slo: str
    key: str
    text: str


# --- reine Bewertung ---------------------------------------------------------


def allowed_open_slots(remaining: timedelta) -> int:
    """Gleitpfad: wie viele Kaderplätze dürfen bei dieser Restzeit noch offen sein?

    Auslegung (siehe Moduldocstring): 3 bei mehr als 7 Tagen, 2 bis 96 h, 1 bis
    72 h, 0 ab 72 h. Die Grenzen selbst zählen zur strengeren Stufe.
    """
    if remaining > timedelta(days=7):
        return 3
    if remaining > timedelta(hours=96):
        return 2
    if remaining > timedelta(hours=72):
        return 1
    return 0


#: Zulässige Formationen (ABW, MF, ST) — dieselben wie in decision/lineup.
_FORMATIONS: tuple[tuple[int, int, int], ...] = (
    (3, 4, 3), (3, 5, 2), (3, 6, 1),
    (4, 3, 3), (4, 4, 2), (4, 5, 1),
    (5, 2, 3), (5, 3, 2), (5, 4, 1),
)


def fillable_slots(squad: tuple[SquadEntry, ...] | list[SquadEntry]) -> int:
    """Besetzbare Elf-Plätze in der besten zulässigen Formation.

    Elf Köpfe sind keine Elf: zwei Torhüter oder kein Stürmer lassen einen Platz leer,
    und jeder leere Platz kostet 100 Punkte (Review-Befund 21.09.2026). Fehlt die
    Position auch nur bei einem Spieler, zählt der Wächter wie zuvor die Köpfe.
    """
    if any(e.position not in (1, 2, 3, 4) for e in squad):
        return min(MIN_SQUAD_SIZE, len(squad))
    count = {pos: sum(1 for e in squad if e.position == pos) for pos in (1, 2, 3, 4)}
    return max(
        min(count[1], 1)
        + sum(min(count[pos], need) for pos, need in zip((2, 3, 4), form, strict=True))
        for form in _FORMATIONS
    )


def _fmt(amount: int) -> str:
    """Geldbetrag deutsch: ab 100.000 € in Millionen, darunter in Euro."""
    if abs(amount) >= 100_000:
        return f"{amount / 1_000_000:.2f}".replace(".", ",") + " M"
    return f"{amount:,}".replace(",", ".") + " €"


def _fmt_time(moment: datetime) -> str:
    local = _aware(moment).astimezone(_BERLIN)
    tag = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")[local.weekday()]
    return f"{tag} {local:%d.%m. %H:%M}"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _day(now: datetime) -> date:
    return _aware(now).astimezone(_BERLIN).date()


def _key(slo: str, state: GuardState, extra: str = "") -> str:
    base = f"{slo}|{len(state.squad)}|{_day(state.now).isoformat()}"
    return f"{base}|{extra}" if extra else base


def exposure(state: GuardState) -> int:
    """Summe aller eigenen Live-Gebote („alle schlagen zu")."""
    return sum(int(b.amount) for b in state.own_bids)


def team_value(state: GuardState) -> int:
    """Teamwert = Summe der Marktwerte des Kaders (Basis der Kickbase-Kaufkraft)."""
    return sum(max(0, int(e.market_value)) for e in state.squad)


def regular_squad(state: GuardState) -> tuple[SquadEntry, ...]:
    """Kader ohne Handelsspieler — nur er füllt die Elf (Emil 01.10.2026: „sie bleiben Bank“)."""
    return tuple(e for e in state.squad if e.player_id not in state.trade_ids)


def trade_cover(state: GuardState) -> int:
    """Was der Handel bis zum Verkaufstermin zurückbringt (`trade_book.sale_cover`, dieselbe
    Rechnung wie Autopilot und Pfand-Einlöser): Handelsspieler und Ziele laufender
    Handelsgebote mit Marktwert (ggf. abzgl. konfiguriertem Freitags-Haircut) — Marktwert,
    nicht Listingpreis (Codex-Review 02.10.2026). Nach Verkaufstermin + Karenz zählen
    Handelsspieler nicht mehr: Ihr Verkauf ist gescheitert, die Deckung muss ohne sie stehen
    (Review 02.10.2026)."""
    targets = {item.player_id: int(item.market_value) for item in state.market
               if item.market_value is not None}
    rules = load_agent_rules()
    haircut = rules.friday_buffer_haircut_pct if rules.is_custom_active() else 0.0
    updates = remaining_updates_until_friday(state.now, state.sale_by)
    return sale_cover(
        held_mv=[int(e.market_value) for e in state.squad if e.player_id in state.trade_ids],
        bid_target_mv=[targets[b.player_id] for b in state.own_bids
                       if b.player_id in state.trade_bid_ids and b.player_id in targets],
        now=state.now,
        sale_by=state.sale_by,
        updates_until_friday=updates,
        haircut_per_update_pct=haircut,
    )


def effective_balance(state: GuardState) -> int:
    """Konto nach dem Verkauf der Handelsspieler (ohne Handel: das Konto)."""
    rules = load_agent_rules()
    haircut = rules.friday_buffer_haircut_pct if rules.is_custom_active() else 0.0
    updates = remaining_updates_until_friday(state.now, state.sale_by)
    return state.budget + sale_cover(
        held_mv=[int(e.market_value) for e in state.squad if e.player_id in state.trade_ids],
        bid_target_mv=[],
        now=state.now,
        sale_by=state.sale_by,
        updates_until_friday=updates,
        haircut_per_update_pct=haircut,
    )


def _squad_size_condition(raw: object) -> int | None:
    """`after_squad_size` wie der Executor liest (int ≥ 1), sonst None."""
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def swap_cover(state: GuardState) -> int:
    """Deckung durch wartende Dispo-Tauschverkäufe; 0 ab der Frist.

    Je Paar Marktwert des Verkaufsspielers × 0,90, höchstens das Live-Gebot auf sein
    Ziel — verliert das Gebot, fällt auch der Verkauf weg. Ohne Verkaufsspieler im
    Kader oder ohne Live-Gebot auf das Ziel deckt der Beleg nichts. Steht das Ziel
    schon im Kader (Zuschlag), deckt der wartende Verkauf seinen vollen Erlös.

    Zweites Review 23.09.2026, Auflage 3: Der Executor verkauft erst ab
    `after_squad_size`. Angerechnet wird nur, wenn der Kader diese Größe hat — bzw.
    vor dem Zuschlag mit dem Ziel haben wird. Fehlt ein anderer Spieler, wartet der
    Verkauf bis zur Frist und bringt kein Geld.
    """
    values = {e.player_id: int(e.market_value) for e in state.squad}
    size = len(state.squad)

    def executable(sale: SwapSale) -> bool:
        after_win = size + (0 if sale.for_player_id in values else 1)
        return sale.after_squad_size is not None and after_win >= sale.after_squad_size

    sales = [
        sale for sale in state.swap_sales if sale.player_id in values and executable(sale)
    ]
    # Nach dem Zuschlag (Ziel im Kader) ist das Minus bis zum Verkauf gewollt (Review
    # 23.09.2026, Befund 6): der wartende Verkauf deckt es, auch nach der Dispo-Frist.
    won = sum(
        swap_sale_proceeds(values[sale.player_id])
        for sale in sales
        if sale.for_player_id in values
    )
    if not overdraft_active(state.now, until=PLANNING_OVERDRAFT_UNTIL):
        return won
    bids = {b.player_id: int(b.amount) for b in state.own_bids}
    return won + sum(
        min(swap_sale_proceeds(values[sale.player_id]), bids[sale.for_player_id])
        for sale in sales
        if sale.for_player_id in bids and sale.for_player_id not in values
    )


def affordable_auctions(state: GuardState) -> list[Listing]:
    """(b): Kickbase-Auktionen, die bezahlbar sind, rechtzeitig enden und gebotsfrei sind.

    Bezahlbar = Preis ≤ Konto − Summe eigener Gebote − Anpfiff-Puffer. Rechtzeitig =
    Ende spätestens `AUCTION_END_MARGIN` vor der Frist (und noch nicht vorbei).
    Früheste Enden zuerst; höchstens `MAX_LISTED_AUCTIONS`.
    """
    if state.deadline is None:
        return []
    room = state.budget - exposure(state) - KICKOFF_MIN_BALANCE
    latest_end = _aware(state.deadline) - AUCTION_END_MARGIN
    now = _aware(state.now)
    taken = {b.player_id for b in state.own_bids} | set(state.planned_bid_ids)
    fitting = [
        item
        for item in state.market
        if item.seller_id == ""
        and item.fitness in (None, 0)
        and item.kb_prob in (None, 1, 2, 3)
        and item.price <= room
        and now < _aware(item.expires_at) <= latest_end
        and item.player_id not in taken
    ]
    fitting.sort(key=lambda item: (_aware(item.expires_at), item.price))
    return fitting[:MAX_LISTED_AUCTIONS]


def _squad_path_finding(state: GuardState) -> Finding | None:
    if state.deadline is None:
        return None
    remaining = _aware(state.deadline) - _aware(state.now)
    if remaining <= timedelta(0):
        return None
    regular = regular_squad(state)
    fillable = fillable_slots(regular)
    open_slots = max(0, MIN_SQUAD_SIZE - fillable)
    allowed = allowed_open_slots(remaining)
    if open_slots <= allowed:
        return None
    hours = int(remaining.total_seconds() // 3600)
    rest = f"{hours // 24} Tage" if hours >= 96 else f"{hours} h"
    spieltag = f"Spieltag {state.matchday}, " if state.matchday is not None else ""
    room = state.budget - exposure(state) - KICKOFF_MIN_BALANCE
    trade = (f" (plus {len(state.squad) - len(regular)} Handelsspieler auf der Bank)"
             if len(regular) < len(state.squad) else "")
    lines = [
        f"**Kader unter dem Gleitpfad** — {len(regular)} Spieler{trade}, {fillable}/"
        f"{MIN_SQUAD_SIZE} Plätze besetzbar, {open_slots} offen; erlaubt sind jetzt {allowed}. "
        f"Frist: {spieltag}{_fmt_time(state.deadline)} (noch {rest}). "
        "Jeder leere Platz kostet 100 Punkte.",
    ]
    auctions = affordable_auctions(state)
    if auctions:
        lines.append(
            f"Erlaubte Handlung: Gebot auf KB-Auktion — bezahlbar (Spielraum {_fmt(room)}), "
            "endet vor der Frist, noch ohne eigenes Gebot:"
        )
        lines += [
            f"• {item.name} — {_fmt(item.price)}, endet {_fmt_time(item.expires_at)}"
            for item in auctions
        ]
    else:
        lines.append(
            "Erlaubte Handlung: keine bezahlbare Kickbase-Auktion endet vor der Frist "
            f"(Spielraum {_fmt(room)}) — Emil in der App."
        )
    if state.own_bids:
        lines.append(f"Laufende eigene Gebote: {len(state.own_bids)} ({_fmt(exposure(state))}).")
    lines.append(f"Regel: {NO_COVER_SALE_SENTENCE}.")
    return Finding(SLO_SQUAD_PATH, _key(SLO_SQUAD_PATH, state), "\n".join(lines))


def _coverage_finding(state: GuardState) -> Finding | None:
    bound = exposure(state)
    worst_case = state.budget - bound
    cover = swap_cover(state)
    traded = trade_cover(state)
    if cover:
        # Tausch und Handel zugleich: Der Handel deckt sich über seinen Verkauf (Review).
        return _swap_coverage_finding(state, bound, worst_case, cover, traded)
    if traded:
        return _trade_coverage_finding(state, bound, worst_case, traded)
    if worst_case >= KICKOFF_MIN_BALANCE:
        return None
    if state.own_bids:
        lines = [
            f"**Deckung gerissen** — Konto {_fmt(state.budget)}, eigene Gebote {_fmt(bound)}: "
            f"schlagen alle zu, steht das Konto bei {_fmt(worst_case)} "
            f"(Puffer {_fmt(KICKOFF_MIN_BALANCE)}).",
        ]
        lines += [f"• {b.name or b.player_id} — {_fmt(b.amount)}" for b in state.own_bids]
    else:
        lines = [
            f"**Konto unter dem Anpfiff-Puffer** — Konto {_fmt(state.budget)} "
            f"(Puffer {_fmt(KICKOFF_MIN_BALANCE)}), keine offenen Gebote.",
        ]
    lines.append("Mit Konto im Minus beim Anpfiff gibt es null Punkte.")
    lines.append(
        "Erlaubte Handlung: Emil in der App (Gebote prüfen, Geld beschaffen). "
        f"Regel: {NO_COVER_SALE_SENTENCE}."
    )
    # Schlüssel mit Gebotszahl und Millionenstufe des schlechtesten Falls: Eine am
    # selben Tag neu aufreißende oder um ≥ 1 M wachsende Lücke ist ein NEUER Zustand
    # (Review-Befund 21.09.2026 — sonst verschluckte der Tages-Dedupe genau das
    # App-Gebot vom 18.09.). Marktwertrauschen unterhalb 1 M bleibt bei einer Karte.
    severity = f"{len(state.own_bids)}|{worst_case // 1_000_000}"
    return Finding(SLO_COVERAGE, _key(SLO_COVERAGE, state, severity), "\n".join(lines))


def _swap_coverage_finding(
    state: GuardState, bound: int, worst_case: int, cover: int, traded: int = 0,
) -> Finding | None:
    """(c) mit offenem Dispo-Tausch: netto gedeckt und innerhalb der 33-%-Grenze?

    `traded` = Handelsverkauf (`trade_cover`): zählt netto wie der Tauschverkauf, aber nicht
    für die 33-%-Grenze — die gilt für das echte Konto, wenn alle Gebote zuschlagen, und der
    Handelsverkauf kommt erst danach."""
    floor = overdraft_floor(team_value(state), state.budget)
    net = worst_case + cover + traded
    if net >= KICKOFF_MIN_BALANCE and worst_case >= floor:
        return None
    sold = f"{_fmt(cover)} angerechnet" + (f", Handel {_fmt(traded)}" if traded else "")
    reason = (
        f"netto {_fmt(net)} nach den gekoppelten Verkäufen ({sold})"
        if net < KICKOFF_MIN_BALANCE
        else f"33-%-Grenze {_fmt(floor)} gerissen ({_fmt(team_value(state))} Teamwert)"
    )
    lines = [
        f"**Tausch-Deckung gerissen** — Konto {_fmt(state.budget)}, eigene Gebote "
        f"{_fmt(bound)}: schlagen alle zu, steht das Konto bei {_fmt(worst_case)}; "
        f"{reason} (Puffer {_fmt(KICKOFF_MIN_BALANCE)}).",
    ]
    lines += [f"• {b.name or b.player_id} — {_fmt(b.amount)}" for b in state.own_bids]
    lines.append(
        f"Planungs-Dispo bis {PLANNING_OVERDRAFT_UNTIL:%d.%m.}: gedeckt wird nur durch "
        f"die wartenden Tausch-Sofortverkäufe ({len(state.swap_sales)})."
    )
    lines.append("Mit Konto im Minus beim Anpfiff gibt es null Punkte.")
    lines.append(
        "Erlaubte Handlung: Emil in der App (Gebote prüfen, Geld beschaffen). "
        f"Regel: {NO_COVER_SALE_SENTENCE}."
    )
    severity = f"tausch|{len(state.own_bids)}|{net // 1_000_000}"
    return Finding(SLO_COVERAGE, _key(SLO_COVERAGE, state, severity), "\n".join(lines))


def _trade_coverage_finding(
    state: GuardState, bound: int, worst_case: int, cover: int,
) -> Finding | None:
    """(c) mit Handel: gedeckt, wenn das Konto nach Verkauf aller Handelsspieler (und der
    Ziele laufender Handelsgebote) den Puffer hält — der Autopilot verkauft spätestens am
    Verkaufstermin. Reicht selbst das nicht, ist es ein echter Befund."""
    net = worst_case + cover
    if net >= KICKOFF_MIN_BALANCE:
        return None
    until = f" bis {_fmt_time(state.sale_by)}" if state.sale_by is not None else ""
    lines = [
        f"**Deckung gerissen trotz Handel** — Konto {_fmt(state.budget)}, eigene Gebote "
        f"{_fmt(bound)}: schlagen alle zu, steht das Konto bei {_fmt(worst_case)}; der "
        f"Verkauf der Handelsspieler{until} bringt rund {_fmt(cover)} — netto {_fmt(net)} "
        f"(Puffer {_fmt(KICKOFF_MIN_BALANCE)}).",
    ]
    lines += [f"• {b.name or b.player_id} — {_fmt(b.amount)}" for b in state.own_bids]
    lines.append("Mit Konto im Minus beim Anpfiff gibt es null Punkte.")
    lines.append(
        "Erlaubte Handlung: Emil in der App (Handelsvorhaben überspringen, Gebote prüfen). "
        f"Regel: {NO_COVER_SALE_SENTENCE}."
    )
    severity = f"handel|{len(state.own_bids)}|{net // 1_000_000}"
    return Finding(SLO_COVERAGE, _key(SLO_COVERAGE, state, severity), "\n".join(lines))


def _dispo_smoothing_findings(state: GuardState) -> list[Finding]:
    """Blocker „Dispo-Glättung gescheitert" (Review 23.09.2026, Befund 2b).

    Konto negativ, Tausch-Ziel im Kader, Verkaufsspieler noch da, und der jüngste
    Dispo-Verkauf ist seit der Karenz nicht mehr offen: Weder der gekoppelte Verkauf
    noch eine Glättung läuft (Kontingent voll, Sperre, Emils Veto, Verfassung).
    """
    if state.budget >= 0:
        return []
    squad = {e.player_id: e for e in state.squad}
    found = []
    for debt in state.dispo_debts:
        seller = squad.get(debt.player_id)
        if (
            seller is None or debt.for_player_id not in squad
            or _aware(debt.updated_at) > state.now - DISPO_SMOOTHING_GRACE
        ):
            continue
        target = squad[debt.for_player_id]
        text = "\n".join([
            f"**Dispo-Glättung gescheitert** — {seller.name or seller.player_id} "
            f"(Marktwert {_fmt(seller.market_value)}) sollte den Tausch für "
            f"{target.name or target.player_id} decken; Konto {_fmt(state.budget)}.",
            f"Letzter Verkaufsversuch: {debt.status} — {debt.result or 'ohne Angabe'}.",
            "Mit Konto im Minus beim Anpfiff gibt es null Punkte.",
            f"Erlaubte Handlung: Emil in der App (Sofortverkauf von "
            f"{seller.name or seller.player_id} prüfen). Tageskontingent und Sperren werden "
            "nicht umgangen.",
        ])
        severity = f"{debt.player_id}|{debt.status}|{state.budget // 1_000_000}"
        found.append(Finding(SLO_DISPO_SMOOTHING, _key(SLO_DISPO_SMOOTHING, state, severity),
                             text))
    return found


def _balance_stale_finding(state: GuardState, memory: GuardMemory) -> Finding | None:
    since = memory.low_balance_since
    if effective_balance(state) >= KICKOFF_MIN_BALANCE or since is None:
        return None
    age = _aware(state.now) - _aware(since)
    if age <= LOW_BALANCE_GRACE:
        return None
    text = (
        f"**Konto seit über 24 h unter dem Puffer** — Konto {_fmt(state.budget)}, unter "
        f"{_fmt(KICKOFF_MIN_BALANCE)} seit {_fmt_time(since)} "
        f"({int(age.total_seconds() // 3600)} h). Kein Write hat das bisher behoben.\n"
        f"Erlaubte Handlung: Emil in der App. Regel: {NO_COVER_SALE_SENTENCE}."
    )
    return Finding(SLO_BALANCE_STALE, _key(SLO_BALANCE_STALE, state), text)


def _observed(entry: SquadEntry) -> dict[str, Any]:
    li = entry.li_status if entry.li_status not in (None, "", "unknown") else None
    return {"fitness": entry.fitness, "kb": entry.kb_prob, "li": li}


def _status_findings(state: GuardState, memory: GuardMemory) -> list[Finding]:
    total = sum(max(0, int(e.market_value)) for e in state.squad)
    if total <= 0:
        return []
    findings: list[Finding] = []
    for entry in state.squad:
        share = entry.market_value / total
        before = memory.player_status.get(entry.player_id)
        if share <= BIG_PLAYER_SHARE or before is None:
            continue
        current = _observed(entry)
        changes = [
            f"{label} {before.get(field_name)} → {current[field_name]}"
            for field_name, label in _STATUS_LABELS
            if current[field_name] is not None
            and before.get(field_name) is not None
            and current[field_name] != before.get(field_name)
        ]
        if not changes:
            continue
        signature = "/".join(str(current[name]) for name, _ in _STATUS_LABELS)
        text = (
            f"**Statuswechsel** — {entry.name or entry.player_id} "
            f"({share * 100:.0f} % Teamwert): {', '.join(changes)}.\n"
            "Nur Meldung, der Wächter löst nichts aus. Erlaubte Handlung: Emil in der App. "
            f"Regel: {NO_COVER_SALE_SENTENCE}."
        )
        findings.append(
            Finding(
                SLO_STATUS_CHANGE,
                _key(SLO_STATUS_CHANGE, state, f"{entry.player_id}:{signature}"),
                text,
            )
        )
    return findings


def evaluate(state: GuardState, memory: GuardMemory) -> list[Finding]:
    """Alle aktuell roten Zustände — reine Funktion, kein I/O, keine Dedupe.

    Reihenfolge = Dringlichkeit: Deckung, Dauer-Minus, Gleitpfad, Statuswechsel.
    Ob ein Fund zugestellt wird, entscheidet `fresh_findings` anhand der Schlüssel.
    """
    findings: list[Finding] = _dispo_smoothing_findings(state)
    for found in (
        _coverage_finding(state),
        _balance_stale_finding(state, memory),
        _squad_path_finding(state),
    ):
        if found is not None:
            findings.append(found)
    findings += _status_findings(state, memory)
    return findings


def fresh_findings(findings: list[Finding], memory: GuardMemory) -> list[Finding]:
    """Funde, deren Schlüssel noch nicht gemeldet wurde (eine Karte je Zustandswechsel)."""
    return [f for f in findings if f.key not in memory.sent_keys]


def deliver_now(finding: Finding, state: GuardState) -> bool:
    """Sofort zustellen statt in die Tageszusammenfassung? (Emil 23.09.2026)

    Nur Deckung und Gleitpfad, nur mit bekannter Frist und nur, solange sie höchstens
    `URGENT_WINDOW` entfernt und noch nicht vorbei ist.
    """
    if finding.slo not in URGENT_SLOS or state.deadline is None:
        return False
    remaining = _aware(state.deadline) - _aware(state.now)
    return timedelta(0) < remaining <= URGENT_WINDOW


def next_memory(
    state: GuardState,
    memory: GuardMemory,
    delivered: list[Finding],
    deferred: list[Finding] | tuple[Finding, ...] = (),
) -> GuardMemory:
    """Gedächtnis nach diesem Lauf — reine Funktion.

    Spielerstatus: beobachtete Werte überschreiben, fehlende behalten den alten
    Wert; Spieler, die den Kader verlassen haben, fallen heraus. `delivered` kam
    sofort in den Kanal, `deferred` liegt für die Tageszusammenfassung bereit.
    Die 24-h-Uhr zählt das Konto nach Verkauf der Handelsspieler (`effective_balance`).
    """
    if effective_balance(state) >= KICKOFF_MIN_BALANCE:
        since = None
    else:
        since = memory.low_balance_since or state.now
    players: dict[str, dict[str, Any]] = {}
    for entry in state.squad:
        merged = dict(memory.player_status.get(entry.player_id) or {})
        for name, value in _observed(entry).items():
            if value is not None or name not in merged:
                merged[name] = value
        players[entry.player_id] = merged
    cutoff = (_day(state.now) - timedelta(days=_SENT_KEY_DAYS)).isoformat()
    kept = {k for k in memory.sent_keys if _key_day(k) >= cutoff}
    kept |= {f.key for f in delivered}
    held = {k for k in memory.deferred_keys if _key_day(k) >= cutoff}
    held |= {f.key for f in deferred}
    return GuardMemory(low_balance_since=since, player_status=players,
                       sent_keys=frozenset(kept), deferred_keys=frozenset(held))


def _key_day(key: str) -> str:
    parts = key.split("|")
    return parts[2] if len(parts) > 2 else ""


# --- Gedächtnis in `reports` -------------------------------------------------


def memory_to_meta(memory: GuardMemory) -> dict[str, Any]:
    since = memory.low_balance_since
    return {
        "schema": 1,
        "low_balance_since": _aware(since).isoformat() if since is not None else None,
        "players": memory.player_status,
        "sent": sorted(memory.sent_keys),
        "deferred": sorted(memory.deferred_keys),
    }


def memory_from_meta(meta: dict[str, Any] | None) -> GuardMemory:
    if not meta:
        return GuardMemory()
    raw_since = meta.get("low_balance_since")
    since = _aware(datetime.fromisoformat(raw_since)) if raw_since else None
    players = {str(pid): dict(val or {}) for pid, val in (meta.get("players") or {}).items()}
    return GuardMemory(
        low_balance_since=since,
        player_status=players,
        sent_keys=frozenset(str(k) for k in meta.get("sent") or []),
        deferred_keys=frozenset(str(k) for k in meta.get("deferred") or []),
    )


def load_memory(session) -> GuardMemory:
    row = session.scalars(
        select(Report).where(Report.source == SOURCE).order_by(Report.id.desc()).limit(1)
    ).first()
    return memory_from_meta(row.meta if row is not None else None)


def _memory_body(
    memory: GuardMemory, delivered: list[Finding], deferred: list[Finding] | None = None
) -> str:
    teile = [f"{len(memory.player_status)} Spielerstatus gemerkt"]
    if memory.low_balance_since is not None:
        teile.append(f"Konto unter Puffer seit {_fmt_time(memory.low_balance_since)}")
    if delivered:
        teile.append("zugestellt: " + ", ".join(f.slo for f in delivered))
    if deferred:
        teile.append("für die Tageszusammenfassung: " + ", ".join(f.slo for f in deferred))
    return "Ergebnis-Wächter, Zustand geändert — " + "; ".join(teile) + "."


def _save_finding(session, found: Finding, stamp: datetime, *, delivered: bool) -> int | None:
    """Befund als Bericht `blocker` ablegen — Quelle der Tageszusammenfassung."""
    return save_report(
        "blocker",
        f"Ergebnis-Wächter: {found.slo}",
        found.text,
        FINDINGS_SOURCE,
        meta={"delivered": delivered, "slo": found.slo, "key": found.key,
              "sofort": delivered, "chars": len(found.text)},
        session=session,
        created_at=stamp,
    )


# --- Verdrahtung -------------------------------------------------------------


async def fetch_li_statuses(players: list[SquadEntry]) -> dict[str, str]:
    """LigaInsider-Status je Spieler-ID (starter/bench/out); unbekannt fehlt.

    Zweitquelle: fällt LigaInsider aus, bleibt der Status unbeobachtet (kein
    Wechsel), und die übrigen Prüfungen laufen weiter — der Ausfall wird geloggt.
    """
    if not players or not get_settings().ligainsider_enabled:
        return {}
    from kickbase_agent.scouts.ligainsider.availability import resolve_availability
    from kickbase_agent.scouts.ligainsider.models import Status
    from kickbase_agent.scouts.ligainsider.scraper import fetch_snapshot

    try:
        snap = await fetch_snapshot()
        avails, _quarantines = resolve_availability(
            [(p.player_id, p.name, p.team_id) for p in players],
            snap.lineups, snap.injuries, snap.scraped_tids,
        )
    except Exception:  # noqa: BLE001 — Zweitquelle darf Konto-/Kaderalarme nicht verhindern
        _log.warning("Ergebnis-Wächter: LigaInsider-Status nicht lesbar.", exc_info=True)
        return {}
    return {a.player_id: str(a.status.value) for a in avails if a.status != Status.UNKNOWN}


def _squad_entries(squad: list[Any]) -> list[SquadEntry]:
    entries = []
    for sp in squad:
        player = sp.player
        name = f"{player.first_name} {player.last_name}".strip()
        entries.append(
            SquadEntry(
                player_id=str(player.id),
                name=name or str(player.id),
                market_value=int(player.market_value or 0),
                fitness=player.fitness_status,
                kb_prob=player.lineup_prob,
                team_id=str(player.team_id or ""),
                position=int(player.position or 0),
            )
        )
    return entries


async def _with_li(entries: list[SquadEntry]) -> tuple[SquadEntry, ...]:
    """LigaInsider nur für Spieler über der Teamwert-Schwelle abfragen."""
    total = sum(max(0, e.market_value) for e in entries)
    big = [e for e in entries if total > 0 and e.market_value / total > BIG_PLAYER_SHARE]
    if not big:
        return tuple(entries)
    li = await fetch_li_statuses(big)
    return tuple(
        SquadEntry(e.player_id, e.name, e.market_value, e.fitness, e.kb_prob,
                   li.get(e.player_id), e.team_id, e.position)
        for e in entries
    )


async def _deliver(channel, text: str) -> None:
    """Sofortzustellung auch im Digest-Modus (Muster `_blocker` in worker/kickoff_guard);
    nur für Befunde, die `deliver_now` freigibt."""
    send = getattr(channel, "send_blocker", None)
    if send is None:
        await channel.send_info(text)
        return
    await send(text)


async def run_outcome_guard(channel, *, now: datetime | None = None) -> str:
    """Ein Wächterlauf: lesen, bewerten, neue rote Zustände melden, Zustand merken.

    Rückgabe: `ok` (alles grün), `alert:<slo,…>` (sofort zugestellt, `deliver_now`),
    `digest:<slo,…>` (für die Tageszusammenfassung gespeichert) — beides kann in
    einem Lauf vorkommen, dann durch Leerzeichen getrennt — oder `open:<slo,…>` (rot,
    aber schon gemeldet). Kickbase wird nur GELESEN. Ausnahmen fliegen normal — die
    Job-Hülle des Workers macht sie sichtbar.
    """
    stamp = now or datetime.now(UTC)
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"
    squad = await client.get_squad(lg)
    budget = int(await client.get_budget(lg))
    bids = await client.get_own_bids(lg, auth.user_id)
    market = await client.get_market(lg)
    upcoming = matchday_deadline(await client.get_matchdays(), stamp)

    with SessionLocal() as session:
        memory = load_memory(session)
        planned = frozenset(
            str(pid)
            for pid in session.scalars(
                select(Intent.player_id).where(Intent.kind == "bid", Intent.status == "planned")
            )
        )
        swap_sales = tuple(
            SwapSale(
                str(row.player_id), str((row.sources or {}).get("swap_for")),
                _squad_size_condition((row.sources or {}).get("after_squad_size")),
            )
            for row in session.scalars(
                select(Intent).where(Intent.kind == "sell", Intent.status == "planned")
            )
            if (
                ((row.sources or {}).get("rule_id") == "swap_sell"
                 and (row.sources or {}).get("swap_dispo") is True)
                or (row.sources or {}).get("rule_id") == "dispo_smoothing"
            )
            and isinstance((row.sources or {}).get("swap_for"), str)
        )
        # Späte Einbindung: intents zieht den ganzen Planbau nach sich.
        from kickbase_agent.worker.intents import dispo_smoothing_pair, latest_dispo_sales

        squad_pids = {str(sp.player.id) for sp in squad}
        # Nur Paare, die eine Glättung tragen (zweites Review 23.09.2026, Auflage 1):
        # verlorene Auktion, Emils Veto oder kein Zuschlag → gewöhnliche Deckungskarte.
        dispo_debts = tuple(
            DispoDebt(
                str(row.player_id), str((row.sources or {}).get("swap_for")),
                str(row.status), str(row.result or ""), _aware(row.updated_at),
            )
            for row in latest_dispo_sales(session, lg, stamp).values()
            if row.status not in ("planned", "executing", "executed")
            and isinstance((row.sources or {}).get("swap_for"), str)
            and dispo_smoothing_pair(session, row, squad_pids=squad_pids, now=stamp) is not None
        )
        book = load_trade_book(
            session, now=stamp, squad_ids=squad_pids, league_id=lg,
            live_bids={str(b.get("player_id")): int(b.get("amount") or 0)
                       for b in bids if b.get("player_id")},
        )
        deadline = upcoming[1] if upcoming else None
        state = GuardState(
            now=stamp,
            squad=await _with_li(_squad_entries(squad)),
            budget=budget,
            own_bids=tuple(
                OwnBid(str(b.get("player_id", "")), str(b.get("name", "")),
                       int(b.get("amount") or 0))
                for b in bids
            ),
            market=tuple(
                Listing(
                    str(m.player.id),
                    f"{m.player.first_name} {m.player.last_name}".strip(),
                    int(m.price),
                    m.expires_at,
                    str(m.seller_id or ""),
                    m.player.fitness_status,
                    m.player.lineup_prob,
                    int(m.player.market_value),
                )
                for m in market
            ),
            matchday=upcoming[0] if upcoming else None,
            deadline=deadline,
            planned_bid_ids=planned,
            swap_sales=swap_sales,
            dispo_debts=dispo_debts,
            trade_ids=frozenset(book.positions),
            trade_bid_ids=frozenset(book.live_bids),
            sale_by=sale_deadline(deadline, regular_fill(
                int(sp.player.position) for sp in squad
                if str(sp.player.id) not in book.positions
            )),
        )
        findings = evaluate(state, memory)
        delivered: list[Finding] = []
        deferred: list[Finding] = []
        for found in fresh_findings(findings, memory):
            if deliver_now(found, state):
                # Erst zustellen, dann merken: scheitert Discord, versucht es der
                # nächste Lauf erneut. Lieber eine doppelte Karte als die Stille.
                await _deliver(channel, found.text)
                delivered.append(found)
                _save_finding(session, found, stamp, delivered=True)
            elif found.key not in memory.deferred_keys:
                # Nicht gespeichert → nicht gemerkt: der nächste Lauf versucht es erneut.
                if _save_finding(session, found, stamp, delivered=False) is not None:
                    deferred.append(found)
        updated = next_memory(state, memory, delivered, deferred)
        if updated != memory:
            saved = save_report(
                "info",
                "Ergebnis-Wächter: Zustand",
                _memory_body(updated, delivered, deferred),
                SOURCE,
                meta=memory_to_meta(updated),
                session=session,
            )
            if saved is None:
                raise RuntimeError(
                    "Ergebnis-Wächter: Zustand nicht speicherbar — Dedupe und 24-h-Uhr "
                    "wären beim nächsten Lauf verloren."
                )
    parts = []
    if delivered:
        parts.append("alert:" + ",".join(f.slo for f in delivered))
    if deferred:
        parts.append("digest:" + ",".join(f.slo for f in deferred))
    if parts:
        return " ".join(parts)
    if findings:
        return "open:" + ",".join(f.slo for f in findings)
    return "ok"
