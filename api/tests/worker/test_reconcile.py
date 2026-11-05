"""Reconcile-Job: 'unknown'/hängende 'pending' gegen die Live-Realität auflösen."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

import kickbase_agent.worker.reconcile as rec
from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.worker.reconcile import (
    classify,
    format_report,
    load_candidates,
    mark_alarm_once,
    own_listing_pids,
    resolve_status,
    run_reconcile,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _action(kind: str = "bid", pid: str | None = "99", payload: dict | None = None):
    """Detachte TransferAction — classify liest nur Attribute, keine DB nötig."""
    return TransferAction(
        league_id="L1", action_type=kind, player_id=pid, status="unknown",
        idempotency_key="k", dry_run=False, created_at=NOW, payload=payload,
    )


def _bid_entry(pid: str = "99", offer_id: str = "o1") -> dict:
    return {"player_id": pid, "name": "Test Spieler", "team_id": "1",
            "amount": 1_000_000, "offer_id": offer_id, "expires_in_s": 3_600}


def _db_row(session, *, status: str, kind: str = "bid", pid: str | None = "99",
            created_at: datetime = NOW, key: str = "k", rationale: str | None = None):
    row = TransferAction(
        league_id="L1", action_type=kind, player_id=pid, status=status,
        idempotency_key=key, dry_run=False, created_at=created_at, rationale=rationale,
    )
    session.add(row)
    session.commit()
    return row


# --- classify (rein, ohne Netz) ---


def test_classify_bid_executed_when_own_bid_live():
    status, note = classify(_action("bid"), [_bid_entry("99")], set(), set())
    assert status == "executed"
    assert "Gebot" in note


def test_classify_bid_executed_when_player_already_in_squad():
    """Kein Gebot mehr sichtbar, aber Spieler im Kader → Zuschlag ist erfolgt."""
    status, note = classify(_action("bid"), [], {"99"}, set())
    assert status == "executed"
    assert "Kader" in note


def test_classify_bid_failed_without_bid_or_squad():
    status, note = classify(_action("bid"), [], set(), set())
    assert status == "failed"
    assert "kein Gebot" in note


def test_classify_cancel_bid_executed_when_bid_gone():
    a = _action("cancel_bid", payload={"offer_id": "o1"})
    assert classify(a, [], set(), set())[0] == "executed"
    # anderes Gebot (neue offer_id) auf demselben Spieler stört nicht
    assert classify(a, [_bid_entry(offer_id="o2")], set(), set())[0] == "executed"


def test_classify_cancel_bid_failed_when_bid_still_there():
    a = _action("cancel_bid", payload={"offer_id": "o1"})
    assert classify(a, [_bid_entry(offer_id="o1")], set(), set())[0] == "failed"
    # ohne payload (direkter execute-Pfad) zählt die player_id
    assert classify(_action("cancel_bid"), [_bid_entry()], set(), set())[0] == "failed"


def test_classify_sell_paths():
    assert classify(_action("sell"), [], set(), set())[0] == "executed"  # Spieler weg
    status, note = classify(_action("sell"), [], {"99"}, set())
    assert status == "failed"
    assert "noch im Kader" in note


def test_classify_list_paths():
    assert classify(_action("list"), [], set(), {"99"})[0] == "executed"  # Listing da
    assert classify(_action("list"), [], {"99"}, set())[0] == "failed"  # kein Listing
    status, note = classify(_action("list"), [], set(), set())
    assert status == "executed"  # kein Listing + Spieler weg = verkauft
    assert "verkauft" in note


def test_classify_unresolvable_types_stay():
    """lineup/bonus/accept/decline/delist: kein sicherer Live-Beweis → None."""
    for kind in ("lineup", "bonus", "accept", "decline", "delist"):
        status, note = classify(_action(kind), [_bid_entry()], {"99"}, {"99"})
        assert status is None
        assert "manuell" in note


def test_own_listing_pids_reads_seller_from_u_i():
    raw = {"it": [
        {"i": "10", "u": {"i": "U1"}},    # eigenes Listing
        {"i": "11", "u": {"i": "OTHER"}},  # fremdes Manager-Listing
        {"i": "12"},                       # Kickbase-Listing ohne Verkäufer
        {"i": "13", "u": None},
    ]}
    assert own_listing_pids(raw, "U1") == {"10"}


# --- Kandidaten-Auswahl ---


def test_load_candidates_pending_age_boundary(session):
    """'pending' mit 9 min Alter bleibt unangetastet, mit 11 min wird geprüft."""
    young = _db_row(session, status="pending",
                    created_at=NOW - timedelta(minutes=9), key="young")
    old = _db_row(session, status="pending",
                  created_at=NOW - timedelta(minutes=11), key="old")
    ids = [r.id for r in load_candidates(session, now=NOW)]
    assert old.id in ids
    assert young.id not in ids


def test_load_candidates_unknown_any_age_others_ignored(session):
    """'unknown' zählt IMMER (auch frisch, auch naive Stempel); Endzustände nie."""
    fresh = _db_row(session, status="unknown", created_at=NOW, key="fresh")
    naive = _db_row(session, status="unknown",
                    created_at=NOW.replace(tzinfo=None), key="naive")
    _db_row(session, status="executed", key="done")
    _db_row(session, status="proposed", key="open")
    _db_row(session, status="failed", key="failed")
    assert [r.id for r in load_candidates(session, now=NOW)] == [fresh.id, naive.id]


# --- Status-Auflösung (konditionales UPDATE) ---


def test_resolve_status_appends_reconciled_note(session):
    row = _db_row(session, status="unknown", key="r1", rationale="Kauf: Score 0.8")
    assert resolve_status(session, row, "executed", "eigenes Gebot live gefunden", NOW)
    session.expire_all()
    fresh = session.get(TransferAction, row.id)
    assert fresh.status == "executed"
    assert fresh.rationale == "Kauf: Score 0.8 | reconciled 12:00: eigenes Gebot live gefunden"
    assert fresh.resolved_at is not None


def test_resolve_status_never_overwrites_parallel_change(session):
    """Parallel aufgelöster Status (z. B. manuell) wird NIE überschrieben."""
    row = _db_row(session, status="unknown", key="r2", rationale="alt")
    session.execute(
        update(TransferAction).where(TransferAction.id == row.id).values(status="failed")
    )
    session.commit()
    assert resolve_status(session, row, "executed", "egal", NOW) is False
    session.expire_all()
    fresh = session.get(TransferAction, row.id)
    assert fresh.status == "failed"
    assert fresh.rationale == "alt"


def test_alarm_once_without_status_change(session):
    """Unauflösbares alarmiert genau EINMAL; der Status bleibt unberührt."""
    row = _db_row(session, status="unknown", kind="lineup", pid=None, key="a1")
    assert mark_alarm_once(session, row, "lineup nicht sicher abgleichbar", NOW) is True
    session.expire_all()
    fresh = session.get(TransferAction, row.id)
    assert fresh.status == "unknown"
    assert "reconcile-alarm" in fresh.rationale
    # zweiter Lauf: Merker in der rationale unterdrückt den Alarm
    assert mark_alarm_once(session, fresh, "lineup nicht sicher abgleichbar", NOW) is False


# --- Discord-Format ---


def test_format_report_lines_and_quiet():
    msg = format_report(
        [{"id": 7, "action_type": "bid", "player_id": "99", "old_status": "unknown",
          "new_status": "executed", "note": "eigenes Gebot live gefunden"}],
        [{"id": 8, "action_type": "lineup", "player_id": None, "status": "pending",
          "note": "lineup nicht sicher abgleichbar — bitte manuell prüfen"}],
    )
    assert msg is not None
    assert msg.startswith("🔁 **Reconcile**")
    assert "bid Spieler 99 (#7): unknown → executed" in msg
    assert "⚠️ lineup #8: Status bleibt pending" in msg
    assert format_report([], []) is None


# --- run_reconcile (Client/Channel gemockt, In-Memory-SQLite) ---


class _FakeChannel:
    def __init__(self):
        self.sent: list[str] = []

    async def send_info(self, text: str) -> None:
        self.sent.append(text)


def _patch_world(monkeypatch, session, *, bids=(), squad_pids=(), listing_pids=(),
                 factory_calls=None):
    class _Ctx:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rec, "SessionLocal", lambda: _Ctx())

    class FakeClient:
        async def get_own_bids(self, league_id, user_id):
            assert (league_id, user_id) == ("L1", "U1")
            return list(bids)

        async def get_squad(self, league_id):
            return [SimpleNamespace(player=SimpleNamespace(id=p)) for p in squad_pids]

        async def _get(self, path):  # roher Markt-Payload (Verkäufer u.i)
            return {"it": [{"i": p, "u": {"i": "U1"}} for p in listing_pids]}

        async def get_matchdays(self):
            # Deadline weit weg: der Pfand-Einlöser (01.09.) prüft den Horizont
            # und legt sich sofort wieder hin — die Kern-Zusicherungen dieses
            # Files bleiben davon unberührt.
            from kickbase_agent.kickbase.models import Fixture

            return [Fixture(matchday=9, home_team_id="1", away_team_id="2",
                            kickoff=datetime.now(UTC) + timedelta(days=30))]

        async def get_budget(self, league_id):
            return 99_000_000

    async def fake_factory():
        if factory_calls is not None:
            factory_calls.append(1)
        auth = SimpleNamespace(
            token="t", user_id="U1", leagues=[SimpleNamespace(id="L1")], league_id="L1"
        )
        return FakeClient(), auth

    monkeypatch.setattr(rec, "make_kickbase_client", fake_factory)


@pytest.mark.asyncio
async def test_run_quiet_without_candidates(monkeypatch, session):
    """Keine Kandidaten → 'quiet', kein Post, KEIN Kickbase-Call."""
    calls: list[int] = []
    _patch_world(monkeypatch, session, factory_calls=calls)
    channel = _FakeChannel()
    assert await run_reconcile(channel) == "quiet"
    assert channel.sent == []
    # Seit 01.09. liest die Pfand-Einlöser-Wache je Lauf den Spielplan (ein
    # Client-Aufbau) — Kandidaten-Abgleich selbst bleibt ohne Kickbase-Call.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_run_resolves_alarms_and_stays_quiet_on_rerun(monkeypatch, session):
    """unknown-bid wird executed (Gebot live da), pending-lineup alarmiert einmal."""
    real_now = datetime.now(UTC)
    bid_row = _db_row(session, status="unknown", key="b", rationale="Kauf 1M")
    lineup_row = _db_row(session, status="pending", kind="lineup", pid=None,
                         created_at=real_now - timedelta(minutes=30), key="l")
    _patch_world(monkeypatch, session, bids=[_bid_entry("99")])
    channel = _FakeChannel()

    assert await run_reconcile(channel) == "resolved=1 alarms=1"
    assert len(channel.sent) == 1
    msg = channel.sent[0]
    assert "🔁 **Reconcile**" in msg
    assert "bid Spieler 99" in msg
    assert "unknown → executed" in msg
    assert "⚠️ lineup" in msg

    session.expire_all()
    assert session.get(TransferAction, bid_row.id).status == "executed"
    assert "reconciled" in session.get(TransferAction, bid_row.id).rationale
    assert session.get(TransferAction, lineup_row.id).status == "pending"

    # zweiter Lauf: bid aufgelöst, lineup bereits alarmiert → leise, kein Post
    assert await run_reconcile(channel) == "quiet"
    assert len(channel.sent) == 1


@pytest.mark.asyncio
async def test_run_marks_lost_bid_failed(monkeypatch, session):
    """Weder Gebot noch Kader → failed; die Idempotenz-Sperre ist damit gelöst."""
    row = _db_row(session, status="unknown", key="lost")
    _patch_world(monkeypatch, session)  # leere Live-Welt
    channel = _FakeChannel()

    assert await run_reconcile(channel) == "resolved=1 alarms=0"
    session.expire_all()
    fresh = session.get(TransferAction, row.id)
    assert fresh.status == "failed"
    assert "kein Gebot, nicht im Kader" in fresh.rationale
    assert "unknown → failed" in channel.sent[0]
