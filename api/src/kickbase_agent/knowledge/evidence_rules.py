"""Auto-Regeln: Belege aus Spuren, die der Betrieb ohnehin hinterlässt (Spec §4.1).

Eine Regel ist eine kleine Funktion `(session, since, now) -> [EvidenceDraft]`. Sie
liest **keine neue Datenquelle** und erzeugt **keinen zusätzlichen API-Verkehr** —
nur `transfer_actions` (Status, `rationale`, `payload`) und, wenn eine Regel es
braucht, `job_runs`. Was der Betrieb nicht ohnehin protokolliert, wird nicht belegt.

Start-Umfang bewusst klein: **drei Regeln**, die heute schon belegbar sind. Weitere
kommen dazu, wenn eine Hypothese es verdient — nicht auf Vorrat.

**Die wichtigste Grenze: keine Selbstbestätigung.** Der Executor-Preflight setzt die
Underpay-Sperre bereits durch. Zählte man seine `refused`-Zeilen als Belege, bestätigte
die Regel sich selbst und die Bilanz wäre ein Echo statt einer Messung. Deshalb zählt
nur, was KICKBASE geantwortet hat (`status='failed'` mit dem Fehler-Body im
`rationale` — den schreibt `_dispatch` seit 24.08. dorthin).

Verdikt-Richtung: `confirm` stützt die Behauptung des Eintrags, `refute` spricht
dagegen. Bei einer Panne heißt `refute` „der Vorfall ist wieder passiert".
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from kickbase_agent.db.models import TransferAction
from kickbase_agent.knowledge.evidence import CONFIRM, REFUTE, EvidenceDraft, record_drafts

log = logging.getLogger(__name__)

#: Anker der belegten Behauptungen in `.claude/skills/kickbase/LEARNINGS.md`.
#: Beim Umformulieren eines Eintrags bleibt die ID — genau dafür gibt es sie.
L_UNDERPAY = "L-054"      #: err 5080: Manager-Listings verlangen >= 100 % Marktwert
L_BESCHAFFUNG = "L-065"   #: Kickbase-Listings gewinnen, Manager-Deals scheitern
L_PRAEMIE = "L-047"       #: Unser eigener Collect löst die Auflaufprämie aus

#: Kickbase-Fehlercode „UnderpayNotAllowed" (Leiter-Beweis 24.08.: 90 % ✗, 100 % ✓).
_ERR_5080_RE = re.compile(r"\berr\W{0,3}5080\b|\b5080\b")
KICKBASE, MANAGER = "kickbase", "manager"


@dataclass(frozen=True)
class Rule:
    """Eine Belegregel mit ihrem Schlüssel (landet in `learning_evidence.rule_key`)."""

    key: str
    learning_id: str
    fn: Callable[[Session, datetime, datetime], list[EvidenceDraft]]


@dataclass
class CollectResult:
    """Was ein Sammellauf getan hat — Grundlage der Discord-Meldung."""

    written: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)
    touched: set[str] = field(default_factory=set)   #: betroffene learning_ids
    failed: list[str] = field(default_factory=list)  #: Regeln, die geworfen haben


def _as_ts(row: TransferAction) -> datetime:
    return row.resolved_at or row.created_at


def _actions(
    session: Session, since: datetime | None, now: datetime, **where: Any
) -> list[TransferAction]:
    """Aktionen bis `now`; ohne Untergrenze kann die Regel spätere Ergebnisse prüfen."""
    stmt = select(TransferAction).where(TransferAction.created_at <= now)
    if since is not None:
        stmt = stmt.where(TransferAction.created_at >= since)
    for column, value in where.items():
        stmt = stmt.where(getattr(TransferAction, column) == value)
    return list(session.execute(stmt.order_by(TransferAction.id.asc())).scalars().all())


# ------------------------------------------------------------------ Regel 1


def underpay_5080(session: Session, since: datetime, now: datetime) -> list[EvidenceDraft]:
    """Kickbase lehnte ein Gebot mit `err 5080` ab ⇒ die Underpay-Sperre existiert.

    Gezählt wird NUR `status='failed'`: Dort steht die Antwort der API. `refused`
    stammt aus unserem eigenen Preflight — das ist die Regel in Anwendung, kein Beleg.

    Ein Gegenbeleg wäre ein Gebot unter Marktwert auf ein Manager-Listing, das
    Kickbase ANNIMMT. Den kann diese Regel nicht sehen, weil der Preflight solche
    Gebote gar nicht erst abschickt — dieser blinde Fleck ist bewusst und muss von
    Hand belegt werden (`kba learn-evidence L-054 refute …`), wenn er je auftritt.
    """
    out: list[EvidenceDraft] = []
    for row in _actions(session, since, now, status="failed"):
        if not _ERR_5080_RE.search(row.rationale or ""):
            continue
        out.append(
            EvidenceDraft(
                learning_id=L_UNDERPAY,
                verdict=CONFIRM,
                ref=f"action:{row.id}",
                note=(
                    f"Kickbase lehnte {row.action_type} über {row.amount or 0} € "
                    f"mit err 5080 ab (Spieler {row.player_id})."
                ),
                observed_at=_as_ts(row),
            )
        )
    return out


# ------------------------------------------------------------------ Regel 2


def manager_deal_abgelehnt(
    session: Session, since: datetime, now: datetime
) -> list[EvidenceDraft]:
    """Abgeschlossene Gebote auf MANAGER-Listings: verloren stützt, gewonnen spricht dagegen.

    Quelle ist die Gebots-Nachschau (`payload.expectation` + `payload.outcome`,
    trading/expectation.py) — sie bucht das Ergebnis ohnehin stündlich. Ein noch
    offenes Gebot ist kein Ergebnis und zählt nicht.
    """
    out: list[EvidenceDraft] = []
    # Ein Gebot kann vor dem letzten Sammellauf abgegeben worden sein und erst
    # heute enden. Entscheidend ist der Zeitpunkt des Ausgangs, nicht des Gebots.
    for row in _actions(session, None, now, action_type="bid", status="executed"):
        data = row.payload if isinstance(row.payload, dict) else {}
        expectation = data.get("expectation") or {}
        outcome = data.get("outcome") or {}
        if str(expectation.get("kind") or "") != MANAGER:
            continue
        result = str(outcome.get("result") or "")
        if result not in ("won", "lost"):
            continue
        if outcome.get("evidence_version") == 2 and not outcome.get("training_eligible"):
            continue
        observed_at = _parse_at(outcome.get("at")) or _as_ts(row)
        if not _as_utc(since) <= _as_utc(observed_at) <= _as_utc(now):
            continue
        lost = result == "lost"
        out.append(
            EvidenceDraft(
                learning_id=L_BESCHAFFUNG,
                verdict=CONFIRM if lost else REFUTE,
                ref=f"action:{row.id}",
                note=(
                    f"Manager-Deal {'gescheitert' if lost else 'gelungen'}: "
                    f"{row.amount or 0} € auf Spieler {row.player_id} "
                    + (f"(Erwartung {float(expectation['p']):.0%})."
                     if expectation.get("p") is not None else "(Erwartung unbekannt).")
                ),
                observed_at=observed_at,
            )
        )
    return out


def _parse_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _as_utc(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


# ------------------------------------------------------------------ Regel 3


def praemie_gutschrift(session: Session, since: datetime, now: datetime) -> list[EvidenceDraft]:
    """Prämien-Läufe: echte Gutschrift stützt, ein Fehlschlag spricht dagegen.

    `noop` zählt NICHT: Ein leerer Griff nach einer Mitternachts-Einsammlung ist laut
    Beweis vom 24.08. korrektes Verhalten, kein Ausfall. Ihn als Gegenbeleg zu buchen
    hieße, richtige Arbeit als Fehler zu zählen. Dry-Run-Zeilen zählen ebenfalls nicht
    — sie haben nie mit Kickbase gesprochen.
    """
    out: list[EvidenceDraft] = []
    for row in _actions(session, since, now, action_type="bonus"):
        if row.dry_run or row.status not in ("executed", "failed"):
            continue
        ok = row.status == "executed"
        out.append(
            EvidenceDraft(
                learning_id=L_PRAEMIE,
                verdict=CONFIRM if ok else REFUTE,
                ref=f"action:{row.id}",
                note=(
                    f"Prämie selbst kassiert (+{row.amount or 0} €)."
                    if ok
                    else f"Prämien-Aufruf fehlgeschlagen: {(row.rationale or '')[:120]}"
                ),
                observed_at=_as_ts(row),
            )
        )
    return out


# ------------------------------------------------------------------ Sammellauf


REGISTRY: list[Rule] = [
    Rule("underpay-5080", L_UNDERPAY, underpay_5080),
    Rule("manager-deal-abgelehnt", L_BESCHAFFUNG, manager_deal_abgelehnt),
    Rule("praemie-gutschrift", L_PRAEMIE, praemie_gutschrift),
]


def all_rules() -> list[Rule]:
    return list(REGISTRY)


def collect(session: Session, *, since: datetime, now: datetime) -> CollectResult:
    """Alle Regeln über das Fenster laufen lassen und die Funde schreiben.

    Idempotent über die Unique-Sperre `(learning_id, ref)`: Ein zweiter Lauf über
    dasselbe Fenster schreibt nichts. Deshalb darf das Fenster großzügig überlappen —
    lieber doppelt gesucht als ein Ereignis verpasst.

    Eine Regel, die wirft, wird protokolliert und übersprungen: Sie ist eine Vermutung
    über Datenformate und darf die anderen nicht mitreißen.
    """
    result = CollectResult()
    for rule in REGISTRY:
        try:
            drafts = rule.fn(session, since, now)
            written = record_drafts(session, drafts, rule_key=rule.key)
        except (SQLAlchemyError, ValueError, TypeError, KeyError, AttributeError) as exc:
            log.warning("Belegregel %s übersprungen: %s: %s", rule.key, type(exc).__name__, exc)
            session.rollback()
            result.failed.append(rule.key)
            continue
        if written:
            result.written += len(written)
            result.by_rule[rule.key] = len(written)
            result.touched.update(row.learning_id for row in written)
    return result
