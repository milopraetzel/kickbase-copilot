"""Selbst-Verfall des Freeze (02.09.2026, Emils Vollfreigabe) — `release_if_stale`.

Neue Datei (L-087). Alle Auslöser sind Tages-Größen, also ist auch die Bremse
tagesgebunden: Ein Freeze aus einem VERGANGENEN Tag wird freigegeben, sobald
die Auslöser HEUTE nicht mehr zutreffen. Der laufende Tag entschärft sich nie
selbst, und ein kaputter Selbst-Deploy heilt nicht durch Zeitablauf.

Hinweis: Die Funktion ist gebaut und geprüft, im stündlichen Wächter aber noch
NICHT verdrahtet — das ist Emils Schalter (docs, Bericht 02.09.).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.agent import freeze as fz
from kickbase_agent.db.models import Base, TransferAction

GESTERN = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
HEUTE = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


def _trade(session, kind: str, amount: int, at: datetime, pid: str, key: str) -> None:
    session.add(TransferAction(
        league_id="TEST", action_type=kind, player_id=pid, amount=amount,
        status="executed", idempotency_key=key, rule_id="intent-" + kind,
        dry_run=False, created_at=at,
    ))
    session.commit()


def test_freeze_von_gestern_wird_heute_freigegeben(session):
    fz.freeze(session, reason="Tagesverlust 12,0 M", source=fz.SOURCE_LOSS, now=GESTERN)
    assert fz.is_frozen(session) is True

    freed = fz.release_if_stale(session, now=HEUTE)

    assert freed is not None and fz.is_frozen(session) is False
    assert freed.released_by == fz.AUTO_RELEASER
    assert "01.09." in (freed.note or "") and "verlust" in (freed.note or "")


def test_heute_eingefroren_bleibt_heute_gesperrt(session):
    fz.freeze(session, reason="Tagesverlust", source=fz.SOURCE_LOSS, now=HEUTE)
    assert fz.release_if_stale(session, now=HEUTE + timedelta(hours=6)) is None
    assert fz.is_frozen(session) is True


def test_laufende_schieflage_bleibt_eingefroren(session):
    """Auslöser trifft HEUTE erneut zu (12 M realisierter Verlust) → kein Verfall."""
    fz.freeze(session, reason="Tagesverlust gestern", source=fz.SOURCE_LOSS, now=GESTERN)
    _trade(session, "bid", 20_000_000, GESTERN - timedelta(days=2), "p1", "k-buy")
    _trade(session, "sell", 8_000_000, HEUTE, "p1", "k-sell")  # −12 M heute

    assert fz.release_if_stale(session, now=HEUTE) is None
    assert fz.is_frozen(session) is True


def test_kaputter_selbst_deploy_heilt_nicht_durch_zeit(session):
    fz.freeze(session, reason="Deploy fehlgeschlagen", source=fz.SOURCE_DEPLOY, now=GESTERN)
    assert fz.release_if_stale(session, now=HEUTE) is None
    assert fz.is_frozen(session) is True


def test_ohne_freeze_passiert_nichts(session):
    assert fz.release_if_stale(session, now=HEUTE) is None


def test_unklare_writes_von_gestern_verfallen_wenn_heute_ruhe_ist(session):
    fz.freeze(session, reason="2 unklare Writes", source=fz.SOURCE_WRITES, now=GESTERN)
    freed = fz.release_if_stale(session, now=HEUTE)
    assert freed is not None and freed.source == fz.SOURCE_WRITES
