"""Fehler bündeln statt jeden einzeln melden (Emil, 29.08.2026).

Anlass: In der Nacht auf den 29.08. sperrte Kickbase die API während der
Spieltags-Auswertung — von 23:23 bis 06:25 antwortete sie mit 403. Jeder
betroffene Job meldete das einzeln nach Discord, jeweils mit angehängtem
MDN-Link zum HTTP-Statuscode. Elf Nachrichten für EINE Ursache, dazwischen
verstreut die echten Meldungen (Prämie, Cron-Wächter).

Emils Punkt dazu: Wenn er selbst in der App handelt, soll das ebenfalls keine
Fehlerlawine auslösen — „das kann ja auch mal vorkommen".

Die Regel hier ist deshalb einfach: Derselbe Fehler an derselben Stelle wird
EINMAL gemeldet. Wiederholungen werden gezählt und bleiben still; erst wenn er
sich gelegt hat, kommt eine Abschlusszeile mit der Bilanz. Was NICHT passiert:
Fehler verschlucken. Jeder bleibt im Log, und die Zusammenfassung nennt Zahl und
Zeitraum — ein Ausfall soll leiser klingen, nicht unsichtbar sein.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

#: So lange gilt ein Fehler als „derselbe Vorfall". Danach beginnt ein neuer —
#: eine Stunde ist lang genug für eine Wartung und kurz genug, dass ein echtes
#: Dauerproblem am nächsten Morgen wieder auffällt.
FENSTER = timedelta(hours=1)

#: Nach so langer Stille gilt ein Vorfall als beendet und wird abgeschlossen.
RUHE = timedelta(minutes=20)

#: Fremdtext, den Bibliotheken an ihre Fehler hängen. httpx setzt hinter jeden
#: HTTP-Fehler zwei Zeilen Doku-Werbung; in Discord wird daraus eine
#: Linkvorschau, die dreimal so hoch ist wie die Meldung selbst.
_BALLAST = re.compile(
    r"\s*For more information check:\s*https?://\S+|"
    r"\s*See\s+https?://\S+\s*for more information\.?",
    re.I,
)

#: URLs auf das Wesentliche kürzen: Der Pfad sagt, was nicht ging; Host und
#: Query sind in jeder Zeile gleich.
_URL = re.compile(r"https?://[^/\s]+(/[^\s'\"]*)")


def _laeuft_auf_der_box() -> bool:
    """Laeuft dieser Prozess im Container der Box — oder auf einem Mac?

    ``/.dockerenv`` legt Docker in jedem Container an; ausserhalb gibt es die
    Datei nicht. Das ist zuverlaessiger als Hostname oder DATABASE_URL, die
    beide gesetzt sein koennen, ohne dass der Container gemeint ist.
    """
    from pathlib import Path
    return Path("/.dockerenv").exists()


def herkunft() -> str:
    """Praefix fuer Meldungen, die NICHT von der Box kommen.

    Anlass (30.08.2026): Ein lokaler Lauf auf Emils Mac fuehrte Worker-Jobs aus
    und meldete zwei OperationalError nach Discord — ``no such table:
    agent_freeze`` und ``job_runs``. Auf der Box gab es die Tabellen laengst;
    die lokale Datenbank stand nur auf einem aelteren Migrationsstand. Emil sah
    zwei Alarme ueber fehlende Sicherungs-Tabellen und musste annehmen, dass
    sein laufender Agent kaputt ist — er war es nicht.

    Eine Meldung ohne Absender ist im Zweifel die schlimmstmoegliche: Man haelt
    sie fuer die Produktion.
    """
    return "" if _laeuft_auf_der_box() else "[lokal] "


def saubere_meldung(text: str) -> str:
    """Fremdballast raus, URL auf den Pfad kürzen — der Rest bleibt wörtlich."""
    ohne = _BALLAST.sub("", str(text)).strip()
    ohne = _URL.sub(lambda m: m.group(1).split("?")[0], ohne)
    return " ".join(ohne.split())


def fingerabdruck(label: str, exc: BaseException) -> str:
    """Was denselben Vorfall ausmacht: Fehlerart plus HTTP-Status, NICHT der Job.

    Absichtlich ohne ``label``: Beim 403-Ausfall waren Cockpit-Build,
    Intent-Planner und Gebots-Wächter betroffen — drei Jobs, ein Vorfall. Wäre
    der Job Teil des Fingerabdrucks, käme wieder je Job eine Meldung.
    """
    text = str(exc)
    status = re.search(r"\b([45]\d{2})\b", text)
    return f"{type(exc).__name__}:{status.group(1)}" if status else type(exc).__name__


@dataclass
class Vorfall:
    """Ein laufender Fehler-Vorfall — erste Meldung, Zähler, betroffene Jobs."""

    fingerabdruck: str
    beispiel: str
    seit: datetime
    zuletzt: datetime
    anzahl: int = 1
    jobs: set[str] = field(default_factory=set)


class FehlerBuendel:
    """Entscheidet, ob ein Fehler gemeldet wird — und fasst Wiederholungen zusammen.

    Zustand lebt im Prozess: Ein Neustart meldet denselben Fehler wieder, und das
    ist richtig so — nach einem Neustart weiß niemand, ob das Problem noch besteht.
    """

    def __init__(self, fenster: timedelta = FENSTER, ruhe: timedelta = RUHE) -> None:
        self._offen: dict[str, Vorfall] = {}
        self._fenster = fenster
        self._ruhe = ruhe

    def melden(self, label: str, exc: BaseException, *,
               now: datetime | None = None) -> str | None:
        """Text für Discord — oder None, wenn dieser Fehler schon läuft.

        Beim ERSTEN Auftreten die volle Meldung (ohne Bibliotheks-Ballast), bei
        jeder Wiederholung im Fenster nichts.
        """
        jetzt = now or datetime.now(UTC)
        fp = fingerabdruck(label, exc)
        v = self._offen.get(fp)
        if v is not None and jetzt - v.seit <= self._fenster:
            v.anzahl += 1
            v.zuletzt = jetzt
            v.jobs.add(label)
            return None
        self._offen[fp] = Vorfall(fp, saubere_meldung(exc), jetzt, jetzt, 1, {label})
        return (f"⚠️ {herkunft()}{label}-Fehler: "
                f"{type(exc).__name__}: {saubere_meldung(exc)}")

    def abgeklungen(self, *, now: datetime | None = None) -> list[str]:
        """Abschlusszeilen für Vorfälle, die sich gelegt haben.

        Wird vom Puls aufgerufen. Ein Vorfall mit nur einer Meldung bekommt keine
        Zusammenfassung — die stand schon da; alles andere wäre Rauschen über
        Rauschen.
        """
        jetzt = now or datetime.now(UTC)
        fertig = [v for v in self._offen.values() if jetzt - v.zuletzt > self._ruhe]
        texte: list[str] = []
        for v in fertig:
            del self._offen[v.fingerabdruck]
            if v.anzahl < 2:
                continue
            dauer = v.zuletzt - v.seit
            minuten = max(1, int(dauer.total_seconds() // 60))
            jobs = ", ".join(sorted(v.jobs))
            texte.append(
                f"✅ {herkunft()}Erledigt: **{v.fingerabdruck}** trat {v.anzahl}× auf "
                f"({minuten} min, betroffen: {jobs}). Seit "
                f"{int((jetzt - v.zuletzt).total_seconds() // 60)} min Ruhe."
            )
        return texte

    def offen(self) -> list[Vorfall]:
        """Laufende Vorfälle — für den Puls, damit ein Dauerausfall sichtbar bleibt."""
        return list(self._offen.values())
