"""Aus Belegen wird ein Status — die Antwort auf „waren die Annahmen richtig?" (Spec §5).

Eigenes Modul, getrennt von `intel/evaluation.py`: Das dort bewertet Trades, hier geht
es um Wissen. Zwei Dinge sollen wahr bleiben:

**Grob statt genau.** Fünf Stufen, keine Prozentzahlen. Bei zweistelligen Fallzahlen
ist jede feinere Statistik gelogen; eine „Trefferquote 71,4 %" aus sieben Beobachtungen
klingt sicherer, als sie ist.

**Ein Status muss eine Handlung auslösen, sonst ist er Deko.** `wackelt`, `widerlegt`
und `erneut aufgetreten` landen in `to_fix` — die einzige Liste, die zum Handeln
auffordert. Der Sammler-Job meldet genau diese Wechsel nach Discord.

Zur Spec-Tabelle kommt eine Stufe hinzu: **`beobachtet`** für „ein Beleg, kein
Gegenbeleg". Die Spec verlangt für `gestützt` mindestens zwei Belege und für
`unbelegt` gar keinen — ein einzelner Beleg fiele sonst zwischen die Stühle und
müsste als „noch keine Erfahrung" angezeigt werden, obwohl es eine gibt. Das wäre
eine Lüge in die vorsichtige Richtung; `beobachtet` sagt stattdessen die Wahrheit.

Ebenso bekommt `wackelt` jeden Fall mit mindestens einem Gegenbeleg, der noch nicht
`widerlegt` ist (auch 0:1 und 1:1). Ein Gegenbeleg gehört gesehen, nicht gerundet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from kickbase_agent.knowledge.learnings import ART_FAKT, ART_HYPOTHESE, ART_PANNE

# --- Hypothese
UNBELEGT = "unbelegt"
BEOBACHTET = "beobachtet"
GESTUETZT = "gestützt"
BEWAEHRT = "bewährt"
WACKELT = "wackelt"
WIDERLEGT = "widerlegt"
HYPOTHESE_STATUSES = (UNBELEGT, BEOBACHTET, GESTUETZT, BEWAEHRT, WACKELT, WIDERLEGT)

# --- Fakt
GILT = "gilt"
UEBERHOLT = "überholt"

# --- Panne
HAELT = "hält"
ERNEUT = "erneut aufgetreten"

#: Diese Status fordern zum Handeln auf und stehen in der Wochen-Ansicht oben.
TO_FIX_STATUSES = (WACKELT, WIDERLEGT, ERNEUT)

#: Schwellen (Spec §5) — bewusst als Konstanten, damit eine Änderung sichtbar ist.
GESTUETZT_MIN = 2
BEWAEHRT_MIN = 4
BEWAEHRT_AGE_DAYS = 14
WIDERLEGT_MIN = 2

#: Fenster des Wochen-Kopfs („wird sie besser?" ist eine Wochenfrage, kein Live-Monitor).
WEEK_DAYS = 7

_SUCCESSOR_RE = re.compile(r"\bL-\d{3,}\b")

_TONE = {
    BEWAEHRT: "gut", GESTUETZT: "gut", GILT: "gut", HAELT: "gut",
    BEOBACHTET: "neutral", UNBELEGT: "neutral", UEBERHOLT: "neutral",
    WACKELT: "warnung",
    WIDERLEGT: "schlecht", ERNEUT: "schlecht",
}


@dataclass(frozen=True)
class Status:
    """Status einer Erkenntnis samt Anzeigetext."""

    status: str
    label: str
    tone: str          #: gut | neutral | warnung | schlecht — Farbrolle, kein Farbwert
    confirm: int = 0
    refute: int = 0
    successor: str | None = None   #: Nachfolge-ID, wenn ein Fakt überholt ist

    @property
    def needs_action(self) -> bool:
        return self.status in TO_FIX_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "label": self.label, "tone": self.tone,
            "needs_action": self.needs_action, "successor": self.successor,
        }


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _bilanz_text(confirm: int, refute: int) -> str:
    return f"{confirm}× bestätigt, {refute}× widerlegt"


def _hypothesis(confirm: int, refute: int, first_at: datetime | None, now: datetime) -> str:
    if confirm == 0 and refute == 0:
        return UNBELEGT
    if refute >= WIDERLEGT_MIN and refute >= confirm:
        return WIDERLEGT
    if refute >= 1:
        # Auch 0:1 und 1:1 landen hier: Ein Gegenbeleg gehoert gesehen.
        return WACKELT
    if confirm >= BEWAEHRT_MIN and first_at is not None:
        if now - first_at >= timedelta(days=BEWAEHRT_AGE_DAYS):
            return BEWAEHRT
    if confirm >= GESTUETZT_MIN:
        return GESTUETZT
    return BEOBACHTET


def _successor(items: list[dict[str, Any]] | None) -> str | None:
    """Nachfolge-ID aus dem Agenten-Gegenbeleg lesen („ersetzt durch L-070")."""
    for item in items or []:
        if item.get("source") != "agent" or item.get("verdict") != "refute":
            continue
        for field in ("note", "ref"):
            match = _SUCCESSOR_RE.search(str(item.get(field) or ""))
            if match:
                return match.group(0)
    return None


def derive(
    art: str,
    summary: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    items: list[dict[str, Any]] | None = None,
) -> Status:
    """Status einer Erkenntnis aus ihrer Beleg-Bilanz.

    `summary` ist ein Eintrag aus `evidence.summaries()`; ``None`` oder leer heißt
    „noch keine Erfahrung". `items` braucht nur der Fakt-Zweig (Nachfolge-ID).
    """
    stamp = now or datetime.now(UTC)
    data = summary or {}
    confirm = int(data.get("confirm") or 0)
    refute = int(data.get("refute") or 0)

    if art == ART_FAKT:
        # Ein Fakt hat keine Quote. Er gilt — bis ICH ihn für überholt erkläre;
        # eine Auto-Regel darf ein Nachschlagewerk nicht entwerten.
        # Die getrennten Summen von Quelle und Verdikt beweisen keinen Schnitt.
        # Der gemeinsame Zähler umfasst auch Belege außerhalb der 10er-Vorschau.
        agent_refutes = (
            int(data["agent_refute"]) > 0
            if "agent_refute" in data
            else any(item.get("source") == "agent" and item.get("verdict") == "refute"
                     for item in items or [])
        )
        if agent_refutes:
            successor = _successor(items)
            hint = f" — ersetzt durch {successor}" if successor else ""
            return Status(UEBERHOLT, f"überholt{hint}", _TONE[UEBERHOLT],
                          confirm, refute, successor)
        return Status(GILT, "gilt", _TONE[GILT], confirm, refute)

    if art == ART_PANNE:
        # Ein Gegenbeleg heißt hier: Der Vorfall ist wieder passiert.
        if refute >= 1:
            return Status(ERNEUT, f"erneut aufgetreten ({refute}×)", _TONE[ERNEUT],
                          confirm, refute)
        return Status(HAELT, "Gegenmaßnahme hält", _TONE[HAELT], confirm, refute)

    status = _hypothesis(confirm, refute, _parse(data.get("first_at")), stamp)
    label = "noch keine Erfahrung" if status == UNBELEGT else _bilanz_text(confirm, refute)
    return Status(status, label, _TONE[status], confirm, refute)


def derive_all(
    learnings: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
    items: dict[str, list[dict[str, Any]]] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Status]:
    """Status je Erkenntnis mit ID. Einträge ohne ID bekommen keinen — sie haben
    keinen Anker und damit keine Bilanz (Spec §3.1)."""
    stamp = now or datetime.now(UTC)
    out: dict[str, Status] = {}
    for entry in learnings:
        lid = entry.get("id")
        if not lid:
            continue
        out[lid] = derive(
            str(entry.get("art") or ART_HYPOTHESE),
            summaries.get(lid),
            now=stamp,
            items=(items or {}).get(lid),
        )
    return out


def to_fix_ids(statuses: dict[str, Status]) -> list[str]:
    """IDs, die eine Korrektur verlangen — sortiert, damit die Liste stabil bleibt."""
    return sorted(lid for lid, s in statuses.items() if s.needs_action)


def balance(
    learnings: list[dict[str, Any]],
    statuses: dict[str, Status],
    week_summaries: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Wochen-Kopf: „3 Annahmen bestätigt · 1 widerlegt · 2 neu · 12 aktive Hypothesen".

    Gezählt werden ERKENNTNISSE, nicht Belege: Fünf Belege zu derselben Annahme sind
    ein Lernfortschritt, nicht fünf. `week_summaries` ist `evidence.summaries(...,
    since=vor 7 Tagen)`.
    """
    stamp = now or datetime.now(UTC)
    since = stamp - timedelta(days=WEEK_DAYS)
    hypothesen = {
        str(e["id"]) for e in learnings
        if e.get("id") and str(e.get("art") or "") == ART_HYPOTHESE
    }
    verteilung = dict.fromkeys(HYPOTHESE_STATUSES, 0)
    for lid in hypothesen:
        state = statuses.get(lid)
        if state is not None and state.status in verteilung:
            verteilung[state.status] += 1

    confirmed = sum(
        1 for lid in hypothesen if int((week_summaries.get(lid) or {}).get("confirm") or 0) > 0
    )
    refuted = sum(
        1 for lid in hypothesen if int((week_summaries.get(lid) or {}).get("refute") or 0) > 0
    )
    # Ein Eintrag OHNE lesbares Datum zaehlt nicht als „neu" — sonst waere jeder
    # undatierte Alt-Eintrag jede Woche wieder neu.
    new_week = sum(
        1 for e in learnings
        if e.get("id") and (written := _parse(f"{e.get('date')}T12:00:00+00:00")) is not None
        and written >= since
    )
    return {
        "since": since.isoformat(),
        "confirmed_week": confirmed,
        "refuted_week": refuted,
        "new_week": new_week,
        "active_hypotheses": len(hypothesen),
        "verteilung": verteilung,
    }
