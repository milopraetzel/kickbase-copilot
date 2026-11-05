"""Jev-Protokoll (intel/jev_journal.py): jede gestellte Jev-Frage bleibt nachlesbar.

Spec `docs/superpowers/specs/2026-10-01-jev-dashboard-design.md`. Das Protokoll ist reine
Nachvollziehbarkeit: Es darf nie ein Urteil, einen Plan oder einen Job kippen — deshalb
prüfen die Tests neben dem Inhalt vor allem das Schlucken jedes Fehlers.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kickbase_agent.db.models import Base, JevJudgment
from kickbase_agent.intel import jev_journal
from kickbase_agent.intel.jev_journal import (
    JevEntry,
    answer_fields,
    failure_entry,
    purge_old_judgments,
    write_entries,
)

NOW = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)


@pytest.fixture
def factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _entry(**over) -> JevEntry:
    base = dict(
        created_at=NOW, kind="rolle", purpose="kaufpruefung", status="ok",
        model="jev-1.13.0", batch_id="b1", batch_size=2, latency_ms=480,
        player_id="9605", player_name="Albert Grønbæk", club="Hamburger SV",
        subject="Bitter für den HSV: Top-Scorer Grönbaek fällt vorerst aus",
        text="Der HSV-Profi hat sich am Oberschenkel verletzt und fällt vorerst aus.",
        url="https://www.kicker.de/x", published_at=NOW - timedelta(hours=3),
        source="kicker", answer="fehlt", probability=0.99,
        probabilities={"fehlt": 0.99, "kein_hinweis": 0.01, "startet": 0.0, "bank": 0.0},
        confidence=0.99, raw={"role_p0": {"choice": "fehlt"}},
        rule="„fehlt“ ≥ 0,95 sperrt einen neuen Kauf", outcome="sperrt",
    )
    base.update(over)
    return JevEntry(**base)


def test_write_entries_stores_every_field(factory):
    assert write_entries([_entry()], session_factory=factory) == 1

    with factory() as s:
        row = s.scalars(select(JevJudgment)).one()
    assert (row.kind, row.purpose, row.status, row.outcome) == (
        "rolle", "kaufpruefung", "ok", "sperrt"
    )
    assert row.player_name == "Albert Grønbæk"
    assert row.probabilities["fehlt"] == 0.99
    assert row.raw == {"role_p0": {"choice": "fehlt"}}
    assert row.latency_ms == 480 and row.batch_size == 2
    # Gespeichert wird UTC ohne Zeitzone (SQLite), gelesen wieder als UTC.
    assert row.created_at.replace(tzinfo=UTC) == NOW


def test_long_texts_are_cut_to_the_column_width(factory):
    write_entries([_entry(subject="x" * 900, url="u" * 2000)], session_factory=factory)
    with factory() as s:
        row = s.scalars(select(JevJudgment)).one()
    assert len(row.subject) == jev_journal.SUBJECT_MAX
    assert len(row.url) == jev_journal.URL_MAX


def test_record_hands_the_built_rows_to_the_writer(monkeypatch):
    seen: list[JevEntry] = []
    monkeypatch.setattr(jev_journal, "_writer", seen.extend)

    jev_journal.record(lambda: [_entry(), _entry(player_id="1")])

    assert [e.player_id for e in seen] == ["9605", "1"]


def test_record_swallows_a_failing_writer(monkeypatch, caplog):
    def boom(rows):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(jev_journal, "_writer", boom)
    jev_journal.record(lambda: [_entry()])  # wirft nicht

    assert "Jev-Protokoll nicht geschrieben" in caplog.text


def test_record_swallows_a_failing_builder(monkeypatch, caplog):
    seen: list[JevEntry] = []
    monkeypatch.setattr(jev_journal, "_writer", seen.extend)

    def broken():
        raise KeyError("role_p3")

    jev_journal.record(broken)  # wirft nicht
    assert seen == []
    assert "Jev-Protokoll nicht geschrieben" in caplog.text


def test_record_writes_nothing_for_an_empty_batch(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(jev_journal, "_writer", calls.append)
    jev_journal.record(list)
    assert calls == []


def test_failure_entry_names_the_error_class_and_the_old_behaviour():
    entry = failure_entry(
        kind="rolle", purpose="kaufpruefung", batch_size=12, latency_ms=10_000,
        error=TimeoutError(), at=NOW, batch_id="b2",
    )
    assert (entry.status, entry.outcome, entry.error) == ("ausfall", "ausfall", "TimeoutError")
    assert entry.batch_size == 12 and entry.latency_ms == 10_000
    assert entry.player_id is None and entry.answer is None
    assert "vorher" in entry.rule  # ohne Urteil gilt das Verhalten von vorher


def test_answer_fields_keeps_only_valid_numbers():
    raw = SimpleNamespace(
        choice="fehlt", confidence=0.99,
        probabilities={"fehlt": 0.99, "bank": math.nan, "startet": 1.5, "kein_hinweis": 0.01},
    )
    probabilities, confidence, dumped = answer_fields(raw)
    assert probabilities == {"fehlt": 0.99, "kein_hinweis": 0.01}
    assert confidence == 0.99
    # Die Rohantwort bleibt lesbar, auch mit ungültigen Zahlen (als Text).
    assert dumped["choice"] == "fehlt"
    assert dumped["probabilities"]["bank"] == "nan"


def test_answer_fields_tolerates_a_missing_answer():
    assert answer_fields(None) == (None, None, None)


def test_purge_deletes_only_old_rows(factory):
    write_entries(
        [_entry(created_at=NOW - timedelta(days=91)), _entry(created_at=NOW - timedelta(days=5))],
        session_factory=factory,
    )
    with factory() as s:
        assert purge_old_judgments(NOW, session=s) == 1
        assert s.scalar(select(JevJudgment.created_at)).replace(tzinfo=UTC) == NOW - timedelta(
            days=5
        )


def test_purge_never_raises(caplog):
    class Broken:
        def query(self, *_a, **_k):
            raise RuntimeError("kaputt")

    assert purge_old_judgments(NOW, session=Broken()) == 0
    assert "Jev-Protokoll-Aufräumen übersprungen" in caplog.text


def test_a_locked_database_costs_only_a_short_wait(tmp_path, monkeypatch, caplog):
    """Review 01.10.: Hält der Snapshot (22:05) den Schreib-Lock, wartete der synchrone
    Commit bis zu 30 s (busy_timeout) im Event-Loop. Das Protokoll gibt schnell auf."""
    import sqlite3
    import time

    import kickbase_agent.config as cfg

    url = f"sqlite:///{tmp_path / 'journal.db'}"
    Base.metadata.create_all(create_engine(url))
    monkeypatch.setenv("DATABASE_URL", url)
    cfg._settings = None
    monkeypatch.setattr(jev_journal, "_writer", jev_journal.write_entries)
    lock = sqlite3.connect(tmp_path / "journal.db")
    lock.execute("BEGIN IMMEDIATE")  # fremder Schreiber hält die Sperre
    try:
        started = time.monotonic()
        jev_journal.record(lambda: [_entry()])
        elapsed = time.monotonic() - started
    finally:
        lock.rollback()
        lock.close()
    assert elapsed < jev_journal.JOURNAL_BUSY_TIMEOUT_S + 2
    assert "Jev-Protokoll nicht geschrieben" in caplog.text


def test_the_journal_writes_into_the_configured_database(tmp_path, monkeypatch):
    import kickbase_agent.config as cfg

    url = f"sqlite:///{tmp_path / 'journal.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    monkeypatch.setenv("DATABASE_URL", url)
    cfg._settings = None
    monkeypatch.setattr(jev_journal, "_writer", jev_journal.write_entries)

    jev_journal.record(lambda: [_entry()])

    with sessionmaker(bind=engine)() as s:
        assert s.scalars(select(JevJudgment.player_id)).all() == ["9605"]
