"""Sammler-Job der Lern-Bilanz (Spec §4.1): taeglich 07:20, meldet nur Statuswechsel.

Zwei Eigenschaften sind der eigentliche Punkt:
1. Der Job ist **still**, solange sich nichts aendert. Eine Meldung „alles wie
   gestern" waere Rauschen und wuerde die echten Wechsel begraben.
2. Das Fenster kommt aus dem letzten Lauf (`job_runs`) und darf grosszuegig
   ueberlappen — die Unique-Sperre faengt Doppelungen ab.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, JobRun, LearningEvidence, TransferAction
from kickbase_agent.knowledge import evidence_rules as rules
from kickbase_agent.trading.expectation import estimate_bid_success, settle_bid_expectations
from kickbase_agent.worker import evidence_job

NOW = datetime(2026, 8, 26, 5, 20, tzinfo=UTC)   # 07:20 Berlin

LEARNINGS_MD = """## Meta-Wissen
- [L-047] **Prämien-Beweis (24.08.):** Unser Collect kassiert selbst.
  **Regel:** Emil muss nichts mehr holen.
  **Art:** hypothese
- [L-054] **err 5080 (24.08.):** Manager-Listings verlangen 100 % MW.
  **Regel:** Nie unter Marktwert ankern.
  **Art:** hypothese
- [L-065] **Beschaffungs-Bilanz (25.08.):** Manager-Deals scheitern.
  **Regel:** Starter über Kickbase-Listings holen.
  **Art:** hypothese
"""


class _Channel:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_info(self, text: str) -> None:
        self.messages.append(text)


@pytest.fixture
def session(tmp_path, monkeypatch) -> Session:
    md = tmp_path / "LEARNINGS.md"
    md.write_text(LEARNINGS_MD, encoding="utf-8")
    monkeypatch.setenv("KBA_LEARNINGS_PATH", str(md))
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    s = Session(engine, future=True)
    monkeypatch.setattr(evidence_job, "SessionLocal", lambda: s)
    monkeypatch.setattr(s, "close", lambda: None)
    return s


def _underpay(session: Session, key: str, *, days_ago: int = 1) -> None:
    when = NOW - timedelta(days=days_ago)
    session.add(TransferAction(
        league_id="L1", action_type="bid", player_id="15589", amount=9_000_000,
        status="failed", idempotency_key=key, rule_id="intent-bid",
        rationale='HTTP 500 {"err":5080,"errMsg":"UnderpayNotAllowed"}',
        dry_run=False, created_at=when, resolved_at=when,
    ))
    session.commit()


async def test_job_writes_evidence_and_reports_a_status_change(session):
    _underpay(session, "a", days_ago=3)
    _underpay(session, "b", days_ago=2)
    channel = _Channel()

    out = await evidence_job.run_evidence_job(channel, now=NOW)

    rows = session.execute(select(LearningEvidence)).scalars().all()
    assert len(rows) == 2 and {r.learning_id for r in rows} == {"L-054"}
    assert out == "belege=2 wechsel=1"
    assert len(channel.messages) == 1
    text = channel.messages[0]
    assert "L-054" in text and "gestützt" in text


async def test_job_stays_quiet_when_nothing_changed(session):
    _underpay(session, "a", days_ago=3)
    _underpay(session, "b", days_ago=2)
    first = _Channel()
    await evidence_job.run_evidence_job(first, now=NOW)

    second = _Channel()
    out = await evidence_job.run_evidence_job(second, now=NOW + timedelta(days=1))
    assert out == "belege=0 wechsel=0"
    assert second.messages == []


async def test_job_reports_a_refutation_as_action_needed(session):
    """Widerlegt heisst Handlungsbedarf — das gehoert nach Discord, nicht ins Log."""
    for i in range(3):
        when = NOW - timedelta(days=i + 1)
        session.add(TransferAction(
            league_id="L1", action_type="bid", player_id=f"{i}", amount=1_000_000,
            status="executed", idempotency_key=f"won-{i}", rule_id="intent-bid",
            rationale="", dry_run=False, created_at=when, resolved_at=when,
            payload={
                "expectation": {"p": 0.15, "kind": "manager"},
                "outcome": {"result": "won", "at": when.isoformat()},
            },
        ))
    session.commit()
    channel = _Channel()
    await evidence_job.run_evidence_job(channel, now=NOW)
    assert "L-065" in channel.messages[0]
    assert "widerlegt" in channel.messages[0]
    assert "korrigieren" in channel.messages[0].lower()


async def test_window_starts_at_the_last_successful_run(session):
    session.add(JobRun(
        label=evidence_job.JOB_LABEL, started_at=NOW - timedelta(days=1),
        finished_at=NOW - timedelta(days=1), status="ok", duration_ms=10,
    ))
    session.commit()
    assert evidence_job.window_start(session, NOW) == NOW - timedelta(days=1)


async def test_window_falls_back_to_a_lookback_on_the_first_run(session):
    assert evidence_job.window_start(session, NOW) == NOW - timedelta(
        days=evidence_job.FIRST_RUN_LOOKBACK_DAYS
    )


@pytest.mark.parametrize("result", ["won", "lost"])
async def test_job_collects_a_bid_settled_after_the_previous_run(session, result):
    """Gebote laufen über Sammelfenster hinweg; erst der Ausgang ist ein Beleg."""
    import json
    from pathlib import Path

    feed = json.loads((Path(__file__).parents[1]
                      / "fixtures/league_history_20260912/feed-matching.json").read_text())
    receipt = next(e for e in feed if e["data"].get("pi") == "3492"
                   and e["data"].get("byr") and e["data"].get("slr"))
    placed_at = datetime.fromisoformat(receipt["dt"]) - timedelta(hours=2)
    amount = receipt["data"]["trp"] - (1 if result == "lost" else 0)
    row = TransferAction(
        league_id="L1", action_type="bid", player_id="3492", amount=amount,
        status="executed", idempotency_key="settled-later", rule_id="intent-bid",
        rationale="", dry_run=False, created_at=placed_at, resolved_at=placed_at,
        payload={"expectation": estimate_bid_success(
            kind="manager", amount=9_000_000, market_value=9_000_000,
        ).as_dict()},
    )
    session.add(row)
    session.commit()
    previous_run = NOW - timedelta(days=1)
    assert await evidence_job.run_evidence_job(None, now=previous_run) == "belege=0 wechsel=0"
    session.add(JobRun(
        label=evidence_job.JOB_LABEL, started_at=previous_run,
        finished_at=previous_run, status="ok", duration_ms=10,
    ))
    session.commit()

    settled_at = NOW - timedelta(hours=1)
    settled = settle_bid_expectations(
        session, squad_pids={"15589"} if result == "won" else set(),
        open_bid_pids=set(), now=settled_at, receipts=[receipt],
        my_name="emil" if result == "won" else "anderer Manager",
    )
    assert len(settled) == 1 and settled[0].result == result
    await evidence_job.run_evidence_job(None, now=NOW)

    evidence = session.scalars(select(LearningEvidence)).all()
    assert len(evidence) == 1
    assert evidence[0].learning_id == rules.L_BESCHAFFUNG
    assert evidence[0].ref == f"action:{row.id}"
    assert evidence[0].verdict == ("refute" if result == "won" else "confirm")
    assert evidence[0].observed_at.replace(tzinfo=UTC) == settled_at
    assert await evidence_job.run_evidence_job(None, now=NOW) == "belege=0 wechsel=0"


async def test_job_survives_a_broken_rule(session, monkeypatch):
    def boom(session, since, now):
        raise ValueError("Feld weg")

    monkeypatch.setattr(
        rules, "REGISTRY", [rules.Rule("kaputt", rules.L_UNDERPAY, boom)]
    )
    channel = _Channel()
    out = await evidence_job.run_evidence_job(channel, now=NOW)
    assert out.startswith("belege=0")
    assert channel.messages == []


async def test_job_runs_without_a_channel(session):
    """Der Job muss auch ohne Discord laufen (CLI/Tests) — ohne zu werfen."""
    _underpay(session, "a", days_ago=3)
    _underpay(session, "b", days_ago=2)
    assert await evidence_job.run_evidence_job(None, now=NOW) == "belege=2 wechsel=1"
