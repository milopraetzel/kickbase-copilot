"""Lern-Schicht: LEARNINGS.md-Parser, Parameter-Historie (JSON) + Ist-Werte, build_learning."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.knowledge import build_learning
from kickbase_agent.knowledge import learnings as lrn
from kickbase_agent.knowledge import parameters as prm

FIXTURE_MD = """# Betriebs-Lektionen

## Fallen (einmal reingefallen reicht)
- **Scheduler:** APScheduler awaited nur echte Coroutine-FUNKTIONEN — `lambda: coro()`
  meldet „executed", tut NICHTS.
- **Storno sperrt Re-Bid (21.08., teuer gelernt):** Nach `cancel_bid` wirft JEDES neue
  Gebot 500 — egal welcher Betrag.
  Zweite Fortsetzungszeile.

## Offizielle Spielregeln (verifiziert 20.08.26, help.kickbase.com)
- **EINE Deadline pro Spieltag** = offizieller Anpfiff des ERSTEN Spiels.

## Meta-Wissen
- **Delegation „kompletter Account" (Emil, 23.08. ~12:20):** Der Orchestrator entscheidet.
- **Steuerzentrale (23.08.):** Vorhaben statt Vorab-Freigabe.
- Ausgeglichene Spiele sind NEUTRAL (0,5) — win_prob nie roh als Score.
"""


# ----------------------------------------------------------------- Learnings


def test_parse_learnings_dates_titles_text_and_order():
    entries = lrn.parse_learnings(FIXTURE_MD)
    assert [e["title"] for e in entries] == [
        "Steuerzentrale",
        "Delegation „kompletter Account\" (Emil)",
        "Storno sperrt Re-Bid (teuer gelernt)",
        "EINE Deadline pro Spieltag",
        "Ausgeglichene Spiele sind NEUTRAL (0,5) — win_prob nie roh als Score",
        "Scheduler",
    ]
    by_title = {e["title"]: e for e in entries}
    # Datumsvarianten: „(23.08.)", „(Emil, 23.08. ~12:20)", „(21.08., teuer gelernt)"
    assert by_title["Steuerzentrale"]["date"] == "2026-08-23"
    assert by_title["Delegation „kompletter Account\" (Emil)"]["date"] == "2026-08-23"
    assert by_title["Storno sperrt Re-Bid (teuer gelernt)"]["date"] == "2026-08-21"
    # „20.08.26" aus der Abschnitts-Überschrift wird vererbt
    deadline = by_title["EINE Deadline pro Spieltag"]
    assert deadline["date"] == "2026-08-20"
    assert deadline["section"] == "Offizielle Spielregeln (verifiziert, help.kickbase.com)"
    assert deadline["text"] == "= offizieller Anpfiff des ERSTEN Spiels."
    # ohne Datum: None, Dateireihenfolge rückwärts, ganz hinten
    assert by_title["Scheduler"]["date"] is None
    assert by_title["Ausgeglichene Spiele sind NEUTRAL (0,5) — win_prob nie roh als Score"][
        "date"] is None
    assert by_title["Scheduler"]["section"] == "Fallen (einmal reingefallen reicht)"
    # mehrzeilige Bullets werden zusammengefügt
    assert by_title["Storno sperrt Re-Bid (teuer gelernt)"]["text"] == (
        "Nach `cancel_bid` wirft JEDES neue Gebot 500 — egal welcher Betrag. "
        "Zweite Fortsetzungszeile."
    )
    assert by_title["Scheduler"]["text"].startswith("APScheduler awaited nur echte")
    # order = Position in der Datei (0-basiert)
    assert by_title["Scheduler"]["order"] == 0
    assert by_title["Ausgeglichene Spiele sind NEUTRAL (0,5) — win_prob nie roh als Score"][
        "order"] == 5
    # gleiches Datum (23.08.): später in der Datei => zuerst
    assert [e["order"] for e in entries[:2]] == [4, 3]
    assert {tuple(sorted(e)) for e in entries} == {
        # seit 26.08. (Lern-Bilanz): stabile ID + Wissensart, beide optional im Markdown
        ("art", "date", "details", "id", "order", "rule", "section", "summary", "text", "title")
    }
    assert all(e["id"] is None for e in entries)          # Fixture ohne IDs
    assert by_title["Scheduler"]["art"] == lrn.ART_FAKT   # ohne Regel: Nachschlagewerk


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Steuerzentrale (23.08.)", "2026-08-23"),
        ("Storno sperrt Re-Bid (21.08., teuer gelernt)", "2026-08-21"),
        ("verifiziert 20.08.26, help.kickbase.com", "2026-08-20"),
        ("Stand 05.09.2026", "2026-09-05"),
        ("Emil, 23.08. ~12:20", "2026-08-23"),
        ("Score 0.55. und 1,22M", None),        # keine Datumsangabe
        ("Underpay-Empirie: 81 % verliert", None),
        ("(99.99.)", None),                      # ungültig
    ],
)
def test_parse_date_variants(text, expected):
    assert lrn.parse_date(text) == expected


def test_load_learnings_missing_file_env_and_explicit_path(tmp_path: Path, monkeypatch):
    missing = tmp_path / "nope.md"
    assert lrn.load_learnings(str(missing)) == []            # explizit + fehlt => []
    md = tmp_path / "LEARNINGS.md"
    md.write_text(FIXTURE_MD, encoding="utf-8")
    assert len(lrn.load_learnings(md)) == 6
    # Env-Variable schlägt die Default-Pfade
    monkeypatch.setenv(lrn.ENV_VAR, str(md))
    assert lrn.resolve_learnings_path() == md
    assert len(lrn.load_learnings()) == 6
    # ohne Env: Repo-Datei (existiert im Checkout) — mindestens ein Eintrag, Vertragsfelder
    monkeypatch.delenv(lrn.ENV_VAR, raising=False)
    if lrn.REPO_LEARNINGS.is_file():
        repo_entries = lrn.load_learnings()
        assert repo_entries and all(
            {"date", "title", "text", "order"} <= set(e) for e in repo_entries
        )
        assert repo_entries[0]["date"] is not None   # neueste zuerst


def test_load_learnings_without_any_file_returns_empty(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(lrn.ENV_VAR, str(tmp_path / "missing.md"))
    monkeypatch.setattr(lrn, "FALLBACK_PATHS", (tmp_path / "a.md", tmp_path / "b.md"))
    assert lrn.load_learnings() == []


# ----------------------------------------------------------------- Parameter


def test_parameter_history_json_is_valid_and_sorted_newest_first():
    raw = json.loads(
        (resources.files("kickbase_agent.knowledge") / prm.HISTORY_FILE).read_text("utf-8")
    )
    assert isinstance(raw["entries"], list) and len(raw["entries"]) >= 10
    hist = prm.load_parameter_history()
    assert len(hist) == len(raw["entries"])
    assert all(set(e) == set(prm.HISTORY_KEYS) for e in hist)
    dates = [e["date"] for e in hist]
    assert dates == sorted(dates, reverse=True)
    assert all(len(e["commit"]) >= 7 and e["reason"] for e in hist)     # nur Belegtes
    # Pflicht-Einträge (Emils Entscheidungen + belegte Fixes)
    idx = {(e["parameter"], e["old"], e["new"]): e for e in hist}
    assert idx[("max_bid_pct_of_budget", "0.25", "0.60")]["date"] == "2026-08-20"
    assert idx[("daily_buy_cap", "3", "5")]["commit"] == "da06d03"
    assert idx[("daily_sell_cap", "3", "5")]["date"] == "2026-08-23"
    assert idx[("bid_overpay_fraction", "0.05", "0.08")]["commit"] == "da06d03"
    assert idx[("FEED_MAX_ITEMS", "100", "1000")]["date"] == "2026-08-21"
    assert any(e["parameter"] == "max_bid_premium" and e["date"] == "2026-08-21" for e in hist)
    # 26.08.: beide Befunde tragen einen vollstaendigen Decision-Record
    neu = {e["parameter"]: e for e in hist if e["date"] == "2026-08-26"}
    assert set(neu) == {"max_bid_premium", "bid_overpay_need", "protected_player_ids"}
    assert all(e["expect"] and e["review"] and e["commit"] for e in neu.values())
    assert "Ljubicic" in neu["max_bid_premium"]["reason"]
    assert "157 %" in neu["max_bid_premium"]["reason"]          # der Beleg, nicht nur die Meinung
    assert "1.35" in neu["max_bid_premium"]["new"]
    assert "leer" in (neu["protected_player_ids"]["old"] or "")
    # neu eingeführte Parameter tragen old=None
    assert idx[("bid_overpay_conviction", None, "0.09")]["date"] == "2026-08-20"


def test_current_parameters_contains_guardrail_contract_keys_and_history_names():
    cur = prm.current_parameters()
    for key in prm.GUARDRAIL_KEYS:
        assert cur[key] == getattr(DEFAULT_GUARDRAILS, key)
    assert cur["daily_buy_cap"] == DEFAULT_GUARDRAILS.daily_buy_cap
    assert cur["bid_overpay_fraction"] == 0.08
    assert cur["max_bid_pct_of_budget"] == 0.70 and cur["approval_threshold"] == 5_000_000
    assert cur["league_trade_limit_per_half"] == 200
    assert cur["FEED_MAX_ITEMS"] == 1000 and cur["PROPOSAL_TTL"] == "45 min"
    # jeder Parameter der Historie hat einen Ist-Wert unter demselben Namen
    missing = {e["parameter"] for e in prm.load_parameter_history()} - set(cur)
    assert missing == set()
    assert prm.open_calibrations() and all(isinstance(s, str) for s in prm.open_calibrations())
    assert any("Backtest" in s for s in prm.open_calibrations())


def test_protected_players_are_constitution_not_a_tunable_parameter():
    """Verfassung ≠ Stellschraube (26.08.): Die Schutzliste ist sichtbar, aber
    sie steht in KEINEM der Parameter-Verträge, an denen der Agent dreht —
    sonst könnte er sich per Decision-Record selbst die Erlaubnis schreiben,
    Olise zu verkaufen."""
    assert "protected_player_ids" in prm.CONSTITUTION_KEYS
    assert "protected_player_ids" not in prm.GUARDRAIL_KEYS
    assert "protected_player_ids" not in prm._EXTRA_GUARDRAIL_KEYS
    cur = prm.current_parameters()
    # sichtbar — und als Text, nicht als Wert zum Drehen
    assert "8329" in cur["protected_player_ids"]
    assert "PROTECTED_PLAYER_IDS" in cur["protected_player_ids"]


def test_staggered_bid_cap_is_part_of_the_parameter_contract():
    cur = prm.current_parameters()
    assert cur["max_bid_premium"] == 1.10 and cur["max_bid_premium_need"] == 1.35
    assert cur["bid_overpay_need"] == 0.20


# ----------------------------------------------------------------- Gesamtbild


def test_build_learning_assembles_the_contract_on_empty_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    with Session(engine, future=True) as session:
        out = build_learning(session, now=now)
    assert set(out) == {
        "now", "evaluation", "learnings", "parameters", "open_calibrations",
        # seit 26.08. (Lern-Bilanz): Wochen-Kopf, Korrektur-Liste und
        # Emils Tabelle der Selbstverbesserungen
        "bilanz", "to_fix", "selbstverbesserungen",
    }
    assert out["to_fix"] == []                       # leere DB: nichts zu korrigieren
    assert out["bilanz"]["confirmed_week"] == 0 and out["bilanz"]["refuted_week"] == 0
    assert out["now"] == now.isoformat()
    assert out["evaluation"]["generated_at"] == now.isoformat()
    assert out["evaluation"]["coverage"]["proposals_total"] == 0
    assert set(out["parameters"]) == {"history", "current"}
    assert isinstance(out["learnings"], list)
    assert isinstance(out["open_calibrations"], list) and out["open_calibrations"]
    json.dumps(out)  # komplett JSON-serialisierbar


# --- Kurzfassung + Regel (25.08., Emils Wunsch: echte Learnings, menschlich zusammengefasst)


MARKER_MD = """## Fallen

- **Storno sperrt Re-Bid (21.08.):** Nach `cancel_bid` wirft JEDES neue Gebot 500 —
  egal welcher Betrag, mindestens stundenlang.
  **Kurz:** Ein Storno verbrennt den Angebots-Slot fuer dieses Listing.
  **Regel:** Nie stornieren, um den Preis zu aendern.
- **Ohne Marker (22.08.):** Erster Satz. Zweiter Satz mit `Code` und **fett**. Dritter Satz.
"""


def test_markers_become_summary_and_rule():
    by = {e["title"]: e for e in lrn.parse_learnings(MARKER_MD)}
    storno = by["Storno sperrt Re-Bid"]
    assert storno["summary"] == "Ein Storno verbrennt den Angebots-Slot fuer dieses Listing."
    assert storno["rule"] == "Nie stornieren, um den Preis zu aendern."
    # `details` traegt den Fliesstext OHNE die Marker-Abschnitte …
    assert "Storno verbrennt" not in storno["details"]
    assert storno["details"].startswith("Nach `cancel_bid`")
    # … `text` bleibt vollstaendig (Bestandsvertrag: MCP evaluation, Alt-Anzeigen).
    assert "**Regel:**" in storno["text"]


def test_summary_is_derived_when_marker_missing():
    """Ohne `Kurz` entsteht die Zusammenfassung aus den ersten Saetzen — ohne Markdown."""
    by = {e["title"]: e for e in lrn.parse_learnings(MARKER_MD)}
    plain = by["Ohne Marker"]
    assert plain["rule"] == ""                      # keine Regel = reine Notiz
    assert plain["summary"].startswith("Erster Satz.")
    assert "`" not in plain["summary"] and "**" not in plain["summary"]
    assert len(plain["summary"]) <= lrn.SUMMARY_MAX


def test_derive_summary_cuts_at_word_boundary():
    long_text = "Wort " * 200
    out = lrn.derive_summary(long_text)
    assert len(out) <= lrn.SUMMARY_MAX and out.endswith("…")


def test_derive_summary_takes_second_sentence_when_first_is_short():
    out = lrn.derive_summary("Kurz. Der zweite Satz traegt die eigentliche Aussage mit Inhalt.")
    assert out.startswith("Kurz.") and "zweite Satz" in out


def test_real_learnings_carry_rules():
    """Die gepflegte Datei selbst: die grosse Mehrheit der Eintraege muss eine Regel
    tragen — genau das ist der Teil, den der Agent im Betrieb anwendet."""
    entries = lrn.load_learnings()
    if not entries:                                  # Datei fehlt (fremde Umgebung)
        pytest.skip("LEARNINGS.md nicht gefunden")
    with_rule = [e for e in entries if e["rule"]]
    assert len(with_rule) >= int(len(entries) * 0.8)
    assert all(len(e["summary"]) <= lrn.SUMMARY_MAX for e in entries)


def test_strip_date_removes_commit_hash_and_paren_artifacts():
    """Titel sind Menschen-Text: Commit-Hashes und Klammer-Reste der Datums-Entfernung
    gehoeren nicht hinein (25.08.)."""
    assert lrn.strip_date("Haustuer zu (25.08., 6f69a6b)") == "Haustuer zu"
    assert lrn.strip_date("Externes Review v0.4.0 (23.08. abends) — vier P0") == (
        "Externes Review v0.4.0 (abends) — vier P0"
    )
    # Klammern mit echtem Inhalt bleiben, und `print()` ist kein Artefakt
    assert lrn.strip_date("Storno sperrt Re-Bid (21.08., teuer gelernt)") == (
        "Storno sperrt Re-Bid (teuer gelernt)"
    )
    assert lrn.strip_date("print() im Container ist gepuffert") == (
        "print() im Container ist gepuffert"
    )


def test_marker_mentioned_mid_sentence_does_not_split_entry():
    """Ein Eintrag, der die Marker BESCHREIBT, darf nicht daran zerfallen — nur ein
    Marker am Zeilenanfang zaehlt (25.08., am eigenen Meta-Eintrag aufgefallen)."""
    md = """## Fallen

- **Wissen braucht zwei Sichten (25.08.):** Jeder Eintrag traegt seitdem **Kurz:**
  fuer Menschen und **Regel:** fuer den Agenten.
  **Kurz:** Lektionen brauchen Zusammenfassung und Regelsatz.
  **Regel:** Jede neue Lektion mit beiden Zeilen schreiben.
"""
    entry = lrn.parse_learnings(md)[0]
    assert entry["summary"] == "Lektionen brauchen Zusammenfassung und Regelsatz."
    assert entry["rule"] == "Jede neue Lektion mit beiden Zeilen schreiben."
    assert "fuer Menschen und" in entry["details"]
