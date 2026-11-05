"""Aufstellungs-Job mit Teil-Elf (L-109, 18.09.2026): Platzliste, Rücklese, T-8-Check."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kickbase_agent.agent.executor import PlannedAction, idempotency_key
from kickbase_agent.decision.lineup import best_partial_lineup
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.kickbase.models import Fixture
from kickbase_agent.worker import lineup_job as job


def _c(pid: str, name: str, pos: int, score: float = 0.5) -> ExplosionCandidate:
    return ExplosionCandidate(player_id=pid, name=name, team_id="1", position=pos,
                              current_market_value=1, total_score=score, signals=[])


def _partial():
    return best_partial_lineup([
        _c("1580", "Schwäbe", 1),
        _c("6668", "Minjae", 2), _c("7320", "Scheller", 2), _c("4333", "Ullrich", 2),
        _c("8329", "Olise", 3, 0.9), _c("12158", "Vozar", 3, 0.2),
        _c("15196", "Yamamoto", 3, 0.1),
        _c("2855", "Ache", 4),
    ])


class _Kanal:
    def __init__(self) -> None:
        self.posts: list[str] = []

    async def send_info(self, text: str) -> None:
        self.posts.append(text)


def _world(monkeypatch, lineup, *, fielded_after: set[str], status: str = "executed"):
    """run_lineup_set-Umgebung fälschen: Plan, Client, Session, Empfehlung, Executor."""
    writes: list[PlannedAction] = []

    class _Client:
        async def get_squad(self, _lg):
            return [SimpleNamespace(player=SimpleNamespace(id=c.player_id),
                                    lineup_order=0 if c.player_id in fielded_after else None)
                    for c in lineup.starters]

    client = _Client()

    async def _plan():
        return SimpleNamespace(league_id="L1", squad=[], lineup_squad=[])

    async def _factory():
        return client, SimpleNamespace(league_id="L1")

    async def _recommend(_client, _session, _squad):
        return SimpleNamespace(lineup=lineup)

    class _Executor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def execute(self, action, dry_run=None):
            writes.append(action)
            return SimpleNamespace(status=status)

    @contextmanager
    def _session():
        yield None

    monkeypatch.setattr(job, "build_plan", _plan)
    monkeypatch.setattr(job, "make_kickbase_client", _factory)
    monkeypatch.setattr(job, "recommend_lineup", _recommend)
    monkeypatch.setattr(job, "ActionExecutor", _Executor)
    monkeypatch.setattr(job, "SessionLocal", _session)
    monkeypatch.setattr(job, "get_settings", lambda: SimpleNamespace(agent_auto_lineup=True))
    return writes


def test_partial_message_names_the_open_slots():
    msg = job.format_lineup_message(_partial(), "executed", armed=True)
    assert "Aufstellung gesetzt" in msg
    assert "3 von 11 Plätzen LEER" in msg and "Yamamoto" in msg and "Vozar" in msg


def test_unverified_message_asks_for_an_immediate_check():
    msg = job.format_lineup_message(_partial(), "unverified", armed=True)
    assert "NICHT bestätigt" in msg and "SOFORT" in msg


@pytest.mark.asyncio
async def test_partial_lineup_is_written_as_eleven_slots_with_gaps(monkeypatch):
    lineup = _partial()
    writes = _world(monkeypatch, lineup, fielded_after={c.player_id for c in lineup.starters})
    kanal = _Kanal()
    assert await job.run_lineup_set(kanal) == "executed"
    (action,) = writes
    assert action.action_type == "lineup" and action.formation == lineup.formation
    assert len(action.player_ids) == 11 and action.player_ids.count(None) == 3
    assert {pid for pid in action.player_ids if pid} >= {"12158", "15196"}
    assert "Teil-Elf 8/11" in action.rationale
    assert len(kanal.posts) == 1 and "LEER" in kanal.posts[0]


@pytest.mark.asyncio
async def test_executed_without_effect_is_reported_as_unverified(monkeypatch):
    """Kickbase antwortet 200, übernimmt aber zwei Spieler nicht: kein stilles „gesetzt"."""
    lineup = _partial()
    taken = {c.player_id for c in lineup.starters} - {"12158", "15196"}
    _world(monkeypatch, lineup, fielded_after=taken)
    kanal = _Kanal()
    assert await job.run_lineup_set(kanal) == "unverified"
    assert "NICHT bestätigt" in kanal.posts[0]


@pytest.mark.asyncio
async def test_unchanged_partial_lineup_does_not_repeat_the_post(monkeypatch):
    lineup = _partial()
    _world(monkeypatch, lineup, fielded_after=set(), status="duplicate")
    kanal = _Kanal()
    assert await job.run_lineup_set(kanal) == "duplicate"
    assert kanal.posts == []


def test_idempotency_key_handles_gaps_and_tells_lineups_apart():
    def key(ids):
        return idempotency_key(PlannedAction(
            action_type="lineup", league_id="L1", formation="3-4-3", player_ids=ids,
        ), "2026-09-18")

    eight = ["1", "2", "3", "4", "5", "6", "7", None, "8", None, None]
    nine = ["1", "2", "3", "4", "5", "6", "7", "9", "8", None, None]
    assert key(eight) == key(list(eight))
    assert key(eight) != key(nine)          # späterer Zuschlag ⇒ neuer Write, kein Duplikat


class _Scheduler:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, id=None, replace_existing=False):  # noqa: A002
        self.jobs.append({"func": func, "trigger": trigger, "id": id,
                          "replace": replace_existing})


def _deadline_world(monkeypatch, kickoff):
    class _Client:
        async def get_matchdays(self):
            return [Fixture(matchday=4, home_team_id="1", away_team_id="2", kickoff=kickoff)]

    async def _factory():
        return _Client(), SimpleNamespace(leagues=[SimpleNamespace(id="L1")], league_id="L1")

    monkeypatch.setattr(job, "make_kickbase_client", _factory)


@pytest.mark.asyncio
async def test_late_gap_check_is_planned_eight_minutes_before_the_deadline(monkeypatch):
    kickoff = datetime.now(UTC) + timedelta(hours=5)
    _deadline_world(monkeypatch, kickoff)
    sched = _Scheduler()
    labels: list[str] = []

    def scheduled(label, factory):
        labels.append(label)
        return factory

    result = await job.schedule_late_gap_check(sched, scheduled, channel=None)
    (planned,) = sched.jobs
    assert planned["id"] == "kickoff-late-gap-md4" and planned["replace"] is True
    assert planned["trigger"].run_date == kickoff - timedelta(minutes=8)
    assert labels == ["Spieltag 4 T-8 Elf-Lücke"]
    assert "Spieltag 4" in result


@pytest.mark.asyncio
async def test_late_gap_check_is_not_planned_inside_the_last_eight_minutes(monkeypatch):
    _deadline_world(monkeypatch, datetime.now(UTC) + timedelta(minutes=5))
    sched = _Scheduler()
    result = await job.schedule_late_gap_check(sched, lambda _l, f: f, channel=None)
    assert sched.jobs == [] and "schon vorbei" in result
