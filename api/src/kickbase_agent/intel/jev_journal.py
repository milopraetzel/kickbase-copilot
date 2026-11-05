"""Jev-Protokoll: jede gestellte Jev-Frage mit Antwort, Regel und Folge.

Emil 01.10.2026: „Könntest du ein Dashboard bauen was mir mehr zeigt Jev arbeitet."
Bis hierhin lebten Jevs Antworten nur in den Caches der Judges (Verletzungs-Schwere,
Rolle, Rückkehr) und waren nach jedem Neustart verloren. Hier schreibt jeder Judge nach
einer Anfrage eine Zeile je gestellter Frage — was Jev las, alle Wahrscheinlichkeiten,
die Rohantwort, die angewandte Regel und ihre Folge — und eine Zeile je gescheiterter
Anfrage. Die Jev-Seite im Cockpit und die Treffer-Bilanz lesen nur diese Tabelle.

Das Protokoll entscheidet nichts. Die Folge leitet der Judge mit genau den Konstanten
ab, mit denen er auch selbst entscheidet; gespeichert wird zusätzlich der Regeltext,
damit eine spätere Schwellenänderung alte Zeilen nicht umdeutet.

Betriebsschutz: `record` schluckt jeden Fehler (DB gesperrt, kaputte Antwort beim
Aufbau der Zeilen) und loggt nur — ein Protokollfehler darf nie ein Urteil, einen Plan
oder einen Job kippen. Geschrieben wird synchron in einer eigenen kurzen Session, wie
andere Berichte der Engine auch. Cache-Treffer erzeugen keine Zeile: protokolliert wird,
was Jev wirklich gefragt wurde. Aufbewahrung `RETENTION_DAYS` (Aufräumen 03:33).
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kickbase_agent.config import get_settings
from kickbase_agent.db.models import JevJudgment
from kickbase_agent.db.session import SessionLocal

log = logging.getLogger(__name__)

KIND_SEVERITY = "schwere"
KIND_ROLE = "rolle"
KIND_RETURN = "rueckkehr"

STATUS_OK = "ok"
STATUS_INVALID = "ungueltig"
STATUS_FAILED = "ausfall"

OUTCOME_INVALID = "ungueltig"
OUTCOME_FAILED = "ausfall"
#: Ein jüngerer eindeutiger Artikel zum selben Spieler entschied in derselben Anfrage.
OUTCOME_SUPERSEDED = "ueberholt"
RULE_SUPERSEDED = "Ein jüngerer eindeutiger Artikel zum selben Spieler entscheidet."

RETENTION_DAYS = 90
#: So lange wartet ein Protokoll-Commit höchstens auf einen fremden Schreiber (SQLite).
#: Die Engine-Session wartet 30 s (`db/session.py`); hält der Snapshot um 22:05 die Sperre,
#: stünde der Event-Loop so lange (Review 01.10.) — eine verlorene Zeile ist billiger.
JOURNAL_BUSY_TIMEOUT_S = 2
SUBJECT_MAX = 500
URL_MAX = 1024
NAME_MAX = 200
CLUB_MAX = 100
_SHORT_MAX = {"source": 32, "answer": 32, "outcome": 32, "error": 64, "model": 32}

#: Rohantwort-Felder der typisierten Jev-Antworten (Choice, Score, Noul).
_ANSWER_FIELDS = ("type", "choice", "score", "noul", "confidence", "probabilities")


@dataclass(frozen=True)
class JevEntry:
    """Eine Protokollzeile, wie ein Judge sie nach einer Anfrage baut."""

    created_at: datetime
    kind: str
    purpose: str
    status: str
    model: str
    batch_id: str
    batch_size: int
    latency_ms: int | None
    player_id: str | None = None
    player_name: str | None = None
    club: str | None = None
    subject: str | None = None
    text: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    source: str | None = None
    answer: str | None = None
    probability: float | None = None
    probabilities: dict[str, float] | None = None
    confidence: float | None = None
    raw: dict[str, Any] | None = None
    rule: str = ""
    outcome: str = ""
    error: str | None = None


def new_batch_id() -> str:
    """Kurze ID, die alle Fragen einer Anfrage verbindet."""
    return uuid.uuid4().hex[:12]


def elapsed_ms(started: float) -> int:
    """Millisekunden seit `started` (time.monotonic())."""
    return max(0, round((time.monotonic() - started) * 1000))


def fmt_p(value: float) -> str:
    """Schwelle deutsch: 0.95 → „0,95"."""
    return f"{value:.2f}".replace(".", ",")


def _is_probability(value: object) -> bool:
    return (
        isinstance(value, int | float) and not isinstance(value, bool)
        and math.isfinite(value) and 0.0 <= value <= 1.0
    )


def _json_safe(value: object) -> object:
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    return str(value)


def answer_fields(
    answer: object,
) -> tuple[dict[str, float] | None, float | None, dict[str, Any] | None]:
    """(gültige Wahrscheinlichkeiten, Konfidenz, Rohantwort) einer typisierten Antwort.

    Die Wahrscheinlichkeiten enthalten nur endliche Zahlen in [0, 1] (Schlüssel als Text,
    auch die Stufen einer Score-Antwort); die Rohantwort zeigt alles, was Jev lieferte,
    nicht endliche Zahlen als Text — damit ein kaputtes Urteil sichtbar bleibt."""
    if answer is None:
        return None, None, None
    raw: dict[str, Any] = {}
    for name in _ANSWER_FIELDS:
        value = getattr(answer, name, None)
        if value is not None:
            raw[name] = _json_safe(value)
    probabilities = getattr(answer, "probabilities", None)
    clean = (
        {str(k): float(v) for k, v in probabilities.items() if _is_probability(v)}
        if isinstance(probabilities, Mapping) else None
    )
    confidence = getattr(answer, "confidence", None)
    return (clean or None), (float(confidence) if _is_probability(confidence) else None), (
        raw or None
    )


def failure_entry(
    *,
    kind: str,
    purpose: str,
    batch_size: int,
    latency_ms: int | None,
    error: BaseException,
    at: datetime,
    batch_id: str,
    model: str = "jev-1.13.0",
    pause_s: float | None = 600.0,
) -> JevEntry:
    """Zeile für eine gescheiterte Anfrage: kein Urteil, das Verhalten von vorher gilt.
    `pause_s=None` für Aufrufer ohne Ausfall-Pause (News-Radar, Review 01.10.)."""
    pause = (f"danach {round(pause_s / 60)} Minuten Pause." if pause_s
             else "ohne Pause; der nächste Lauf fragt erneut.")
    return JevEntry(
        created_at=at, kind=kind, purpose=purpose, status=STATUS_FAILED, model=model,
        batch_id=batch_id, batch_size=batch_size, latency_ms=latency_ms,
        rule=f"Ohne Urteil gilt das Verhalten von vorher; {pause}",
        outcome=OUTCOME_FAILED, error=type(error).__name__,
    )


def answering_model(response: object, default: str) -> str:
    """Das Modell, das wirklich antwortete (laut Antwort), sonst das bestellte."""
    model = getattr(response, "model", None)
    return model if isinstance(model, str) and model else default


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


def _cut(value: str | None, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else value


_FACTORIES: dict[str, Callable[[], Session]] = {}


def _journal_sessions() -> Callable[[], Session]:
    """Sessions in die konfigurierte Datenbank mit kurzer Wartezeit auf Schreibsperren (je
    URL einmal gebaut). Andere Datenbanken nutzen die Engine-Session."""
    url = get_settings().database_url
    if not url.startswith("sqlite") or ":memory:" in url:
        return SessionLocal
    if url not in _FACTORIES:
        engine = create_engine(url, future=True,
                               connect_args={"timeout": JOURNAL_BUSY_TIMEOUT_S})
        _FACTORIES[url] = sessionmaker(bind=engine, autoflush=False, future=True)
    return _FACTORIES[url]


def write_entries(
    entries: Iterable[JevEntry],
    *,
    session_factory: Callable[[], Session] | None = None,
) -> int:
    """Schreibt die Zeilen in einer eigenen Session (UTC ohne Zeitzone, wie SQLite es
    speichert); Rückgabe: Anzahl. Fehler fliegen — `record` fängt sie."""
    session_factory = session_factory or _journal_sessions()
    rows = []
    for entry in entries:
        data = asdict(entry)
        data["created_at"] = _utc_naive(entry.created_at)
        data["published_at"] = _utc_naive(entry.published_at)
        data["subject"] = _cut(entry.subject, SUBJECT_MAX)
        data["url"] = _cut(entry.url, URL_MAX)
        data["player_name"] = _cut(entry.player_name, NAME_MAX)
        data["club"] = _cut(entry.club, CLUB_MAX)
        for name, limit in _SHORT_MAX.items():
            data[name] = _cut(data[name], limit)
        rows.append(JevJudgment(**data))
    if not rows:
        return 0
    with session_factory() as session:
        session.add_all(rows)
        session.commit()
    return len(rows)


#: Schreibweg des Protokolls; Tests ersetzen ihn (tests/conftest.py), damit nie eine
#: Test-Anfrage in eine echte Datenbank schreibt.
_writer: Callable[[list[JevEntry]], object] = write_entries


def record(build: Callable[[], Iterable[JevEntry]]) -> None:
    """Baut die Zeilen (`build`) und schreibt sie — beides im Fail-safe.

    Der Aufbau steckt bewusst mit im Schutz: Er liest die Antwort ein zweites Mal, und
    eine unerwartete Form darf das bereits gefallene Urteil nicht nachträglich kippen."""
    try:
        rows = list(build())
        if rows:
            _writer(rows)
    except Exception as exc:  # noqa: BLE001 — Protokoll ist nie einen Urteilsabbruch wert
        log.warning("Jev-Protokoll nicht geschrieben (%s) — das Urteil gilt trotzdem",
                    type(exc).__name__)


def purge_old_judgments(
    now: datetime | None = None,
    *,
    days: int = RETENTION_DAYS,
    session: Session | None = None,
) -> int:
    """Zeilen älter als `days` löschen — Rückgabe: gelöschte Zeilen; Fehler nur geloggt."""
    cutoff = _utc_naive(now or datetime.now(UTC)) - timedelta(days=days)
    try:
        own = session is None
        s = SessionLocal() if own else session
        try:
            deleted = (
                s.query(JevJudgment)
                .filter(JevJudgment.created_at < cutoff)
                .delete(synchronize_session=False)
            )
            s.commit()
            return int(deleted)
        finally:
            if own:
                s.close()
    except Exception:  # noqa: BLE001 — Aufräumen ist nie einen Jobabbruch wert
        log.warning("Jev-Protokoll-Aufräumen übersprungen.", exc_info=True)
        return 0
