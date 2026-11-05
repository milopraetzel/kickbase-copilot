"""Stufe 1 der Lern-Bilanz: stabile IDs + Wissensart im LEARNINGS-Parser und die Migration.

Spec: docs/superpowers/specs/2026-08-25-lern-bilanz-design.md §3.1, §8, §10.

Der Vertrag in einem Satz: Ein Eintrag darf eine ID (`[L-051]`) und eine Art
(`**Art:** hypothese|fakt|panne`) tragen; fehlt beides, bleibt der Eintrag gueltig
(kein Datenverlust waehrend der Migration) und die Art wird konservativ geraten.
"""

from __future__ import annotations

import pytest

from kickbase_agent.knowledge import learnings as lrn
from kickbase_agent.knowledge import migrate as mig

ID_MD = """# Betriebs-Lektionen

## Fallen
- [L-001] **Storno sperrt Re-Bid (21.08.):** Nach `cancel_bid` wirft JEDES neue Gebot 500.
  **Art:** hypothese
  **Kurz:** Ein Storno verbrennt den Angebots-Slot.
  **Regel:** Nie stornieren, um den Preis zu aendern.
- [L-002] **Feld `lo` (22.08.):** Lineup-Order 0-10.
  **Art:** fakt
- **Noch ohne ID (23.08.):** Bleibt in der Liste, aber ohne Bilanz.
  **Regel:** Trotzdem anwenden.

<!-- next-id: L-004 -->
"""


# ----------------------------------------------------------------- Parser


def test_parser_reads_id_and_art_without_leaking_into_text():
    by_title = {e["title"]: e for e in lrn.parse_learnings(ID_MD)}

    storno = by_title["Storno sperrt Re-Bid"]
    assert storno["id"] == "L-001"
    assert storno["art"] == lrn.ART_HYPOTHESE
    # Die ID ist ein Anker, kein Text: weder Titel noch Fliesstext duerfen sie tragen.
    assert "L-001" not in storno["text"]
    assert "L-001" not in storno["details"]
    assert "L-001" not in storno["title"]
    # Ebenso wenig darf die Art-Zeile in Details/Kurzfassung landen.
    assert "hypothese" not in storno["details"]
    assert storno["summary"] == "Ein Storno verbrennt den Angebots-Slot."
    assert storno["rule"] == "Nie stornieren, um den Preis zu aendern."

    assert by_title["Feld `lo`"]["id"] == "L-002"
    assert by_title["Feld `lo`"]["art"] == lrn.ART_FAKT


def test_entry_without_id_stays_valid_but_has_no_anchor():
    """Kein Datenverlust waehrend der Migration (Spec §3.1)."""
    entry = {e["title"]: e for e in lrn.parse_learnings(ID_MD)}["Noch ohne ID"]
    assert entry["id"] is None
    # `text` ist der Bestandsvertrag (MCP/Cockpit) und traegt die Marker weiter;
    # nur die Art-Zeile bleibt draussen, weil sie Einordnung ist, kein Inhalt.
    assert entry["text"] == "Bleibt in der Liste, aber ohne Bilanz. **Regel:** Trotzdem anwenden."
    assert entry["rule"] == "Trotzdem anwenden."


def test_art_defaults_conservatively_when_marker_is_missing():
    """Spec §2: mit `Regel:` ⇒ hypothese, ohne ⇒ fakt — ein Fakt bekommt nie eine Quote."""
    md = (
        "## Fallen\n"
        "- **Mit Regel (21.08.):** Text.\n"
        "  **Regel:** Tu dies.\n"
        "- **Ohne Regel (21.08.):** Nur eine Beobachtung.\n"
    )
    by_title = {e["title"]: e for e in lrn.parse_learnings(md)}
    assert by_title["Mit Regel"]["art"] == lrn.ART_HYPOTHESE
    assert by_title["Ohne Regel"]["art"] == lrn.ART_FAKT


def test_unknown_art_value_falls_back_instead_of_inventing_a_sort():
    md = "## X\n- **Kaputt (21.08.):** Text.\n  **Art:** quatsch\n  **Regel:** Tu dies.\n"
    assert lrn.parse_learnings(md)[0]["art"] == lrn.ART_HYPOTHESE


def test_next_id_comment_is_recognised():
    assert lrn.parse_next_id(ID_MD) == "L-004"
    assert lrn.parse_next_id("## Ohne Kommentar\n- **A (1.1.):** x\n") is None


def test_entry_contract_keys_are_stable():
    keys = {tuple(sorted(e)) for e in lrn.parse_learnings(ID_MD)}
    assert keys == {
        ("art", "date", "details", "id", "order", "rule", "section", "summary", "text", "title")
    }


# ----------------------------------------------------------------- Migration


RAW_MD = """# Betriebs-Lektionen

## API-Funde (verifiziert)
- Squad-`lo` = Lineup-Order 0-10 (echter Starter-Marker).

## Fallen (einmal reingefallen reicht)
- **Deploy-Falle (21.08.):** „hab deployed" heisst nicht deployed.
  **Kurz:** Deployt ist erst bewiesen, wenn der Code im Container steht.
  **Regel:** Nach jedem Deploy den Code-Inhalt greppen.

## Meta-Wissen
- **Manager-Listings (24.08.):** Gebote unter Marktwert werden abgelehnt.
  **Regel:** Nie unter Marktwert ankern.
"""


def test_migration_assigns_ids_in_file_order_and_writes_next_id():
    out, report = mig.migrate(RAW_MD)
    assert [r.learning_id for r in report.entries] == ["L-001", "L-002", "L-003"]
    assert "- [L-001] Squad-`lo` = Lineup-Order 0-10" in out
    assert "- [L-002] **Deploy-Falle (21.08.):**" in out
    assert out.rstrip().endswith("<!-- next-id: L-004 -->")
    assert report.assigned == 3


def test_migration_proposes_art_by_heuristic():
    _, report = mig.migrate(RAW_MD)
    by_id = {r.learning_id: r for r in report.entries}
    # ohne Regel ⇒ Nachschlagewerk
    assert by_id["L-001"].art == lrn.ART_FAKT
    # Titel mit „Falle" ⇒ Panne, auch wenn eine Regel dransteht
    assert by_id["L-002"].art == lrn.ART_PANNE
    # mit Regel, kein Pannen-Wort ⇒ Hypothese
    assert by_id["L-003"].art == lrn.ART_HYPOTHESE


def test_migration_only_adds_header_lines_and_never_rewrites_content():
    out, _ = mig.migrate(RAW_MD)
    added = [ln for ln in out.splitlines() if ln not in RAW_MD.splitlines()]
    # Neu sind ausschliesslich: drei Bullet-Kopfzeilen (jetzt mit ID), drei Art-Zeilen
    # und der next-id-Kommentar. Kein Satz Fliesstext aendert sich.
    assert all(
        ln.lstrip().startswith(("- [L-", "**Art:**", "<!-- next-id:")) for ln in added
    ), added
    removed = [ln for ln in RAW_MD.splitlines() if ln not in out.splitlines()]
    assert all(ln.lstrip().startswith("- ") for ln in removed), removed


def test_migration_is_idempotent():
    once, first = mig.migrate(RAW_MD)
    twice, second = mig.migrate(once)
    assert twice == once
    assert second.assigned == 0 and second.skipped == first.assigned


def test_migration_continues_numbering_after_existing_ids():
    md = (
        "## X\n"
        "- [L-007] **Alt (21.08.):** Text.\n"
        "  **Art:** panne\n"
        "- **Neu (22.08.):** Text.\n"
        "  **Regel:** Tu dies.\n"
        "\n<!-- next-id: L-008 -->\n"
    )
    out, report = mig.migrate(md)
    assert [r.learning_id for r in report.entries] == ["L-007", "L-008"]
    assert report.assigned == 1
    assert "- [L-008] **Neu (22.08.):**" in out
    assert out.count("<!-- next-id:") == 1
    assert out.rstrip().endswith("<!-- next-id: L-009 -->")
    # Der bereits migrierte Eintrag behaelt seine handgesetzte Art.
    assert {r.learning_id: r.art for r in report.entries}["L-007"] == lrn.ART_PANNE


def test_migrated_markdown_parses_back_into_the_same_entries():
    """Die Runde muss schliessen: Was die Migration schreibt, liest der Parser."""
    out, report = mig.migrate(RAW_MD)
    parsed = {e["id"]: e for e in lrn.parse_learnings(out)}
    assert set(parsed) == {"L-001", "L-002", "L-003"}
    assert {i: e["art"] for i, e in parsed.items()} == {
        r.learning_id: r.art for r in report.entries
    }
    # Titel und Regeln der Ausgangsdatei bleiben unveraendert.
    before = {e["title"]: e for e in lrn.parse_learnings(RAW_MD)}
    for entry in parsed.values():
        assert entry["title"] in before
        assert entry["rule"] == before[entry["title"]]["rule"]
        assert entry["text"] == before[entry["title"]]["text"]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Deploy-Falle (21.08.)", lrn.ART_PANNE),
        ("Vorfall im Executor", lrn.ART_PANNE),
        ("Zeitbomben-Bug gefangen", lrn.ART_PANNE),
        ("Manager-Listings verlangen 100 % MW", lrn.ART_HYPOTHESE),
    ],
)
def test_art_heuristic_flags_incidents_by_title(title, expected):
    assert mig.propose_art(title, rule="Tu dies.") == expected


def test_art_heuristic_uses_the_section_only_when_it_starts_with_a_panne_word():
    """„Fallen (einmal reingefallen reicht)" ist ein Pannen-Abschnitt — ein Datums-
    Abschnitt mit „…-Falle" im Titel dagegen sammelt Empirie, keine Vorfaelle."""
    assert mig.propose_art(
        "Scheduler", rule="Tu dies.", section="Fallen (einmal reingefallen reicht)"
    ) == lrn.ART_PANNE
    assert mig.propose_art(
        "Olise-Zuschlag", rule="Tu dies.",
        section="23.08.2026 (Nacht) — Zuschlags-Empirie, Doppel-Listing-Falle",
    ) == lrn.ART_HYPOTHESE


def test_real_learnings_file_is_migrated_and_consistent(monkeypatch):
    """Regression fuer die echte Datei: Nach der Migration hat JEDER Eintrag eine ID."""
    # Env-Falle (2x reingefallen, 24.+25.08.): Eine gesetzte KBA_LEARNINGS_PATH aus
    # Emils Umgebung wuerde diesen Test auf eine FREMDE Datei zeigen lassen.
    monkeypatch.delenv(lrn.ENV_VAR, raising=False)
    entries = lrn.load_learnings()
    if not entries:  # kein Repo-Checkout (Container) — dann gibt es nichts zu pruefen
        pytest.skip("keine LEARNINGS.md gefunden")
    missing = [e["title"] for e in entries if not e["id"]]
    assert missing == [], f"Eintraege ohne ID: {missing}"
    ids = [e["id"] for e in entries]
    assert len(set(ids)) == len(ids), "doppelte IDs"
    assert all(e["art"] in lrn.ARTEN for e in entries)
    path = lrn.resolve_learnings_path()
    assert path is not None
    next_id = lrn.parse_next_id(path.read_text(encoding="utf-8"))
    assert next_id is not None and next_id > max(ids)
