"""Kehrer fuer liegengebliebene Zustaende (Fund 31.08.2026).

Anlass: Ein Fehler-Audit fand elf `proposed`-Zeilen vom 21./22.08., deren
`valid_until` neun bis zehn Tage zurueckliegt. Der Executor kennt den Uebergang
'proposed' -> 'expired' laengst (TTL-Luecke b), vollzieht ihn aber nur TRAEGE:
erst wenn dieselbe Aktion erneut angelegt wird. Wird sie das nie, bleibt die
Zeile ewig offen — und blockt als _BLOCKING still jeden Neuversuch.

Dazu ein `job_runs`-Eintrag vom 28.08., der seit dem Absturz auf 'running'
steht. `job_health` MELDET ihn korrekt als 'stuck', schliesst ihn aber nie.

Beide Kehrer aendern nur den Status — geloescht wird nichts. Die Zeile IST der
Beleg, dass es den Vorschlag gab (CLAUDE.md §9).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.worker.reconcile as rec
from kickbase_agent.db.models import Base, JobRun, TransferAction
from kickbase_agent.worker.job_health import STUCK_AFTER_S, close_stuck_runs
from kickbase_agent.worker.reconcile import expire_lapsed_proposals

NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _proposal(session, *, key: str, valid_until: datetime | None,
              status: str = "proposed", rationale: str | None = None):
    row = TransferAction(
        league_id="L1", action_type="bid", player_id="99", status=status,
        idempotency_key=key, dry_run=False, created_at=NOW - timedelta(days=10),
        valid_until=valid_until, rationale=rationale,
    )
    session.add(row)
    session.commit()
    return row


def _run(session, *, label: str, started_at: datetime, status: str = "running"):
    row = JobRun(label=label, started_at=started_at, status=status)
    session.add(row)
    session.commit()
    return row


# --- abgelaufene Vorschlaege ---


def test_lapsed_proposal_becomes_expired(session):
    """Der reale Fall: Frist vor neun Tagen verstrichen, nie freigegeben."""
    row = _proposal(session, key="a", valid_until=NOW - timedelta(days=9))
    swept = expire_lapsed_proposals(session, NOW)

    assert [s["id"] for s in swept] == [row.id]
    session.refresh(row)
    assert row.status == "expired"
    assert row.resolved_at is not None


def test_row_survives_the_sweep(session):
    """Kein DELETE: die Zeile bleibt als Beleg stehen (CLAUDE.md §9)."""
    _proposal(session, key="a", valid_until=NOW - timedelta(days=9))
    expire_lapsed_proposals(session, NOW)

    assert session.query(TransferAction).count() == 1


def test_reason_records_why_it_was_swept(session):
    """Die Begruendung sagt, was passiert ist — nicht bloss 'expired'."""
    row = _proposal(session, key="a", valid_until=NOW - timedelta(days=9),
                    rationale="Erstgebot 14,1 M")
    expire_lapsed_proposals(session, NOW)

    session.refresh(row)
    assert "Erstgebot 14,1 M" in (row.rationale or "")  # das Alte bleibt stehen
    assert "Frist" in (row.rationale or "")


def test_running_deadline_is_left_alone(session):
    """Ein Vorschlag, dessen Frist noch laeuft, gehoert nicht dem Kehrer."""
    row = _proposal(session, key="a", valid_until=NOW + timedelta(hours=2))
    assert expire_lapsed_proposals(session, NOW) == []

    session.refresh(row)
    assert row.status == "proposed"


def test_proposal_without_deadline_is_never_guessed_at(session):
    """Ohne `valid_until` gibt es keine Frist — und damit nichts zu schliessen."""
    row = _proposal(session, key="a", valid_until=None)
    assert expire_lapsed_proposals(session, NOW) == []

    session.refresh(row)
    assert row.status == "proposed"


@pytest.mark.parametrize("status", ["executed", "failed", "refused", "pending", "unknown"])
def test_other_statuses_are_untouchable(session, status):
    """Der Kehrer fasst AUSSCHLIESSLICH 'proposed' an — nie einen Endzustand
    und nie eine Zeile, die der Reconcile-Job gerade aufloest."""
    row = _proposal(session, key="a", valid_until=NOW - timedelta(days=9), status=status)
    assert expire_lapsed_proposals(session, NOW) == []

    session.refresh(row)
    assert row.status == status


# --- haengende Job-Laeufe ---


def test_stuck_run_is_closed(session):
    """Der reale Fall: Cockpit-Build vom 28.08., Prozess gestorben."""
    row = _run(session, label="Cockpit-Build", started_at=NOW - timedelta(days=3))
    assert close_stuck_runs(session, NOW) == 1

    session.refresh(row)
    assert row.status == "failed"
    assert row.error and "ohne Endzeile" in row.error


def test_stuck_run_gets_no_invented_end_time(session):
    """Wir wissen NICHT, wann der Prozess starb — also wird kein Ende erfunden.

    Ein erfundener `finished_at` wuerde in der Dauer-Statistik als echte
    Messung erscheinen. Lieber eine Luecke als eine erfundene Zahl.
    """
    row = _run(session, label="Cockpit-Build", started_at=NOW - timedelta(days=3))
    close_stuck_runs(session, NOW)

    session.refresh(row)
    assert row.finished_at is None
    assert row.duration_ms is None


def test_fresh_run_keeps_running(session):
    """Ein Job, der gerade laeuft, ist kein Haenger."""
    row = _run(session, label="Recherche", started_at=NOW - timedelta(seconds=30))
    assert close_stuck_runs(session, NOW) == 0

    session.refresh(row)
    assert row.status == "running"


def test_threshold_matches_the_watchdog(session):
    """Der Kehrer schliesst genau das, was `job_health` als 'stuck' meldet —
    zwei verschiedene Grenzen waeren ein Widerspruch im selben Bild."""
    knapp_drunter = _run(session, label="A", started_at=NOW - timedelta(seconds=STUCK_AFTER_S - 60))
    knapp_drueber = _run(session, label="B", started_at=NOW - timedelta(seconds=STUCK_AFTER_S + 60))
    assert close_stuck_runs(session, NOW) == 1

    session.refresh(knapp_drunter)
    session.refresh(knapp_drueber)
    assert knapp_drunter.status == "running"
    assert knapp_drueber.status == "failed"


def test_finished_runs_are_untouched(session):
    """'ok' und 'failed' sind Endzustaende."""
    row = _run(session, label="A", started_at=NOW - timedelta(days=3), status="ok")
    assert close_stuck_runs(session, NOW) == 0

    session.refresh(row)
    assert row.status == "ok"


# --- Einbau in den stuendlichen Job ---


class _FakeChannel:
    def __init__(self):
        self.sent: list[str] = []

    async def send_info(self, text: str) -> None:
        self.sent.append(text)


@pytest.mark.asyncio
async def test_sweep_runs_even_when_there_is_nothing_to_reconcile(session, monkeypatch):
    """Der eigentliche Fehler: `run_reconcile` stieg frueh mit 'quiet' aus, wenn
    es keine 'unknown'/'pending' gab — und genau das ist der Normalfall.

    Die Kehrer haengen deshalb VOR dem Ausstieg. Ohne diesen Test wuerde ein
    spaeterer Umbau sie klaglos wieder hinter das `return` schieben.
    """
    class _Ctx:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rec, "SessionLocal", lambda: _Ctx())
    # Seit 01.09. kontaktiert die Pfand-Einlöser-Wache Kickbase in JEDEM Lauf
    # (Spielplan-Read für den Deadline-Horizont) — der Abgleich selbst bleibt
    # ohne Kickbase-Call. Deadline weit weg ⇒ die Wache legt sich sofort hin.
    from types import SimpleNamespace

    class _WatchClient:
        async def get_matchdays(self):
            from kickbase_agent.kickbase.models import Fixture

            # Relativ zur ECHTEN Uhr: Die Wache rechnet mit datetime.now(); ein fester
            # Anpfiff (NOW + 30 Tage = 30.09.2026) rückte am 26.09. in ihren
            # 96-h-Horizont und liess den Test seitdem kippen.
            return [Fixture(matchday=9, home_team_id="1", away_team_id="2",
                            kickoff=datetime.now(UTC) + timedelta(days=30))]

    async def _factory():
        return _WatchClient(), SimpleNamespace(league_id="L1", user_id="U1")

    monkeypatch.setattr(rec, "make_kickbase_client", _factory)

    row = _proposal(session, key="a", valid_until=NOW - timedelta(days=9))
    stuck = _run(session, label="Cockpit-Build", started_at=NOW - timedelta(days=3))
    channel = _FakeChannel()

    ergebnis = await rec.run_reconcile(channel)

    assert "swept=1" in ergebnis and "stuck=1" in ergebnis
    session.refresh(row)
    session.refresh(stuck)
    assert row.status == "expired"
    assert stuck.status == "failed"
    # Emil erfaehrt davon: Beide Kehrer stehen in der Discord-Karte.
    assert len(channel.sent) == 1
    assert "Vorschlag" in channel.sent[0]
    assert "Job-Lauf" in channel.sent[0]
