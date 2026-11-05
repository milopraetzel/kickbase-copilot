"""Handels-Autopilot: bietet auf Radar-Kandidaten und verkauft Handelsspieler — über Vorhaben.

Emil 01.10.2026: „alles soll automatisch geschehen ich will nicht dass ich bieten muss. Und ich
glaube ein Spieler der 12% steigt muss noch höher angesetzt werden.“ Entscheidungen im Chat:
Gebote nach Steigung · Handelsspieler bleiben auf der Bank · ins Minus bis 20 M · „auf dem
normalen Transfermarkt auch bis 130%, aber nicht bei Angeboten an andere Spieler“. Spec
`docs/superpowers/specs/2026-10-01-handels-autopilot.md`.

Der Handels-Radar (08:12, 22:35) ist die Analyse, der Autopilot (:17, :47) setzt sie um:

* **Gebote:** je Kandidat des jüngsten Radar-Berichts ein Gebots-Vorhaben zum Radar-Höchstgebot
  (Kickbase-Auktion: Ausführung 15 min vor Ende, ab 5 M mindestens 2 h Veto). Die Kaufprüfung
  (Kaufzweck `handel`, höchstens drei Stunden gültig) erneuert er bei jedem Lauf; er räumt das
  Vorhaben, sobald der Radar den Spieler nicht mehr nennt, der Marktwert sich seit dem Radar
  bewegt hat (das Update um 22:00 — erst der nächste Radar bewertet neu) oder der Rahmen nicht
  mehr reicht. Rahmen: `TRADE_CAP_EUR` (20 M) in Kaufpreisen und Handelsgeboten, höchstens
  `MAX_OPEN_TRADES` Handelsspieler und -gebote, `MAX_TRADE_BIDS_PER_DAY` Handelsgebote je Tag,
  Kickbases 33-%-Grenze mit Abstand, und Transfers nur bis `TRADE_TRANSFER_STOP` von 200.
* **Elf zuerst:** Solange die Engine ein Gebot plant oder ein eigenes Gebot läuft, legt der
  Autopilot kein neues Handelsgebot an — ein Handelsgebot hätte das Geld eines geplanten
  Elf-Kaufs gebunden. Bestehende Handelsvorhaben bleiben (die Engine plante mit ihnen).
* **Verkauf:** Sofortverkauf-Vorhaben (30 min Veto, der Preis steht bis 22:00 fest) nach dem
  ersten fallenden Update (`tfhmvt` < 0) und für alle Handelsspieler am Verkaufstermin
  (`trade_book.sale_deadline`: T−96 h vor der Spieltags-Frist bei offener Elf, sonst T−50 h),
  angelegt `FORCED_SALE_LEAD` vorher.

Kein Kickbase-Write: Der Autopilot legt nur Vorhaben an, erneuert und räumt sie. Ausgeführt
wird im Intent-Executor durch die einzige Schreibstelle (`agent/executor.py`): Kill-Switch,
Scharfschaltung `AGENT_AUTO_INTENTS`, Kaufprüfung, Stufe „Handel“ (130 % MW nur auf
Kickbase-Auktionen), fit + KB-Stufe 1–3 + letzter Tag steigend, 33-%-Regel, T-120-Freeze,
Starter-Schutz, Tageskontingente und geschützte Spieler. Emil kann jedes Vorhaben in der
Zentrale überspringen. Einen Bericht speichert er nur, wenn sich etwas geändert hat.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from kickbase_agent.config import get_settings
from kickbase_agent.db.models import Intent, Report
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.decision.kickoff_freeze import KICKOFF_MIN_BALANCE
from kickbase_agent.decision.overdraft import MIDWEEK_SWAP_BRIDGE_EUR
from kickbase_agent.decision.purchase_review import REVIEW_LIFETIME
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify.report_store import save_report
from kickbase_agent.trading.finance import overdraft_floor
from kickbase_agent.trading.trade_book import (
    POSITION_MINIMUMS,
    TRADE_CAP_EUR,
    TradeBook,
    is_trade,
    load_trade_book,
    regular_fill,
    sale_cover,
    sale_deadline,
    trade_sale_proceeds,
)
from kickbase_agent.worker import intents as wi
from kickbase_agent.worker.kickoff_guard import matchday_deadline
from kickbase_agent.worker.steiger_radar import load_history
from kickbase_agent.worker.trade_radar import (
    SOURCE as RADAR_SOURCE,
)
from kickbase_agent.worker.trade_radar import (
    TRADE_LINEUP_PROBS,
    _last_step,
    daily_values,
    updates_between,
)

log = logging.getLogger(__name__)

SOURCE = "worker:trade_autopilot"
REPORT_KIND = "recherche"
BERLIN = ZoneInfo("Europe/Berlin")
RULE_BID = "trade_autopilot_bid"
RULE_SELL = "trade_autopilot_sell"
#: Kaufzweck der Kaufprüfung — die Schreibstelle erlaubt ihm auf Kickbase-Auktionen 130 % MW.
PURPOSE = "handel"
#: Der Radar läuft 08:12 und 22:35; älter als die längste Lücke darf sein Bericht nicht sein.
RADAR_MAX_AGE = timedelta(hours=15)
#: Engine zuerst (Befund 03.10.2026): So alt darf der jüngste Mannschaftsplan höchstens sein —
#: die Engine plant mindestens stündlich neu (engine_cycle.MAX_PLAN_AGE) plus Spielraum.
ENGINE_PLAN_MAX_AGE = timedelta(minutes=90)
ENGINE_PLAN_BUYING = "Engine plant einen Kauf"
ENGINE_PLAN_STALE = (
    f"kein Engine-Plan der letzten {int(ENGINE_PLAN_MAX_AGE.total_seconds() // 60)} min"
)
ENGINE_PLAN_UNREADABLE = "Engine-Plan ohne lesbare Kaufauswahl"
#: Handelsspieler plus Handelsgebote (live und geplant) gleichzeitig.
MAX_OPEN_TRADES = 6
#: Handelsgebote je UTC-Tag (ausgeführt plus geplant) — die Schreibstelle erlaubt allen Pfaden
#: zusammen `daily_buy_cap` (8).
MAX_TRADE_BIDS_PER_DAY = 6
#: Transfers je Halbserie: ab hier keine neuen Handel mehr (ein Handel sind zwei Transfers), die
#: restlichen 60 von 200 bleiben der Engine.
TRADE_TRANSFER_STOP = 140
#: Abstand zu Kickbases Minus-Grenze (33 % des Teamwerts), damit ein Gebot nicht erst an der
#: Schreibstelle scheitert, wenn ein anderes Gebot dazukommt.
OVERDRAFT_MARGIN = 1_000_000
#: Veto-Fenster eines Sofortverkaufs (Spec): Der Preis steht bis zum Update um 22:00 fest.
SELL_VETO = timedelta(minutes=30)
#: Verkaufstermin-Vorhaben entstehen so lange vorher (Ausführung genau am Termin).
FORCED_SALE_LEAD = timedelta(hours=2)
#: Kürzer laufende Auktionen bekommen kein neues Vorhaben mehr.
MIN_AUCTION_LEFT = timedelta(minutes=20)
#: Kaufprüfung erst erneuern, wenn sie bald abläuft (spart Karten-Aktualisierungen).
REVIEW_REFRESH_BEFORE = timedelta(minutes=90)
UPDATE_TIME = time(22, 0)
#: Ende des Kickbase-Gebotsfensters vor der Frist (T-120, versiegelte Anpfiff-Kaskade).
CASCADE_WINDOW = timedelta(minutes=120)

RESULT_RADAR_GONE = (
    "Handels-Autopilot: nicht mehr im Handels-Radar (Welle gebrochen, Jev oder Rahmen)"
)
RESULT_MV_MOVED = (
    "Handels-Autopilot: Marktwert-Update seit dem Radar — der nächste Radar bewertet neu"
)
#: Halt nur für den Handel (AGENT_TRADE_AUTOPILOT=0): Gebote geräumt, Verkäufe laufen weiter.
STOP_REASON = "Handel angehalten (AGENT_TRADE_AUTOPILOT=0)"
RESULT_STOPPED = f"Handels-Autopilot: {STOP_REASON}"
RESULT_NO_SIGNAL = "Handels-Autopilot: Verkaufssignal entfallen"
RESULT_NOT_HELD = "Handels-Autopilot: kein Handelsspieler mehr im Kader"

_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _utc(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def _when(stamp: datetime) -> str:
    local = _utc(stamp).astimezone(BERLIN)
    return f"{_WEEKDAYS[local.weekday()]} {local:%d.%m. %H:%M}"


def _eur(value: int) -> str:
    sign = "-" if value < 0 else ""
    a = abs(int(value))
    text = f"{a / 1e6:.2f} M" if a >= 1_000_000 else f"{a / 1e3:.0f} k" if a >= 1_000 else f"{a} €"
    return sign + text.replace(".", ",")


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:+.0f} %".replace(".", ",")


def next_update(now: datetime) -> datetime:
    """Nächstes Marktwert-Update (22:00 Berlin) nach `now`."""
    local = _utc(now).astimezone(BERLIN)
    stamp = datetime.combine(local.date(), UPDATE_TIME, tzinfo=BERLIN)
    if stamp <= local:
        stamp = datetime.combine(local.date() + timedelta(days=1), UPDATE_TIME, tzinfo=BERLIN)
    return stamp.astimezone(UTC)


# ------------------------------------------------------------------ Eingaben


@dataclass(frozen=True)
class Candidate:
    """Kaufkandidat aus dem jüngsten Radar-Bericht (`meta["kauf"]`)."""

    player_id: str
    name: str
    mv: int
    max_bid: int
    premium: float
    r3: float | None
    expected: float | None
    horizon_days: int
    basis: str
    expires_at: datetime
    jev_return: bool = False

    @classmethod
    def from_meta(cls, row: Any) -> Candidate | None:
        """Unlesbare Zeilen fallen weg (None) — ein kaputter Bericht bietet nie."""
        if not isinstance(row, Mapping):
            return None
        try:
            mv, max_bid = int(row["mv"]), int(row["max_bid"])
            expires = datetime.fromisoformat(str(row["expires_at"]))
            if mv <= 0 or max_bid < mv or expires.tzinfo is None:
                return None
            return cls(
                player_id=str(row["player_id"]), name=str(row.get("name") or row["player_id"]),
                mv=mv, max_bid=max_bid, premium=float(row["premium"]),
                r3=None if row.get("r3") is None else float(row["r3"]),
                expected=None if row.get("expected") is None else float(row["expected"]),
                horizon_days=int(row.get("horizon_days") or 0), basis=str(row.get("basis") or ""),
                expires_at=_utc(expires), jev_return=bool(row.get("jev_return")),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Slot:
    """Frische Marktzeile eines Kandidaten."""

    player_id: str
    name: str
    team_id: str
    position: int
    mv: int
    price: int
    expires_at: datetime
    seller_id: str = ""
    fitness: int | None = None
    lineup_prob: int | None = None


@dataclass(frozen=True)
class Holding:
    """Handelsspieler im Kader: Kaufpreis aus dem Handelsbuch, letzter Schritt laut Kickbase."""

    player_id: str
    name: str
    team_id: str
    position: int
    mv: int
    buy_price: int
    last_step: int | None


@dataclass(frozen=True)
class Capacity:
    """Was neue Handelsgebote noch dürfen — ohne die geplanten Handelsvorhaben dieses Laufs."""

    exposure: int          # Kaufpreise der Handelsspieler + laufende Handelsgebote
    open_trades: int       # Handelsspieler + laufende Handelsgebote
    trade_bids_today: int  # heute ausgeführte Handelsgebote
    buys_today: int        # heute ausgeführte Gebote aller Vorhaben
    engine_bid_intents: int  # offene Gebots-Vorhaben der Engine/manuell
    after_bids: int        # Konto minus alle laufenden Gebote (Kickbases pbaa)
    floor: int             # Kickbases Minus-Grenze (−⅓ des bereinigten Teamwerts)
    #: Konto zum Anpfiff, wenn alle Gebote zuschlagen und der Handel zu 90 % MW verkauft wird
    #: (`trade_book.sale_cover`); None = ungeprüft (nur in Tests einzelner Regeln).
    coverage: int | None = None
    #: Stammkader je Position (ohne Handelsspieler); None = ungeprüft.
    regular_counts: Mapping[int, int] | None = None


@dataclass(frozen=True)
class BidPlan:
    player_id: str
    name: str
    team_id: str
    position: int
    amount: int
    candidate: Candidate
    slot: Slot
    execute_at: datetime
    deadline: datetime
    review: dict[str, Any]
    rationale: str


@dataclass(frozen=True)
class SalePlan:
    player_id: str
    name: str
    team_id: str
    position: int
    amount: int
    signal: str  # fallend | frist
    execute_at: datetime
    deadline: datetime | None
    rationale: str
    buy_price: int


# ------------------------------------------------------------------ reine Planung


def trade_cap(mv: int) -> int:
    """Technische Obergrenze eines Handelsgebots: Stufe „Handel“ der Schreibstelle (130 % MW)."""
    return int(mv * DEFAULT_GUARDRAILS.max_bid_premium_trade)


def trade_review(
    plan_amount: int, cand: Candidate, slot: Slot, *, now: datetime, sale_by: datetime | None,
) -> dict[str, Any]:
    """Kaufprüfung (Schema der Schreibstelle, Kaufzweck `handel`): höchstens drei Stunden und
    nie über das Auktionsende hinaus gültig. Geplant ist das Radar-Höchstgebot
    (`target_bid_eur`); die Preisobergrenze ist die Stufe „Handel“ (130 % MW) — so bleiben
    Emils +10 %/+20 % in der Zentrale möglich (Emil 01.10.2026: „bis 130%“)."""
    valid = min(_utc(now) + REVIEW_LIFETIME, _utc(slot.expires_at))
    days = f"{cand.horizon_days} Tagen" if cand.horizon_days != 1 else "1 Tag"
    reasons = [
        f"Handels-Radar: {_pct(cand.r3)} in 3 Tagen, letzter Tag steigend "
        f"({cand.basis or 'Grundlage unbekannt'}).",
        f"Erwartet {_pct(cand.expected)} in {days} bis zum Verkauf; Gebot "
        f"{_eur(plan_amount)} (+{(plan_amount / slot.mv - 1) * 100:.0f} % auf MW "
        f"{_eur(slot.mv)}), Obergrenze 130 % MW.",
    ]
    if sale_by is not None:
        reasons.append(f"Verkauf nach dem ersten fallenden Update, spätestens {_when(sale_by)}.")
    return {
        "schema_version": 1, "player_id": slot.player_id, "selected": True,
        "max_bid_eur": trade_cap(slot.mv), "target_bid_eur": plan_amount,
        "market_value_eur": slot.mv, "purpose": PURPOSE, "listing_type": "kickbase",
        "reasons": reasons, "observed_at": _utc(now).isoformat(),
        "valid_until": valid.isoformat(),
    }


def plan_bids(
    candidates: Iterable[Candidate],
    slots: Mapping[str, Slot],
    *,
    now: datetime,
    sale_by: datetime | None,
    capacity: Capacity,
    existing: Iterable[str] = (),
    blocked: Iterable[str] = (),
    taken: Iterable[str] = (),
    engine_busy: bool = False,
    transfers_left: bool = True,
) -> tuple[list[BidPlan], dict[str, str]]:
    """Gebotspläne in Radar-Reihenfolge; bestehende Handelsvorhaben (`existing`) zuerst, damit
    ein einmal geplantes Gebot nicht vom nächsten Kandidaten verdrängt wird.

    `taken` = Spieler mit eigenem Gebot oder fremdem (Engine-/manuellem) Vorhaben; `blocked` =
    heute gesperrte Gebote (ausgeführt, gescheitert, übersprungen). `engine_busy` oder
    aufgebrauchte Transfers sperren nur NEUE Vorhaben. Rückgabe (Pläne, Grund je Ausgelassenem).
    """
    existing, blocked, taken = set(existing), set(blocked), set(taken)
    ordered = sorted(candidates, key=lambda c: c.player_id not in existing)
    stamp = _utc(now)
    plans: list[BidPlan] = []
    skipped: dict[str, str] = {}
    kept_amount = kept_count = kept_cost = 0
    for cand in ordered:
        pid = cand.player_id
        new = pid not in existing
        slot = slots.get(pid)
        reason = None
        if slot is None or slot.seller_id:
            reason = "nicht mehr am Markt"
        elif slot.mv != cand.mv:
            reason = "Marktwert-Update seit dem Radar"
        elif new and _utc(slot.expires_at) < stamp + MIN_AUCTION_LEFT:
            # Nur für neue Vorhaben: ein geplantes führt der Executor gleich aus (Review
            # 02.10.2026 — die Grenze räumte sie sonst Minuten vor der Ausführung).
            reason = "Auktion endet gleich"
        elif sale_by is not None and updates_between(slot.expires_at, sale_by) < 1:
            reason = "kein Update vor dem Verkaufstermin"
        elif slot.fitness != 0 or slot.lineup_prob not in TRADE_LINEUP_PROBS:
            reason = "nicht fit oder KB-Stufe 4/5"
        elif (capacity.regular_counts is not None
              and capacity.regular_counts.get(slot.position, 0)
              < POSITION_MINIMUMS.get(slot.position, 0)):
            # Die Schreibstelle hält das Positions-Minimum: Ein Handelsspieler auf einer
            # Position, die der Stammkader nicht deckt, wäre unverkäuflich (Review 02.10.2026).
            reason = "Position unter dem Minimum — sein Verkauf wäre gesperrt"
        elif pid in taken:
            reason = "eigenes Gebot oder Engine-Vorhaben"
        elif new and pid in blocked:
            reason = "heute gesperrt (ausgeführt, gescheitert oder übersprungen)"
        elif new and engine_busy:
            reason = "Engine plant ein Gebot — Elf zuerst"
        elif new and not transfers_left:
            reason = f"Transfer-Reserve: ab {TRADE_TRANSFER_STOP} von 200 nur noch die Engine"
        if reason is not None:
            skipped[pid] = reason
            continue
        assert slot is not None
        amount = min(cand.max_bid, trade_cap(slot.mv))
        if capacity.exposure + kept_amount + amount > TRADE_CAP_EUR:
            skipped[pid] = f"Handelsrahmen {_eur(TRADE_CAP_EUR)} reicht nicht"
            continue
        if capacity.open_trades + kept_count >= MAX_OPEN_TRADES:
            skipped[pid] = f"schon {MAX_OPEN_TRADES} Handelsspieler/-gebote offen"
            continue
        if capacity.trade_bids_today + kept_count >= MAX_TRADE_BIDS_PER_DAY:
            skipped[pid] = f"Tageskontingent {MAX_TRADE_BIDS_PER_DAY} Handelsgebote erreicht"
            continue
        if (capacity.buys_today + capacity.engine_bid_intents + kept_count
                >= DEFAULT_GUARDRAILS.daily_buy_cap):
            skipped[pid] = "Tages-Cap Käufe aller Pfade erreicht"
            continue
        if capacity.after_bids - kept_amount - amount < capacity.floor + OVERDRAFT_MARGIN:
            skipped[pid] = "Kickbases 33-%-Grenze (mit 1 M Abstand)"
            continue
        # Deckung zum Anpfiff (Review 02.10.2026 / Emil 03.11.2026): Schlägt alles zu und bleibt
        # der Marktwert, bringt der Sofortverkauf 100 % des Marktwerts zurück — nur der Aufschlag
        # über Marktwert muss aus echtem Geld gedeckt sein, sonst verkauften Pfand-Einlöser und
        # Notbremse Stammspieler. Kosten je Gebot = max(0, Betrag − MW).
        cost = max(0, amount - trade_sale_proceeds(slot.mv))
        if (capacity.coverage is not None
                and capacity.coverage - kept_cost - cost < KICKOFF_MIN_BALANCE):
            skipped[pid] = (f"Deckung zum Anpfiff reicht nicht (Konto nach Verkauf zu 100 % MW "
                            f"unter {_eur(KICKOFF_MIN_BALANCE)})")
            continue
        sched = wi.schedule_for(stamp, amount, wi.MarketRow(
            player_id=pid, price=slot.price,
            expires_in_s=int((_utc(slot.expires_at) - stamp).total_seconds()),
            player_name=slot.name, team_id=slot.team_id, position=slot.position,
            kb_prob=slot.lineup_prob,
        ))
        review = trade_review(amount, cand, slot, now=stamp, sale_by=sale_by)
        rationale = (
            f"Handels-Autopilot: {cand.name} steigt ({_pct(cand.r3)} in 3 Tagen) — Gebot bis "
            f"{_eur(amount)} (+{(amount / slot.mv - 1) * 100:.0f} % auf MW {_eur(slot.mv)}), "
            f"erwartet {_pct(cand.expected)} bis zum Verkauf"
            + (f" spätestens {_when(sale_by)}" if sale_by is not None else "")
            + ". Bleibt auf der Bank; Verkauf nach dem ersten fallenden Update."
        )
        plans.append(BidPlan(
            player_id=pid, name=cand.name, team_id=slot.team_id, position=slot.position,
            amount=amount, candidate=cand, slot=slot, execute_at=sched.execute_at,
            deadline=_utc(slot.expires_at), review=review, rationale=rationale,
        ))
        kept_amount += amount
        kept_count += 1
        kept_cost += cost
    return plans, skipped


def plan_sales(
    holdings: Iterable[Holding],
    *,
    now: datetime,
    sale_by: datetime | None,
    deadline: datetime | None,
) -> list[SalePlan]:
    """Verkaufspläne: am Verkaufstermin alle (angelegt `FORCED_SALE_LEAD` vorher, ausgeführt
    am Termin), sonst nach einem fallenden Update (`SELL_VETO` Veto)."""
    stamp = _utc(now)
    window_end = _utc(deadline) - CASCADE_WINDOW if deadline is not None else None
    plans: list[SalePlan] = []
    for h in holdings:
        pnl = h.mv - h.buy_price
        sign = "+" if pnl >= 0 else ""
        money = f"MW {_eur(h.mv)}, gekauft {_eur(h.buy_price)}, {sign}{_eur(pnl)}"
        if sale_by is not None and stamp >= _utc(sale_by) - FORCED_SALE_LEAD:
            execute_at = max(_utc(sale_by), stamp + SELL_VETO)
            signal, end = "frist", window_end
            text = (f"Handels-Autopilot: Verkaufstermin vor der Spieltags-Frist "
                    f"({_when(sale_by)}) — Sofortverkauf zum Marktwert ({money}).")
        elif h.last_step is not None and h.last_step < 0:
            execute_at = stamp + SELL_VETO
            signal, end = "fallend", next_update(stamp)
            if window_end is not None:
                end = min(end, window_end)
            text = (f"Handels-Autopilot: {h.name} fiel beim letzten Update "
                    f"({_eur(h.last_step)}) — Sofortverkauf zum Marktwert ({money}).")
        else:
            continue
        if end is not None and execute_at >= end:
            # Fällig erst nach dem nächsten Update (dann entscheidet der neue Schritt) oder im
            # Anpfiff-Fenster (dort gehört das Konto der versiegelten Kaskade).
            continue
        plans.append(SalePlan(
            player_id=h.player_id, name=h.name, team_id=h.team_id, position=h.position,
            amount=h.mv, signal=signal, execute_at=execute_at, deadline=end, rationale=text,
            buy_price=h.buy_price,
        ))
    return plans


# ------------------------------------------------------------------ Vorhaben abgleichen


@dataclass
class SyncStats:
    created: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    superseded: list[tuple[str, str]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def changes(self) -> bool:
        return bool(self.created or self.changed or self.superseded)


def _supersede(session: Any, row: Intent, result: str, now: datetime) -> bool:
    res = session.execute(
        update(Intent)
        .where(Intent.id == row.id, Intent.status == wi.STATUS_PLANNED)
        .values(status=wi.STATUS_SUPERSEDED, result=result, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount:
        session.expire(row)
    return bool(res.rowcount)


def _is_planned_dispo_swap_bid(row: Intent) -> bool:
    """Ein noch nicht ausgeführtes (`planned`) Engine-Gebot mit gekoppeltem Tausch-Sofortverkauf
    finanziert sich aus dem Verkauf seines Tauschpartners und sperrt den Bank-Handel nicht."""
    if row.status != wi.STATUS_PLANNED:
        return False
    sources = row.sources or {}
    return (
        bool(sources.get("swap_out"))
        and wi._dispo_sale(sources.get("purchase_review")) is not None
    )


def _planned_swap_cash_need(row: Intent) -> int:
    """Echtes Konto-Geld, das ein geplanter Dispo-Tausch über Verkaufserlös + Midweek-Brücke
    hinaus bindet — bleibt in der Handelsdeckung für die Engine reserviert."""
    sale = wi._dispo_sale((row.sources or {}).get("purchase_review"))
    proceeds = int(sale.get("proceeds_eur") or 0) if isinstance(sale, Mapping) else 0
    return max(0, int(row.amount or 0) - proceeds - MIDWEEK_SWAP_BRIDGE_EUR)


def engine_busy(session: Any, now: datetime) -> bool:
    """Bindet die Engine Geld für ein Gebot? Ein Gebots-Vorhaben ohne Quelle `trade` ist
    geplant, gerade in Ausführung oder ausgeführt mit noch laufender Auktion. Dann entsteht kein
    neues Handelsgebot (Elf zuerst): Die Kaufprüfung der Engine zählt beim Ausführen frische
    Livegebote mit, ein Handelsgebot dazwischen ließe das Elf-Gebot scheitern. Ausnahme: ein
    noch ungebotener (`planned`) 1-zu-1-Dispo-Tausch mit gekoppeltem Sofortverkauf."""
    stamp = _utc(now)
    for row in session.scalars(select(Intent).where(
        Intent.kind == "bid",
        Intent.status.in_((wi.STATUS_PLANNED, wi.STATUS_EXECUTING, wi.STATUS_EXECUTED)),
    )):
        if is_trade(row.sources) or _is_planned_dispo_swap_bid(row):
            continue
        if row.status != wi.STATUS_EXECUTED:
            return True
        if row.deadline is not None and wi._as_utc(row.deadline) > stamp:
            return True
    return False


def engine_cycle_running() -> bool:
    """Läuft gerade ein Engine-Zyklus? Er baut seinen Plan außerhalb der Vorhaben-Sperre und
    rechnete ein jetzt angelegtes Handelsgebot nicht ein (Review 02.10.2026)."""
    from kickbase_agent.worker import engine_cycle

    return engine_cycle._cycle_lock.locked()


def engine_plan_reason(session: Any, now: datetime, league_id: str) -> str | None:
    """Warum die Engine gerade Vorrang hat — sonst None (Befund 03.10.2026, Emils GO).

    Maßgeblich ist der jüngste gespeicherte Mannschaftsplan (Bericht `engine_decision`), nicht
    erst ein Vorhaben: Am 02.10. wählte der Plan einen Tausch, ein Vorhaben entstand noch
    nicht, und der Autopilot band das Geld. Wählt der Plan einen Kauf, gibt es kein neues
    Handelsgebot (`ENGINE_PLAN_BUYING`). Gibt es keinen Plan der letzten
    `ENGINE_PLAN_MAX_AGE`, weiß der Autopilot nicht, ob die Engine kaufen will — dann auch
    nicht (`ENGINE_PLAN_STALE`, fail-closed); ebenso bei einem Bericht ohne lesbare Kaufauswahl
    (`ENGINE_PLAN_UNREADABLE`, Codex-Review 03.10.2026). Die Engine plant mindestens stündlich
    neu. Ein Bericht ohne Liga zählt zur angefragten — und sperrt, weil ihm der Plan fehlt."""
    stamp = _utc(now)
    for row in session.scalars(
        select(Report).where(Report.kind == "engine_decision")
        .order_by(Report.created_at.desc(), Report.id.desc()).limit(20)
    ):
        meta = row.meta if isinstance(row.meta, Mapping) else {}
        if str(meta.get("league_id", league_id)) != league_id:
            continue
        if stamp - _utc(row.created_at) > ENGINE_PLAN_MAX_AGE:
            return ENGINE_PLAN_STALE
        team_plan = meta.get("team_plan")
        purchases = team_plan.get("purchase_ids") if isinstance(team_plan, Mapping) else None
        if not isinstance(purchases, list | tuple):
            return ENGINE_PLAN_UNREADABLE
        if wi.is_swap_backed_full_lineup(team_plan):
            return None
        return ENGINE_PLAN_BUYING if purchases else None
    return ENGINE_PLAN_STALE


def _frist_unblocked(session: Any, now: datetime) -> set[str]:
    """Spieler, deren Verkaufssperre NUR aus übersprungenen Verkäufen nach fallendem Update
    stammt: Emils Veto galt dem Signal „fallend“, nicht dem Pflichtverkauf am Verkaufstermin.
    Ausgeführte, laufende oder heute gescheiterte Verkäufe sperren weiter."""
    stamp = _utc(now)
    soft: set[str] = set()
    hard: set[str] = set()
    for row in session.scalars(select(Intent).where(
        Intent.kind == "sell",
        Intent.status.in_((wi.STATUS_EXECUTED, wi.STATUS_EXECUTING, wi.STATUS_FAILED,
                           wi.STATUS_VETOED)),
    )):
        updated = wi._as_utc(row.updated_at)
        if row.status == wi.STATUS_VETOED:
            live = row.deadline is not None and wi._as_utc(row.deadline) > stamp
            if updated < stamp - wi.VETO_BLOCK and not live:
                continue
            sources = row.sources or {}
            fallend = is_trade(sources) and sources.get("signal") == "fallend"
            (soft if fallend else hard).add(str(row.player_id))
        elif updated.date() == stamp.date():
            hard.add(str(row.player_id))
    return soft - hard


def _review_due(row: Intent, now: datetime) -> bool:
    review = (row.sources or {}).get("purchase_review")
    try:
        until = datetime.fromisoformat(review["valid_until"])
    except (KeyError, TypeError, ValueError):
        return True
    return _utc(until) - _utc(now) < REVIEW_REFRESH_BEFORE


def sync_trade_intents(
    session: Any,
    *,
    now: datetime,
    league_id: str,
    bids: list[BidPlan],
    sales: list[SalePlan],
    skipped: Mapping[str, str],
    radar_report_id: int | None,
    sale_by: datetime | None,
    held: Iterable[str] = (),
) -> SyncStats:
    """Handelsvorhaben anlegen, erneuern und räumen — nur die eigenen (Quelle `trade`).

    Erneuert wie der Engine-Planer (`_apply_draft`, `_refresh_purchase_review`): konditionale
    UPDATEs, ein Veto dazwischen gewinnt, Emils Betrag wird nie überschrieben, Zeiten wandern
    nur nach vorn. Gesperrte Schlüssel (heute ausgeführt/gescheitert, Veto) bekommen kein neues
    Vorhaben. `held` = Handelsspieler im Kader (Grund beim Räumen eines Verkaufs). Committet
    am Ende."""
    stamp = _utc(now)
    held = set(held)
    stats = SyncStats(skipped=dict(skipped))
    open_rows = wi.open_intents(session)
    own = {(r.kind, r.player_id): r for r in open_rows
           if is_trade(r.sources) and str((r.sources or {}).get("league_id", league_id))
           == league_id}
    others = {(r.kind, r.player_id) for r in open_rows if not is_trade(r.sources)}
    blocked = wi.blocked_keys(session, stamp)
    counts = wi.today_intent_counts(session, stamp)
    open_sells = sum(1 for r in open_rows if r.kind in ("sell", "accept"))
    # Unter der Sperre frisch (Codex-Review 02.10.2026): Plante die Engine NACH dem Lesen des
    # Autopiloten ein Gebot oder läuft ihr Plan gerade (er rechnet ein neues Handelsgebot nicht
    # ein), entsteht kein neues Handelsgebot mehr; bestehende bleiben. Ebenso, wenn ihr jüngster
    # Plan inzwischen einen Kauf wählt (Befund 03.10.2026) — der Planer räumt die bestehenden.
    busy = (engine_busy(session, stamp) or engine_cycle_running()
            or engine_plan_reason(session, stamp, league_id) == ENGINE_PLAN_BUYING)
    frist_ok = _frist_unblocked(session, stamp)

    wanted_bids = {p.player_id: p for p in bids}
    for (kind, pid), row in list(own.items()):
        if kind != "bid" or pid in wanted_bids:
            continue
        reason = skipped.get(pid)
        result = (RESULT_MV_MOVED if reason == "Marktwert-Update seit dem Radar"
                  else wi.RESULT_NOT_ON_MARKET if reason == "nicht mehr am Markt"
                  else RESULT_STOPPED if reason == STOP_REASON
                  else RESULT_RADAR_GONE if reason is None
                  else f"Handels-Autopilot: {reason}")
        if _supersede(session, row, result, stamp):
            stats.superseded.append((row.player_name or pid, result))
    for plan in bids:
        sources = {
            "trade": True, "league_id": league_id, "rule_id": RULE_BID,
            "purchase_review": plan.review, "listing_price": plan.slot.price,
            "kb_prob": plan.slot.lineup_prob, "radar_report_id": radar_report_id,
            "trade_signal": {
                "r3": plan.candidate.r3, "basis": plan.candidate.basis,
                "premium": plan.candidate.premium, "expected": plan.candidate.expected,
                "horizon_days": plan.candidate.horizon_days, "mv_at_radar": plan.candidate.mv,
                "sale_by": _utc(sale_by).isoformat() if sale_by is not None else None,
            },
        }
        draft = wi.IntentDraft(
            kind="bid", player_id=plan.player_id, player_name=plan.name, team_id=plan.team_id,
            position=plan.position, amount=plan.amount, rationale=plan.rationale,
            sources=sources, listing_type="kickbase", deadline=plan.deadline,
            execute_at=plan.execute_at,
        )
        row = own.get(("bid", plan.player_id))
        if row is not None:
            before = (row.amount, wi._as_utc(row.execute_at))
            amount_changed = row.adjusted_from is None and row.amount != plan.amount
            if amount_changed or _review_due(row, stamp) or (
                wi._as_utc(row.execute_at) > wi._as_utc(plan.execute_at) + timedelta(minutes=1)
            ):
                wi._apply_draft(session, row, draft, stamp)
                wi._refresh_purchase_review(session, row, plan.review, stamp)
                session.refresh(row)
                if row.status == wi.STATUS_SUPERSEDED:
                    stats.superseded.append((row.player_name, row.result or ""))
                elif before != (row.amount, wi._as_utc(row.execute_at)):
                    stats.changed.append(row.player_name or plan.player_id)
            continue
        if ("bid", plan.player_id) in blocked or ("bid", plan.player_id) in others:
            stats.skipped[plan.player_id] = "heute gesperrt oder fremdes Vorhaben"
            continue
        if busy:
            stats.skipped[plan.player_id] = "Engine plant gerade ein Gebot — Elf zuerst"
            continue
        session.add(wi._new_intent(draft, stamp))
        stats.created.append(f"Gebot {plan.name} bis {_eur(plan.amount)}")

    wanted_sales = {p.player_id: p for p in sales}
    for (kind, pid), row in list(own.items()):
        if kind != "sell" or pid in wanted_sales:
            continue
        result = RESULT_NO_SIGNAL if pid in held else RESULT_NOT_HELD
        if _supersede(session, row, result, stamp):
            stats.superseded.append((row.player_name or pid, result))
    for plan in sales:
        draft = wi.IntentDraft(
            kind="sell", player_id=plan.player_id, player_name=plan.name, team_id=plan.team_id,
            position=plan.position, amount=plan.amount, rationale=plan.rationale,
            sources={"trade": True, "league_id": league_id, "rule_id": RULE_SELL,
                     "signal": plan.signal, "buy_price": plan.buy_price,
                     "sale_by": _utc(sale_by).isoformat() if sale_by is not None else None},
            listing_type="kickbase", deadline=plan.deadline, execute_at=plan.execute_at,
        )
        row = own.get(("sell", plan.player_id))
        if row is not None:
            before = (row.amount, wi._as_utc(row.execute_at))
            if wi._apply_draft(session, row, draft, stamp):
                session.refresh(row)
                if before != (row.amount, wi._as_utc(row.execute_at)):
                    stats.changed.append(row.player_name or plan.player_id)
            continue
        sell_blocked = ("sell", plan.player_id) in blocked and not (
            plan.signal == "frist" and plan.player_id in frist_ok
        )
        if sell_blocked or ("sell", plan.player_id) in others:
            stats.skipped[plan.player_id] = "Verkauf heute gesperrt oder fremdes Vorhaben"
            continue
        if counts.sells + open_sells >= DEFAULT_GUARDRAILS.daily_sell_cap:
            stats.skipped[plan.player_id] = "Tages-Cap Verkäufe erreicht — nächster Lauf"
            continue
        session.add(wi._new_intent(draft, stamp))
        open_sells += 1
        label = "Verkaufstermin" if plan.signal == "frist" else "MW fiel"
        stats.created.append(f"Verkauf {plan.name} ({label}) um {_when(plan.execute_at)}")
    session.commit()
    return stats


# ------------------------------------------------------------------ Bericht


def format_report(
    stats: SyncStats, *, now: datetime, book: TradeBook, sale_by: datetime | None,
    notes: Iterable[str] = (), planned: Iterable[BidPlan] = (),
) -> str:
    """Bericht nach dem Abgleich: Rahmen mit den Geboten DIESES Laufs (`planned`), dann was
    neu, aktualisiert oder geräumt ist."""
    planned = list(planned)
    exposure = (sum(p.buy_price for p in book.positions.values())
                + sum(book.live_bids.values()) + sum(p.amount for p in planned))
    local = _utc(now).astimezone(BERLIN)
    out = [f"Handels-Autopilot {_WEEKDAYS[local.weekday()]} {local:%d.%m. %H:%M}",
           f"Handelsrahmen {_eur(exposure)} von {_eur(TRADE_CAP_EUR)} · "
           f"{len(book.positions)} Handelsspieler · {len(book.live_bids)} laufende und "
           f"{len(planned)} geplante Handelsgebote"
           + (f" · Verkaufstermin {_when(sale_by)}" if sale_by is not None else "")]
    out += [f"• neu: {line}" for line in stats.created]
    out += [f"• aktualisiert: {name}" for name in stats.changed]
    out += [f"• geräumt: {name} — {why.removeprefix('Handels-Autopilot: ')}"
            for name, why in stats.superseded]
    if stats.skipped:
        reasons: dict[str, int] = {}
        for why in stats.skipped.values():
            reasons[why] = reasons.get(why, 0) + 1
        out.append("Ausgelassen: " + " · ".join(f"{n}× {why}" for why, n in reasons.items()))
    out += list(notes)
    out.append("Ausführung durch die Schreibstelle (Veto-Fenster in der Zentrale).")
    text = "\n".join(out)
    return text if len(text) <= 1900 else text[:1897].rstrip() + "…"


# ------------------------------------------------------------------ Verdrahtung


def _latest_radar(session: Any, now: datetime) -> Report | None:
    since = (_utc(now) - RADAR_MAX_AGE).replace(tzinfo=None)
    return session.scalars(
        select(Report).where(Report.source == RADAR_SOURCE, Report.created_at >= since)
        .order_by(Report.created_at.desc()).limit(1)
    ).first()


def _trade_bids_today(session: Any, now: datetime) -> int:
    today: date = _utc(now).date()
    return sum(
        1 for row in session.scalars(select(Intent).where(
            Intent.kind == "bid", Intent.status.in_((wi.STATUS_EXECUTED, wi.STATUS_EXECUTING)),
        ))
        if is_trade(row.sources) and wi._as_utc(row.updated_at).date() == today
    )


async def _transfers_left(client: Any, league_id: str, user_id: str) -> bool:
    from kickbase_agent.trading.trade_budget import get_trade_usage, trades_blocked

    usage = await get_trade_usage(client, league_id, user_id)
    if trades_blocked(usage.used + 2, DEFAULT_GUARDRAILS.league_trade_limit_per_half,
                      count_saturated=usage.count_saturated):
        return False
    return usage.used + 2 <= TRADE_TRANSFER_STOP


async def run_trade_autopilot(channel=None, *, now: datetime | None = None) -> str:
    """Ein Lauf: Kader, Markt, Konto, Gebote, Spielplan und den jüngsten Radar-Bericht lesen,
    Handelsvorhaben abgleichen, bei Änderungen einen Bericht speichern.

    Rückgabe `ok:<neu>/<aktualisiert>/<geräumt>`, `no_league` ohne Liga. Kickbase wird nur
    gelesen (get_squad, get_market, get_budget, get_own_bids, get_matchdays; für neue Gebote
    zusätzlich das Transferbudget). `channel` bleibt Teil der Job-Signatur; gesendet wird nichts
    — die Veto-Karten legt der Kehrer des Intent-Executors an."""
    del channel  # bewusst ungenutzt, siehe Docstring
    stamp = now or datetime.now(UTC)
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"
    user_id = str(auth.user_id or "")
    squad = await client.get_squad(lg)
    market = await client.get_market(lg)
    budget = int(await client.get_budget(lg))
    own_bids = [b for b in await client.get_own_bids(lg, user_id) if isinstance(b, Mapping)]
    fixtures = await client.get_matchdays()
    found = matchday_deadline(list(fixtures), stamp)
    deadline = found[1] if found else None
    live_bids = {str(b["player_id"]): int(b.get("amount") or 0)
                 for b in own_bids if b.get("player_id")}
    squad_ids = {str(sp.player.id) for sp in squad}
    notes: list[str] = []

    with SessionLocal() as session:
        book = load_trade_book(session, now=stamp, squad_ids=squad_ids, live_bids=live_bids,
                               league_id=lg)
        radar = _latest_radar(session, stamp)
        history = {pid: daily_values(points) for pid, points in
                   load_history(session, since=stamp - timedelta(days=5)).items()}
        open_rows = wi.open_intents(session)
        engine_bid_rows = [r for r in open_rows if r.kind == "bid" and not is_trade(r.sources)]
        engine_planned = engine_busy(session, stamp)
        plan_reason = engine_plan_reason(session, stamp, lg)
        existing = {r.player_id for r in open_rows if r.kind == "bid" and is_trade(r.sources)}
        taken = ({r.player_id for r in open_rows if not is_trade(r.sources)}
                 | set(live_bids) | squad_ids)
        blocked = {pid for kind, pid in wi.blocked_keys(session, stamp) if kind == "bid"}
        trade_bids_today = _trade_bids_today(session, stamp)
        buys_today = wi.today_intent_counts(session, stamp).buys
    regular = [sp for sp in squad if str(sp.player.id) not in book.positions]
    sale_by = sale_deadline(deadline, regular_fill(int(sp.player.position) for sp in regular))

    holdings = []
    for sp in squad:
        pos = book.positions.get(str(sp.player.id))
        if pos is None:
            continue
        holdings.append(Holding(
            player_id=pos.player_id, name=f"{sp.player.first_name} {sp.player.last_name}".strip(),
            team_id=str(sp.player.team_id), position=int(sp.player.position),
            mv=int(sp.player.market_value), buy_price=pos.buy_price,
            last_step=_last_step(sp.player, history.get(pos.player_id), stamp),
        ))
    sales = plan_sales(holdings, now=stamp, sale_by=sale_by, deadline=deadline)

    candidates: list[Candidate] = []
    if radar is None:
        notes.append("Kein Handels-Radar-Bericht der letzten 15 h — keine neuen Gebote.")
    else:
        # Ein geplantes Handelsgebot nennt der Radar auch kurz vor Auktionsende (seine
        # 30-min-Grenze gilt nur für neue Kandidaten) — nennt er es nicht, wird es geräumt.
        candidates = [c for c in map(Candidate.from_meta, (radar.meta or {}).get("kauf") or [])
                      if c is not None]
    slots = {
        str(mp.player.id): Slot(
            player_id=str(mp.player.id),
            name=f"{mp.player.first_name} {mp.player.last_name}".strip(),
            team_id=str(mp.player.team_id), position=int(mp.player.position),
            mv=int(mp.player.market_value), price=int(mp.price),
            expires_at=_utc(mp.expires_at), seller_id=str(mp.seller_id or ""),
            fitness=mp.player.fitness_status, lineup_prob=mp.player.lineup_prob,
        )
        for mp in market
    }
    # Elf zuerst: Engine-Vorhaben (auch mit laufender Auktion), ein laufender Engine-Zyklus,
    # ein eigenes Gebot, das kein Handelsgebot ist (Engine oder App), oder — seit dem Befund
    # vom 03.10.2026 — ein Mannschaftsplan, der einen Kauf wählt bzw. kein frischer Plan.
    busy = (engine_planned or engine_cycle_running()
            or bool(set(live_bids) - set(book.live_bids)) or plan_reason is not None)
    team_value = sum(int(sp.player.market_value) for sp in squad)
    held_mv = [int(sp.player.market_value) for sp in squad
               if str(sp.player.id) in book.positions]
    target_mv = [slots[pid].mv for pid in book.live_bids if pid in slots]
    regular_counts: dict[int, int] = {}
    for sp in regular:
        regular_counts[int(sp.player.position)] = regular_counts.get(int(sp.player.position),
                                                                     0) + 1
    swap_cash_reserved = sum(
        _planned_swap_cash_need(r)
        for r in engine_bid_rows
        if _is_planned_dispo_swap_bid(r)
    )
    engine_planned_amount = sum(int(r.amount or 0) for r in engine_bid_rows)
    capacity = Capacity(
        exposure=sum(p.buy_price for p in book.positions.values()) + sum(book.live_bids.values()),
        open_trades=len(book.positions) + len(book.live_bids),
        trade_bids_today=trade_bids_today, buys_today=buys_today,
        engine_bid_intents=len(engine_bid_rows),
        after_bids=budget - sum(live_bids.values()) - engine_planned_amount,
        floor=overdraft_floor(team_value, budget),
        coverage=budget - sum(live_bids.values()) - swap_cash_reserved
        + sale_cover(held_mv=held_mv, bid_target_mv=target_mv, now=stamp, sale_by=sale_by),
        regular_counts=regular_counts,
    )
    if not get_settings().agent_trade_autopilot:
        # Halt nur für den Handel: Gebote räumen, verkaufen weiter (sonst bliebe das Minus).
        bids, skipped = [], dict.fromkeys(existing, STOP_REASON)
        notes.append(f"{STOP_REASON}: keine neuen Gebote, geplante geräumt; Verkäufe der "
                     "Handelsspieler laufen weiter.")
    else:
        transfers_left = True
        if any(c.player_id not in existing for c in candidates) and not busy:
            try:
                transfers_left = await _transfers_left(client, lg, user_id)
            except Exception as exc:  # noqa: BLE001 — ohne Zählung keine neuen Handel
                log.warning("Handels-Autopilot: Transferzählung nicht lesbar (%s)",
                            type(exc).__name__)
                transfers_left = False
                notes.append("Transferzählung nicht lesbar — keine neuen Handelsgebote.")
        bids, skipped = plan_bids(
            candidates, slots, now=stamp, sale_by=sale_by, capacity=capacity,
            existing=existing, blocked=blocked, taken=taken - existing,
            engine_busy=busy, transfers_left=transfers_left,
        )
        if busy:
            why = (plan_reason if plan_reason is not None
                   else "Engine plant ein Gebot oder ein eigenes Gebot läuft")
            notes.append(f"{why} — Elf zuerst, keine neuen Handelsgebote.")

    async with wi._intent_lock:
        with SessionLocal() as session:
            stats = sync_trade_intents(
                session, now=stamp, league_id=lg, bids=bids, sales=sales, skipped=skipped,
                radar_report_id=radar.id if radar is not None else None, sale_by=sale_by,
                held=book.positions,
            )
            if stats.changes:
                saved = save_report(
                    REPORT_KIND, f"Handels-Autopilot {_utc(stamp).astimezone(BERLIN):%d.%m.%Y}",
                    format_report(stats, now=stamp, book=book, sale_by=sale_by, notes=notes,
                                  planned=bids),
                    SOURCE,
                    meta={"neu": stats.created, "aktualisiert": stats.changed,
                          "geraeumt": [list(s) for s in stats.superseded],
                          "ausgelassen": stats.skipped,
                          "rahmen": {"exposure": book.exposure, "cap": TRADE_CAP_EUR},
                          "verkauf_bis": _utc(sale_by).isoformat() if sale_by else None,
                          "radar_report_id": radar.id if radar is not None else None},
                    session=session, created_at=stamp,
                )
                if saved is None:
                    raise RuntimeError("Handels-Autopilot: Bericht nicht gespeichert.")
    return f"ok:{len(stats.created)}/{len(stats.changed)}/{len(stats.superseded)}"
