"""Tägliche Prämie (Login-Bonus / Auflaufprämie) automatisch einsammeln.

Emils Wunsch: die App soll keine uneingesammelten Prämien mehr anzeigen. Läuft
mehrmals täglich (08:03 / 13:03 / 18:03) UND einmal beim Container-Start
(Neustart-Nachholer). Zwei Wahrheitsquellen (Live-Fund 23.08.):
- Der Executor misst die Gutschrift als Konto-Delta: executed ⇒ wirklich
  gutgeschrieben (Betrag in der Meldung); noop ⇒ Kickbase antwortete 200, aber
  0 € (Prämie heute schon per App kassiert oder noch nicht frei) — retryable,
  der nächste Slot prüft nach; executed-Zeilen machen weitere Slots 'duplicate'.
- Der Liga-Feed kennt das Ereignis „Auflaufprämie kassiert" (t=22, `bn` =
  Betrag, `day` = Streak-Tag, Zeitstempel UTC; Tagesgrenze = Mitternacht
  Berlin). Damit sagt die Meldung ehrlich, ob HEUTE schon kassiert wurde — auch
  wenn die App es war, nicht wir.
Gepostet wird im primären Slot (08:03) und immer bei echter Gutschrift; der
18:03-Slot warnt, falls bis dahin weder App noch Agent etwas kassiert haben.
Scharf nur mit AGENT_AUTO_BONUS=1; sonst stiller Dry-Run-Logeintrag.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
from kickbase_agent.config import get_settings
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify.discord_channel import DiscordChannel

log = logging.getLogger(__name__)

#: Feed-Ereignistyp „Auflaufprämie kassiert" (data.bn = Betrag, data.day = Streak-Tag).
BONUS_FEED_TYPE = 22
BERLIN = ZoneInfo("Europe/Berlin")


def _eur(value: object) -> str:
    try:
        return f"{int(value):,} €".replace(",", ".")
    except (TypeError, ValueError):
        return "? €"


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def find_todays_bonus_event(items: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Jüngstes Prämien-Ereignis (t=22) des HEUTIGEN Berliner Tages, sonst None.

    Rückgabe: {"at": datetime(Berlin), "amount": int, "day": int}.
    """
    today = now.astimezone(BERLIN).date()
    best: dict[str, Any] | None = None
    for item in items:
        if item.get("t") != BONUS_FEED_TYPE:
            continue
        dt = _parse_iso(item.get("dt"))
        if dt is None:
            continue
        local = dt.astimezone(BERLIN)
        if local.date() != today:
            continue
        data = item.get("data") or {}
        try:
            event = {
                "at": local,
                "amount": int(data.get("bn") or 0),
                "day": int(data.get("day") or 0),
            }
        except (TypeError, ValueError):
            continue
        if best is None or event["at"] > best["at"]:
            best = event
    return best


async def run_bonus(
    channel: DiscordChannel,
    *,
    quiet_duplicate: bool = True,
    warn_on_noop: bool = False,
    primary: bool = False,
) -> str:
    """Login-Bonus einsammeln. Gibt den Executor-Status zurück."""
    settings = get_settings()
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"
    armed = settings.agent_auto_bonus
    with SessionLocal() as session:
        result = await ActionExecutor(client, session).execute(
            PlannedAction(action_type="bonus", league_id=lg,
                          rule_id="auto-bonus", rationale="Tägliche Prämie einsammeln"),
            dry_run=False if armed else None,
        )
    raw = result.raw_response if isinstance(result.raw_response, dict) else {}
    event: dict[str, Any] | None = None
    try:
        event = find_todays_bonus_event(
            await client.get_activity_feed(lg, max_items=40), datetime.now(UTC)
        )
    except Exception as exc:  # noqa: BLE001 — Gegenprobe ist Zusatz, kein Blocker
        log.warning("Prämien-Feed-Gegenprobe fehlgeschlagen: %s", exc)

    if result.status == "executed":
        if "kba_credited" in raw:
            await channel.send_info(
                f"🎁 Prämie eingesammelt ✓ +{_eur(raw['kba_credited'])} "
                f"(Konto {_eur(raw.get('kba_budget_after'))})"
                + (f" — Tag {event['day']}" if event and event.get("day") else "")
            )
        else:
            await channel.send_info("🎁 Prämie eingesammelt ✓")
    elif result.status in ("noop", "duplicate"):
        if event is not None and (primary or not quiet_duplicate):
            await channel.send_info(
                f"🎁 Prämie heute bereits kassiert (App/Agent, {event['at']:%H:%M} Uhr) — "
                f"Tag {event['day']}, +{_eur(event['amount'])}."
            )
        elif result.status == "noop" and warn_on_noop and event is None:
            await channel.send_info(
                "⚠️ Prämie heute bisher nicht gutgeschrieben (Konto unverändert bei "
                f"{_eur(raw.get('kba_budget_after'))}, kein Feed-Ereignis) — bitte einmal in "
                "der App prüfen."
            )
    elif result.status in ("skipped_dry_run", "refused") and not armed:
        await channel.send_info(
            "🎁 [DRY-RUN] Würde Prämie einsammeln — scharf schalten: AGENT_AUTO_BONUS=1"
        )
    return result.status
