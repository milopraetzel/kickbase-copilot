"""Reconcile-Job: ungeklärte Schreib-Aktionen gegen die Kickbase-Realität abgleichen.

Warum: Nach einem API-Timeout steht eine Aktion auf 'unknown' — der Write KANN
remote durchgegangen sein (executor.py löst Timeouts bewusst so auf, statt blind
zu retryen). Hängende 'pending' (Prozess starb mitten im Dispatch) sind ebenso
ungeklärt. Beide zählen im Executor als _BLOCKING und sperren jeden erneuten
Versuch derselben Aktion — bis jetzt musste ein Mensch aufräumen; für Autonomie
(Phase 2, P0 aller drei Audits) klärt dieser Job stündlich gegen die LIVE-Daten:

| Typ        | Beweis (live)                                   | Ergebnis |
|------------|-------------------------------------------------|----------|
| bid        | eigenes Gebot auf player_id in get_own_bids     | executed |
|            | sonst: Spieler im Kader (Zuschlag kann          | executed |
|            | inzwischen erfolgt sein)                        |          |
|            | weder Gebot noch Kader                          | failed   |
| cancel_bid | Gebot (offer_id, sonst player_id) nicht mehr da | executed |
|            | Gebot besteht noch                              | failed   |
| sell       | Spieler nicht mehr im Kader                     | executed |
|            | Spieler noch im Kader                           | failed   |
| list       | eigenes Listing (Verkäufer u.i = eigener User)  | executed |
|            | kein Listing, Spieler noch im Kader             | failed   |
|            | kein Listing, Spieler weg (= verkauft)          | executed |

lineup/bonus/accept/decline/delist sind nicht sicher abgleichbar → Status
BLEIBT, aber EINE Alarm-Zeile an Discord. Der Merker dafür lebt in der
rationale (DB-persistent): ein In-Memory-Set würde nach jedem Neustart erneut
alarmieren, obwohl derselbe Fall weiter offen ist.

Sicherheit: Der Job schreibt NIE zu Kickbase (nur Reads + lokale DB-Updates) —
Kill-Switch/Interlock des Executors sind hier nicht berührt. Die Status-
Auflösung ist ein konditionales UPDATE (WHERE status IN ('unknown','pending')):
ein parallel geänderter Status wird nie überschrieben (Muster: executor._recycle).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Report, TransferAction
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS, protected_player_ids
from kickbase_agent.decision.kickoff_freeze import KICKOFF_MIN_BALANCE
from kickbase_agent.kickbase import endpoints
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.kickbase.models import SquadPlayer
from kickbase_agent.notify.discord_channel import DiscordChannel
from kickbase_agent.notify.report_store import save_report
from kickbase_agent.trading.trade_book import (
    load_trade_book,
    regular_fill,
    sale_cover,
    sale_deadline,
    sale_overdue,
)
from kickbase_agent.worker.intents import (
    IntentDraft,
    insert_cover_sells,
    open_intents,
)
from kickbase_agent.worker.job_health import close_stuck_runs
from kickbase_agent.worker.kickoff_guard import matchday_deadline

#: 'pending' gilt erst nach dieser Frist als hängend — ein regulärer Dispatch
#: braucht Sekunden; 10 Minuten heißt, der Prozess ist mittendrin gestorben.
PENDING_GRACE = timedelta(minutes=10)

#: Nur diese Status darf der Job anfassen — das konditionale UPDATE prüft sie
#: erneut, damit ein parallel aufgelöster Status nie überschrieben wird.
_OPEN_STATUSES = ("unknown", "pending")

#: Merker-Wort in der rationale: einmal alarmiert = beim nächsten Lauf leise.
_ALARM_MARKER = "reconcile-alarm"

#: Vermerk an einem Vorschlag, dessen Freigabe-Frist verstrich, ohne dass ihn
#: jemand ausgeführt hat.
_LAPSED_NOTE = "Frist abgelaufen, nie freigegeben"

#: Pfand-Einlöser (01.09., Spec 2026-08-31-pfand-hebel §3): Deploy-Kopplung —
#: cli/plan.py rechnet die Pfand-Plan-Basis NUR, wenn dieses Modul (und damit
#: der Einlöser-Nachlauf unten) im selben Build ausgeliefert ist. Nie auf False
#: stellen, um den Hebel „schnell" zu deaktivieren — dafür ist AGENT_AUTO_PURGE
#: (Emils Flag) bzw. pfand_risk_fraction der ehrliche Weg.
PFAND_EINLOESER_BEREIT = True

#: Ab dieser Rest-Zeit zur Spieltags-Deadline wacht der Einlöser (96 h = Vorlauf
#: der Wächter-Planung in schedule_kickoff_guards — dieselbe Uhr).
COVER_HORIZON = timedelta(hours=96)
#: Veto-Fenster der Deckungsverkäufe: sichtbar, aber zügig (45 min wie der
#: Accept-Pfad); große Beträge (≥ approval_threshold) bekommen das 2-h-Fenster.
COVER_WINDOW = timedelta(minutes=45)
COVER_BIG_WINDOW = timedelta(hours=2)


def _as_utc(dt: datetime) -> datetime:
    """SQLite liefert naive Zeitstempel zurück — als UTC interpretieren
    (Muster: worker/scan.open_approval_ids)."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def load_candidates(session: Session, now: datetime | None = None) -> list[TransferAction]:
    """Abgleich-Kandidaten: alle 'unknown' (jedes Alter) + hängende 'pending'.

    Die Altersgrenze läuft in Python statt SQL: SQLite gibt naive Stempel
    zurück, ein String-Vergleich gegen aware Zeiten wäre wacklig.
    """
    stamp = now or datetime.now(UTC)
    rows = (
        session.execute(
            select(TransferAction)
            .where(TransferAction.status.in_(_OPEN_STATUSES))
            .order_by(TransferAction.id)
        )
        .scalars()
        .all()
    )
    return [
        row
        for row in rows
        if row.status == "unknown" or stamp - _as_utc(row.created_at) > PENDING_GRACE
    ]


def own_listing_pids(market_raw: dict[str, Any], user_id: str) -> set[str]:
    """Player-IDs der EIGENEN Listings aus dem rohen Markt-Payload.

    Verkäufer steht im Markt-Item unter `u.i` — gelesen wie in
    cli/cockpit/data._market_rows (das typisierte MarketPlayer-Modell trägt
    den Verkäufer nicht).
    """
    out: set[str] = set()
    for it in market_raw.get("it", []):
        seller_id = str((it.get("u") or {}).get("i") or "")
        if seller_id and seller_id == str(user_id):
            out.add(str(it.get("i")))
    return out


def classify(
    action: TransferAction,
    own_bids: list[dict[str, Any]],
    squad_pids: set[str],
    own_listing_pids: set[str],
    *,
    now: datetime | None = None,
) -> tuple[str | None, str]:
    """Live-Beweis je Aktionstyp → (neuer Status | None, Vermerk).

    Reine Funktion ohne Netz/DB — None heißt: nicht sicher abgleichbar, Status
    bleibt (Aufrufer alarmiert einmalig). Der Vermerk wird vom Aufrufer als
    " | reconciled HH:MM: <Vermerk>" an die rationale gehängt.
    """
    pid = str(action.player_id or "")
    kind = action.action_type

    if kind == "bid" and pid:
        if any(str(b.get("player_id")) == pid for b in own_bids):
            return "executed", "eigenes Gebot live gefunden"
        if pid in squad_pids:
            return "executed", "Spieler im Kader (Zuschlag erfolgt)"
        return "failed", "kein Gebot, nicht im Kader"

    if kind == "cancel_bid" and pid:
        payload = action.payload or {}
        if payload.get("funding_review") is not None or action.rule_id == "intent-funding-cancel":
            return _classify_funding_cancel(action, own_bids, squad_pids, now)
        # offer_id steckt (falls über den Approval-Pfad gestaged) im payload;
        # direkte execute()-Zeilen haben keins → Fallback auf player_id.
        oid = str((action.payload or {}).get("offer_id") or "")
        still_there = any(
            (str(b.get("offer_id")) == oid) if oid else (str(b.get("player_id")) == pid)
            for b in own_bids
        )
        if still_there:
            return "failed", "Gebot besteht noch"
        return "executed", "Gebot nicht mehr vorhanden"

    if kind == "sell" and pid:
        if pid in squad_pids:
            return "failed", "Spieler noch im Kader"
        return "executed", "Spieler nicht mehr im Kader"

    if kind == "list" and pid:
        if pid in own_listing_pids:
            return "executed", "eigenes Listing am Markt"
        if pid in squad_pids:
            return "failed", "kein Listing, Spieler noch im Kader"
        return "executed", "verkauft (kein Listing, Spieler nicht mehr im Kader)"

    return None, f"{kind} nicht sicher abgleichbar — bitte manuell prüfen"


def _classify_funding_cancel(
    action: TransferAction,
    own_bids: list[dict[str, Any]],
    squad_pids: set[str],
    now: datetime | None,
) -> tuple[str | None, str]:
    """Nur den ursprünglichen Spieler im datierten Auktionsfenster abgleichen."""
    payload = action.payload or {}
    review = payload.get("funding_review")
    uncertain = "Deckungsrückzug ungeklärt: kein eindeutiger ursprünglicher Auktionsbeleg."
    if not isinstance(review, dict) or review.get("purpose") != "funding_abandon_bid":
        return None, uncertain
    pid, oid = str(action.player_id or ""), str(payload.get("offer_id") or "")
    try:
        until = datetime.fromisoformat(review["abandon_until"])
        original_end = datetime.fromisoformat(review["original_listing_expires_at"])
        observed = datetime.fromisoformat(review["observed_at"])
        current = now or datetime.now(UTC)
        original_bid_id = review["original_bid_action_id"]
        if (
            type(original_bid_id) is not int
            or original_bid_id <= 0
            or until.tzinfo is None
            or original_end.tzinfo is None
            or observed.tzinfo is None
            or current.tzinfo is None
            or action.created_at is None
            or abs((until - original_end).total_seconds()) > 120
            or not observed <= _as_utc(action.created_at) <= current < min(until, original_end)
        ):
            return None, "Deckungsrückzug ungeklärt: ursprüngliches Auktionsfenster nicht belegt."
    except (TypeError, ValueError, KeyError):
        return None, uncertain
    if (
        not pid
        or not oid
        or review.get("player_id") != pid
        or review.get("offer_id") != oid
        or str(review.get("league_id")) != str(action.league_id)
    ):
        return None, uncertain
    if pid in squad_pids:
        return None, "Deckungsrückzug ungeklärt: Spieler im Kader; Zuschlag statt Rückzug möglich."
    if not isinstance(own_bids, list) or any(
        not isinstance(bid, dict) or not bid.get("player_id") or not bid.get("offer_id")
        for bid in own_bids
    ):
        return None, "Deckungsrückzug ungeklärt: Live-Gebotsdaten unvollständig."
    target_bids = [bid for bid in own_bids if str(bid["player_id"]) == pid]
    exact = [bid for bid in target_bids if str(bid["offer_id"]) == oid]
    if len(exact) == 1 and len(target_bids) == 1:
        if type(exact[0].get("amount")) is not int or exact[0]["amount"] != review.get(
            "amount_eur"
        ):
            return None, "Deckungsrückzug ungeklärt: Gebotsbetrag verändert oder nicht belegt."
        return "failed", "source=observed_state: ursprüngliches Spielergebot besteht noch."
    if target_bids:
        return None, "Deckungsrückzug ungeklärt: Zielgebot verändert oder nicht eindeutig."
    return (
        "executed",
        "source=observed_state: Zielgebot vor ursprünglichem Ablauf nicht vorhanden; "
        "kein API-Rückzugsbeleg, nur beobachteter Zustand.",
    )


def _with_note(rationale: str | None, note: str) -> str:
    base = (rationale or "").strip()
    return f"{base} | {note}" if base else note


def resolve_status(
    session: Session, action: TransferAction, new_status: str, note: str, stamp: datetime
) -> bool:
    """Status auflösen — konditional (WHERE status IN unknown/pending).

    Ein parallel geänderter Status (z. B. manuell aufgeräumt, während der Job
    lief) wird NIE überschrieben: rowcount 0 → rollback, False. True = wir
    haben aufgelöst; der Vermerk hängt dann an der rationale.
    """
    rationale = _with_note(action.rationale, f"reconciled {stamp:%H:%M}: {note}")
    hit = session.execute(
        update(TransferAction)
        .where(TransferAction.id == action.id, TransferAction.status.in_(_OPEN_STATUSES))
        .values(status=new_status, rationale=rationale, resolved_at=stamp)
        .execution_options(synchronize_session=False)
    )
    if hit.rowcount == 0:
        session.rollback()
        return False
    session.commit()
    return True


def expire_lapsed_proposals(session: Session, now: datetime) -> list[dict[str, Any]]:
    """Vorschläge mit verstrichener Frist auf 'expired' setzen.

    Fund 31.08.2026: Elf `proposed`-Zeilen vom 21./22.08. lagen unangetastet in
    der Datenbank, `valid_until` neun bis zehn Tage vorbei. Der Executor kennt
    den Übergang längst (TTL-Lücke b), vollzieht ihn aber nur TRÄGE — erst wenn
    dieselbe Aktion erneut angelegt wird. Passiert das nie, bleibt der Vorschlag
    für immer offen und blockt als `_BLOCKING` still jeden Neuversuch.

    Kein DELETE: Die Zeile IST der Beleg, dass es den Vorschlag gab
    (CLAUDE.md §9). Nur der Status wandert dorthin, wo die Zustandsmaschine ihn
    bei abgelaufener Frist ohnehin sieht.

    Die Altersgrenze läuft in Python statt SQL — SQLite gibt naive Stempel
    zurück (dasselbe Muster wie `load_candidates`). Das UPDATE prüft `proposed`
    erneut, damit eine parallel freigegebene Aktion nie überschrieben wird.
    """
    rows = (
        session.execute(
            select(TransferAction)
            .where(TransferAction.status == "proposed", TransferAction.valid_until.is_not(None))
            .order_by(TransferAction.id)
        )
        .scalars()
        .all()
    )
    swept: list[dict[str, Any]] = []
    for row in rows:
        if row.valid_until is None or _as_utc(row.valid_until) >= now:
            continue
        rationale = _with_note(row.rationale, f"aufgeräumt {now:%d.%m. %H:%M}: {_LAPSED_NOTE}")
        hit = session.execute(
            update(TransferAction)
            .where(TransferAction.id == row.id, TransferAction.status == "proposed")
            .values(status="expired", rationale=rationale, resolved_at=now)
            .execution_options(synchronize_session=False)
        )
        if hit.rowcount == 0:
            session.rollback()
            continue
        session.commit()
        swept.append({"id": row.id, "action_type": row.action_type, "player_id": row.player_id})
    return swept


def mark_alarm_once(session: Session, action: TransferAction, note: str, stamp: datetime) -> bool:
    """Einmalige Alarm-Zeile je unauflösbarer Aktion; Status bleibt unberührt.

    True = jetzt alarmieren (Merker wurde gesetzt); False = schon früher
    alarmiert ODER der Status wurde parallel aufgelöst (dann ist kein Alarm
    mehr nötig). Der Merker lebt in der rationale und überlebt Neustarts.
    """
    if _ALARM_MARKER in (action.rationale or ""):
        return False
    rationale = _with_note(action.rationale, f"{_ALARM_MARKER} {stamp:%H:%M}: {note}")
    hit = session.execute(
        update(TransferAction)
        .where(TransferAction.id == action.id, TransferAction.status.in_(_OPEN_STATUSES))
        .values(rationale=rationale)
        .execution_options(synchronize_session=False)
    )
    if hit.rowcount == 0:
        session.rollback()
        return False
    session.commit()
    return True


def format_report(
    resolved: list[dict[str, Any]],
    alarms: list[dict[str, Any]],
    *,
    swept: list[dict[str, Any]] | None = None,
    stuck: int = 0,
) -> str | None:
    """Discord-Karte — oder None, wenn es nichts zu melden gibt (leise)."""
    swept = swept or []
    if not resolved and not alarms and not swept and not stuck:
        return None
    lines = ["🔁 **Reconcile** — ungeklärte Aktionen gegen Kickbase abgeglichen"]
    for r in resolved:
        lines.append(
            f"• {r['action_type']} Spieler {r['player_id'] or '—'} (#{r['id']}): "
            f"{r['old_status']} → {r['new_status']} — {r['note']}"
        )
    for a in alarms:
        lines.append(f"⚠️ {a['action_type']} #{a['id']}: Status bleibt {a['status']} — {a['note']}")
    if swept:
        nummern = ", ".join(f"#{s['id']}" for s in swept)
        lines.append(
            f"🧹 {len(swept)} abgelaufene(r) Vorschlag/Vorschläge geschlossen ({nummern}) — "
            "Frist verstrichen, nie freigegeben."
        )
    if stuck:
        lines.append(f"🧹 {stuck} hängende(r) Job-Lauf geschlossen (ohne Endzeile).")
    return "\n".join(lines)


async def _live_state(
    client: Any, league_id: str, user_id: str
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    """Live-Realität einer Liga: (eigene Gebote, Kader-PIDs, eigene Listing-PIDs).

    Der Markt kommt ROH (client._get) statt über get_market: das typisierte
    Modell trägt den Verkäufer (`u`) nicht — dasselbe Muster wie
    cli/cockpit/data.collect.
    """
    bids = await client.get_own_bids(league_id, user_id)
    squad = await client.get_squad(league_id)
    market_raw = await client._get(endpoints.market(league_id))
    squad_pids = {str(sp.player.id) for sp in squad}
    return bids, squad_pids, own_listing_pids(market_raw, user_id)


def cover_sell_drafts(
    squad: list[SquadPlayer],
    protected: frozenset[str] | set[str],
    shortfall: int,
    now: datetime,
    *,
    trade_ids: frozenset[str] = frozenset(),
    trade_due: bool = False,
) -> tuple[list[IntentDraft], int]:
    """Deckungsverkaufs-Entwürfe für den Pfand-Einlöser — reine Funktion.

    Nur BANK-Spieler (lineup_order is None): Die gesetzte Elf wird nie zur
    Deckung angefasst (Emils Starter-Regel; der Executor-Starter-Schutz würde
    solche Verkäufe ohnehin refusen, und ein „Elf-Spieler verkaufen"-Vorhaben
    in der Zentrale wäre die bekannte Lüge auf dem Bildschirm). Verfassung
    (protected) fliegt VOR der Auswahl raus. Rangfolge L-080 (Nutzen vor
    Preis): erst vermutliche Nicht-Starter (prob ≥ 4) TEUERSTE zuerst —
    wenige Verkäufe, totes Kapital zuerst —, dann der Rest BILLIGSTE zuerst.
    Rückgabe: (Entwürfe, ungedeckter Rest in €).

    Handelsspieler (`trade_ids`, Handels-Autopilot): Ihr Verkauf ist geplant und zählt bis
    zum Verkaufstermin als Deckung — dann fasst der Einlöser sie nicht an. Ist der Termin
    verpasst (`trade_due`), gehen sie ZUERST, teuerste zuerst (sie stehen nur auf der Bank).
    Die Stammspieler fallen nie unter 11 (max. Stammkader − 11 Verkäufe) und nie unter die
    Positions-Minima des Stammkaders: Die Handelsspieler gehen zum Termin ohnehin
    (Reviews 02.10.2026).
    """
    # Positions-Reserve-Gate wie in momentum_reserve (9c-Fund 01.09.): die
    # letzte Besetzung einer Position (einziger STU/TW …) wird nie gedraftet —
    # der Executor würde den Verkauf an der Kader-Deckung ohnehin refusen,
    # und ein solches Vorhaben in der Zentrale wäre eine Lüge auf dem Schirm.
    # Gezählt wird je Entwurf herunter (zwei Bank-ABW bei vier ABW sind EINE Reserve) und
    # getrennt: Stammspieler gegen den Stammkader, Handelsspieler gegen den ganzen Kader (so
    # prüft die Schreibstelle) — Codex-Review 02.10.2026.
    minimums = {1: 1, 2: 3, 3: 2, 4: 1}
    squad_counts: dict[int, int] = {}
    regular_counts: dict[int, int] = {}
    for sp in squad:
        pos = int(sp.player.position or 0)
        squad_counts[pos] = squad_counts.get(pos, 0) + 1
        if str(sp.player.id) not in trade_ids:
            regular_counts[pos] = regular_counts.get(pos, 0) + 1
    bench = [
        sp for sp in squad if sp.lineup_order is None and str(sp.player.id) not in protected
    ]

    def _rang(sp: SquadPlayer) -> tuple[int, int]:
        prob = sp.player.lineup_prob
        unlikely = prob is not None and prob >= 4
        mv = int(sp.player.market_value or 0)
        return (0, -mv) if unlikely else (1, mv)

    trade = sorted(
        (sp for sp in bench if trade_due and str(sp.player.id) in trade_ids),
        key=lambda sp: -int(sp.player.market_value or 0),
    )
    bench = sorted((sp for sp in bench if str(sp.player.id) not in trade_ids), key=_rang)
    max_sells = max(0, sum(1 for sp in squad if str(sp.player.id) not in trade_ids) - 11)
    drafts: list[IntentDraft] = []
    raised = 0
    regular_sold = 0
    for sp in trade + bench:
        is_trade = str(sp.player.id) in trade_ids
        if raised >= shortfall or (not is_trade and regular_sold >= max_sells):
            break
        mv = int(sp.player.market_value or 0)
        pos = int(sp.player.position or 0)
        counts = squad_counts if is_trade else regular_counts
        if mv <= 0 or counts.get(pos, 0) <= minimums.get(pos, 0):
            continue
        window = COVER_BIG_WINDOW if mv >= DEFAULT_GUARDRAILS.approval_threshold else COVER_WINDOW
        name = f"{sp.player.first_name} {sp.player.last_name}".strip()
        who = ("Handelsspieler, Verkaufstermin verpasst" if is_trade
               else "Bank-Spieler, Elf bleibt unberührt")
        drafts.append(
            IntentDraft(
                kind="sell",
                player_id=str(sp.player.id),
                player_name=name or str(sp.player.id),
                team_id=str(sp.player.team_id or ""),
                position=int(sp.player.position or 0),
                amount=mv,
                rationale=(
                    "Pfand-Einlöser: Deckungsverkauf vor der Spieltags-Deadline — "
                    f"Worst-Case-Fehlbetrag {shortfall / 1_000_000:.2f}M €, dieser "
                    f"Verkauf deckt {mv / 1_000_000:.2f}M € (Sofortverkauf = voller "
                    f"aktueller MW, L-081; {who})."
                ),
                sources={"cover": True, "rule_id": "pfand-cover"},
                listing_type="kickbase",
                deadline=None,
                execute_at=now + window,
            )
        )
        raised += mv
        squad_counts[pos] -= 1
        if not is_trade:
            regular_counts[pos] -= 1
            regular_sold += 1
    return drafts, max(0, shortfall - raised)


#: Quelle der Merkzeilen „Deckungslücke gemeldet" in `reports` (keine neue Tabelle).
PFAND_GAP_SOURCE = "worker:pfand_einloeser:luecke"
#: Lücken-Änderungen unter dieser Schwelle (tägliches MW-Rauschen der Bank) sind
#: kein neuer Zustand und lösen keine neue Meldung aus.
_PFAND_GAP_BUCKET = 100_000


def pfand_gap_signature(matchday: int, rest: int) -> str:
    """Zustand der Deckungslücke: Spieltag plus Lücke in 0,1-M-Schritten."""
    return f"md{matchday}|{rest // _PFAND_GAP_BUCKET}"


def pfand_gap_already_reported(session: Session, signature: str) -> bool:
    """War GENAU dieser Lückenzustand die zuletzt gemeldete Lage?

    Verglichen wird nur mit der jüngsten Meldung: Kehrt ein früherer Zustand
    nach einer Änderung zurück, ist das wieder ein Wechsel und wird gemeldet.
    Lesefehler heißen „nicht gemeldet" — lieber doppelt als still.
    """
    try:
        row = session.scalars(
            select(Report)
            .where(Report.source == PFAND_GAP_SOURCE)
            .order_by(Report.id.desc())
            .limit(1)
        ).first()
    except Exception:  # noqa: BLE001 — Dedupe darf den Einlöser nie brechen
        session.rollback()
        return False
    return row is not None and (row.meta or {}).get("signature") == signature


#: Merkwert „Lücke geschlossen" — macht ein späteres Wiederaufreißen zum Wechsel.
_PFAND_GAP_CLOSED = "closed"


def pfand_gap_mark_closed(session: Session) -> None:
    """Gedeckte Lage einmal merken, wenn zuletzt eine Lücke gemeldet war.

    Ohne diesen Schluss-Zustand galt eine geschlossene und mit ähnlichem Betrag wieder
    geöffnete Lücke als „schon gemeldet" (Review-Befund 21.09.2026). Geschrieben wird
    nur beim Wechsel, nie in jedem gedeckten Lauf; Fehler brechen den Einlöser nicht.
    """
    try:
        row = session.scalars(
            select(Report)
            .where(Report.source == PFAND_GAP_SOURCE)
            .order_by(Report.id.desc())
            .limit(1)
        ).first()
    except Exception:  # noqa: BLE001 — Dedupe darf den Einlöser nie brechen
        session.rollback()
        return
    if row is None or (row.meta or {}).get("signature") == _PFAND_GAP_CLOSED:
        return
    save_report(
        "info",
        "Pfand-Einlöser: Deckungslücke geschlossen",
        "Worst-Case-Deckung wieder über dem Anpfiff-Puffer oder durch Verkäufe gedeckt.",
        PFAND_GAP_SOURCE,
        meta={"signature": _PFAND_GAP_CLOSED},
        session=session,
    )


async def _trade_cover(
    session: Session,
    client: Any,
    league_id: str,
    squad: list[SquadPlayer],
    own_bids: list[dict[str, Any]],
    stamp: datetime,
    deadline: datetime,
) -> tuple[frozenset[str], int, bool]:
    """Handelsdeckung: (Handelsspieler, was ihr Verkauf bis zum Termin bringt, Termin verpasst?).

    Handelsspieler und Ziele laufender Handelsgebote zählen mit Marktwert × 0,90 bis zum
    Verkaufstermin plus Karenz (`trade_book.sale_cover`). Den Markt liest nur, wer ein
    laufendes Handelsgebot hat (Marktwert des Ziels)."""
    live = {str(b.get("player_id")): int(b.get("amount") or 0)
            for b in own_bids if b.get("player_id")}
    book = load_trade_book(session, now=stamp, squad_ids={str(sp.player.id) for sp in squad},
                           live_bids=live, league_id=league_id)
    if not book.positions and not book.live_bids:
        return frozenset(), 0, False
    trade_ids = frozenset(book.positions)
    sale_by = sale_deadline(deadline, regular_fill(
        int(sp.player.position or 0) for sp in squad if str(sp.player.id) not in trade_ids
    ))
    targets: dict[str, int] = {}
    if book.live_bids:
        targets = {str(m.player.id): int(m.player.market_value or 0)
                   for m in await client.get_market(league_id)}
    traded = sale_cover(
        held_mv=[int(sp.player.market_value or 0) for sp in squad
                 if str(sp.player.id) in trade_ids],
        bid_target_mv=[targets[pid] for pid in book.live_bids if pid in targets],
        now=stamp, sale_by=sale_by,
    )
    return trade_ids, traded, sale_overdue(stamp, sale_by)


async def run_pfand_einloeser(
    session: Session, channel: DiscordChannel | None, stamp: datetime
) -> str | None:
    """Pfand-Einlöser (01.09., Spec §3): Deckung ziehen, BEVOR die Wächter müssen.

    Bedingung: Spieltags-Deadline < 96 h UND Worst-Case-Konto (Konto − offene
    Gebote, „alle schlagen zu") unter dem Anpfiff-Puffer. Dann entstehen
    sichtbare SOFORTVERKAUFS-Vorhaben (45-min-Veto-Karten, sources.cover) aus
    dem Bank-Pool, bis der Worst-Case wieder über dem Puffer liegt — der
    Einlöser ersetzt die Notbremse nicht, er entlastet sie zwei Tage früher.
    Sofortverkauf statt Listing ist Pflicht der Pfand-Rechnung (L-081: Listings
    bringen empirisch keine Angebote). Offene Deckungsverkäufe zählen auf die
    Deckung, damit der Stundentakt nicht doppelt verkauft. None = nichts zu tun.

    Handel (Review 02.10.2026, P0): Der Handels-Autopilot kauft gewollt ins Minus und
    verkauft zum Verkaufstermin. Dieser Verkauf zählt als Deckung (`_trade_cover`, dieselbe
    Rechnung wie Ergebnis-Wächter und Autopilot) — sonst verkaufte der Einlöser Stammspieler
    für einen Minus, den der Handel selbst schließt.
    """
    client, auth = await make_kickbase_client()
    league_id = auth.league_id
    if league_id is None:
        return None
    fixtures = await client.get_matchdays()
    deadline_pair = matchday_deadline(fixtures, stamp)
    if deadline_pair is None or deadline_pair[1] - stamp > COVER_HORIZON:
        return None
    budget = int(await client.get_budget(league_id))
    own_bids = await client.get_own_bids(league_id, auth.user_id)
    exposure = sum(int(b.get("amount") or 0) for b in own_bids)
    squad = await client.get_squad(league_id)
    trade_ids, traded, trade_due = await _trade_cover(
        session, client, league_id, squad, own_bids, stamp, deadline_pair[1]
    )
    worst_case = budget - exposure + traded
    if worst_case >= KICKOFF_MIN_BALANCE:
        pfand_gap_mark_closed(session)
        return None
    shortfall = KICKOFF_MIN_BALANCE - worst_case
    open_cover_mw = sum(
        int(r.amount or 0)
        for r in open_intents(session)
        if r.kind == "sell" and (r.sources or {}).get("cover")
    )
    shortfall -= open_cover_mw
    if shortfall <= 0:
        pfand_gap_mark_closed(session)
        return None  # bereits gedeckt — offene Deckungsverkäufe laufen
    drafts, rest = cover_sell_drafts(
        squad, protected_player_ids(), shortfall, stamp, trade_ids=trade_ids, trade_due=trade_due
    )
    created = insert_cover_sells(session, drafts, stamp)
    # Veto-Karten postet der Karten-Kehrer des Executor-Ticks per Backfill
    # (cards_needing_post, ≤ 5 min) — eine Post-Stelle, keine zwei.
    # Eine Meldung je ZUSTAND (Turnaround-Studie P0-4): Ohne Bank blieb die Lücke
    # tagelang gleich, und der Stundentakt postete dieselbe Zeile bis zu 90-mal.
    # Neue Deckungsverkäufe sind immer ein Ereignis; eine unveränderte Lücke nicht.
    signature = pfand_gap_signature(deadline_pair[0], rest)
    if channel is not None and (
        created or (rest > 0 and not pfand_gap_already_reported(session, signature))
    ):
        lines = [
            "🛟 **Pfand-Einlöser** — Worst-Case-Deckung vor der Deadline "
            f"({deadline_pair[1]:%a %d.%m. %H:%M} UTC)"
        ]
        for row in created:
            lines.append(
                f"• Deckungsverkauf {row.player_name} {row.amount / 1_000_000:.2f}M € "
                f"— fällig {row.execute_at:%H:%M} UTC (Veto-Karte im Kanal)"
            )
        if rest > 0:
            lines.append(
                f"⚠️ Deckungslücke bleibt: {rest / 1_000_000:.2f}M € — Bank-Pool "
                "erschöpft; den Rest heilt nur T-120-Storno/Notbremse."
            )
        await channel.send_info("\n".join(lines))
        if rest > 0:
            # Erst NACH dem Versand merken: scheitert Discord, meldet der nächste
            # Lauf erneut. Speicherfehler loggt save_report — schlimmstenfalls
            # kommt die Meldung doppelt, nie gar nicht.
            save_report(
                "info",
                "Pfand-Einlöser: Deckungslücke gemeldet",
                lines[-1],
                PFAND_GAP_SOURCE,
                meta={"signature": signature, "matchday": deadline_pair[0], "rest": rest},
                session=session,
            )
    if rest <= 0:
        pfand_gap_mark_closed(session)
    teile = [f"cover={len(created)}"]
    if rest > 0:
        teile.append(f"luecke={rest}")
    return " ".join(teile)


async def run_reconcile(channel: DiscordChannel) -> str:
    """Ungeklärte Aktionen ('unknown' + hängende 'pending') gegen live auflösen.

    Post NUR bei Funden; keine Kandidaten (oder alles schon alarmiert) → "quiet"
    ohne Discord-Nachricht. Kickbase wird ausschließlich GELESEN.
    """
    stamp = datetime.now(UTC)
    resolved: list[dict[str, Any]] = []
    alarms: list[dict[str, Any]] = []
    with SessionLocal() as session:
        # Die beiden Kehrer brauchen keine Live-Daten und laufen bewusst VOR dem
        # frühen Ausstieg: Sonst räumt niemand auf, solange es nichts
        # abzugleichen gibt — und genau das ist der Normalfall (Fund 31.08.:
        # elf abgelaufene Vorschläge lagen zehn Tage, ein Job-Lauf drei).
        swept = expire_lapsed_proposals(session, stamp)
        stuck = close_stuck_runs(session, stamp)

        # Pfand-Einlöser (01.09., Spec §3) — ebenfalls VOR dem frühen Ausstieg
        # (L-088) und unabhängig von Abgleich-Kandidaten: Er wacht stündlich,
        # ob der Worst-Case („alle Gebote schlagen zu") vor der Deadline noch
        # gedeckt ist, und zieht sonst sichtbare Deckungsverkäufe. Er darf den
        # Abgleich nie brechen — Fehler werden gemeldet, nicht geschluckt.
        pfand: str | None = None
        pfand_fehler: str | None = None
        try:
            pfand = await run_pfand_einloeser(session, channel, stamp)
        except Exception as exc:  # noqa: BLE001 — sichtbar machen, weiterlaufen
            pfand_fehler = f"{type(exc).__name__}: {exc}"

        candidates = load_candidates(session, stamp)
        if candidates:
            client, auth = await make_kickbase_client()
            # Live-Zustand je Liga genau EINMAL holen (Kandidaten tragen ihre
            # eigene league_id — normal ist es eine Liga, der Cache kostet nichts).
            states: dict[str, tuple[list[dict[str, Any]], set[str], set[str]]] = {}
            for row in candidates:
                if row.league_id not in states:
                    states[row.league_id] = await _live_state(client, row.league_id, auth.user_id)
                bids, squad_pids, listing_pids = states[row.league_id]
                old_status = row.status
                new_status, note = classify(
                    row, bids, squad_pids, listing_pids, now=datetime.now(UTC)
                )
                if new_status is None:
                    if mark_alarm_once(session, row, note, stamp):
                        alarms.append(
                            {
                                "id": row.id,
                                "action_type": row.action_type,
                                "player_id": row.player_id,
                                "status": old_status,
                                "note": note,
                            }
                        )
                    continue
                if resolve_status(session, row, new_status, note, stamp):
                    resolved.append(
                        {
                            "id": row.id,
                            "action_type": row.action_type,
                            "player_id": row.player_id,
                            "old_status": old_status,
                            "new_status": new_status,
                            "note": note,
                        }
                    )

    if pfand_fehler is not None and channel is not None:
        await channel.send_info(
            f"⚠️ **Pfand-Einlöser fehlgeschlagen** — {pfand_fehler}. Die Deckungs-"
            "Wache hat diesen Lauf ausgesetzt; T-120/T-15 bleiben das Netz."
        )
    msg = format_report(resolved, alarms, swept=swept, stuck=stuck)
    if msg is None:
        # Einlöser-Aktivität hält den Lauf trotzdem ehrlich in der Job-Historie.
        teile_leise = [t for t in (pfand, f"fehler={pfand_fehler}" if pfand_fehler else None) if t]
        return " ".join(teile_leise) if teile_leise else "quiet"
    await channel.send_info(msg)
    # Der Kehrer taucht nur auf, wenn er etwas getan hat — „swept=0 stuck=0"
    # an jeder Zeile wäre Rauschen in der Job-Historie.
    teile = [f"resolved={len(resolved)}", f"alarms={len(alarms)}"]
    if swept:
        teile.append(f"swept={len(swept)}")
    if stuck:
        teile.append(f"stuck={stuck}")
    if pfand:
        teile.append(pfand)
    if pfand_fehler:
        teile.append(f"pfand_fehler={pfand_fehler}")
    return " ".join(teile)
