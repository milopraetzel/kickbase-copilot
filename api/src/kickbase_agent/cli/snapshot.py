"""`kba snapshot` — Tages-Snapshot von Kader + Markt in die DB schreiben.
`kba history-reload` — Voll-Nachladen der Marktwert-/Leistungshistorie aller Spieler."""

from __future__ import annotations

import asyncio
from typing import Any

import typer

from kickbase_agent.db.session import SessionLocal
from kickbase_agent.jobs.snapshot import SnapshotResult, run_full_history_reload, run_snapshot
from kickbase_agent.kickbase.auth import authenticate
from kickbase_agent.kickbase.client import KickbaseClient


def snapshot_command(
    league_id: str | None = typer.Option(
        None, "--league", "-l", help="Kickbase-Liga-ID (Default: erste Liga des Accounts)"
    ),
    backfill_timeframe: int = typer.Option(
        92, "--timeframe", "-t", help="Backfill-Zeitraum der MV-Historie in Tagen"
    ),
) -> None:
    async def _run() -> tuple[str, SnapshotResult]:
        auth = await authenticate()
        effective_league = league_id or (auth.leagues[0].id if auth.leagues else None)
        if effective_league is None:
            raise typer.BadParameter("Keine Liga gefunden — --league/-l angeben.")
        client = KickbaseClient(token=auth.token)
        with SessionLocal() as session:
            result = await run_snapshot(
                client, session, effective_league, backfill_timeframe=backfill_timeframe
            )
        return effective_league, result

    effective_league, result = asyncio.run(_run())
    typer.echo(
        f"✅ Snapshot Liga {effective_league}: {result.players_upserted} Spieler, "
        f"{result.history_rows_inserted} Tageswerte, "
        f"{result.backfill_rows_inserted} Backfill-Werte, "
        f"{result.history_rows_corrected} berichtigt, "
        f"{result.history_missing_today} ohne heutigen Wert (Update noch nicht da), "
        f"{result.history_today_from_live} heute aus dem Live-Wert, "
        f"{result.history_failed} Verläufe nicht lesbar, "
        f"{result.performance_rows_inserted} Spieltags-Punkte"
    )


def history_reload_command(
    league_id: str | None = typer.Option(
        None, "--league", "-l", help="Kickbase-Liga-ID (Default: erste Liga des Accounts)"
    ),
    pause_seconds: float = typer.Option(
        0.35, "--pause", help="Drosselung zwischen zwei Spielern in Sekunden"
    ),
    check_only: bool = typer.Option(
        False, "--check",
        help="Nur zählen: was fehlt und was von Kickbases Verlauf abweicht — schreibt nichts",
    ),
) -> None:
    """Voll-Nachladen einmal von Hand (Paket 1, P0-10): gedacht für den ersten Lauf direkt
    nach dem Deploy — der Wochenjob läuft erst dienstags 04:10. Rein lesend gegen
    Kickbase, idempotent, verweigert im Sperrfenster 21:25–22:25 Berlin. Berichtigt Zeilen,
    die von Kickbases Verlauf abweichen (Befund 01.10.2026); `--check` zählt nur."""

    async def _run() -> tuple[str, dict[str, Any]]:
        auth = await authenticate()
        effective_league = league_id or (auth.leagues[0].id if auth.leagues else None)
        if effective_league is None:
            raise typer.BadParameter("Keine Liga gefunden — --league/-l angeben.")
        client = KickbaseClient(token=auth.token)
        with SessionLocal() as session:
            result = await run_full_history_reload(
                client, session, league_id=effective_league, pause_seconds=pause_seconds,
                check_only=check_only,
            )
        return effective_league, result

    effective_league, result = asyncio.run(_run())
    status = result["status"]
    if status == "blocked_window":
        typer.echo("Sperrfenster 21:25–22:25 Berlin — kein Abruf. Danach erneut starten.")
        raise typer.Exit(code=1)
    unfinished = (
        f" — nicht fertig: {result['done']} von {result['players']} Spielern bearbeitet, "
        f"{result['remaining']} offen" if "done" in result else ""
    )
    if result.get("check_only"):
        typer.echo(
            f"Historie-Prüfung Liga {effective_league} ({status}, Prüfung, nichts geschrieben): "
            f"{result['players']} Spieler, {result['history_rows_added']} Tageswerte fehlen, "
            f"{result['history_rows_corrected']} würden berichtigt, "
            f"{len(result['failed_ids'])} nicht lesbar{unfinished}"
        )
        if status != "ok":
            raise typer.Exit(code=1)
        return
    typer.echo(
        f"Historie-Nachladen Liga {effective_league} ({status}): {result['players']} Spieler, "
        f"{result['history_rows_added']} Tageswerte, "
        f"{result.get('history_rows_corrected', 0)} berichtigt, "
        f"{result['performance_rows_added']} Spieltage neu, "
        f"{result['performance_rows_updated']} aktualisiert, "
        f"{len(result['failed_ids'])} fehlgeschlagen{unfinished}"
    )
    if status != "ok":
        raise typer.Exit(code=1)
    if status != "ok":
        # paused_window / aborted_failures: ein zweiter Lauf setzt idempotent fort.
        raise typer.Exit(code=1)
