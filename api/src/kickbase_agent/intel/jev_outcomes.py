"""Treffer-Bilanz der Jev-Urteile: Urteil gegen echtes Ergebnis (Spec 2026-10-01 §3).

Emil 01.10.2026: „baue es weiter aus". Die Schwellen der Jev-Fragen (0,95) stammen aus
Messungen an wenigen Anreißern (L-114). Hier misst der Agent jedes klare Urteil an dem,
was danach wirklich passierte — rein lesend, beim Cockpit-Build berechnet:

* **Rolle** („fehlt", „startet", „bank" ≥ 0,95): das erste ausgewertete Spiel des Spielers
  NACH dem Artikel (`player_performance`). „fehlt" stimmt bei 0 Minuten, „startet" bei
  mindestens 45, „bank" bei weniger als 45. Kickbase liefert keinen Startelf-Beleg; 45
  Minuten sind eine Näherung.
* **Rückkehr-Hinweis**: stimmt, wenn er im nächsten Spiel wieder spielte. Dazu der
  Marktwert am Tag des Hinweises und nach 3 und 7 Tagen (`player_history`) — als
  Information, nicht als Treffer: Der Radar behauptet eine Rückkehr, keinen Kursanstieg.

Fehlende Daten sind nie ein Fehlurteil: Ein Spiel ohne beobachtete Minuten, ein noch
nicht abgeschlossenes Spiel (Anpfiff vor weniger als `SETTLE`) oder ein Spiel mehr als
`MAX_GAP` nach dem Artikel (dann fehlt vermutlich die Zeile des eigentlichen nächsten
Spiels) lassen den Fall offen.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

#: Ab so lange nach Anpfiff gilt ein Spiel als ausgewertet.
SETTLE = timedelta(hours=3)
#: Eine Zeile, die früher nach Anpfiff erfasst wurde, kann Teilminuten tragen (der Snapshot
#: um 22:05 erfasst ein 20:30-Spiel mitten in der zweiten Halbzeit, Review 01.10.).
FINAL_AFTER = timedelta(hours=2, minutes=15)
#: Kickbase aktualisiert Marktwerte um 22:00 Berlin; ein Tageswert gilt bis zum nächsten.
MV_UPDATE_HOUR = 22
#: Weiter weg ist es nicht mehr „das nächste Spiel" (Länderspielpausen ≤ 2 Wochen).
MAX_GAP = timedelta(days=14)
#: „startet" verlangt so viele Minuten, „bank" weniger.
START_MINUTES = 45

TREFFER = "treffer"
DANEBEN = "daneben"
OFFEN = "offen"

_BERLIN = ZoneInfo("Europe/Berlin")


@dataclass(frozen=True)
class Verdict:
    """Was die Bilanz von einer Protokollzeile braucht."""

    id: int
    kind: str
    outcome: str
    answer: str | None
    probability: float | None
    player_id: str | None
    player_name: str | None
    subject: str | None
    published_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class Match:
    """Ein Spieltag eines Spielers: Anpfiff, Minuten, ob sie beobachtet sind, und wann die
    Zeile zuletzt erfasst wurde (None bei Altzeilen)."""

    match_date: datetime
    minutes: int
    observed: bool
    captured_at: datetime | None = None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def first_match_after(
    matches: Iterable[Match], after: datetime, now: datetime
) -> Match | None:
    """Das erste Spiel nach `after` — nur wenn es ausgewertet ist, sonst None (offen)."""
    after, now = _utc(after), _utc(now)
    later = sorted((m for m in matches if _utc(m.match_date) > after),
                   key=lambda m: _utc(m.match_date))
    if not later:
        return None
    first = later[0]
    kickoff = _utc(first.match_date)
    if kickoff - after > MAX_GAP or now - kickoff < SETTLE or not first.observed:
        return None
    if first.captured_at is not None and _utc(first.captured_at) - kickoff < FINAL_AFTER:
        return None  # während des Spiels erfasst: Teilminuten, erst der nächste Snapshot zählt
    return first


def next_kickoff(matches: Iterable[Match], after: datetime) -> datetime | None:
    """Anpfiff des ersten Spiels nach `after` — ausgewertet oder nicht; None, wenn noch keine
    Zeile danach existiert (das kommende Spiel). Gruppiert Urteile je Spiel."""
    after = _utc(after)
    return min((_utc(m.match_date) for m in matches if _utc(m.match_date) > after), default=None)


def _anchor(verdict: Verdict) -> datetime:
    return _utc(verdict.published_at or verdict.created_at)


def _day(value: datetime) -> str:
    return _utc(value).astimezone(_BERLIN).strftime("%d.%m.")


def _base(verdict: Verdict) -> dict:
    return {
        "id": verdict.id,
        "player_id": verdict.player_id,
        "player": verdict.player_name or verdict.player_id or "?",
        "subject": verdict.subject or "",
        "verdict": verdict.answer,
        "p": verdict.probability,
        "at": _utc(verdict.created_at).isoformat(),
    }


def check_role(verdict: Verdict, matches: Iterable[Match], now: datetime) -> dict:
    """Ein klares Rollen-Urteil gegen die Minuten seines nächsten Spiels."""
    match = first_match_after(matches, _anchor(verdict), now)
    row = _base(verdict) | {"match_date": None, "minutes": None}
    if match is None:
        return row | {"result": OFFEN, "why": "nächstes Spiel noch nicht ausgewertet"}
    minutes = match.minutes
    if verdict.answer == "fehlt":
        hit = minutes == 0
    elif verdict.answer == "startet":
        hit = minutes >= START_MINUTES
    else:  # bank
        hit = minutes < START_MINUTES
    played = f"{minutes} Minuten" if minutes else "nicht gespielt"
    return row | {
        "match_date": _utc(match.match_date).isoformat(),
        "minutes": minutes,
        "result": TREFFER if hit else DANEBEN,
        "why": f"{played} am {_day(match.match_date)}",
    }


def valid_day(when: datetime) -> date:
    """Der Tag, dessen Marktwert zur Fragezeit galt: Ein Tageswert entsteht mit dem Update
    um 22:00 Berlin (Snapshot 22:05) — vorher gilt noch der des Vortags (Review 01.10.)."""
    return (_utc(when).astimezone(_BERLIN) - timedelta(hours=MV_UPDATE_HOUR)).date()


def _value_on(values: Sequence[tuple[date, int]], day: date) -> int | None:
    return next((v for d, v in values if d == day), None)


def _value_until(values: Sequence[tuple[date, int]], day: date) -> int | None:
    earlier = [(d, v) for d, v in values if d <= day]
    return max(earlier)[1] if earlier else None


def _pct(new: int | None, old: int | None) -> float | None:
    if new is None or not old:
        return None
    return round((new - old) / old * 100, 1)


def check_return(
    verdict: Verdict,
    matches: Iterable[Match],
    values: Iterable[tuple[date, int]],
    now: datetime,
) -> dict:
    """Ein Rückkehr-Hinweis: spielte er wieder? Dazu der Marktwert nach 3 und 7 Tagen."""
    ordered = sorted(values)
    day = valid_day(verdict.created_at)
    mv_0 = _value_until(ordered, day)
    mv_3 = _value_on(ordered, day + timedelta(days=3))
    mv_7 = _value_on(ordered, day + timedelta(days=7))
    match = first_match_after(matches, _anchor(verdict), now)
    row = _base(verdict) | {
        "mv_0": mv_0, "mv_3": mv_3, "mv_7": mv_7,
        "d3_pct": _pct(mv_3, mv_0), "d7_pct": _pct(mv_7, mv_0),
        "match_date": None, "minutes": None, "played": None,
    }
    if match is None:
        return row | {"result": OFFEN, "why": "nächstes Spiel noch nicht ausgewertet"}
    played = match.minutes > 0
    return row | {
        "match_date": _utc(match.match_date).isoformat(),
        "minutes": match.minutes,
        "played": played,
        "result": TREFFER if played else DANEBEN,
        "why": (f"spielte {match.minutes} Minuten" if played else "spielte nicht")
        + f" am {_day(match.match_date)}",
    }


def summarize(checks: Iterable[dict]) -> dict[str, int]:
    """Treffer, daneben, offen — und wie viele entschieden sind."""
    counts = {TREFFER: 0, DANEBEN: 0, OFFEN: 0}
    for check in checks:
        counts[check["result"]] = counts.get(check["result"], 0) + 1
    return counts | {"entschieden": counts[TREFFER] + counts[DANEBEN]}
