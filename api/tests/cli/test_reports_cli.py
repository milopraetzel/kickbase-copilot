"""`kba report-add` (Box-Spiegel der Berichte-Ablage) + `kba reports` (Liste)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from kickbase_agent.cli import app
from kickbase_agent.cli import reports as reports_mod
from kickbase_agent.db.models import Base, Report
from kickbase_agent.notify import report_store

BODY = "-# Hinweis: 🧭 **Lage & Gebote**\n• Wirtz halten\n• Gebot auf Olise läuft"


@pytest.fixture
def reports_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(report_store, "SessionLocal", factory)
    monkeypatch.setattr(reports_mod, "SessionLocal", factory)

    def rows() -> list[Report]:
        with Session(engine, future=True) as s:
            return list(s.execute(select(Report).order_by(Report.id)).scalars())

    return rows


def test_report_add_stores_body_with_kind_source_meta(reports_db):
    result = CliRunner().invoke(
        app,
        ["report-add", "--kind", "recherche", "--source", "mcp:discord_post",
         "--meta", '{"delivered": true, "local_report_id": 7}', "--", BODY],
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Bericht #1 (recherche, mcp:discord_post) gespeichert."
    (r,) = reports_db()
    assert (r.kind, r.source, r.body) == ("recherche", "mcp:discord_post", BODY)
    assert r.title == "Hinweis: Lage & Gebote"  # `--` schützt den führenden Bindestrich
    assert r.meta == {"delivered": True, "local_report_id": 7}


def test_report_add_defaults_and_explicit_title(reports_db):
    result = CliRunner().invoke(app, ["report-add", "--title", "Mein Titel", "Text"])
    assert result.exit_code == 0, result.output
    (r,) = reports_db()
    assert (r.kind, r.source, r.title, r.meta) == ("info", "cli:report-add", "Mein Titel", None)


@pytest.mark.parametrize(
    "argv",
    [
        ["report-add", "--kind", "quatsch", "Text"],
        ["report-add", "--meta", "kein json", "Text"],
        ["report-add", "--meta", "[1, 2]", "Text"],
        ["report-add", "   "],
    ],
)
def test_report_add_rejects_bad_input_without_storing(reports_db, argv):
    result = CliRunner().invoke(app, argv)
    assert result.exit_code != 0
    assert reports_db() == []


def test_report_add_exit_1_when_db_not_ready(monkeypatch):
    bare = sessionmaker(bind=create_engine("sqlite:///:memory:", future=True), future=True)
    monkeypatch.setattr(report_store, "SessionLocal", bare)
    result = CliRunner().invoke(app, ["report-add", "Text"])
    assert result.exit_code == 1 and "NICHT gespeichert" in result.output


def test_reports_lists_newest_first_with_filter_and_flags(reports_db):
    for kind, title, delivered in (
        ("briefing", "Tagesbriefing", True),
        ("recherche", "Lage & Gebote", False),
        ("schatten", "Schatten-Scan", True),
    ):
        report_store.save_report(kind, title, "x", "worker:" + kind, meta={"delivered": delivered})

    result = CliRunner().invoke(app, ["reports"])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert [ln.split()[0] for ln in lines] == ["#3", "#2", "#1"]
    assert "Lage & Gebote" in lines[1] and "(nicht zugestellt)" in lines[1]
    assert "(nicht zugestellt)" not in lines[0]

    result = CliRunner().invoke(app, ["reports", "--kind", "briefing", "-n", "5"])
    assert result.exit_code == 0
    assert [ln.split()[0] for ln in result.output.splitlines() if ln.strip()] == ["#1"]

    result = CliRunner().invoke(app, ["reports", "--kind", "retro"])
    assert "Keine Berichte." in result.output
