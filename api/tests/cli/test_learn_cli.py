"""`kba learn-migrate` und `kba learn-evidence` (Spec §4.2, §8).

Der Schreibweg trägt die Zwei-DB-Lehre vom 24.08.: Steht eine Box-API bereit, geht
der Beleg DORT hin — sonst entstünde er in der Mac-DB und wäre für die Zentrale
unsichtbar. Genau das prüft der letzte Test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from kickbase_agent.cli import app
from kickbase_agent.cli import learn as cli_learn
from kickbase_agent.db.models import Base, LearningEvidence

runner = CliRunner()


@pytest.fixture
def session(monkeypatch) -> Session:
    """Echte `sessionmaker` auf EINER In-Memory-DB — kein stillgelegtes `close()`.

    Wichtig (Lehre 25.08.: „der Fake bildete die Absicht ab, nicht die Bibliothek"):
    Eine Fake-Session, die sich nicht schliessen laesst, verdeckt genau den Fehler,
    der beim ersten echten CLI-Lauf auftrat — nach `close()` ist die ORM-Instanz
    detached und jeder Attributzugriff wirft.
    """
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(cli_learn, "SessionLocal", factory)
    with factory() as s:
        yield s


# ----------------------------------------------------------------- learn-migrate


def test_learn_migrate_dry_run_leaves_the_file_untouched(tmp_path):
    md = tmp_path / "LEARNINGS.md"
    md.write_text("## X\n- **A (21.08.):** Text.\n  **Regel:** Tu dies.\n", encoding="utf-8")
    before = md.read_text(encoding="utf-8")
    result = runner.invoke(app, ["learn-migrate", "--path", str(md)])
    assert result.exit_code == 0
    assert "TROCKENLAUF" in result.stdout and "L-001" in result.stdout
    assert md.read_text(encoding="utf-8") == before


def test_learn_migrate_write_applies_and_is_idempotent(tmp_path):
    md = tmp_path / "LEARNINGS.md"
    md.write_text("## X\n- **A (21.08.):** Text.\n  **Regel:** Tu dies.\n", encoding="utf-8")
    assert runner.invoke(app, ["learn-migrate", "--path", str(md), "--write"]).exit_code == 0
    once = md.read_text(encoding="utf-8")
    assert "- [L-001] **A (21.08.):**" in once and "next-id: L-002" in once
    second = runner.invoke(app, ["learn-migrate", "--path", str(md), "--write"])
    assert "0 IDs neu vergeben" in second.stdout
    assert md.read_text(encoding="utf-8") == once


def test_learn_migrate_without_file_fails_loudly(tmp_path):
    result = runner.invoke(app, ["learn-migrate", "--path", str(tmp_path / "weg.md")])
    assert result.exit_code == 1 and "Keine LEARNINGS.md" in result.stdout


# ----------------------------------------------------------------- learn-evidence


def test_learn_evidence_writes_an_agent_row(session):
    result = runner.invoke(
        app,
        ["learn-evidence", "L-065", "confirm", "--note", "Ulrich abgelehnt", "--ref", "bid:34"],
    )
    assert result.exit_code == 0, result.stdout
    row = session.execute(select(LearningEvidence)).scalars().one()
    assert row.learning_id == "L-065" and row.source == "agent" and row.verdict == "confirm"
    assert "gespeichert" in result.stdout


def test_learn_evidence_reports_a_duplicate_instead_of_writing_twice(session):
    args = ["learn-evidence", "L-065", "confirm", "--note", "x", "--ref", "bid:34"]
    assert runner.invoke(app, args).exit_code == 0
    second = runner.invoke(app, args)
    assert second.exit_code == 0 and "schon belegt" in second.stdout
    assert len(session.execute(select(LearningEvidence)).scalars().all()) == 1


def test_learn_evidence_rejects_a_bad_verdict(session):
    result = runner.invoke(
        app, ["learn-evidence", "L-065", "vielleicht", "--note", "x", "--ref", "r:1"]
    )
    assert result.exit_code == 1
    assert session.execute(select(LearningEvidence)).scalars().first() is None


def test_learn_evidence_accepts_an_observed_at(session):
    result = runner.invoke(
        app,
        ["learn-evidence", "L-065", "refute", "--note", "x", "--ref", "r:9",
         "--at", "2026-08-20T18:30:00+02:00"],
    )
    assert result.exit_code == 0, result.stdout
    row = session.execute(select(LearningEvidence)).scalars().one()
    # SQLite gibt naive Zeitstempel zurueck — sie sind UTC (18:30 Berlin = 16:30 UTC).
    assert row.observed_at.replace(tzinfo=UTC) == datetime(2026, 8, 20, 16, 30, tzinfo=UTC)


def test_learn_evidence_goes_to_the_box_when_a_remote_api_is_configured(session, monkeypatch):
    """Zwei-DB-Lehre (24.08.): Mit Box-API schreibt die CLI dort, nicht lokal."""
    posted: list[dict] = []

    def fake_post(base, auth, payload):
        posted.append({"base": base, "payload": payload})
        return {"evidence": {"created": True}, "note": "Beleg gespeichert."}

    monkeypatch.setenv("KBA_REMOTE_API_URL", "https://kickbase.example/api")
    monkeypatch.setattr(cli_learn, "post_evidence_remote", fake_post)
    result = runner.invoke(
        app, ["learn-evidence", "L-065", "confirm", "--note", "x", "--ref", "bid:7"]
    )
    assert result.exit_code == 0, result.stdout
    assert posted and posted[0]["payload"]["learning_id"] == "L-065"
    assert "Box" in result.stdout
    # NICHTS in der lokalen DB — sonst gaebe es zwei Wahrheiten.
    assert session.execute(select(LearningEvidence)).scalars().first() is None
