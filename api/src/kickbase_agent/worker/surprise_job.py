"""Der Überraschungs-Auslöser: sucht Widersprüche und schweigt, wenn es keine gibt.

Emil, 26.08. (wörtlich): „Nur wenn irgendwas Unerwartetes kommt — wenn du mit
irgendwas gerechnet hast und es hat nicht geklappt … Wenn alles normal klappt, muss
nichts geändert werden."

Eine Überraschung ist immer dasselbe Muster: **eine festgehaltene Erwartung, eine
gemessene Wirklichkeit, ein Widerspruch.** Drei Quellen, alle schon vorhanden — es
wird nichts Neues gemessen, nur endlich verglichen:

| Art      | Erwartung lag vor als         | Überraschung, wenn |
|----------|-------------------------------|--------------------|
| `gebot`  | `payload["expectation"]["p"]` | p ≥ 0,60 und verloren, p ≤ 0,20 und gewonnen |
| `job`    | der Trigger sagt „läuft um X" | ein Job kippt von `ok` nach `failed` |
| `sperre` | der Planner hielt es für ok   | der Executor antwortet `refused` (Preflight) |

Was ausdrücklich KEINE Überraschung ist: ein schwankender Marktwert, ein verlorenes
Gebot mit niedriger Erwartung, ein Job, der schon gestern kaputt war, ein `refused`
wegen fehlendem Flag oder wegen des Selbst-Einfrierens. Für all das gibt es entweder
die Wochen-Bilanz oder eine eigene Meldung — hier wäre es Lärm.

Dieses Modul entscheidet nichts. Es merkt offene Aktionsfälle am Audit-Eintrag
vor und legt sie dem Agenten vor (agent/judgment.py); das Urteil fällt dort.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import JobRun, TransferAction
from kickbase_agent.db.session import SessionLocal

log = logging.getLogger(__name__)

#: Suchfenster für neue Fälle. Bereits vorgemerkte Aktionsfälle bleiben darüber
#: hinaus offen, bis ein Urteil vorliegt. Job-Fälle haben noch keinen Retry-Speicher.
DEFAULT_WINDOW_DAYS = 1

#: Refusal-Gründe, die KEINE Überraschung sind: der Interlock (ein Flag ist aus —
#: das ist der geplante Zustand) und das Selbst-Einfrieren (sonst erzeugte ein
#: eingefrorener Tag Dutzende Meldungen über denselben Sachverhalt).
_EXPECTED_REFUSALS = ("Live-Write in Liga", "Selbst-Einfrieren", "Freigabe-Pfad nicht")

RICHTUNG_OPTIMISTISCH = "zu_optimistisch"
RICHTUNG_VORSICHTIG = "zu_vorsichtig"
RICHTUNG_AUSFALL = "ausfall"
RICHTUNG_BLOCKADE = "blockade"


@dataclass(frozen=True)
class Surprise:
    """Ein Widerspruch — roh, ohne Deutung."""

    art: str          # gebot | job | sperre
    ref: str          # stabile Referenz: action:<id> | jobrun:<id>
    kind: str         # kickbase | manager | <Job-Label> | <Sperr-Art>
    richtung: str     # zu_optimistisch | zu_vorsichtig | ausfall | blockade
    title: str
    detail: str
    at: datetime
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Der Vergleichsschlüssel — GERECHNET, nie gewählt.

        Er entscheidet später, ob ein zweiter Fall „gleichartig" ist (Zwei-Treffer-
        Regel). Dürfte der Urteilende ihn setzen, könnte er ihn passend machen.
        """
        return f"{self.art}|{self.kind}|{self.richtung}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "art": self.art, "ref": self.ref, "kind": self.kind,
            "richtung": self.richtung, "fingerprint": self.fingerprint,
            "title": self.title, "detail": self.detail,
            "at": self.at.isoformat(), "evidence": dict(self.evidence),
        }


def _as_utc(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def surprise_thresholds() -> tuple[float, float]:
    """Gemeinsame Schwellen für Erkennung und gleichartige Vergleichsfälle."""
    from kickbase_agent.knowledge.tuning import tuning_value

    return float(tuning_value("surprise.high_p", 0.60)), float(
        tuning_value("surprise.low_p", 0.20)
    )


# ----------------------------------------------------------------- Erkennung


def _bid_surprises(session: Session, since: datetime, high: float, low: float
                   ) -> list[Surprise]:
    rows = session.execute(
        select(TransferAction).where(
            TransferAction.action_type == "bid",
            TransferAction.status == "executed",
        ).order_by(TransferAction.id)
    ).scalars().all()

    out: list[Surprise] = []
    for row in rows:
        data = dict(row.payload or {})
        exp, outcome = data.get("expectation"), data.get("outcome")
        if not exp or not outcome or exp.get("p") is None or data.get("surprise"):
            continue  # offen, unbewertet oder schon beurteilt
        try:
            stamp = datetime.fromisoformat(str(outcome.get("at")))
        except (TypeError, ValueError):
            stamp = _as_utc(row.created_at)
        stamp = _as_utc(stamp)
        if stamp < since and not data.get("surprise_pending"):
            continue
        if outcome.get("evidence_version") == 2 and not outcome.get("training_eligible"):
            continue
        p = float(exp["p"])
        result = str(outcome.get("result") or "")
        if p >= high and result == "lost":
            richtung = RICHTUNG_OPTIMISTISCH
            title = f"Gebot {row.player_id}: erwartet {p:.0%} Zuschlag, verloren"
        elif p <= low and result == "won":
            richtung = RICHTUNG_VORSICHTIG
            title = f"Gebot {row.player_id}: erwartet nur {p:.0%} Zuschlag, gewonnen"
        else:
            continue
        out.append(Surprise(
            art="gebot",
            ref=f"action:{row.id}",
            kind=str(exp.get("kind") or "?"),
            richtung=richtung,
            title=title,
            detail=f"{(row.amount or 0) / 1e6:.2f} M — {exp.get('why') or ''}",
            at=stamp,
            evidence={"expectation": exp, "outcome": outcome,
                      "player_id": row.player_id, "amount": row.amount,
                      "rule_id": row.rule_id},
        ))
    return out


def _job_surprises(session: Session, since: datetime) -> list[Surprise]:
    """Nur der KIPP-Punkt zählt: ok → failed. Ein Dauerausfall ist bekannt."""
    rows = session.execute(select(JobRun).order_by(JobRun.id)).scalars().all()
    previous: dict[str, str] = {}
    out: list[Surprise] = []
    for row in rows:
        label = str(row.label)
        prior = previous.get(label)
        previous[label] = str(row.status)
        if row.status != "failed":
            continue
        if prior == "failed":
            continue  # kein Dauerfeuer
        stamp = _as_utc(row.started_at)
        if stamp < since:
            continue
        out.append(Surprise(
            art="job",
            ref=f"jobrun:{row.id}",
            kind=label,
            richtung=RICHTUNG_AUSFALL,
            title=f"Job {label} ist ausgefallen",
            detail=(row.error or "")[:200],
            at=stamp,
            evidence={"label": label, "error": row.error, "vorher": prior},
        ))
    return out


def _refusal_kind(rationale: str) -> str:
    """Grobe Sortierung der Sperre — für den Fingerabdruck, nicht als Deutung."""
    text = rationale or ""
    if "Preflight" in text:
        return "preflight"
    if "Zentrale Invariante" in text:
        return "invariante"
    if "Vereinslimit" in text:
        return "vereinslimit"
    return "sonstige"


def _refusal_surprises(session: Session, since: datetime) -> list[Surprise]:
    rows = session.execute(
        select(TransferAction).where(
            TransferAction.status == "refused",
            TransferAction.dry_run.is_(False),
        ).order_by(TransferAction.id)
    ).scalars().all()

    out: list[Surprise] = []
    for row in rows:
        data = dict(row.payload or {})
        if data.get("surprise"):
            continue
        rationale = row.rationale or ""
        if any(marker in rationale for marker in _EXPECTED_REFUSALS):
            continue
        stamp = _as_utc(row.created_at)
        if stamp < since and not data.get("surprise_pending"):
            continue
        out.append(Surprise(
            art="sperre",
            ref=f"action:{row.id}",
            kind=_refusal_kind(rationale),
            richtung=RICHTUNG_BLOCKADE,
            title=f"{row.action_type} auf {row.player_id} wurde verweigert",
            detail=rationale[:220],
            at=stamp,
            evidence={"action_type": row.action_type, "rule_id": row.rule_id,
                      "rationale": rationale, "amount": row.amount},
        ))
    return out


def find_surprises(
    session: Session,
    *,
    now: datetime,
    days: int = DEFAULT_WINDOW_DAYS,
    high_p: float | None = None,
    low_p: float | None = None,
) -> list[Surprise]:
    """Neue Widersprüche im Fenster plus vorgemerkte, noch offene Aktionsfälle."""
    high, low = surprise_thresholds()
    if high_p is not None:
        high = high_p
    if low_p is not None:
        low = low_p
    since = _as_utc(now) - timedelta(days=days)
    found = (
        _bid_surprises(session, since, high, low)
        + _job_surprises(session, since)
        + _refusal_surprises(session, since)
    )
    found.sort(key=lambda s: s.at)
    return found


# ----------------------------------------------------------------- Meldung


def format_line(surprise: Surprise) -> str:
    """Eine Zeile, sachlich, ohne Deutung — die kommt erst nach dem Urteil."""
    return f"🔎 **Überraschung:** {surprise.title}" + (
        f" — {surprise.detail}" if surprise.detail else ""
    )


async def _send_chunks(channel, text: str) -> None:
    """Discord-Limit einschließlich Überschrift einhalten, ohne Fälle abzuschneiden."""
    while text:
        end = len(text)
        if end > 1900:
            end = text.rfind("\n", 0, 1901)
            if end <= 0:
                end = 1900
        await channel.send_info(text[:end])
        text = text[end:].lstrip("\n")


# ----------------------------------------------------------------- Der Job


def _record_pending(
    session: Session, surprise: Surprise, now: datetime, *,
    attempted: bool = False, error: str | None = None,
) -> None:
    """Offene Aktionen über das Suchfenster hinaus am vorhandenen Audit vormerken."""
    if not surprise.ref.startswith("action:"):
        return
    row = session.get(TransferAction, int(surprise.ref.split(":", 1)[1]))
    if row is None:
        return
    data = dict(row.payload or {})
    pending = dict(data.get("surprise_pending") or {})
    pending.setdefault("first_seen_at", _as_utc(now).isoformat())
    if attempted:
        pending["last_attempt_at"] = _as_utc(now).isoformat()
        pending["attempts"] = int(pending.get("attempts", 0)) + 1
        pending.pop("error", None)
    if error:
        pending["error"] = error
    data["surprise_pending"] = pending
    row.payload = data


async def run_surprise_job(
    channel=None,
    *,
    now: datetime | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
    judge=None,
) -> str:
    """Täglicher Lauf. Rückgabe 'quiet' = nichts gefunden (der Normalfall).

    `judge` ist die Urteils-Funktion (agent/judgment.judge_surprise); ohne sie wird
    der Rohfall nur gemeldet. Fehlende Urteile lassen den Lauf fehlschlagen, nachdem
    die anderen Fälle geprüft wurden. Offene Aktionen werden erneut vorgelegt;
    Job-Fälle sind weiterhin auf das Suchfenster begrenzt.
    """
    stamp = now or datetime.now(UTC)
    with SessionLocal() as session:
        found = find_surprises(session, now=stamp, days=days)
        if not found:
            return "quiet"
        lines = [format_line(s) for s in found]
        verdicts: list[str] = []
        failures: list[str] = []
        for surprise in found:
            _record_pending(session, surprise, stamp)
        session.commit()
        if judge is not None:
            for surprise in found:
                _record_pending(session, surprise, stamp, attempted=True)
                session.commit()
                try:
                    verdict = await judge(session, surprise, now=stamp)
                    if verdict is None:
                        raise RuntimeError("Kein Urteil zurückgegeben")
                except Exception as exc:  # noqa: BLE001 — weitere Fälle trotzdem prüfen
                    session.rollback()
                    error = type(exc).__name__
                    _record_pending(session, surprise, stamp, error=error)
                    session.commit()
                    log.warning("Urteil zu %s fehlgeschlagen (%s)", surprise.ref, error)
                    failures.append(f"{surprise.ref}: {error}")
                    continue
                verdicts.append(verdict.as_line())
        session.commit()

    if channel is not None:
        await _send_chunks(channel, "\n".join(lines))
        if verdicts:
            await _send_chunks(
                channel,
                "🧭 **Urteil je Fall** (Vorschlag, nichts wurde geändert):\n"
                + "\n".join(verdicts)
            )
    if failures:
        raise RuntimeError(
            f"Urteil für {len(failures)} von {len(found)} Fällen fehlgeschlagen: "
            + "; ".join(failures[:10])
        )
    return f"surprises:{len(found)}"
