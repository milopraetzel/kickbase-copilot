"""Discord-Veto-Karten für Vorhaben (Spec 2026-08-31-discord-veto-buttons §3/§4.1).

Jedes vom Planner NEU angelegte Vorhaben erscheint als Embed-Karte mit Buttons
(▶ Jetzt ausführen · +10 % · +20 % · Überspringen); die Buttons mutieren über
den injizierten Callback ausschließlich `intents`-Zeilen — nie Kickbase, das
bleibt beim Intent-Executor → ActionExecutor.

custom_id-Schema: `kba:i:<verb>:<intent_id>` mit verb ∈ exec|skip|p10|p20
(VIER Segmente — der alte Approval-Parser `parse_custom_id` lehnt ≠ 3 Segmente
ab und bleibt unberührt; keine Kollision mit `kba:approve:*`). EIN
`discord.ui.DynamicItem`-Regex-Handler matcht alle Karten, auch die von vor
einem Neustart — das Reload-Muster der Approval-Welt entfällt für Intents.

Das Modul bleibt DB-frei (Muster `DiscordChannel`): der Worker injiziert per
`configure(...)` einen Callback `on_intent_action(verb, intent_id)` und die
`allowed_user_id`. Fail-closed wie in notify/discord_channel.py: ohne
konfigurierte DISCORD_USER_ID wird ausnahmslos abgelehnt.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import discord

from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.intents.actions import ADJUSTABLE_KINDS, INTENT_STATUS_DE
from kickbase_agent.mcp._util import fmt_money
from kickbase_agent.scouts.ligainsider.teams import TEAMS

_log = logging.getLogger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

#: Ab hier gilt ein Vorhaben als „groß": orange Karte + Warnzeile (Spec §3).
BIG_AMOUNT = DEFAULT_GUARDRAILS.approval_threshold

#: Embed-Feld-Limit von Discord — rationale wird darauf geclippt (Spec §6).
RATIONALE_LIMIT = 1024

#: custom_id-Template (Spec §4.1) — einmal registriert via bot.add_dynamic_items.
CUSTOM_ID_TEMPLATE = r"^kba:i:(?P<verb>exec|skip|p10|p20):(?P<id>[0-9]+)$"

_KIND_LABEL = {
    "bid": "Gebot",
    "list": "Listing",
    "sell": "Sofortverkauf",
    "accept": "Annahme",
    "lineup": "Aufstellung",
}
_POS = {1: "TW", 2: "ABW", 3: "MF", 4: "ST"}

#: Label, Stil und Zeile je Button-Verb (Reihen wie im Spec-Mockup).
_BUTTONS: dict[str, tuple[str, discord.ButtonStyle, int]] = {
    "exec": ("▶ Jetzt ausführen", discord.ButtonStyle.primary, 0),
    "p10": ("+10 %", discord.ButtonStyle.secondary, 0),
    "p20": ("+20 %", discord.ButtonStyle.secondary, 0),
    "skip": ("Überspringen", discord.ButtonStyle.danger, 1),
}

_COLOR_PLANNED = 0x00E38C
_COLOR_BIG = 0xE67E22
_COLOR_EXECUTED = 0x5E8D6E  # grau-grün (Spec §3: Endzustand „Ausgeführt")
_COLOR_FAILED = 0xE74C3C
_COLOR_NEUTRAL = 0x95A5A6  # vetoed/expired/superseded/executing

_STATUS_EMOJI = {
    "executed": "✅",
    "vetoed": "⏭",
    "failed": "❌",
    "expired": "⏰",
    "superseded": "↺",
    "executing": "⏳",
}


def intent_custom_id(verb: str, intent_id: int) -> str:
    return f"kba:i:{verb}:{intent_id}"


# ------------------------------------------------------------------ Injektion

#: Callback des Workers: (verb, intent_id) → Ergebnis der Zustandsänderung.
OnIntentAction = Callable[[str, int], Awaitable["IntentActionOutcome"]]


@dataclass
class _CardsConfig:
    allowed_user_id: str = ""
    on_intent_action: OnIntentAction | None = None
    # Fail-closed-Warnung nur einmal loggen (nicht bei jedem Klick spammen).
    warned_missing_user_id: bool = False


_config = _CardsConfig()


def configure(*, allowed_user_id: str, on_intent_action: OnIntentAction | None) -> None:
    """Der Worker injiziert Emils User-ID + den Aktions-Callback (Modul bleibt DB-frei)."""
    _config.allowed_user_id = str(allowed_user_id or "")
    _config.on_intent_action = on_intent_action
    _config.warned_missing_user_id = False


@dataclass(frozen=True)
class IntentActionOutcome:
    """Ergebnis eines Button-Klicks, wie der Worker-Callback es zurückgibt.

    `intent` ist die frische Vorhaben-Zeile (attributgeladen) für das
    Karten-Update — None, wenn es nichts nachzuziehen gibt. Fehlerarten
    (`error`): not_found | not_planned | over_ceiling | adjust | unknown.
    """

    verb: str
    intent_id: int
    ok: bool
    intent: Any | None = None
    old_amount: int | None = None
    new_amount: int | None = None
    ceiling: int | None = None
    error: str = ""
    status: str = ""
    detail: str = ""
    # Ärm-Zustand zur Klick-Zeit (Review 31.08.): die exec-Antwort versprach
    # Ausführung, auch wenn AGENT_AUTO_INTENTS=0 sie sicher verweigern würde.
    armed: bool = True


def outcome_reply(outcome: IntentActionOutcome) -> str:
    """Ephemerer Antworttext je Klick-Ergebnis (Spec §3, Tabelle)."""
    if outcome.ok:
        if outcome.verb == "exec":
            if not outcome.armed:
                return (
                    "▶ Vorgezogen — ABER: AGENT_AUTO_INTENTS ist aus, der Tick wird "
                    "ehrlich refused melden. Scharf schalten kann nur Emil (.env)."
                )
            return "▶ Wird beim nächsten Tick (≤ 5 min) ausgeführt."
        if outcome.verb == "skip":
            if outcome.status == "executing":
                # Veto-Vormerkung (Review 31.08.): Versuch läuft gerade — Emils
                # Wille greift, sobald der Versuch scheitert; nur ein bereits
                # durchgegangener Kauf ist ehrlich zu spät.
                return (
                    "⏳ Versuch läuft gerade — Veto VORGEMERKT: scheitert er, wird das "
                    "Vorhaben übersprungen; ging der Kauf schon durch, ist es zu spät."
                )
            return "⏭ Übersprungen — der Planner legt es heute nicht neu an."
        if outcome.verb in ("p10", "p20"):
            return (
                f"Betrag: {fmt_money(outcome.old_amount)} → {fmt_money(outcome.new_amount)} "
                f"(Grenze {fmt_money(outcome.ceiling)})."
            )
        return "OK."
    if outcome.error == "not_planned":
        status_de = INTENT_STATUS_DE.get(outcome.status, outcome.status or "?")
        return f"Vorhaben ist bereits ‚{status_de}' — nichts geändert."
    if outcome.error == "over_ceiling":
        # Review 31.08.: Der alte Text verwies auf die Zentrale — die lehnt mit
        # derselben Grenze genauso ab. Der echte Ausweg ist ein neues Vorhaben.
        return (
            "Mehr als 1,5 × Ursprungsbetrag geht nirgends — für mehr in der "
            "Zentrale ein NEUES Vorhaben anlegen."
        )
    if outcome.error == "not_found":
        return f"Vorhaben {outcome.intent_id} nicht gefunden."
    return outcome.detail or "Aktion nicht möglich."


# ------------------------------------------------------------------ Embed


def _as_utc(dt: datetime) -> datetime:
    """SQLite liefert naive Zeitstempel zurück — als UTC interpretieren."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _clip(text: str, limit: int = RATIONALE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 2].rstrip() + " …"


def _club(team_id: str | None) -> str:
    team = TEAMS.get(str(team_id or ""))
    return team.kb_name if team else str(team_id or "")


def _euro(amount: int) -> str:
    """Exakter Betrag deutsch: 4213337 → '4.213.337 €'."""
    return f"{amount:,}".replace(",", ".") + " €"


def _clock(dt: datetime, now: datetime) -> str:
    local = _as_utc(dt).astimezone(BERLIN)
    if local.date() == now.astimezone(BERLIN).date():
        return local.strftime("%H:%M Uhr")
    return local.strftime("%d.%m. %H:%M Uhr")


def _relative(seconds: int) -> str:
    hours, minutes = divmod(max(0, seconds) // 60, 60)
    if hours and minutes:
        return f"in {hours} h {minutes} min"
    if hours:
        return f"in {hours} h"
    return f"in {max(minutes, 1)} min"


def _result_line(intent: Any) -> str:
    """Endzustands-Text der Karte (Spec §3, Karten-Lebenszyklus)."""
    result = getattr(intent, "result", None) or ""
    status = intent.status
    if status == "executed":
        return f"✅ Ausgeführt · {result}" if result else "✅ Ausgeführt"
    if status == "vetoed":
        via = "Discord" if "[Discord]" in (getattr(intent, "emil_note", None) or "") else "Zentrale"
        return f"⏭ Übersprungen (via {via})"
    if status == "failed":
        return f"❌ Fehlgeschlagen: {result}" if result else "❌ Fehlgeschlagen"
    if status == "expired":
        return "⏰ Abgelaufen (Deadline überschritten)"
    if status == "superseded":
        return f"↺ Ersetzt: {result}" if result else "↺ Ersetzt"
    if status == "executing":
        return "⏳ Wird gerade ausgeführt …"
    return status


def format_intent_embed(intent: Any, now: datetime) -> discord.Embed:
    """Karte je Vorhaben: planned mit Zeiten, Endzustand mit ehrlichem Ergebnis.

    `intent` ist eine (auch detachte) Intent-Zeile — es werden nur einfache
    Attribute gelesen (id, kind, status, amount, rationale, …), keine DB.
    """
    amount = int(intent.amount or 0)
    big = amount >= BIG_AMOUNT
    status = intent.status
    if status == "planned":
        emoji = "🟠" if big else "🟢"
        color = _COLOR_BIG if big else _COLOR_PLANNED
    else:
        emoji = _STATUS_EMOJI.get(status, "•")
        color = {
            "executed": _COLOR_EXECUTED,
            "failed": _COLOR_FAILED,
        }.get(status, _COLOR_NEUTRAL)

    kind_label = _KIND_LABEL.get(intent.kind, intent.kind)
    lines = []
    name = intent.player_name or f"Spieler {intent.player_id}"
    pos = _POS.get(int(intent.position or 0), "")
    club = _club(intent.team_id)
    detail = " · ".join(x for x in (pos, club) if x)
    lines.append(f"**{name}**" + (f" ({detail})" if detail else ""))
    if big and status == "planned":
        lines.append("⚠ Großes Vorhaben — Veto-Fenster 2 h")

    embed = discord.Embed(
        title=f"{emoji} Vorhaben #{intent.id} · {kind_label}",
        description="\n".join(lines),
        color=color,
    )
    if amount > 0:
        embed.add_field(name="Betrag", value=_euro(amount), inline=False)
    if status == "planned":
        execute_at = _as_utc(intent.execute_at)
        if execute_at <= now:
            when = "sofort"
        else:
            when = (
                f"{_clock(execute_at, now)} "
                f"({_relative(int((execute_at - now).total_seconds()))})"
            )
        embed.add_field(name="Ausführung", value=when, inline=False)
        if intent.deadline is not None:
            embed.add_field(name="Deadline", value=_clock(intent.deadline, now), inline=False)
    else:
        embed.add_field(name="Ergebnis", value=_clip(_result_line(intent)), inline=False)
    if intent.rationale:
        embed.add_field(name="Warum", value=_clip(str(intent.rationale)), inline=False)
    return embed


def build_intent_view(intent: Any) -> discord.ui.View:
    """Buttons je kind: bid/list alle vier, sell/accept nur Ausführen + Überspringen
    (kein Betrag anpassbar — wie `ADJUSTABLE_KINDS` der API)."""
    view = discord.ui.View(timeout=None)
    verbs = ("exec", "p10", "p20", "skip") if intent.kind in ADJUSTABLE_KINDS else ("exec", "skip")
    for verb in verbs:
        view.add_item(IntentButton(verb, int(intent.id)))
    return view


# ------------------------------------------------------------------ Buttons


class IntentButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=CUSTOM_ID_TEMPLATE,
):
    """EIN Regex-Handler für alle Intent-Buttons — restart-sicher ohne Reload-Listen.

    `bot.add_dynamic_items(IntentButton)` (einmal, in on_ready) matcht jede
    eingehende custom_id gegen das Template; auch Karten von vor einem Neustart
    funktionieren damit ohne Re-Registrierung je Nachricht.
    """

    def __init__(self, verb: str, intent_id: int) -> None:
        label, style, row = _BUTTONS[verb]
        super().__init__(
            discord.ui.Button(
                label=label, style=style, custom_id=intent_custom_id(verb, intent_id)
            ),
            row=row,
        )
        self.verb = verb
        self.intent_id = intent_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        # Signatur von discord.py vorgegeben (Interaction, Item, re.Match).
        return cls(match["verb"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        # Fail-closed-User-Prüfung bleibt VOR jeder Auswertung.
        if not await _authorized(interaction):
            return
        handler = _config.on_intent_action
        if handler is None:
            await interaction.response.send_message(
                "Intent-Buttons sind nicht verdrahtet — bitte Worker-Logs prüfen.",
                ephemeral=True,
            )
            return
        # defer() SOFORT (Review 31.08.): Der Handler darf auf eine SQLite-
        # Schreibsperre warten (busy_timeout 30 s, via to_thread ohne die Loop
        # zu blocken) — Discords 3-s-Fenster für die Erst-Antwort hält das
        # defer, die echte Antwort kommt als Followup. Fehler landen sichtbar
        # beim Klicker statt nur im Log („Interaktion fehlgeschlagen").
        await interaction.response.defer(ephemeral=True)
        try:
            outcome = await handler(self.verb, self.intent_id)
        except Exception:  # noqa: BLE001 — Emil braucht eine Antwort, kein Schweigen
            _log.warning(
                "Intent-Button %s:%s fehlgeschlagen.", self.verb, self.intent_id,
                exc_info=True,
            )
            await interaction.followup.send(
                "Aktion gerade nicht möglich (DB beschäftigt?) — bitte gleich "
                "erneut versuchen; die Zentrale zeigt den echten Stand.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(outcome_reply(outcome), ephemeral=True)
        await _refresh_card(interaction, outcome)


async def _authorized(interaction: discord.Interaction) -> bool:
    # Fail-closed wie DiscordChannel._authorized: ohne konfigurierte
    # DISCORD_USER_ID wird ausnahmslos abgelehnt.
    if not _config.allowed_user_id:
        if not _config.warned_missing_user_id:
            _log.warning(
                "Keine DISCORD_USER_ID konfiguriert — alle Intent-Eingriffe werden abgelehnt."
            )
            _config.warned_missing_user_id = True
        await interaction.response.send_message(
            "Keine DISCORD_USER_ID konfiguriert — Eingriffe gesperrt.", ephemeral=True
        )
        return False
    if str(interaction.user.id) != _config.allowed_user_id:
        await interaction.response.send_message("Nicht autorisiert.", ephemeral=True)
        return False
    return True


async def _refresh_card(interaction: discord.Interaction, outcome: IntentActionOutcome) -> None:
    """Karte direkt nach dem Klick nachziehen (best effort).

    Scheitert der Edit, bleibt die ephemere Antwort trotzdem stehen — der
    Kehrer im Executor-Tick (≤ 5 min) gleicht die Karte ohnehin an (L-088).
    """
    intent = outcome.intent
    message = getattr(interaction, "message", None)
    if intent is None or message is None:
        return
    try:
        now = datetime.now(UTC)
        view = build_intent_view(intent) if intent.status == "planned" else None
        await message.edit(embed=format_intent_embed(intent, now), view=view)
    except Exception:  # noqa: BLE001 — Karten-Kosmetik darf den Klick nie brechen
        _log.warning(
            "Intent-Karte %s nicht direkt nachgezogen (Kehrer übernimmt).",
            outcome.intent_id,
            exc_info=True,
        )
