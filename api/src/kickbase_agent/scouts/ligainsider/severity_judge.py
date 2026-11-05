"""Verletzungs-Schwere per TypeSafe — Urteil statt Wortsuche, Wortsuche als Rückfall.

Die Wortsuche in `severity.py` machte aus jedem „riss" einen Langzeit-Ausfall. Ein
Muskelbündelriss (3–8 Wochen) löste damit denselben Sofortverkauf aus wie ein
Kreuzbandriss — genau der Panikverkauf am Tiefpunkt, den die Profi-Regel verbietet.

Hier beurteilt TypeSafe (System One, Modell Jev) jeden LigaInsider-Grund mit drei
Fragen in EINER Anfrage:

* **Dauer** (Score, vier Stufen: Tage · 1–3 Wochen · 3–8 Wochen · Monate)
* **Bestimmt** (Noul): nennt der Grund eine konkrete Verletzung, Krankheit oder
  Sperre — oder nur „Ausfall"?
* **Gesicherte Diagnose** (Noul): nennt der Grund selbst Riss/Bruch/OP — oder nur
  ein verletztes Körperteil („Achillessehnenverletzung")?

Der Code entscheidet, nicht das Modell. LONG (= Sofortverkauf) nur, wenn der Grund
bestimmt ist, das Modell sicher ist, die Monate-Stufe klar vorn liegt UND der Grund
eine gesicherte schwere Diagnose nennt (Emil 29.09.2026: „Hürde höher setzen").
Eine unscharfe Angabe wartet, bis LigaInsider die Diagnose nachträgt. Alles
Zweifelhafte wird UNKNOWN: kein Sofortverkauf; die übrigen Verkaufsregeln (etwa ein
fallender Marktwert in `decision/policies.py`) gelten weiter. Grenze: Ein Grund ohne
genannten Riss, Bruch oder OP („Kreuzbandverletzung", „Reha") wird nie LONG. Fällt
TypeSafe aus oder fehlt der Schlüssel, gilt unverändert die Wortsuche — der Rückfall
ist das Altverhalten.

Betriebsschutz (Review 29.09.2026): harte Obergrenze `TIMEOUT_S` für die ganze Anfrage
(der SDK-Timeout gilt nur je HTTP-Schritt), nach einem Ausfall `FAILURE_PAUSE_S` Pause
ohne Anfrage, und nur endliche Wahrscheinlichkeiten in [0, 1] werden als Urteil
übernommen. Der Kader-Alarm der News liest dasselbe Urteil (`cached_severity`); die
versiegelte Anpfiff-Kaskade (`worker/kickoff_guard.py`) bleibt bewusst bei der Wortsuche.

Die Schwellen sind Startwerte, geprüft an der echten Antwort vom 29.09.2026
(`tests/fixtures/typesafe/severity_response.json`), keine Naturkonstanten.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Noul,
    NoulAnswer,
    RetryPolicy,
    Score,
    ScoreAnswer,
    SystemOneResponse,
)

from kickbase_agent.config import get_settings
from kickbase_agent.intel import jev_journal
from kickbase_agent.intel.jev_journal import JevEntry, fmt_p
from kickbase_agent.scouts.ligainsider.severity import Severity, injury_severity

log = logging.getLogger(__name__)

Ask = Callable[[dict, Mapping[str, Noul | Score]], Awaitable[SystemOneResponse]]
#: Grund (wie übergeben) → (Spieler-ID, Name) der Spieler mit diesem Grund — nur fürs
#: Jev-Protokoll, damit die Jev-Seite zeigt, wen ein Urteil betrifft.
Subjects = Mapping[str, Iterable[tuple[str, str]]]

#: Fest, damit die geprüften Schwellen zum Modell passen (volle Version — die API
#: lehnt „jev-1.13" mit 400 ab). Wird es abgekündigt, scheitert die Anfrage und die
#: Wortsuche greift; dann Schwellen am neuen Modell prüfen, bevor man umstellt.
MODEL = "jev-1.13.0"
#: Engine-Zyklus darf nicht auf TypeSafe warten — lieber Wortsuche als Verzug. Gilt
#: hart für die ganze Anfrage (asyncio.timeout), nicht nur je HTTP-Schritt.
TIMEOUT_S = 10.0
#: Nach einem Ausfall so lange nicht fragen (Wortsuche), statt jeden Planlauf zu bremsen.
FAILURE_PAUSE_S = 600.0

#: LONG löst einen Sofortverkauf aus → hohe Hürde.
LONG_MIN_P_MONTHS = 0.8
#: Kurz genug zum Halten (Stufen „Tage" + „1–3 Wochen").
SHORT_MIN_P = 0.6
#: Darunter hat das Modell keine klare Lesart → beobachten.
MIN_CONFIDENCE = 0.5
#: Darunter nennt der Grund nichts Konkretes („Ausfall") → beobachten.
MIN_P_SPECIFIC = 0.5
#: Zweite Hürde für LONG: gesicherte Diagnose im Grund selbst. Echte Diagnosen
#: lagen am 29.09.2026 bei 0,94–0,97, Körperteil-Angaben bei ≤ 0,14.
LONG_MIN_P_DIAGNOSIS = 0.8

_LEVEL_MONTHS = 3
_LEVELS = [
    "A few days or a single match: a minor knock, a cold or other short illness, "
    "a one-match suspension, rest or load management, or not selected by the coach",
    "About one to three weeks: for example a muscle strain, a bruise, or a stretched "
    "ligament",
    "About three to eight weeks: for example a torn muscle fibre, a torn ankle "
    "ligament, or a small bone fracture in the foot or hand",
    "Several months or the rest of the season: for example a ruptured cruciate "
    "ligament, a ruptured Achilles tendon, a broken leg, or an operation followed by "
    "long rehabilitation",
]
_SOURCE = (
    "Absence labels from LigaInsider, a German Bundesliga injury tracker. Each label "
    "is the German reason a player is currently unavailable."
)
_LI_PREFIX = "LI: "

#: Grund (ohne Präfix) → Modell-Urteil. Nur echte Antworten, nie der Rückfall —
#: sonst bliebe ein Ausfall bis zum Neustart festgeschrieben.
_CACHE: dict[str, Severity] = {}
_CACHE_MAX = 2000
#: Monotone Uhrzeit, bis zu der nach einem Ausfall nicht gefragt wird.
_pause_until = 0.0


def clear_cache() -> None:
    """Cache und Ausfall-Pause zurücksetzen (Tests, Neustart-Verhalten)."""
    global _pause_until
    _CACHE.clear()
    _pause_until = 0.0


def cached_severity(reason: str) -> Severity | None:
    """Das zuletzt gespeicherte TypeSafe-Urteil zu einem Grund, sonst None (synchron).

    Für Stellen, die nicht selbst fragen sollen — etwa den Kader-Alarm der News: Sie
    folgen dem Urteil der Engine, statt mit der Wortsuche etwas anderes zu raten."""
    return _CACHE.get(reason.removeprefix(_LI_PREFIX))


def _is_probability(value: object) -> bool:
    return (
        isinstance(value, int | float) and not isinstance(value, bool)
        and math.isfinite(value) and 0.0 <= value <= 1.0
    )


def _answers_valid(duration: ScoreAnswer, specific: NoulAnswer, diagnosis: NoulAnswer) -> bool:
    """Nur endliche Wahrscheinlichkeiten in [0, 1] (Review 29.09.2026: NaN öffnete die
    Schwellen, p > 1 hätte LONG ergeben können)."""
    probabilities = getattr(duration, "probabilities", None)
    return (
        isinstance(probabilities, Mapping) and bool(probabilities)
        and all(_is_probability(p) for p in probabilities.values())
        and _is_probability(getattr(duration, "confidence", None))
        and _is_probability(getattr(specific, "noul", None))
        and _is_probability(getattr(diagnosis, "noul", None))
    )


def severity_from_answers(
    duration: ScoreAnswer, specific: NoulAnswer, diagnosis: NoulAnswer
) -> Severity:
    """Übersetzt die drei Antworten in die Schwere — die Politik steht hier im Code."""
    if specific.noul < MIN_P_SPECIFIC or duration.confidence < MIN_CONFIDENCE:
        return Severity.UNKNOWN
    p = duration.probabilities
    if p.get(_LEVEL_MONTHS, 0.0) >= LONG_MIN_P_MONTHS:
        if diagnosis.noul >= LONG_MIN_P_DIAGNOSIS:
            return Severity.LONG
        return Severity.UNKNOWN  # vermutlich lang, aber unbestätigt → beobachten
    if p.get(0, 0.0) + p.get(1, 0.0) >= SHORT_MIN_P:
        return Severity.SHORT
    return Severity.UNKNOWN


def _questions(labels: list[str]) -> dict[str, Noul | Score]:
    questions: dict[str, Noul | Score] = {}
    for i in range(len(labels)):
        questions[f"dur_r{i}"] = Score(
            instructions=(
                "How long will a professional footballer whose absence reason is "
                f"`labels.r{i}` typically be unavailable?"
            ),
            criteria=_LEVELS,
        )
        questions[f"spec_r{i}"] = Noul(
            instructions=(
                f"Does `labels.r{i}` name a specific injury, illness, or suspension, "
                "rather than only a generic word such as 'absence' or 'unknown knock'?"
            ),
        )
        questions[f"diag_r{i}"] = Noul(
            instructions=(
                f"Does `labels.r{i}` itself state a confirmed severe diagnosis: a "
                "ruptured or completely torn ligament or tendon, a broken bone, or an "
                "operation? Answer no if it only names an injured body part (for "
                "example 'knee injury' or 'Achilles tendon injury') or a muscle tear."
            ),
        )
    return questions


#: Wofür Jev hier gefragt wird und woher der Grund stammt — nur fürs Jev-Protokoll.
PURPOSE = "ausfall"
_SOURCE_NAME = "LigaInsider"
#: Protokoll-Namen der vier Dauer-Stufen (Reihenfolge wie `_LEVELS`).
_LEVEL_KEYS = ("tage", "wochen_1_3", "wochen_3_8", "monate")


def journal_outcome(severity: Severity | None, purpose: str = PURPOSE) -> tuple[str, str]:
    """Folge und Regeltext EINES Urteils fürs Jev-Protokoll — mit genau den Konstanten,
    nach denen `severity_from_answers` entscheidet. Verkaufen kann die Engine nur eigene
    Spieler: „Sofortverkauf möglich" nur beim Zweck `kader` (Review 01.10.)."""
    if severity is None:
        return jev_journal.OUTCOME_INVALID, (
            "Ungültige Antwort: für diesen Grund gilt die Wortsuche."
        )
    if severity is Severity.LONG:
        rule = (f"Monate ≥ {fmt_p(LONG_MIN_P_MONTHS)} und gesicherte Diagnose ≥ "
                f"{fmt_p(LONG_MIN_P_DIAGNOSIS)}")
        if purpose == "kader":
            return "sofortverkauf_moeglich", (
                f"{rule}: Ein eigener Spieler unter der Verkaufsschwelle darf als Vorhaben mit "
                "Veto-Fenster sofort verkauft werden (nie geschützte Spieler)."
            )
        return "monate_gesichert", f"{rule}: langer Ausfall gesichert."
    if severity is Severity.SHORT:
        return "halten", f"Tage + 1–3 Wochen ≥ {fmt_p(SHORT_MIN_P)}: halten."
    return "beobachten", (
        "Weder klar Monate mit gesicherter Diagnose noch klar kurz: beobachten, kein "
        "Sofortverkauf."
    )


def _journal_rows(
    labels: list[str],
    response: SystemOneResponse,
    *,
    subjects: Mapping[str, list[tuple[str, str]]],
    purpose: str,
    at: datetime,
    batch_id: str,
    latency_ms: int,
    judged: set[str] | None = None,
) -> list[JevEntry]:
    """Eine Protokollzeile je Grund (drei Fragen): Dauer-Stufen, „bestimmt", „Diagnose".

    `judged` (nur bei einer abgebrochenen Teilantwort): die Gründe, deren Urteil wirklich in
    den Cache kam — für alle anderen galt die Wortsuche, ihre Zeile heißt „ungültig"
    (Nach-Review 01.10.: sonst stand ein Urteil im Protokoll, das nie galt)."""
    rows = []
    model = jev_journal.answering_model(response, MODEL)
    for i, label in enumerate(labels):
        keys = (f"dur_r{i}", f"spec_r{i}", f"diag_r{i}")
        scores = getattr(response, "scores", None) or {}
        nouls = getattr(response, "nouls", None) or {}
        duration, specific, diagnosis = scores.get(keys[0]), nouls.get(keys[1]), nouls.get(keys[2])
        valid = None not in (duration, specific, diagnosis) and _answers_valid(
            duration, specific, diagnosis
        )
        if judged is not None and label not in judged:
            valid = False
        severity = severity_from_answers(duration, specific, diagnosis) if valid else None
        levels, confidence, _ = jev_journal.answer_fields(duration)
        probabilities = {
            name: levels[str(level)]
            for level, name in enumerate(_LEVEL_KEYS) if levels and str(level) in levels
        }
        for name, given in (("bestimmt", specific), ("diagnose", diagnosis)):
            value = getattr(given, "noul", None)
            if _is_probability(value):
                probabilities[name] = float(value)
        top = max((name for name in _LEVEL_KEYS if name in probabilities),
                  key=probabilities.__getitem__, default=None)
        raw = {}
        for key, given in zip(keys, (duration, specific, diagnosis), strict=True):
            fields = jev_journal.answer_fields(given)[2]
            if fields:
                raw[key] = fields
        players = subjects.get(label, [])
        outcome, rule = journal_outcome(severity, purpose)
        if judged is not None and label not in judged:
            rule = "Teilantwort abgebrochen: für diesen Grund gilt die Wortsuche."
        rows.append(JevEntry(
            created_at=at, kind=jev_journal.KIND_SEVERITY, purpose=purpose,
            status=jev_journal.STATUS_OK if valid else jev_journal.STATUS_INVALID,
            model=model, batch_id=batch_id, batch_size=len(labels), latency_ms=latency_ms,
            player_id=players[0][0] if len(players) == 1 else None,
            player_name=", ".join(name for _, name in players) or None,
            subject=label, source=_SOURCE_NAME, answer=top,
            probability=probabilities.get(top) if top else None,
            probabilities=probabilities or None, confidence=confidence, raw=raw or None,
            rule=rule, outcome=outcome,
        ))
    return rows


def _subjects_by_label(subjects: Subjects | None) -> dict[str, list[tuple[str, str]]]:
    by_label: dict[str, list[tuple[str, str]]] = {}
    for reason, players in (subjects or {}).items():
        bucket = by_label.setdefault(reason.removeprefix(_LI_PREFIX), [])
        bucket.extend(p for p in players if p not in bucket)
    return by_label


def _default_ask() -> Ask | None:
    key = get_settings().typesafe_api_key
    if not key:
        return None

    async def ask(state: dict, questions: Mapping[str, Noul | Score]) -> SystemOneResponse:
        async with AsyncTypeSafeClient(
            api_key=key, model=MODEL, timeout=TIMEOUT_S,
            retry=RetryPolicy(max_retries=1, timeout=TIMEOUT_S),
        ) as client:
            return await client.system_one(state, questions)

    return ask


async def judge_severities(
    reasons: Iterable[str], *, ask: Ask | None = None, subjects: Subjects | None = None,
    purpose: str = PURPOSE,
) -> dict[str, Severity]:
    """Schwere je Grund (Schlüssel = Grund wie übergeben, auch mit „LI: "-Präfix).

    `subjects` nennt je Grund die betroffenen Spieler und `purpose` den Aufrufer (`kader`
    für eigene Spieler, `kaufpruefung` für den Markt) — beides nur fürs Jev-Protokoll."""
    global _pause_until
    wanted = list(dict.fromkeys(reasons))
    labels = {r: r.removeprefix(_LI_PREFIX) for r in wanted}
    open_labels = list(dict.fromkeys(lab for lab in labels.values() if lab not in _CACHE))

    if open_labels and time.monotonic() >= _pause_until:
        ask = ask or _default_ask()
        if ask is not None:
            batch_id, started = jev_journal.new_batch_id(), time.monotonic()
            response: SystemOneResponse | None = None
            try:
                async with asyncio.timeout(TIMEOUT_S):
                    response = await ask(
                        {"source": _SOURCE,
                         "labels": {f"r{i}": lab for i, lab in enumerate(open_labels)}},
                        _questions(open_labels),
                    )
                latency_ms = jev_journal.elapsed_ms(started)
                if len(_CACHE) > _CACHE_MAX:
                    _CACHE.clear()
                for i, lab in enumerate(open_labels):
                    answers = (
                        response.scores[f"dur_r{i}"],
                        response.nouls[f"spec_r{i}"],
                        response.nouls[f"diag_r{i}"],
                    )
                    if _answers_valid(*answers):  # sonst Wortsuche für diesen Grund
                        _CACHE[lab] = severity_from_answers(*answers)
                jev_journal.record(lambda: _journal_rows(
                    open_labels, response, subjects=_subjects_by_label(subjects),
                    purpose=purpose, at=datetime.now(UTC), batch_id=batch_id,
                    latency_ms=latency_ms,
                ))
            except Exception as exc:  # fail-safe: Wortsuche wie vor TypeSafe
                _pause_until = time.monotonic() + FAILURE_PAUSE_S
                log.warning("TypeSafe-Schwere nicht verfügbar (%s) — Wortsuche greift, "
                            "Pause %d s", type(exc).__name__, int(FAILURE_PAUSE_S))
                elapsed = jev_journal.elapsed_ms(started)
                if response is not None:  # Teilantwort: schon beurteilte Gründe zeigen
                    judged = {lab for lab in open_labels if lab in _CACHE}
                    jev_journal.record(lambda answered=response, judged=judged: _journal_rows(
                        open_labels, answered, subjects=_subjects_by_label(subjects),
                        purpose=purpose, at=datetime.now(UTC), batch_id=batch_id,
                        latency_ms=elapsed, judged=judged,
                    ))
                jev_journal.record(lambda error=exc: [jev_journal.failure_entry(
                    kind=jev_journal.KIND_SEVERITY, purpose=purpose,
                    batch_size=len(open_labels), latency_ms=elapsed, error=error,
                    at=datetime.now(UTC), batch_id=batch_id, model=MODEL,
                    pause_s=FAILURE_PAUSE_S,
                )])

    return {r: _CACHE.get(lab) or injury_severity(lab) for r, lab in labels.items()}
