"""Belege zu Erkenntnissen: schreiben, zählen, auflisten (Spec §3.2/§4.2).

Die Lücke, die dieses Modul schließt: Bisher konnte kein Eintrag in `LEARNINGS.md`
sagen, ob er sich bewährt hat. Hier wächst die andere Hälfte — was der Betrieb seit
der Behauptung beobachtet hat, verankert an der stabilen ID (`L-051`).

Zwei Regeln tragen alles:

- **Ein Ereignis zählt einmal.** `(learning_id, ref)` ist eindeutig; ein doppelter
  Beleg wird still verworfen (Rückgabe ``None``/leere Liste), nicht als Fehler
  geworfen — der Sammler läuft absichtlich über überlappende Fenster.
- **`source` gehört dem System, nicht dem Aufrufer.** `record_drafts` schreibt immer
  ``auto``, `record_agent_evidence` immer ``agent``. Es gibt keinen Weg, das zu
  überschreiben; sonst wäre die Trennung „gemessen vs. beurteilt" im UI wertlos.

Gelöscht wird nie: Eine widerlegte Annahme behält ihre Geschichte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from kickbase_agent.db.models import LearningEvidence

CONFIRM, REFUTE = "confirm", "refute"
VERDICTS = (CONFIRM, REFUTE)
SOURCE_AUTO, SOURCE_AGENT = "auto", "agent"

#: Anker-Format der Behauptung im Repo (`L-051`).
LEARNING_ID_RE = re.compile(r"^L-\d{3,}$")
#: So viele Einzelbelege zeigt die API je Erkenntnis (Spec §7).
ITEM_LIMIT = 10
#: Obergrenzen, damit ein Tippfehler die Spalten nicht sprengt.
REF_MAX, NOTE_MAX = 200, 2000


@dataclass(frozen=True)
class EvidenceDraft:
    """Was eine Auto-Regel gefunden hat — noch ohne Quelle und Regelschlüssel.

    Der Entwurf trägt bewusst kein ``source``: Den setzt erst das Schreiben, und zwar
    immer auf ``auto``. Eine Regel kann sich damit nicht als Agenten-Urteil ausgeben.
    """

    learning_id: str
    verdict: str
    ref: str
    note: str
    observed_at: datetime


def _as_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(UTC)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _validate(learning_id: str, verdict: str, ref: str, note: str) -> tuple[str, str, str, str]:
    lid = (learning_id or "").strip().upper()
    if not LEARNING_ID_RE.match(lid):
        raise ValueError(f"learning_id muss wie 'L-051' aussehen, nicht {learning_id!r}")
    v = (verdict or "").strip().lower()
    if v not in VERDICTS:
        raise ValueError(f"verdict muss {' | '.join(VERDICTS)} sein, nicht {verdict!r}")
    r = (ref or "").strip()[:REF_MAX]
    if not r:
        raise ValueError("ref ist Pflicht — ohne Ereignis-Referenz zählt ein Beleg doppelt")
    n = (note or "").strip()[:NOTE_MAX]
    if not n:
        raise ValueError("note ist Pflicht — ein Beleg ohne Beobachtung ist keiner")
    return lid, v, r, n


def _insert(
    session: Session,
    *,
    learning_id: str,
    verdict: str,
    ref: str,
    note: str,
    source: str,
    rule_key: str,
    observed_at: datetime,
) -> LearningEvidence | None:
    """Eine Zeile schreiben; verletzt sie die Unique-Sperre, gibt es ``None``.

    Der SAVEPOINT ist wesentlich, kein Zierrat (im Test gefangen): Ein einfaches
    ``session.rollback()`` beim Duplikat würde die im selben Stapel bereits
    geschriebenen NEUEN Belege mit verwerfen. Der Sammler läuft absichtlich über
    überlappende Fenster — bekannte und neue Belege liegen dort ständig gemischt.
    """
    row = LearningEvidence(
        learning_id=learning_id,
        observed_at=observed_at,
        verdict=verdict,
        source=source,
        ref=ref,
        note=note,
        rule_key=rule_key,
        created_at=datetime.now(UTC),
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        # Kein Fehlerfall: Das Ereignis war für diese Erkenntnis schon belegt.
        return None
    return row


def record_agent_evidence(
    session: Session,
    learning_id: str,
    verdict: str,
    *,
    note: str,
    ref: str,
    observed_at: datetime | None = None,
) -> LearningEvidence | None:
    """Beleg, den ICH beurteilt habe (`source=agent`). Doppelter `ref` ⇒ ``None``.

    Pflichtfelder sind ID, Verdikt, Notiz und `ref` (Spec §4.2). Die Notiz ist kein
    Schmuck: Sie ist das Einzige, woran später erkennbar ist, WAS beobachtet wurde.
    """
    lid, v, r, n = _validate(learning_id, verdict, ref, note)
    row = _insert(
        session, learning_id=lid, verdict=v, ref=r, note=n,
        source=SOURCE_AGENT, rule_key="", observed_at=_as_utc(observed_at),
    )
    session.commit()
    return row


def record_drafts(
    session: Session, drafts: list[EvidenceDraft], *, rule_key: str
) -> list[LearningEvidence]:
    """Funde einer Auto-Regel schreiben (`source=auto`). Bekannte `ref`s fallen raus."""
    written: list[LearningEvidence] = []
    for draft in drafts:
        lid, v, r, n = _validate(draft.learning_id, draft.verdict, draft.ref, draft.note)
        row = _insert(
            session, learning_id=lid, verdict=v, ref=r, note=n,
            source=SOURCE_AUTO, rule_key=rule_key.strip()[:64],
            observed_at=_as_utc(draft.observed_at),
        )
        if row is not None:
            written.append(row)
    session.commit()
    return written


# ------------------------------------------------------------------ Lesen


def _rows(
    session: Session, learning_ids: list[str] | None = None, since: datetime | None = None
) -> list[LearningEvidence]:
    stmt = select(LearningEvidence)
    if learning_ids is not None:
        if not learning_ids:
            return []
        stmt = stmt.where(LearningEvidence.learning_id.in_(sorted(set(learning_ids))))
    if since is not None:
        stmt = stmt.where(LearningEvidence.observed_at >= since)
    stmt = stmt.order_by(LearningEvidence.observed_at.desc(), LearningEvidence.id.desc())
    try:
        return list(session.execute(stmt).scalars().all())
    except SQLAlchemyError:
        # Alte DB ohne Migration: Die Lern-Schicht darf daran nie scheitern — sie
        # zeigt dann eben „noch keine Erfahrung" statt eines 500ers.
        session.rollback()
        return []


def summaries(
    session: Session, learning_ids: list[str] | None = None, since: datetime | None = None
) -> dict[str, dict[str, Any]]:
    """``{learning_id: {confirm, refute, auto, agent, agent_refute, first_at, last_at}}``.

    ``first_at`` trägt die Reifeprüfung: „bewährt" verlangt nicht nur vier Belege,
    sondern vier Belege über vierzehn Tage — vier Treffer an einem Nachmittag sind
    ein Zufall, kein Beweis.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in _rows(session, learning_ids, since):
        bucket = out.setdefault(
            row.learning_id,
            {"confirm": 0, "refute": 0, "auto": 0, "agent": 0, "agent_refute": 0,
             "first_at": None, "last_at": None},
        )
        bucket[row.verdict] = bucket.get(row.verdict, 0) + 1
        bucket[row.source] = bucket.get(row.source, 0) + 1
        if row.source == SOURCE_AGENT and row.verdict == REFUTE:
            bucket["agent_refute"] += 1
        stamp = _as_utc(row.observed_at).isoformat()
        if bucket["last_at"] is None or stamp > bucket["last_at"]:
            bucket["last_at"] = stamp
        if bucket["first_at"] is None or stamp < bucket["first_at"]:
            bucket["first_at"] = stamp
    return out


def items_for(
    session: Session, learning_ids: list[str], limit: int = ITEM_LIMIT
) -> dict[str, list[dict[str, Any]]]:
    """Einzelbelege je Erkenntnis, neueste zuerst, höchstens ``limit`` Stück."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(session, learning_ids):
        bucket = out.setdefault(row.learning_id, [])
        if len(bucket) >= limit:
            continue
        bucket.append(
            {
                "observed_at": _as_utc(row.observed_at).isoformat(),
                "verdict": row.verdict,
                "source": row.source,
                "ref": row.ref,
                "note": row.note,
                "rule_key": row.rule_key,
            }
        )
    return out
