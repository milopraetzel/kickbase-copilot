"""DiscordChannel: fail-closed Freigaben + restart-sichere Approval-Buttons.

Fail-closed: ohne DISCORD_USER_ID darf NIEMAND klicken. Restart-sicher
(Review-Fund): Buttons tragen stabile custom_ids (kba:approve/reject:{id}),
Views sind persistent (timeout=None) und werden beim Bot-Start für noch gültige
'proposed'-Vorschläge neu registriert — das Handling zieht die action_id aus der
custom_id, nicht aus einer Closure. Unten: Berichte-Ablage der Info-/Briefing-Posts.
"""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from kickbase_agent.db.models import Base, Report
from kickbase_agent.notify import report_store
from kickbase_agent.notify.discord_channel import (
    DiscordChannel,
    approve_custom_id,
    parse_custom_id,
    reject_custom_id,
)


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []
        self.edits: list[dict] = []

    async def send_message(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)


class FakeBot:
    """get_channel + add_view — mehr braucht der Channel nicht."""

    def __init__(self) -> None:
        self.registered_views: list = []

    def get_channel(self, _id):
        return None

    def add_view(self, view):
        self.registered_views.append(view)


def _interaction(user_id=111, custom_id: str | None = None):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=FakeResponse(),
        data={"custom_id": custom_id} if custom_id is not None else {},
    )


def _channel(allowed=""):
    async def approve(action_id):
        approve.calls.append(action_id)
        return SimpleNamespace(status="executed", message="")

    approve.calls = []

    async def reject(action_id):
        reject.calls.append(action_id)
        return SimpleNamespace(status="rejected", message="")

    reject.calls = []

    bot = FakeBot()
    ch = DiscordChannel(
        bot=bot,
        channel_id="",
        allowed_user_id=allowed,
        on_approve=approve,
        on_reject=reject,
    )
    ch._fake_bot = bot  # für Registrierungs-Asserts
    return ch, approve, reject


# ------------------------------------------------------------------ fail-closed


@pytest.mark.asyncio
async def test_missing_user_id_rejects_everyone_fail_closed():
    """P0-Fund: leere DISCORD_USER_ID ließ vorher JEDEN freigeben — jetzt niemand."""
    ch, _, _ = _channel(allowed="")
    inter = _interaction(user_id=42)

    assert await ch._authorized(inter) is False
    content, ephemeral = inter.response.messages[0]
    assert "Freigaben gesperrt" in content
    assert ephemeral is True


@pytest.mark.asyncio
async def test_missing_user_id_blocks_approve_button_callback():
    """Auch über den echten Button-Callback: kein on_approve, keine Ausführung."""
    ch, approve, _ = _channel(allowed="")
    view = ch._build_view(7)
    inter = _interaction(user_id=42, custom_id=approve_custom_id(7))

    await view.children[0].callback(inter)  # Annehmen-Button

    assert approve.calls == []  # kein Dispatch
    assert inter.response.edits == []  # Nachricht bleibt mit Buttons stehen
    assert "Freigaben gesperrt" in inter.response.messages[0][0]


@pytest.mark.asyncio
async def test_missing_user_id_blocks_reject_button_callback():
    ch, _, reject = _channel(allowed="")
    view = ch._build_view(7)
    inter = _interaction(user_id=42, custom_id=reject_custom_id(7))

    await view.children[1].callback(inter)  # Ablehnen-Button

    assert reject.calls == []


@pytest.mark.asyncio
async def test_wrong_user_is_rejected():
    ch, approve, _ = _channel(allowed="111")
    inter = _interaction(user_id=222, custom_id=approve_custom_id(9))

    assert await ch._authorized(inter) is False
    assert "Nicht autorisiert" in inter.response.messages[0][0]


@pytest.mark.asyncio
async def test_matching_user_is_authorized():
    ch, approve, _ = _channel(allowed="111")
    view = ch._build_view(9)
    inter = _interaction(user_id=111, custom_id=approve_custom_id(9))

    await view.children[0].callback(inter)

    assert approve.calls == [9]
    assert inter.response.edits  # Nachricht wurde aufgelöst


@pytest.mark.asyncio
async def test_missing_user_id_warns_only_once(caplog):
    caplog.set_level(logging.WARNING, logger="kickbase_agent.notify.discord_channel")
    ch, _, _ = _channel(allowed="")

    await ch._authorized(_interaction())
    await ch._authorized(_interaction())

    warnings = [r for r in caplog.records if "DISCORD_USER_ID" in r.getMessage()]
    assert len(warnings) == 1


# ------------------------------------------------- custom_id: Format + Parsing


def test_custom_id_format_is_stable():
    """Stabiles Schema — daran hängen die nach Neustart registrierten Views."""
    assert approve_custom_id(17) == "kba:approve:17"
    assert reject_custom_id(17) == "kba:reject:17"


def test_parse_custom_id_roundtrip_and_garbage():
    assert parse_custom_id("kba:approve:17") == ("approve", 17)
    assert parse_custom_id("kba:reject:3") == ("reject", 3)
    for junk in ("", "kba:approve", "other:approve:1", "kba:destroy:1",
                 "kba:approve:abc", "kba:approve:1:extra"):
        assert parse_custom_id(junk) is None


def test_build_view_is_persistent_with_stable_custom_ids():
    """timeout=None + custom_id je Button ⇒ von bot.add_view akzeptierbar."""
    ch, _, _ = _channel(allowed="111")
    view = ch._build_view(23)

    assert view.timeout is None
    assert view.is_persistent()
    assert [item.custom_id for item in view.children] == [
        "kba:approve:23", "kba:reject:23",
    ]


# ------------------------------------------- Neustart: persistente Registrierung


def test_register_persistent_views_adds_one_view_per_action():
    ch, _, _ = _channel(allowed="111")

    count = ch.register_persistent_views([5, 8])

    assert count == 2
    views = ch._fake_bot.registered_views
    assert len(views) == 2
    assert all(v.is_persistent() for v in views)
    assert [v.children[0].custom_id for v in views] == ["kba:approve:5", "kba:approve:8"]


@pytest.mark.asyncio
async def test_restored_view_routes_click_to_approve_with_id_from_custom_id():
    """Neustart-Szenario: FRISCHER Channel (keine Closure vom Senden), View wird
    aus der DB-ID re-registriert — ein Klick landet mit der RICHTIGEN action_id
    im on_approve, extrahiert aus der custom_id."""
    ch, approve, _ = _channel(allowed="111")
    ch.register_persistent_views([42])
    restored = ch._fake_bot.registered_views[0]
    inter = _interaction(user_id=111, custom_id=approve_custom_id(42))

    await restored.children[0].callback(inter)

    assert approve.calls == [42]
    assert "✅ Ausgeführt (#42)" in inter.response.edits[0]["content"]


@pytest.mark.asyncio
async def test_restored_view_routes_reject_too():
    ch, _, reject = _channel(allowed="111")
    ch.register_persistent_views([42])
    restored = ch._fake_bot.registered_views[0]
    inter = _interaction(user_id=111, custom_id=reject_custom_id(42))

    await restored.children[1].callback(inter)

    assert reject.calls == [42]
    assert "❌ Abgelehnt (#42)" in inter.response.edits[0]["content"]


@pytest.mark.asyncio
async def test_unparseable_custom_id_answers_ephemeral_and_calls_nothing():
    """Kaputte/fremde custom_id: keine Ausführung, ephemerer Hinweis."""
    ch, approve, reject = _channel(allowed="111")
    view = ch._build_view(9)
    inter = _interaction(user_id=111, custom_id="other:thing:1")

    await view.children[0].callback(inter)

    assert approve.calls == [] and reject.calls == []
    content, ephemeral = inter.response.messages[0]
    assert "Unbekannte Interaktion" in content and ephemeral is True
    assert inter.response.edits == []


# ------------------------------------------------- Berichte-Ablage (reports)
# Jeder Info-/Briefing-Post landet NACH dem Versand als Volltext in `reports`
# (source worker:<kind>, meta.delivered) — ohne den Versand je zu stören.

LONG_TAIL = "\n" + "• Zeile mit Inhalt\n" * 12  # macht jeden Text „substanziell" (> 120)


class FakeMessageable:
    """discord-Channel-Ersatz: protokolliert send(); optional mit Fehler."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send(self, content=None, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.sent.append({"content": content, **kwargs})


class BotWithChannel(FakeBot):
    def __init__(self, messageable) -> None:
        super().__init__()
        self.messageable = messageable

    def get_channel(self, _id):
        return self.messageable


@pytest.fixture
def reports_db(monkeypatch):
    """In-Memory-`reports` hinter report_store.SessionLocal; liefert einen Leser."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(report_store, "SessionLocal", sessionmaker(bind=engine, future=True))

    def rows() -> list[Report]:
        with Session(engine, future=True) as s:
            return list(s.execute(select(Report).order_by(Report.id)).scalars())

    return rows


def _live_channel(messageable):
    async def approve(action_id):
        return SimpleNamespace(status="executed", message="")

    async def reject(action_id):
        return SimpleNamespace(status="rejected", message="")

    return DiscordChannel(
        bot=BotWithChannel(messageable), channel_id="42", allowed_user_id="111",
        on_approve=approve, on_reject=reject,
    )


@pytest.mark.asyncio
async def test_send_info_stores_report_after_delivery_with_inferred_kind(reports_db):
    ch = _live_channel(FakeMessageable())
    text = "🤖 **Schatten-Scan** — was der Automat (Phase 2) heute getan HÄTTE" + LONG_TAIL

    await ch.send_info(text)

    rows = reports_db()
    assert len(rows) == 1
    r = rows[0]
    assert r.kind == "schatten" and r.source == "worker:schatten"
    assert r.title == "Schatten-Scan — was der Automat (Phase 2) heute getan HÄTTE"
    assert r.body == text.strip()
    assert r.meta == {"delivered": True, "chars": len(text.strip())}


@pytest.mark.asyncio
async def test_send_info_explicit_kind_and_title_win_and_store_even_short_texts(reports_db):
    ch = _live_channel(FakeMessageable())

    await ch.send_info("**Steuerzentrale** — Executor: 1 ausgeführt", kind="executor")
    await ch.send_info("Irgendein Text" + LONG_TAIL, kind="bid_watch", title="Mein Titel")

    rows = reports_db()
    assert [(r.kind, r.source) for r in rows] == [
        ("executor", "worker:executor"), ("bid_watch", "worker:bid_watch"),
    ]
    assert rows[0].title == "Steuerzentrale — Executor: 1 ausgeführt"  # kurz, aber explizit
    assert rows[1].title == "Mein Titel"


@pytest.mark.asyncio
async def test_send_info_skips_short_infos_and_error_notices_without_kind(reports_db):
    ch = _live_channel(FakeMessageable())

    await ch.send_info("🎁 Prämie eingesammelt ✓")
    await ch.send_info("⚠️ Briefing-Fehler: ValueError: kaputt " + "x" * 200)
    await ch.send_info("   ")

    assert reports_db() == []
    assert len(ch._channel().sent) == 3  # zugestellt wurde trotzdem alles


@pytest.mark.asyncio
async def test_send_info_unknown_kind_is_stored_as_sonstiges(reports_db):
    ch = _live_channel(FakeMessageable())
    await ch.send_info("Text", kind="komisch")
    (r,) = reports_db()
    assert r.kind == "sonstiges" and r.source == "worker:sonstiges"


@pytest.mark.asyncio
async def test_send_info_stores_undelivered_when_channel_missing_or_send_fails(reports_db):
    # Bot kennt den Kanal (noch) nicht → nichts gesendet, Bericht mit delivered=false
    ch_missing, _, _ = _channel(allowed="111")
    await ch_missing.send_info("🧠 **KI-Recherche** — Tipps" + LONG_TAIL)

    # Discord wirft beim Senden → Exception propagiert, Bericht trotzdem abgelegt
    boom = RuntimeError("discord down")
    ch_fail = _live_channel(FakeMessageable(fail=boom))
    with pytest.raises(RuntimeError):
        await ch_fail.send_info("🤖 Auto-Trading (Schatten) — hätte ausgeführt" + LONG_TAIL)

    rows = reports_db()
    assert [(r.kind, r.meta["delivered"]) for r in rows] == [
        ("recherche", False), ("auto_trading", False),
    ]


@pytest.mark.asyncio
async def test_send_info_survives_missing_reports_table(monkeypatch, caplog):
    """Kein Alembic-Upgrade: Versand klappt, Ablage loggt nur (keine Exception)."""
    engine = create_engine("sqlite:///:memory:", future=True)  # ohne Tabellen
    monkeypatch.setattr(report_store, "SessionLocal", sessionmaker(bind=engine, future=True))
    caplog.set_level(logging.WARNING, logger="kickbase_agent.notify.report_store")
    messageable = FakeMessageable()
    ch = _live_channel(messageable)

    await ch.send_info("🧠 **KI-Recherche** — Tipps" + LONG_TAIL)

    assert len(messageable.sent) == 1
    assert any("konnte nicht gespeichert werden" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_send_briefing_stores_embed_body_with_title_and_footer(reports_db):
    messageable = FakeMessageable()
    ch = _live_channel(messageable)

    await ch.send_briefing(
        "📊 BASE · Tagesbriefing", "**`3/8`** Platz …\nKäufe", footer="Kickbase-Agent · 22:15",
        kind="briefing",
    )

    assert "embed" in messageable.sent[0]
    (r,) = reports_db()
    assert r.kind == "briefing" and r.source == "worker:briefing"
    assert r.title == "BASE · Tagesbriefing"
    assert r.body == "**`3/8`** Platz …\nKäufe"
    assert r.meta == {
        "delivered": True, "chars": len(r.body), "embed": True, "footer": "Kickbase-Agent · 22:15",
    }


@pytest.mark.asyncio
async def test_send_briefing_without_kind_infers_from_title(reports_db):
    ch = _live_channel(FakeMessageable())
    await ch.send_briefing("📊 BASE · Tagesbriefing", "kurz")
    (r,) = reports_db()
    assert r.kind == "briefing" and r.body == "kurz"


def _digest_channel(messageable):
    """Wie _live_channel, aber im Digest-Modus (Produktionsdefault)."""
    async def approve(action_id):
        return SimpleNamespace(status="executed", message="")

    async def reject(action_id):
        return SimpleNamespace(status="rejected", message="")

    return DiscordChannel(
        bot=BotWithChannel(messageable), channel_id="42", allowed_user_id="111",
        on_approve=approve, on_reject=reject, digest_only=True,
    )


@pytest.mark.asyncio
async def test_digest_archives_morgenbericht_and_other_kinds_only_blocker_passes(reports_db):
    """Emil 23.09.2026 („nur einmal am Tag eine Zusammenfassung"): Die Art
    `morgenbericht` (22.09. noch durchgelassen) wird nur archiviert; den Kanal
    erreicht über send_info allein ein Blocker."""
    box = FakeMessageable()
    ch = _digest_channel(box)

    await ch.send_info("Morgenbericht Di 22.09." + LONG_TAIL, kind="morgenbericht")
    await ch.send_info("Steiger-Radar" + LONG_TAIL, kind="recherche")
    await ch.send_info("Blocker" + LONG_TAIL, kind="blocker")

    assert [m["content"][:7] for m in box.sent] == ["Blocker"]
    rows = reports_db()
    assert [(r.kind, r.meta["delivered"]) for r in rows] == [
        ("morgenbericht", False), ("recherche", False), ("blocker", True),
    ]


# Donnerstag 24.09.2026 08:15:30 bzw. 22:15:30 Berlin (CEST).
_MORNING_SLOT = datetime(2026, 9, 24, 6, 15, 30, tzinfo=UTC)
_EVENING_SLOT = datetime(2026, 9, 24, 20, 15, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_digest_delivers_only_the_morning_slot_and_only_once(reports_db):
    """Die eine Nachricht des Tages: Morgen-Slot einmal (Claim), Abend nur Archiv."""
    box = FakeMessageable()
    ch = _digest_channel(box)

    await ch.send_briefing("Tageszusammenfassung", "Inhalt", delivery_slot="morning",
                           now=_MORNING_SLOT)
    await ch.send_briefing("Tageszusammenfassung", "Inhalt", delivery_slot="morning",
                           now=_MORNING_SLOT + timedelta(minutes=1))
    await ch.send_briefing("Abendbericht", "Inhalt", delivery_slot="evening",
                           now=_EVENING_SLOT)

    assert len(box.sent) == 1 and box.sent[0]["embed"].title == "Tageszusammenfassung"
    rows = reports_db()
    assert [(r.title, r.meta["delivered"]) for r in rows] == [
        ("Tageszusammenfassung", True), ("Tageszusammenfassung", False),
        ("Abendbericht", False),
    ]
    assert rows[2].meta["suppressed_delivery_slot"] == "2026-09-24:evening"
