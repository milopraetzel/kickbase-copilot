"""Daten der Jev-Seite (`jev.html`, Spec 2026-10-01-jev-dashboard-design.md).

Rein lesend beim Cockpit-Build: das Jev-Protokoll (`jev_judgments`), für die
Treffer-Bilanz zusätzlich Minuten je Spieltag (`player_performance`) und Tagesmarktwerte
(`player_history`). Kickbase oder TypeSafe werden hier nie gefragt.

Status: Der Build läuft im Worker oder (Aktualisieren-Knopf) in der API — die Ausfall-Pause
der Judges lebt aber nur im Worker-Speicher. Deshalb leitet die Seite die Pause aus dem
Protokoll ab (letzte gescheiterte Anfrage + `FAILURE_PAUSE_S`, ohne gelungene danach).
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from kickbase_agent.config import get_settings
from kickbase_agent.db.models import JevJudgment, PlayerHistory, PlayerPerformance
from kickbase_agent.intel import jev_outcomes, news_signal, role_judge
from kickbase_agent.intel.jev_journal import (
    KIND_RETURN,
    KIND_ROLE,
    KIND_SEVERITY,
    STATUS_FAILED,
    STATUS_OK,
)
from kickbase_agent.intel.jev_outcomes import Match, Verdict
from kickbase_agent.scouts.ligainsider import severity_judge

log = logging.getLogger(__name__)

#: Je Frage die jüngsten Zeilen — eine große Rollen-Anfrage drängt Rückkehr und Verletzung
#: sonst aus dem Protokoll, und der Filter „Rückkehr" bliebe leer.
FEED_PER_KIND = 25
FAILURES_IN_FEED = 10
#: Ohne die Folgen der letzten 7 Tage (die kommen immer dazu).
FEED_LIMIT = 3 * FEED_PER_KIND + FAILURES_IN_FEED
TEXT_MAX = 600
CALIBRATION_WINDOW = timedelta(days=30)
COUNT_WINDOW = timedelta(days=7)
BILANZ_WINDOW = timedelta(days=60)
#: Folgen, die etwas bewirken (Filter „nur mit Folge", Zahl „Folgen").
EFFECTS = frozenset({"sperrt", "warnung", "fehlt_noch", "kaufhinweis", "sofortverkauf_moeglich"})
#: Rollen-Antworten, die die Bilanz an Minuten misst.
_MEASURED_ROLES = frozenset({"fehlt", "startet", "bank"})
#: Eindeutige Urteile je Frage (≥ Schwelle, entschieden) — wie die Judges aggregieren.
_DECISIVE = {
    KIND_ROLE: frozenset({"sperrt", "warnung", "fehlt_noch", "eindeutig"}),
    KIND_RETURN: frozenset({"kaufhinweis", "hebt_auf"}),
}
#: Folgen, die ein jüngeres Urteil vor demselben Spiel umkehren kann, und die Umkehrer:
#: ein klares „startet"/„bank" hebt ein „fehlt" auf, ein Ausfall die Rückkehr.
_LIFTABLE = {KIND_ROLE: frozenset({"sperrt", "warnung", "fehlt_noch"}),
             KIND_RETURN: frozenset({"kaufhinweis"})}
_LIFTING = {KIND_ROLE: frozenset({"eindeutig"}), KIND_RETURN: frozenset({"hebt_auf"})}
#: Diese Rollen-Aufrufer fragen ohne Ausfall-Pause (News-Radar, `pause_on_failure=False`).
_NO_PAUSE_PURPOSES = frozenset({"kader", "naechstes_spiel", "handel"})
_PAUSE_S = {
    KIND_SEVERITY: severity_judge.FAILURE_PAUSE_S,
    KIND_ROLE: role_judge.FAILURE_PAUSE_S,
    KIND_RETURN: news_signal.FAILURE_PAUSE_S,
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    stamp = _utc(value)
    return stamp.isoformat() if stamp else None


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def thresholds() -> dict[str, dict[str, float]]:
    """Die Schwellen der Judges — für Schwellenstriche und Texte der Seite."""
    return {
        KIND_ROLE: {"fehlt": role_judge.MIN_P_ROLE, "startet": role_judge.MIN_P_ROLE,
                    "bank": role_judge.MIN_P_ROLE},
        KIND_RETURN: {"rueckkehr": news_signal.MIN_P_RETURN, "ausfall": news_signal.MIN_P_CANCEL},
        KIND_SEVERITY: {"monate": severity_judge.LONG_MIN_P_MONTHS,
                        "diagnose": severity_judge.LONG_MIN_P_DIAGNOSIS,
                        "bestimmt": severity_judge.MIN_P_SPECIFIC},
    }


def _entry(row: JevJudgment, now: datetime) -> dict[str, Any]:
    text = row.text[:TEXT_MAX] if isinstance(row.text, str) else None
    # Filter „Folgen · 7 Tage" und Zahl „mit Folge" zählen dasselbe Fenster (Nach-Review).
    wirkung = row.outcome in EFFECTS and now - _utc(row.created_at) <= COUNT_WINDOW
    return {
        "id": row.id, "at": _iso(row.created_at), "kind": row.kind, "purpose": row.purpose,
        "status": row.status, "model": row.model, "batch_id": row.batch_id,
        "batch_size": row.batch_size, "latency_ms": row.latency_ms,
        "player": row.player_name or row.player_id, "player_id": row.player_id,
        "club": row.club, "subject": row.subject, "text": text, "url": row.url,
        "published_at": _iso(row.published_at), "source": row.source, "answer": row.answer,
        "p": row.probability, "probabilities": row.probabilities or {},
        "confidence": row.confidence, "raw": row.raw, "rule": row.rule,
        "outcome": row.outcome, "error": row.error, "aufgehoben": False,
        "wirkung": wirkung, "wiederholt": 1,
    }


def _pauses(rows: list[JevJudgment], now: datetime) -> dict[str, str]:
    """Je Frage „Pause bis …": Die letzte pausierende Anfrage scheiterte, seitdem kam keine
    gültige Antwort derselben Frage, und die Pause der Judges läuft noch. Ausfälle des
    News-Radars (ohne Pause gefragt) pausieren nichts."""
    out: dict[str, str] = {}
    for kind, pause_s in _PAUSE_S.items():
        mine = [r for r in rows if r.kind == kind]
        oks = [_utc(r.created_at) for r in mine if r.status == STATUS_OK]
        # Ohne Pause fragt nur der News-Radar die ROLLE (Nach-Review: die Schwere mit Zweck
        # „kader" kommt aus dem Plan und pausiert).
        fails = [_utc(r.created_at) for r in mine if r.status == STATUS_FAILED
                 and not (kind == KIND_ROLE and r.purpose in _NO_PAUSE_PURPOSES)]
        last_ok, last_fail = max(oks, default=None), max(fails, default=None)
        if last_fail is None or (last_ok is not None and last_ok > last_fail):
            continue
        until = last_fail + timedelta(seconds=pause_s)
        if until > now:
            out[kind] = until.isoformat()
    return out


def _status(rows: list[JevJudgment], now: datetime, key_set: bool) -> dict[str, Any]:
    oks = [_utc(r.created_at) for r in rows if r.status == STATUS_OK]
    fails = [_utc(r.created_at) for r in rows if r.status == STATUS_FAILED]
    day = [r for r in rows if r.status != STATUS_FAILED
           and now - _utc(r.created_at) <= timedelta(hours=24)]
    last_ok, last_fail = max(oks, default=None), max(fails, default=None)
    pausen = _pauses(rows, now)
    if not key_set:
        state = "kein_schluessel"
    elif not rows:
        state = "noch_nichts"
    elif pausen:
        state = "pause"
    elif day and not any(r.status == STATUS_OK for r in day):
        state = "ungueltig"  # Jev antwortet, aber keine Antwort war brauchbar
    else:
        state = "ok"
    return {
        "state": state, "last_ok_at": _iso(last_ok), "last_failure_at": _iso(last_fail),
        "pausen": pausen, "pause_until": max(pausen.values(), default=None),
        "failures_24h": sum(1 for f in fails if now - f <= timedelta(hours=24)),
    }


def _counts(rows: list[JevJudgment], now: datetime) -> dict[str, Any]:
    recent = [r for r in rows if now - _utc(r.created_at) <= COUNT_WINDOW]
    day = [r for r in recent if now - _utc(r.created_at) <= timedelta(hours=24)]
    asked = [r for r in recent if r.status != STATUS_FAILED]
    batches: dict[str, int] = {}
    for r in asked:
        if r.latency_ms is not None:
            batches.setdefault(r.batch_id, r.latency_ms)
    latencies = sorted(batches.values())
    return {
        "urteile_7d": len(asked),
        "anfragen_7d": len({r.batch_id for r in asked}),
        "folgen_7d": sum(1 for r in asked if r.outcome in EFFECTS),
        "ausfaelle_7d": sum(1 for r in recent if r.status == STATUS_FAILED),
        "ungueltig_7d": sum(1 for r in asked if r.status != STATUS_OK),
        "urteile_24h": sum(1 for r in day if r.status != STATUS_FAILED),
        "folgen_24h": sum(1 for r in day if r.outcome in EFFECTS),
        "median_ms": round(statistics.median(latencies)) if latencies else None,
        "p90_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.9))]
        if latencies else None,
    }


def _kinds(rows: list[JevJudgment], now: datetime) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for kind in (KIND_SEVERITY, KIND_ROLE, KIND_RETURN):
        mine = [r for r in rows if r.kind == kind and r.status != STATUS_FAILED]
        recent = [r for r in mine if now - _utc(r.created_at) <= COUNT_WINDOW]
        out[kind] = {
            "urteile_7d": len(recent),
            "folgen_7d": sum(1 for r in recent if r.outcome in EFFECTS),
            "letzte": _iso(max((_utc(r.created_at) for r in mine), default=None)),
        }
    return out


_RING_THRESHOLD = {
    KIND_ROLE: lambda: role_judge.MIN_P_ROLE,
    KIND_RETURN: lambda: news_signal.MIN_P_RETURN,
    KIND_SEVERITY: lambda: severity_judge.LONG_MIN_P_MONTHS,
}


def _calibration(rows: list[JevJudgment], now: datetime) -> dict[str, list[dict[str, Any]]]:
    """Je Frage die Wahrscheinlichkeit des entscheidenden Labels jeder gültigen Antwort."""
    label = {KIND_ROLE: "fehlt", KIND_RETURN: "rueckkehr", KIND_SEVERITY: "monate"}
    hit = {KIND_ROLE: {"sperrt", "warnung", "fehlt_noch"}, KIND_RETURN: {"kaufhinweis"},
           KIND_SEVERITY: {"sofortverkauf_moeglich"}}
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in label}
    for r in rows:
        if r.status != STATUS_OK or now - _utc(r.created_at) > CALIBRATION_WINDOW:
            continue
        p = (r.probabilities or {}).get(label.get(r.kind, ""))
        if r.kind not in out or not isinstance(p, int | float):
            continue
        is_hit = r.outcome in hit[r.kind]
        # Ring: über der Schwelle, aber ohne Folge (überholt, nicht gesichert oder kein
        # eigener Spieler) — Nach-Review: vorher nur bei der Schwere und falsch beschriftet.
        ring = not is_hit and p >= _RING_THRESHOLD[r.kind]()
        out[r.kind].append({"p": float(p), "hit": is_hit, "ring": ring})
    return out


def _verdict(row: JevJudgment) -> Verdict:
    return Verdict(
        id=row.id, kind=row.kind, outcome=row.outcome, answer=row.answer,
        probability=row.probability, player_id=row.player_id,
        player_name=row.player_name, subject=row.subject,
        published_at=_utc(row.published_at), created_at=_utc(row.created_at),
    )


def _unique(rows: Iterable[JevJudgment]) -> list[JevJudgment]:
    """Dasselbe Paar nach einem Neustart erneut gefragt: nur die jüngste Zeile zählt."""
    seen: dict[tuple, JevJudgment] = {}
    for r in sorted(rows, key=lambda r: (_utc(r.created_at), r.id)):
        seen[(r.kind, r.player_id, r.subject, _iso(r.published_at))] = r
    return sorted(seen.values(), key=lambda r: (_utc(r.created_at), r.id), reverse=True)


def _matches(session: Session, pids: set[str], since: datetime) -> dict[str, list[Match]]:
    out: dict[str, list[Match]] = defaultdict(list)
    if not pids:
        return out
    rows = session.execute(
        select(PlayerPerformance.player_id, PlayerPerformance.match_date,
               PlayerPerformance.minutes, PlayerPerformance.minutes_observed,
               PlayerPerformance.captured_at)
        .where(PlayerPerformance.player_id.in_(pids),
               PlayerPerformance.match_date >= _naive(since))
    ).all()
    # NULL = Altzeile vor der Feldpräsenz: zählt wie im Plan (L-111). Nicht eingesetzte
    # Spieler stehen mit beobachteten 0 Minuten da (Box 01.10.2026: alle 2.333 seit 25.09.
    # erfassten 0-Minuten-Zeilen mit minutes_observed=1).
    for pid, when, minutes, observed, captured in rows:
        if when is not None:
            out[pid].append(Match(_utc(when), int(minutes or 0), observed is not False,
                                  _utc(captured)))
    return out


def _values(session: Session, pids: set[str], since: datetime) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    if not pids:
        return out
    rows = session.execute(
        select(PlayerHistory.player_id, PlayerHistory.recorded_at, PlayerHistory.market_value)
        .where(PlayerHistory.player_id.in_(pids),
               PlayerHistory.recorded_at >= _naive(since - timedelta(days=3)))
    ).all()
    for pid, when, value in rows:
        out[pid].append((_utc(when).date(), int(value)))
    return out


def _per_match(rows: list[JevJudgment],
               matches: dict[str, list[Match]]) -> tuple[set[int], set[int]]:
    """(wirksam, später aufgehoben): Je Spieler und nächstem Spiel zählt das jüngste
    eindeutige Urteil vor dem Anpfiff — genau die Aggregation der Judges (Nach-Review 01.10.:
    vorher hob ein Artikel über ein SPÄTERES Spiel einen Treffer auf, und zwei „fehlt" vor
    demselben Spiel zählten doppelt). Ältere Folgen derselben Gruppe, die das wirksame
    Urteil umkehrt, sind „später aufgehoben"."""
    groups: dict[tuple, list[JevJudgment]] = defaultdict(list)
    for r in _unique(rows):  # erst entdoppeln: ein erneut gefragtes Paar zählt einmal
        if (r.status != STATUS_OK or not r.player_id
                or r.outcome not in _DECISIVE.get(r.kind, ())):
            continue
        kickoff = jev_outcomes.next_kickoff(matches.get(r.player_id, []), _when(r))
        groups[(r.kind, r.player_id, kickoff)].append(r)
    effective: set[int] = set()
    lifted: set[int] = set()
    for (kind, _, _), group in groups.items():
        group.sort(key=lambda r: (_when(r), _utc(r.created_at), r.id))
        newest = group[-1]
        effective.add(newest.id)
        if newest.outcome in _LIFTING[kind]:
            lifted.update(r.id for r in group[:-1] if r.outcome in _LIFTABLE[kind])
    return effective, lifted


def _bilanz(session: Session, rows: list[JevJudgment], now: datetime,
            matches: dict[str, list[Match]], effective: set[int]) -> dict[str, Any]:
    """Nur wirksame Urteile: je Spieler und Spiel das jüngste eindeutige (`_per_match`)."""
    usable = [r for r in rows if r.id in effective
              and now - _utc(r.created_at) <= BILANZ_WINDOW]
    usable.sort(key=lambda r: (_utc(r.created_at), r.id), reverse=True)
    roles = [r for r in usable if r.kind == KIND_ROLE and r.answer in _MEASURED_ROLES]
    returns = [r for r in usable if r.kind == KIND_RETURN and r.outcome == "kaufhinweis"]
    since = min((_when(r) for r in returns), default=now)
    values = _values(session, {r.player_id for r in returns}, since)
    role_checks = [jev_outcomes.check_role(_verdict(r), matches.get(r.player_id, []), now)
                   for r in roles]
    return_checks = [
        jev_outcomes.check_return(_verdict(r), matches.get(r.player_id, []),
                                  values.get(r.player_id, []), now)
        for r in returns
    ]
    return {
        KIND_ROLE: {"checks": role_checks, "summe": jev_outcomes.summarize(role_checks)},
        KIND_RETURN: {"checks": return_checks, "summe": jev_outcomes.summarize(return_checks)},
    }


def _newest(session: Session, now: datetime) -> list[JevJudgment]:
    """Die jüngsten Zeilen je Frage, die jüngsten Ausfälle und JEDE Folge der letzten 7 Tage
    (Review 01.10.: sonst fiel ein „sperrt" aus einer großen Anfrage heraus, und Zahl und
    Filter „mit Folge" liefen auseinander) — zusammen neueste zuerst."""
    order = (JevJudgment.created_at.desc(), JevJudgment.id.desc())
    picked: list[JevJudgment] = list(session.scalars(
        select(JevJudgment).where(JevJudgment.outcome.in_(EFFECTS),
                                  JevJudgment.created_at >= _naive(now - COUNT_WINDOW))
    ))
    for kind in (KIND_ROLE, KIND_RETURN, KIND_SEVERITY):
        picked += session.scalars(
            select(JevJudgment)
            .where(JevJudgment.kind == kind, JevJudgment.status != STATUS_FAILED)
            .order_by(*order).limit(FEED_PER_KIND)
        )
    picked += session.scalars(
        select(JevJudgment).where(JevJudgment.status == STATUS_FAILED)
        .order_by(*order).limit(FAILURES_IN_FEED)
    )
    unique = {r.id: r for r in picked}
    return sorted(unique.values(), key=lambda r: (_utc(r.created_at), r.id), reverse=True)


def _collapse(rows: list[JevJudgment]) -> list[tuple[JevJudgment, int]]:
    """Dieselbe Frage mit derselben Antwort erneut gestellt — jeder neue Prozess (Deploy,
    manueller Build, API-Aktualisieren) fragt mit leerem Cache (live 01.10.) — wird EIN
    Eintrag mit Anzahl; die neueste Zeile steht für alle. `rows` neueste zuerst."""
    groups: dict[tuple, list] = {}
    for r in rows:
        key = (r.kind, r.purpose, r.status, r.player_id, r.subject, _iso(r.published_at),
               r.answer, r.outcome, r.error)
        if key in groups:
            groups[key][1] += 1
        else:
            groups[key] = [r, 1]
    return [(row, count) for row, count in groups.values()]


def _when(r: JevJudgment) -> datetime:
    """Wann das Urteil galt: Veröffentlichung des Artikels, sonst die Fragezeit."""
    return _utc(r.published_at or r.created_at)


def _by_player(
    session: Session, rows: list[JevJudgment], lifted: set[int], now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Jüngste Jev-Urteile je Spieler (`player_id` sowie `name:<klein>` bei fehlender ID).

    Pro Spieler und Frageart (`rolle`, `rueckkehr`, `schwere`) werden das jeweils neueste
    gültige Urteil sowie das neueste wirksame Folge-Urteil der letzten 7 Tage bereitgestellt,
    damit das Spieler-Dossier im Haupt-Cockpit sofort die aktuelle Jev-Einschätzung zeigt.
    """
    ordered = sorted(
        (r for r in rows if r.status == STATUS_OK),
        key=lambda r: (_utc(r.created_at), r.id),
        reverse=True,
    )
    picked_keys: dict[str, list[int]] = defaultdict(list)
    seen_slots: set[tuple[str, str, bool]] = set()
    needed_ids: set[int] = set()
    for r in ordered:
        targets: list[str] = []
        if r.player_id:
            targets.append(str(r.player_id))
        if r.player_name:
            for part in str(r.player_name).split(","):
                clean = part.strip().lower()
                if clean:
                    targets.append(f"name:{clean}")
        is_effect = (
            r.outcome in EFFECTS
            and r.id not in lifted
            and now - _utc(r.created_at) <= COUNT_WINDOW
        )
        for t in targets:
            slot = (t, r.kind, is_effect)
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
            if r.id not in picked_keys[t]:
                picked_keys[t].append(r.id)
                needed_ids.add(r.id)
    if not needed_ids:
        return {}
    full_rows = {
        r.id: r
        for r in session.scalars(select(JevJudgment).where(JevJudgment.id.in_(needed_ids)))
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for key, ids in picked_keys.items():
        entries = [
            _entry(full_rows[i], now) | {"aufgehoben": i in lifted}
            for i in ids
            if i in full_rows
        ]
        if entries:
            out[key] = entries
    return out


def read_jev(session: Session, now: datetime | None = None, *,
             key_set: bool | None = None) -> dict[str, Any]:
    """Alles, was `jev.html` zeigt — ein Dict aus einfachen Typen (JSON-tauglich)."""
    now = _utc(now or datetime.now(UTC))
    if key_set is None:
        key_set = bool(get_settings().typesafe_api_key)
    horizon = max(CALIBRATION_WINDOW, BILANZ_WINDOW)
    # Erst das Protokoll (volle Zeilen), dann der Zeitraum ohne Texte und Rohantworten:
    # Zahlen, Trennschärfe und Bilanz brauchen sie nicht (Review 01.10.: bei vielen
    # Zeilen sonst Hunderte MB im Build).
    newest = _newest(session, now)
    rows = list(session.scalars(
        select(JevJudgment).where(JevJudgment.created_at >= _naive(now - horizon))
        .options(defer(JevJudgment.text), defer(JevJudgment.raw), defer(JevJudgment.rule),
                 defer(JevJudgment.url))
        .order_by(JevJudgment.created_at.desc(), JevJudgment.id.desc())
    ))
    first = session.scalar(select(JevJudgment.created_at).order_by(JevJudgment.created_at)
                           .limit(1))
    every = list({r.id: r for r in [*rows, *newest]}.values())
    judged = [r for r in every if r.player_id and r.kind in _DECISIVE]
    matches = _matches(session, {r.player_id for r in judged},
                       min((_when(r) for r in judged), default=now))
    effective, lifted = _per_match(every, matches)
    feed = [_entry(r, now) | {"aufgehoben": r.id in lifted, "wiederholt": count}
            for r, count in _collapse(newest)]
    return {
        "stand": now.isoformat(),
        "model": role_judge.MODEL,
        "key": key_set,
        "seit": _iso(first),
        "status": _status(rows or newest, now, key_set),
        "zahlen": _counts(rows, now),
        "arten": _kinds(rows, now),
        "feed": feed,
        "spieler": _by_player(session, every, lifted, now),
        "kalibrierung": _calibration(rows, now),
        "schwellen": thresholds(),
        "bilanz": _bilanz(session, every, now, matches, effective),
    }


def read_jev_safely(session: Session, now: datetime | None = None) -> dict[str, Any] | None:
    """`read_jev` für den Cockpit-Build: Ein Fehler (etwa fehlende Tabelle vor der Migration)
    kostet nur die Jev-Daten, nie den Build — die Seite sagt dann ehrlich, was fehlt."""
    try:
        return read_jev(session, now)
    except Exception as exc:  # noqa: BLE001 — die übrigen Cockpit-Seiten gehen vor
        log.warning("Jev-Seite ohne Daten (%s)", type(exc).__name__)
        return None
