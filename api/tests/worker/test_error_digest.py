"""Fehler bündeln statt fluten (Emil 29.08.2026).

Der reale Anlass: In der Nacht auf den 29.08. sperrte Kickbase die API während
der Spieltags-Auswertung. Von 23:23 bis 06:25 kamen 403er, und jeder betroffene
Job meldete einzeln nach Discord — elf Nachrichten für eine Ursache, jede mit
angehängtem MDN-Link, der in Discord zur Linkvorschau aufblies.

Was diese Tests festhalten, ist die Grenze: leiser, aber nicht unsichtbar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kickbase_agent.worker.error_digest import (
    FehlerBuendel,
    fingerabdruck,
    saubere_meldung,
)

NOW = datetime(2026, 8, 28, 23, 23, tzinfo=UTC)


def _http403(pfad: str = "/v4/leagues/1234567/squad") -> Exception:
    """Eine httpx-Fehlermeldung, wortgleich wie in der Nacht."""
    return RuntimeError(
        f"Client error '403 Forbidden' for url 'https://api.kickbase.com{pfad}'\n"
        "For more information check: "
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/403"
    )


# ------------------------------------------------------------------ Aufräumen


def test_library_boilerplate_is_stripped():
    """Der MDN-Hinweis ist httpx' Doku-Werbung, nicht unsere Information — und in
    Discord wird daraus eine Vorschau, die höher ist als die Meldung selbst."""
    text = saubere_meldung(_http403())
    assert "403 Forbidden" in text
    assert "developer.mozilla.org" not in text
    assert "For more information" not in text


def test_url_is_shortened_to_its_path():
    """Der Host steht in jeder Zeile gleich; der Pfad sagt, was nicht ging."""
    text = saubere_meldung(_http403("/v4/leagues/1234567/me/budget"))
    assert "/v4/leagues/1234567/me/budget" in text
    assert "api.kickbase.com" not in text


# --------------------------------------------------------------- Zusammenfassen


def test_the_same_outage_is_reported_once_across_different_jobs():
    """DER Kernfall: Cockpit-Build, Intent-Planner und Gebots-Wächter liefen in
    denselben 403 — drei Jobs, ein Vorfall, eine Meldung."""
    b = FehlerBuendel()
    erste = b.melden("Cockpit-Build", _http403(), now=NOW)
    assert erste is not None and "403" in erste

    for minute, job in ((2, "Intent-Planner"), (24, "Gebots-Wächter"),
                        (59, "Cockpit-Build")):
        weiter = b.melden(job, _http403(), now=NOW + timedelta(minutes=minute))
        assert weiter is None, f"{job} hat erneut gemeldet"

    v = b.offen()[0]
    assert v.anzahl == 4
    assert v.jobs == {"Cockpit-Build", "Intent-Planner", "Gebots-Wächter"}


def test_a_different_error_still_gets_through():
    """Bündeln darf nicht heißen: alles schlucken. Ein anderer Fehler meldet sich."""
    b = FehlerBuendel()
    b.melden("Cockpit-Build", _http403(), now=NOW)
    anderer = b.melden("Briefing", ValueError("Kader leer"), now=NOW + timedelta(minutes=5))
    assert anderer is not None and "Kader leer" in anderer


def test_a_new_incident_after_the_window_is_reported_again():
    """Nach einer Stunde beginnt ein neuer Vorfall — ein Dauerproblem soll am
    nächsten Morgen wieder auffallen, nicht für immer stumm bleiben."""
    b = FehlerBuendel()
    b.melden("Cockpit-Build", _http403(), now=NOW)
    spaeter = b.melden("Cockpit-Build", _http403(), now=NOW + timedelta(hours=1, minutes=1))
    assert spaeter is not None


# ------------------------------------------------------------------ Abschluss


def test_a_settled_outage_is_summarised_with_numbers():
    """Wenn Ruhe einkehrt, kommt die Bilanz — sonst wüsste niemand, wie groß der
    Ausfall war. Genau diese Zeile hätte in der Nacht gefehlt."""
    b = FehlerBuendel()
    b.melden("Cockpit-Build", _http403(), now=NOW)
    for i in (1, 2, 3, 4):
        b.melden("Intent-Planner", _http403(), now=NOW + timedelta(minutes=i * 10))
    fertig = b.abgeklungen(now=NOW + timedelta(minutes=70))
    assert len(fertig) == 1
    text = fertig[0]
    assert "5×" in text
    assert "Cockpit-Build" in text and "Intent-Planner" in text
    assert b.offen() == []


def test_a_single_error_needs_no_summary():
    """Ein Einzelfehler stand schon in Discord — eine Zusammenfassung darüber
    wäre Rauschen über Rauschen."""
    b = FehlerBuendel()
    b.melden("Briefing", ValueError("einmalig"), now=NOW)
    assert b.abgeklungen(now=NOW + timedelta(minutes=30)) == []


def test_an_ongoing_outage_is_not_closed_early():
    """Solange es weiter knallt, bleibt der Vorfall offen — er darf nicht als
    erledigt gemeldet werden, während er noch läuft."""
    b = FehlerBuendel()
    b.melden("Cockpit-Build", _http403(), now=NOW)
    b.melden("Cockpit-Build", _http403(), now=NOW + timedelta(minutes=15))
    assert b.abgeklungen(now=NOW + timedelta(minutes=25)) == []
    assert len(b.offen()) == 1


def test_fingerprint_groups_by_status_not_by_job():
    """Ein 403 und ein 500 sind zwei Vorfälle; derselbe 403 in zwei Jobs einer."""
    assert fingerabdruck("A", _http403()) == fingerabdruck("B", _http403())
    fuenfhundert = RuntimeError("Server error '500 Internal Server Error' for url '/x'")
    assert fingerabdruck("A", _http403()) != fingerabdruck("A", fuenfhundert)


# ------------------------------------------------------------------- Herkunft


def test_messages_from_outside_the_box_say_so(monkeypatch):
    """Am 30.08. meldete ein lokaler Lauf zwei OperationalError nach Discord —
    „no such table: agent_freeze" und „job_runs". Auf der Box gab es die
    Tabellen längst; die lokale Datenbank stand nur auf einem älteren
    Migrationsstand. Emil sah zwei Alarme über fehlende SICHERUNGS-Tabellen und
    musste annehmen, sein laufender Agent sei kaputt.

    Eine Meldung ohne Absender ist im Zweifel die schlimmstmögliche: Man hält
    sie für die Produktion."""
    from kickbase_agent.worker import error_digest as ed

    monkeypatch.setattr(ed, "_laeuft_auf_der_box", lambda: False)
    text = FehlerBuendel().melden("Einfrier-Wächter",
                                  RuntimeError("no such table: agent_freeze"), now=NOW)
    assert text is not None and text.startswith("⚠️ [lokal]")


def test_messages_from_the_box_carry_no_prefix(monkeypatch):
    """Der Normalfall bleibt schmucklos — ein Präfix, das immer dasteht, liest
    nach zwei Tagen niemand mehr."""
    from kickbase_agent.worker import error_digest as ed

    monkeypatch.setattr(ed, "_laeuft_auf_der_box", lambda: True)
    text = FehlerBuendel().melden("Briefing", ValueError("x"), now=NOW)
    assert text is not None and text.startswith("⚠️ Briefing-Fehler")


def test_the_marker_is_the_docker_file_not_a_guess():
    """Erkennung über /.dockerenv — das legt Docker in jedem Container an.
    Hostname oder DATABASE_URL können gesetzt sein, ohne dass der Container
    gemeint ist."""
    from pathlib import Path

    from kickbase_agent.worker import error_digest as ed
    assert ed._laeuft_auf_der_box() == Path("/.dockerenv").exists()
