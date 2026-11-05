"""Erkenntnisse (LEARNINGS.md) als Daten — Lern-Schicht der Steuerzentrale.

Der Orchestrator pflegt `.claude/skills/kickbase/LEARNINGS.md` als Bullet-Liste
(``- **Titel (Datum):** Text …``, mehrzeilig eingerückt, unter ``##``-Abschnitten).
Ein Eintrag darf zwei Strukturmarker tragen (25.08., Emils Wunsch „echte Learnings,
aber zusammengefasst wie Menschen"):

    - **Titel (Datum):** Ausführlicher Text für den Agenten (= Details).
      **Kurz:** Ein bis zwei Sätze, wie man es einem Kollegen erzählt.
      **Regel:** Was der Agent ab jetzt konkret tut (ein Satz).

Beide sind optional und rückwärtskompatibel: Fehlt ``Kurz``, wird die Zusammenfassung
aus den ersten Sätzen abgeleitet (Markdown entfernt, auf ``SUMMARY_MAX`` gekürzt);
fehlt ``Regel``, ist das Feld leer und der Eintrag zählt als Notiz statt als Regel.

Seit der Lern-Bilanz (26.08., Spec 2026-08-25-lern-bilanz-design.md) kommen zwei
Anker dazu — beide optional, beide rückwärtskompatibel:

    - [L-051] **Titel (24.08.):** Ausführlicher Text …
      **Art:** hypothese | fakt | panne

``[L-051]`` ist die STABILE ID des Eintrags: Sie überlebt jede Umformulierung und ist
der Anker, an dem Belege (`learning_evidence`) hängen. Ein Eintrag ohne ID bleibt
gültig — er bekommt ``id=None`` und erscheint weiter in der Liste, nur ohne Bilanz
(kein Datenverlust während der Migration). ``**Art:**`` trennt prüfbare Annahmen von
Nachschlagewerk und Pannen; fehlt sie, gilt konservativ: mit ``Regel:`` ⇒ ``hypothese``,
ohne ⇒ ``fakt`` (ein Fakt bekommt so nie eine falsche Trefferquote).

Dieses Modul liest genau dieses Format und liefert Einträge für UI/API/MCP:

    {"id": "L-051" | None, "art": "hypothese"|"fakt"|"panne",
     "date": "2026-08-21" | None, "title": str, "text": str, "order": int,
     "section": str, "summary": str, "rule": str, "details": str}

``text`` bleibt der vollständige Fließtext (Bestandsverträge: MCP ``evaluation``,
Cockpit-Fallback); ``details`` ist derselbe Text OHNE die Marker-Abschnitte.

Pfad-Auflösung (``load_learnings``): explizites Argument (strikt — fehlt die Datei,
gibt es []) -> Env ``KBA_LEARNINGS_PATH`` -> ``<repo-root>/.claude/skills/kickbase/
LEARNINGS.md`` -> ``/app/learnings.md`` -> ``/app/data/learnings.md`` (Box-Container,
der nur ``api/`` enthält). Keine Datei gefunden => [].

Datum: aus dem Fettteil („(21.08.)", „(21.08., teuer gelernt)", „20.08.26",
„(Emil, 23.08. ~12:20)"); ohne eigenes Datum erbt ein Eintrag das Datum seines
``##``-Abschnitts (z. B. „verifiziert 20.08.26"), sonst None. Jahr fehlt => 2026.
Sortierung: neueste zuerst; gleiches/fehlendes Datum => Dateireihenfolge rückwärts.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any

#: Jahr, das bei „TT.MM." ohne Jahresangabe ergänzt wird (Saison 2026/27).
DEFAULT_YEAR = 2026
ENV_VAR = "KBA_LEARNINGS_PATH"
#: Repo-Root = vier Ebenen über diesem Modul (knowledge -> kickbase_agent -> src -> api -> Root).
REPO_ROOT = Path(__file__).resolve().parents[4]
REPO_LEARNINGS = REPO_ROOT / ".claude" / "skills" / "kickbase" / "LEARNINGS.md"
FALLBACK_PATHS: tuple[Path, ...] = (
    REPO_LEARNINGS,
    Path("/app/learnings.md"),
    Path("/app/data/learnings.md"),
)
#: Titel ohne Fettteil: bis zum ersten Doppelpunkt, höchstens so viele Zeichen.
_TITLE_MAX = 80
#: Abgeleitete Kurzfassung: so viele Zeichen darf sie höchstens haben (Tabellenzeile).
SUMMARY_MAX = 220
#: … und mindestens so lang soll sie sein, bevor der Satz-Sammler aufhört.
_SUMMARY_MIN = 110

#: Drei Sorten Wissen (Spec §2). Nicht jede Lektion ist statistisch prüfbar — wer
#: alles in eine Trefferquote presst, erzeugt Scheingenauigkeit.
ART_HYPOTHESE = "hypothese"   #: Verhaltensannahme — bekommt eine Trefferbilanz.
ART_FAKT = "fakt"             #: Nachschlagewerk über die API — kein Zähler.
ART_PANNE = "panne"           #: Vorfall + Gegenmaßnahme — jede Wiederholung zählt gegen.
ARTEN: tuple[str, ...] = (ART_HYPOTHESE, ART_FAKT, ART_PANNE)

_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?(?!\d)")
#: Datum samt direkt folgender Uhrzeit („23.08. ~12:20") — nur diese wird mit entfernt.
_DATE_TIME_RE = re.compile(
    r"(?<!\d)\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})?(?!\d)(?:\s*~?\d{1,2}:\d{2}(?!\d))?"
)
_BOLD_RE = re.compile(r"^\*\*(.+?)\*\*\s*:?\s*(.*)$", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_SENTENCE_END_RE = re.compile(r"[.;!?](?:\s+|$)")
#: Strukturmarker — nur am ZEILENANFANG gueltig (25.08.): Ein Eintrag, der die Marker
#: im Fliesstext erwaehnt („… traegt **Kurz:** und **Regel:**"), darf nicht zerfallen.
_MARKER_RE = re.compile(r"^\*\*(Kurz|Regel|Art):?\*\*:?\s*")
_MARKER_FIELD = {"Kurz": "summary", "Regel": "rule", "Art": "art"}
#: Stabile ID am Zeilenanfang eines Bullets: „- [L-051] **Titel …" (Spec §3.1).
_ID_RE = re.compile(r"^\[(L-\d{3,})\]\s*")
#: Höchste vergebene Nummer als Datei-Fußzeile — verhindert ID-Kollisionen, wenn
#: zwei Sitzungen gleichzeitig schreiben.
NEXT_ID_RE = re.compile(r"<!--\s*next-id:\s*(L-\d{3,})\s*-->")
#: Markdown-Reste, die in einer Kurzfassung nichts verloren haben.
_MD_NOISE_RE = re.compile(r"\*\*|`|_{2,}")


# ------------------------------------------------------------------ Pfad


def resolve_learnings_path(path: str | os.PathLike[str] | None = None) -> Path | None:
    """Erste existierende Datei in der Reihenfolge Argument (strikt) -> Env -> Defaults."""
    if path is not None:
        p = Path(path)
        return p if p.is_file() else None
    candidates: list[Path] = []
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        candidates.append(Path(env))
    candidates.extend(FALLBACK_PATHS)
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


# ------------------------------------------------------------------ Parser


def parse_date(text: str) -> str | None:
    """Erstes gültiges Datum „TT.MM.", „TT.MM.JJ" oder „TT.MM.JJJJ" als ISO; sonst None."""
    for m in _DATE_RE.finditer(text):
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if year is None:
            y = DEFAULT_YEAR
        elif len(year) == 2:
            y = 2000 + int(year)
        else:
            y = int(year)
        try:
            return date(y, month, day).isoformat()
        except ValueError:
            continue
    return None


def strip_date(text: str) -> str:
    """Datums-/Zeitangabe aus einem Titel entfernen und Klammerreste aufräumen.

    „Storno sperrt Re-Bid (21.08., teuer gelernt)" -> „Storno sperrt Re-Bid (teuer gelernt)";
    „Steuerzentrale (23.08.)" -> „Steuerzentrale"; „(Emil, 23.08. ~12:20)" -> „(Emil)";
    „Haustuer zu (25.08., 6f69a6b)" -> „Haustuer zu" (Commit-Hashes sind kein Titel).
    """
    out = _DATE_TIME_RE.sub("", text)
    out = re.sub(r"\(\s*[,;·]\s*", "(", out)       # „(, teuer gelernt)" -> „(teuer gelernt)"
    out = re.sub(r"\(\s+", "(", out)                # „( abends)" -> „(abends)"
    # Commit-Hashes sind Agenten-Rauschen, keine Ueberschrift (25.08.) — NACH der
    # Komma-Bereinigung, weil aus „(25.08., 6f69a6b)" erst „(6f69a6b)" werden muss.
    out = re.sub(r"\s*\(\s*[0-9a-f]{7,10}\s*\)", "", out)
    out = re.sub(r"\s*[,;·]\s*\)", ")", out)       # „(Emil, )" -> „(Emil)"
    out = re.sub(r"\s*,\s*,", ",", out)            # doppelte Kommata
    # Leere Klammern nur, wenn sie durch die Datums-Entfernung entstanden sind — also
    # freistehend nach einem Leerzeichen. „print()" ist Teil des Namens und bleibt.
    out = re.sub(r"\s\(\s*\)", "", out)
    out = re.sub(r"\s+([,;:)])", r"\1", out)       # Leerzeichen vor Satzzeichen
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" -—:")


def _split_entry(raw: str) -> tuple[str, str, str]:
    """Roh-Text eines Bullets -> (title_raw, text, date_source).

    date_source = der Teil, in dem das Datum gesucht wird (Fettteil; ohne Fettteil nichts —
    Daten im Fließtext sind meist Ereignisdaten, keine Lern-Daten).
    """
    m = _BOLD_RE.match(raw)
    if m:
        title_raw = m.group(1).strip().rstrip(":").strip()
        return title_raw, m.group(2).strip(), title_raw
    title, text = _plain_title(raw)
    return title, text, ""


def _plain_title(raw: str) -> tuple[str, str]:
    """Eintrag ohne Fettteil: Titel = Kopf bis zum ersten Doppelpunkt, sonst bis zum ersten
    Satzende/Semikolon; passt nichts in ``_TITLE_MAX`` Zeichen, wird am Wortende gekürzt
    („…") und der volle Text bleibt im Text."""
    head, sep, tail = raw.partition(":")
    h = head.strip()
    if sep and 0 < len(h) <= _TITLE_MAX:
        return h, tail.strip()
    m = _SENTENCE_END_RE.search(raw)
    if m and 0 < m.start() <= _TITLE_MAX:
        return raw[: m.start()].strip(), raw[m.end():].strip()
    if len(raw) <= _TITLE_MAX:
        return raw.strip(), ""
    cut = raw[:_TITLE_MAX].rsplit(" ", 1)[0]
    return cut.strip() + " …", raw.strip()


def strip_markdown(text: str) -> str:
    """Fettzeichen, Code-Ticks und Mehrfach-Leerzeichen raus — für Kurzfassungen."""
    return re.sub(r"\s{2,}", " ", _MD_NOISE_RE.sub("", text)).strip()


def split_markers(parts: list[str]) -> dict[str, str]:
    """Roh-Zeilen eines Bullets -> {details, summary, rule, art}.

    Ein Marker gilt nur, wenn eine ZEILE mit ihm beginnt; Folgezeilen gehoeren zum
    zuletzt geoeffneten Abschnitt (Ausnahme ``Art``, s. u.). Ohne Marker landet alles
    in ``details`` — genau das Verhalten der Alt-Eintraege.
    """
    buckets: dict[str, list[str]] = {"details": [], "summary": [], "rule": [], "art": []}
    current = "details"
    for part in parts:
        line = part.strip()
        m = _MARKER_RE.match(line)
        if m:
            field = _MARKER_FIELD[m.group(1)]
            line = line[m.end():]
            if field == "art":
                # ``Art`` ist EIN Wort, kein Abschnitt (26.08.): Folgezeilen gehören
                # weiter zum zuvor offenen Bucket. Sonst verschluckt eine Art-Marke
                # mitten im Eintrag den ganzen Rest des Fließtextes.
                if line:
                    buckets["art"].append(line)
                continue
            current = field
        if line:
            buckets[current].append(line)
    return {k: " ".join(v).strip() for k, v in buckets.items()}


def derive_summary(text: str) -> str:
    """Kurzfassung aus dem Fließtext: ganze Sätze bis ``SUMMARY_MAX``, Markdown entfernt.

    Ein Satz reicht, wenn er lang genug ist; sonst kommt der nächste dazu. Passt kein
    ganzer Satz mehr, wird am Wortende mit „…" gekürzt.
    """
    clean = strip_markdown(text)
    if not clean:
        return ""
    out = ""
    for m in _SENTENCE_END_RE.finditer(clean):
        end = m.end()
        if end > SUMMARY_MAX:
            break
        out = clean[:end].strip()
        if len(out) >= _SUMMARY_MIN:
            break
    if out:
        return out
    if len(clean) <= SUMMARY_MAX:
        return clean
    # -2 Zeichen Platz fuer das angehaengte „ …" — sonst reisst die Kuerzung das Limit.
    return clean[: SUMMARY_MAX - 2].rsplit(" ", 1)[0].rstrip(" ,;:") + " …"


def default_art(rule: str) -> str:
    """Art raten, wenn der ``Art:``-Marker fehlt (Spec §2).

    Bewusst konservativ: Nur ein Eintrag mit ``Regel:`` behauptet etwas über die Welt
    und verdient eine Trefferbilanz; alles andere bleibt Nachschlagewerk. So bekommt
    ein API-Fakt nie eine Quote, die er nicht tragen kann. Pannen erkennt diese
    Ableitung nicht — die trägt der Mensch (bzw. die Migration) ein.
    """
    return ART_HYPOTHESE if rule.strip() else ART_FAKT


def parse_next_id(markdown: str) -> str | None:
    """Letzte ``<!-- next-id: L-064 -->``-Marke der Datei; ohne Marke None."""
    matches = NEXT_ID_RE.findall(markdown)
    return matches[-1] if matches else None


def is_art_line(line: str) -> bool:
    """Beginnt die Zeile mit dem ``Art``-Marker? (Einordnung, nicht Inhalt.)"""
    m = _MARKER_RE.match(line.strip())
    return bool(m) and m.group(1) == "Art"


def art_value(line: str) -> str | None:
    """Eingetragene Art einer ``**Art:**``-Zeile; unbekannter Wert oder andere Zeile => None."""
    m = _MARKER_RE.match(line.strip())
    if not m or m.group(1) != "Art":
        return None
    value = strip_markdown(line.strip()[m.end():]).strip().lower()
    return value if value in ARTEN else None


def section_title(line: str) -> str | None:
    """Titel einer ``##``-Überschrift (oder tiefer); jede andere Zeile => None."""
    m = _HEADING_RE.match(line)
    return m.group(2).strip() if m and len(m.group(1)) >= 2 else None


def split_id(line: str) -> tuple[str | None, str]:
    """``"[L-051] **Titel …"`` -> ``("L-051", "**Titel …")``; ohne ID ``(None, line)``."""
    m = _ID_RE.match(line)
    return (m.group(1), line[m.end():].lstrip()) if m else (None, line)


# --- Blockgrenzen: EINE Quelle für Parser und Migration -----------------------
# Beide müssen exakt dieselben Einträge sehen. Läuft die Migration auf andere
# Grenzen, schreibt sie die Art-Zeile in den falschen Eintrag.


def split_bullet(line: str) -> str | None:
    """Top-Level-Bullet -> Inhalt ohne „- "; jede andere Zeile -> None."""
    m = _BULLET_RE.match(line)
    return m.group(1) if m else None


def is_block_break(line: str) -> bool:
    """Zeile, die einen Eintrag beendet: Überschrift, Trennlinie oder Leerzeile."""
    return (
        bool(_HEADING_RE.match(line))
        or line.strip() in ("---", "***", "___")
        or not line.strip()
    )


def title_and_date(raw: str) -> tuple[str, str, str | None]:
    """Bullet-Rohtext -> (Titel, Fließtext, eigenes Datum). Von Parser UND Migration
    benutzt, damit die Prüf-Übersicht dieselben Titel zeigt wie die Zentrale."""
    title_raw, text, date_source = _split_entry(raw)
    own_date = parse_date(date_source) if date_source else None
    return (strip_date(title_raw) if own_date else title_raw.strip()), text, own_date


def parse_learnings(markdown: str) -> list[dict[str, Any]]:
    """Markdown-Text -> Einträge (neueste zuerst, siehe Modul-Docstring)."""
    entries: list[dict[str, Any]] = []
    section = ""
    section_date: str | None = None
    current: list[str] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        lines = [part.strip() for part in current if part.strip()]
        current = None
        # Die ID ist ein Anker, kein Text: Sie fliegt vor jeder Auswertung raus, damit
        # weder Titel noch Fließtext noch Kurzfassung sie je zu sehen bekommen.
        learning_id: str | None = None
        if lines:
            learning_id, lines[0] = split_id(lines[0])
            if not lines[0]:
                lines.pop(0)
        # Die Art-Zeile ist Einordnung, kein Inhalt: Sie darf `text` NICHT verändern
        # (Bestandsvertrag MCP/Cockpit) — sonst schriebe die Migration in jeden der
        # 69 Einträge ein „**Art:** hypothese" hinein.
        raw = " ".join(ln for ln in lines if not is_art_line(ln)).strip()
        if not raw:
            return
        # `text` bleibt der VOLLE Fliesstext (Bestandsvertrag); Titel/Details kommen
        # aus den Zeilen OHNE Marker-Abschnitte.
        title, text, own_date = title_and_date(raw)
        parts = split_markers(lines)
        parts["details"] = _split_entry(parts["details"])[1] if parts["details"] else ""
        rule = strip_markdown(parts["rule"])
        art = strip_markdown(parts["art"]).strip().lower()
        entries.append(
            {
                "id": learning_id,
                # Unbekannter Wert wird NICHT erfunden — dann gilt die Ableitung.
                "art": art if art in ARTEN else default_art(rule),
                "date": own_date or section_date,
                "title": title,
                "text": text,
                "order": len(entries),
                "section": section,
                # Menschen-Sicht: explizite „Kurz"-Zeile schlaegt die Ableitung.
                "summary": strip_markdown(parts["summary"]) or derive_summary(parts["details"]),
                # Agenten-Sicht: was ab jetzt gilt (leer = reine Notiz).
                "rule": rule,
                "details": parts["details"],
            }
        )

    for line in markdown.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            if len(heading.group(1)) >= 2:
                title = heading.group(2).strip()
                section_date = parse_date(title)
                section = strip_date(title) if section_date else title
            continue
        if line.strip() in ("---", "***", "___"):  # Trennlinie
            flush()
            continue
        bullet = split_bullet(line)
        if bullet is not None:  # Top-Level-Bullet: neuer Eintrag
            flush()
            current = [bullet]
            continue
        if not line.strip():
            flush()
            continue
        if current is not None:  # Fortsetzungszeile (eingerückt, auch Sub-Bullets)
            cont = line.strip()
            if cont.startswith(("- ", "* ")):
                cont = cont[2:]
            current.append(cont)
    flush()

    entries.sort(key=lambda e: (e["date"] or "", e["order"]), reverse=True)
    return entries


def load_learnings(path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    """LEARNINGS.md lesen und parsen; keine Datei => []."""
    resolved = resolve_learnings_path(path)
    if resolved is None:
        return []
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError:
        return []
    return parse_learnings(text)


def load_next_id(path: str | os.PathLike[str] | None = None) -> str | None:
    """``next-id``-Marke der aufgelösten LEARNINGS-Datei; keine Datei/Marke => None."""
    resolved = resolve_learnings_path(path)
    if resolved is None:
        return None
    try:
        return parse_next_id(resolved.read_text(encoding="utf-8"))
    except OSError:
        return None
