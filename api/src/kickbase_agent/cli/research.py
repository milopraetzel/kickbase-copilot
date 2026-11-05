"""`kba research` — KI-Recherche über die lokale `claude`-CLI (Abo).

Für den lokalen launchd-Cron: nutzt die Abo-Anmeldung der `claude`-CLI statt
API-Guthaben. Läuft die OAuth-Session ab oder scheitert die Recherche gewöhnlich,
wird der Fehler intern mit `delivered=false` archiviert; nur explizite Blocker dürfen
im Digest-Modus Discord unterbrechen.

Berichte-Ablage: Rechercheergebnisse und per `kba post-discord` eingereichte Dateien
landen als Volltext in `reports` (Steuerzentrale, Zone „Berichte"). Die Ablage
bewahrt Routine und gewöhnliche Fehler auch dann, wenn Discord bewusst still bleibt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx
import typer

from kickbase_agent.config import get_settings
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.intel.collector import collect_from_players
from kickbase_agent.intel.history import load_history, load_performance
from kickbase_agent.intel.researcher import ClaudeAuthError, research_via_cli
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.news.kicker_rss import KickerRSSClient
from kickbase_agent.notify.report_store import (
    REPORT_KINDS,
    discord_delivery_allowed,
    save_report,
)
from kickbase_agent.scouts.ligainsider.service import enrich_candidates
from kickbase_agent.worker.research import format_research, profiteur_lines_from_scout


async def _post_discord(text: str, *, kind: str = "info") -> bool:
    """Eine Nachricht per Discord-REST (Bot-Token) — ohne Gateway-Verbindung.

    True = bei Discord angekommen; False = kein Discord konfiguriert (Text nach stdout).
    """
    settings = get_settings()
    if not discord_delivery_allowed(kind, settings.discord_digest_only):
        return False
    if not (settings.discord_bot_token and settings.discord_channel_id):
        typer.echo(text)  # kein Discord konfiguriert → nach stdout
        return False
    url = f"https://discord.com/api/v10/channels/{settings.discord_channel_id}/messages"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bot {settings.discord_bot_token}"},
            json={"content": text[:1990]},
        )
        resp.raise_for_status()
    return True


def _store_report(
    text: str, kind: str, source: str, delivered: bool, **extra: object
) -> int | None:
    """Volltext in `reports` ablegen (Zone „Berichte"); None, wenn die DB nicht mitspielt."""
    return save_report(
        kind=kind,
        title=None,
        body=text,
        source=source,
        meta={"delivered": delivered, "chars": len(text.strip()), **extra},
    )


def _archive_research_failure(text: str, exc: Exception) -> int | None:
    """Gewöhnliche Recherchefehler intern belegen, ohne Discord zu unterbrechen."""
    return _store_report(
        text,
        "recherche",
        "cli:research",
        False,
        error_type=type(exc).__name__,
    )


async def _run() -> None:
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        raise typer.BadParameter("Keine Liga gefunden.")

    squad = await client.get_squad(lg)
    market = await client.get_market(lg)
    # Dedupe: gelistete eigene Spieler stehen in Kader UND Markt (Hermes-Audit
    # fand 16 statt 14 Kader-Zeilen) — die Kader-Version gewinnt.
    players = [sp.player for sp in squad]
    seen = {p.id for p in players}
    players += [mp.player for mp in market if mp.player.id not in seen]
    ids = [p.id for p in players]
    fixtures = await client.get_matchdays()
    team_names = await client.get_team_names()
    with SessionLocal() as session:
        history = load_history(session, ids)
        perf = load_performance(session, ids)
    candidates = await collect_from_players(players, fixtures, team_names, history, perf)
    scout = await enrich_candidates(candidates)
    candidates = scout.candidates
    plines = profiteur_lines_from_scout(scout, {mp.player.id for mp in market})

    try:
        news = await KickerRSSClient().fetch()
    except Exception:  # noqa: BLE001 — News-Ausfall darf die Recherche nicht killen
        news = []

    try:
        briefing = await research_via_cli(candidates, news, profiteur_lines=plines)
    except ClaudeAuthError as exc:
        message = (
            "KI-Recherche pausiert: claude-CLI nicht angemeldet (OAuth abgelaufen). "
            "Re-Login erforderlich."
        )
        _archive_research_failure(message, exc)
        typer.echo("claude-CLI nicht angemeldet — Re-Login nötig.")
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001
        message = f"KI-Recherche fehlgeschlagen: {type(exc).__name__}: {exc}"
        _archive_research_failure(message, exc)
        typer.echo(message)
        raise typer.Exit(1) from exc

    text = format_research(briefing)
    delivered = await _post_discord(text, kind="recherche")
    _store_report(text, "recherche", "cli:research", delivered)
    typer.echo(
        "KI-Recherche nach Discord gepostet."
        if delivered
        else "KI-Recherche archiviert; Discord-Digest bleibt still."
    )


def research_command() -> None:
    """KI-Recherche via lokaler claude-CLI (Abo) → Discord."""
    asyncio.run(_run())


async def _dump_data(echo: Callable[[str], object] = typer.echo) -> None:
    """Recherche-ROHDATEN kompakt ausgeben (für eine Claude-Session als Analyst).

    `echo` ist der Ausgabekanal: Default typer.echo (stdout für `kba research-data`);
    der MCP-Server (`research_data`-Tool) sammelt die Zeilen stattdessen ein —
    stdout gehört dort dem Protokoll.
    """
    from kickbase_agent.scouts.ligainsider.teams import TEAMS
    from kickbase_agent.trading.orchestrator import build_trading_plan
    from kickbase_agent.trading.pipeline import TradingConfig

    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        raise typer.BadParameter("Keine Liga gefunden.")
    squad = await client.get_squad(lg)
    market = await client.get_market(lg)
    budget = await client.get_budget(lg)
    # Dedupe wie in _run: gelistete eigene Spieler nicht doppelt scoren.
    players = [sp.player for sp in squad]
    _seen = {p.id for p in players}
    players += [mp.player for mp in market if mp.player.id not in _seen]
    ids = [p.id for p in players]
    fixtures = await client.get_matchdays()
    team_names = await client.get_team_names()
    with SessionLocal() as session:
        history = load_history(session, ids)
        perf = load_performance(session, ids)
        trading = await build_trading_plan(client, session, lg, TradingConfig())
    cands = await collect_from_players(players, fixtures, team_names, history, perf)
    scout = await enrich_candidates(cands)
    status = {a.player_id: a.status.value for a in scout.availabilities}
    squad_ids = {sp.player.id for sp in squad}
    # Kickbase-Member-Startelf-Stufe (1=sicher … 5=Bank; None ohne Abo) als
    # Zweitmeinung neben LI — Skala bis SP1 unvalidiert, daher reine Anzeige.
    kb_prob = {sp.player.id: sp.player.lineup_prob for sp in squad}
    kb_prob.update({mp.player.id: mp.player.lineup_prob for mp in market})

    def _line(c) -> str:
        team = TEAMS.get(c.team_id)
        prob = kb_prob.get(c.player_id)
        kb = f"KB:St.{prob}" if prob is not None else "KB:—"
        return (
            f"{c.name} | {team.kb_name if team else c.team_id} | Pos{c.position} | "
            f"MW {c.current_market_value / 1e6:.1f}M | Score {c.total_score:.2f} | "
            f"LI:{status.get(c.player_id, '?')} | {kb}"
        )

    from kickbase_agent.trading.trade_budget import budget_line, get_trade_usage
    usage = await get_trade_usage(client, lg, auth.user_id)
    echo(f"BUDGET: {budget / 1e6:.1f}M € | PHASE: {trading.phase} | "
         f"KAUFKRAFT: {trading.overdraft_available / 1e6:.1f}M | "
         f"SLOTS: {trading.open_slots} | "
         + budget_line(usage.used, 200, usage.count_saturated))
    echo("\n== KADER ==")
    for c in sorted((c for c in scout.candidates if c.player_id in squad_ids),
                    key=lambda x: -x.total_score):
        echo(_line(c))
    echo("\n== MARKT ==")
    for c in sorted((c for c in scout.candidates if c.player_id not in squad_ids),
                    key=lambda x: -x.total_score):
        echo(_line(c))
    echo("\n== PROFITEURE ==")
    for ln in profiteur_lines_from_scout(scout, {mp.player.id for mp in market}):
        echo(f"- {ln}")
    try:
        news = await KickerRSSClient().fetch()
        echo("\n== NEWS ==")
        for a in news[:15]:
            echo(f"- {a.title}")
    except Exception:  # noqa: BLE001
        echo("\n== NEWS == (nicht erreichbar)")
    try:
        from kickbase_agent.news.community import fetch_community
        items = await fetch_community()
        echo("\n== COMMUNITY (YouTube/Reddit — Profi-/Insider-Titel) ==")
        for it in items[:14]:
            echo(f"- [{it.source}] {it.title[:100]}")
        if not items:
            echo("(keine Community-Daten erreichbar)")
    except Exception:  # noqa: BLE001
        echo("\n== COMMUNITY == (nicht erreichbar)")
    from kickbase_agent.intel.community import load_recent_tips
    with SessionLocal() as session:
        tips = load_recent_tips(session)
    echo("\n== AKTIVE TIPPS (fliessen bereits in Kauf-Scores) ==")
    for t in tips:
        echo(f"- {t.player_name} {t.weight:+.1f} [{t.source}] {t.note[:60]}")
    if not tips:
        echo("(keine)")


def research_data_command() -> None:
    """Recherche-Rohdaten (Kader/Markt/Profiteure/News) nach stdout."""
    asyncio.run(_dump_data())


def post_discord_command(
    file: str = typer.Argument(..., help="Datei mit der Nachricht (Markdown/Text)"),
    kind: str = typer.Option(
        "recherche",
        "--kind",
        help="Berichtsart für die Ablage in `reports`: " + " | ".join(REPORT_KINDS),
    ),
) -> None:
    """Dateiinhalt als Nachricht in den Discord-Kanal posten und als Bericht ablegen
    (Zone „Berichte", source cli:post-discord; Default --kind recherche)."""
    if kind not in REPORT_KINDS:
        raise typer.BadParameter(f"Unbekannte Berichtsart {kind!r} — erlaubt: "
                                 + ", ".join(REPORT_KINDS))
    text = open(file, encoding="utf-8").read().strip()
    if not text:
        raise typer.BadParameter("Datei ist leer.")
    delivered = asyncio.run(_post_discord(text, kind=kind))
    report_id = _store_report(text, kind, "cli:post-discord", delivered, file=Path(file).name)
    verb = "gepostet" if delivered else "archiviert; Discord-Digest bleibt still"
    if report_id is None:
        typer.echo(f"{verb}. (Bericht NICHT gespeichert — DB prüfen, Log lesen)")
    else:
        typer.echo(f"{verb}. Bericht #{report_id} ({kind}) gespeichert.")
