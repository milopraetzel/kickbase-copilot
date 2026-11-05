"""Gemeinsamer Aktions-Service für Vorhaben: Veto, Anpassen, Sofort ausführen.

Spec: docs/superpowers/specs/2026-08-31-discord-veto-buttons.md (§4.2). Extrahiert
aus `server/routes/intents.py` — VERHALTENSNEUTRAL: die Route und der
Discord-Button-Handler rufen dieselben Funktionen; die Route übersetzt die
typisierten Fehler in ihre heutigen 404/409/400-Texte (`detail` trägt exakt den
alten Wortlaut), Discord in ephemere Kurzmeldungen.

Eiserne Regel bleibt: diese Funktionen ändern ausschließlich Zeilen in `intents`
— nie Kickbase, nie `transfer_actions`. Zustandswechsel sind atomare, bedingte
UPDATEs (`WHERE status = 'planned'`), damit ein gleichzeitig zugreifender
Executor-Tick nie ein bereits ausgeführtes Vorhaben „übersprungen" sieht.

`now` akzeptiert bewusst datetime ODER einen Null-Argument-Callable: die Route
reicht ihr modul-eigenes `_now` durch, das erst NACH dem Lesen des Ist-Standes
aufgerufen wird — exakt die Stelle, an der die Race-Tests der API ihre
Konkurrenz-Mutation einschleusen (Lesen → fremder Write → bedingtes UPDATE).
Ein vorab aufgelöster Zeitstempel würde diese Reihenfolge (und damit die
Verhaltensneutralität) brechen.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Intent
from kickbase_agent.decision.purchase_review import (
    is_engine_review,
    purchase_review_veto,
    team_selection_veto,
)
from kickbase_agent.mcp._util import fmt_money

#: Obergrenze fuers Anpassen, relativ zum URSPRUENGLICH geplanten Betrag (24.08.):
#: Emils Buttons (+10 %/+20 %, auch mehrfach) bleiben moeglich, ein absurder Betrag
#: nicht. Die echte Preisgrenze zieht der Executor-Preflight (110 % Marktwert) —
#: das hier verhindert, dass ueberhaupt eine absurde Zeile entsteht.
ADJUST_MAX_FACTOR = 1.5

#: Kinds, deren Betrag Emil anpassen kann (Sofortverkauf/Aufstellung haben keinen).
ADJUSTABLE_KINDS = ("bid", "list")

#: Deutsche Statusworte — eine Quelle für Route (409-Texte) und Discord-Antworten.
INTENT_STATUS_DE = {
    "planned": "geplant",
    "executed": "ausgeführt",
    "vetoed": "übersprungen",
    "expired": "abgelaufen",
    "failed": "fehlgeschlagen",
    "superseded": "ersetzt",
    "executing": "wird ausgeführt",
}

#: Wer die Aktion ausgelöst hat. „web" ist der heutige Standard (Zentrale) und
#: hinterlässt KEINE Zusatzspur — sonst wären die bestehenden API-Antworten
#: (emil_note exakt Emils Notiz) nicht mehr verhaltensneutral. Jeder andere
#: Akteur (aktuell „discord") schreibt eine Append-Zeile in `emil_note`,
#: z. B. `[Discord] Veto 14:32` — das Feld gehört Emil, der Planner
#: überschreibt es nie (Protokoll-Frage „Wer hat es freigegeben?", CLAUDE.md §9).
ACTOR_WEB = "web"
ACTOR_DISCORD = "discord"

_BERLIN = ZoneInfo("Europe/Berlin")

#: `now` als Wert oder als aufschiebbarer Lieferant (siehe Modul-Docstring).
NowArg = datetime | Callable[[], datetime]


def _resolve_now(now: NowArg) -> datetime:
    return now() if callable(now) else now


# ------------------------------------------------------------------ Fehler


class IntentActionError(Exception):
    """Basisfehler; `detail` trägt den Wortlaut der heutigen API-Antworten."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class IntentNotFound(IntentActionError):  # noqa: N818 — Name von der Spec (§4.2) vorgegeben
    """Vorhaben existiert nicht (Route: 404)."""

    def __init__(self, intent_id: int) -> None:
        self.intent_id = intent_id
        super().__init__(f"Vorhaben {intent_id} nicht gefunden")


class IntentNotPlanned(IntentActionError):  # noqa: N818 — Name von der Spec (§4.2) vorgegeben
    """Vorhaben ist nicht mehr `planned` (Route: 409)."""

    def __init__(self, intent_id: int, status: str, verb: str) -> None:
        self.intent_id = intent_id
        self.status = status
        self.verb = verb
        status_de = INTENT_STATUS_DE.get(status, status)
        super().__init__(
            f"Vorhaben {intent_id} ist bereits '{status}' ({status_de}) — "
            f"{verb} ist nur bei geplanten Vorhaben möglich."
        )


class IntentAdjustError(IntentActionError):
    """Anpassen abgelehnt. `conflict` = Konkurrenz-Änderung (Route: 409, sonst 400);
    `ceiling` ist gesetzt, wenn die 1,5×-Grenze der Grund war (Discord-Kurztext)."""

    def __init__(self, grund: str, *, conflict: bool = False, ceiling: int | None = None) -> None:
        self.conflict = conflict
        self.ceiling = ceiling
        super().__init__(grund)


# ------------------------------------------------------------------ Bausteine


def _get_intent(session: Session, intent_id: int) -> Intent:
    intent = session.get(Intent, intent_id)
    if intent is None:
        raise IntentNotFound(intent_id)
    return intent


def _not_planned(intent: Intent, verb: str) -> IntentNotPlanned:
    return IntentNotPlanned(int(intent.id), intent.status, verb)


def _claim_planned(session: Session, intent_id: int, verb: str, **values: Any) -> Intent:
    """Atomarer Zustandswechsel: UPDATE … WHERE status='planned'.

    rowcount 0 ⇒ IntentNotFound/IntentNotPlanned (der frische Ist-Status steht
    in der Exception) — identische Semantik für Web-Route und Discord-Buttons.
    """
    result = session.execute(
        update(Intent)
        .where(Intent.id == intent_id, Intent.status == "planned")
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        session.rollback()
        raise _not_planned(_get_intent(session, intent_id), verb)
    session.commit()
    session.expire_all()
    return _get_intent(session, intent_id)


def _actor_note(current: str | None, actor: str, verb_label: str, now: datetime) -> str:
    """Append-Zeile `[Discord] Veto 14:32` (Berlin-Zeit) unter Emils Notiz."""
    line = f"[{actor.capitalize()}] {verb_label} {now.astimezone(_BERLIN).strftime('%H:%M')}"
    return f"{current}\n{line}" if current else line


# ------------------------------------------------------------------ Aktionen


def veto_intent(
    session: Session,
    intent_id: int,
    *,
    note: str | None,
    actor: str,
    now: NowArg,
) -> Intent:
    """Veto: planned -> vetoed (+ optionale Notiz). Der Executor fasst es nie mehr an.

    `executing`-Fenster (Review 31.08.): Ein Veto während des laufenden Versuchs
    lief bisher spurlos ins Leere (rowcount 0) — und der pbaa-Retry belebte das
    Vorhaben danach wieder. Jetzt wird der Wunsch atomar in `veto_requested_at`
    VORGEMERKT: Scheitert der laufende Versuch (refused/failed) oder stünde ein
    Retry an, löst der Executor die Vormerkung als `vetoed` ein. Ist der Versuch
    bereits durch (executed), ist das Veto ehrlich zu spät — dann wie bisher
    IntentNotPlanned. Der Aufrufer erkennt die Vormerkung an
    `status == "executing"` und gesetztem `veto_requested_at`.
    """
    intent = _get_intent(session, intent_id)  # IntentNotFound vor IntentNotPlanned
    now_dt = _resolve_now(now)
    values: dict[str, Any] = {"status": "vetoed", "updated_at": now_dt}
    if note is not None:
        values["emil_note"] = note.strip() or None
    if actor != ACTOR_WEB:
        base = values["emil_note"] if "emil_note" in values else intent.emil_note
        values["emil_note"] = _actor_note(base, actor, "Veto", now_dt)
    try:
        return _claim_planned(session, intent_id, "Überspringen", **values)
    except IntentNotPlanned as exc:
        if exc.status != "executing":
            raise
        # Atomar vormerken — greift nur, solange der Versuch noch läuft.
        res = session.execute(
            update(Intent)
            .where(Intent.id == intent_id, Intent.status == "executing")
            .values(veto_requested_at=now_dt, updated_at=now_dt)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount == 0:
            session.rollback()
            raise _not_planned(_get_intent(session, intent_id), "Überspringen") from exc
        session.commit()
        session.expire_all()
        return _get_intent(session, intent_id)


def execute_intent_now(
    session: Session,
    intent_id: int,
    *,
    actor: str,
    now: NowArg,
) -> Intent:
    """`execute_at = jetzt` — der Executor-Job nimmt das Vorhaben beim nächsten Tick.
    Es wird hier NICHTS ausgeführt (kein Kickbase-Write aus Route oder Button)."""
    intent = _get_intent(session, intent_id)
    now_dt = _resolve_now(now)
    values: dict[str, Any] = {"execute_at": now_dt, "updated_at": now_dt}
    if actor != ACTOR_WEB:
        values["emil_note"] = _actor_note(intent.emil_note, actor, "Sofort ausführen", now_dt)
    return _claim_planned(session, intent_id, "Sofort ausführen", **values)


def _snap_pct_to_scenario(
    review: Any, old_amount: int, new_amount: int, pct: float
) -> int:
    """Prozent-Anpassung (+10 % / +20 %) auf geprüfte Engine-Preisszenarien rastern."""
    if not is_engine_review(review) or not isinstance(review, dict):
        return new_amount
    scenarios = review.get("bid_scenarios")
    if not isinstance(scenarios, list):
        return new_amount
    prices = sorted(
        s["amount"]
        for s in scenarios
        if isinstance(s, dict) and type(s.get("amount")) is int and s["amount"] > 0
    )
    if not prices or new_amount in prices:
        return new_amount
    if pct > 0:
        up_le = [p for p in prices if old_amount < p <= new_amount]
        if up_le:
            return up_le[-1]
        up_gt = [p for p in prices if p > old_amount]
        if up_gt:
            return up_gt[0]
    elif pct < 0:
        down_ge = [p for p in prices if new_amount <= p < old_amount]
        if down_ge:
            return down_ge[0]
        down_lt = [p for p in prices if p < old_amount]
        if down_lt:
            return down_lt[-1]
    return new_amount


def adjust_intent(
    session: Session,
    intent_id: int,
    *,
    pct: float | None = None,
    amount: int | None = None,
    actor: str,
    now: NowArg,
) -> Intent:
    """Betrag ändern (absolut oder ±%). `adjusted_from` hält den Erst-Betrag fest
    (nur beim ERSTEN Anpassen gesetzt) — der Planner überschreibt ihn dann nicht mehr."""
    intent = _get_intent(session, intent_id)
    if intent.status != "planned":
        raise _not_planned(intent, "Anpassen")
    if intent.kind not in ADJUSTABLE_KINDS:
        raise IntentAdjustError(
            f"Vorhaben {intent.id} ('{intent.kind}') hat keinen anpassbaren Betrag — "
            "nur Gebot (bid) und Listing (list) lassen sich ändern."
        )
    old_amount = int(intent.amount or 0)
    if amount is not None:
        new_amount = int(amount)
    else:
        assert pct is not None  # Route: Schema erzwingt genau eines; Discord: pct fest
        new_amount = int(round(old_amount * (1 + pct / 100)))
        if intent.kind == "bid":
            new_amount = _snap_pct_to_scenario(
                (intent.sources or {}).get("purchase_review"),
                old_amount,
                new_amount,
                pct,
            )
    if new_amount < 1:
        raise IntentAdjustError(
            f"Neuer Betrag {new_amount} € ist ungültig — muss mindestens 1 € sein."
        )
    base = int(intent.adjusted_from or old_amount)
    ceiling = int(base * ADJUST_MAX_FACTOR)
    if base > 0 and new_amount > ceiling:
        raise IntentAdjustError(
            f"Neuer Betrag {fmt_money(new_amount)} übersteigt die Anpass-Grenze "
            f"{fmt_money(ceiling)} ({int(ADJUST_MAX_FACTOR * 100)} % des geplanten "
            f"{fmt_money(base)}) — für mehr ein neues Vorhaben anlegen.",
            ceiling=ceiling,
        )

    now_dt = _resolve_now(now)
    if intent.kind == "bid":
        review = (intent.sources or {}).get("purchase_review")
        problem = purchase_review_veto(
            review, player_id=intent.player_id, amount=new_amount, now=now_dt, required=False,
        )
        if problem is not None:
            limit = review.get("max_bid_eur") if isinstance(review, dict) else None
            raise IntentAdjustError(
                f"Individuelle Kaufprüfung: {problem}",
                ceiling=limit if type(limit) is int and limit > 0 else None,
            )
        if is_engine_review(review):
            assert isinstance(review, dict)
            reviews, amounts = {intent.player_id: review}, {intent.player_id: new_amount}
            for other in session.query(Intent).filter(
                Intent.kind == "bid", Intent.status.in_(("planned", "executing")),
            ):
                proof = (other.sources or {}).get("purchase_review")
                if (other.id == intent.id or not isinstance(proof, dict)
                        or proof.get("plan_id") != review["plan_id"]):
                    continue
                problem = purchase_review_veto(
                    proof, player_id=other.player_id, amount=other.amount, now=now_dt,
                )
                if problem is not None:
                    raise IntentAdjustError(f"Mannschaftsplan erneut prüfen: {problem}")
                reviews[other.player_id], amounts[other.player_id] = proof, other.amount
            problem = team_selection_veto(reviews, amounts)
            if problem is not None:
                raise IntentAdjustError(problem)
    values: dict[str, Any] = {
        "amount": new_amount,
        # nur beim ersten Anpassen setzen; danach bleibt der Ur-Betrag stehen
        "adjusted_from": intent.adjusted_from if intent.adjusted_from is not None else old_amount,
        "updated_at": now_dt,
    }
    if actor != ACTOR_WEB:
        values["emil_note"] = _actor_note(intent.emil_note, actor, "Anpassung", now_dt)
    result = session.execute(
        update(Intent)
        .where(Intent.id == intent_id, Intent.status == "planned", Intent.amount == old_amount)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        session.rollback()
        fresh = _get_intent(session, intent_id)
        if fresh.status != "planned":
            raise _not_planned(fresh, "Anpassen")
        raise IntentAdjustError(
            f"Vorhaben {intent_id} wurde gerade geändert (Betrag jetzt "
            f"{fmt_money(fresh.amount)}) — bitte neu laden und erneut anpassen.",
            conflict=True,
        )
    session.commit()
    session.expire_all()
    return _get_intent(session, intent_id)
