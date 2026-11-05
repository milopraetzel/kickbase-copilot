"""Einmal-Migration der Erkenntnisse: stabile IDs + Wissensart (Spec §8).

`LEARNINGS.md` ist gewachsen, ohne dass ein Eintrag sagen konnte, ob er sich bewährt
hat — es fehlte der Anker, an dem Belege hängen können. Dieses Modul vergibt ihn:

1. jeder Eintrag ohne ID bekommt in DATEIREIHENFOLGE eine (`L-001`, `L-002`, …),
2. die höchste vergebene Nummer landet als `<!-- next-id: L-070 -->` am Dateiende
   (damit zwei Sitzungen nicht dieselbe Nummer vergeben),
3. jeder neu migrierte Eintrag bekommt einen `**Art:**`-Vorschlag.

**Was hier NICHT passiert: Inhalte umschreiben.** Der Diff besteht ausschließlich aus
Bullet-Kopfzeilen (die ID rutscht hinter den Bindestrich), je einer neuen `**Art:**`-
Zeile und der `next-id`-Fußzeile. Kein Satz Fließtext wird angefasst — die Behauptung
gehört Emil und mir, nicht dem Skript.

Idempotent: Ein Eintrag, der schon eine ID trägt, bleibt unberührt — auch dann, wenn
seine Art fehlt oder von der Heuristik abweicht. Die Heuristik ist ein Vorschlag,
keine Wahrheit; deshalb gibt `kba learn-migrate` eine Übersicht aus, die VOR dem
Commit gelesen wird.

**Wo die Art-Zeile landet:** am ENDE des Eintrags, nicht direkt hinter der Kopfzeile
(anders als das Beispiel der Spec). Grund: Viele Einträge brechen ihren Fettteil über
zwei Zeilen um („- **Beschaffungs-Bilanz … (25.08.,\\n  Emils Beobachtung …):** …").
Eine dazwischengeschobene Zeile zerrisse dort die Überschrift für jeden Menschen, der
die Datei liest. Für den Parser ist beides gleichwertig.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kickbase_agent.knowledge.learnings import (
    ART_PANNE,
    NEXT_ID_RE,
    art_value,
    default_art,
    is_art_line,
    is_block_break,
    parse_next_id,
    section_title,
    split_bullet,
    split_id,
    title_and_date,
)

#: Titel-Wörter, die einen Vorfall verraten (Spec §8). Bewusst eine kurze Liste:
#: Sie darf falsch liegen, deshalb wird der Vorschlag vor dem Commit geprüft.
PANNE_WORDS = ("falle", "vorfall", "bug", "gefangen")

#: Marker-Zeile, die einen Eintrag als bereits eingeordnet ausweist.
_RULE_MARKER_RE = re.compile(r"\*\*Regel:?\*\*")
_ID_NUM_RE = re.compile(r"^L-(\d+)$")

#: Breite der laufenden Nummer („L-001"). Läuft der Vorrat über 999, wächst sie
#: einfach mit — die ID bleibt trotzdem eindeutig und sortierbar.
ID_WIDTH = 3


def format_id(number: int) -> str:
    return f"L-{number:0{ID_WIDTH}d}"


def id_number(learning_id: str) -> int | None:
    m = _ID_NUM_RE.match(learning_id.strip())
    return int(m.group(1)) if m else None


def propose_art(title: str, rule: str, section: str = "") -> str:
    """Art-Vorschlag: Vorfall am Titel (oder am Abschnitt) erkennen, sonst ableiten.

    „Panne" schlägt „Hypothese": Ein Eintrag über einen Vorfall ist auch dann keine
    Verhaltensannahme, wenn er eine Gegenmaßnahme als `Regel:` trägt — die Frage
    lautet dort „hält die Maßnahme?", nicht „stimmt die Annahme?".

    Der Abschnitt zählt nur, wenn er MIT einem Pannen-Wort BEGINNT („Fallen (einmal
    reingefallen reicht)"). Ein Datums-Abschnitt, in dessen Titel zufällig „…-Falle"
    vorkommt („23.08.2026 (Nacht) — … Doppel-Listing-Falle"), sammelt sonst sieben
    Empirie-Einträge ein, die klar Hypothesen sind.
    """
    if any(word in title.lower() for word in PANNE_WORDS):
        return ART_PANNE
    if section.strip().lower().startswith(PANNE_WORDS):
        return ART_PANNE
    return default_art(rule)


@dataclass(frozen=True)
class MigratedEntry:
    """Eine Zeile der Prüf-Übersicht."""

    learning_id: str
    title: str
    art: str
    line: int      #: 1-basierte Zeilennummer der Bullet-Kopfzeile
    assigned: bool  #: True = ID in diesem Lauf vergeben


@dataclass
class MigrationReport:
    entries: list[MigratedEntry] = field(default_factory=list)
    assigned: int = 0   #: neu vergebene IDs
    skipped: int = 0    #: Einträge, die schon eine ID trugen
    next_id: str = format_id(1)

    @property
    def total(self) -> int:
        return len(self.entries)


def _sections(lines: list[str]) -> list[str]:
    """Je Zeile der zuletzt gesehene ``##``-Abschnittstitel (für die Art-Heuristik)."""
    out: list[str] = []
    current = ""
    for line in lines:
        current = section_title(line) or current
        out.append(current)
    return out


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Bullet-Blöcke als (start, end_exklusiv) — dieselben Grenzen wie der Parser.

    Ein Block endet an einer Überschrift, einer Trennlinie, einer Leerzeile oder am
    nächsten Top-Level-Bullet. Wer hier abweicht, schreibt die Art-Zeile in den
    falschen Eintrag.
    """
    out: list[tuple[int, int]] = []
    start: int | None = None

    def close(end: int) -> None:
        nonlocal start
        if start is not None:
            out.append((start, end))
            start = None

    for i, line in enumerate(lines):
        if is_block_break(line):
            close(i)
            continue
        if split_bullet(line) is not None:
            close(i)
            start = i
            continue
    close(len(lines))
    return out


@dataclass(frozen=True)
class _Block:
    """Was die Migration über einen Eintrag wissen muss."""

    learning_id: str | None
    title: str
    has_rule: bool
    art: str | None   #: bereits eingetragene Art (None = noch keine)


def _block_facts(lines: list[str], start: int, end: int) -> _Block:
    head = split_bullet(lines[start]) or ""
    learning_id, head_rest = split_id(head.strip())
    body = [head_rest, *(ln.strip() for ln in lines[start + 1:end])]
    joined = " ".join(ln for ln in body if ln and not is_art_line(ln))
    declared = next((art_value(ln) for ln in body if is_art_line(ln)), None)
    return _Block(
        learning_id=learning_id,
        title=title_and_date(joined)[0],
        has_rule=bool(_RULE_MARKER_RE.search(joined)),
        art=declared,
    )


def _indent_of(lines: list[str], start: int, end: int) -> str:
    """Einrückung der Fortsetzungszeilen; einzeilige Einträge bekommen zwei Leerzeichen."""
    for ln in lines[start + 1:end]:
        if ln.strip():
            return ln[: len(ln) - len(ln.lstrip())] or "  "
    return "  "


def migrate(markdown: str) -> tuple[str, MigrationReport]:
    """Markdown -> (migriertes Markdown, Prüf-Übersicht). Reine Funktion, kein I/O."""
    lines = markdown.splitlines()
    blocks = _blocks(lines)

    sections = _sections(lines)
    facts = [_block_facts(lines, s, e) for s, e in blocks]
    used = [id_number(b.learning_id) for b in facts if b.learning_id]
    highest = max([n for n in used if n is not None] or [0])
    declared = parse_next_id(markdown)
    declared_num = id_number(declared) if declared else None
    counter = max(highest + 1, declared_num or 0, 1)

    report = MigrationReport()
    # Einfügungen sammeln und ERST danach anwenden — sonst verschieben sich die
    # Zeilennummern der noch offenen Blöcke.
    head_rewrites: dict[int, str] = {}
    art_inserts: dict[int, str] = {}

    for (start, end), block in zip(blocks, facts, strict=True):
        rule = "x" if block.has_rule else ""
        proposed = block.art or propose_art(block.title, rule, sections[start])
        if block.learning_id is not None:
            # Schon migriert: unberuehrt lassen — auch wenn die Heuristik heute
            # etwas anderes vorschlagen wuerde. Die Einordnung gehoert dem Menschen.
            report.skipped += 1
            report.entries.append(
                MigratedEntry(block.learning_id, block.title, proposed, start + 1, False)
            )
            continue
        learning_id = format_id(counter)
        counter += 1
        bullet = lines[start]
        marker_end = len(bullet) - len(bullet.lstrip()) + 2   # „- " bzw. „* "
        head_rewrites[start] = (
            f"{bullet[:marker_end]}[{learning_id}] {bullet[marker_end:].lstrip()}"
        )
        if block.art is None:
            art_inserts[end] = f"{_indent_of(lines, start, end)}**Art:** {proposed}"
        report.assigned += 1
        report.entries.append(MigratedEntry(learning_id, block.title, proposed, start + 1, True))

    out: list[str] = []
    for i, line in enumerate(lines):
        if i in art_inserts:
            out.append(art_inserts.pop(i))
        out.append(head_rewrites.get(i, line))
    for rest in art_inserts.values():   # Block, der die Datei beendet
        out.append(rest)

    report.next_id = format_id(max(counter, highest + 1))
    text = "\n".join(ln for ln in out if not NEXT_ID_RE.search(ln)).rstrip("\n")
    return f"{text}\n\n<!-- next-id: {report.next_id} -->\n", report
