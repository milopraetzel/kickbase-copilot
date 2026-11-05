"""Auto-Aufstellungs-Job: Discord-Nachricht je Zustand (gesetzt/Dry-Run/Fallback)."""

from types import SimpleNamespace

import pytest

from kickbase_agent.decision.lineup import Lineup
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.worker.lineup_job import format_lineup_message


def _c(pid: str, name: str, pos: int) -> ExplosionCandidate:
    return ExplosionCandidate(player_id=pid, name=name, team_id="1", position=pos,
                              current_market_value=1, total_score=0.5, signals=[])


_LINEUP = Lineup(
    formation="4-4-2",
    starters=[_c("k", "Marvin Schwäbe", 1)]
    + [_c(f"d{i}", f"Abw N{i}", 2) for i in range(4)]
    + [_c(f"m{i}", f"Mit N{i}", 3) for i in range(4)]
    + [_c(f"s{i}", f"Stu N{i}", 4) for i in range(2)],
    bench=[],
    expected_score=4.0,
)


def test_armed_executed_message():
    msg = format_lineup_message(_LINEUP, "executed", armed=True)
    assert "Aufstellung gesetzt" in msg and "4-4-2" in msg
    assert "Schwäbe" in msg
    assert "überschreibbar" in msg


def test_dry_run_message_shows_arming_hint():
    msg = format_lineup_message(_LINEUP, "skipped_dry_run", armed=False)
    assert "DRY-RUN" in msg and "AGENT_AUTO_LINEUP=1" in msg


def test_failed_message_asks_manual():
    msg = format_lineup_message(_LINEUP, "failed", armed=True)
    assert "fehlgeschlagen" in msg and "manuell" in msg


def test_incomplete_squad_fallback():
    empty = Lineup(formation="", starters=[], bench=[], expected_score=0.0)
    msg = format_lineup_message(empty, "no_lineup", armed=True)
    assert "bisherige bleibt" in msg


def test_next_kickoff_picks_earliest_future():
    from datetime import UTC, datetime

    from kickbase_agent.kickbase.models import Fixture
    from kickbase_agent.worker.kickoff_guard import next_kickoff
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    fx = [
        Fixture(matchday=1, home_team_id="1", away_team_id="2",
                kickoff=datetime(2026, 8, 21, 18, 30, tzinfo=UTC)),
        Fixture(matchday=1, home_team_id="3", away_team_id="4",
                kickoff=datetime(2026, 8, 22, 13, 30, tzinfo=UTC)),
        Fixture(matchday=1, home_team_id="5", away_team_id="6",
                kickoff=datetime(2026, 8, 20, 18, 30, tzinfo=UTC), is_finished=True),
    ]
    assert next_kickoff(fx, now) == datetime(2026, 8, 21, 18, 30, tzinfo=UTC)
    assert next_kickoff([], now) is None


def test_closing_bell_only_for_long_term_squad_injuries():
    from kickbase_agent.kickbase.models import Player, Position, SquadPlayer
    from kickbase_agent.scouts.ligainsider.models import InjuryEntry
    from kickbase_agent.worker.kickoff_guard import closing_bell_lines

    def sp(last):
        return SquadPlayer(player=Player(id=last, first_name="", last_name=last,
                                         position=Position.DEFENDER,
                                         market_value=5_000_000, team_id="1"),
                           is_starter=False)

    squad = [sp("Minjae"), sp("Vogt")]
    injuries = [
        InjuryEntry("2", "Minjae Kim", "Kreuzbandriss"),      # lang → Alarm
        InjuryEntry("14", "Kevin Vogt", "angeschlagen"),       # kurz → kein Alarm
        InjuryEntry("3", "Emre Can", "Kreuzbandriss"),         # nicht im Kader
    ]
    lines = closing_bell_lines(squad, injuries)
    assert len(lines) == 1 and "Minjae" in lines[0] and "VOR 22:00" in lines[0]


def _sqp(pid, last, mv, pos=2, prob=None):
    from kickbase_agent.kickbase.models import Player, Position, SquadPlayer
    pmap = {1: Position.GOALKEEPER, 2: Position.DEFENDER, 3: Position.MIDFIELDER}
    return SquadPlayer(player=Player(id=pid, first_name="", last_name=last,
                                     position=pmap.get(pos, Position.DEFENDER),
                                     market_value=mv, team_id="1",
                                     lineup_prob=prob), is_starter=False)


def test_purge_cascade_hierarchy_trading_bench_fielded_keeper():
    """Leitfaden-Kaskade: S1 Trading (Momentum) → S2 Bank (MV) → S3 Startelf → TW zuletzt."""
    from kickbase_agent.worker.kickoff_guard import build_purge_cascade

    squad = [
        _sqp("t1", "TraderA", 5_000_000), _sqp("t2", "TraderB", 4_000_000),
        _sqp("b1", "BankTeuer", 6_000_000), _sqp("b2", "BankBillig", 2_000_000),
        _sqp("f1", "StarterBillig", 3_000_000), _sqp("f2", "StarterTeuer", 20_000_000),
        _sqp("gk", "Keeper", 14_000_000, pos=1),
    ]
    cascade = build_purge_cascade(
        squad,
        lineup_ids={"f1", "f2", "gk"},
        trading_ids={"t1", "t2"},
        momentum_by_id={"t1": 0.5, "t2": -0.3},   # t2 schwächer → zuerst
    )
    order = [(n, stufe) for _, n, _, stufe in cascade]
    assert order == [
        ("TraderB", "S1 Trading"), ("TraderA", "S1 Trading"),
        ("BankBillig", "S2 Bank"), ("BankTeuer", "S2 Bank"),
        ("StarterBillig", "S3 Startelf!"), ("StarterTeuer", "S3 Startelf!"),
        ("Keeper", "S3 Torwart!!"),
    ]


def test_purge_cascade_sells_non_playing_starters_before_real_ones():
    """S3 sortiert nach NUTZEN, nicht nach Preis.

    Reale Lage 27.08.26: Der Kader hatte nur 12 Spieler, also standen auch
    Hoffmeier (prob 5) und Leszczynski (prob 4) zwangsweise in der Elf — sie
    fielen damit aus S1/S2 heraus und landeten in S3. Bei reiner Preis-
    Sortierung hätte die T-15-Notbremse zuerst Schäfer und Højlund (beide
    prob 3, echte Starter, aber billiger) verkauft und die beiden Nieten
    behalten. Wer laut Startelf-Stufe ohnehin nicht spielt, kostet kein
    Punkt-Potenzial und geht deshalb zuerst.
    """
    from kickbase_agent.worker.kickoff_guard import build_purge_cascade

    squad = [
        _sqp("s1", "Schäfer", 5_030_000, pos=3, prob=3),
        _sqp("s2", "Højlund", 5_270_000, pos=3, prob=3),
        _sqp("d1", "Hoffmeier", 6_530_000, prob=5),
        _sqp("d2", "Leszczynski", 6_840_000, prob=4),
        _sqp("v", "Vogt", 800_000, pos=3, prob=5),
    ]
    cascade = build_purge_cascade(
        squad, lineup_ids={"s1", "s2", "d1", "d2", "v"}, trading_ids=set()
    )
    assert [n for _, n, _, _ in cascade] == [
        # spielen ohnehin nicht → zuerst, und TEUERSTE zuerst: sie decken den
        # Fehlbetrag mit den wenigsten Verkäufen, und ihr Marktwert ist totes
        # Kapital, das ohnehin weg soll (Codex-Fund 27.08.)
        "Leszczynski", "Hoffmeier", "Vogt",
        # echte Starter → letzte Rettung, hier billigste zuerst (so wenig
        # Punkt-Potenzial wie möglich abgeben)
        "Schäfer", "Højlund",
    ]
    stufen = {n: st for _, n, _, st in cascade}
    assert stufen["Hoffmeier"] == "S3 Elf (Start unwahrsch.)"
    assert stufen["Schäfer"] == "S3 Startelf!"


def test_purge_cascade_covers_deficit_without_an_extra_sale():
    """Kaskade UND Selektor zusammen: keine Verkäufe über den Bedarf hinaus.

    Codex-Fund 27.08.: `select_cascade_sells` nimmt gierig ein Präfix. Standen
    die nutzlosen Spieler billigste-zuerst, schleppte die Deckung von -14,93 M
    den 0,80-M-Vogt mit, obwohl Leszczynski + Hoffmeier + Reggiani (16,82 M)
    schon reichen — ein Kaderplatz weniger, ohne einen Cent Nutzen. Die Elf
    braucht 11 Bodies; bei 12 Kaderspielern ist genau das der Unterschied
    zwischen "Formation steht" und "steht nicht".
    """
    from kickbase_agent.worker.kickoff_guard import build_purge_cascade, select_cascade_sells

    squad = [
        _sqp("s1", "Schäfer", 5_030_000, pos=3, prob=3),
        _sqp("s2", "Højlund", 5_270_000, pos=3, prob=3),
        _sqp("d1", "Hoffmeier", 6_530_000, prob=5),
        _sqp("d2", "Leszczynski", 6_840_000, prob=4),
        _sqp("v", "Vogt", 800_000, pos=3, prob=5),
        _sqp("r", "Reggiani", 3_450_000, prob=4),
    ]
    cascade = build_purge_cascade(
        squad,
        lineup_ids={"s1", "s2", "d1", "d2", "v"},   # Reggiani sitzt auf der Bank
        trading_ids={"r"},                            # und ist gelistet → S1
    )
    sells = select_cascade_sells(cascade, budget=-14_933_731)
    namen = [n for _, n, _, _ in sells]
    assert namen == ["Reggiani", "Leszczynski", "Hoffmeier"]
    assert sum(mv for _, _, mv, _ in sells) >= 14_933_731 + 10_000
    assert "Vogt" not in namen          # der Extra-Verkauf, den Codex fand
    assert "Schäfer" not in namen and "Højlund" not in namen


def test_purge_cascade_leaves_protected_players_out(monkeypatch):
    """Verfassung schlägt Notbremse: Olise steht nicht einmal in der Kaskade.
    Der Executor würde ihn ohnehin verweigern — aber ein Vorschlag, der
    garantiert `refused` endet, verbrennt nur Zeit vor dem Anpfiff."""
    from kickbase_agent.worker.kickoff_guard import build_purge_cascade

    squad = [_sqp("8329", "Olise", 60_000_000, pos=3), _sqp("b2", "Bank", 2_000_000)]
    cascade = build_purge_cascade(squad, lineup_ids=set(), trading_ids=set())
    assert [n for _, n, _, _ in cascade] == ["Bank"]

    monkeypatch.setenv("PROTECTED_PLAYER_IDS", "")
    ohne = build_purge_cascade(squad, lineup_ids=set(), trading_ids=set())
    assert sorted(n for _, n, _, _ in ohne) == ["Bank", "Olise"]


def test_cascade_sells_stop_at_buffer():
    from kickbase_agent.worker.kickoff_guard import select_cascade_sells
    cascade = [("a", "A", 2_000_000, "S1 Trading"), ("b", "B", 3_000_000, "S2 Bank"),
               ("c", "C", 9_000_000, "S3 Startelf!")]
    sells = select_cascade_sells(cascade, budget=-4_000_000, buffer=10_000)
    assert [n for _, n, _, _ in sells] == ["A", "B"]   # 5M reicht — C bleibt!


def _patch_lineup_world(monkeypatch, fixtures, set_calls):
    """Fabrik/run_lineup_set im Modul-Namensraum von lineup_job fälschen."""
    from types import SimpleNamespace

    import kickbase_agent.worker.lineup_job as lj

    class FakeClient:
        async def get_matchdays(self):
            return fixtures

    async def fake_factory():
        auth = SimpleNamespace(
            token="t", user_id="u1", leagues=[SimpleNamespace(id="L1")], league_id="L1"
        )
        return FakeClient(), auth

    async def fake_set(channel):
        set_calls.append(channel)
        return "executed"

    monkeypatch.setattr(lj, "make_kickbase_client", fake_factory)
    monkeypatch.setattr(lj, "run_lineup_set", fake_set)


def _fx(md, kickoff, finished=False):
    from kickbase_agent.kickbase.models import Fixture
    return Fixture(matchday=md, home_team_id="1", away_team_id="2",
                   kickoff=kickoff, is_finished=finished)


@pytest.mark.asyncio
async def test_lineup_cron_skips_while_matchday_running(monkeypatch, caplog):
    """Sa 10:00 im laufenden Spieltag: Elf ist seit Fr eingefroren → Skip + Log,
    kein Write, kein Fehler."""
    import logging
    from datetime import UTC, datetime, timedelta

    from kickbase_agent.worker.lineup_job import run_lineup_set_gated

    now = datetime.now(UTC)
    fixtures = [
        _fx(2, now - timedelta(hours=14), finished=True),   # Fr-Spiel vorbei
        _fx(2, now + timedelta(hours=5)),                   # Sa-Spiel steht noch aus
    ]
    calls = []
    _patch_lineup_world(monkeypatch, fixtures, calls)
    with caplog.at_level(logging.INFO, logger="kickbase_agent.worker.lineup_job"):
        status = await run_lineup_set_gated(channel=None)
    assert status == "locked"
    assert calls == []                                       # kein Lineup-Write
    assert "gesperrt bis Spieltag 2" in caplog.text


@pytest.mark.asyncio
async def test_lineup_cron_runs_before_matchday_deadline(monkeypatch):
    """Fr 19:45 vor der 20:30-Deadline (oder Sa-Start-Spieltag): Set läuft normal."""
    from datetime import UTC, datetime, timedelta

    from kickbase_agent.worker.lineup_job import run_lineup_set_gated

    now = datetime.now(UTC)
    fixtures = [_fx(3, now + timedelta(minutes=45)), _fx(3, now + timedelta(hours=20))]
    calls = []
    _patch_lineup_world(monkeypatch, fixtures, calls)
    assert await run_lineup_set_gated(channel=None) == "executed"
    assert len(calls) == 1


def test_bid_cancel_selection_largest_first_until_safe():
    """T-120: größte Gebote streichen, bis kein Zuschlag mehr ins Minus führen kann."""
    from kickbase_agent.worker.kickoff_guard import select_bids_to_cancel
    bids = [
        {"name": "Klein", "amount": 2_000_000, "player_id": "1", "offer_id": "u"},
        {"name": "Groß", "amount": 20_000_000, "player_id": "2", "offer_id": "u"},
        {"name": "Mittel", "amount": 8_000_000, "player_id": "3", "offer_id": "u"},
    ]
    # Konto 12M, Exposure 30M → −18M möglich → Groß (20M) streichen reicht
    out = select_bids_to_cancel(bids, budget=12_000_000)
    assert [b["name"] for b in out] == ["Groß"]
    # Konto 25M, Exposure 30M → alle Zuschläge zusammen = −5M → nur Groß weg
    out2 = select_bids_to_cancel(bids, budget=25_000_000)
    assert [b["name"] for b in out2] == ["Groß"]
    # Konto 40M → nichts nötig
    assert select_bids_to_cancel(bids, budget=40_000_000) == []


# --------------------------------------------------------- Elf-Lücken-Wächter


class _FakeSquadPlayer:
    """Nur das, was der Wächter liest: Spieler-ID und Platz in der Aufstellung."""

    _seq = 0

    def __init__(self, lineup_order: int | None, pid: str | None = None) -> None:
        _FakeSquadPlayer._seq += 1
        self.lineup_order = lineup_order
        self.player = SimpleNamespace(id=pid or f"p{_FakeSquadPlayer._seq}")


class _FakeClient:
    def __init__(self, squad: list[_FakeSquadPlayer]) -> None:
        self._squad = squad

    async def get_matchdays(self):
        return []          # keine Fixtures ⇒ kein laufender Spieltag ⇒ nicht gesperrt

    async def get_squad(self, _lg):
        return self._squad


class _Kanal:
    def __init__(self) -> None:
        self.posts: list[str] = []

    async def send_info(self, text: str) -> None:
        self.posts.append(text)


def _patch(monkeypatch, squad, set_status="executed", trade=frozenset()):
    from kickbase_agent.worker import lineup_job as job

    async def _client():
        class _Auth:
            league_id = "L1"
        return _FakeClient(squad), _Auth()

    async def _set(_channel):
        return set_status

    monkeypatch.setattr(job, "make_kickbase_client", _client)
    monkeypatch.setattr(job, "run_lineup_set", _set)
    monkeypatch.setattr(job, "_trade_ids", lambda squad_ids: frozenset(trade) & squad_ids)
    return job


@pytest.mark.asyncio
async def test_gap_check_is_quiet_when_lineup_is_full(monkeypatch):
    """Elf voll: kein teurer Neuaufbau, keine Nachricht."""
    squad = [_FakeSquadPlayer(i) for i in range(11)] + [_FakeSquadPlayer(None)]
    job = _patch(monkeypatch, squad)
    kanal = _Kanal()
    assert await job.run_lineup_gap_check(kanal) == "ok (11/11)"
    assert kanal.posts == []


@pytest.mark.asyncio
async def test_gap_check_closes_hole_left_by_a_sale(monkeypatch):
    """Der reale Fall vom 27.08.: Kader 11, aber nur 10 aufgestellt — ein Abgang
    nach dem letzten Aufstellen. Jeder leere Platz kostet 100 Punkte."""
    squad = [_FakeSquadPlayer(i) for i in range(10)] + [_FakeSquadPlayer(None)]
    job = _patch(monkeypatch, squad)
    kanal = _Kanal()
    assert await job.run_lineup_gap_check(kanal) == "gap_closed:executed"
    assert len(kanal.posts) == 1 and "Elf-Lücke geschlossen" in kanal.posts[0]
    assert "100 Punkte" in kanal.posts[0]


@pytest.mark.asyncio
async def test_gap_check_accepts_a_squad_smaller_than_eleven(monkeypatch):
    """Bei 9 Spielern sind 9 aufgestellte das Maximum — kein Neuaufbau, keine
    Nachricht. Aber der Status ist nicht „ok": zwei Plätze bleiben leer (je 100
    Punkte). Turnaround-Studie P0-4: 1049 „ok (8/8)"-Läufe bei Kader 8."""
    squad = [_FakeSquadPlayer(i) for i in range(9)]
    job = _patch(monkeypatch, squad)
    kanal = _Kanal()
    status = await job.run_lineup_gap_check(kanal)
    assert status == "unvollständig (9/11)"
    assert not status.startswith("ok")
    assert kanal.posts == []


@pytest.mark.asyncio
async def test_gap_check_never_reports_ok_for_a_squad_of_eight(monkeypatch):
    squad = [_FakeSquadPlayer(i) for i in range(8)]
    job = _patch(monkeypatch, squad)
    assert await job.run_lineup_gap_check(_Kanal()) == "unvollständig (8/11)"


@pytest.mark.asyncio
async def test_gap_check_stays_silent_when_the_hole_cannot_be_closed(monkeypatch):
    """Trägt der Kader keine Formation, meldet der Aufstellungs-Job das selbst —
    der Wächter legt NICHT alle 30 Minuten dieselbe Nachricht nach."""
    squad = [_FakeSquadPlayer(i) for i in range(10)] + [_FakeSquadPlayer(None)]
    job = _patch(monkeypatch, squad, set_status="no_lineup")
    kanal = _Kanal()
    assert await job.run_lineup_gap_check(kanal) == "gap_open:no_lineup"
    assert kanal.posts == []


@pytest.mark.asyncio
async def test_gap_check_does_not_count_trade_players(monkeypatch):
    """Handels-Autopilot (Emil 01.10.2026: „sie bleiben Bank“): 10 Stammspieler aufgestellt,
    zwei Handelsspieler auf der Bank — das ist der volle Kader für die Elf, kein Neuaufbau."""
    squad = ([_FakeSquadPlayer(i) for i in range(10)]
             + [_FakeSquadPlayer(None, "t1"), _FakeSquadPlayer(None, "t2")])
    job = _patch(monkeypatch, squad, trade={"t1", "t2"})
    kanal = _Kanal()
    assert await job.run_lineup_gap_check(kanal) == "unvollständig (10/11)"
    assert kanal.posts == []


@pytest.mark.asyncio
async def test_gap_check_moves_a_fielded_trade_player_to_the_bench(monkeypatch):
    """Steht ein Handelsspieler in der Elf, sperrt der Starter-Schutz seinen Verkauf — der
    Wächter setzt neu (ohne ihn)."""
    squad = [_FakeSquadPlayer(i) for i in range(10)] + [_FakeSquadPlayer(10, "t1")]
    job = _patch(monkeypatch, squad, trade={"t1"})
    assert await job.run_lineup_gap_check(_Kanal()) == "handel_bank:executed"


@pytest.mark.asyncio
async def test_the_lineup_is_set_without_trade_players(monkeypatch):
    from kickbase_agent.worker import lineup_job as job

    seen: dict = {}
    squad = [SimpleNamespace(player=SimpleNamespace(id=pid)) for pid in ("a", "t")]
    plan = SimpleNamespace(squad=squad, trade_player_ids=frozenset({"t"}), league_id="L1")
    plan.lineup_squad = [sp for sp in squad if sp.player.id not in plan.trade_player_ids]

    async def build():
        return plan

    async def client():
        return object(), SimpleNamespace(league_id="L1")

    async def recommend(_client, _session, squad):
        seen["ids"] = [sp.player.id for sp in squad]
        return SimpleNamespace(lineup=SimpleNamespace(starters=[]))

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(job, "build_plan", build)
    monkeypatch.setattr(job, "make_kickbase_client", client)
    monkeypatch.setattr(job, "recommend_lineup", recommend)
    monkeypatch.setattr(job, "SessionLocal", _Session)

    assert await job.run_lineup_set(_Kanal()) == "no_lineup"
    assert seen["ids"] == ["a"]
