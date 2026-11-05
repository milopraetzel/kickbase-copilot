"""Spieltags-Wächter: EINE Deadline pro Spieltag + ehrliche Notbremsen-Auswertung.

Kickbase-Regel (offizielle Hilfe): Aufstellung und Konto werden EINMAL pro
Spieltag geprüft — beim Anpfiff des ersten Spiels. Einzelne Sa/So-Anstöße sind
keine Deadlines; und die Notbremse meldet drei EHRLICHE Zustände (Review-Fund:
Erfolg feuerte schon bei Konto ≥ 0, obwohl die Schleife auf ≥ Puffer zielt):
Konto ≥ 10.000-€-Puffer = Erfolg, 0 ≤ Konto < Puffer = Teil-Erfolg,
Konto < 0 = Alarm — immer nur, wenn der Executor "executed" sagt und der
Kontostand FRISCH gelesen wurde.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kickbase_agent.kickbase.errors import KickbaseDataError
from kickbase_agent.kickbase.models import Fixture, Player, Position, SquadPlayer
from kickbase_agent.worker.kickoff_guard import (
    matchday_deadline,
    run_bid_cancel_guard,
    run_debt_ping,
    run_purge_enforce,
    running_matchday,
    schedule_kickoff_guards,
)


def _fx(md: int, kickoff: datetime | None, finished: bool = False) -> Fixture:
    return Fixture(matchday=md, home_team_id="1", away_team_id="2",
                   kickoff=kickoff, is_finished=finished)


# ---------------------------------------------------------------- matchday_deadline

def test_matchday_deadline_is_first_kickoff_of_matchday():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)  # Donnerstag
    fx = [
        _fx(2, datetime(2026, 8, 22, 13, 30, tzinfo=UTC)),   # Sa
        _fx(2, datetime(2026, 8, 21, 18, 30, tzinfo=UTC)),   # Fr — früheste Partie
        _fx(2, datetime(2026, 8, 23, 15, 30, tzinfo=UTC)),   # So
        _fx(3, datetime(2026, 8, 28, 18, 30, tzinfo=UTC)),
    ]
    assert matchday_deadline(fx, now) == (2, datetime(2026, 8, 21, 18, 30, tzinfo=UTC))


def test_matchday_deadline_mid_matchday_jumps_to_next():
    """P0-Kern: Sa-Morgen MITTEN im Spieltag ist der Sa-15:30-Anstoß KEINE
    Deadline mehr — Elf/Konto sind seit Fr eingefroren, nächste Deadline = MD 3."""
    now = datetime(2026, 8, 22, 8, 0, tzinfo=UTC)  # Samstag früh
    fx = [
        _fx(2, datetime(2026, 8, 21, 18, 30, tzinfo=UTC), finished=True),  # Fr vorbei
        _fx(2, datetime(2026, 8, 22, 13, 30, tzinfo=UTC)),                 # Sa offen
        _fx(3, datetime(2026, 8, 28, 18, 30, tzinfo=UTC)),
    ]
    assert matchday_deadline(fx, now) == (3, datetime(2026, 8, 28, 18, 30, tzinfo=UTC))


def test_matchday_deadline_none_without_future_matchday_start():
    now = datetime(2026, 8, 22, 8, 0, tzinfo=UTC)
    past = [_fx(1, datetime(2026, 8, 15, 13, 30, tzinfo=UTC), finished=True)]
    assert matchday_deadline([], now) is None
    assert matchday_deadline(past, now) is None
    assert matchday_deadline([_fx(4, None)], now) is None


def test_matchday_deadline_picks_earliest_start_not_lowest_number():
    """Nachholspiel-Wirrwarr: es zählt der früheste ZUKÜNFTIGE Spieltagsstart."""
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    fx = [
        _fx(7, datetime(2026, 8, 21, 18, 30, tzinfo=UTC)),   # vorgezogener Spieltag
        _fx(3, datetime(2026, 8, 25, 18, 30, tzinfo=UTC)),
    ]
    assert matchday_deadline(fx, now) == (7, datetime(2026, 8, 21, 18, 30, tzinfo=UTC))


# ----------------------------------------------------------------- running_matchday

def test_running_matchday_between_first_and_last_game():
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)  # Sa 10:00
    fx = [
        _fx(2, datetime(2026, 8, 21, 18, 30, tzinfo=UTC), finished=True),
        _fx(2, datetime(2026, 8, 23, 15, 30, tzinfo=UTC)),                 # So offen
        _fx(3, datetime(2026, 8, 28, 18, 30, tzinfo=UTC)),
    ]
    assert running_matchday(fx, now) == 2


def test_running_matchday_none_before_start_and_after_end():
    fx = [
        _fx(2, datetime(2026, 8, 21, 18, 30, tzinfo=UTC)),
        _fx(2, datetime(2026, 8, 23, 15, 30, tzinfo=UTC)),
    ]
    assert running_matchday(fx, datetime(2026, 8, 21, 12, 0, tzinfo=UTC)) is None
    done = [
        _fx(2, datetime(2026, 8, 21, 18, 30, tzinfo=UTC), finished=True),
        _fx(2, datetime(2026, 8, 23, 15, 30, tzinfo=UTC), finished=True),
    ]
    assert running_matchday(done, datetime(2026, 8, 23, 18, 0, tzinfo=UTC)) is None


def test_running_matchday_grace_closes_stale_finished_flags():
    """`is_finished` hängt manchmal — 3 h nach letztem Anpfiff gilt: vorbei."""
    fx = [_fx(2, datetime(2026, 8, 23, 15, 30, tzinfo=UTC), finished=False)]
    assert running_matchday(fx, datetime(2026, 8, 23, 17, 0, tzinfo=UTC)) == 2
    assert running_matchday(fx, datetime(2026, 8, 23, 20, 0, tzinfo=UTC)) is None


# ------------------------------------------------------------ Wächter-Planung

class _FakeScheduler:
    def __init__(self):
        self.jobs: list[tuple[str, datetime]] = []

    def add_job(self, func, trigger, id=None, replace_existing=False):  # noqa: A002
        self.jobs.append((id, trigger.run_date))


def _auth_state(leagues=None, user_id="u1"):
    """AuthState-Fake, wie ihn make_kickbase_client liefert (league_id/leagues/user_id)."""
    lgs = leagues if leagues is not None else [SimpleNamespace(id="L1")]
    return SimpleNamespace(
        token="t",
        user_id=user_id,
        leagues=lgs,
        league_id=lgs[0].id if lgs else None,
    )


def _patch_auth_and_fixtures(monkeypatch, fixtures):
    import kickbase_agent.worker.kickoff_guard as kg

    class FakeClient:
        async def get_matchdays(self):
            return fixtures

    async def fake_factory():
        return FakeClient(), _auth_state()

    monkeypatch.setattr(kg, "make_kickbase_client", fake_factory)


@pytest.mark.asyncio
async def test_guards_hang_on_matchday_deadline_not_next_game(monkeypatch):
    """Laufender Spieltag mit Sa-Spiel in 5 h: die Wächter zielen auf die
    Deadline des NÄCHSTEN Spieltags (in 50 h), nicht auf den Einzel-Anstoß."""
    now = datetime.now(UTC)
    fixtures = [
        _fx(2, now - timedelta(hours=20), finished=True),   # Spieltag 2 läuft
        _fx(2, now + timedelta(hours=5)),                   # Falle: Einzel-Anstoß
        _fx(3, now + timedelta(hours=50)),                  # echte nächste Deadline
        _fx(3, now + timedelta(hours=70)),
    ]
    _patch_auth_and_fixtures(monkeypatch, fixtures)
    sched = _FakeScheduler()
    result = await schedule_kickoff_guards(sched, lambda label, factory: factory, None)

    assert "Spieltag 3" in result
    assert len(sched.jobs) == 4
    deadline = now + timedelta(hours=50)
    expected = {
        f"kickoff-T-{m}-md3": deadline - timedelta(minutes=m) for m in (120, 60, 35, 15)
    }
    for job_id, run_at in sched.jobs:
        assert job_id in expected
        assert abs((run_at - expected[job_id]).total_seconds()) < 1
        # Kein Wächter darf am 5-h-Einzel-Anstoß des laufenden Spieltags hängen
        assert abs((run_at - (now + timedelta(hours=5))).total_seconds()) > 3600


@pytest.mark.asyncio
async def test_guards_skip_when_deadline_beyond_96h_horizon(monkeypatch):
    now = datetime.now(UTC)
    _patch_auth_and_fixtures(monkeypatch, [_fx(5, now + timedelta(hours=120))])
    sched = _FakeScheduler()
    result = await schedule_kickoff_guards(sched, lambda label, factory: factory, None)
    assert "keine Spieltags-Deadline" in result
    assert sched.jobs == []


# ------------------------------------------------- Catch-up nach Worker-Neustart

@pytest.mark.asyncio
async def test_restart_inside_window_catches_up_missed_guard_immediately(monkeypatch):
    """Neustart bei T-90: der T-120-Trigger läge in der Vergangenheit und würde
    von APScheduler verworfen → der Gebots-Storno wird SOFORT nachgeholt (Job in
    den nächsten Sekunden), T-60/T-35/T-15 bleiben normale Zukunfts-Jobs."""
    now = datetime.now(UTC)
    deadline = now + timedelta(minutes=90)
    _patch_auth_and_fixtures(monkeypatch, [_fx(4, deadline)])
    sched = _FakeScheduler()
    result = await schedule_kickoff_guards(sched, lambda label, factory: factory, None)

    assert "Spieltag 4" in result
    jobs = dict(sched.jobs)
    assert set(jobs) == {f"kickoff-T-{m}-md4" for m in (120, 60, 35, 15)}
    # T-120 (verstrichen): sofortiger Catch-up statt Vergangenheits-Trigger
    assert timedelta(0) < jobs["kickoff-T-120-md4"] - now < timedelta(minutes=2)
    assert "T-120 (verpasst → läuft SOFORT)" in result
    # Die übrigen drei hängen unverändert an der Deadline
    for m in (60, 35, 15):
        expected = deadline - timedelta(minutes=m)
        assert abs((jobs[f"kickoff-T-{m}-md4"] - expected).total_seconds()) < 1


@pytest.mark.asyncio
async def test_restart_shortly_before_deadline_catches_up_all_in_cascade_order(monkeypatch):
    """Neustart bei T-10: ALLE vier Fenster haben begonnen → alle werden sofort
    nachgeholt, gestaffelt in Kaskaden-Reihenfolge Storno → Ping → Elf → Purge."""
    now = datetime.now(UTC)
    _patch_auth_and_fixtures(monkeypatch, [_fx(5, now + timedelta(minutes=10))])
    sched = _FakeScheduler()
    await schedule_kickoff_guards(sched, lambda label, factory: factory, None)

    assert [job_id for job_id, _ in sched.jobs] == [
        "kickoff-T-120-md5", "kickoff-T-60-md5", "kickoff-T-35-md5", "kickoff-T-15-md5",
    ]
    run_dates = [run_at for _, run_at in sched.jobs]
    assert run_dates == sorted(run_dates)          # strikt: Storno vor Purge
    assert all(now < r < now + timedelta(minutes=2) for r in run_dates)
    assert run_dates[-1] < now + timedelta(minutes=10)  # alle VOR der Deadline


@pytest.mark.asyncio
async def test_restart_after_deadline_schedules_nothing(monkeypatch):
    """Neustart NACH der Deadline: Konto-/Elf-Prüfung ist gelaufen — für diesen
    Spieltag wird NICHTS nachgeholt (ein Notverkauf risse nur ein 0-Punkte-Loch)."""
    now = datetime.now(UTC)
    fixtures = [
        _fx(2, now - timedelta(minutes=30)),   # Deadline (erster Anpfiff) vorbei
        _fx(2, now + timedelta(hours=26)),     # spätere Partie DESSELBEN Spieltags
    ]
    _patch_auth_and_fixtures(monkeypatch, fixtures)
    sched = _FakeScheduler()
    result = await schedule_kickoff_guards(sched, lambda label, factory: factory, None)
    assert sched.jobs == []
    assert "keine Spieltags-Deadline" in result


# ------------------------------------------------- Notbremse: ehrliche Auswertung

class _Channel:
    def __init__(self):
        self.messages: list[str] = []

    async def send_info(self, text: str):
        self.messages.append(text)


def _sqp(pid: str, last: str, mv: int) -> SquadPlayer:
    return SquadPlayer(
        player=Player(id=pid, first_name="", last_name=last,
                      position=Position.DEFENDER, market_value=mv, team_id="1"),
        is_starter=False,
    )


def _arm_purge(monkeypatch):
    import kickbase_agent.config as cfg

    monkeypatch.setenv("AGENT_AUTO_PURGE", "1")
    cfg._settings = None


def _patch_purge_world(monkeypatch, budgets: list, statuses: list[str], squad=None):
    """Fabrik/Executor/_gather_state fälschen; echte Kaskaden-Logik.

    `budgets` wird Lesung für Lesung konsumiert; ein Exception-Eintrag (z. B.
    KickbaseDataError) wird bei seiner Lesung GEWORFEN — so testen wir
    „Kontostand unbekannt" an jeder Stelle der Kaskade.
    """
    import kickbase_agent.agent.executor as ex_mod
    import kickbase_agent.worker.kickoff_guard as kg

    calls: list[tuple[str, str | None, object]] = []
    the_squad = squad if squad is not None else [
        # Zwei Bankspieler à 1,5M — Kaskade/Auswahl laufen mit der ECHTEN Logik
        _sqp("b1", "BankEins", 1_500_000), _sqp("b2", "BankZwei", 1_500_000),
    ]

    class FakeClient:
        async def get_budget(self, lg):
            item = budgets.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        async def get_own_bids(self, lg, uid):
            return []

    async def fake_factory():
        return FakeClient(), _auth_state()

    async def fake_state(client, session, lg):
        return the_squad, set(), set(), {}

    class FakeExecutor:
        def __init__(self, client, session):
            pass

        async def execute(self, action, dry_run=None):
            calls.append((action.action_type, action.player_id, dry_run))
            return SimpleNamespace(status=statuses.pop(0))

    monkeypatch.setattr(kg, "make_kickbase_client", fake_factory)
    monkeypatch.setattr(kg, "_gather_state", fake_state)
    monkeypatch.setattr(ex_mod, "ActionExecutor", FakeExecutor)
    return calls


@pytest.mark.asyncio
async def test_purge_success_needs_executed_and_balance_above_buffer(monkeypatch):
    """Voller Erfolg NUR bei frisch gelesenem Konto ≥ PURGE_MIN_BALANCE (10.000 €)."""
    _arm_purge(monkeypatch)
    calls = _patch_purge_world(
        monkeypatch,
        # vorher / FRISCH nach Verkauf 1 (noch unterm Puffer) / FRISCH nach Verkauf 2
        budgets=[-2_000_000, -500_000, 1_200_000],
        statuses=["executed", "executed"],
    )
    ch = _Channel()
    assert await run_purge_enforce(ch) == "purged"
    assert calls == [("sell", "b1", False), ("sell", "b2", False)]
    msg = ch.messages[-1]
    assert "NOTBREMSE ausgeführt" in msg
    assert "-2.00M → 1.20M" in msg                # echte Zahlen, frisch von der API
    assert "TEIL-Erfolg" not in msg


@pytest.mark.asyncio
async def test_purge_positive_but_below_buffer_is_partial_success(monkeypatch):
    """Zustand 2 (Review-Fund): Konto positiv, aber unter dem 10.000-€-Puffer —
    vorher kam hier die volle Erfolgsmeldung, jetzt ein ⚠️-Teil-Erfolg."""
    _arm_purge(monkeypatch)
    calls = _patch_purge_world(
        monkeypatch,
        # vorher / FRISCH nach dem einzigen möglichen Verkauf: 5.000 € (< Puffer)
        budgets=[-1_000_000, 5_000],
        statuses=["executed"],
        squad=[_sqp("b1", "BankEins", 1_500_000)],   # Kaskade danach erschöpft
    )
    ch = _Channel()
    assert await run_purge_enforce(ch) == "purged_below_buffer"
    assert calls == [("sell", "b1", False)]
    msg = ch.messages[-1]
    assert "TEIL-Erfolg" in msg
    assert "unter dem" in msg and "10.000" in msg and "beobachten" in msg
    assert "NOTBREMSE ausgeführt" not in msg       # keine Jubelmeldung
    assert "FEHLGESCHLAGEN" not in msg             # aber auch kein Fehlalarm


@pytest.mark.asyncio
async def test_purge_exact_buffer_counts_as_full_success(monkeypatch):
    """Randfall: FRISCH gelesenes Konto exakt auf dem Puffer (10.000 €) = Erfolg."""
    _arm_purge(monkeypatch)
    _patch_purge_world(
        monkeypatch,
        budgets=[-1_000_000, 10_000],
        statuses=["executed"],
        squad=[_sqp("b1", "BankEins", 1_500_000)],
    )
    ch = _Channel()
    assert await run_purge_enforce(ch) == "purged"
    assert "NOTBREMSE ausgeführt" in ch.messages[-1]


@pytest.mark.asyncio
async def test_purge_all_executed_but_still_negative_alarms(monkeypatch):
    """Zustand 3: alle Verkäufe 'executed', Konto trotzdem im Minus → Alarm."""
    _arm_purge(monkeypatch)
    calls = _patch_purge_world(
        monkeypatch,
        budgets=[-4_000_000, -700_000],              # Erlös reichte nicht
        statuses=["executed"],
        squad=[_sqp("b1", "BankEins", 1_500_000)],   # mehr gibt der Kader nicht her
    )
    ch = _Channel()
    assert await run_purge_enforce(ch) == "purge_failed"
    assert calls == [("sell", "b1", False)]
    msg = ch.messages[-1]
    assert "NOTBREMSE FEHLGESCHLAGEN" in msg
    assert "im Minus" in msg and "manuell" in msg
    assert "TEIL-Erfolg" not in msg and "NOTBREMSE ausgeführt" not in msg


@pytest.mark.asyncio
async def test_purge_refused_posts_alarm_never_success(monkeypatch):
    """Executor sagt refused (z. B. Interlock) → Alarm mit Status je Spieler,
    niemals eine Erfolgsmeldung."""
    _arm_purge(monkeypatch)
    _patch_purge_world(
        monkeypatch,
        budgets=[-2_000_000, -2_000_000],          # Konto steht weiter im Minus
        statuses=["refused", "refused"],
    )
    ch = _Channel()
    assert await run_purge_enforce(ch) == "purge_failed"
    msg = ch.messages[-1]
    assert "NOTBREMSE FEHLGESCHLAGEN" in msg
    assert "refused" in msg                        # Status je Spieler
    assert "im Minus" in msg and "manuell" in msg
    assert "NOTBREMSE ausgeführt" not in msg


@pytest.mark.asyncio
async def test_purge_partial_failure_is_no_success(monkeypatch):
    """Ein Verkauf klemmt → auch bei Konto ≥ 0 keine Jubelmeldung, sondern Alarm."""
    _arm_purge(monkeypatch)
    _patch_purge_world(
        monkeypatch,
        # Verkauf 1 klemmt (kein Re-Read), Verkauf 2 klappt → FRISCH: 0,5M
        budgets=[-2_000_000, 500_000],
        statuses=["failed", "executed"],
    )
    ch = _Channel()
    assert await run_purge_enforce(ch) == "purge_failed"
    msg = ch.messages[-1]
    assert "NOTBREMSE FEHLGESCHLAGEN" in msg and "failed" in msg
    assert "unvollständig" in msg


@pytest.mark.asyncio
async def test_purge_quiet_when_balance_ok(monkeypatch):
    _arm_purge(monkeypatch)
    _patch_purge_world(monkeypatch, budgets=[5_000_000], statuses=[])
    ch = _Channel()
    assert await run_purge_enforce(ch) == "ok"
    assert ch.messages == []


@pytest.mark.asyncio
async def test_purge_stops_selling_once_fresh_balance_covers_buffer(monkeypatch):
    """Inkrementelle Kaskade: 3 Kandidaten geplant, aber nach Verkauf 1 steht das
    FRISCH gelesene Konto überm Puffer → genau EIN Verkauf, Meldung nennt ihn."""
    _arm_purge(monkeypatch)
    calls = _patch_purge_world(
        monkeypatch,
        budgets=[-4_000_000, 1_200_000],   # vorher / FRISCH nach Verkauf 1: Puffer steht
        statuses=["executed"],             # ein 2. Verkauf würde hier laut crashen
        squad=[
            _sqp("b1", "BankEins", 1_500_000),
            _sqp("b2", "BankZwei", 1_500_000),
            _sqp("b3", "BankDrei", 1_500_000),
        ],
    )
    ch = _Channel()
    assert await run_purge_enforce(ch) == "purged"
    assert calls == [("sell", "b1", False)]            # genau EIN Verkauf
    msg = ch.messages[-1]
    assert "NOTBREMSE ausgeführt" in msg
    assert "BankEins" in msg
    assert "BankZwei" not in msg and "BankDrei" not in msg
    assert "1 von 3" in msg                             # ehrliche Zählung
    assert "-4.00M → 1.20M" in msg


@pytest.mark.asyncio
async def test_purge_continues_past_the_preselection_when_sales_are_refused(monkeypatch):
    """Fehlgeschlagene Verkäufe dürfen die Notbremse nicht vorzeitig beenden.

    Codex-Fund 27.08.: `select_cascade_sells` schätzt vorab ein Präfix der
    Kaskade, und die Ausführung lief NUR über dieses Präfix. Wird davon etwas
    `refused` oder `failed` (Preflight, Schutzliste, API-Fehler), meldete die
    Notbremse Misserfolg, obwohl in der Kaskade noch verkäufliche Spieler
    standen — bei negativem Konto kostet das den ganzen Spieltag. Jetzt läuft
    sie durch die VOLLE Kaskade und stoppt erst, wenn das frisch gelesene
    Konto den Puffer trägt.
    """
    _arm_purge(monkeypatch)
    calls = _patch_purge_world(
        monkeypatch,
        # Start -2,00 M; Re-Read nur nach dem einen echten Verkauf
        budgets=[-2_000_000, 1_200_000],
        statuses=["refused", "failed", "executed"],
        squad=[
            _sqp("b1", "BankEins", 1_500_000),
            _sqp("b2", "BankZwei", 1_500_000),
            _sqp("b3", "BankDrei", 3_500_000),   # ausserhalb der Vorauswahl
        ],
    )
    ch = _Channel()
    # Rueckgabe bleibt bewusst "purge_failed": Klemmt unterwegs etwas, gibt es
    # Alarm statt Jubel (test_purge_partial_failure_is_no_success). Neu ist
    # allein, dass die Schleife bis b3 WEITERGEHT — der Spieltag ist damit
    # gerettet, auch wenn die Meldung ehrlich von Klemmern spricht.
    assert await run_purge_enforce(ch) == "purge_failed"
    assert calls == [
        ("sell", "b1", False), ("sell", "b2", False), ("sell", "b3", False),
    ]
    assert "1.20M" in ch.messages[-1]   # Konto steht trotz der zwei Klemmer


@pytest.mark.asyncio
async def test_purge_budget_unknown_alarms_and_sells_nothing(monkeypatch):
    """/me/budget ohne `b` → Zustand UNBEKANNT: Alarm-Ping, aber NIEMALS blind
    den halben Kader verkaufen (das wäre der alte „fehlendes b = 0 €"-Bug)."""
    _arm_purge(monkeypatch)
    calls = _patch_purge_world(
        monkeypatch,
        budgets=[KickbaseDataError("/me/budget ohne Kontostand-Feld 'b'")],
        statuses=[],
    )
    ch = _Channel()
    assert await run_purge_enforce(ch) == "budget_unknown"
    assert calls == []                                  # KEIN einziger Verkauf
    msg = ch.messages[-1]
    assert "UNBEKANNT" in msg and "App" in msg
    assert "ausgeführt" not in msg


@pytest.mark.asyncio
async def test_purge_stops_when_fresh_reread_fails_mid_cascade(monkeypatch):
    """Re-Read nach Verkauf 1 liefert keinen Kontostand → Kaskade STOPPT sofort
    (kein Blindverkauf der restlichen Kandidaten), Alarm statt Jubelmeldung."""
    _arm_purge(monkeypatch)
    calls = _patch_purge_world(
        monkeypatch,
        budgets=[-4_000_000, KickbaseDataError("/me/budget ohne Kontostand-Feld 'b'")],
        statuses=["executed"],
        squad=[
            _sqp("b1", "BankEins", 1_500_000),
            _sqp("b2", "BankZwei", 1_500_000),
            _sqp("b3", "BankDrei", 1_500_000),
        ],
    )
    ch = _Channel()
    assert await run_purge_enforce(ch) == "purge_failed"
    assert calls == [("sell", "b1", False)]
    msg = ch.messages[-1]
    assert "UNBEKANNT" in msg and "BankEins" in msg
    assert "BankZwei" not in msg
    assert "NOTBREMSE ausgeführt" not in msg


# ------------------------------------------------- T-120-Storno: ehrliche Auswertung

def _patch_bid_world(monkeypatch, budget, bids: list[dict], statuses: list[str]):
    import kickbase_agent.agent.executor as ex_mod
    import kickbase_agent.worker.kickoff_guard as kg

    class FakeClient:
        async def get_budget(self, lg):
            if isinstance(budget, Exception):
                raise budget
            return budget

        async def get_own_bids(self, lg, uid):
            return bids

    async def fake_factory():
        return FakeClient(), _auth_state()

    class FakeExecutor:
        def __init__(self, client, session):
            pass

        async def execute(self, action, dry_run=None):
            return SimpleNamespace(status=statuses.pop(0))

    monkeypatch.setattr(kg, "make_kickbase_client", fake_factory)
    monkeypatch.setattr(ex_mod, "ActionExecutor", FakeExecutor)


_BID = {"name": "Groß", "amount": 5_000_000, "player_id": "p9", "offer_id": "o1"}


@pytest.mark.asyncio
async def test_bid_cancel_success_only_when_executed(monkeypatch):
    _arm_purge(monkeypatch)
    _patch_bid_world(monkeypatch, budget=1_000_000, bids=[_BID], statuses=["executed"])
    ch = _Channel()
    assert await run_bid_cancel_guard(ch) == "cancelled"
    assert "Gebots-Storno ausgeführt" in ch.messages[-1]


@pytest.mark.asyncio
async def test_bid_cancel_failure_posts_alarm_not_success(monkeypatch):
    _arm_purge(monkeypatch)
    _patch_bid_world(monkeypatch, budget=1_000_000, bids=[_BID], statuses=["failed"])
    ch = _Channel()
    assert await run_bid_cancel_guard(ch) == "cancel_failed"
    msg = ch.messages[-1]
    assert "STORNO FEHLGESCHLAGEN" in msg
    assert "Groß → failed" in msg and "manuell" in msg
    assert "Storno ausgeführt" not in msg


@pytest.mark.asyncio
async def test_bid_cancel_budget_unknown_alarms_without_cancelling(monkeypatch):
    """Kontostand unbekannt → kein Storno-Entscheid möglich: Alarm statt Aktion.
    (Ein Executor-Aufruf würde am leeren statuses-Stapel laut crashen.)"""
    _arm_purge(monkeypatch)
    _patch_bid_world(
        monkeypatch,
        budget=KickbaseDataError("/me/budget ohne Kontostand-Feld 'b'"),
        bids=[_BID],
        statuses=[],
    )
    ch = _Channel()
    assert await run_bid_cancel_guard(ch) == "budget_unknown"
    msg = ch.messages[-1]
    assert "T-120" in msg and "UNBEKANNT" in msg and "App" in msg


@pytest.mark.asyncio
async def test_debt_ping_budget_unknown_alarms(monkeypatch):
    """T-60 mit unbekanntem Kontostand: Alarm-Ping statt „Konto ok"-Wunschdenken."""
    _patch_bid_world(
        monkeypatch,
        budget=KickbaseDataError("/me/budget ohne Kontostand-Feld 'b'"),
        bids=[],
        statuses=[],
    )
    ch = _Channel()
    assert await run_debt_ping(ch) == "budget_unknown"
    msg = ch.messages[-1]
    assert "T-60" in msg and "UNBEKANNT" in msg
    assert "Konto ok" not in msg
