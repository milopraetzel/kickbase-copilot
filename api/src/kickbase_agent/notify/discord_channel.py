"""Discord-Implementierung des Approval-Kanals (Buttons: Annehmen/Ablehnen).

Restart-sicherheit (Review-Fund): Die Buttons tragen eine STABILE custom_id
(`kba:approve:{action_id}` / `kba:reject:{action_id}`) und hängen an einer View
mit timeout=None. discord.py ordnet eingehende Button-Klicks über genau diese
custom_id einer registrierten View zu — nach einem Worker-Neustart registriert
`register_persistent_views` die Views aller noch gültigen 'proposed'-Vorschläge
neu, damit alte Buttons wieder funktionieren (vorher: „Interaktion
fehlgeschlagen", obwohl der Vorschlag in der DB noch gültig war). Das
Interaktions-Handling extrahiert die action_id aus der custom_id — die
Datenbank bleibt die einzige Wahrheit über den Vorschlagszustand
(execute_staged prüft TTL/Status selbst).

Berichte-Ablage (Steuerzentrale, Zone „Berichte"): jeder Info-/Briefing-Post
wird NACH dem Versand über notify/report_store.save_report als Volltext in
`reports` abgelegt (source `worker:<kind>`, meta.delivered = ob Discord ihn
angenommen hat). Gespeichert wird auch, was NICHT rauskam (delivered=false) —
so bleibt der Bericht lesbar, wenn Discord hakt. Ohne expliziten `kind`
greift die Text-Heuristik und es werden nur substanzielle Texte abgelegt
(keine Kurz-Infos < MIN_REPORT_CHARS, keine „…-Fehler:"-Meldungen); ein
explizit mitgegebener `kind` speichert immer. Die Ablage darf den Versand nie
stören: DB-Fehler werden im report_store nur geloggt.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import discord

from kickbase_agent.notify.base import ChannelMessage
from kickbase_agent.notify.report_store import (
    DIGEST_DELIVERY_SLOTS,
    briefing_slot_key,
    claim_briefing_slot,
    discord_delivery_allowed,
    finish_briefing_claim,
    infer_kind,
    normalize_kind,
    save_report,
    title_from,
    worth_storing,
)

_log = logging.getLogger(__name__)

# Callbacks, die der Worker injiziert (action_id → Ergebnis).
ApproveCb = Callable[[int], Awaitable[Any]]
RejectCb = Callable[[int], Awaitable[Any]]

# Stabiles custom_id-Schema — Präfix "kba" hält fremde Komponenten fern.
_CID_NAMESPACE = "kba"
_CID_KINDS = ("approve", "reject")


def approve_custom_id(action_id: int) -> str:
    return f"{_CID_NAMESPACE}:approve:{action_id}"


def reject_custom_id(action_id: int) -> str:
    return f"{_CID_NAMESPACE}:reject:{action_id}"


def parse_custom_id(custom_id: str) -> tuple[str, int] | None:
    """'kba:approve:17' → ("approve", 17); None für fremde/kaputte IDs."""
    parts = (custom_id or "").split(":")
    if len(parts) != 3 or parts[0] != _CID_NAMESPACE or parts[1] not in _CID_KINDS:
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


class DiscordChannel:
    def __init__(
        self,
        bot: discord.Client,
        channel_id: str,
        allowed_user_id: str,
        on_approve: ApproveCb,
        on_reject: RejectCb,
        digest_only: bool = False,
    ) -> None:
        self._bot = bot
        self._channel_id = int(channel_id) if channel_id else 0
        self._allowed_user_id = str(allowed_user_id or "")
        self._on_approve = on_approve
        self._on_reject = on_reject
        self._digest_only = digest_only
        # Fail-closed-Warnung nur einmal loggen (nicht bei jedem Klick spammen).
        self._warned_missing_user_id = False

    def _channel(self) -> discord.abc.Messageable | None:
        return self._bot.get_channel(self._channel_id)  # type: ignore[return-value]

    async def send_info(
        self, text: str, *, kind: str | None = None, title: str | None = None
    ) -> None:
        """Reine Info/Report; im Digest-Modus nur archivieren — außer der Art `blocker`
        (seit 23.09.2026 auch nicht mehr `morgenbericht`: eine Nachricht am Tag)."""
        channel = self._channel()
        delivered = False
        try:
            if discord_delivery_allowed(kind or "", self._digest_only) and channel is not None:
                await channel.send(text)
                delivered = True
        finally:
            # Digest-only is also the audit boundary: even short or implicit routine/error
            # messages must survive in DB/WebApp while Discord stays silent.
            store_kind = kind
            if self._digest_only and store_kind is None:
                store_kind = infer_kind(text)
            self._store(text, kind=store_kind, title=title, delivered=delivered)

    async def send_blocker(self, text: str) -> None:
        """Expliziter, nicht autonom behebbarer Blocker; auch im Digest-Modus sichtbar."""
        body = f"🚨 **Blocker** — {text.strip()}"
        channel = self._channel()
        delivered = False
        try:
            if channel is not None:
                await channel.send(body)
                delivered = True
        finally:
            self._store(body, kind="blocker", title="Blocker", delivered=delivered)

    async def send_briefing(
        self,
        title: str,
        body: str,
        footer: str = "",
        *,
        kind: str | None = None,
        delivery_slot: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Embed-Karte (Tagesbriefing); Ablage mit dem Embed-Titel als Berichtstitel.

        Digest-Modus (Emil 23.09.2026): zugestellt wird nur ein Slot aus
        `DIGEST_DELIVERY_SLOTS` (08:15, Tageszusammenfassung) und auch der höchstens
        einmal (atomarer Claim). Das Abend-Briefing wird nur archiviert.
        """
        channel = self._channel()
        delivered = False
        slot_key = briefing_slot_key(delivery_slot, now or datetime.now(UTC))
        claim_id: int | None = None
        if (
            self._digest_only
            and slot_key is not None
            and delivery_slot in DIGEST_DELIVERY_SLOTS
        ):
            claim_id = claim_briefing_slot(
                slot_key=slot_key, title=title, body=body, footer=footer
            )
        may_deliver = not self._digest_only or claim_id is not None
        try:
            if may_deliver and channel is not None:
                embed = discord.Embed(title=title, description=body, color=0x00E38C)
                if footer:
                    embed.set_footer(text=footer)
                await channel.send(embed=embed)
                delivered = True
        finally:
            if claim_id is not None:
                finish_briefing_claim(claim_id, delivered=delivered)
            else:
                extra = {"embed": True, "footer": footer}
                if slot_key is not None:
                    extra["suppressed_delivery_slot"] = slot_key
                self._store(
                    body,
                    kind=kind
                    or ("briefing" if delivery_slot else infer_kind(f"{title}\n{body}")),
                    title=title_from(title),
                    delivered=delivered,
                    extra=extra,
                )

    def _store(
        self,
        text: str,
        *,
        kind: str | None,
        title: str | None,
        delivered: bool,
        extra: dict[str, Any] | None = None,
    ) -> int | None:
        """Bericht ablegen — explizites kind immer, geratenes nur für substanzielle Texte.

        Niemals eine Exception nach außen (sonst würde der Fehler-Post von `_safe`
        selbst wieder hier landen): save_report loggt DB-Fehler, der Rest ist abgesichert.
        """
        try:
            body = (text or "").strip()
            if not body:
                return None
            if kind is None and not worth_storing(body):
                return None
            final_kind = normalize_kind(kind or infer_kind(body))
            meta: dict[str, Any] = {"delivered": delivered, "chars": len(body)}
            if extra:
                meta.update(extra)
            return save_report(
                kind=final_kind,
                title=title or title_from(body),
                body=body,
                source=f"worker:{final_kind}",
                meta=meta,
            )
        except Exception:  # noqa: BLE001 — Ablage darf den Versand nie stören
            _log.warning("Berichte-Ablage übersprungen (kind=%s).", kind, exc_info=True)
            return None

    async def send_card(self, *, embed: discord.Embed, view: discord.ui.View) -> str | None:
        """Intent-Karte mit Buttons senden (Spec 2026-08-31 §4.4).

        Rückgabe: Discord-Message-ID als String (für `intents.discord_msg_id`)
        — None, wenn der Bot den Kanal (noch) nicht kennt oder Digest-only aktiv ist. Fehler beim
        Senden propagieren; der Aufrufer (Planner-Hook) fängt sie je Karte ab.
        """
        if self._digest_only:
            return None
        channel = self._channel()
        if channel is None:
            return None
        msg = await channel.send(embed=embed, view=view)
        return str(msg.id)

    async def edit_card(
        self, message_id: str, *, embed: discord.Embed, view: discord.ui.View | None
    ) -> bool:
        """Bestehende Intent-Karte nachziehen (Kehrer, Spec §4.5).

        Rückgabe False NUR bei 404 (Karte manuell gelöscht) — der Aufrufer
        nullt dann `discord_msg_id` und postet nie neu. Andere Fehler
        propagieren (nächster Tick versucht erneut).
        """
        channel = self._channel()
        if channel is None:
            raise RuntimeError("Discord-Kanal nicht verfügbar (Bot noch nicht bereit?)")
        try:
            # partial_message (Review 31.08.): EIN REST-Call statt fetch+edit —
            # der Kehrer läuft alle 5 min, das N+1 war der halbe Preis jedes
            # Laufs. discord.NotFound wirft der Edit genauso.
            await channel.get_partial_message(int(message_id)).edit(embed=embed, view=view)
        except discord.NotFound:
            return False
        return True

    async def send_approval(self, message: ChannelMessage) -> None:
        if self._digest_only:
            return
        channel = self._channel()
        if channel is None:
            return
        embed = discord.Embed(
            title=message.title, description=message.body, color=0x00E38C
        )
        await channel.send(embed=embed, view=self._build_view(message.action_id))

    def register_persistent_views(self, action_ids: Iterable[int]) -> int:
        """Beim Bot-Start: Views für noch gültige 'proposed'-Vorschläge registrieren.

        discord.py matcht eingehende Button-Interaktionen über die custom_id —
        ohne Neu-Registrierung wären Buttons aus der Zeit VOR dem Neustart tot,
        obwohl der Vorschlag in der DB weiter gültig ist. Gibt die Anzahl
        registrierter Views zurück.
        """
        count = 0
        for action_id in action_ids:
            self._bot.add_view(self._build_view(action_id))
            count += 1
        if count:
            _log.info("%d offene Freigabe-View(s) nach Neustart reaktiviert.", count)
        return count

    def _build_view(self, action_id: int | None) -> discord.ui.View:
        # timeout=None + custom_id auf JEDEM Item ⇒ View.is_persistent() — nur
        # solche Views akzeptiert bot.add_view beim Neustart-Reload.
        view = discord.ui.View(timeout=None)
        aid = int(action_id or 0)
        approve = discord.ui.Button(
            label="✅ Annehmen",
            style=discord.ButtonStyle.success,
            custom_id=approve_custom_id(aid),
        )
        reject = discord.ui.Button(
            label="❌ Ablehnen",
            style=discord.ButtonStyle.danger,
            custom_id=reject_custom_id(aid),
        )
        # Beide Buttons laufen durch denselben Handler — die action_id kommt aus
        # der custom_id, nicht aus einer Closure: so funktioniert derselbe Code
        # für frisch gesendete UND nach Neustart re-registrierte Views.
        approve.callback = self._handle_component  # type: ignore[method-assign]
        reject.callback = self._handle_component  # type: ignore[method-assign]
        view.add_item(approve)
        view.add_item(reject)
        return view

    async def _handle_component(self, interaction: discord.Interaction) -> None:
        # Fail-closed-User-Prüfung bleibt VOR jeder Auswertung.
        if not await self._authorized(interaction):
            return
        raw_cid = str((getattr(interaction, "data", None) or {}).get("custom_id") or "")
        parsed = parse_custom_id(raw_cid)
        if parsed is None:
            await interaction.response.send_message(
                f"Unbekannte Interaktion ({raw_cid!r}).", ephemeral=True
            )
            return
        kind, action_id = parsed
        if kind == "approve":
            await self._approve(interaction, action_id)
        else:
            await self._reject(interaction, action_id)

    async def _approve(self, interaction: discord.Interaction, action_id: int) -> None:
        result = await self._on_approve(action_id)
        status = getattr(result, "status", "ok")
        # Ehrliche Rückmeldung: Dry-Run darf nie wie ein echter Trade aussehen.
        if status == "executed":
            content = f"✅ Ausgeführt (#{action_id})"
        elif status == "skipped_dry_run":
            content = (
                f"🟠 [DRY-RUN] Freigabe protokolliert, aber NICHT bei Kickbase "
                f"ausgeführt (#{action_id}) — scharf erst mit AGENT_DRY_RUN=0."
            )
        elif status == "expired":
            content = (
                f"⏰ Vorschlag abgelaufen (#{action_id}) — bitte neu rechnen "
                "lassen (der nächste Scan schlägt frisch vor)."
            )
        elif status == "unknown":
            content = (
                f"⚠️ Timeout bei #{action_id} — Ausführung UNKLAR: kann bei "
                "Kickbase durchgegangen sein, bitte in der App prüfen. "
                "Kein automatischer Retry."
            )
        else:
            content = f"⚠️ Status: {status} (#{action_id})"
        await interaction.response.edit_message(content=content, embed=None, view=None)

    async def _reject(self, interaction: discord.Interaction, action_id: int) -> None:
        await self._on_reject(action_id)
        await interaction.response.edit_message(
            content=f"❌ Abgelehnt (#{action_id})", embed=None, view=None
        )

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        # Fail-closed (Review-Fund): ohne konfigurierte DISCORD_USER_ID durfte
        # vorher JEDER Server-Nutzer freigeben — jetzt wird ausnahmslos abgelehnt.
        if not self._allowed_user_id:
            if not self._warned_missing_user_id:
                _log.warning(
                    "Keine DISCORD_USER_ID konfiguriert — alle Freigaben werden abgelehnt."
                )
                self._warned_missing_user_id = True
            await interaction.response.send_message(
                "Keine DISCORD_USER_ID konfiguriert — Freigaben gesperrt.", ephemeral=True
            )
            return False
        if str(interaction.user.id) != self._allowed_user_id:
            await interaction.response.send_message(
                "Nicht autorisiert.", ephemeral=True
            )
            return False
        return True
