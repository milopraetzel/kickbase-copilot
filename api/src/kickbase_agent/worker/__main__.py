"""`kba-worker` — Discord-Bot + Scheduler.

Startet den Bot (hört auf Approval-Buttons) und plant den täglichen Markt-Scan
um 22:15 (nach dem Kickbase-Marktwert-Update). Mit `--scan-now` wird sofort ein
Scan gesendet (für Tests). Solange AGENT_DRY_RUN=1 löst ein Klick keine echte
Kickbase-Aktion aus.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger as _CronTrigger

from kickbase_agent.agent.executor import ActionExecutor
from kickbase_agent.cli.plan import build_plan
from kickbase_agent.config import get_settings
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.lineup_service import recommend_lineup
from kickbase_agent.intel.jev_journal import purge_old_judgments
from kickbase_agent.jobs.snapshot import SnapshotResult, run_full_history_reload, run_snapshot
from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify import intent_cards
from kickbase_agent.notify.discord_channel import DiscordChannel
from kickbase_agent.notify.report_store import purge_old_reports
from kickbase_agent.trading.orchestrator import build_trading_plan
from kickbase_agent.trading.pipeline import TradingConfig
from kickbase_agent.worker import job_health
from kickbase_agent.worker.bid_review import run_bid_review
from kickbase_agent.worker.bonus_job import run_bonus
from kickbase_agent.worker.briefing import (
    briefing_window_start,
    build_briefing,
    summarize_report_window,
    team_value_change,
)
from kickbase_agent.worker.competition_training_job import run_competition_training_job
from kickbase_agent.worker.daily_summary import publish_briefing
from kickbase_agent.worker.decision_scan import run_decision_scan
from kickbase_agent.worker.engine_cycle import run_engine_cycle
from kickbase_agent.worker.error_digest import FehlerBuendel
from kickbase_agent.worker.evidence_job import JOB_LABEL as EVIDENCE_LABEL
from kickbase_agent.worker.evidence_job import run_evidence_job
from kickbase_agent.worker.forecast_training_job import run_forecast_training_job
from kickbase_agent.worker.freeze_job import run_freeze_watch
from kickbase_agent.worker.intents import (
    handle_intent_action,
    run_intent_executor,
)
from kickbase_agent.worker.kickoff_guard import (
    run_closing_bell,
    schedule_kickoff_guards,
)
from kickbase_agent.worker.lineup_job import (
    run_lineup_gap_check,
    run_lineup_set,
    run_lineup_set_gated,
    schedule_late_gap_check,
)
from kickbase_agent.worker.news_radar import run_news_radar
from kickbase_agent.worker.newsfeed import run_news_update
from kickbase_agent.worker.outcome_guard import run_outcome_guard
from kickbase_agent.worker.pulse import run_lineup_leak, run_pulse
from kickbase_agent.worker.reconcile import run_reconcile
from kickbase_agent.worker.research import run_research
from kickbase_agent.worker.scan import reload_approval_views, send_staged, stage_plan
from kickbase_agent.worker.steiger_radar import run_steiger_radar
from kickbase_agent.worker.trade_autopilot import run_trade_autopilot
from kickbase_agent.worker.trade_radar import run_trade_radar


def CronTrigger(*args, **kwargs):  # noqa: N802 — Drop-in mit fester Zeitzone
    """CronTrigger IMMER in Europe/Berlin bauen.

    Nackte Trigger-OBJEKTE binden ihre Zeitzone bei der Konstruktion an die
    Container-Systemzeit (UTC auf der Box) — die Scheduler-Zeitzone greift dann
    NICHT mehr und alle Jobs laufen 2 h zu spät (Review-Fund, live bewiesen:
    das „22:15-Briefing" feuerte um 00:15 Berlin).
    """
    kwargs.setdefault("timezone", "Europe/Berlin")
    return _CronTrigger(*args, **kwargs)


async def _make_client() -> KickbaseClient:
    # Zentrale Fabrik (Review-Fund P0): Client kommt MIT Auth-Failure-Refresh —
    # ein abgelaufener Token killt keinen Job mehr, er wird transparent erneuert.
    client, _auth = await make_kickbase_client()
    return client


async def _make_client_with_user() -> tuple[KickbaseClient, str | None]:
    """Client + user_id — der Executor braucht die user_id fürs Vereinslimit
    (offene Gebote je Club; Codex-Folge-Review 24.08., P1)."""
    client, auth = await make_kickbase_client()
    return client, auth.user_id


def make_scheduled_job(safe, label: str, factory):
    """Async-Job für APScheduler: erzeugt bei JEDEM Feuern eine frische Coroutine.

    WICHTIG: Ein `lambda: _safe(coro, …)` wäre eine Sync-Funktion, deren Coroutine
    der Scheduler nie awaited — Jobs melden dann „executed successfully", tun aber
    NICHTS (so blieb das erste 22:15-Briefing stumm). Der Runner ist eine echte
    Coroutine-FUNKTION, die AsyncIOScheduler auf dem Event-Loop awaited.
    """
    async def runner():
        # Heartbeat (Emils Regel 25.08.): JEDER Job schreibt Start und Ende in
        # `job_runs` — nur so ist beweisbar, dass ein Cron wirklich gefeuert hat
        # ("Container läuft" und "Added job" haben zweimal getäuscht: Sync-Lambda
        # und TZ-Falle). Der Wächter (job_health) rechnet daraus Überfälligkeit.
        now = datetime.now(UTC)
        run_id = None
        with SessionLocal() as session:
            run_id = job_health.record_start(session, label, now)
        error: str | None = None

        async def observed_work():
            # _safe meldet und schluckt Fehler. Deshalb vor dieser Grenze
            # beobachten, sonst wird auch ein gescheiterter Lauf als ok verbucht.
            nonlocal error
            try:
                return await factory()
            except BaseException as exc:  # noqa: BLE001 — auch Abbruch ist kein Erfolg
                error = f"{type(exc).__name__}: {exc}"
                raise

        try:
            await safe(observed_work(), label)
        except BaseException as exc:  # noqa: BLE001 — Heartbeat schreiben, dann weiterreichen
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            with SessionLocal() as session:
                job_health.record_finish(
                    session, run_id, datetime.now(UTC), error=error
                )

    runner.__name__ = f"job_{label}"
    # APScheduler benennt den Job ueber `get_callable_name`, und das nimmt den
    # QUALNAME — ohne die naechste Zeile heisst JEDER Job
    # „make_scheduled_job.<locals>.runner". Folge (Fehlalarm-Serie 25.08.): Der
    # Cron-Waechter konnte keinen Job seinem Heartbeat in `job_runs` zuordnen und
    # meldete gesunde Jobs als „noch nie gelaufen" — sechs identisch benannte
    # Zeilen, aus denen niemand lesen konnte, welcher Job gemeint ist.
    runner.__qualname__ = runner.__name__
    return runner


#: Sonntags-Erinnerung an die zwei Retro-Texte (Emils Wahl 25.08.: Brief + Plan
#: statt Lern-Bilanz-Infrastruktur). Der Worker kann die Texte nicht selbst
#: schreiben — sie brauchen Urteil, nicht Daten. Er sorgt nur dafür, dass sie
#: nicht untergehen, wenn gerade keine Session läuft.
RETRO_REMINDER = (
    "📅 **Sonntag — Zeit für die Wochen-Retro** (zwei Texte, beide in die Zentrale):\n"
    "**(A) Der Brief:** Ich hielt X für wahr → Y ist passiert → deshalb ändere ich Z. "
    "Pflicht: mindestens ein Punkt, bei dem ich falsch lag.\n"
    "**(C) Mein Plan, mein Stand:** Ziel · wo ich stehe · nächste drei Schritte · "
    "was mich stoppen würde · was ich diese Woche geändert habe.\n"
    "-# Auslösen mit `/kickbase retro` — ohne Session bleibt es bei dieser Erinnerung."
)


# Zwei Briefings täglich, Berliner Zeit. Im Digest-Modus geht nur der Morgen in den
# Kanal — als Tageszusammenfassung (Emil 23.09.2026: „nur einmal am Tag"); der Abend
# wird archiviert (report_store.DIGEST_DELIVERY_SLOTS).
DAILY_BRIEFING_SLOTS = ((8, 15), (22, 15))


def daily_briefing_trigger(hour: int, minute: int) -> _CronTrigger:
    """Bericht exakt zur vereinbarten Berliner Uhrzeit — bewusst ohne Jitter."""
    return CronTrigger(hour=hour, minute=minute)


def purge_archives() -> dict[str, int]:
    """Aufräumen 03:33: flüchtige Berichte und das Jev-Protokoll, je älter als 90 Tage.
    Beide Aufräumer loggen ihre Fehler selbst und geben dann 0 zurück."""
    return {"berichte": purge_old_reports(), "jev": purge_old_judgments()}


async def run_retro_reminder(channel) -> str:
    """Sonntags an die Retro erinnern (reiner Discord-Post, kein Datenzugriff)."""
    await channel.send_info(RETRO_REMINDER)
    return "sent"


async def run_snapshot_job() -> SnapshotResult | None:
    """Täglicher Daten-Snapshot (Marktwerte/Performance) — Grundlage fürs Briefing.

    Das Ergebnis steht im Log: wie viele Spieler noch ohne heutigen Wert in Kickbases Verlauf
    waren (Update nach 22:05) und wie viele Zeilen berichtigt wurden (Befund 01.10.2026)."""
    client, auth = await make_kickbase_client()
    league_id = auth.league_id
    if league_id is None:
        return None
    with SessionLocal() as session:
        result = await run_snapshot(client, session, league_id)
    logging.getLogger(__name__).info(
        "Snapshot: %d Spieler, %d Tageswerte, %d ohne heutigen Wert (Update noch nicht im "
        "Verlauf), %d heute aus dem Live-Wert, %d berichtigt, %d Verläufe nicht lesbar",
        result.players_upserted, result.history_rows_inserted, result.history_missing_today,
        result.history_today_from_live, result.history_rows_corrected, result.history_failed,
    )
    return result


async def run_history_reload_job() -> dict:
    """Wöchentliches Voll-Nachladen (Paket 1, P0-10): Marktwert- und Leistungshistorie
    ALLER bekannten Spieler, gedrosselt und rein lesend. Eine Fehlerserie macht den
    Lauf ehrlich rot (job_runs), statt ein halbes Ergebnis als Erfolg zu melden; eine
    Pause am Sperrfenster ist kein Fehler — der nächste Lauf setzt idempotent fort."""
    client, auth = await make_kickbase_client()
    league_id = auth.league_id
    if league_id is None:
        return {"status": "no_league"}
    with SessionLocal() as session:
        result = await run_full_history_reload(client, session, league_id=league_id)
    logging.getLogger(__name__).info("Historie-Nachladen: %s", result)
    if result["status"] == "aborted_failures":
        raise RuntimeError(
            f"Historie-Nachladen nach Fehlerserie abgebrochen: {result['done']} von "
            f"{result['players']} Spielern bearbeitet, {len(result['failed_ids'])} Fehler."
        )
    return result


def _format_quarantine(quarantines: list) -> str:
    """Grenzfall-Matches (< 90 %): NICHT genullt, aber zur Zuordnung melden."""
    rows = [
        f'• {q.kb_name} ↔ LI „{q.li_name}" ({q.li_reason}, {round(q.confidence * 100)}%)'
        for q in quarantines[:10]
    ]
    return (
        "🟠 **LI-Quarantäne** — unsichere Zuordnung, NICHT genullt. Bitte Kickbase-ID "
        "bestätigen:\n" + "\n".join(rows)
    )


async def run_briefing(channel: DiscordChannel, period: str | None = None) -> None:
    """Tägliches Briefing: Dashboard + Punkte-Plan + Trading-Fabrik als Discord-Karte.

    Morgens (08:15) die Tageszusammenfassung — die EINE Discord-Nachricht des Tages
    (Emil 23.09.2026, worker/daily_summary.py); abends (22:15) nur Archiv im Digest.
    """
    # Zeitstempel VOR dem Planbau (Review-Fund 17.09.): `briefing_slot_key` erlaubt
    # die Zustellung nur binnen 5 Minuten nach 08:15/22:15. Wurde `now` erst nach
    # build_plan()/Ranking genommen, kostete ein langsamer Planbau (LigaInsider-
    # Sweep, MILP) das Fenster — und im Digest-Modus verschwand der Bericht
    # still. Der Slot gehört zum Jobstart, nicht zum Ende der Rechnung.
    now = datetime.now(UTC)
    plan = await build_plan()
    client = await _make_client()
    ranking = await client.get_ranking(plan.league_id)
    player_ids = [sp.player.id for sp in plan.squad]
    with SessionLocal() as session:
        change = team_value_change(session, player_ids, datetime.now(UTC).date())
        trading = await build_trading_plan(client, session, plan.league_id, TradingConfig())
        rec = await recommend_lineup(client, session, plan.lineup_squad)
        completed, learnings = summarize_report_window(
            session, plan.league_id, briefing_window_start(now, period)
        )
    briefing = build_briefing(
        league_name=plan.league_name or plan.league_id,
        user_id=plan.user_id,
        ranking=ranking,
        squad=plan.squad,
        budget=plan.budget,
        day_change=change,
        plan=plan,
        trading=trading,
        lineup=rec.lineup,
        availabilities=rec.scout.availabilities,
        completed=completed,
        learnings=learnings,
    )
    await publish_briefing(channel, briefing, period=period, now=now)
    if rec.scout.quarantines:
        await channel.send_info(_format_quarantine(rec.scout.quarantines))


async def run_scan(channel: DiscordChannel) -> int:
    client, user_id = await _make_client_with_user()
    plan = await build_plan()
    with SessionLocal() as session:
        executor = ActionExecutor(client, session, user_id=user_id)
        staged = await stage_plan(executor, plan)
    return await send_staged(channel, staged)


async def on_approve(action_id: int):
    client, user_id = await _make_client_with_user()
    with SessionLocal() as session:
        return await ActionExecutor(
            client, session, user_id=user_id
        ).execute_staged(action_id)


async def on_reject(action_id: int):
    with SessionLocal() as session:
        return await ActionExecutor(client=None, session=session).reject_staged(action_id)


async def run(
    scan_now: bool = False,
    briefing_now: bool = False,
    pulse_now: bool = False,
    research_now: bool = False,
    lineup_now: bool = False,
    news_now: bool = False,
) -> None:
    settings = get_settings()
    if not settings.discord_bot_token or not settings.discord_channel_id:
        raise SystemExit("DISCORD_BOT_TOKEN und DISCORD_CHANNEL_ID müssen in .env gesetzt sein.")

    bot = discord.Client(intents=discord.Intents.default())
    channel = DiscordChannel(
        bot,
        settings.discord_channel_id,
        settings.discord_user_id,
        on_approve,
        on_reject,
        digest_only=settings.discord_digest_only,
    )
    scheduler = AsyncIOScheduler(timezone="Europe/Berlin")

    # Fehler-Buendel (Emil 29.08.): In der Nacht sperrte Kickbase die API waehrend
    # der Spieltags-Auswertung; jeder betroffene Job meldete einzeln — elf
    # Nachrichten fuer eine Ursache. Derselbe Fehler wird jetzt einmal gemeldet,
    # Wiederholungen gezaehlt, und wenn Ruhe eintritt kommt eine Bilanz.
    # Beide gehen ueber send_info OHNE kind: im Digest-Modus nur ins Archiv (Emil
    # 23.09.2026: eine Nachricht am Tag; Beleg tests/worker/test_daily_summary.py).
    fehler = FehlerBuendel()

    async def _safe(coro, label: str):
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001 — Fehler an Discord melden, Bot lebt weiter
            text = fehler.melden(label, exc)
            if text:
                await channel.send_info(text)
            else:
                logging.getLogger(__name__).warning(
                    "%s-Fehler (gebuendelt, schon gemeldet): %r", label, exc)
            return None

    async def _fehler_abschluss() -> None:
        """Abgeklungene Vorfaelle abschliessen — laeuft mit dem Puls."""
        for text in fehler.abgeklungen():
            await channel.send_info(text)

    def scheduled(label: str, factory):
        return make_scheduled_job(_safe, label, factory)

    @bot.event
    async def on_ready() -> None:
        print(f"✅ Discord-Bot online: {bot.user}")
        # Intent-Veto-Buttons (Spec 2026-08-31): EIN DynamicItem-Regex-Handler
        # für alle Karten — restart-sicher ohne Reload-Listen. Der Callback
        # öffnet SessionLocal und ruft den gemeinsamen Aktions-Service
        # (intents/actions.py); fail-closed auf Emils DISCORD_USER_ID.
        intent_cards.configure(
            allowed_user_id=settings.discord_user_id,
            on_intent_action=handle_intent_action,
        )
        bot.add_dynamic_items(intent_cards.IntentButton)
        # Restart-sicher (Review-Fund): Approve-Buttons noch gültiger Vorschläge
        # aus der Zeit VOR dem Neustart wieder bedienbar machen.
        await _safe(reload_approval_views(channel), "Approval-Reload")
        await _safe(run_bonus(channel), "Prämie")
        await _safe(schedule_kickoff_guards(scheduler, scheduled, channel), "Anpfiff-Planer")
        await _safe(schedule_late_gap_check(scheduler, scheduled, channel), "Elf-Lücke T-8")
        if briefing_now:
            await _safe(run_briefing(channel), "Briefing")
        if pulse_now:
            posted = await _safe(run_pulse(channel, "Test"), "Puls")
            if posted is False:
                await channel.send_info("🔕 Puls-Test: aktuell kein Handlungsbedarf (leise).")
        if research_now:
            await _safe(run_research(channel), "KI-Recherche")
        if lineup_now:
            await _safe(run_lineup_set(channel), "Auto-Aufstellung")
        if news_now:
            await _safe(run_news_update(channel), "News-Update")
        if scan_now:
            await _safe(run_scan(channel), "Scan")
        if not scheduler.running:
            # 22:05 Daten-Snapshot → Abendbriefing nach Kickbase-MW-Update.
            scheduler.add_job(
                scheduled("Snapshot", run_snapshot_job),
                CronTrigger(hour=22, minute=5, jitter=180),
            )
            for _briefing_hour, _briefing_minute in DAILY_BRIEFING_SLOTS:
                _period = "morning" if _briefing_hour < 12 else "evening"
                scheduler.add_job(
                    scheduled(
                        f"Briefing {_period}",
                        lambda period=_period: run_briefing(channel, period),
                    ),
                    daily_briefing_trigger(_briefing_hour, _briefing_minute),
                )
            # Markt-Puls ABGESCHALTET (Meldungs-Umbau, Emil 31.08.): „leise, nur
            # bei Handlungsbedarf" postete real fast jeden Slot (20×/Woche) und
            # erzählte dieselben Kandidaten, die der Planner 25 min später als
            # Veto-Karten anlegt — die Karten SIND der Puls. run_pulse bleibt
            # als Code für den manuellen Test-Pfad (pulse_now) erhalten.
            # Berichte-Aufräumen 03:33 täglich: flüchtige Arten (info, bid_watch,
            # planner, executor, schatten, auto_trading) und das Jev-Protokoll nach
            # 90 Tagen löschen — Substanz-Berichte und das Audit-Log sind NIE betroffen.
            scheduler.add_job(
                scheduled(
                    "Berichte-Aufräumen",
                    lambda: asyncio.to_thread(purge_archives),
                ),
                CronTrigger(hour=3, minute=33, jitter=300),
            )
            # Fehler-Abschluss (:17, stuendlich): meldet Vorfaelle, die sich gelegt
            # haben, mit Zahl und Dauer. Ohne diese Zeile waere ein Ausfall zwar
            # leise — aber niemand wuesste, wie gross er war und ob er vorbei ist.
            scheduler.add_job(
                scheduled("Fehler-Abschluss", _fehler_abschluss),
                CronTrigger(minute=17, jitter=60),
            )
            # Tägliche Prämie (Login-Bonus): 08:03 / 13:03 / 18:03. Die Idempotenz
            # lässt pro Tag nur EINE echte Gutschrift zu; ein leerer Griff ('noop',
            # Prämie noch nicht freigeschaltet) wird im nächsten Slot nachgeholt.
            # Der 18:03-Slot warnt, falls bis dahin nichts gutgeschrieben wurde.
            for _hh in (8, 13, 18):
                scheduler.add_job(
                    scheduled(
                        f"Prämie {_hh:02d}:03",
                        lambda late=(_hh == 18), first=(_hh == 8): run_bonus(
                            channel, warn_on_noop=late, primary=first
                        ),
                    ),
                    CronTrigger(hour=_hh, minute=3, jitter=120),
                )
            # 07:03 Anpfiff-Wächter planen (dynamisch aus echten Kickoff-Zeiten —
            # englische Wochen inklusive; ersetzt starre Freitags-Annahmen).
            scheduler.add_job(
                scheduled("Anpfiff-Planer",
                          lambda: schedule_kickoff_guards(scheduler, scheduled, channel)),
                CronTrigger(hour=7, minute=3, jitter=120),
            )
            # Später Elf-Lücken-Check (T-8, L-109): eigener Planer, damit ein Zuschlag
            # oder Notverkauf kurz vor dem Anpfiff noch in der Aufstellung landet —
            # bewusst NICHT in schedule_kickoff_guards (versiegelte Kaskade).
            scheduler.add_job(
                scheduled("Elf-Lücke-Planer",
                          lambda: schedule_late_gap_check(scheduler, scheduled, channel)),
                CronTrigger(hour=7, minute=8, jitter=60),
            )
            # 21:33 Closing-Bell: Langzeit-Verletzte VOR dem 22-Uhr-MW-Update melden.
            scheduler.add_job(
                scheduled("Closing-Bell", lambda: run_closing_bell(channel)),
                CronTrigger(hour=21, minute=33, jitter=60),
            )
            # News-Update 2×/Tag: ① Lage (News/Spieler/Liga-Feed) ② Schlussfolgerungen.
            for _hh, _mm in ((10, 33), (19, 7)):
                scheduler.add_job(
                    scheduled("News-Update", lambda: run_news_update(channel)),
                    CronTrigger(hour=_hh, minute=_mm, jitter=240),
                )
            # Freitag 19:30: LigaInsider-Lineup-Leak → finale Startelf + Verletzungs-Alarm.
            scheduler.add_job(
                scheduled("Lineup-Leak", lambda: run_lineup_leak(channel)),
                CronTrigger(day_of_week="fri", hour=19, minute=30, jitter=120),
            )
            # Auto-Aufstellung: Fr 19:45 (nach dem Leak) + Sa/So 10:00 (für Spieltage,
            # die erst Sa/So starten). GATED: läuft der Spieltag schon, ist die Elf seit
            # dem ersten Anpfiff eingefroren → Job überspringt still (Log, kein Fehler).
            # Echt gesetzt nur mit AGENT_AUTO_LINEUP=1, sonst Dry-Run-Post.
            scheduler.add_job(
                scheduled("Auto-Aufstellung", lambda: run_lineup_set_gated(channel)),
                CronTrigger(day_of_week="fri", hour=19, minute=45, jitter=60),
            )
            scheduler.add_job(
                scheduled("Auto-Aufstellung", lambda: run_lineup_set_gated(channel)),
                CronTrigger(day_of_week="sat,sun", hour=10, minute=0, jitter=300),
            )
            # Werktags-Refresh (31.08., Momentum-Doktrin): Nach einem Zuschlag
            # unter der Woche steht der Neuzugang auf der Bank, während der
            # schwächere Alt-Spieler in der GESPEICHERTEN Elf bleibt — der
            # Starter-Schutz des Executors blockt dann jeden autonomen Verkauf
            # bis Freitag. Ein täglicher Refresh Mo–Do hält die gespeicherte
            # Elf auf Empfehlungs-Stand und macht Pfadlose wieder verkaufbar;
            # mitten in der Woche kostet das nichts (Deadline-Freeze greift
            # erst am Spieltag, das Gate im Job bleibt unverändert).
            scheduler.add_job(
                scheduled("Auto-Aufstellung (werktags)", lambda: run_lineup_set_gated(channel)),
                CronTrigger(day_of_week="mon-thu", hour=18, minute=40, jitter=120),
            )
            # Elf-Lücken-Wächter (:12/:42): Ein Abgang NACH dem letzten Aufstellen
            # hinterlässt einen leeren Platz, den bis zum nächsten festen Slot
            # niemand schliesst — am 27.08. real passiert (12:03 Elf gesetzt,
            # 12:05 Hoffmeier verkauft, Loch bis zum Zufallsfund zwei Stunden
            # später). Kickbase zieht je leerem Platz 100 Punkte ab. Der Check
            # kostet einen API-Aufruf; nur bei echter Lücke folgt die teure
            # Neuberechnung. Eigenes Label (nicht "Auto-Aufstellung"): doppelte
            # Job-Namen haben den Cron-Wächter schon einmal blind gemacht.
            scheduler.add_job(
                scheduled("Elf-Lücken-Wächter", lambda: run_lineup_gap_check(channel)),
                CronTrigger(minute="12,42", jitter=60),
            )
            # 08:30 KI-Rechercheur (Phase 2) — nur wenn bewusst auf dem Server aktiviert
            # (API-Guthaben). Standard: läuft lokal per `kba research` (claude-CLI/Abo).
            if settings.ai_research_on_server:
                scheduler.add_job(
                    scheduled("KI-Recherche", lambda: run_research(channel)),
                    CronTrigger(hour=8, minute=30, jitter=300),
                )
            # Gebots-Wächter (:47) ABGEMELDET (Paket 1, P0-6, 21.09.2026): Er erinnerte
            # an eine Eskalation, die die Saisonengine nicht mehr kennt (Gebote
            # entstehen aus geprüften Szenarien, nicht aus pauschalen Aufschlägen).
            # worker/bid_watch.py bleibt als Code erhalten; kein .env-Schalter.
            # Ergebnis-Wächter (Paket 1, P0-4), viertelstündlich: misst das ERGEBNIS
            # (offene Plätze über dem Gleitpfad, Deckung Konto − Gebote, Konto > 24 h
            # unter dem Puffer, Statuswechsel eines Spielers > 30 % Teamwert) statt der
            # Job-Gesundheit — 1049 ok-Läufe bei Kader 8 und 94 h bei −8,6 M blieben
            # sonst still. Befunde gehen in die Tageszusammenfassung; sofort nur Deckung
            # und Gleitpfad binnen 24 h vor der Frist (Emil 23.09.2026).
            # Rein lesend und meldend; eigener Job, bewusst KEINE fünfte
            # Stufe in schedule_kickoff_guards (versiegelte Kaskade) und nicht im
            # Cron-Wächter. Minuten neben Planner (:x3), Executor (*/5), Elf-Lücke.
            scheduler.add_job(
                scheduled("Ergebnis-Wächter", lambda: run_outcome_guard(channel)),
                CronTrigger(minute="9,24,39,54", jitter=45),
            )
            # Steiger-Radar 08:10 täglich (Emil, 21.09.2026): rein lesender Bericht
            # mit bis zu fünf Marktwert-Steigern (>= +10 % in 3 Tagen, 1-10 M, fit);
            # gekauft wird nur auf Emils namentlichen Auftrag je Spieler. Seit 23.09.
            # sendet er nicht selbst: VOR dem Briefing 08:15, damit die
            # Tageszusammenfassung seine Kandidaten schon enthält.
            # misfire_grace_time (Review 30.09.2026): Standard ist 1 s — ein kurz
            # blockierter Event-Loop um 08:10/08:11 hieße sonst „heute kein Radar".
            scheduler.add_job(
                scheduled("Steiger-Radar", lambda: run_steiger_radar(channel)),
                CronTrigger(hour=8, minute=10, jitter=60),
                misfire_grace_time=120,
            )
            # News-Radar 08:11 täglich (Emil 30.09.2026, Schritt 4 des Jev-Umbaus): rein
            # lesend — Kaufsignale (klare Rückkehr am Transfermarkt) und Kader-Warnungen
            # (klares „fehlt") aus kicker per Jev; gekauft wird nur auf Emils Auftrag je
            # Spieler. Speichert nur, die Tageszusammenfassung 08:15 übernimmt die Zeilen.
            scheduler.add_job(
                scheduled("News-Radar", lambda: run_news_radar(channel)),
                CronTrigger(hour=8, minute=11, jitter=30),
                misfire_grace_time=120,
            )
            # Handels-Radar 08:12 täglich (Emil 01.10.2026: „ja bau den Handels-Radar“): rein
            # lesend — kaufbare Steiger mit Höchstgebot, Handelsspieler mit Verkaufssignal,
            # Konto bis zur Spieltags-Frist. Nach dem News-Radar (Jev-Rückkehrer) und ohne
            # Jitter, damit er sicher vor der Tageszusammenfassung 08:15 fertig ist.
            scheduler.add_job(
                scheduled("Handels-Radar", lambda: run_trade_radar(channel)),
                CronTrigger(hour=8, minute=12),
                misfire_grace_time=60,
            )
            # Zweiter Radar 22:35 (Handels-Autopilot, Emil 01.10.2026): nach dem
            # Marktwert-Update 22:00 und dem Snapshot 22:05 — der Autopilot bewertet die
            # Gebote der Nacht und des Vormittags mit den neuen Werten.
            scheduler.add_job(
                scheduled("Handels-Radar Abend", lambda: run_trade_radar(channel)),
                CronTrigger(hour=22, minute=35),
                misfire_grace_time=300,
            )
            # Handels-Autopilot :17/:47 (Emil 01.10.2026: „alles soll automatisch geschehen
            # ich will nicht dass ich bieten muss“): legt aus dem jüngsten Radar Gebots-
            # Vorhaben an, verkauft Handelsspieler nach dem ersten fallenden Update und am
            # Verkaufstermin. Kein Kickbase-Write — ausgeführt wird im Intent-Executor über
            # die Schreibstelle (Veto-Fenster, AGENT_AUTO_INTENTS).
            scheduler.add_job(
                scheduled("Handels-Autopilot", lambda: run_trade_autopilot(channel)),
                CronTrigger(minute="17,47"),
                misfire_grace_time=300,
            )
            # Dienstag 04:10 Voll-Nachladen (Paket 1, P0-10): rund 430 Spieler x 2
            # Abrufe, gedrosselt — weit weg vom Sperrfenster 21:25–22:25 und EINEN Tag
            # vor den Modellvergleichen (Mi 05:10/05:30), die davon zehren.
            scheduler.add_job(
                scheduled("Historie-Nachladen", run_history_reload_job),
                CronTrigger(day_of_week="tue", hour=4, minute=10, jitter=300),
            )
            # :52 stündlich: Reconcile (Phase 2) — 'unknown'/hängende 'pending'
            # gegen den echten Kickbase-Zustand auflösen; Alarm nur bei Unklärbarem.
            scheduler.add_job(
                scheduled("Reconcile", lambda: run_reconcile(channel)),
                CronTrigger(minute=52, jitter=120),
            )
            # 22:20 Schatten-Decision-Scan (Phase-2-Generalprobe, nach dem
            # 22:00-MW-Update): strukturierte Proposals + „hätte getan"-Report.
            scheduler.add_job(
                scheduled("Schatten-Scan", lambda: run_decision_scan(channel)),
                CronTrigger(hour=22, minute=20, jitter=120),
            )
            # Auto-Trading 22:30 ABGEMELDET (Paket 1, P0-6, 21.09.2026): ein zweiter
            # Handelspfad neben dem einen Vorhaben-Pfad, seit Wochen nur Schatten.
            # worker/auto_trading.py bleibt als Code erhalten; AGENT_AUTO_TRADING hat
            # damit im Worker keine Wirkung mehr.
            # Steuerzentrale (Vorhaben mit Veto-Fenster, worker/intents.py):
            # Alle zehn Minuten Zustand prüfen; bei Veränderung bzw. spätestens
            # nach einer Stunde neu planen. Ein gemeinsamer Vorhaben-Pfad.
            # Alle 5 min Intent-Executor — fällige
            # Vorhaben über den ActionExecutor (rule_id 'intent-*', geärmt nur
            # mit AGENT_AUTO_INTENTS=1; sonst ehrlich refused im Vorhaben).
            scheduler.add_job(
                scheduled("Intent-Planner", lambda: run_engine_cycle(channel)),
                CronTrigger(minute="3,13,23,33,43,53"),
            )
            scheduler.add_job(
                scheduled("Intent-Executor", lambda: run_intent_executor(channel)),
                CronTrigger(minute="*/5"),
            )
            # Neue Daten wöchentlich gegen die unveränderte Basis prüfen.
            # Der Job speichert Kandidaten und Messwerte, aktiviert aber kein Modell.
            scheduler.add_job(
                scheduled("Modellvergleich", run_forecast_training_job),
                CronTrigger(day_of_week="wed", hour=5, minute=10, jitter=120),
            )
            scheduler.add_job(
                scheduled("Gebotsmodellvergleich", run_competition_training_job),
                CronTrigger(day_of_week="wed", hour=5, minute=30, jitter=120),
            )
            # Stündlicher Cockpit-Build für localhost:8080 — nur aktiv,
            # wenn COCKPIT_HTML_PATH gesetzt ist (Box). Browser-Refresh zeigt
            # dann immer den letzten fertigen Stand (nginx: Cache no-store).
            if settings.cockpit_html_path:
                from kickbase_agent.worker.cockpit_job import run_cockpit_build

                # Tagsueber (7-23 Uhr) alle 30 min, nachts stuendlich — Emil
                # (23.08.): frischere Web-App; MW aendert sich eh nur um 22:00,
                # untertags zaehlen Listings/Gebote/Zuschlaege.
                scheduler.add_job(
                    scheduled("Cockpit-Build", run_cockpit_build),
                    CronTrigger(hour="7-23", minute="22,52", jitter=120),
                )
                scheduler.add_job(
                    scheduled("Cockpit-Build (Nacht)", run_cockpit_build),
                    CronTrigger(hour="0-6", minute=22, jitter=120),
                )
            # Prozessstart am Scheduler vermerken: Der Wächter darf Jobs, die
            # seit dem Start noch gar nicht dran waren, nicht als Ausfall melden.
            scheduler.kba_started_at = datetime.now(UTC)
            # Cron-Wächter (Emils Regel 25.08.): stündlich :38 prüfen, ob alle
            # registrierten Jobs laut Heartbeat planmäßig gelaufen sind. Meldet
            # NUR bei Problemen; die Morgen-Zusammenfassung kommt um 07:38.
            scheduler.add_job(
                scheduled(
                    "Cron-Wächter",
                    lambda: job_health.run_job_watchdog(scheduler, channel),
                ),
                CronTrigger(minute=38, jitter=120),
            )
            scheduler.add_job(
                scheduled(
                    "Cron-Wächter (Morgen)",
                    lambda: job_health.run_job_watchdog(scheduler, channel, always=True),
                ),
                CronTrigger(hour=7, minute=38, jitter=60),
            )
            # Stündlich :57 Gebots-Nachschau: Erwartung gegen Zuschlag buchen
            # und melden, wenn die Schätzung systematisch danebenliegt (25.08.).
            scheduler.add_job(
                scheduled("Gebots-Nachschau", lambda: run_bid_review(channel)),
                CronTrigger(minute=57, jitter=120),
            )
            # 07:20 Lern-Belege sammeln (Lern-Bilanz, Spec 2026-08-25): Auto-Regeln
            # ueber die Spuren der Nacht laufen lassen. Meldet NUR Statuswechsel —
            # eine taegliche „alles wie gestern"-Zeile wuerde die echten Wechsel
            # begraben. Liegt vor dem 07:38-Cron-Waechter, damit die Morgenlage
            # in einem Rutsch im Kanal steht.
            scheduler.add_job(
                scheduled(EVIDENCE_LABEL, lambda: run_evidence_job(channel)),
                CronTrigger(hour=7, minute=20, jitter=120),
            )
            # Sonntags 18:03: Erinnerung an Brief + Plan (Emils Wahl 25.08.).
            scheduler.add_job(
                scheduled("Retro-Erinnerung", lambda: run_retro_reminder(channel)),
                CronTrigger(day_of_week="sun", hour=18, minute=3, jitter=120),
            )
            # --- Autonomer Lernzyklus (26.08.) ---
            # :12 stündlich Einfrier-Wächter: Tagesverlust und unklare Writes
            # prüfen. Die Auslöser entstehen über den Tag verteilt — eine
            # Sicherung, die erst am nächsten Morgen greift, ist keine.
            scheduler.add_job(
                scheduled("Einfrier-Wächter", lambda: run_freeze_watch(channel)),
                CronTrigger(minute=12, jitter=120),
            )
            # Überraschungen (06:40/23:40) ABGEMELDET (Paket 1, P0-6, 21.09.2026):
            # 10 von 10 Läufen rot — das entwertete failed_window_total als Signal.
            # worker/surprise_job.py bleibt als Code erhalten.
            scheduler.start()

    await bot.start(settings.discord_bot_token)


def main() -> None:
    # Ein lokaler LaunchAgent darf nicht versehentlich einen zweiten Scheduler
    # neben dem produktiven Box-Worker starten.
    if os.environ.get("KBA_WORKER_RUNTIME") != "box" and "--local-dev" not in sys.argv:
        raise SystemExit(
            "kba-worker startet nur im Server-Container; lokal nur mit --local-dev."
        )

    # Nach stdout (journald/docker logs fangen es). Zeitstempel + Level, damit ein
    # 22:15-API-WRITE-Timeout sofort auffindbar ist.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(
        run(
            scan_now="--scan-now" in sys.argv,
            briefing_now="--briefing-now" in sys.argv,
            pulse_now="--pulse-now" in sys.argv,
            research_now="--research-now" in sys.argv,
            lineup_now="--lineup-now" in sys.argv,
            news_now="--news-now" in sys.argv,
        )
    )
