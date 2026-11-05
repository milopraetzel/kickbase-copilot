"""Berichte-Ablage (notify/report_store): Heuristiken + Persistenz.

`infer_kind`/`title_from` raten Art und Titel aus der ersten Zeile eines Discord-Posts;
`save_report` schreibt in `reports` und darf den Versand NIE stören (DB-Fehler → None
+ Log, keine Exception).
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from kickbase_agent.db.models import Base, Report
from kickbase_agent.notify import report_store
from kickbase_agent.notify.report_store import (
    MIN_REPORT_CHARS,
    REPORT_KINDS,
    infer_kind,
    is_error_notice,
    normalize_kind,
    save_report,
    title_from,
    worth_storing,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


# ------------------------------------------------------------------ infer_kind


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("📊 BASE · Tagesbriefing\n**`3/8`** Platz", "briefing"),
        ("🧠 **KI-Recherche** — Geheimtipps & Risiken", "recherche"),
        ("🧭 **Lage & Gebote** (Teil 1/2)", "recherche"),
        ("Teil 2: Gebote & Plan", "recherche"),
        ("🤖 **Schatten-Scan** — was der Automat (Phase 2) heute getan HÄTTE", "schatten"),
        # Auto-Trading vor Schatten: „Auto-Trading (Schatten)" ist ein Auto-Trading-Report
        ("🤖 **Auto-Trading (Schatten)** — hätte ausgeführt:", "auto_trading"),
        ("🤖 **Auto-Trading (LIVE)** — ausgeführt:", "auto_trading"),
        ("🚫 **Auto-Trading blockiert** — 200er-Transferzählung gesättigt", "auto_trading"),
        ("**Steuerzentrale** — Planner: 3 neu, 0 aktualisiert, 0 superseded", "planner"),
        ("**Steuerzentrale** — Executor: 1 ausgeführt, 0 fehlgeschlagen", "executor"),
        ("Vorhaben für heute Abend", "planner"),
        ("Steuerzentrale: 2 Vorhaben ausgeführt", "executor"),
        ("📝 SP1-Retro 30.08. — was lief, was nicht", "retro"),
        ("⏫ **Gebots-Eskalation empfohlen** — Listing läuft bald ab", "bid_watch"),
        ("💎 **Schnäppchen-Radar** — Listings deutlich unter Marktwert", "bid_watch"),
        ("🎯 **Sniper-Fenster** — Kickbase-Listings laufen bald ab", "bid_watch"),
        # „ausgeführt" OHNE Steuerzentrale-Kontext bleibt info (Anpfiff-Wächter)
        ("🚨 **T-15 NOTBREMSE ausgeführt** — verkauft: X.", "info"),
        ("🛡️ **T-120 Gebots-Storno ausgeführt**: X — Zuschläge", "info"),
        ("🔕 Puls-Test: aktuell kein Handlungsbedarf (leise).", "info"),
        ("", "info"),
        ("\n\n  \n", "info"),
    ],
)
def test_infer_kind_from_first_line(text, kind):
    assert infer_kind(text) == kind


def test_infer_kind_default_is_configurable_and_only_first_line_counts():
    assert infer_kind("irgendwas", default="sonstiges") == "sonstiges"
    # Marker in späteren Zeilen zählen nicht (die erste Zeile entscheidet)
    assert infer_kind("Hallo\n🧠 KI-Recherche") == "info"
    assert infer_kind("\n\n  📊 Tagesbriefing") == "briefing"  # erste NICHT-LEERE Zeile


def test_normalize_kind_maps_unknown_to_sonstiges_and_empty_to_default():
    assert normalize_kind("briefing") == "briefing"
    assert normalize_kind(" Briefing ") == "briefing"
    assert normalize_kind("foo") == "sonstiges"
    assert normalize_kind("") == "info"
    assert normalize_kind(None, default="recherche") == "recherche"
    assert set(REPORT_KINDS) >= {"briefing", "recherche", "schatten", "auto_trading",
                                 "planner", "executor", "retro", "bid_watch", "info",
                                 "sonstiges"}


# ------------------------------------------------------------------ title_from


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("📊 BASE · Tagesbriefing\nZeile 2", "BASE · Tagesbriefing"),
        ("🤖 **Schatten-Scan** — was der Automat heute getan HÄTTE",
         "Schatten-Scan — was der Automat heute getan HÄTTE"),
        ("**Steuerzentrale** — Planner: 3 neu", "Steuerzentrale — Planner: 3 neu"),
        ("🧭 **Lage & Gebote** (Teil 1/2)", "Lage & Gebote (Teil 1/2)"),
        ("-# Hinweis mit `code` und __unterstrichen__", "Hinweis mit code und unterstrichen"),
        # Unterstriche in Bezeichnern bleiben (kein Markdown)
        ("AGENT_AUTO_INTENTS=1: Executor schreibt live.",
         "AGENT_AUTO_INTENTS=1: Executor schreibt live."),
        ("\n\n   \n🎁 Prämie eingesammelt ✓", "Prämie eingesammelt"),
        ("", ""),
    ],
)
def test_title_from_strips_markdown_and_emoji(text, title):
    assert title_from(text) == title


def test_title_from_caps_length_with_ellipsis():
    long = "Sehr langer Titel " * 20
    title = title_from(long)
    assert len(title) == 120 and title.endswith("…")
    assert title_from(long, max_chars=30).endswith("…") and len(title_from(long, 30)) == 30
    assert title_from("x" * 120) == "x" * 120  # exakt am Limit: ungekürzt


# ------------------------------------------------------------------ worth_storing


def test_error_notices_and_short_infos_are_not_worth_storing():
    assert is_error_notice("⚠️ Briefing-Fehler: ValueError: kaputt")
    assert is_error_notice("⚠️ Intent-Executor-Fehler: RuntimeError: boom")
    assert is_error_notice("⚠️ Puls 09:00-Fehler: TimeoutError: x")
    assert not is_error_notice("🚨 **Auto-Trading ABGEBROCHEN** — bid X endete als 'failed'")
    assert not worth_storing("⚠️ Briefing-Fehler: ValueError: " + "x" * 200)
    assert not worth_storing("🎁 Prämie eingesammelt ✓")
    assert not worth_storing("x" * (MIN_REPORT_CHARS - 1))
    assert worth_storing("x" * MIN_REPORT_CHARS)
    assert worth_storing("🤖 **Schatten-Scan** — Report\n" + "• Zeile\n" * 30)


# ------------------------------------------------------------------ save_report


def test_save_report_writes_row_and_returns_id(session):
    body = "🧠 **KI-Recherche** — Geheimtipps & Risiken\n💎 Tipps\n• **Wirtz** — Form"
    rid = save_report("recherche", None, body, "cli:post-discord",
                      meta={"delivered": True, "chars": len(body)}, session=session)
    assert isinstance(rid, int)
    row = session.get(Report, rid)
    assert row.kind == "recherche"
    assert row.title == "KI-Recherche — Geheimtipps & Risiken"  # aus der ersten Zeile
    assert row.body == body
    assert row.source == "cli:post-discord"
    assert row.meta == {"delivered": True, "chars": len(body)}
    assert row.created_at is not None


def test_save_report_uses_explicit_title_and_normalizes_unknown_kind(session):
    rid = save_report("fremd", "  Mein Titel  ", "Text", "worker:x", session=session)
    row = session.get(Report, rid)
    assert row.kind == "sonstiges" and row.title == "Mein Titel"
    # leerer kind → info; leerer Text ohne Titel → kind als Titel
    rid2 = save_report("", None, "   ", "mcp:discord_post", session=session)
    row2 = session.get(Report, rid2)
    assert row2.kind == "info" and row2.title == "info" and row2.body == ""


def test_save_report_opens_own_session_when_none_given(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(report_store, "SessionLocal", sessionmaker(bind=engine, future=True))

    rid = save_report("briefing", "Tagesbriefing", "Body " * 10, "worker:briefing")

    assert rid == 1
    with Session(engine, future=True) as s:
        rows = list(s.execute(select(Report)).scalars())
    assert [(r.id, r.kind, r.title) for r in rows] == [(1, "briefing", "Tagesbriefing")]


def test_save_report_never_raises_on_db_error(monkeypatch, caplog):
    """Tabelle fehlt (kein Alembic-Upgrade): None + Warnung — der Versand bleibt unberührt."""
    engine = create_engine("sqlite:///:memory:", future=True)  # KEIN create_all
    monkeypatch.setattr(report_store, "SessionLocal", sessionmaker(bind=engine, future=True))
    caplog.set_level(logging.WARNING, logger="kickbase_agent.notify.report_store")

    assert save_report("info", "t", "x" * 200, "worker:info") is None

    assert any("konnte nicht gespeichert werden" in r.getMessage() for r in caplog.records)


def test_save_report_with_broken_passed_session_rolls_back_and_returns_none(session):
    """Übergebene Session: Fehler → rollback, None; die Session bleibt benutzbar."""
    Report.__table__.drop(session.get_bind())
    assert save_report("info", "t", "x" * 200, "worker:info", session=session) is None
    Base.metadata.create_all(session.get_bind())
    assert isinstance(save_report("info", "t", "x" * 200, "worker:info", session=session), int)


def test_morgenbericht_is_a_known_kind_but_no_longer_passes_the_digest():
    """Emil 22.09.2026: eigene Berichtsart für den täglichen Morgenbericht. Seit
    23.09.2026 („nur einmal am Tag eine Zusammenfassung") lässt der Digest über
    send_info/discord_post nur noch Blocker durch."""
    from kickbase_agent.notify.report_store import discord_delivery_allowed

    assert "morgenbericht" in REPORT_KINDS
    assert discord_delivery_allowed("morgenbericht", True) is False
    assert discord_delivery_allowed("morgenbericht", False) is True
    assert discord_delivery_allowed("blocker", True) is True
    assert discord_delivery_allowed("briefing", True) is False
    assert discord_delivery_allowed("recherche", False) is True
