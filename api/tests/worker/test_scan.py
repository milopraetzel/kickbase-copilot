"""stage_plan + send_staged: Plan → vorgemerkte Vorschläge → Kanal."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.decision.models import Proposal
from kickbase_agent.notify.base import ChannelMessage
from kickbase_agent.worker.scan import send_staged, stage_plan


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _prop(pid: str, kind: str = "bid") -> Proposal:
    return Proposal(
        action=PlannedAction(action_type=kind, league_id="L", player_id=pid, amount=3_000_000),
        rule_id=f"{kind}_rule",
        rationale=f"{kind} {pid}",
        score=0.7,
        requires_approval=False,
    )


class CollectingChannel:
    def __init__(self) -> None:
        self.approvals: list[ChannelMessage] = []

    async def send_approval(self, message: ChannelMessage) -> None:
        self.approvals.append(message)

    async def send_info(self, text: str) -> None:
        pass


@pytest.mark.asyncio
async def test_stage_plan_marks_all_proposed(session):
    plan = Plan(league_id="L", budget=50_000_000, buys=[_prop("1")], sells=[_prop("9", "sell")])
    executor = ActionExecutor(client=None, session=session)

    staged = await stage_plan(executor, plan)

    assert len(staged) == 2
    assert session.query(TransferAction).filter_by(status="proposed").count() == 2
    # rationale landet im Audit-Log
    assert {r.rationale for r in session.query(TransferAction).all()} == {"bid 1", "sell 9"}


@pytest.mark.asyncio
async def test_send_staged_delivers_one_message_per_proposal(session):
    plan = Plan(league_id="L", budget=50_000_000, buys=[_prop("1"), _prop("2")], sells=[])
    executor = ActionExecutor(client=None, session=session)
    staged = await stage_plan(executor, plan)

    channel = CollectingChannel()
    count = await send_staged(channel, staged)

    assert count == 2
    assert len(channel.approvals) == 2
    assert all(m.action_id is not None for m in channel.approvals)


# ---------------------------------------- Neustart-Reload der Approval-Buttons


def _row(session, *, status: str, valid_until, action_type="bid", key_suffix=""):
    from datetime import UTC, datetime

    row = TransferAction(
        league_id="L",
        action_type=action_type,
        player_id="p1",
        status=status,
        idempotency_key=f"k-{status}-{valid_until}-{key_suffix}",
        dry_run=False,
        valid_until=valid_until,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    return row


def test_open_approval_ids_returns_only_valid_proposed(session):
    """Nur 'proposed' MIT valid_until in der Zukunft — abgelaufene, fremde Status
    und Alt-Zeilen ohne TTL werden NICHT re-registriert (Review-Fund)."""
    from datetime import UTC, datetime, timedelta

    from kickbase_agent.worker.scan import open_approval_ids

    now = datetime.now(UTC)
    fresh = _row(session, status="proposed", valid_until=now + timedelta(minutes=30))
    _row(session, status="proposed", valid_until=now - timedelta(minutes=5), key_suffix="old")
    _row(session, status="proposed", valid_until=None, key_suffix="legacy")
    _row(session, status="executed", valid_until=now + timedelta(minutes=30), key_suffix="done")
    _row(session, status="rejected", valid_until=now + timedelta(minutes=30), key_suffix="no")

    assert open_approval_ids(session, now=now) == [fresh.id]


def test_open_approval_ids_treats_naive_timestamps_as_utc(session):
    """SQLite liefert naive Zeitstempel — sie zählen als UTC (kein Absturz/Skew)."""
    from datetime import UTC, datetime, timedelta

    from kickbase_agent.worker.scan import open_approval_ids

    now = datetime.now(UTC)
    naive_future = (now + timedelta(minutes=10)).replace(tzinfo=None)
    row = _row(session, status="proposed", valid_until=naive_future, key_suffix="naive")

    assert open_approval_ids(session, now=now) == [row.id]


@pytest.mark.asyncio
async def test_reload_approval_views_registers_open_proposals(session, monkeypatch):
    """Bot-Start: für jeden noch gültigen Vorschlag wird die persistente View
    registriert (Kanal bekommt die IDs aus der DB)."""
    from datetime import UTC, datetime, timedelta

    import kickbase_agent.worker.scan as scan_mod
    from kickbase_agent.worker.scan import reload_approval_views

    now = datetime.now(UTC)
    fresh = _row(session, status="proposed", valid_until=now + timedelta(minutes=30))
    _row(session, status="proposed", valid_until=now - timedelta(minutes=5), key_suffix="old")

    class _SessionCtx:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(scan_mod, "SessionLocal", lambda: _SessionCtx())

    class FakeChannel:
        def __init__(self):
            self.registered: list[list[int]] = []

        def register_persistent_views(self, ids):
            ids = list(ids)
            self.registered.append(ids)
            return len(ids)

    channel = FakeChannel()
    assert await reload_approval_views(channel) == 1
    assert channel.registered == [[fresh.id]]
