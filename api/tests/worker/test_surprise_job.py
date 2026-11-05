"""Der Überraschungs-Auslöser: findet Widersprüche — und schweigt, wenn es keine gibt.

Emil, 26.08.: „Nur wenn irgendwas Unerwartetes kommt … Wenn alles normal klappt, muss
nichts geändert werden." Der wichtigste Test dieser Datei ist deshalb der, in dem
NICHTS passiert.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kickbase_agent.agent import judgment as jd
from kickbase_agent.db.models import Base, JobRun, TransferAction
from kickbase_agent.worker import surprise_job as sj

NOW = datetime(2026, 8, 26, 6, 40, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _bid(
    session,
    *,
    pid: str,
    p: float,
    result: str | None,
    kind: str = "kickbase",
    at: datetime = NOW,
    judged: bool = False,
):
    payload: dict = {"expectation": {"p": p, "why": "Auto-Zuschlag", "kind": kind,
                                     "ratio": 1.08}}
    if result:
        payload["outcome"] = {"result": result, "at": at.isoformat()}
    if judged:
        payload["surprise"] = {"urteil": "einzelfall", "at": at.isoformat()}
    row = TransferAction(
        league_id="L1", action_type="bid", player_id=pid, amount=2_930_000,
        status="executed", idempotency_key=f"b{pid}", dry_run=False,
        created_at=at - timedelta(hours=6), payload=payload, rule_id="intent-bid",
    )
    session.add(row)
    session.commit()
    return row


def _job_run(session, label: str, status: str, at: datetime):
    row = JobRun(label=label, started_at=at, finished_at=at, status=status)
    session.add(row)
    session.commit()
    return row


def _refused(session, rationale: str, at: datetime = NOW, key: str = "r1"):
    row = TransferAction(
        league_id="L1", action_type="bid", player_id="99", amount=1_000_000,
        status="refused", idempotency_key=key, dry_run=False, created_at=at,
        rationale=rationale, rule_id="intent-bid",
    )
    session.add(row)
    session.commit()
    return row


# ----------------------------------------------------------------- Ruhe


def test_empty_database_is_quiet(session):
    assert sj.find_surprises(session, now=NOW) == []


def test_a_normal_day_is_quiet(session):
    """Ein 45-%-Gebot zu verlieren ist der wahrscheinliche Ausgang, keine Überraschung."""
    _bid(session, pid="1", p=0.45, result="lost")
    _bid(session, pid="2", p=0.80, result="won")
    _job_run(session, "Briefing", "ok", NOW - timedelta(hours=2))
    assert sj.find_surprises(session, now=NOW) == []


def test_an_open_bid_is_never_a_surprise(session):
    """Ohne Ergebnis gibt es nichts zu vergleichen."""
    _bid(session, pid="1", p=0.90, result=None)
    assert sj.find_surprises(session, now=NOW) == []


# ----------------------------------------------------------------- Gebote


def test_high_expectation_lost_is_a_surprise(session):
    _bid(session, pid="1", p=0.90, result="lost")
    (found,) = sj.find_surprises(session, now=NOW)
    assert found.art == "gebot" and found.richtung == "zu_optimistisch"
    assert found.fingerprint == "gebot|kickbase|zu_optimistisch"
    assert "90" in found.title


def test_low_expectation_won_is_a_surprise(session):
    _bid(session, pid="1", p=0.15, result="won", kind="manager")
    (found,) = sj.find_surprises(session, now=NOW)
    assert found.richtung == "zu_vorsichtig"
    assert found.fingerprint == "gebot|manager|zu_vorsichtig"


def test_an_already_judged_bid_is_not_reported_twice(session):
    _bid(session, pid="1", p=0.90, result="lost", judged=True)
    assert sj.find_surprises(session, now=NOW) == []


def test_old_results_fall_out_of_the_window(session):
    _bid(session, pid="1", p=0.90, result="lost", at=NOW - timedelta(days=9))
    assert sj.find_surprises(session, now=NOW, days=2) == []
    assert len(sj.find_surprises(session, now=NOW, days=30)) == 1


# ----------------------------------------------------------------- Jobs


def test_a_job_that_flips_from_ok_to_failed_is_a_surprise(session):
    _job_run(session, "Reconcile", "ok", NOW - timedelta(hours=2))
    _job_run(session, "Reconcile", "failed", NOW - timedelta(hours=1))
    (found,) = sj.find_surprises(session, now=NOW)
    assert found.art == "job" and found.kind == "Reconcile"
    assert found.fingerprint == "job|Reconcile|ausfall"


def test_a_job_that_keeps_failing_reports_only_the_first_flip(session):
    """Dauerfeuer wäre Lärm: Der Zustand ist bekannt, er ist keine Überraschung mehr."""
    _job_run(session, "Reconcile", "ok", NOW - timedelta(hours=3))
    _job_run(session, "Reconcile", "failed", NOW - timedelta(hours=2))
    _job_run(session, "Reconcile", "failed", NOW - timedelta(hours=1))
    assert len(sj.find_surprises(session, now=NOW)) == 1


def test_a_first_ever_failure_counts(session):
    """Kein Vorlauf heisst nicht „normal" — der erste Ausfall zählt."""
    _job_run(session, "Reconcile", "failed", NOW - timedelta(hours=1))
    assert len(sj.find_surprises(session, now=NOW)) == 1


# ----------------------------------------------------------------- Sperren


def test_a_preflight_refusal_is_a_surprise(session):
    """Der Planner hielt es für zulässig, der Preflight nicht — zwei Meinungen."""
    _refused(session, "Preflight: Marktpreis 12 % ueber dem Staging-Betrag")
    (found,) = sj.find_surprises(session, now=NOW)
    assert found.art == "sperre" and found.richtung == "blockade"


def test_a_central_invariant_refusal_is_a_surprise(session):
    _refused(session, "Zentrale Invariante: Tages-Cap Kaeufe erreicht (5/5 heute)")
    assert len(sj.find_surprises(session, now=NOW)) == 1


def test_an_interlock_refusal_is_not_a_surprise(session):
    """Ein fehlendes Flag ist der geplante Zustand, kein Widerspruch."""
    _refused(session, "Live-Write in Liga L1 verweigert: nicht die Test-Liga und "
                      "AGENT_AUTO_INTENTS=0 (Intent-Pfad nicht geaermt).")
    assert sj.find_surprises(session, now=NOW) == []


def test_a_freeze_refusal_is_not_a_surprise(session):
    """Sonst erzeugte ein eingefrorener Tag Dutzende Meldungen ueber denselben Fall."""
    _refused(session, "Selbst-Einfrieren aktiv seit 26.08. 10:00 UTC (verlust): ...")
    assert sj.find_surprises(session, now=NOW) == []


# ----------------------------------------------------------------- Der Job


class Channel:
    def __init__(self) -> None:
        self.posts: list[str] = []

    async def send_info(self, text: str, **kw) -> None:
        self.posts.append(text)


@pytest.fixture
def db(monkeypatch) -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(sj, "SessionLocal", factory)
    with factory() as s:
        yield s


@pytest.mark.asyncio
async def test_quiet_run_posts_nothing_and_changes_nothing(db):
    channel = Channel()
    assert await sj.run_surprise_job(channel, now=NOW) == "quiet"
    assert channel.posts == []


@pytest.mark.asyncio
async def test_a_surprise_is_reported_as_a_plain_fact(db):
    """Erst der Rohfall, dann (spaeter) die Deutung — Emil soll beides trennen koennen."""
    _bid(db, pid="1", p=0.90, result="lost")
    channel = Channel()

    result = await sj.run_surprise_job(channel, now=NOW, judge=None)

    assert result.startswith("surprises:1")
    assert len(channel.posts) == 1
    assert "Überraschung" in channel.posts[0]


@pytest.mark.asyncio
async def test_missing_judgment_runtime_fails_the_real_job(db, monkeypatch):
    """Fehlende CLI im Worker darf keinen erfolgreichen Lernlauf vortäuschen."""
    row = _bid(db, pid="1", p=0.90, result="lost")

    async def missing_cli(*args, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(jd.asyncio, "create_subprocess_exec", missing_cli)
    with pytest.raises(RuntimeError, match="Urteil"):
        await sj.run_surprise_job(now=NOW, judge=jd.judge_surprise)

    db.refresh(row)
    assert "surprise" not in row.payload


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bid", "refusal"])
async def test_failed_case_is_retried_after_the_detection_window(db, monkeypatch, kind):
    """Der zweite echte Joblauf muss dieselbe offene Fallakte erneut vorlegen."""
    if kind == "bid":
        _bid(db, pid="1", p=0.90, result="lost")
    else:
        _refused(db, "Preflight: Marktpreis weicht ab")
    prompts = []

    async def unavailable(prompt):
        prompts.append(prompt)
        raise jd.ClaudeCliError("CLI nicht installiert")

    monkeypatch.setattr(jd, "ask_claude", unavailable)
    for at in (NOW, NOW + timedelta(days=2)):
        try:
            await sj.run_surprise_job(now=at, judge=jd.judge_surprise)
        except RuntimeError:
            pass

    assert len(prompts) == 2


@pytest.mark.asyncio
async def test_judged_refusals_are_not_submitted_again(db, monkeypatch):
    _refused(db, "Preflight: Marktpreis weicht ab")

    async def answer(prompt):
        return json.dumps({"ausgang": "einzelfall", "erklaerung": "Vorfall geprüft."})

    monkeypatch.setattr(jd, "ask_claude", answer)
    assert await sj.run_surprise_job(now=NOW, judge=jd.judge_surprise) == "surprises:1"
    assert await sj.run_surprise_job(now=NOW, judge=jd.judge_surprise) == "quiet"


@pytest.mark.asyncio
async def test_failed_case_does_not_lose_other_verdicts_and_recovers_later(db, monkeypatch):
    failed = _bid(db, pid="1", p=0.90, result="lost")
    succeeded = _bid(db, pid="2", p=0.90, result="lost")
    fail_first = True

    async def answer(prompt):
        if fail_first and f'"ref": "action:{failed.id}"' in prompt:
            # Nur die aktuelle Fallakte zählt, nicht die Vergleichsfälle.
            context = json.loads(prompt.split("FALL:\n", 1)[1].split("\n\nFRAGE:", 1)[0])
            if context["ueberraschung"]["ref"] == f"action:{failed.id}":
                raise jd.ClaudeCliError("vorübergehend nicht verfügbar")
        return json.dumps({"ausgang": "einzelfall", "erklaerung": "Vorfall geprüft."})

    monkeypatch.setattr(jd, "ask_claude", answer)
    channel = Channel()
    with pytest.raises(RuntimeError, match="1 von 2"):
        await sj.run_surprise_job(channel, now=NOW, judge=jd.judge_surprise)

    db.refresh(failed)
    db.refresh(succeeded)
    assert failed.payload["surprise_pending"]["attempts"] == 1
    assert failed.payload["surprise_pending"]["error"] == "ClaudeCliError"
    assert succeeded.payload["surprise"]["urteil"] == "einzelfall"
    assert "surprise_pending" not in succeeded.payload
    # Die gebündelte Fehlermeldung kommt erst aus _safe, hier nur Rohfälle + Urteil.
    assert len(channel.posts) == 2
    assert all("nicht möglich" not in post for post in channel.posts)

    fail_first = False
    later = NOW + timedelta(days=2)
    assert await sj.run_surprise_job(now=later, judge=jd.judge_surprise) == "surprises:1"
    db.refresh(failed)
    assert failed.payload["surprise"]["urteil"] == "einzelfall"
    assert "surprise_pending" not in failed.payload
    assert await sj.run_surprise_job(now=later, judge=jd.judge_surprise) == "quiet"


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_pending_backlog_and_long_verdicts_fit_discord(db, monkeypatch, unavailable):
    """Auch ein alter Rückstau muss vollständig durch das echte Nachrichtenlimit passen."""
    for i in range(20):
        row = _refused(
            db, "Preflight: " + "Abweichender Marktpreis. " * 9,
            at=NOW - timedelta(days=2), key=f"r{i}",
        )
        row.payload = {"surprise_pending": {"first_seen_at": row.created_at.isoformat()}}
    db.commit()
    cases = sj.find_surprises(db, now=NOW)

    class LimitedChannel(Channel):
        async def send_info(self, text, **kwargs):
            assert len(text) <= 2000, "Discord-Nachricht zu lang"
            await super().send_info(text, **kwargs)

    async def answer(prompt):
        if unavailable:
            raise jd.ClaudeCliError("nicht verfügbar")
        return json.dumps({"ausgang": "einzelfall", "erklaerung": "X" * 2200})

    monkeypatch.setattr(jd, "ask_claude", answer)
    channel = LimitedChannel()
    if unavailable:
        with pytest.raises(RuntimeError, match="Urteil für 20 von 20"):
            await sj.run_surprise_job(channel, now=NOW, judge=jd.judge_surprise)
    else:
        assert await sj.run_surprise_job(channel, now=NOW, judge=jd.judge_surprise) == (
            "surprises:20"
        )
        assert await sj.run_surprise_job(now=NOW, judge=jd.judge_surprise) == "quiet"

    assert len(channel.posts) > 1
    assert all(len(post) <= 1900 for post in channel.posts)
    delivered = "\n".join(channel.posts)
    assert delivered.count("**Überraschung:**") == 20
    assert all(sj.format_line(case) in delivered for case in cases)
    assert sum(post.count("X") for post in channel.posts) == (0 if unavailable else 44_000)


def test_format_line_stays_sober():
    s = sj.Surprise(
        art="gebot", ref="action:5", kind="kickbase", richtung="zu_optimistisch",
        title="Gebot Ljubicic: erwartet 90 % Zuschlag, verloren",
        detail="2,93 M = 108 % MW", at=NOW, evidence={},
    )
    line = sj.format_line(s)
    assert "Ljubicic" in line and "90" in line
    assert "sollte" not in line.lower()  # keine Deutung
