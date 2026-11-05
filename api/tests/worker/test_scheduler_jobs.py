"""Regression: Scheduler-Jobs müssen echte Coroutine-Funktionen sein und real laufen.

Hintergrund: `lambda: _safe(coro, …)` ist eine Sync-Funktion — AsyncIOScheduler
awaited deren Rückgabe-Coroutine NIE („executed successfully", aber nichts passiert).
Das erste 22:15-Briefing blieb deshalb stumm.
"""

import asyncio
import inspect

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kickbase_agent.db.models import Base, JobRun
from kickbase_agent.worker import __main__ as worker_main
from kickbase_agent.worker.__main__ import make_scheduled_job


def test_scheduled_job_is_coroutine_function():
    async def safe(coro, label):
        await coro

    job = make_scheduled_job(safe, "Test", lambda: asyncio.sleep(0))
    assert inspect.iscoroutinefunction(job)   # sonst awaited der Scheduler nie


@pytest.mark.asyncio
async def test_scheduled_job_actually_runs_factory_each_time():
    ran: list[str] = []

    async def work():
        ran.append("x")

    async def safe(coro, label):
        await coro

    job = make_scheduled_job(safe, "Briefing", work)
    await job()
    await job()   # jedes Feuern erzeugt eine FRISCHE Coroutine
    assert ran == ["x", "x"]


@pytest.mark.asyncio
async def test_scheduled_job_routes_errors_through_safe():
    seen: list[str] = []

    async def boom():
        raise RuntimeError("kaputt")

    async def safe(coro, label):
        try:
            await coro
        except RuntimeError:
            seen.append(label)

    await make_scheduled_job(safe, "Snapshot", boom)()
    assert seen == ["Snapshot"]


@pytest.mark.asyncio
async def test_notified_failure_is_still_recorded_as_failed(monkeypatch):
    """Der echte _safe-Pfad schluckt nach der Meldung: Heartbeat muss failed bleiben."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker_main, "SessionLocal", factory)
    notified = []

    async def boom():
        raise RuntimeError("Urteil nicht möglich")

    async def safe(coro, label):
        try:
            await coro
        except RuntimeError:
            notified.append(label)
            return None

    await make_scheduled_job(safe, "Überraschungen", boom)()

    assert notified == ["Überraschungen"]
    with factory() as session:
        row = session.execute(select(JobRun)).scalar_one()
        assert row.status == "failed"
        assert row.error == "RuntimeError: Urteil nicht möglich"


def test_job_name_survives_apscheduler_naming():
    """Regression 25.08. (Fehlalarm-Serie im Cron-Waechter): APScheduler benennt Jobs
    ueber `get_callable_name`, und das nimmt den QUALNAME. Solange nur `__name__`
    gesetzt war, hiess JEDER Job „make_scheduled_job.<locals>.runner" — der Waechter
    konnte keinen Job seinem Heartbeat in `job_runs` zuordnen und meldete gesunde Jobs
    als „noch nie gelaufen".

    Dieser Test geht bewusst durch die ECHTE APScheduler-Funktion: Die Job-Attrappe in
    test_job_health.py setzt `name="job_<label>"` von Hand und konnte den Fehler
    deshalb nie zeigen.
    """
    from apscheduler.util import get_callable_name

    from kickbase_agent.worker import job_health

    async def safe(coro, label):
        await coro

    job = make_scheduled_job(safe, "Snapshot 22:05", lambda: asyncio.sleep(0))
    assert get_callable_name(job) == "job_Snapshot 22:05"

    class _Job:
        name = get_callable_name(job)

    assert job_health.label_of(_Job()) == "Snapshot 22:05"


def _registered_job_labels() -> set[str]:
    """Feste Labels aller `scheduled("…", …)`-Registrierungen im Worker — per AST aus
    dem echten Quelltext, weil die Registrierung in `on_ready` inline steht. Labels
    aus f-Strings (Prämie, Briefing) sind hier bewusst nicht erfasst."""
    import ast

    tree = ast.parse(inspect.getsource(worker_main))
    labels: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "scheduled"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            labels.add(node.args[0].value)
    return labels


def test_retired_jobs_are_no_longer_registered():
    """Paket 1, P0-6 (Studie 19.09.2026): Überraschungen lief 10 von 10 Mal rot und
    entwertete failed_window_total; der Gebots-Wächter erinnert an eine Eskalation,
    die die Engine nicht mehr kennt; Auto-Trading 22:30 ist ein Schattenpfad neben
    dem einen Vorhaben-Pfad. Abgemeldet im Code (kein .env-Schalter), Code bleibt."""
    labels = _registered_job_labels()
    assert labels.isdisjoint({"Überraschungen", "Gebots-Wächter", "Auto-Trading"})
    # Der Code bleibt importierbar — manuelle Wege und Tests hängen daran.
    from kickbase_agent.worker import auto_trading, bid_watch, surprise_job

    assert callable(auto_trading.run_auto_trading)
    assert callable(bid_watch.run_bid_watch)
    assert callable(surprise_job.run_surprise_job)


def test_core_jobs_stay_registered():
    labels = _registered_job_labels()
    for label in (
        "Snapshot", "Anpfiff-Planer", "Elf-Lücke-Planer", "Elf-Lücken-Wächter",
        "Intent-Planner", "Intent-Executor", "Reconcile", "Gebots-Nachschau",
        "Cron-Wächter", "Einfrier-Wächter", "Modellvergleich", "Gebotsmodellvergleich",
    ):
        assert label in labels, label


def test_paket1_jobs_are_registered():
    """P0-4 Ergebnis-Wächter (eigener Job, keine fünfte Anpfiff-Stufe) und P0-10
    wöchentliches Voll-Nachladen der Marktwert-/Leistungshistorie."""
    labels = _registered_job_labels()
    assert "Ergebnis-Wächter" in labels
    assert "Historie-Nachladen" in labels
    # Emil 21.09.2026: täglicher, rein lesender Steiger-Radar (Handel nur auf Auftrag).
    assert "Steiger-Radar" in labels
    # Emil 30.09.2026 (Schritt 4 des Jev-Umbaus): rein lesender News-Radar vor 08:15.
    assert "News-Radar" in labels
    # Emil 01.10.2026: Handels-Radar vor 08:15 und nach dem Update 22:00, der Autopilot
    # setzt ihn um („alles soll automatisch geschehen“).
    assert {"Handels-Radar", "Handels-Radar Abend", "Handels-Autopilot"} <= labels


def test_the_evening_radar_follows_the_update_and_the_autopilot_runs_twice_an_hour():
    """Der Abend-Radar läuft nach dem Marktwert-Update 22:00 und dem Snapshot 22:05; der
    Autopilot zweimal je Stunde, versetzt zu Engine (:03/:13 …) und Executor (alle 5 min)."""
    evening = _const_kwargs(_add_job_call("Handels-Radar Abend").args[1])
    assert (evening["hour"], evening["minute"]) == (22, 35)
    autopilot = _const_kwargs(_add_job_call("Handels-Autopilot").args[1])
    assert autopilot["minute"] == "17,47" and "hour" not in autopilot


def _add_job_call(label: str):
    """Der `scheduler.add_job(scheduled("<label>", …), <Trigger>, …)`-Aufruf im Worker."""
    import ast

    tree = ast.parse(inspect.getsource(worker_main))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_job"
            and node.args
            and isinstance(node.args[0], ast.Call)
            and getattr(node.args[0].func, "id", None) == "scheduled"
            and isinstance(node.args[0].args[0], ast.Constant)
            and node.args[0].args[0].value == label
        ):
            return node
    raise AssertionError(f"{label} nicht registriert")


def _const_kwargs(call) -> dict:
    import ast

    return {kw.arg: kw.value.value for kw in call.keywords if isinstance(kw.value, ast.Constant)}


def test_news_radar_runs_before_the_daily_summary_and_tolerates_a_late_start():
    """Die Tageszusammenfassung 08:15 liest den Radar-Bericht des Tages; ein um wenige
    Sekunden verpasster Start (Standard-Toleranz 1 s) hieße: heute kein Radar."""
    for label, minute in (("News-Radar", 11), ("Steiger-Radar", 10), ("Handels-Radar", 12)):
        call = _add_job_call(label)
        trigger = _const_kwargs(call.args[1])
        grace = _const_kwargs(call).get("misfire_grace_time", 1)
        assert trigger["hour"] == 8 and trigger["minute"] == minute
        assert 60 <= grace
        # Spätester Start (Jitter + Toleranz) plus eine Minute Laufzeit vor 08:15.
        assert 60 * minute + trigger.get("jitter", 0) + grace + 60 <= 60 * 15
