"""Discord-Karten im Worker (Spec 2026-08-31 §4.4/§4.5/§7; Review-Umbau 31.08.).

Planner postet Karten NUR für `created` und übersteht Discord-Fehler; der
Kehrer (collect_card_work im Lock + apply_card_work danach, L-088) zieht
Web-Vetos nach und finalisiert genau einmal; 404 nullt die msg_id und setzt
synced_at (Backfill-Schutz — nie neu posten); nie geborene Karten holt der
Backfill nach; der Executor-Tick kehrt auch, wenn nichts auszuführen war.
Dazu: handle_intent_action (Button → Service, via to_thread).
Fakes nach dem Muster von tests/worker/test_intents.py (_patch_world).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import kickbase_agent.worker.intents as wi
from kickbase_agent.agent.executor import PlannedAction
from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import Base, Intent
from kickbase_agent.decision.models import Proposal
from kickbase_agent.worker.intents import (
    apply_card_work,
    card_jobs_for_created,
    cards_needing_post,
    collect_card_retire_work,
    collect_card_work,
    handle_intent_action,
    run_intent_executor,
    run_intent_planner,
    stale_cards,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)
MIN = timedelta(minutes=1)


@pytest.fixture
def engine():
    # StaticPool + check_same_thread=False: handle_intent_action läuft via
    # to_thread und apply_card_work schreibt in eigenen Sessions — alle Threads
    # müssen DIESELBE in-memory-DB sehen.
    eng = create_engine(
        "sqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


async def _kehren(engine, session: Session, channel, now: datetime) -> int:
    """Test-Helfer: ein Kehrer-Durchlauf wie im Executor-Tick (collect → apply)."""
    work = collect_card_work(session, now)
    done = await apply_card_work(channel, work)
    session.expire_all()  # apply schreibt in eigenen Sessions
    return done


@pytest.fixture
def session(engine):
    with Session(engine, future=True) as s:
        yield s


@pytest.fixture
def env(monkeypatch):
    def _set(dry="1", intents="0"):
        monkeypatch.setenv("AGENT_DRY_RUN", dry)
        monkeypatch.setenv("AGENT_AUTO_INTENTS", intents)
        monkeypatch.setenv("AGENT_ALLOW_LIVE", "0")
        monkeypatch.setenv("AGENT_WRITE_SPACING_SECONDS", "0")
        import kickbase_agent.config as cfg

        cfg._settings = None

    yield _set
    import kickbase_agent.config as cfg

    cfg._settings = None


def _intent(session, *, kind="bid", pid="B1", amount=1_200_000, status="planned",
            execute_at=NOW + 2 * H, updated_at=None, msg_id=None, synced_at=None,
            adjusted_from=None, note=None, result=None) -> Intent:
    row = Intent(
        created_at=NOW - H, updated_at=updated_at or NOW - H, kind=kind, player_id=pid,
        player_name="Kaufmann", team_id="T1", position=4, amount=amount, rationale="r",
        sources={"score": 0.62}, listing_type="kickbase", deadline=None,
        execute_at=execute_at, status=status, adjusted_from=adjusted_from,
        emil_note=note, result=result, discord_msg_id=msg_id,
        discord_card_synced_at=synced_at,
    )
    session.add(row)
    session.commit()
    return row


class FakeCardChannel:
    """send_info + send_card + edit_card — protokolliert alles; Fehler steuerbar."""

    def __init__(self, *, fail_send_ids=(), missing_msg_ids=()):
        self.infos: list[str] = []
        self.cards: list[dict] = []
        self.edits: list[dict] = []
        self.fail_send_ids = set(fail_send_ids)  # Intent-IDs, deren Post crasht
        self.missing_msg_ids = set(missing_msg_ids)  # Message-IDs, die 404 liefern
        self._next_msg_id = 1000

    async def send_info(self, text: str, *, kind=None, title=None) -> None:
        self.infos.append(text)

    async def send_card(self, *, embed, view) -> str | None:
        intent_id = int(embed.title.split("#")[1].split(" ")[0])
        if intent_id in self.fail_send_ids:
            raise RuntimeError("discord down")
        self._next_msg_id += 1
        self.cards.append({"intent_id": intent_id, "msg_id": str(self._next_msg_id),
                           "embed": embed, "view": view})
        return str(self._next_msg_id)

    async def edit_card(self, message_id: str, *, embed, view) -> bool:
        if message_id in self.missing_msg_ids:
            return False  # 404: Karte manuell gelöscht
        self.edits.append({"msg_id": message_id, "embed": embed, "view": view})
        return True


# ------------------------------------------------- Karten-Posts (Planner-Weg)


@pytest.mark.asyncio
async def test_card_jobs_for_created_store_msg_id_and_synced_at(
    engine, session, monkeypatch
):
    monkeypatch.setattr(wi, "SessionLocal", sessionmaker(bind=engine, future=True))
    monkeypatch.setattr(wi, "CARD_POST_SPACING_S", 0)
    a = _intent(session, pid="A")
    b = _intent(session, pid="B")
    channel = FakeCardChannel()

    jobs = card_jobs_for_created([a, b], NOW)
    assert await apply_card_work(channel, jobs) == 2

    with Session(engine, future=True) as s:
        rows = {r.player_id: r for r in s.execute(select(Intent)).scalars()}
    assert rows["A"].discord_msg_id == "1001"
    assert rows["B"].discord_msg_id == "1002"
    for r in rows.values():
        assert r.discord_card_synced_at == r.updated_at
    # Karte trägt Buttons (bid: 4) und das Embed nennt das Vorhaben
    assert len(channel.cards[0]["view"].children) == 4
    assert "Vorhaben #1" in channel.cards[0]["embed"].title


@pytest.mark.asyncio
async def test_card_posts_survive_discord_error_per_post(
    engine, session, monkeypatch
):
    """Discord darf den Planner nie brechen: ein crashender Post überspringt nur
    DIESE Karte, die nächste wird trotzdem gepostet."""
    monkeypatch.setattr(wi, "SessionLocal", sessionmaker(bind=engine, future=True))
    monkeypatch.setattr(wi, "CARD_POST_SPACING_S", 0)
    a = _intent(session, pid="A")  # id 1 → Post crasht
    b = _intent(session, pid="B")  # id 2 → Post klappt
    channel = FakeCardChannel(fail_send_ids={1})

    assert await apply_card_work(channel, card_jobs_for_created([a, b], NOW)) == 1

    with Session(engine, future=True) as s:
        rows = {r.player_id: r for r in s.execute(select(Intent)).scalars()}
    assert rows["A"].discord_msg_id is None  # nichts gespeichert, kein Crash
    assert rows["B"].discord_msg_id == "1001"


@pytest.mark.asyncio
async def test_card_posts_skip_channels_without_card_support(session):
    class BareChannel:
        async def send_info(self, text, *, kind=None, title=None): ...

    row = _intent(session)
    assert await apply_card_work(BareChannel(), card_jobs_for_created([row], NOW)) == 0
    assert row.discord_msg_id is None


# ----------------------------------------------------------- Kehrer


@pytest.fixture
def kehr_env(monkeypatch, engine):
    """apply_card_work schreibt in eigenen Sessions — auf die Test-Engine lenken."""
    monkeypatch.setattr(wi, "SessionLocal", sessionmaker(bind=engine, future=True))


@pytest.mark.asyncio
async def test_kehrer_finalizes_web_veto_exactly_once(engine, session, kehr_env):
    """Web-Veto (api-Container hat keinen Bot): Kehrer zieht die Karte final
    (view=None) und setzt synced_at — der zweite Lauf editiert NICHTS mehr."""
    _intent(session, status="vetoed", msg_id="777", synced_at=NOW - H,
            updated_at=NOW - MIN, note="lieber Khannouss")
    channel = FakeCardChannel()

    assert await _kehren(engine, session, channel, NOW) == 1
    assert channel.edits[0]["msg_id"] == "777"
    assert channel.edits[0]["view"] is None
    fields = {f.name: f.value for f in channel.edits[0]["embed"].fields}
    assert fields["Ergebnis"] == "⏭ Übersprungen (via Zentrale)"
    row = session.get(Intent, 1)
    assert row.discord_card_synced_at == row.updated_at

    assert await _kehren(engine, session, channel, NOW) == 0
    assert len(channel.edits) == 1  # genau einmal finalisiert


@pytest.mark.asyncio
async def test_kehrer_updates_planned_card_and_keeps_buttons(engine, session, kehr_env):
    """Planner-Update (Betrag gewandert): Karte wird editiert, Buttons bleiben."""
    _intent(session, msg_id="778", synced_at=NOW - H, updated_at=NOW - MIN)
    channel = FakeCardChannel()

    assert await _kehren(engine, session, channel, NOW) == 1
    view = channel.edits[0]["view"]
    assert view is not None and len(view.children) == 4


def test_disabled_discord_controls_retire_existing_planned_card(session):
    """Bereits gepostete Steuerkarten verlieren ihre Buttons, ohne das Intent zu vetoen."""
    _intent(session, msg_id="778", synced_at=NOW, updated_at=NOW)

    work = collect_card_retire_work(session, NOW)

    assert len(work) == 1
    assert work[0].msg_id == "778"
    assert work[0].view is None


@pytest.mark.asyncio
async def test_kehrer_404_nulls_msg_id_and_never_reposts(engine, session, kehr_env):
    _intent(session, status="vetoed", msg_id="999", synced_at=NOW - H,
            updated_at=NOW - MIN)
    channel = FakeCardChannel(missing_msg_ids={"999"})

    assert await _kehren(engine, session, channel, NOW) == 1  # 404 verarbeitet
    row = session.get(Intent, 1)
    assert row.discord_msg_id is None
    # Backfill-Schutz (Review 31.08.): synced_at markiert „bewusst ohne Karte" —
    # weder stale- noch Backfill-Kandidat, also nie ein Repost.
    assert row.discord_card_synced_at is not None
    assert stale_cards(session) == []
    assert cards_needing_post(session, NOW) == []
    assert await _kehren(engine, session, channel, NOW) == 0
    assert channel.edits == [] and channel.cards == []


@pytest.mark.asyncio
async def test_kehrer_survives_edit_exception_and_retries_next_tick(
    engine, session, kehr_env
):
    class BrokenEditChannel(FakeCardChannel):
        async def edit_card(self, message_id, *, embed, view):
            raise RuntimeError("discord down")

    _intent(session, status="vetoed", msg_id="777", synced_at=NOW - H,
            updated_at=NOW - MIN)
    assert await _kehren(engine, session, BrokenEditChannel(), NOW) == 0
    row = session.get(Intent, 1)
    # nichts verbrannt: msg_id bleibt, synced_at bleibt alt → nächster Tick versucht erneut
    assert row.discord_msg_id == "777"
    assert wi._as_utc(row.discord_card_synced_at) == NOW - H


@pytest.mark.asyncio
async def test_kehrer_backfills_never_born_card(engine, session, kehr_env):
    """Spec-§6-Nachtrag (Review 31.08.): Eine Karte, die nie entstand (Discord-
    Fehler im Planner-Lauf, Tick vor on_ready), wird nachgepostet — aber erst
    nach CARD_BACKFILL_MIN_AGE, damit der Planner-Post-Pfad das Rennen gewinnt."""
    fresh = _intent(session, pid="FRISCH", updated_at=NOW)
    fresh.created_at = NOW - MIN  # jünger als Mindestalter → Planner-Sache
    old = _intent(session, pid="ALT", updated_at=NOW - H)  # created NOW-H → alt genug
    session.commit()
    channel = FakeCardChannel()

    assert await _kehren(engine, session, channel, NOW) == 1
    assert [c["intent_id"] for c in channel.cards] == [old.id]
    row = session.get(Intent, old.id)
    assert row.discord_msg_id == channel.cards[0]["msg_id"]
    assert row.discord_card_synced_at == row.updated_at
    # zweiter Lauf: Karte existiert und ist synchron — nichts mehr zu tun
    assert await _kehren(engine, session, channel, NOW) == 0
    assert len(channel.cards) == 1


@pytest.mark.asyncio
async def test_kehrer_ignores_synced_and_deliberately_cardless_rows(
    engine, session, kehr_env
):
    _intent(session, pid="SYNCED", status="executed", msg_id="1", updated_at=NOW - H,
            synced_at=NOW - H)
    # synced_at gesetzt, msg_id NULL = 404-Marker „bewusst ohne Karte" → kein Backfill
    _intent(session, pid="GELOESCHT", updated_at=NOW - MIN, synced_at=NOW - MIN)
    assert [r.player_id for r in stale_cards(session)] == []
    assert cards_needing_post(session, NOW) == []
    assert await _kehren(engine, session, FakeCardChannel(), NOW) == 0


# ----------------------------------------------------------- Runner-Integration


def _patch_world(monkeypatch, engine, *, plan=None, raw_market=None):
    async def fake_build_plan(**_kwargs):
        return plan

    client = SimpleNamespace()

    async def _get(path):
        return raw_market or {"it": []}

    client._get = _get

    async def fake_factory():
        return client, SimpleNamespace(user_id="U1", league_id="L1")

    monkeypatch.setattr(wi, "build_plan", fake_build_plan)
    monkeypatch.setattr(wi, "make_kickbase_client", fake_factory)
    monkeypatch.setattr(wi, "SessionLocal", sessionmaker(bind=engine, future=True))
    monkeypatch.setattr(wi, "CARD_POST_SPACING_S", 0)


def _prop(pid="B1", amount=1_200_000):
    return Proposal(
        action=PlannedAction(
            action_type="bid", league_id="L1", player_id=pid, amount=amount,
            rule_id="buy_explosion",
        ),
        rule_id="buy_explosion",
        rationale="r",
        score=0.62,
        requires_approval=False,
        player_name="Kaufmann",
    )


@pytest.mark.asyncio
async def test_run_planner_posts_cards_only_for_created(monkeypatch, engine, env):
    """Erster Lauf: Karte je NEUEM Vorhaben (msg_id in der DB). Zweiter Lauf mit
    geändertem Betrag: Update, aber KEINE zweite Karte (das macht der Kehrer)."""
    env()
    raw = {"it": [
        {"i": "B1", "fn": "Kauf", "n": "Mann", "pos": 4, "mv": 1_150_000,
         "prc": 1_150_000, "exs": 10 * 3600, "tid": "T1", "prob": 2, "ofs": []},
    ]}
    plan = Plan(league_id="L1", budget=20_000_000, buys=[_prop()], sells=[],
                squad=[], user_id="U1", market_candidates=[])
    _patch_world(monkeypatch, engine, plan=plan, raw_market=raw)
    channel = FakeCardChannel()

    assert await run_intent_planner(channel) == "1 neu, 0 aktualisiert, 0 superseded"
    assert len(channel.cards) == 1
    with Session(engine, future=True) as s:
        row = s.execute(select(Intent)).scalars().one()
        assert row.discord_msg_id == channel.cards[0]["msg_id"]
        assert row.discord_card_synced_at == row.updated_at

    plan.buys[0] = _prop(amount=1_300_000)  # zweiter Lauf: nur Update
    summary = await run_intent_planner(channel)
    assert summary.startswith("0 neu, 1 aktualisiert")
    assert len(channel.cards) == 1  # keine zweite Karte für dasselbe Vorhaben


@pytest.mark.asyncio
async def test_run_planner_survives_card_post_failure(monkeypatch, engine, env):
    """Karten-Post crasht komplett → Vorhaben entsteht trotzdem, Planner-Summary
    unverändert (Karte = nice-to-have, Spec §6)."""
    env()
    raw = {"it": [
        {"i": "B1", "fn": "Kauf", "n": "Mann", "pos": 4, "mv": 1_150_000,
         "prc": 1_150_000, "exs": 10 * 3600, "tid": "T1", "prob": 2, "ofs": []},
    ]}
    plan = Plan(league_id="L1", budget=20_000_000, buys=[_prop()], sells=[],
                squad=[], user_id="U1", market_candidates=[])
    _patch_world(monkeypatch, engine, plan=plan, raw_market=raw)
    channel = FakeCardChannel(fail_send_ids={1})

    assert await run_intent_planner(channel) == "1 neu, 0 aktualisiert, 0 superseded"
    with Session(engine, future=True) as s:
        row = s.execute(select(Intent)).scalars().one()
        assert row.status == "planned" and row.discord_msg_id is None


@pytest.mark.asyncio
async def test_run_executor_kehrt_auch_ohne_faellige_vorhaben(monkeypatch, engine, env):
    """L-088: Der Kehrer läuft VOR den frühen Ausstiegen — ein Web-Veto wandert
    auch dann auf die Karte, wenn der Tick sonst nichts zu tun hat ('quiet')."""
    env()
    _patch_world(monkeypatch, engine)
    with Session(engine, future=True) as s:
        # nichts fällig (Zukunft), aber Karte veraltet (Web-Veto liegt vor)
        _intent(s, status="vetoed", execute_at=datetime.now(UTC) + 10 * H,
                msg_id="777", synced_at=datetime.now(UTC) - H,
                updated_at=datetime.now(UTC) - MIN)
    channel = FakeCardChannel()

    assert await run_intent_executor(channel) == "quiet"

    assert [e["msg_id"] for e in channel.edits] == ["777"]
    assert channel.edits[0]["view"] is None
    with Session(engine, future=True) as s:
        row = s.execute(select(Intent)).scalars().one()
        assert row.discord_card_synced_at == row.updated_at


@pytest.mark.asyncio
async def test_run_executor_finalizes_card_in_same_tick(monkeypatch, engine, env):
    """Fälliges Vorhaben wird ausgeführt (hier: unarmed ⇒ failed) und die Karte
    im SELBEN Tick final gezogen (Latenz 0, Spec §4.5)."""
    env(dry="0", intents="0")
    _patch_world(monkeypatch, engine)

    # Executor braucht einen Client mit Preflight-Reads — Minimal-Fake reicht
    # nicht (unarmed ⇒ refused kommt VOR jedem Read). Vorhaben fällig stellen:
    with Session(engine, future=True) as s:
        _intent(s, execute_at=datetime.now(UTC) - MIN, msg_id="555",
                synced_at=datetime.now(UTC) - H, updated_at=datetime.now(UTC) - H)
    channel = FakeCardChannel()

    out = await run_intent_executor(channel)

    assert out == "0 ausgeführt, 1 fehlgeschlagen, 0 abgelaufen"
    assert [e["msg_id"] for e in channel.edits] == ["555"]
    assert channel.edits[0]["view"] is None
    fields = {f.name: f.value for f in channel.edits[0]["embed"].fields}
    assert fields["Ergebnis"].startswith("❌ Fehlgeschlagen: refused")
    with Session(engine, future=True) as s:
        row = s.execute(select(Intent)).scalars().one()
        assert row.status == "failed"
        assert row.discord_card_synced_at == row.updated_at


# ----------------------------------------------------------- handle_intent_action


@pytest.fixture
def wired_db(monkeypatch, engine):
    monkeypatch.setattr(wi, "SessionLocal", sessionmaker(bind=engine, future=True))
    with Session(engine, future=True) as s:
        yield s


@pytest.mark.asyncio
async def test_handle_exec_pulls_execute_at_and_traces_discord(wired_db):
    _intent(wired_db, execute_at=NOW + 5 * H)
    out = await handle_intent_action("exec", 1)
    assert (out.ok, out.verb) == (True, "exec")
    assert out.intent.status == "planned"
    assert "[Discord] Sofort ausführen" in out.intent.emil_note
    wired_db.expire_all()
    assert wi._as_utc(wired_db.get(Intent, 1).execute_at) <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_handle_skip_vetoes_with_trace(wired_db):
    _intent(wired_db)
    out = await handle_intent_action("skip", 1)
    assert out.ok and out.intent.status == "vetoed"
    assert "[Discord] Veto" in out.intent.emil_note


@pytest.mark.asyncio
async def test_handle_p10_returns_amounts_and_ceiling(wired_db):
    _intent(wired_db, amount=4_210_000)
    out = await handle_intent_action("p10", 1)
    assert out.ok is True
    assert (out.old_amount, out.new_amount) == (4_210_000, 4_631_000)
    assert out.ceiling == 6_315_000  # 1,5 × Ursprungsbetrag
    assert out.intent.adjusted_from == 4_210_000


@pytest.mark.asyncio
async def test_handle_p20_over_ceiling_reports_limit(wired_db):
    _intent(wired_db, amount=1_450_000, adjusted_from=1_000_000)  # Grenze 1,5 M
    out = await handle_intent_action("p20", 1)
    assert out.ok is False and out.error == "over_ceiling"
    wired_db.expire_all()
    assert wired_db.get(Intent, 1).amount == 1_450_000  # unverändert


@pytest.mark.asyncio
async def test_handle_click_on_final_intent_returns_status_and_fresh_row(wired_db):
    _intent(wired_db, status="executed", result="executed")
    out = await handle_intent_action("skip", 1)
    assert out.ok is False and out.error == "not_planned"
    assert out.status == "executed"
    assert out.intent is not None and out.intent.status == "executed"


@pytest.mark.asyncio
async def test_handle_unknown_intent_and_unknown_verb(wired_db):
    out = await handle_intent_action("exec", 99)
    assert out.ok is False and out.error == "not_found"
    _intent(wired_db)
    weird = await handle_intent_action("boom", 1)
    assert weird.ok is False and weird.error == "unknown"


@pytest.mark.asyncio
async def test_handle_skip_on_executing_marks_veto_requested(wired_db):
    """Review 31.08.: Ein Veto im executing-Fenster wird VORGEMERKT statt
    verworfen — status bleibt executing, veto_requested_at ist gesetzt, und
    der Executor löst die Vormerkung beim Scheitern/Retry als vetoed ein."""
    _intent(wired_db, status="executing")
    out = await handle_intent_action("skip", 1)
    assert out.ok is True and out.status == "executing"
    wired_db.expire_all()
    row = wired_db.get(Intent, 1)
    assert row.status == "executing"
    assert row.veto_requested_at is not None
