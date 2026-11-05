"""`kba post-discord <datei> [--kind]`: posten + Berichte-Ablage (source cli:post-discord).

Der Discord-REST-Post ist gemockt (kein Netz); die Ablage schreibt in eine
In-Memory-`reports`-Tabelle hinter report_store.SessionLocal.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from kickbase_agent.cli import app
from kickbase_agent.cli import research as research_mod
from kickbase_agent.db.models import Base, Report
from kickbase_agent.notify import report_store

TEXT = "🧭 **Lage & Gebote** (Teil 1/2)\n• Wirtz: halten\n• Gebot auf Olise läuft"


@pytest.fixture(autouse=True)
def _legacy_delivery_policy(monkeypatch, _env):
    """Diese Versandtests aktivieren explizit Einzelmeldungen; Produktion bleibt still."""
    monkeypatch.setenv("DISCORD_DIGEST_ONLY", "0")


@pytest.fixture
def reports_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(report_store, "SessionLocal", sessionmaker(bind=engine, future=True))

    def rows() -> list[Report]:
        with Session(engine, future=True) as s:
            return list(s.execute(select(Report).order_by(Report.id)).scalars())

    return rows


@pytest.fixture
def posted(monkeypatch):
    """Discord-Versand abfangen: protokolliert Texte, meldet „zugestellt"."""
    calls: list[str] = []

    async def fake_post(text: str, *, kind: str = "info") -> bool:
        calls.append(text)
        return True

    monkeypatch.setattr(research_mod, "_post_discord", fake_post)
    return calls


def _file(tmp_path, text=TEXT):
    path = tmp_path / "recherche-teil1.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_post_discord_posts_file_and_stores_report_default_kind_recherche(
    tmp_path, posted, reports_db
):
    result = CliRunner().invoke(app, ["post-discord", str(_file(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "gepostet. Bericht #1 (recherche) gespeichert." in result.output
    assert posted == [TEXT]
    (r,) = reports_db()
    assert (r.kind, r.title, r.source) == ("recherche", "Lage & Gebote (Teil 1/2)",
                                           "cli:post-discord")
    assert r.body == TEXT
    assert r.meta == {"delivered": True, "chars": len(TEXT), "file": "recherche-teil1.md"}


def test_post_discord_kind_option_is_stored(tmp_path, posted, reports_db):
    result = CliRunner().invoke(
        app, ["post-discord", str(_file(tmp_path, "📝 SP1-Retro\nwas lief")), "--kind", "retro"]
    )
    assert result.exit_code == 0, result.output
    (r,) = reports_db()
    assert r.kind == "retro" and r.title == "SP1-Retro"


def test_post_discord_rejects_unknown_kind_without_posting(tmp_path, posted, reports_db):
    result = CliRunner().invoke(
        app, ["post-discord", str(_file(tmp_path)), "--kind", "quatsch"]
    )
    assert result.exit_code != 0
    assert "Unbekannte Berichtsart" in result.output
    assert posted == [] and reports_db() == []


def test_post_discord_empty_file_is_rejected(tmp_path, posted, reports_db):
    result = CliRunner().invoke(app, ["post-discord", str(_file(tmp_path, "  \n"))])
    assert result.exit_code != 0
    assert posted == [] and reports_db() == []


def test_post_discord_without_discord_config_echoes_and_stores_undelivered(
    tmp_path, monkeypatch, reports_db
):
    """Kein Bot-Token/Kanal: Text nach stdout (kein Netz), Bericht mit delivered=false."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "")
    import kickbase_agent.config as cfg

    cfg._settings = None
    result = CliRunner().invoke(app, ["post-discord", str(_file(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "Lage & Gebote" in result.output and "archiviert;" in result.output
    (r,) = reports_db()
    assert r.meta["delivered"] is False and r.kind == "recherche"


def test_post_discord_reports_when_storage_fails_but_post_succeeded(
    tmp_path, posted, monkeypatch
):
    """Tabelle fehlt: Post zählt, Ausgabe sagt ehrlich „NICHT gespeichert"."""
    bare = sessionmaker(bind=create_engine("sqlite:///:memory:", future=True), future=True)
    monkeypatch.setattr(report_store, "SessionLocal", bare)
    result = CliRunner().invoke(app, ["post-discord", str(_file(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "NICHT gespeichert" in result.output and posted == [TEXT]


def test_digest_policy_archives_research_without_sending(
    tmp_path, reports_db, monkeypatch, httpx_mock
):
    monkeypatch.setenv("DISCORD_DIGEST_ONLY", "1")
    import kickbase_agent.config as cfg

    cfg._settings = None
    result = CliRunner().invoke(app, ["post-discord", str(_file(tmp_path))])
    assert result.exit_code == 0, result.output
    assert httpx_mock.get_requests() == []
    (report,) = reports_db()
    assert report.body == TEXT
    assert report.meta["delivered"] is False
