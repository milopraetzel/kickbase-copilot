"""Handelsbuch: welche Spieler und Gebote gehören dem Handels-Autopiloten (Emil 01.10.2026).

Emil 01.10.2026: „alles soll automatisch geschehen ich will nicht dass ich bieten muss“ —
Entscheidungen im Chat: Handelsspieler bleiben auf der Bank, Handel ins Minus bis 20 M.
Spec `docs/superpowers/specs/2026-10-01-handels-autopilot.md`.

Ein **Handelsspieler** ist ein Kaderspieler, dessen jüngstes echtes Gebot (ausgeführte
Audit-Zeile, kein Trockenlauf) der Handels-Autopilot abgab: Das Vorhaben dazu trägt die
Quelle `trade`. Bietet danach die Engine auf denselben Spieler, gehört er ihr. Hat die
Gebots-Nachschau die Handelsauktion als verloren gebucht, macht ein späterer Kauf auf
anderem Weg ihn nicht zum Handelsspieler.

Alle Teile lesen DIESES Buch, damit sie dasselbe meinen: die Engine (Handelsspieler zählen
keinen Elf-Platz, Handelsgebote binden nur Geld und fallen aus der Deckungsreparatur), die
Aufstellung und der Lücken-Wächter (Handelsspieler bleiben auf der Bank), der
Ergebnis-Wächter (Handel deckt sich über seinen Verkauf), der Radar und der Autopilot.

Rein lesend: nur die eigene Datenbank, kein Kickbase-Aufruf.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from kickbase_agent.agent.rules import load_agent_rules
from kickbase_agent.db.models import Intent, TransferAction
from kickbase_agent.decision.lineup import _FORMATIONS, MIN_SQUAD_SIZE

_BERLIN = ZoneInfo("Europe/Berlin")

#: Quelle eines Handelsvorhabens (Intent.sources).
TRADE_KEY = "trade"
#: Handelsrahmen (Emil 03.11.2026: „an sich können wir 50 Millionen ins Minus gehen“):
#: Kaufpreise offener Handelsspieler plus offene Handelsgebote. Kickbases 33-%-Grenze prüft die
#: Schreibstelle zusätzlich.
TRADE_CAP_EUR = 50_000_000
#: Verkaufstermin vor der Spieltags-Frist (Spec, Emils Freigabe 01.10./03.11.2026): solange die
#: Elf offene Plätze hat, braucht die Engine im Notbesetzungs-Fenster (96 h) echtes Geld; sonst
#: am Freitagnachmittag 6 h vor Anpfiff (14:30 Berlin, vier Stunden vor dem T-120-Fenster) —
#: damit nimmt der Handel noch das Mittwoch- und Donnerstag-Update um 22:00 mit.
SALE_LEAD_GAP = timedelta(hours=96)
SALE_LEAD = timedelta(hours=6)
#: So weit zurück zählt ein Handelsgebot (jede Position ist spätestens zum Verkaufstermin weg).
LOOKBACK = timedelta(days=28)
#: So lange nach dem Verkaufstermin gilt ein Handelsspieler noch als „wird verkauft“ (Verkauf
#: am Termin, Planer :17/:47, Executor alle 5 min, eine Wiederholung). Danach ist er ein
#: gescheiterter Verkauf und deckt nichts mehr — Wächter und Pfand-Einlöser melden dann.
SALE_GRACE = timedelta(hours=3)
#: Gebots-Nachschau: so endete die Auktion nicht mit unserem Zuschlag.
_NOT_WON = frozenset({"lost", "cancelled", "superseded"})
#: Positions-Minima jeder Kickbase-Formation (1 TW / 3 ABW / 2 MF / 1 ST).
POSITION_MINIMUMS = {1: 1, 2: 3, 3: 2, 4: 1}


def trade_sale_proceeds(
    market_value: int,
    *,
    updates_until_friday: int = 0,
    haircut_per_update_pct: float = 0.0,
) -> int:
    """Sofortverkaufserlös eines Handelsspielers an Kickbase (optional mit Sicherheits-Haircut
    pro verbleibendem 22:00-Uhr-Marktwert-Update bis zum Freitags-Verkaufstermin)."""
    base = max(0, int(market_value))
    if updates_until_friday <= 0 or haircut_per_update_pct <= 0.0:
        return base
    factor = max(0.75, 1.0 - (updates_until_friday * (haircut_per_update_pct / 100.0)))
    return int(round(base * factor))


def is_trade(sources: Any) -> bool:
    """Gehört das Vorhaben dem Handels-Autopiloten?"""
    return isinstance(sources, Mapping) and sources.get(TRADE_KEY) is True


def _utc(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def regular_fill(positions: Iterable[int]) -> int:
    """Besetzbare Elf-Plätze (beste zulässige Formation) eines Kaders aus seinen Positionen
    (1 TW · 2 ABW · 3 MF · 4 ST). Zwei Torhüter sind keine zwei Plätze; fehlt eine Position,
    zählen die Köpfe (wie im Ergebnis-Wächter)."""
    positions = [int(p) for p in positions]
    if any(p not in (1, 2, 3, 4) for p in positions):
        return min(MIN_SQUAD_SIZE, len(positions))
    count = {pos: positions.count(pos) for pos in (1, 2, 3, 4)}
    return max(
        min(count[1], 1)
        + sum(min(count[pos], need) for pos, need in zip((2, 3, 4), form, strict=True))
        for form in _FORMATIONS
    )


def sale_deadline(deadline: datetime | None, regular_filled: int) -> datetime | None:
    """Spätester Verkauf aller Handelsspieler vor der Spieltags-Frist (None ohne Frist).

    `regular_filled` = besetzbare Elf-Plätze ohne Handelsspieler (`regular_fill`): Solange die
    Elf offene Plätze hat, gilt der frühere Termin (Spec: „solange die Elf offene Plätze hat“)."""
    if deadline is None:
        return None
    return _utc(deadline) - (SALE_LEAD_GAP if regular_filled < MIN_SQUAD_SIZE else SALE_LEAD)


def remaining_updates_until_friday(
    now: datetime | None, sale_by: datetime | None
) -> int:
    """Anzahl der verbleibenden 22:00-Uhr-Marktwert-Updates (Europe/Berlin) zwischen
    `now` und `sale_by`."""
    if now is None or sale_by is None:
        return 0
    start = _utc(now).astimezone(_BERLIN)
    end = _utc(sale_by).astimezone(_BERLIN)
    if end <= start:
        return 0
    cursor = datetime.combine(start.date(), time(22, 0), tzinfo=_BERLIN)
    if cursor <= start:
        cursor += timedelta(days=1)
    count = 0
    while cursor <= end:
        count += 1
        cursor += timedelta(days=1)
    return count


def sale_overdue(now: datetime, sale_by: datetime | None) -> bool:
    """Verkaufstermin samt Karenz verstrichen? Dann ist der Verkauf gescheitert."""
    return sale_by is not None and _utc(now) >= _utc(sale_by) + SALE_GRACE


def sale_cover(
    *,
    held_mv: Iterable[int],
    bid_target_mv: Iterable[int],
    now: datetime,
    sale_by: datetime | None,
    updates_until_friday: int | None = None,
    haircut_per_update_pct: float | None = None,
) -> int:
    """Was der Handel bis zum Verkaufstermin zurückbringt: Handelsspieler und Ziele laufender
    Handelsgebote mit 100 % ihres Marktwerts (optional gedämpft um den Freitags-Haircut aus dem
    Regelwerk je verbleibendem 22:00-Update). Nach Verkaufstermin + `SALE_GRACE` zählt der
    Handel gar nicht mehr — der Verkauf ist dann gescheitert, und ein noch laufendes
    Handelsgebot würde gewonnen sofort ein solcher Handelsspieler (Codex-Review 02.10.2026);
    die Deckung muss ohne den Handel stehen. Gemeinsame Rechnung für Autopilot, Ergebnis-Wächter
    und Pfand-Einlöser (sonst warnt der eine, was der andere plant)."""
    if sale_overdue(now, sale_by):
        return 0
    updates = (
        remaining_updates_until_friday(now, sale_by)
        if updates_until_friday is None
        else int(updates_until_friday)
    )
    if haircut_per_update_pct is None:
        rules = load_agent_rules()
        haircut = rules.friday_buffer_haircut_pct if rules.is_custom_active() else 0.0
    else:
        haircut = float(haircut_per_update_pct)
    return (
        sum(
            trade_sale_proceeds(
                int(mv),
                updates_until_friday=updates,
                haircut_per_update_pct=haircut,
            )
            for mv in held_mv
        )
        + sum(
            trade_sale_proceeds(
                int(mv),
                updates_until_friday=updates,
                haircut_per_update_pct=haircut,
            )
            for mv in bid_target_mv
        )
    )


@dataclass(frozen=True)
class TradePosition:
    """Handelsspieler im Kader: Kaufpreis, Auktionsende und sein Vorhaben."""

    player_id: str
    buy_price: int
    bought_at: datetime
    intent_id: int
    action_id: int


@dataclass(frozen=True)
class TradeBook:
    positions: dict[str, TradePosition] = field(default_factory=dict)
    #: Laufende Handelsgebote (Live-Gebot laut Kickbase, Betrag).
    live_bids: dict[str, int] = field(default_factory=dict)
    #: Geplante oder gerade ausgeführte Handelsvorhaben (Betrag).
    pending: dict[str, int] = field(default_factory=dict)
    #: Davon die festen: gerade in Ausführung oder von Emil angepasst. Sie weichen einem Kauf
    #: der Engine nicht (worker/intents.yield_trade_bids räumt nur die übrigen).
    firm: dict[str, int] = field(default_factory=dict)

    @property
    def bid_ids(self) -> set[str]:
        return set(self.live_bids) | set(self.pending)

    @property
    def firm_bids(self) -> dict[str, int]:
        """Handelsgebote, die das Geld der Engine binden: abgegebene und feste Vorhaben, je
        Spieler die höhere Bindung. Ein nur geplantes Handelsgebot bindet nichts — es weicht,
        sobald die Engine kaufen will (Befund 03.10.2026)."""
        merged = dict(self.live_bids)
        for pid, amount in self.firm.items():
            merged[pid] = max(merged.get(pid, 0), amount)
        return merged

    @property
    def bids(self) -> dict[str, int]:
        """Je Spieler die höhere Bindung aus Live-Gebot und Vorhaben (nie doppelt)."""
        merged = dict(self.live_bids)
        for pid, amount in self.pending.items():
            merged[pid] = max(merged.get(pid, 0), amount)
        return merged

    @property
    def exposure(self) -> int:
        """Handelsrahmen in Gebrauch: Kaufpreise der Positionen plus Handelsgebote."""
        return sum(p.buy_price for p in self.positions.values()) + sum(self.bids.values())


def _outcome(action: TransferAction) -> Mapping[str, Any]:
    payload = action.payload if isinstance(action.payload, Mapping) else {}
    outcome = payload.get("outcome")
    return outcome if isinstance(outcome, Mapping) else {}


def load_trade_book(
    session: Any,
    *,
    now: datetime,
    squad_ids: Iterable[str],
    live_bids: Mapping[str, int] | None = None,
    league_id: str | None = None,
) -> TradeBook:
    """Handelsbuch aus Audit und Vorhaben: Positionen im Kader, Live- und geplante Gebote.

    Ohne lesbare Datenbank (Read-only-Replay wie `cli.plan.load_recovery_audit`) ist das Buch
    leer: Dann gibt es keine Handelsspieler, und alle Gebote gelten als Engine-Gebote."""
    if session is None or not hasattr(session, "scalars"):
        return TradeBook()
    since = _utc(now) - LOOKBACK
    query = select(TransferAction).where(
        TransferAction.action_type == "bid",
        TransferAction.status == "executed",
        TransferAction.dry_run.is_(False),
    )
    if league_id is not None:
        query = query.where(TransferAction.league_id == league_id)
    latest: dict[str, TransferAction] = {}
    for action in session.scalars(query):
        pid = str(action.player_id or "")
        if not pid or _utc(action.created_at) < since:
            continue
        old = latest.get(pid)
        if old is None or (_utc(action.created_at), action.id) > (_utc(old.created_at), old.id):
            latest[pid] = action
    trade_intents = {
        row.executed_action_id: row
        for row in session.scalars(select(Intent).where(
            Intent.kind == "bid", Intent.executed_action_id.is_not(None),
        ))
        if is_trade(row.sources)
    }
    # Jüngster echter Abgang je Spieler (Sofortverkauf, angenommenes Angebot): Ein Verkauf NACH
    # dem Handelsgebot beendet den Handel — kauft Emil den Spieler später in der App zurück
    # (ohne Audit-Zeile), ist er ein Stammspieler (Review 02.10.2026, P0).
    sales_query = select(TransferAction).where(
        TransferAction.action_type.in_(("sell", "accept")),
        TransferAction.status == "executed",
        TransferAction.dry_run.is_(False),
    )
    if league_id is not None:
        sales_query = sales_query.where(TransferAction.league_id == league_id)
    last_sale: dict[str, datetime] = {}
    for action in session.scalars(sales_query):
        pid = str(action.player_id or "")
        stamp = _utc(action.created_at)
        if pid and stamp > last_sale.get(pid, datetime.min.replace(tzinfo=UTC)):
            last_sale[pid] = stamp

    def trade_action(pid: str) -> tuple[TransferAction, Intent] | None:
        action = latest.get(pid)
        if action is None or action.id not in trade_intents:
            return None
        return action, trade_intents[action.id]

    positions: dict[str, TradePosition] = {}
    for pid in map(str, squad_ids):
        found = trade_action(pid)
        if found is None:
            continue
        action, intent = found
        outcome = _outcome(action)
        if outcome.get("result") in _NOT_WON:
            continue
        if pid in last_sale and last_sale[pid] > _utc(action.created_at):
            continue  # nach dem Handel verkauft — ein späterer Rückkauf ist kein Handel
        paid = outcome.get("winner_amount")
        price = paid if type(paid) is int and paid > 0 else int(action.amount or intent.amount)
        bought = intent.deadline if intent.deadline is not None else action.created_at
        positions[pid] = TradePosition(pid, price, _utc(bought), intent.id, action.id)
    live = {
        str(pid): int(amount)
        for pid, amount in (live_bids or {}).items()
        if trade_action(str(pid)) is not None
    }
    pending: dict[str, int] = {}
    firm: dict[str, int] = {}
    for row in session.scalars(select(Intent).where(
        Intent.kind == "bid", Intent.status.in_(("planned", "executing")),
    )):
        sources = row.sources or {}
        if not is_trade(sources):
            continue
        if league_id is not None and str(sources.get("league_id", league_id)) != league_id:
            continue
        pid = str(row.player_id)
        pending[pid] = max(pending.get(pid, 0), int(row.amount or 0))
        if row.status != "planned" or row.adjusted_from is not None:
            firm[pid] = max(firm.get(pid, 0), int(row.amount or 0))
    return TradeBook(positions=positions, live_bids=live, pending=pending, firm=firm)
