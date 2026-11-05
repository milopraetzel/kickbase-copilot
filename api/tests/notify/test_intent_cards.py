"""Discord-Veto-Karten (notify/intent_cards.py, Spec 2026-08-31 §7).

custom_id-Template (gültig/fremd/kaputt), View-Bau je kind, Embed-Formatierung
(big, Endzustände, Clipping), Autorisierung fail-closed, Outcome-Antworttexte,
plus DiscordChannel.send_card/edit_card (404 ⇒ False). Fake-Muster aus
tests/notify/test_discord_channel.py.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from kickbase_agent.db.models import Intent
from kickbase_agent.notify import intent_cards
from kickbase_agent.notify.discord_channel import DiscordChannel, parse_custom_id
from kickbase_agent.notify.intent_cards import (
    IntentActionOutcome,
    IntentButton,
    build_intent_view,
    format_intent_embed,
    intent_custom_id,
    outcome_reply,
)

NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)  # Berlin 12:00


@pytest.fixture(autouse=True)
def _reset_config():
    """Modul-Konfiguration je Test zurücksetzen (fail-closed Default)."""
    intent_cards.configure(allowed_user_id="", on_intent_action=None)
    yield
    intent_cards.configure(allowed_user_id="", on_intent_action=None)


def _intent(**over) -> Intent:
    base = dict(
        id=17,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
        kind="bid",
        player_id="7291",
        player_name="Openda",
        team_id="43",  # Leipzig
        position=4,
        amount=4_213_337,
        rationale="Kauf-Score 0.68 · LI: Startelf",
        sources={"score": 0.68},
        listing_type="kickbase",
        deadline=NOW + timedelta(hours=2),
        execute_at=NOW + timedelta(hours=1, minutes=52),
        status="planned",
    )
    base.update(over)
    return Intent(**base)


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []
        self.deferred = False

    async def send_message(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))

    async def defer(self, ephemeral=False):
        # Review 31.08.: Der Klick deferred sofort (3-s-Fenster), die echte
        # Antwort kommt als Followup.
        self.deferred = True


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))


class FakeMessage:
    def __init__(self) -> None:
        self.id = 777
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


def _interaction(user_id=111, message: FakeMessage | None = None):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=FakeResponse(),
        followup=FakeFollowup(),
        message=message,
    )


def _handler(outcome: IntentActionOutcome):
    async def on_action(verb: str, intent_id: int) -> IntentActionOutcome:
        on_action.calls.append((verb, intent_id))
        return outcome

    on_action.calls = []
    return on_action


# ------------------------------------------------------------- custom_id


def test_custom_id_builder_and_template_roundtrip():
    tpl = IntentButton.__discord_ui_compiled_template__
    for verb in ("exec", "skip", "p10", "p20"):
        cid = intent_custom_id(verb, 17)
        assert cid == f"kba:i:{verb}:17"
        m = tpl.match(cid)
        assert m is not None and m["verb"] == verb and m["id"] == "17"


@pytest.mark.parametrize(
    "junk",
    [
        "",
        "kba:approve:17",  # Approval-Welt — anderes Schema
        "kba:i:exec",  # zu kurz
        "kba:i:destroy:17",  # unbekanntes Verb
        "kba:i:exec:abc",  # keine Zahl
        "kba:i:exec:17:extra",  # zu lang
        "other:i:exec:17",  # fremder Namespace
    ],
)
def test_template_rejects_foreign_and_broken_ids(junk):
    assert IntentButton.__discord_ui_compiled_template__.match(junk) is None


def test_old_approval_parser_ignores_intent_ids():
    """Vier Segmente — der alte Parser (3 Segmente) bleibt unberührt (Spec §4.1)."""
    assert parse_custom_id("kba:i:exec:17") is None
    assert parse_custom_id("kba:approve:17") == ("approve", 17)


@pytest.mark.asyncio
async def test_from_custom_id_builds_button_from_match():
    m = IntentButton.__discord_ui_compiled_template__.match("kba:i:p10:42")
    btn = await IntentButton.from_custom_id(SimpleNamespace(), SimpleNamespace(), m)
    assert (btn.verb, btn.intent_id) == ("p10", 42)
    assert btn.custom_id == "kba:i:p10:42"


# ------------------------------------------------------------- View-Bau


def test_view_for_bid_and_list_has_all_four_buttons():
    for kind in ("bid", "list"):
        view = build_intent_view(_intent(kind=kind))
        assert view.timeout is None
        assert [item.custom_id for item in view.children] == [
            "kba:i:exec:17", "kba:i:p10:17", "kba:i:p20:17", "kba:i:skip:17",
        ]
        assert all(item.is_persistent() for item in view.children)


def test_view_for_sell_and_accept_has_only_exec_and_skip():
    for kind in ("sell", "accept"):
        view = build_intent_view(_intent(kind=kind))
        assert [item.custom_id for item in view.children] == [
            "kba:i:exec:17", "kba:i:skip:17",
        ]


# ------------------------------------------------------------- Embed


def _fields(embed: discord.Embed) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


def test_embed_planned_shows_amount_times_and_rationale():
    embed = format_intent_embed(_intent(), NOW)
    assert embed.title == "🟢 Vorhaben #17 · Gebot"
    assert embed.color.value == 0x00E38C
    assert "Openda" in embed.description and "ST" in embed.description
    assert "Leipzig" in embed.description
    f = _fields(embed)
    assert f["Betrag"] == "4.213.337 €"
    assert f["Ausführung"] == "13:52 Uhr (in 1 h 52 min)"
    assert f["Deadline"] == "14:00 Uhr"
    assert f["Warum"] == "Kauf-Score 0.68 · LI: Startelf"


def test_embed_planned_due_now_says_sofort_and_skips_missing_deadline():
    embed = format_intent_embed(
        _intent(execute_at=NOW - timedelta(minutes=1), deadline=None), NOW
    )
    f = _fields(embed)
    assert f["Ausführung"] == "sofort"
    assert "Deadline" not in f


def test_embed_big_amount_is_orange_with_warning_line():
    embed = format_intent_embed(_intent(amount=6_000_000), NOW)
    assert embed.title.startswith("🟠 ")
    assert embed.color.value == 0xE67E22
    assert "⚠ Großes Vorhaben — Veto-Fenster 2 h" in embed.description


def test_embed_clips_rationale_to_1024():
    embed = format_intent_embed(_intent(rationale="x" * 3000), NOW)
    warum = _fields(embed)["Warum"]
    assert len(warum) <= 1024 and warum.endswith("…")


@pytest.mark.parametrize(
    ("status", "result", "note", "expect"),
    [
        ("executed", "executed", None, "✅ Ausgeführt · executed"),
        ("vetoed", None, "[Discord] Veto 14:32", "⏭ Übersprungen (via Discord)"),
        ("vetoed", None, "lieber Khannouss", "⏭ Übersprungen (via Zentrale)"),
        (
            "failed",
            "refused: AGENT_AUTO_INTENTS=0",
            None,
            "❌ Fehlgeschlagen: refused: AGENT_AUTO_INTENTS=0",
        ),
        ("expired", "Deadline überschritten, nicht ausgeführt", None,
         "⏰ Abgelaufen (Deadline überschritten)"),
        ("superseded", "nicht mehr am Markt", None, "↺ Ersetzt: nicht mehr am Markt"),
    ],
)
def test_embed_final_states_show_honest_result_without_time_fields(
    status, result, note, expect
):
    embed = format_intent_embed(
        _intent(status=status, result=result, emil_note=note), NOW
    )
    f = _fields(embed)
    assert f["Ergebnis"] == expect
    assert "Ausführung" not in f and "Deadline" not in f


# ------------------------------------------------------------- Autorisierung


@pytest.mark.asyncio
async def test_missing_user_id_rejects_everyone_fail_closed(caplog):
    caplog.set_level(logging.WARNING, logger="kickbase_agent.notify.intent_cards")
    handler = _handler(IntentActionOutcome(verb="exec", intent_id=5, ok=True))
    intent_cards.configure(allowed_user_id="", on_intent_action=handler)
    btn = IntentButton("exec", 5)

    inter = _interaction(user_id=42)
    await btn.callback(inter)
    await btn.callback(_interaction(user_id=42))  # Warnung nur einmal loggen

    assert handler.calls == []
    content, ephemeral = inter.response.messages[0]
    assert "gesperrt" in content and ephemeral is True
    warnings = [r for r in caplog.records if "DISCORD_USER_ID" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_wrong_user_is_rejected():
    handler = _handler(IntentActionOutcome(verb="exec", intent_id=5, ok=True))
    intent_cards.configure(allowed_user_id="111", on_intent_action=handler)
    inter = _interaction(user_id=222)

    await IntentButton("exec", 5).callback(inter)

    assert handler.calls == []
    assert inter.response.messages[0] == ("Nicht autorisiert.", True)


@pytest.mark.asyncio
async def test_missing_handler_answers_ephemeral_without_crash():
    intent_cards.configure(allowed_user_id="111", on_intent_action=None)
    inter = _interaction(user_id=111)
    await IntentButton("skip", 9).callback(inter)
    content, ephemeral = inter.response.messages[0]
    assert "nicht verdrahtet" in content and ephemeral is True


# ------------------------------------------------------------- Klick-Fluss


@pytest.mark.asyncio
async def test_click_dispatches_verb_and_id_and_updates_card():
    """Autorisierter Klick: Handler bekommt (verb, id) aus der custom_id, die
    ephemere Antwort kommt aus outcome_reply, die Karte wird final (view=None)."""
    row = _intent(status="vetoed", emil_note="[Discord] Veto 14:32")
    handler = _handler(IntentActionOutcome(verb="skip", intent_id=17, ok=True, intent=row))
    intent_cards.configure(allowed_user_id="111", on_intent_action=handler)
    message = FakeMessage()
    inter = _interaction(user_id=111, message=message)

    await IntentButton("skip", 17).callback(inter)

    assert handler.calls == [("skip", 17)]
    assert inter.response.deferred is True
    content, ephemeral = inter.followup.messages[0]
    assert content == "⏭ Übersprungen — der Planner legt es heute nicht neu an."
    assert ephemeral is True
    assert len(message.edits) == 1
    assert message.edits[0]["view"] is None
    edited = _fields(message.edits[0]["embed"])
    assert edited["Ergebnis"] == "⏭ Übersprungen (via Discord)"


@pytest.mark.asyncio
async def test_click_on_planned_intent_keeps_buttons_on_card():
    row = _intent(execute_at=NOW - timedelta(seconds=1))  # nach exec: sofort fällig
    handler = _handler(IntentActionOutcome(verb="exec", intent_id=17, ok=True, intent=row))
    intent_cards.configure(allowed_user_id="111", on_intent_action=handler)
    message = FakeMessage()

    await IntentButton("exec", 17).callback(_interaction(user_id=111, message=message))

    view = message.edits[0]["view"]
    assert view is not None and len(view.children) == 4


@pytest.mark.asyncio
async def test_card_edit_failure_never_breaks_the_click(caplog):
    caplog.set_level(logging.WARNING, logger="kickbase_agent.notify.intent_cards")

    class BrokenMessage(FakeMessage):
        async def edit(self, **kwargs):
            raise RuntimeError("discord down")

    row = _intent(status="executed", result="executed")
    handler = _handler(IntentActionOutcome(verb="exec", intent_id=17, ok=True, intent=row))
    intent_cards.configure(allowed_user_id="111", on_intent_action=handler)
    inter = _interaction(user_id=111, message=BrokenMessage())

    await IntentButton("exec", 17).callback(inter)  # keine Exception nach außen

    assert inter.followup.messages  # ephemere Antwort kam trotzdem
    assert any("Kehrer" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------- Outcome-Texte


def test_outcome_reply_texts():
    ok = IntentActionOutcome(verb="p10", intent_id=1, ok=True,
                             old_amount=4_210_000, new_amount=4_630_000, ceiling=6_320_000)
    assert outcome_reply(ok) == "Betrag: 4,21 M → 4,63 M (Grenze 6,32 M)."
    assert outcome_reply(
        IntentActionOutcome(verb="exec", intent_id=1, ok=True)
    ) == "▶ Wird beim nächsten Tick (≤ 5 min) ausgeführt."
    # Review 31.08.: unarmed sagt die Antwort ehrlich, statt Ausführung zu versprechen.
    assert "AGENT_AUTO_INTENTS ist aus" in outcome_reply(
        IntentActionOutcome(verb="exec", intent_id=1, ok=True, armed=False)
    )
    # Review 31.08.: Veto im executing-Fenster wird vorgemerkt, nicht verworfen.
    assert "VORGEMERKT" in outcome_reply(
        IntentActionOutcome(verb="skip", intent_id=1, ok=True, status="executing")
    )
    assert outcome_reply(
        IntentActionOutcome(verb="skip", intent_id=1, ok=True)
    ) == "⏭ Übersprungen — der Planner legt es heute nicht neu an."
    assert outcome_reply(
        IntentActionOutcome(verb="skip", intent_id=1, ok=False, error="not_planned",
                            status="executed")
    ) == "Vorhaben ist bereits ‚ausgeführt' — nichts geändert."
    assert outcome_reply(
        IntentActionOutcome(verb="p20", intent_id=1, ok=False, error="over_ceiling",
                            detail="egal")
    ) == (
        "Mehr als 1,5 × Ursprungsbetrag geht nirgends — für mehr in der "
        "Zentrale ein NEUES Vorhaben anlegen."
    )
    assert outcome_reply(
        IntentActionOutcome(verb="exec", intent_id=7, ok=False, error="not_found")
    ) == "Vorhaben 7 nicht gefunden."
    assert outcome_reply(
        IntentActionOutcome(verb="p10", intent_id=1, ok=False, error="adjust",
                            detail="Vorhaben 1 wurde gerade geändert …")
    ).startswith("Vorhaben 1 wurde gerade geändert")


# ------------------------------------------- DiscordChannel: send_card/edit_card


class FakeCardMessageable:
    def __init__(self, *, missing=False):
        self.sent: list[dict] = []
        self.message = FakeMessage()
        self.missing = missing

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})
        return self.message

    def get_partial_message(self, message_id: int):
        # Review 31.08.: edit_card nutzt PartialMessage (EIN REST-Call).
        self.fetched = message_id
        outer = self

        class _Partial:
            async def edit(self, **kwargs):
                if outer.missing:
                    raise discord.NotFound(
                        SimpleNamespace(status=404, reason="Not Found"), "Unknown Message"
                    )
                await outer.message.edit(**kwargs)

        return _Partial()


class _Bot:
    def __init__(self, messageable):
        self.messageable = messageable

    def get_channel(self, _id):
        return self.messageable


def _card_channel(messageable):
    async def _noop(action_id):
        return SimpleNamespace(status="ok")

    return DiscordChannel(
        bot=_Bot(messageable), channel_id="42", allowed_user_id="111",
        on_approve=_noop, on_reject=_noop,
    )


@pytest.mark.asyncio
async def test_send_card_returns_message_id_and_sends_embed_with_view():
    messageable = FakeCardMessageable()
    ch = _card_channel(messageable)
    embed = format_intent_embed(_intent(), NOW)
    view = build_intent_view(_intent())

    msg_id = await ch.send_card(embed=embed, view=view)

    assert msg_id == "777"
    assert messageable.sent[0]["embed"] is embed
    assert messageable.sent[0]["view"] is view


@pytest.mark.asyncio
async def test_send_card_without_channel_returns_none():
    ch = _card_channel(None)
    ch._bot.get_channel = lambda _id: None  # Bot kennt den Kanal (noch) nicht
    assert await ch.send_card(
        embed=format_intent_embed(_intent(), NOW), view=build_intent_view(_intent())
    ) is None


@pytest.mark.asyncio
async def test_edit_card_edits_message_and_reports_404_as_false():
    messageable = FakeCardMessageable()
    ch = _card_channel(messageable)
    embed = format_intent_embed(_intent(status="executed", result="executed"), NOW)

    ok = await ch.edit_card("777", embed=embed, view=None)
    assert ok is True
    assert messageable.fetched == 777
    assert messageable.message.edits[0] == {"embed": embed, "view": None}

    gone = _card_channel(FakeCardMessageable(missing=True))
    assert await gone.edit_card("777", embed=embed, view=None) is False
