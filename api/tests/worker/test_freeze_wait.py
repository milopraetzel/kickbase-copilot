"""Selbst-Einfrieren ist ein Riegel, kein Fehler des Vorhabens (02.09.2026).

Neue Datei (L-087). Ist der Agent eingefroren, wartet ein Geld-Vorhaben in
15-min-Schritten auf Emils Entsperren, statt als failed zu enden und den
Spieler für den Tag zu sperren — der Executor wird dabei gar nicht erst
gerufen (kein Audit-Rauschen alle 15 Minuten).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.worker.intents as wi
from kickbase_agent.db.models import Base, Intent

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class _NeverExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *a, **kw):
        self.calls += 1
        raise AssertionError("Executor darf im Freeze nicht gerufen werden")


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


def _intent(session, *, kind="sell", deadline=NOW + timedelta(days=2)) -> Intent:
    row = Intent(
        created_at=NOW - timedelta(hours=1), updated_at=NOW - timedelta(hours=1), kind=kind,
        player_id="4333", player_name="Ullrich", team_id="T", position=2, amount=8_000_000,
        rationale="Tausch", sources={"manual": True}, listing_type="manager",
        deadline=deadline, execute_at=NOW - timedelta(minutes=1), status="planned",
    )
    session.add(row)
    session.commit()
    return row


@pytest.mark.asyncio
async def test_im_freeze_wartet_das_vorhaben_statt_zu_scheitern(session, monkeypatch):
    monkeypatch.setattr(wi.fz, "is_frozen", lambda s: True)
    row = _intent(session)
    ex = _NeverExecutor()

    status = await wi.execute_intent(session, row, ex, "L1", now=NOW)

    assert status == wi.STATUS_PLANNED and ex.calls == 0
    assert "Einfrieren" in (row.result or "") and "Entsperren" in (row.result or "")
    assert wi._as_utc(row.execute_at) == NOW + wi.CONDITION_RETRY_DELAY


@pytest.mark.asyncio
async def test_kurz_vor_der_deadline_endet_das_warten_ehrlich(session, monkeypatch):
    monkeypatch.setattr(wi.fz, "is_frozen", lambda s: True)
    row = _intent(session, deadline=NOW + timedelta(minutes=20))
    ex = _NeverExecutor()

    status = await wi.execute_intent(session, row, ex, "L1", now=NOW)

    assert status == wi.STATUS_FAILED and ex.calls == 0
    assert "Einfrieren" in (row.result or "")


@pytest.mark.asyncio
async def test_ohne_freeze_laeuft_der_normale_weg(session, monkeypatch):
    monkeypatch.setattr(wi.fz, "is_frozen", lambda s: False)
    row = _intent(session)

    class _Ok:
        async def execute(self, *a, **kw):
            from types import SimpleNamespace

            return SimpleNamespace(status="executed", action_id=1, message="ok")

    status = await wi.execute_intent(session, row, _Ok(), "L1", now=NOW)
    assert status == wi.STATUS_EXECUTED
