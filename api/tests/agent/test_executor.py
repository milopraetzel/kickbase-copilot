"""ActionExecutor: Dry-Run, Test-Liga-Interlock, Idempotenz, Audit-Log."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from kickbase_agent.agent.executor import ActionExecutor, PlannedAction, PreflightState
from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS

NOW = datetime(2026, 8, 13, 22, 15, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _squad_member(pid: str, market_value: int, position: int = 3, team_id: str = "T1"):
    return SimpleNamespace(
        player=SimpleNamespace(id=pid, market_value=market_value, position=position,
                               team_id=team_id)
    )


def _far_fixture():
    """Ein Fixture weit in der Zukunft: Deadline bekannt, Fenster inaktiv."""
    return SimpleNamespace(
        kickoff=datetime.now(UTC) + timedelta(days=10), matchday=99, is_finished=False
    )


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple] = []
        self.fail = fail
        # Daten für den automatisch verdrahteten default_preflight: großzügiges
        # Budget + Spieler 99 zum Staging-Preis gelistet, damit Live-Tests den
        # Preflight passieren. Kader: DECKUNGSFÄHIGE 12 (seit dem zentralen
        # Deckungs-Preflight, Codex-Re-Check 24.08.: nach EINEM Verkauf bleiben
        # 11 + stellbare Formation); 9 (Notbremse) und 99 (Verkaufskandidat)
        # sind die Spieler der sell/list-Tests. Reads landen bewusst NICHT in
        # `calls` (die Tests zählen dort nur Writes).
        self.budget = 99_000_000
        # Markt-Player mit MW + Club: die zentrale Overpay-Kappe (110 % MW) und
        # das Vereinslimit lesen beide Felder — MW 20 M lässt alle Standard-
        # Gebote der Tests unter der Kappe, die 33-%-Tests rechnen weiter über
        # den Kader-Teamwert.
        self.market = [SimpleNamespace(
            player=SimpleNamespace(id="99", market_value=20_000_000, team_id="11"),
            price=5_000_000,
        )]
        self.league_rules = {"max_players_per_club": 11}
        self.own_bids: list[dict] = []
        # Fueller mit MW 0: die 33-%-Tests rechnen mit Teamwert GENAU 7 M
        # (9 = 2 M + 99 = 5 M) wie vor dem Kader-Ausbau.
        self.squad = [
            _squad_member("gk1", 0, position=1),
            _squad_member("9", 2_000_000, position=2),
            _squad_member("d1", 0, position=2),
            _squad_member("d2", 0, position=2),
            _squad_member("d3", 0, position=2),
            _squad_member("99", 5_000_000, position=3),
            _squad_member("m1", 0, position=3),
            _squad_member("m2", 0, position=3),
            _squad_member("m3", 0, position=3),
            _squad_member("x1", 0, position=2),
            _squad_member("s1", 0, position=4),
            _squad_member("s2", 0, position=4),
        ]
        # Spielplan fürs T-120-/T-48h-Fenster des Bid-Preflights: Default EIN
        # fernes Fixture (Deadline bekannt, Fenster inaktiv) — eine LEERE Liste
        # ist seit dem Codex-Re-Check fail-closed; Freeze-Tests setzen eigene
        # Fixtures relativ zur ECHTEN Uhr.
        self.fixtures: list = [_far_fixture()]
        # Roher Markt-Payload für den Accept-Preflight (client._get) — Default
        # ohne eigene Listings; Accept-Tests setzen ihn passend.
        self.raw_market: dict = {"it": []}

    async def get_matchdays(self):
        return self.fixtures

    async def get_league_rules(self, league_id):
        return self.league_rules

    async def get_own_bids(self, league_id, user_id):
        return self.own_bids

    async def _get(self, path):
        return self.raw_market

    async def get_budget(self, league_id):
        return self.budget

    async def get_budget_fields(self, league_id):
        # pbaa = Konto nach offenen Geboten (Fake: keine offenen Gebote) —
        # seit 23.08. verlangt der Bid-Preflight dieses Feld (fail-closed).
        b = await self.get_budget(league_id)
        return {"b": b, "pbaa": b}

    async def get_market(self, league_id):
        return self.market

    async def get_squad(self, league_id):
        return self.squad

    async def place_bid(self, league_id, player_id, price):
        self.calls.append(("place_bid", league_id, player_id, price))
        if self.fail:
            raise RuntimeError("API kaputt")
        return {"ok": True}

    async def sell_to_kickbase(self, league_id, player_id):
        self.calls.append(("sell", league_id, player_id))
        return {"sold": True}

    async def list_player(self, league_id, player_id, price):
        self.calls.append(("list", league_id, player_id, price))
        return {"listed": True}

    async def cancel_bid(self, league_id, player_id, offer_id):
        self.calls.append(("cancel_bid", league_id, player_id, offer_id))
        return {"cancelled": True}


def _bid(league="TEST", amount=5_000_000):
    return PlannedAction(
        action_type="bid", league_id=league, player_id="99", amount=amount, rule_id="r1"
    )


@pytest.fixture
def env(monkeypatch):
    def _set(dry="1", test_league="TEST", allow_live="0"):
        monkeypatch.setenv("AGENT_DRY_RUN", dry)
        monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", test_league)
        monkeypatch.setenv("AGENT_ALLOW_LIVE", allow_live)
        # Auto-Flags aus der lokalen .env dürfen nicht in Tests leaken —
        # Tests, die sie brauchen, setzen sie NACH env(...) explizit.
        monkeypatch.setenv("AGENT_AUTO_PURGE", "0")
        monkeypatch.setenv("AGENT_AUTO_BONUS", "0")
        monkeypatch.setenv("AGENT_AUTO_LINEUP", "0")
        monkeypatch.setenv("AGENT_APPROVE_LIVE", "0")
        monkeypatch.setenv("AGENT_AUTO_TRADING", "0")
        monkeypatch.setenv("KICKBASE_EMAIL", "x@y.z")
        monkeypatch.setenv("KICKBASE_PASSWORD", "pw")
        import kickbase_agent.config as cfg

        cfg._settings = None

    yield _set
    import kickbase_agent.config as cfg

    cfg._settings = None


@pytest.mark.asyncio
async def test_dry_run_logs_but_makes_no_api_call(session, env):
    env(dry="1")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(), now=NOW)

    assert result.status == "skipped_dry_run"
    assert client.calls == []
    row = session.query(TransferAction).one()
    assert row.status == "skipped_dry_run"
    assert row.dry_run is True
    assert row.rule_id == "r1"


@pytest.mark.asyncio
async def test_live_write_in_test_league_calls_api(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(league="TEST"), now=NOW)

    assert result.status == "executed"
    assert client.calls == [("place_bid", "TEST", "99", 5_000_000)]
    row = session.query(TransferAction).one()
    assert row.status == "executed"
    assert row.raw_response == {"ok": True}


@pytest.mark.asyncio
async def test_live_write_outside_test_league_is_refused(session, env):
    env(dry="0", test_league="TEST", allow_live="0")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(league="REAL"), now=NOW)

    assert result.status == "refused"
    assert client.calls == []  # echter Account bleibt unberührt
    assert session.query(TransferAction).one().status == "refused"


@pytest.mark.asyncio
async def test_allow_live_permits_write_outside_test_league(session, env):
    env(dry="0", test_league="TEST", allow_live="1")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(league="REAL"), now=NOW)

    assert result.status == "executed"
    assert client.calls[0][1] == "REAL"


@pytest.mark.asyncio
async def test_idempotent_no_double_bid(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    await ex.execute(_bid(), now=NOW)
    result2 = await ex.execute(_bid(), now=NOW)

    assert result2.status == "duplicate"
    assert len(client.calls) == 1  # nur einmal geboten
    assert session.query(TransferAction).count() == 1


@pytest.mark.asyncio
async def test_failed_api_call_marked_failed(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient(fail=True)
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(), now=NOW)

    assert result.status == "failed"
    row = session.query(TransferAction).one()
    assert row.status == "failed"
    assert "API kaputt" in (row.rationale or "")


# --- Approval-Zustandsmaschine (stage → approve/reject) ---

@pytest.mark.asyncio
async def test_stage_creates_proposed_row_without_api_call(session, env):
    env(dry="1")  # Dry-Run-Default egal — stage ruft nie eine API
    ex = ActionExecutor(FakeClient(), session)

    result = await ex.stage(_bid())

    assert result.status == "proposed"
    row = session.query(TransferAction).one()
    assert row.status == "proposed"
    assert row.payload["player_id"] == "99"
    assert row.rule_id == "r1"


@pytest.mark.asyncio
async def test_execute_staged_dry_run_simulates_no_api_call(session, env):
    # AGENT_DRY_RUN=1: Approval-Klick simuliert nur (sicherer erster Betrieb)
    env(dry="1")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    staged = await ex.stage(_bid())
    result = await ex.execute_staged(staged.action_id)

    # Review-Fix: Dry-Run darf sich NICHT als "executed" ausgeben.
    assert result.status == "skipped_dry_run"
    assert client.calls == []  # kein echtes Gebot
    assert session.get(TransferAction, staged.action_id).raw_response == {"dry_run": True}


@pytest.mark.asyncio
async def test_execute_staged_runs_live_when_dry_off(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    staged = await ex.stage(_bid())
    result = await ex.execute_staged(staged.action_id)

    assert result.status == "executed"
    assert client.calls == [("place_bid", "TEST", "99", 5_000_000)]
    assert session.get(TransferAction, staged.action_id).status == "executed"


@pytest.mark.asyncio
async def test_reject_staged_marks_rejected_no_call(session, env):
    env()
    client = FakeClient()
    ex = ActionExecutor(client, session)

    staged = await ex.stage(_bid())
    result = await ex.reject_staged(staged.action_id)

    assert result.status == "rejected"
    assert client.calls == []
    assert session.get(TransferAction, staged.action_id).status == "rejected"


@pytest.mark.asyncio
async def test_execute_staged_no_double_run(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    staged = await ex.stage(_bid())
    await ex.execute_staged(staged.action_id)
    again = await ex.execute_staged(staged.action_id)

    assert again.status == "duplicate"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_auto_lineup_flag_arms_only_lineup(session, env, monkeypatch):
    """AGENT_AUTO_LINEUP=1 erlaubt lineup-Writes live — Trades bleiben gesperrt."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_LINEUP", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None

    class LineupClient(FakeClient):
        async def set_lineup(self, league_id, formation, player_ids):
            self.calls.append(("set_lineup", league_id, formation, tuple(player_ids)))
            return {"ok": True}

    client = LineupClient()
    ex = ActionExecutor(client, session)

    lineup = PlannedAction(action_type="lineup", league_id="1234567",
                           formation="4-4-2", player_ids=["1", "2"], rule_id="auto-lineup")
    r1 = await ex.execute(lineup, dry_run=False)
    assert r1.status == "executed"                       # Aufstellung: erlaubt

    r2 = await ex.execute(_bid(league="1234567"), dry_run=False)
    assert r2.status == "refused"                        # Trade: weiter gesperrt
    assert [c[0] for c in client.calls] == ["set_lineup"]


@pytest.mark.asyncio
async def test_changed_lineup_same_day_is_not_duplicate(session, env, monkeypatch):
    """Geänderte Elf am selben Tag muss durchgehen; identische Elf = Duplikat."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_LINEUP", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None

    class LineupClient(FakeClient):
        async def set_lineup(self, league_id, formation, player_ids):
            self.calls.append(("set_lineup", tuple(player_ids)))
            return {}

    client = LineupClient()
    ex = ActionExecutor(client, session)

    def _lineup(ids):
        return PlannedAction(action_type="lineup", league_id="L",
                             formation="4-4-2", player_ids=ids, rule_id="auto-lineup")

    r1 = await ex.execute(_lineup(["1", "2"]), dry_run=False, now=NOW)
    r2 = await ex.execute(_lineup(["1", "3"]), dry_run=False, now=NOW)   # geändert
    r3 = await ex.execute(_lineup(["1", "2"]), dry_run=False, now=NOW)   # identisch

    assert (r1.status, r2.status, r3.status) == ("executed", "executed", "duplicate")
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_auto_bonus_flag_arms_only_bonus(session, env, monkeypatch):
    """AGENT_AUTO_BONUS=1 erlaubt Bonus-Writes live — Trades bleiben gesperrt."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_BONUS", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None

    class BonusClient(FakeClient):
        async def collect_bonus(self):
            self.calls.append(("bonus",))
            self.budget += 100_000  # Kickbase schreibt die Prämie sofort gut
            return {"it": []}

    client = BonusClient()
    ex = ActionExecutor(client, session)

    r1 = await ex.execute(
        PlannedAction(action_type="bonus", league_id="1234567", rule_id="auto-bonus"),
        dry_run=False,
    )
    r2 = await ex.execute(_bid(league="1234567"), dry_run=False)

    assert r1.status == "executed"
    assert r2.status == "refused"
    assert [c[0] for c in client.calls] == ["bonus"]


# --- Prämie ergebnisbewusst: Budget-Delta entscheidet executed vs. noop ---


def _bonus_action():
    return PlannedAction(action_type="bonus", league_id="1234567", rule_id="auto-bonus")


def _arm_bonus(env, monkeypatch):
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_BONUS", "1")
    import kickbase_agent.config as cfg

    cfg._settings = None


@pytest.mark.asyncio
async def test_bonus_credit_is_measured_and_recorded(session, env, monkeypatch):
    """Gutschrift wird als Konto-Delta gemessen und im Audit-Log festgehalten."""
    _arm_bonus(env, monkeypatch)

    class BonusClient(FakeClient):
        async def collect_bonus(self):
            self.calls.append(("bonus",))
            self.budget += 100_000
            return {"it": []}

    client = BonusClient()
    client.budget = 47_540_777
    r = await ActionExecutor(client, session).execute(_bonus_action(), dry_run=False)

    assert r.status == "executed"
    assert r.raw_response["kba_credited"] == 100_000
    assert r.raw_response["kba_budget_before"] == 47_540_777
    assert r.raw_response["kba_budget_after"] == 47_640_777
    assert "+100.000" in r.message
    row = session.get(TransferAction, r.action_id)
    assert row.raw_response["kba_credited"] == 100_000


@pytest.mark.asyncio
async def test_bonus_without_credit_is_noop_and_retryable(session, env, monkeypatch):
    """Leerer Griff (Konto unverändert) endet als 'noop' — blockt den Tag NICHT.

    Live-Fund 23.08.: der Start-Nachholer um 02:53 rief /bonus/collect, nachdem
    die App die Prämie um 02:28 schon kassiert hatte (Feed t=22); Kickbase
    antwortete trotzdem 200 + `it: []`, gutgeschrieben wurde nichts — die
    'executed'-Zeile blockte danach den ganzen Tag (08:03 → duplicate).
    """
    _arm_bonus(env, monkeypatch)

    class LateBonusClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.available = False

        async def collect_bonus(self):
            self.calls.append(("bonus",))
            if self.available:
                self.budget += 100_000
            return {"it": []}

    client = LateBonusClient()
    ex = ActionExecutor(client, session)

    r1 = await ex.execute(_bonus_action(), dry_run=False)
    assert r1.status == "noop"
    assert r1.raw_response["kba_credited"] == 0
    assert "nichts gutgeschrieben" in r1.message
    # Gleicher Tag, Prämie inzwischen freigeschaltet: die noop-Zeile wird recycelt,
    # KEIN 'duplicate' — und jetzt zählt die Gutschrift.
    client.available = True
    r2 = await ex.execute(_bonus_action(), dry_run=False)
    assert r2.status == "executed"
    assert r2.action_id == r1.action_id
    assert r2.raw_response["kba_credited"] == 100_000
    # Danach ist der Tag erledigt: dritter Versuch = duplicate, kein API-Call.
    r3 = await ex.execute(_bonus_action(), dry_run=False)
    assert r3.status == "duplicate"
    assert [c[0] for c in client.calls] == ["bonus", "bonus"]


@pytest.mark.asyncio
async def test_bonus_noop_is_not_an_incident_status(session, env, monkeypatch):
    """noop zählt nicht als unknown/failed — der Auto-Trader darf sich dadurch
    nicht selbst stoppen (Incident-Stopp schaut auf unknown/failed)."""
    _arm_bonus(env, monkeypatch)

    class NoCreditClient(FakeClient):
        async def collect_bonus(self):
            return {"it": []}

    r = await ActionExecutor(NoCreditClient(), session).execute(_bonus_action(), dry_run=False)
    assert r.status == "noop"
    row = session.get(TransferAction, r.action_id)
    assert row.status == "noop"
    assert row.status not in ("unknown", "failed")


@pytest.mark.asyncio
async def test_bonus_without_budget_reader_stays_executed(session, env, monkeypatch):
    """Kann das Konto nicht gelesen werden (kein get_budget / Lesefehler), bleibt
    der alte Vertrag: 200 ⇒ executed, ohne Delta-Felder."""
    _arm_bonus(env, monkeypatch)

    class BlindClient:
        def __init__(self):
            self.calls = []

        async def collect_bonus(self):
            self.calls.append(("bonus",))
            return {"it": []}

    r = await ActionExecutor(BlindClient(), session, preflight=False).execute(
        _bonus_action(), dry_run=False
    )
    assert r.status == "executed"
    assert "kba_credited" not in r.raw_response

    class FlakyBudgetClient(FakeClient):
        async def get_budget(self, league_id):
            raise RuntimeError("Konto gerade nicht lesbar")

        async def collect_bonus(self):
            self.calls.append(("bonus",))
            return {"it": []}

    session.query(TransferAction).delete()
    session.commit()
    r2 = await ActionExecutor(FlakyBudgetClient(), session).execute(_bonus_action(), dry_run=False)
    assert r2.status == "executed"
    assert "kba_credited" not in r2.raw_response


# --- P0-Fix Interlock: AGENT_ALLOW_LIVE ärmt NUR Trades, nie lineup/bonus ---

def _lineup_action(league="REAL"):
    return PlannedAction(action_type="lineup", league_id=league, formation="4-4-2",
                         player_ids=["1", "2"], rule_id="auto-lineup")


@pytest.mark.asyncio
async def test_allow_live_does_not_arm_lineup_or_bonus(session, env):
    """P0-Kernfund: allow_live stand VOR den Spezialschaltern und schaltete
    ALLES frei — jetzt bleiben lineup/bonus ohne ihre eigenen Flags gesperrt,
    und die Refusal-Begründung nennt ehrlich den fehlenden Schalter."""
    env(dry="0", test_league="", allow_live="1")  # auto_lineup/bonus=0 via env()
    client = FakeClient()
    ex = ActionExecutor(client, session)

    r1 = await ex.execute(_lineup_action(), dry_run=False, now=NOW)
    r2 = await ex.execute(
        PlannedAction(action_type="bonus", league_id="REAL", rule_id="auto-bonus"),
        dry_run=False, now=NOW,
    )

    assert r1.status == "refused"
    assert "AGENT_AUTO_LINEUP" in r1.message  # ehrlich: nicht "ALLOW_LIVE=0"
    assert r2.status == "refused"
    assert "AGENT_AUTO_BONUS" in r2.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_allow_live_lineup_stays_dry_run_with_kill_switch(session, env):
    """allow_live=1 + auto_lineup=0 + DRY_RUN=1: der Lauf endet ehrlich als
    skipped_dry_run — nie als stiller Live-Write."""
    env(dry="1", test_league="", allow_live="1")
    client = FakeClient()

    result = await ActionExecutor(client, session).execute(_lineup_action(), now=NOW)

    assert result.status == "skipped_dry_run"
    assert client.calls == []


@pytest.mark.asyncio
async def test_allow_live_arms_manual_trade_types(session, env):
    """Emils manuelle Trades (`env AGENT_ALLOW_LIVE=1` pro Kommando) MÜSSEN
    weiter funktionieren: bid, sell, list und cancel_bid laufen live."""
    env(dry="0", test_league="", allow_live="1")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    bid = await ex.execute(_bid(league="REAL"), dry_run=False, now=NOW)
    sell = await ex.execute(
        PlannedAction(action_type="sell", league_id="REAL", player_id="9",
                      rule_id="manual"),
        dry_run=False, now=NOW,
    )
    listing = await ex.execute(
        PlannedAction(action_type="list", league_id="REAL", player_id="99",
                      amount=5_000_000, rule_id="manual"),
        dry_run=False, now=NOW,
    )
    storno = await ex.execute(
        PlannedAction(action_type="cancel_bid", league_id="REAL", player_id="9",
                      offer_id="o1", rule_id="manual"),
        dry_run=False, now=NOW,
    )

    assert (bid.status, sell.status, listing.status, storno.status) == (
        "executed", "executed", "executed", "executed"
    )
    assert [c[0] for c in client.calls] == ["place_bid", "sell", "list", "cancel_bid"]


# --- Fix 1: Idempotenz ohne IntegrityError (Re-Stage, Retry, Insert-Race) ---

@pytest.mark.asyncio
async def test_execute_while_proposed_is_duplicate_not_crash(session, env):
    """P0-Kernfund: 'proposed' fehlte in der Blockliste — ein erneutes Anlegen
    derselben Aktion crashte mit IntegrityError am unique idempotency_key."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    staged = await ex.stage(_bid(), now=NOW)
    result = await ex.execute(_bid(), now=NOW)

    assert result.status == "duplicate"
    assert result.action_id == staged.action_id
    assert client.calls == []
    assert session.query(TransferAction).count() == 1


@pytest.mark.asyncio
async def test_restage_while_proposed_is_duplicate_not_crash(session, env):
    env()
    ex = ActionExecutor(FakeClient(), session)

    staged = await ex.stage(_bid(), now=NOW)
    again = await ex.stage(_bid(), now=NOW)

    assert again.status == "duplicate"
    assert again.action_id == staged.action_id
    assert session.query(TransferAction).count() == 1


@pytest.mark.asyncio
async def test_retry_after_failed_recycles_row(session, env):
    """Endzustand 'failed' erlaubt einen ECHTEN Retry: dieselbe Zeile wird
    zurückgesetzt und wiederverwendet — kein Insert-Konflikt, kein Doppel-Audit."""
    env(dry="0", test_league="TEST")
    broken = FakeClient(fail=True)
    r1 = await ActionExecutor(broken, session).execute(_bid(), now=NOW)
    assert r1.status == "failed"

    ok = FakeClient()
    r2 = await ActionExecutor(ok, session).execute(_bid(), now=NOW)

    assert r2.status == "executed"
    assert r2.action_id == r1.action_id  # gleiche Zeile recycelt
    row = session.query(TransferAction).one()  # kein zweiter Insert
    assert row.status == "executed"
    assert row.raw_response == {"ok": True}
    assert len(ok.calls) == 1


@pytest.mark.asyncio
async def test_restage_after_reject_recycles_row(session, env):
    env()
    ex = ActionExecutor(FakeClient(), session)
    staged = await ex.stage(_bid(), now=NOW)
    await ex.reject_staged(staged.action_id, now=NOW)

    again = await ex.stage(_bid(), now=NOW)

    assert again.status == "proposed"
    assert again.action_id == staged.action_id
    row = session.query(TransferAction).one()
    assert row.status == "proposed"
    assert row.resolved_at is None
    assert row.raw_response is None


@pytest.mark.asyncio
async def test_race_insert_conflict_becomes_duplicate(session, env):
    """Insert-Race: ein paralleler Prozess legt denselben Schlüssel zwischen
    Vorab-Check und Insert an — der IntegrityError wird zu 'duplicate'."""
    env(dry="1")
    ex = ActionExecutor(FakeClient(), session)
    first = await ex.execute(_bid(), now=NOW)  # legt die Zeile an

    # Race simulieren: der erste Vorab-Check sieht nichts, der Insert kollidiert;
    # der Konflikt-Lookup danach findet die fremde Zeile.
    real_find = ex._find_existing
    calls = {"n": 0}

    def flaky(key):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(key)

    ex._find_existing = flaky
    result = await ex.execute(_bid(), now=NOW)

    assert result.status == "duplicate"
    assert result.action_id == first.action_id
    assert session.query(TransferAction).count() == 1


# --- Fix 2: AGENT_AUTO_PURGE armt die Anpfiff-Notbremse ---

@pytest.mark.asyncio
async def test_auto_purge_arms_kickoff_guard_actions_only(session, env, monkeypatch):
    """AGENT_AUTO_PURGE=1 erlaubt Live-Writes GENAU für die Guard-Aktionen des
    Kickoff-Wächters (sell/cancel_bid mit kickoff-rule_id) — sonst nichts."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_PURGE", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None

    client = FakeClient()
    ex = ActionExecutor(client, session)

    purge = PlannedAction(action_type="sell", league_id="REAL", player_id="9",
                          rule_id="kickoff-purge")
    storno = PlannedAction(action_type="cancel_bid", league_id="REAL", player_id="8",
                           offer_id="o1", rule_id="kickoff-bid-cancel")
    r1 = await ex.execute(purge, dry_run=False, now=NOW)
    r2 = await ex.execute(storno, dry_run=False, now=NOW)
    assert (r1.status, r2.status) == ("executed", "executed")

    normal_sell = PlannedAction(action_type="sell", league_id="REAL", player_id="7",
                                rule_id="sell_weak")
    fake_bid = PlannedAction(action_type="bid", league_id="REAL", player_id="6",
                             amount=1_000_000, rule_id="kickoff-purge")  # falscher Typ
    r3 = await ex.execute(normal_sell, dry_run=False, now=NOW)
    r4 = await ex.execute(fake_bid, dry_run=False, now=NOW)
    assert (r3.status, r4.status) == ("refused", "refused")
    assert [c[0] for c in client.calls] == ["sell", "cancel_bid"]


@pytest.mark.asyncio
async def test_purge_without_flag_stays_refused(session, env):
    env(dry="0", test_league="", allow_live="0")  # AGENT_AUTO_PURGE=0 via env()
    client = FakeClient()
    purge = PlannedAction(action_type="sell", league_id="REAL", player_id="9",
                          rule_id="kickoff-purge")

    result = await ActionExecutor(client, session).execute(purge, dry_run=False, now=NOW)

    assert result.status == "refused"
    assert client.calls == []


# --- Stufe 2: AGENT_AUTO_TRADING ärmt NUR den Auto-Trading-Pfad (dormant) ---

def _auto_bid(rule="auto-trading-buy"):
    # Betrag = Marktpreis des FakeClient-Listings (5M), damit der Default-
    # Preflight passiert und WIRKLICH der Interlock getestet wird.
    return PlannedAction(action_type="bid", league_id="REAL", player_id="99",
                         amount=5_000_000, rule_id=rule)


@pytest.mark.asyncio
async def test_auto_trading_flag_arms_prefixed_trades_only(session, env, monkeypatch):
    """AGENT_AUTO_TRADING=1 erlaubt bid/list/sell live GENAU mit rule_id-Präfix
    'auto-trading' (Muster kickoff-Präfix) — ohne allow_live, ohne Test-Liga."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_TRADING", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None

    from unittest.mock import AsyncMock

    client = FakeClient()
    # Der Interlock-Test braucht auch die vorgeschriebene frische Kaufprüfung.
    # Die Rohdaten-Parser werden weiterhin mit den echten API-Fixtures geprüft.
    client.market[0].player.fitness_status = 0
    client.market[0].player.lineup_prob = 1
    client.market[0].player.mv_change_24h = 26_065
    client.get_player = AsyncMock(return_value=client.market[0].player)
    ex = ActionExecutor(client, session)
    bid = _auto_bid()
    bid.purchase_review = {
        "schema_version": 1, "player_id": "99", "selected": True,
        "max_bid_eur": 5_000_000, "market_value_eur": 20_000_000,
        "observed_at": (NOW - timedelta(minutes=5)).isoformat(),
        "valid_until": (NOW + timedelta(minutes=20)).isoformat(),
        "purpose": "starter", "reasons": ["Isolierter Interlock-Test"],
    }

    r1 = await ex.execute(bid, dry_run=False, now=NOW)
    client.get_player.assert_awaited_once_with("REAL", "99")
    r2 = await ex.execute(
        PlannedAction(action_type="list", league_id="REAL", player_id="99",
                      amount=5_000_000, rule_id="auto-trading-list"),
        dry_run=False, now=NOW,
    )

    assert (r1.status, r2.status) == ("executed", "executed")
    assert [c[0] for c in client.calls] == ["place_bid", "list"]


@pytest.mark.asyncio
async def test_auto_trading_flag_ignores_foreign_rule_ids(session, env, monkeypatch):
    """Flag an, aber fremde rule_id ('buy_explosion') → weiter refused; das
    Präfix auf einem Nicht-Trade-Typ (lineup) ärmt ebenfalls nichts."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_TRADING", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None

    client = FakeClient()
    ex = ActionExecutor(client, session)

    r1 = await ex.execute(_auto_bid(rule="buy_explosion"), dry_run=False, now=NOW)
    r2 = await ex.execute(
        PlannedAction(action_type="lineup", league_id="REAL", formation="4-4-2",
                      player_ids=["1", "2"], rule_id="auto-trading-lineup"),
        dry_run=False, now=NOW,
    )

    assert (r1.status, r2.status) == ("refused", "refused")
    assert client.calls == []


@pytest.mark.asyncio
async def test_auto_trading_actions_refused_without_flag(session, env):
    """Flag aus (Default, dormant): auch korrekt geprägte auto-trading-Aktionen
    bleiben refused — der Pfad ist ausgeliefert, aber NICHT geärmt."""
    env(dry="0", test_league="", allow_live="0")  # AGENT_AUTO_TRADING=0 via env()
    client = FakeClient()

    result = await ActionExecutor(client, session).execute(
        _auto_bid(), dry_run=False, now=NOW
    )

    assert result.status == "refused"
    assert client.calls == []


# --- Fix 3: AGENT_DRY_RUN=1 ist der globale Not-Aus ---

@pytest.mark.asyncio
async def test_kill_switch_dry_run_beats_all_flags_and_params(session, env):
    """dry_run=False + AGENT_ALLOW_LIVE=1 dürfen den Not-Aus NICHT aufheben."""
    env(dry="1", test_league="TEST", allow_live="1")
    client = FakeClient()

    result = await ActionExecutor(client, session).execute(
        _bid(league="REAL"), dry_run=False, now=NOW
    )

    assert result.status == "skipped_dry_run"
    assert client.calls == []
    assert session.query(TransferAction).one().dry_run is True


@pytest.mark.asyncio
async def test_param_dry_run_true_forces_dry_despite_live_settings(session, env):
    """Der Parameter kann Dry-Run zusätzlich erzwingen — nur aufheben kann er nie."""
    env(dry="0", test_league="TEST", allow_live="1")
    client = FakeClient()

    result = await ActionExecutor(client, session).execute(_bid(), dry_run=True, now=NOW)

    assert result.status == "skipped_dry_run"
    assert client.calls == []


# --- Fix 4: Audit-Reihenfolge + Timeout = unbekannter Zustand ---

@pytest.mark.asyncio
async def test_pending_audit_committed_before_dispatch(tmp_path, env):
    """Die 'pending'-Zeile muss VOR dem API-Call committed sein — ein Absturz
    mitten im Request hinterlässt sonst einen Remote-Write ohne lokale Spur."""
    env(dry="0", test_league="TEST")
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}", future=True)
    Base.metadata.create_all(engine)
    seen: list[str] = []

    class AuditPeekClient(FakeClient):
        async def place_bid(self, league_id, player_id, price):
            # Zweite Verbindung = Blick eines anderen Prozesses in die DB.
            with Session(engine, future=True) as other:
                seen.append(other.query(TransferAction).one().status)
            return await super().place_bid(league_id, player_id, price)

    with Session(engine, future=True) as s:
        result = await ActionExecutor(AuditPeekClient(), s).execute(_bid(), now=NOW)

    assert result.status == "executed"
    assert seen == ["pending"]


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("Lesetimeout"),
        TimeoutError("asyncio-Timeout"),
        # Review-Fund 23.08.: Abbruch NACH dem Senden ist genauso mehrdeutig
        httpx.RemoteProtocolError("Server disconnected without response"),
        httpx.ReadError("connection reset"),
        httpx.WriteError("broken pipe"),
    ],
)
@pytest.mark.asyncio
async def test_timeout_marks_unknown_and_blocks_blind_retry(session, env, exc):
    """Timeout heißt NICHT 'failed': der Write kann durch sein → 'unknown',
    Warnung im Ergebnis, und die Aktion bleibt für blinde Retries gesperrt."""
    env(dry="0", test_league="TEST")

    class TimeoutClient(FakeClient):
        async def place_bid(self, league_id, player_id, price):
            self.calls.append(("place_bid",))
            raise exc

    client = TimeoutClient()
    ex = ActionExecutor(client, session)
    result = await ex.execute(_bid(), now=NOW)

    assert result.status == "unknown"
    assert "Reconcile" in result.message  # Warnung: Remote-Zustand manuell prüfen
    assert session.query(TransferAction).one().status == "unknown"

    retry = await ex.execute(_bid(), now=NOW)  # KEIN blinder Retry
    assert retry.status == "duplicate"
    assert len(client.calls) == 1


# --- Fix 5: Doppel-Dispatch — Approve claimt atomar ---

@pytest.mark.asyncio
async def test_two_sequential_approves_dispatch_once(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid())

    first = await ex.execute_staged(staged.action_id)
    second = await ex.execute_staged(staged.action_id)

    assert first.status == "executed"
    assert second.status == "duplicate"
    assert len(client.calls) == 1  # genau EIN Dispatch


@pytest.mark.asyncio
async def test_approve_claim_race_loser_does_not_dispatch(session, env):
    """Hat ein paralleler Worker die Zeile schon auf 'pending' geclaimt (Dispatch
    läuft), liefert der zweite Klick 'duplicate' — kein zweiter API-Call."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid())

    other = session.get(TransferAction, staged.action_id)
    other.status = "pending"  # anderer Worker war schneller, noch unaufgelöst
    session.commit()

    result = await ex.execute_staged(staged.action_id)

    assert result.status == "duplicate"
    assert "pending" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_reject_cannot_overwrite_executed_action(session, env):
    """Ein später Reject-Klick darf eine ausgeführte Aktion nicht rückwirkend
    zu 'rejected' umetikettieren (Audit-Integrität)."""
    env(dry="0", test_league="TEST")
    ex = ActionExecutor(FakeClient(), session)
    staged = await ex.stage(_bid())
    await ex.execute_staged(staged.action_id)

    result = await ex.reject_staged(staged.action_id)

    assert result.status == "duplicate"
    assert session.get(TransferAction, staged.action_id).status == "executed"


# --- Fix 6: Freigabe-TTL + Preflight ---

@pytest.mark.asyncio
async def test_stage_sets_valid_until_45min(session, env):
    env()
    await ActionExecutor(FakeClient(), session).stage(_bid(), now=NOW)

    row = session.query(TransferAction).one()
    # SQLite speichert naiv (UTC) — für den Vergleich normalisieren.
    assert row.valid_until.replace(tzinfo=UTC) == NOW + timedelta(minutes=45)


@pytest.mark.asyncio
async def test_approve_after_ttl_is_expired_no_call(session, env):
    """Ein Stunden später geklickter Button führt KEIN veraltetes Gebot aus."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid(), now=NOW)

    result = await ex.execute_staged(staged.action_id, now=NOW + timedelta(minutes=46))

    assert result.status == "expired"
    assert "abgelaufen" in result.message
    assert client.calls == []
    assert session.get(TransferAction, staged.action_id).status == "expired"


@pytest.mark.asyncio
async def test_approve_within_ttl_executes(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid(), now=NOW)

    result = await ex.execute_staged(staged.action_id, now=NOW + timedelta(minutes=44))

    assert result.status == "executed"
    assert len(client.calls) == 1


def _preflight_stub(state: PreflightState):
    """Fake-Callback: liefert den vorgegebenen Markt-Zustand, zählt Aufrufe."""
    calls: list[PlannedAction] = []

    async def cb(action: PlannedAction) -> PreflightState:
        calls.append(action)
        return state

    return cb, calls


@pytest.mark.asyncio
async def test_preflight_blocks_bid_when_price_jumped_over_10pct(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, calls = _preflight_stub(
        PreflightState(
            budget=99_000_000, on_market=True, current_mv=5_000_000,
            current_price=5_600_000,
        )
    )
    ex = ActionExecutor(client, session, preflight=cb)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)  # Limit: 5.5M

    assert result.status == "refused"
    assert "10 %" in result.message
    assert client.calls == []
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_preflight_blocks_bid_when_player_left_market(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, _ = _preflight_stub(PreflightState(budget=99_000_000, on_market=False))
    ex = ActionExecutor(client, session, preflight=cb)

    result = await ex.execute(_bid(), now=NOW)

    assert result.status == "refused"
    assert "Transfermarkt" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_blocks_bid_over_fresh_budget(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, _ = _preflight_stub(PreflightState(budget=4_000_000, on_market=True, current_mv=5_000_000))
    ex = ActionExecutor(client, session, preflight=cb)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)

    assert result.status == "refused"
    assert "Budget" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_ok_dispatches(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, calls = _preflight_stub(
        PreflightState(
            budget=99_000_000, on_market=True, current_mv=5_000_000,
            current_price=5_400_000,
        )
    )
    ex = ActionExecutor(client, session, preflight=cb)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)  # 5.4M <= 5.5M ok

    assert result.status == "executed"
    assert len(calls) == 1
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_preflight_error_refuses_live_write(session, env):
    """Fail-closed: liefert der Preflight keine frischen Daten, wird nicht gekauft."""
    env(dry="0", test_league="TEST")
    client = FakeClient()

    async def broken(action):
        raise RuntimeError("Markt-API down")

    ex = ActionExecutor(client, session, preflight=broken)
    result = await ex.execute(_bid(), now=NOW)

    assert result.status == "refused"
    assert "Preflight fehlgeschlagen" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_guards_approved_staged_bid(session, env):
    """Auch der Approval-Pfad prüft unmittelbar vor dem Dispatch frisch."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, _ = _preflight_stub(PreflightState(on_market=False))
    ex = ActionExecutor(client, session, preflight=cb)
    staged = await ex.stage(_bid(), now=NOW)

    result = await ex.execute_staged(staged.action_id, now=NOW + timedelta(minutes=5))

    assert result.status == "refused"
    assert client.calls == []
    assert session.get(TransferAction, staged.action_id).status == "refused"


@pytest.mark.asyncio
async def test_preflight_not_called_for_non_money_actions_or_dry_run(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, calls = _preflight_stub(PreflightState(on_market=False))
    ex = ActionExecutor(client, session, preflight=cb)

    storno = PlannedAction(action_type="cancel_bid", league_id="TEST", player_id="9",
                           offer_id="o1", rule_id="r1")
    r1 = await ex.execute(storno, now=NOW)  # kein Geld-Write → kein Preflight
    assert r1.status == "executed"

    r2 = await ex.execute(_bid(), dry_run=True, now=NOW)  # Dry-Run → kein Preflight
    assert r2.status == "skipped_dry_run"
    assert calls == []


# --- 3. Review, Fund 1: Approval-Pfad umgeht AGENT_ALLOW_LIVE nicht mehr ---

@pytest.mark.asyncio
async def test_approved_bid_without_arming_flag_is_refused(session, env):
    """P0-Bypass geschlossen: DRY_RUN=0 + ALLOW_LIVE=0 + APPROVE_LIVE=0 → ein
    freigegebenes Gebot außerhalb des Interlocks läuft NICHT mehr live."""
    env(dry="0", test_league="", allow_live="0")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid(league="REAL"), now=NOW)

    result = await ex.execute_staged(staged.action_id, now=NOW + timedelta(minutes=1))

    assert result.status == "refused"
    assert "AGENT_APPROVE_LIVE=0" in result.message
    assert client.calls == []
    assert session.get(TransferAction, staged.action_id).status == "refused"


@pytest.mark.asyncio
async def test_approve_live_flag_arms_approval_path(session, env, monkeypatch):
    """AGENT_APPROVE_LIVE=1 ärmt GENAU den Freigabe-Pfad: der Klick führt aus."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_APPROVE_LIVE", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None

    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid(league="REAL"), now=NOW)

    result = await ex.execute_staged(staged.action_id, now=NOW + timedelta(minutes=1))

    assert result.status == "executed"
    assert client.calls == [("place_bid", "REAL", "99", 5_000_000)]


@pytest.mark.asyncio
async def test_allow_live_also_arms_approval_path(session, env):
    """Der reguläre Interlock (AGENT_ALLOW_LIVE=1) genügt weiterhin für Approvals."""
    env(dry="0", test_league="", allow_live="1")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid(league="REAL"), now=NOW)

    result = await ex.execute_staged(staged.action_id, now=NOW + timedelta(minutes=1))

    assert result.status == "executed"
    assert len(client.calls) == 1


# --- 3. Review, Fund 2: default_preflight ist produktiv verdrahtet ---

@pytest.mark.asyncio
async def test_default_preflight_auto_wired_blocks_bid_beyond_33_percent(session, env):
    """Ohne explizites preflight= schützt default_preflight(client) automatisch:
    Gebot sprengt die offizielle 33-%-Regel (Konto 4 M, Teamwert 7 M → Limit
    −2,33 M; 7 M Gebot → −3 M) → refused, kein Write."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.budget = 4_000_000
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(amount=7_000_000), now=NOW)

    assert result.status == "refused"
    assert "33" in result.message and "Budget" in result.message
    assert client.calls == []


# --- Emils Regel (23.08.): bieten ZUERST, verkaufen nur bei Zuschlag ---
# Kickbase verlangt beim Bieten kein Bargeld, sondern die 33-%-Regel (Konto nach
# allen offenen Geboten ≥ −⅓ Teamwert) und erst zum Anpfiff ≥ 0. Der alte
# cash-only-Preflight zwang uns zu "erst verkaufen, dann bieten" — und bei
# verlorenen Geboten standen wir ohne Spieler UND ohne das verkaufte Asset da.


@pytest.mark.asyncio
async def test_default_preflight_allows_overdraft_within_33_percent(session, env):
    """Konto 4 M, Teamwert 7 M (Limit −2,33 M): ein 5-M-Gebot (→ −1 M) ist nach
    Kickbase-Regel erlaubt — der Write läuft, kein Vorab-Verkauf nötig."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.budget = 4_000_000
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)

    assert result.status == "executed"
    assert [c[0] for c in client.calls] == ["place_bid"]


@pytest.mark.asyncio
async def test_default_preflight_counts_open_bids_via_pbaa(session, env):
    """Offene Gebote zählen mit (Kickbase-Feld `pbaa` = Konto nach Annahme aller
    Gebote): Konto 4 M, aber 3 M schon in Geboten gebunden → pbaa 1 M; Teamwert 7 M
    (Limit −2,33 M): 5-M-Gebot → −4 M → refused."""
    env(dry="0", test_league="TEST")

    class BidsClient(FakeClient):
        async def get_budget_fields(self, league_id):
            return {"b": self.budget, "pbaa": self.budget - 3_000_000}

    client = BidsClient()
    client.budget = 4_000_000
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)

    assert result.status == "refused"
    assert "offenen Geboten" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_without_team_value_stays_cash_only(session, env):
    """Fehlt der Teamwert (kein Kader lesbar), bleibt der konservative Vertrag:
    Gebot über dem Konto → refused."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, _ = _preflight_stub(PreflightState(budget=4_000_000, on_market=True, current_mv=5_000_000))
    ex = ActionExecutor(client, session, preflight=cb)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)

    assert result.status == "refused"
    assert "Budget" in result.message


@pytest.mark.asyncio
async def test_default_preflight_auto_wired_blocks_gone_player(session, env):
    """Default-Preflight sieht den Markt frisch: Spieler weg → kein Kauf."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.market = []  # Spieler nicht mehr gelistet
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(), now=NOW)

    assert result.status == "refused"
    assert "Transfermarkt" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_false_opts_out_of_default(session, env):
    """Bewusstes Opt-out: preflight=False schaltet den Default-Preflight ab."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.budget = 4_000_000  # würde der Default-Preflight blocken …
    ex = ActionExecutor(client, session, preflight=False)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)  # … so aber nicht

    assert result.status == "executed"
    assert client.calls == [("place_bid", "TEST", "99", 5_000_000)]


@pytest.mark.asyncio
async def test_none_client_has_no_preflight_and_reject_works(session, env):
    """Reject-Pfad ohne Client (worker.on_reject) bleibt funktionsfähig."""
    env()
    staged = await ActionExecutor(FakeClient(), session).stage(_bid(), now=NOW)

    ex = ActionExecutor(None, session)
    assert ex._preflight is None  # ohne Client wird kein Default verdrahtet
    result = await ex.reject_staged(staged.action_id, now=NOW)

    assert result.status == "rejected"


# --- P0-Fix Preflight: sell/list laufen nicht mehr mit leerem State durch ---

def _sell(pid="99", amount=5_000_000, rule="sell_weak", league="TEST"):
    return PlannedAction(action_type="sell", league_id=league, player_id=pid,
                         amount=amount, rule_id=rule)


def _list(pid="99", amount=5_000_000, rule="sell_weak", league="TEST"):
    return PlannedAction(action_type="list", league_id=league, player_id=pid,
                         amount=amount, rule_id=rule)


@pytest.mark.asyncio
async def test_preflight_blocks_sell_of_player_not_in_squad(session, env):
    """Verkauf eines Spielers, der nicht (mehr) im Kader ist → refused."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.squad = []  # Spieler zwischenzeitlich verkauft/nie besessen

    result = await ActionExecutor(client, session).execute(_sell(), now=NOW)

    assert result.status == "refused"
    assert "Kader" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_blocks_list_when_value_drifted_30pct(session, env):
    """Listing mit 30 % MW-Abweichung vom Staging-Betrag → refused (Zustand
    hat sich geändert — neu entscheiden statt zum alten Preis handeln)."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.squad = [_squad_member("99", 6_500_000)]  # Staging war 5,0M → +30 %

    result = await ActionExecutor(client, session).execute(
        _list(amount=5_000_000), now=NOW
    )

    assert result.status == "refused"
    assert "20 %" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_blocks_sell_when_value_crashed(session, env):
    """Drift wirkt in BEIDE Richtungen: auch ein Preisrutsch nach unten
    (−32 %) stoppt den normalen Verkauf — nur die Notbremse darf das."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    # Kader bleibt deckungsfaehig (zentraler Deckungs-Preflight) — nur der MW
    # von 99 faellt: Staging war 5,0M.
    for sp in client.squad:
        if sp.player.id == "99":
            sp.player.market_value = 3_400_000

    result = await ActionExecutor(client, session).execute(
        _sell(amount=5_000_000), now=NOW
    )

    assert result.status == "refused"
    assert "20 %" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_allows_sell_within_drift(session, env):
    """Gegenprobe: ±20 % Toleranz — 8 % Abweichung geht durch."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    for sp in client.squad:
        if sp.player.id == "99":
            sp.player.market_value = 5_400_000

    result = await ActionExecutor(client, session).execute(
        _sell(amount=5_000_000), now=NOW
    )

    assert result.status == "executed"
    assert client.calls == [("sell", "TEST", "99")]


@pytest.mark.asyncio
async def test_kickoff_sell_executes_despite_value_drift(session, env, monkeypatch):
    """Anpfiff-Notbremse ist vom Drift-Check AUSGENOMMEN: sie muss auch bei
    Preisrutsch verkaufen dürfen (Spieltag retten schlägt Kurspflege)."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_PURGE", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None

    client = FakeClient()
    client.squad = [_squad_member("9", 2_000_000)]  # 60 % unter Staging-Betrag

    result = await ActionExecutor(client, session).execute(
        _sell(pid="9", amount=5_000_000, rule="kickoff-purge", league="REAL"),
        dry_run=False, now=NOW,
    )

    assert result.status == "executed"
    assert client.calls == [("sell", "REAL", "9")]


@pytest.mark.asyncio
async def test_kickoff_sell_of_gone_player_still_refused(session, env, monkeypatch):
    """Der Kader-Check (a) gilt AUCH für die Notbremse: wer nicht im Kader
    ist, kann nicht notverkauft werden."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_PURGE", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None

    client = FakeClient()
    client.squad = []

    result = await ActionExecutor(client, session).execute(
        _sell(pid="9", amount=None, rule="kickoff-purge", league="REAL"),
        dry_run=False, now=NOW,
    )

    assert result.status == "refused"
    assert "Kader" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_api_error_refuses_sell(session, env):
    """Fail-closed: liefert get_squad keine frischen Daten, wird nicht verkauft."""
    env(dry="0", test_league="TEST")

    class BrokenSquadClient(FakeClient):
        async def get_squad(self, league_id):
            raise RuntimeError("Kader-API down")

    client = BrokenSquadClient()
    result = await ActionExecutor(client, session).execute(_sell(), now=NOW)

    assert result.status == "refused"
    assert "Preflight fehlgeschlagen" in result.message
    assert client.calls == []


# --- 3. Review, Fund 3: TTL-Lücken (NULL-TTL-Altzeilen, blockierendes Alt-proposed) ---

@pytest.mark.asyncio
async def test_legacy_proposed_without_ttl_expires_after_45min(session, env):
    """Alt-'proposed' mit valid_until=NULL ist NICHT unbegrenzt ausführbar:
    nach created_at + 45 min endet die Freigabe als 'expired' (hier 10 Tage alt)."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid(), now=NOW - timedelta(days=10))
    row = session.get(TransferAction, staged.action_id)
    row.valid_until = None  # Alt-Zeile von vor der TTL-Migration simulieren
    session.commit()

    result = await ex.execute_staged(staged.action_id, now=NOW)

    assert result.status == "expired"
    assert client.calls == []
    assert session.get(TransferAction, staged.action_id).status == "expired"


@pytest.mark.asyncio
async def test_legacy_proposed_without_ttl_fresh_still_executes(session, env):
    """Frische NULL-TTL-Zeile (jünger als 45 min) bleibt ausführbar."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid(), now=NOW - timedelta(minutes=10))
    row = session.get(TransferAction, staged.action_id)
    row.valid_until = None
    session.commit()

    result = await ex.execute_staged(staged.action_id, now=NOW)

    assert result.status == "executed"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_expired_proposed_does_not_block_restage(session, env):
    """TTL-Lücke b: ein zeitlich abgelaufenes 'proposed' blockiert kein neues
    Staging mehr — die Zeile wird als frisches 'proposed' recycelt."""
    env()
    ex = ActionExecutor(FakeClient(), session)
    staged = await ex.stage(_bid(), now=NOW - timedelta(hours=3))

    again = await ex.stage(_bid(), now=NOW)

    assert again.status == "proposed"
    assert again.action_id == staged.action_id
    row = session.query(TransferAction).one()
    assert row.status == "proposed"
    assert row.valid_until.replace(tzinfo=UTC) == NOW + timedelta(minutes=45)


@pytest.mark.asyncio
async def test_expired_proposed_does_not_block_execute(session, env):
    """Auch der Direkt-Pfad recycelt ein zeitlich abgelaufenes 'proposed'."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid(), now=NOW - timedelta(hours=3))

    result = await ex.execute(_bid(), now=NOW)

    assert result.status == "executed"
    assert result.action_id == staged.action_id
    assert session.query(TransferAction).count() == 1


@pytest.mark.asyncio
async def test_fresh_proposed_still_blocks_restage(session, env):
    """Gegenprobe: ein NICHT abgelaufenes 'proposed' blockt weiterhin."""
    env()
    ex = ActionExecutor(FakeClient(), session)
    staged = await ex.stage(_bid(), now=NOW)

    again = await ex.stage(_bid(), now=NOW + timedelta(minutes=10))

    assert again.status == "duplicate"
    assert again.action_id == staged.action_id


# --- 3. Review, Fund 4: State-Machine-Races ---

@pytest.mark.asyncio
async def test_parallel_retry_claim_loser_returns_duplicate(session, env):
    """Zwei parallele Retries desselben failed-Eintrags: der Verlierer des
    atomaren Claims (rowcount 0) liefert 'duplicate' — kein zweiter API-Call."""
    env(dry="0", test_league="TEST")
    r1 = await ActionExecutor(FakeClient(fail=True), session).execute(_bid(), now=NOW)
    assert r1.status == "failed"

    # Race nachstellen: die Identity Map dieses Prozesses sieht noch 'failed',
    # aber in der DB hat der parallele Retry die Zeile bereits auf 'pending'
    # geclaimt (raw SQL am ORM vorbei = stale Read des Claim-Verlierers).
    session.query(TransferAction).one()  # 'failed' in die Identity Map laden
    session.execute(
        text("UPDATE transfer_actions SET status='pending' WHERE id=:i"),
        {"i": r1.action_id},
    )

    client = FakeClient()
    result = await ActionExecutor(client, session).execute(_bid(), now=NOW)

    assert result.status == "duplicate"
    assert "parallel" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_dry_run_preview_does_not_block_live_retry_same_day(session, env):
    """'skipped_dry_run' ist retryable: nach der Dry-Run-Vorschau darf Emils
    »mach den Trade« dieselbe Aktion am selben Tag ECHT ausführen."""
    env(dry="1", test_league="TEST")
    client = FakeClient()
    preview = await ActionExecutor(client, session).execute(_bid(), now=NOW)
    assert preview.status == "skipped_dry_run"

    env(dry="0", test_league="TEST")  # Not-Aus aus → jetzt echt nachziehen
    result = await ActionExecutor(client, session).execute(_bid(), now=NOW)

    assert result.status == "executed"
    assert result.action_id == preview.action_id  # gleiche Zeile recycelt
    assert client.calls == [("place_bid", "TEST", "99", 5_000_000)]
    row = session.query(TransferAction).one()
    assert row.status == "executed"
    assert row.dry_run is False



@pytest.mark.asyncio
async def test_gateway_5xx_marks_unknown(session, env):
    """502/503/504 kommen vom Gateway — der Upstream kann den Write verarbeitet haben
    → 'unknown' (kein blinder Retry)."""
    env(dry="0", test_league="TEST")

    class GatewayClient(FakeClient):
        async def place_bid(self, league_id, player_id, price):
            req = httpx.Request("POST", "https://api.kickbase.com/x")
            raise httpx.HTTPStatusError(
                "bad gateway", request=req, response=httpx.Response(502, request=req)
            )

    r = await ActionExecutor(GatewayClient(), session).execute(_bid(), now=NOW)
    assert r.status == "unknown" and "502" in r.message


@pytest.mark.asyncio
async def test_connect_error_stays_failed_and_retryable(session, env):
    """Verbindung nie aufgebaut → der Write ist sicher NICHT angekommen → 'failed'
    (retrybar), nicht 'unknown'."""
    env(dry="0", test_league="TEST")

    class ConnectClient(FakeClient):
        async def place_bid(self, league_id, player_id, price):
            raise httpx.ConnectError("name resolution failed")

    r = await ActionExecutor(ConnectClient(), session).execute(_bid(), now=NOW)
    assert r.status == "failed"


@pytest.mark.asyncio
async def test_bid_preflight_is_fail_closed_without_pbaa(session, env):
    """Fehlt Kickbase' Konto-nach-Geboten (pbaa), wird ein Gebot verweigert statt
    blind auf das Cash-Budget zurueckzufallen (Review-Fund 23.08.)."""
    env(dry="0", test_league="TEST")

    class NoPbaaClient(FakeClient):
        async def get_budget_fields(self, league_id):
            return {"b": self.budget}  # kein pbaa

    client = NoPbaaClient()
    r = await ActionExecutor(client, session).execute(_bid(), now=NOW)
    assert r.status == "refused"
    assert "pbaa" in r.message and "fail-closed" in r.message
    assert not any(c[0] == "place_bid" for c in client.calls)


# --- T-120-Freeze; davor gelten 33-%-/Pfandregeln ---------------------------
# Der T-120-Storno-Wächter räumt auf — kein anderer Pfad darf danach neue
# Gebote platzieren. Fixtures + Aufrufe hängen hier an der ECHTEN Uhr
# (kein now=NOW), weil default_preflight die Deadline gegen datetime.now(UTC)
# rechnet — fixe Anker wären Zeitbomben (LEARNINGS 23.08.).


def _fixture_in(delta_minutes: int, matchday: int = 1):
    from datetime import UTC as _UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    return SimpleNamespace(
        kickoff=_dt.now(_UTC) + _td(minutes=delta_minutes),
        matchday=matchday,
        is_finished=False,
    )


@pytest.mark.asyncio
async def test_bid_freeze_blocks_new_bids_within_t120(session, env):
    """90 min vor dem ersten Anpfiff: Gebot wird trotz voller Deckung verweigert."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.fixtures = [_fixture_in(90)]

    r = await ActionExecutor(client, session).execute(_bid())

    assert r.status == "refused"
    assert "T-120" in r.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_24h_before_kickoff_allows_a_33pct_compliant_replacement_bid(session, env):
    """24 h vor Anpfiff darf ein Ersatzgebot ins Minus gehen; T-120 sichert später."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.budget = 4_000_000
    client.fixtures = [_fixture_in(24 * 60)]

    r = await ActionExecutor(client, session).execute(_bid(amount=5_000_000))

    assert r.status == "executed"
    assert [c[0] for c in client.calls] == ["place_bid"]


@pytest.mark.asyncio
async def test_exposure_gate_lets_covered_bid_through(session, env):
    """24 h vor Anpfiff, Worst-Case gedeckt (Konto 99 M, Gebot 5 M) -> Write laeuft."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.fixtures = [_fixture_in(24 * 60)]

    r = await ActionExecutor(client, session).execute(_bid(amount=5_000_000))

    assert r.status == "executed"
    assert [c[0] for c in client.calls] == ["place_bid"]


@pytest.mark.asyncio
async def test_72h_before_kickoff_33pct_rules_apply(session, env):
    """72 h vor Anpfiff: Worst-Case negativ, aber 33-%-konform -> Gebot laeuft."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.budget = 4_000_000  # Teamwert 7 M -> Limit -2,33 M; 5 M -> -1 M ok
    client.fixtures = [_fixture_in(72 * 60)]

    r = await ActionExecutor(client, session).execute(_bid(amount=5_000_000))

    assert r.status == "executed"
    assert [c[0] for c in client.calls] == ["place_bid"]


@pytest.mark.asyncio
async def test_freeze_does_not_touch_sells_or_lists(session, env):
    """Verkaeufe/Listings verbessern das Konto — das Fenster gilt NUR fuer Gebote."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.fixtures = [_fixture_in(90)]

    r = await ActionExecutor(client, session).execute(
        PlannedAction(
            action_type="sell", league_id="TEST", player_id="99",
            amount=5_000_000, rule_id="r1",
        )
    )

    assert r.status == "executed"
    assert [c[0] for c in client.calls] == ["sell"]


@pytest.mark.asyncio
async def test_broken_matchdays_fails_closed_for_bids(session, env):
    """Wirft get_matchdays (API down), bricht der Preflight fail-closed ab —
    Konto >= 0 beim Anpfiff schlaegt jeden Trade."""
    env(dry="0", test_league="TEST")

    class BrokenFixturesClient(FakeClient):
        async def get_matchdays(self):
            raise RuntimeError("Spielplan-API down")

    client = BrokenFixturesClient()
    r = await ActionExecutor(client, session).execute(_bid())

    assert r.status == "refused"
    assert "Preflight fehlgeschlagen" in r.message
    assert client.calls == []


# --- Zentrale Invarianten (Codex-P1, 24.08.): Kappe · Vereinslimit · Caps · Starter ---


@pytest.mark.asyncio
async def test_overpay_cap_blocks_bid_above_110_percent_mv(session, env):
    """Gebot über MW × 1.10 wird an der Schreibstelle gekappt — auch Emils
    +%-Anpassungen laufen gegen dieselbe Kappe."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.market = [SimpleNamespace(
        player=SimpleNamespace(id="99", market_value=10_000_000, team_id="11"),
        price=10_000_000,
    )]

    r = await ActionExecutor(client, session).execute(_bid(amount=11_100_000), now=NOW)

    assert r.status == "refused" and "Overpay-Kappe" in r.message
    assert client.calls == []

    ok = await ActionExecutor(client, session).execute(_bid(amount=10_900_000), now=NOW)
    assert ok.status == "executed"


@pytest.mark.asyncio
async def test_club_limit_counts_squad_and_open_bids(session, env):
    """Vereinslimit (lpc): Kader + offene Gebote des Ziel-Clubs + dieses Gebot
    dürfen das Liga-Limit nicht sprengen (Regel 5)."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.league_rules = {"max_players_per_club": 2}
    for sp in client.squad:
        if sp.player.id == "9":
            sp.player.team_id = "11"          # 1 Kaderspieler des Ziel-Clubs
    client.own_bids = [{"player_id": "77", "team_id": "11", "amount": 1_000_000}]

    r = await ActionExecutor(client, session, user_id="U1").execute(_bid(), now=NOW)

    assert r.status == "refused" and "Vereinslimit" in r.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_daily_cap_counts_all_paths_and_spares_kickoff(session, env):
    """Zentrale Tages-Caps zählen executed-Zeilen ALLER rule_ids; die
    Notbremse (kickoff-*) bleibt ausgenommen."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    for i in range(DEFAULT_GUARDRAILS.daily_sell_cap):
        session.add(TransferAction(
            league_id="TEST", action_type="sell", player_id=f"p{i}",
            status="executed", idempotency_key=f"k{i}", rule_id="mcp-sell",
            dry_run=False, created_at=NOW,
        ))
    session.commit()

    r = await ActionExecutor(client, session).execute(
        PlannedAction(action_type="sell", league_id="TEST", player_id="99",
                      amount=5_000_000, rule_id="intent-sell"),
        now=NOW,
    )
    assert r.status == "refused" and "Tages-Cap Verkäufe" in r.message

    purge = await ActionExecutor(client, session).execute(
        PlannedAction(action_type="sell", league_id="TEST", player_id="9",
                      rule_id="kickoff-purge"),
        now=NOW,
    )
    assert purge.status == "executed"       # Notbremse kennt keinen Cap


@pytest.mark.asyncio
async def test_listings_do_not_consume_the_sell_cap(session, env):
    """L-077 (26.08.): Fünf Gegenfinanzierungs-Listings sperrten den einzigen
    echten Verkauf des Abends — obwohl kein Listing den Kader verkleinert.
    Seitdem zählt der Verkaufs-Cap nur Abgänge (sell/accept)."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    for i in range(5):
        session.add(TransferAction(
            league_id="TEST", action_type="list", player_id=f"p{i}",
            status="executed", idempotency_key=f"k{i}", rule_id="manual-mandate",
            dry_run=False, created_at=NOW,
        ))
    session.commit()

    r = await ActionExecutor(client, session).execute(
        PlannedAction(action_type="sell", league_id="TEST", player_id="99",
                      amount=5_000_000, rule_id="intent-sell"),
        now=NOW,
    )
    assert r.status == "executed", r.message


@pytest.mark.asyncio
async def test_listings_keep_their_own_cap(session, env):
    """Eine Bremse bleibt: ein Fehlläufer soll nicht den halben Kader listen."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    for i in range(DEFAULT_GUARDRAILS.daily_list_cap):
        session.add(TransferAction(
            league_id="TEST", action_type="list", player_id=f"p{i}",
            status="executed", idempotency_key=f"k{i}", rule_id="manual-mandate",
            dry_run=False, created_at=NOW,
        ))
    session.commit()

    r = await ActionExecutor(client, session).execute(
        PlannedAction(action_type="list", league_id="TEST", player_id="99",
                      amount=5_000_000, rule_id="intent-list"),
        now=NOW,
    )
    assert r.status == "refused" and "Tages-Cap Listings" in r.message


@pytest.mark.asyncio
async def test_starter_protection_blocks_autonomous_sale_only(session, env):
    """Aufgestellte Spieler (Squad-`lo`) verkauft kein autonomer Pfad; Emils
    manuelle Wege (mcp-*) und die Notbremse bleiben frei."""
    env(dry="0", test_league="TEST")

    class LineupClient(FakeClient):
        async def _get(self, path):
            if "squad" in str(path):
                return {"it": [{"i": "99", "lo": 3}]}   # 99 ist aufgestellt
            return self.raw_market

    client = LineupClient()
    auto = await ActionExecutor(client, session).execute(
        PlannedAction(action_type="sell", league_id="TEST", player_id="99",
                      amount=5_000_000, rule_id="intent-sell"),
        now=NOW,
    )
    assert auto.status == "refused" and "Starter" in auto.message

    manual = await ActionExecutor(client, session).execute(
        PlannedAction(action_type="sell", league_id="TEST", player_id="99",
                      amount=5_100_000, rule_id="mcp-sell"),
        now=NOW,
    )
    assert manual.status == "executed"


# --- Underpay-Sperre für Manager-Listings (err 5080 „UnderpayNotAllowed", 24.08.) ---
# Leiter-Beweis: 90 % ✗, 95 % ✗, 100 % ✓ — Kickbase lehnt Gebote unter dem
# AKTUELLEN Marktwert auf Manager-Listings mit HTTP 500 ab; der Listing-Preis
# (prc) ist Deko. Die Sperre sitzt zentral im Preflight der einzigen
# Schreibstelle: auch MCP-Gebote, Emils Betrags-Anpassungen und ein
# 22-Uhr-MW-Anstieg zwischen Planung und Ausführung laufen gegen dieselbe Wand.


@pytest.mark.asyncio
async def test_preflight_blocks_manager_listing_underpay(session, env):
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, _ = _preflight_stub(PreflightState(
        on_market=True, is_manager_listing=True, current_mv=6_000_000
    ))
    ex = ActionExecutor(client, session, preflight=cb)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)

    assert result.status == "refused"
    assert "5080" in result.message and "Manager-Listing" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_preflight_allows_manager_bid_at_market_value(session, env):
    """Exakt 100 % MW wurde live platziert (Otávio/Ulrich) — die Sperre greift
    nur UNTER dem Marktwert."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, _ = _preflight_stub(PreflightState(
        on_market=True, is_manager_listing=True, current_mv=5_000_000
    ))
    ex = ActionExecutor(client, session, preflight=cb)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)

    assert result.status == "executed"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_preflight_allows_kickbase_listing_underpay(session, env):
    """Kickbase-Listings (kein Verkäufer) erlauben Underpay weiterhin —
    Friedrich (81 % MW) wurde platziert, er verlor nur."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, _ = _preflight_stub(PreflightState(
        on_market=True, is_manager_listing=False, current_mv=6_000_000
    ))
    ex = ActionExecutor(client, session, preflight=cb)

    result = await ex.execute(_bid(amount=5_000_000), now=NOW)

    assert result.status == "executed"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_default_preflight_detects_manager_listing_underpay(session, env):
    """End-to-end über den auto-verdrahteten default_preflight: das Markt-Item
    trägt einen Verkäufer (seller_id) → Gebot unter MW wird verweigert."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.market[0].seller_id = "4242"  # MW 20 M, Gebot 5 M → Underpay

    result = await ActionExecutor(client, session).execute(_bid(), now=NOW)

    assert result.status == "refused"
    assert "5080" in result.message
    assert not any(c[0] == "place_bid" for c in client.calls)


# --- HTTP-Fehlerkörper im Audit (err/errMsg statt stummem „HTTP 500", 24.08.) ---


@pytest.mark.asyncio
async def test_http_500_body_lands_in_failed_rationale(session, env):
    """Kickbase erklärt den echten Grund eines 500ers NUR im Body — der landet
    gekürzt in der failed-rationale (5 Fehlversuche über 2 Sessions, weil nur
    „HTTP 500" sichtbar war)."""
    env(dry="0", test_league="TEST")

    class UnderpayClient(FakeClient):
        async def place_bid(self, league_id, player_id, price):
            req = httpx.Request("POST", "https://api.kickbase.com/x")
            raise httpx.HTTPStatusError(
                "server error", request=req,
                response=httpx.Response(
                    500, request=req,
                    json={"err": 5080, "errMsg": "UnderpayNotAllowed"},
                ),
            )

    result = await ActionExecutor(UnderpayClient(), session).execute(_bid(), now=NOW)

    assert result.status == "failed"
    assert "5080" in result.message and "UnderpayNotAllowed" in result.message
    row = session.query(TransferAction).one()
    assert "UnderpayNotAllowed" in (row.rationale or "")


def test_response_excerpt_truncates_and_flattens():
    from kickbase_agent.agent.executor import _response_excerpt

    req = httpx.Request("POST", "https://api.kickbase.com/x")
    exc = httpx.HTTPStatusError(
        "server error", request=req,
        response=httpx.Response(500, request=req, text="  a\n" * 400),
    )
    excerpt = _response_excerpt(exc)
    assert len(excerpt) <= 300
    assert excerpt.endswith("…")
    assert "\n" not in excerpt

# --- Codex-Folge-Review 24.08.: Approval-Caps, pending-Slots, Manager-Drift, -manual ---


@pytest.mark.asyncio
async def test_staged_path_enforces_daily_cap(session, env):
    """P1: Auch der Approval-Pfad (execute_staged) läuft gegen die Tages-Caps —
    Discord-Freigaben sind kein Cap-Bypass."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    for i in range(DEFAULT_GUARDRAILS.daily_buy_cap):
        session.add(TransferAction(
            league_id="TEST", action_type="bid", player_id=f"c{i}",
            status="executed", idempotency_key=f"cap{i}", rule_id="mcp-bid",
            dry_run=False, created_at=NOW,
        ))
    session.commit()
    cb, _ = _preflight_stub(PreflightState(on_market=True))
    ex = ActionExecutor(client, session, preflight=cb)
    staged = await ex.stage(_bid(), now=NOW)

    result = await ex.execute_staged(staged.action_id, now=NOW + timedelta(minutes=5))

    assert result.status == "refused" and "Tages-Cap" in result.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_daily_cap_counts_pending_reservations(session, env):
    """P1 (TOCTOU entschärft): `pending` zählt als belegter Slot — zwei parallele
    Pfade können nicht beide den letzten freien Slot sehen."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    for i in range(DEFAULT_GUARDRAILS.daily_sell_cap - 1):
        session.add(TransferAction(
            league_id="TEST", action_type="sell", player_id=f"s{i}",
            status="executed", idempotency_key=f"se{i}", rule_id="mcp-sell",
            dry_run=False, created_at=NOW,
        ))
    session.add(TransferAction(
        league_id="TEST", action_type="sell", player_id="s9",
        status="pending", idempotency_key="pend-slot", rule_id="intent-sell",
        dry_run=False, created_at=NOW,
    ))
    session.commit()

    r = await ActionExecutor(client, session).execute(
        PlannedAction(action_type="sell", league_id="TEST", player_id="99",
                      amount=5_000_000, rule_id="intent-sell"),
        now=NOW,
    )
    assert r.status == "refused" and "Tages-Cap" in r.message


@pytest.mark.asyncio
async def test_manager_listing_ignores_decorative_price_drift(session, env):
    """P2: `prc` ist bei Manager-Listings Deko (err-5080-Beweis) — ein hoher
    Wunschpreis blockiert kein regelkonformes 100–110-%-MW-Gebot. Kickbase-
    Listings behalten den Drift-Schutz."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    cb, _ = _preflight_stub(PreflightState(
        on_market=True, is_manager_listing=True,
        current_mv=5_000_000, current_price=9_000_000,
    ))
    ex = ActionExecutor(client, session, preflight=cb)
    result = await ex.execute(_bid(amount=5_400_000), now=NOW)
    assert result.status == "executed"
    assert len(client.calls) == 1

    cb2, _ = _preflight_stub(PreflightState(
        on_market=True, is_manager_listing=False,
        current_mv=5_000_000, current_price=9_000_000,
    ))
    ex2 = ActionExecutor(FakeClient(), session, preflight=cb2)
    r2 = await ex2.execute(
        PlannedAction(action_type="bid", league_id="TEST", player_id="77",
                      amount=5_400_000, rule_id="mcp-bid"),
        now=NOW,
    )
    assert r2.status == "refused" and "10 %" in r2.message


@pytest.mark.asyncio
async def test_manual_remote_sell_bypasses_starter_protection(session, env):
    """P2: `intent-sell-manual` (Emils bestätigtes Remote-Vorhaben) ist NICHT
    autonom — der Starter-Schutz blockt nur Engine-Pfade; der Transport (MCP
    lokal vs. Box-Vorhaben) ändert die Semantik nicht mehr."""
    env(dry="0", test_league="TEST")

    class LineupClient(FakeClient):
        async def _get(self, path):
            if "squad" in str(path):
                return {"it": [{"i": "99", "lo": 3}]}   # 99 ist aufgestellt
            return self.raw_market

    manual = await ActionExecutor(LineupClient(), session).execute(
        PlannedAction(action_type="sell", league_id="TEST", player_id="99",
                      amount=5_100_000, rule_id="intent-sell-manual"),
        now=NOW,
    )
    assert manual.status == "executed"


# --- Emils Auftrag 25.08.: JEDES Gebot trägt eine überprüfbare Erwartung ---


@pytest.mark.asyncio
async def test_bid_gets_expectation_from_preflight(session, env):
    """Kickbase-Listing ohne Mitbieter ⇒ hohe Zuschlag-Chance, im Audit hinterlegt.

    Der Wert kommt NICHT vom Aufrufer, sondern aus dem frischen Preflight — so
    trägt jedes Gebot eine Erwartung, egal ob Planner, MCP oder Sofort-Eingriff.
    """
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(amount=21_000_000), now=NOW)

    assert result.status == "executed"
    row = session.get(TransferAction, result.action_id)
    exp = row.payload["expectation"]
    # 21 M auf 20 M Marktwert = 105 % — nach der Ljubičić-Lehre (26.08.) KEIN
    # sicherer Zuschlag mehr, auch ohne sichtbare Mitbieter.
    assert exp["kind"] == "kickbase" and 0.35 <= exp["p"] <= 0.55
    assert "Auto-Zuschlag" in exp["why"]


@pytest.mark.asyncio
async def test_manager_listing_bid_gets_sober_expectation(session, env):
    """Manager-Listing knapp über Marktwert ⇒ ehrlich niedrige Chance (Bilanz 25.08.)."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.market[0].seller_id = "999"  # Manager-Listing
    ex = ActionExecutor(client, session)

    result = await ex.execute(_bid(amount=21_000_000), now=NOW)

    row = session.get(TransferAction, result.action_id)
    exp = row.payload["expectation"]
    assert exp["kind"] == "manager" and exp["p"] <= 0.2


@pytest.mark.asyncio
async def test_non_bid_actions_get_no_expectation(session, env):
    """Verkäufe/Listings haben keine Zuschlag-Chance — nichts erfinden."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    ex = ActionExecutor(client, session)

    result = await ex.execute(
        PlannedAction(action_type="sell", league_id="TEST", player_id="99",
                      amount=5_000_000, rule_id="r1"),
        now=NOW,
    )
    row = session.get(TransferAction, result.action_id)
    assert "expectation" not in (row.payload or {})


# --- Verfassung: geschuetzte Spieler (26.08.) --------------------------------
#
# Emils Anweisung vom 25.08. ("Verkaufe nicht Olise, aber sonst mach einfach die
# Entscheidung") war bis heute blosse Doktrin: `protected_player_ids` war in JEDEM
# Produktionspfad leer. Der Schutz sitzt jetzt an der EINZIGEN Schreibstelle —
# jeder Abgabe-Pfad (sell/list/accept) laeuft gegen dieselbe Wand.

PROTECTED = "8329"  # Olise


def _protected_client() -> FakeClient:
    client = FakeClient()
    client.squad = [*client.squad, _squad_member(PROTECTED, 5_000_000, position=3)]
    return client


@pytest.mark.asyncio
async def test_protected_player_is_never_sold(session, env):
    env(dry="0", test_league="TEST")
    client = _protected_client()

    r = await ActionExecutor(client, session).execute(_sell(pid=PROTECTED), now=NOW)

    assert r.status == "refused"
    assert "Geschützter Spieler" in r.message and PROTECTED in r.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_protected_player_is_never_listed_or_given_away(session, env):
    env(dry="0", test_league="TEST")
    client = _protected_client()
    ex = ActionExecutor(client, session)

    listed = await ex.execute(_list(pid=PROTECTED), now=NOW)
    accepted = await ex.execute(
        PlannedAction(action_type="accept", league_id="TEST", player_id=PROTECTED,
                      amount=6_000_000, offer_id="o1", rule_id="accept_offer"),
        now=NOW,
    )

    assert listed.status == "refused" and "Geschützter Spieler" in listed.message
    assert accepted.status == "refused" and "Geschützter Spieler" in accepted.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_protected_player_survives_the_kickoff_emergency_brake(
    session, env, monkeypatch
):
    """Auch die Notbremse (kickoff-*) verkauft ihn nicht — sie ist von Caps und
    Drift ausgenommen, NICHT von der Verfassung."""
    env(dry="0", test_league="", allow_live="0")
    monkeypatch.setenv("AGENT_AUTO_PURGE", "1")
    import kickbase_agent.config as cfg
    cfg._settings = None
    client = _protected_client()

    r = await ActionExecutor(client, session).execute(
        _sell(pid=PROTECTED, amount=None, rule="kickoff-purge", league="REAL"),
        dry_run=False, now=NOW,
    )

    assert r.status == "refused" and "Geschützter Spieler" in r.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_protected_refusal_is_visible_even_in_dry_run(session, env):
    """Die Verfassung steht VOR dem Kill-Switch: im Schatten-Modus meldet der
    Executor 'refused' mit Grund statt eines harmlosen 'skipped_dry_run' —
    sonst saehe ein Bericht so aus, als waere der Verkauf nur wegen DRY_RUN
    unterblieben."""
    env(dry="1", test_league="TEST")
    client = _protected_client()

    r = await ActionExecutor(client, session).execute(_sell(pid=PROTECTED), now=NOW)

    assert r.status == "refused"
    row = session.get(TransferAction, r.action_id)
    assert row.status == "refused" and "Geschützter Spieler" in (row.rationale or "")


@pytest.mark.asyncio
async def test_protected_player_is_never_staged_as_a_proposal(session, env):
    env(dry="0", test_league="TEST")
    client = _protected_client()

    r = await ActionExecutor(client, session).stage(_sell(pid=PROTECTED), now=NOW)

    assert r.status == "refused" and "Geschützter Spieler" in r.message
    assert session.query(TransferAction).count() == 0


@pytest.mark.asyncio
async def test_protected_player_may_still_be_bought(session, env):
    """Der Schutz verbietet die ABGABE, nicht den Erwerb."""
    env(dry="0", test_league="TEST")
    client = FakeClient()
    client.market = [SimpleNamespace(
        player=SimpleNamespace(id=PROTECTED, market_value=20_000_000, team_id="11"),
        price=5_000_000,
    )]

    r = await ActionExecutor(client, session).execute(
        PlannedAction(action_type="bid", league_id="TEST", player_id=PROTECTED,
                      amount=5_000_000, rule_id="r1"),
        now=NOW,
    )

    assert r.status == "executed"


@pytest.mark.asyncio
async def test_unprotected_players_are_sold_as_before(session, env):
    env(dry="0", test_league="TEST")
    client = _protected_client()

    r = await ActionExecutor(client, session).execute(_sell(pid="99"), now=NOW)

    assert r.status == "executed"
    assert client.calls == [("sell", "TEST", "99")]


@pytest.mark.asyncio
async def test_empty_protected_list_restores_old_behaviour(session, env, monkeypatch):
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", "")
    env(dry="0", test_league="TEST")
    client = _protected_client()

    r = await ActionExecutor(client, session).execute(_sell(pid=PROTECTED), now=NOW)

    assert r.status == "executed"
    assert client.calls == [("sell", "TEST", PROTECTED)]


# --- Gestaffelte Overpay-Kappe (26.08., Ljubicic-Widerspruch) ----------------


def _need_bid(amount: int) -> PlannedAction:
    """Gebot mit ausdruecklicher Kader-Bedarf-Markierung."""
    return PlannedAction(action_type="bid", league_id="TEST", player_id="99",
                         amount=amount, rule_id="r1", squad_need=True)


def _mv_client(mv: int = 10_000_000) -> FakeClient:
    client = FakeClient()
    client.market = [SimpleNamespace(
        player=SimpleNamespace(id="99", market_value=mv, team_id="11"), price=mv,
    )]
    return client


@pytest.mark.asyncio
async def test_need_bid_may_pay_up_to_135_percent(session, env):
    """Kader-Bedarf: 125 % MW geht durch — die 120-/135-%-Stufen der
    Erwartungsformel sind damit erreichbar (Ljubicic: 108 % verloren gegen 157 %)."""
    env(dry="0", test_league="TEST")
    client = _mv_client()

    r = await ActionExecutor(client, session).execute(_need_bid(12_500_000), now=NOW)

    assert r.status == "executed"
    assert client.calls == [("place_bid", "TEST", "99", 12_500_000)]


@pytest.mark.asyncio
async def test_need_bid_is_still_capped_at_135_percent(session, env):
    env(dry="0", test_league="TEST")
    client = _mv_client()

    r = await ActionExecutor(client, session).execute(_need_bid(13_600_000), now=NOW)

    assert r.status == "refused" and "Overpay-Kappe" in r.message
    assert "Stufe Kader-Bedarf" in r.message and "135 %" in r.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_unmarked_bid_keeps_the_110_percent_cap(session, env):
    """Ohne Markierung bleibt alles beim Alten — die hoehere Praemie ist die
    Ausnahme, nicht der neue Normalfall."""
    env(dry="0", test_league="TEST")
    client = _mv_client()

    r = await ActionExecutor(client, session).execute(_bid(amount=11_100_000), now=NOW)

    assert r.status == "refused" and "Overpay-Kappe" in r.message
    assert "Stufe Standard" in r.message and "110 %" in r.message
    # Der Hinweis nennt den Ausweg — sonst rätselt der nächste Leser, warum
    # die eigene 120-%-Regel nicht greift.
    assert "squad_need" in r.message
    assert client.calls == []


@pytest.mark.asyncio
async def test_need_marker_survives_the_staging_round_trip(session, env):
    """Der Marker muss den Payload-Umweg (stage → Freigabe) ueberleben, sonst
    faellt ein freigegebenes Bedarfs-Gebot zurueck auf die 110-%-Kappe."""
    env(dry="0", test_league="TEST")
    client = _mv_client()
    ex = ActionExecutor(client, session)

    staged = await ex.stage(_need_bid(12_500_000), now=NOW)
    result = await ex.execute_staged(staged.action_id, now=NOW)

    assert result.status == "executed"
    assert client.calls == [("place_bid", "TEST", "99", 12_500_000)]

# --- Selbst-Einfrieren (A2): der Agent sperrt seine eigenen Geld-Writes ---


@pytest.mark.asyncio
async def test_frozen_agent_refuses_a_live_bid(session, env):
    """Eingefroren heißt eingefroren — auch in der Test-Liga, auch mit allen Flags."""
    from kickbase_agent.agent import freeze as fz

    env(dry="0", test_league="TEST", allow_live="1")
    fz.freeze(session, reason="Tagesverlust 4,0 M", source=fz.SOURCE_LOSS, now=NOW)
    client = FakeClient()

    result = await ActionExecutor(client, session).execute(_bid(league="TEST"), now=NOW)

    assert result.status == "refused"
    assert "Selbst-Einfrieren" in result.message
    assert client.calls == []  # keine API berührt


@pytest.mark.asyncio
async def test_frozen_agent_still_runs_the_kickoff_emergency_brake(session, env):
    """Konto ≥ 0 beim Anpfiff schlägt jede Selbstsicherung (sonst 0 Punkte)."""
    from kickbase_agent.agent import freeze as fz

    env(dry="0", test_league="TEST")
    fz.freeze(session, reason="Tagesverlust", source=fz.SOURCE_LOSS, now=NOW)

    result = await ActionExecutor(FakeClient(), session).execute(
        _sell(rule="kickoff-purge"), now=NOW
    )

    assert result.status == "executed"


@pytest.mark.asyncio
async def test_frozen_agent_still_cancels_bids(session, env):
    """Storno baut Risiko AB — es einzufrieren wäre pervers."""
    from kickbase_agent.agent import freeze as fz

    env(dry="0", test_league="TEST")
    fz.freeze(session, reason="unklare Writes", source=fz.SOURCE_WRITES, now=NOW)

    result = await ActionExecutor(FakeClient(), session).execute(
        PlannedAction(action_type="cancel_bid", league_id="TEST", player_id="99",
                      offer_id="o1", rule_id="r1"),
        now=NOW,
    )

    assert result.status == "executed"


@pytest.mark.asyncio
async def test_release_lets_the_agent_trade_again(session, env):
    from kickbase_agent.agent import freeze as fz

    env(dry="0", test_league="TEST")
    fz.freeze(session, reason="Tagesverlust", source=fz.SOURCE_LOSS, now=NOW)
    fz.release(session, by="emil", now=NOW)

    result = await ActionExecutor(FakeClient(), session).execute(
        _bid(league="TEST"), now=NOW
    )

    assert result.status == "executed"


@pytest.mark.asyncio
async def test_approval_click_does_not_beat_the_freeze(session, env):
    """Ein Freigabe-Klick hebt das Selbst-Einfrieren nicht auf."""
    from kickbase_agent.agent import freeze as fz

    env(dry="0", test_league="TEST", allow_live="1")
    client = FakeClient()
    ex = ActionExecutor(client, session)
    staged = await ex.stage(_bid(league="TEST"), now=NOW)
    fz.freeze(session, reason="Tagesverlust", source=fz.SOURCE_LOSS, now=NOW)

    result = await ex.execute_staged(staged.action_id, now=NOW)

    assert result.status == "refused" and "Selbst-Einfrieren" in result.message
    assert client.calls == []
