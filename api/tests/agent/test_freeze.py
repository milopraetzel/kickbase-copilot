"""Selbst-Einfrieren: der Agent legt sich selbst still, wenn etwas kippt.

Der Zustand ist bewusst grob und einseitig: Einfrieren kann der Agent allein,
Entsperren nur ein Mensch. Ein System, das sich selbst wieder scharf schaltet,
hat keine Notbremse — es hat eine Verzögerung.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.agent import freeze as fz
from kickbase_agent.db.models import AgentFreeze, Base, TransferAction

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _action(
    session,
    *,
    kind: str,
    amount: int,
    status: str = "executed",
    pid: str = "99",
    at: datetime = NOW,
    dry: bool = False,
    key: str | None = None,
):
    row = TransferAction(
        league_id="L1", action_type=kind, player_id=pid, amount=amount, status=status,
        idempotency_key=key or f"{kind}-{pid}-{at.isoformat()}-{amount}-{status}",
        dry_run=dry, created_at=at,
    )
    session.add(row)
    session.commit()
    return row


# ----------------------------------------------------------------- Zustand


def test_no_freeze_row_means_not_frozen(session):
    assert fz.active_freeze(session) is None
    assert fz.is_frozen(session) is False


def test_freeze_sets_readonly_since_and_reason(session):
    row = fz.freeze(session, reason="Tagesverlust 2,0 M", source=fz.SOURCE_LOSS, now=NOW)
    assert row.readonly_since.replace(tzinfo=UTC) == NOW
    assert row.source == fz.SOURCE_LOSS and "2,0 M" in row.reason
    assert fz.is_frozen(session) is True


def test_freezing_twice_keeps_the_first_reason(session):
    """Der erste Grund ist der wahre — spätere Folgefehler überschreiben ihn nicht."""
    first = fz.freeze(session, reason="Tagesverlust", source=fz.SOURCE_LOSS, now=NOW)
    again = fz.freeze(
        session, reason="Deploy kaputt", source=fz.SOURCE_DEPLOY, now=NOW + timedelta(hours=1)
    )
    assert again.id == first.id and again.reason == "Tagesverlust"
    assert session.query(AgentFreeze).count() == 1


def test_release_is_manual_and_recorded(session):
    fz.freeze(session, reason="Tagesverlust", source=fz.SOURCE_LOSS, now=NOW)
    released = fz.release(session, by="emil", now=NOW + timedelta(hours=2), note="geprüft")
    assert released is not None and released.released_by == "emil"
    assert fz.is_frozen(session) is False


def test_release_without_active_freeze_returns_none(session):
    assert fz.release(session, by="emil", now=NOW) is None


# --------------------------------------------------------- Wirkung (Interlock)


def test_frozen_state_vetoes_money_writes(session):
    fz.freeze(session, reason="zwei unklare Writes", source=fz.SOURCE_WRITES, now=NOW)
    veto = fz.freeze_veto(session, "bid", "intent-42")
    assert veto is not None
    assert "Selbst-Einfrieren" in veto and "zwei unklare Writes" in veto


def test_frozen_state_leaves_risk_reducing_actions_alone(session):
    """Storno und Auslisten bauen Risiko AB — sie einzufrieren wäre pervers."""
    fz.freeze(session, reason="x", source=fz.SOURCE_WRITES, now=NOW)
    assert fz.freeze_veto(session, "cancel_bid", "intent-1") is None
    assert fz.freeze_veto(session, "delist", "intent-1") is None
    assert fz.freeze_veto(session, "lineup", None) is None
    assert fz.freeze_veto(session, "bonus", None) is None


def test_kickoff_emergency_brake_is_never_frozen(session):
    """Konto ≥ 0 beim Anpfiff schlägt alles — sonst kostet die Sicherung Punkte."""
    fz.freeze(session, reason="x", source=fz.SOURCE_LOSS, now=NOW)
    assert fz.freeze_veto(session, "sell", "kickoff-purge") is None


def test_without_freeze_nothing_is_vetoed(session):
    assert fz.freeze_veto(session, "bid", "intent-1") is None


# ----------------------------------------------------------------- Auslöser


def test_realized_pnl_matches_sales_against_their_own_purchase(session):
    _action(session, kind="bid", amount=3_000_000, at=NOW - timedelta(days=3), pid="99")
    _action(session, kind="sell", amount=2_000_000, at=NOW, pid="99")
    pnl = fz.realized_pnl(session, NOW.date())
    assert pnl.total == -1_000_000 and pnl.unknown_basis == 0
    assert pnl.trades[0]["player_id"] == "99"


def test_realized_pnl_ignores_sales_without_a_known_purchase(session):
    """Spieler aus der Zeit vor dem Agenten haben keinen Einstandspreis im Audit.

    Sie zu bewerten hieße, eine Zahl zu erfinden — der Zähler bleibt ehrlich leer.
    """
    _action(session, kind="sell", amount=2_000_000, at=NOW, pid="alt")
    pnl = fz.realized_pnl(session, NOW.date())
    assert pnl.total == 0 and pnl.unknown_basis == 1


def test_realized_pnl_ignores_dry_run_rows(session):
    _action(session, kind="bid", amount=3_000_000, at=NOW - timedelta(days=1), pid="99")
    _action(session, kind="sell", amount=1_000_000, at=NOW, pid="99", dry=True)
    assert fz.realized_pnl(session, NOW.date()).total == 0


def test_loss_above_the_limit_triggers_a_freeze(session):
    _action(session, kind="bid", amount=5_000_000, at=NOW - timedelta(days=2), pid="99")
    _action(session, kind="sell", amount=1_000_000, at=NOW, pid="99")
    trigger = fz.evaluate(session, now=NOW, loss_limit=1_000_000)
    assert trigger is not None and trigger.source == fz.SOURCE_LOSS
    assert "4,0 M" in trigger.reason or "4.0 M" in trigger.reason


def test_loss_below_the_limit_stays_quiet(session):
    _action(session, kind="bid", amount=1_200_000, at=NOW - timedelta(days=2), pid="99")
    _action(session, kind="sell", amount=1_000_000, at=NOW, pid="99")
    assert fz.evaluate(session, now=NOW, loss_limit=1_000_000) is None


def test_two_unclear_writes_on_one_day_trigger_a_freeze(session):
    _action(session, kind="bid", amount=1, status="unknown", pid="a", at=NOW)
    _action(session, kind="bid", amount=1, status="failed", pid="b", at=NOW)
    trigger = fz.evaluate(session, now=NOW)
    assert trigger is not None and trigger.source == fz.SOURCE_WRITES
    assert "2" in trigger.reason


def test_one_unclear_write_is_not_enough(session):
    _action(session, kind="bid", amount=1, status="failed", pid="a", at=NOW)
    assert fz.evaluate(session, now=NOW) is None


def test_refused_writes_are_not_failures(session):
    """`refused` ist die Sicherung bei der Arbeit, kein Störfall."""
    _action(session, kind="bid", amount=1, status="refused", pid="a", at=NOW)
    _action(session, kind="bid", amount=1, status="refused", pid="b", at=NOW)
    assert fz.evaluate(session, now=NOW) is None


def test_yesterdays_failures_do_not_freeze_today(session):
    y = NOW - timedelta(days=1)
    _action(session, kind="bid", amount=1, status="unknown", pid="a", at=y)
    _action(session, kind="bid", amount=1, status="failed", pid="b", at=y)
    assert fz.evaluate(session, now=NOW) is None


def test_evaluate_and_freeze_writes_the_state_and_reports_it(session):
    _action(session, kind="bid", amount=1, status="unknown", pid="a", at=NOW)
    _action(session, kind="bid", amount=1, status="unknown", pid="b", at=NOW)
    row = fz.evaluate_and_freeze(session, now=NOW)
    assert row is not None and fz.is_frozen(session)
    assert row.evidence and row.evidence.get("count") == 2
    # zweiter Lauf friert nicht erneut ein (idempotent)
    assert fz.evaluate_and_freeze(session, now=NOW) is None


def test_quiet_day_changes_nothing(session):
    assert fz.evaluate_and_freeze(session, now=NOW) is None
    assert session.query(AgentFreeze).count() == 0


# ----------------------------------------------------------------- Meldung


def test_alarm_text_names_reason_and_the_manual_way_out(session):
    row = fz.freeze(session, reason="Tagesverlust 4,0 M", source=fz.SOURCE_LOSS, now=NOW)
    text = fz.format_alarm(row)
    assert "Tagesverlust 4,0 M" in text
    assert "kba unfreeze" in text


def test_status_text_when_free(session):
    assert "nicht eingefroren" in fz.format_status(None).lower()
