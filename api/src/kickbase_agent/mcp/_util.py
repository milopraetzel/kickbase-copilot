"""Gemeinsame Bausteine der MCP-Tools: Geldformat, Fehler-Wrapper, ssh-Runner, Zeitfenster.

Alles hier ist nebenwirkungsfrei testbar; die einzige Prozess-Grenze ist
`run_cmd` (subprocess) — Tests biegen genau diese Funktion um.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import shlex
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase.errors import KickbaseAuthError, KickbaseDataError
from kickbase_agent.kickbase.models import Position
from kickbase_agent.scouts.ligainsider.teams import TEAMS

log = logging.getLogger("kickbase_agent.mcp")

BERLIN = ZoneInfo("Europe/Berlin")

#: Die n8n-Box, auf der der Container `kickbase-agent` 24/7 läuft (SKILL.md).
BOX_HOST = "root@203.0.113.10"
BOX_DIR = "/opt/kickbase-agent"
CONTAINER = "kickbase-agent"
#: Lokales api/-Verzeichnis (…/Kickbase-Agent/api) — Quelle des rsync-Deploys.
API_DIR = Path(__file__).resolve().parents[3]

#: Deploy-Sperrfenster (Berlin): `compose up --build` um die Abend-Jobs herum
#: (21:33 Closing-Bell, 22:05 Snapshot, 22:15 Briefing) frisst die In-Memory-Crons
#: des APSchedulers (LEARNINGS 21.08.). Das Tool erzwingt das Fenster selbst.
DEPLOY_BLOCK_START = dtime(21, 25)
DEPLOY_BLOCK_END = dtime(22, 25)

#: Default-Timeouts (Sekunden) für Box-Kommandos.
SSH_TIMEOUT_S = 60
DEPLOY_TIMEOUT_S = 600
COCKPIT_TIMEOUT_S = 300

_POS_NAMES = {
    Position.GOALKEEPER: "TW",
    Position.DEFENDER: "ABW",
    Position.MIDFIELDER: "MIT",
    Position.STRIKER: "STU",
}


class ToolError(Exception):
    """Erwarteter Tool-Fehler mit Handlungs-Hinweis — wird zu {"error","hint"}."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


# ---------------------------------------------------------------- Formatierung


def fmt_money(value: int | float | None) -> str:
    """Lesbares Geld: 7_410_000 → „7,41 M", 850_000 → „850 k", 120 → „120 €"."""
    if value is None:
        return "—"
    v = int(value)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        text = f"{a / 1e6:.2f} M"
    elif a >= 1_000:
        text = f"{a / 1e3:.0f} k"
    else:
        text = f"{a} €"
    return sign + text.replace(".", ",")


def money_fields(prefix: str, value: int | float | None) -> dict[str, Any]:
    """`{prefix}_eur` (int) + `{prefix}_fmt` (lesbar) — das Rückgabeformat der Spec."""
    return {
        f"{prefix}_eur": None if value is None else int(value),
        f"{prefix}_fmt": fmt_money(value),
    }


def pos_name(position: Any) -> str:
    try:
        return _POS_NAMES[Position(int(position))]
    except (KeyError, TypeError, ValueError):
        return str(position)


LINEUP_LABELS: dict[int, str] = {
    1: "Sicher", 2: "Erwartet", 3: "Unsicher", 4: "Unwahrscheinlich", 5: "Ausgeschlossen",
}


def lineup_label(prob: Any) -> str | None:
    """Offizielle Kickbase-Member-Legende zur Startelf-Stufe (prob 1..5), sonst None."""
    return LINEUP_LABELS.get(prob) if isinstance(prob, int) else None


def club_name(team_id: Any) -> str:
    team = TEAMS.get(str(team_id))
    return team.kb_name if team else str(team_id or "")


def iso(dt: datetime | None) -> str | None:
    """Zeitstempel als ISO-String in Berliner Zeit (naive DB-Werte = UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(BERLIN).isoformat(timespec="seconds")


def berlin_now() -> datetime:
    return datetime.now(BERLIN)


def deploy_window_blocked(now: datetime | None = None) -> bool:
    """True innerhalb des Sperrfensters 21:25–22:25 Berlin (inklusive Ränder)."""
    local = (now or berlin_now()).astimezone(BERLIN).time().replace(tzinfo=None)
    return DEPLOY_BLOCK_START <= local <= DEPLOY_BLOCK_END


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def settings_flags() -> dict[str, bool]:
    """Die lokalen Arming-Flags als 0/1-Bild (keine Secrets) — für ehrliche Statusmeldungen."""
    s = get_settings()
    return {
        "agent_dry_run": bool(s.agent_dry_run),
        "agent_allow_live": bool(s.agent_allow_live),
        "agent_auto_bonus": bool(s.agent_auto_bonus),
        "agent_auto_lineup": bool(s.agent_auto_lineup),
        "agent_auto_purge": bool(s.agent_auto_purge),
        "agent_auto_trading": bool(s.agent_auto_trading),
        "test_league_configured": bool(s.agent_test_league_id),
    }


def audit_row(row: Any) -> dict[str, Any]:
    """Eine transfer_actions-Zeile kompakt (Audit-Log + Ergebnis der Write-Tools)."""
    return {
        "id": row.id,
        "created_at": iso(row.created_at),
        "resolved_at": iso(row.resolved_at),
        "type": row.action_type,
        "status": row.status,
        "dry_run": bool(row.dry_run),
        "rule_id": row.rule_id,
        "player_id": row.player_id,
        **money_fields("amount", row.amount),
        "rationale": (row.rationale or "")[:300],
        "league_id": row.league_id,
    }


# ---------------------------------------------------------------- Fehler-Wrapper


def hint_for(exc: BaseException) -> str:
    if isinstance(exc, ToolError):
        return exc.hint
    if isinstance(exc, KickbaseAuthError):
        return (
            "Kickbase-Login abgelehnt — KICKBASE_TOKEN/REFRESH_TOKEN in api/.env prüfen "
            "(kba login)."
        )
    if isinstance(exc, KickbaseDataError):
        return "Kickbase-Antwort ohne Pflichtfeld — Zustand unbekannt, Endpoint live proben."
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return f"HTTP {code} vom Server — Endpoint/Rate-Limit prüfen, später erneut versuchen."
    if isinstance(exc, httpx.TimeoutException | TimeoutError | subprocess.TimeoutExpired):
        return "Timeout — Netz/Box erreichbar? Später erneut versuchen."
    if isinstance(exc, httpx.HTTPError):
        return "Netzwerkfehler — Verbindung prüfen."
    if isinstance(exc, FileNotFoundError):
        return "Programm/Datei fehlt lokal (ssh/rsync installiert? Pfad korrekt?)."
    return "Details im stderr-Log des MCP-Servers (kba-mcp)."


def error_dict(exc: BaseException, hint: str | None = None) -> dict[str, Any]:
    return {"error": f"{type(exc).__name__}: {exc}", "hint": hint or hint_for(exc)}


def tool_guard[**P](
    fn: Callable[P, Awaitable[dict[str, Any]]],
) -> Callable[P, Awaitable[dict[str, Any]]]:
    """Fehler-Wrapper: Exceptions werden zu {"error","hint"} (Traceback nur nach stderr).

    Signatur/Docstring bleiben erhalten (functools.wraps) — FastMCP leitet das
    Tool-Schema aus der Originalfunktion ab.
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — das Tool meldet, statt zu crashen
            log.exception("Tool %s fehlgeschlagen", getattr(fn, "__name__", "?"))
            return error_dict(exc)

    return wrapper


# ---------------------------------------------------------------- Prozess-Runner


@dataclass(frozen=True)
class CmdResult:
    rc: int
    out: str
    err: str
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def tail(self, lines: int = 8, *, stream: str = "out") -> str:
        text = self.out if stream == "out" else self.err
        return "\n".join(text.strip().splitlines()[-lines:])


def run_cmd(argv: list[str], timeout: float) -> CmdResult:
    """Einziger subprocess-Aufruf des MCP-Servers (blockierend; Tests mocken ihn).

    Niemals über eine Shell auf dem Mac — Argumente bleiben Liste. Das
    Remote-Kommando (ssh) ist EIN String, den wir mit shlex.quote zusammenbauen.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 — feste Programme, gequotete Argumente
            argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CmdResult(
            rc=124,
            out=out,
            err=(err + f"\n[timeout nach {timeout:.0f} s]").strip(),
            duration_s=round(time.monotonic() - start, 1),
        )
    return CmdResult(
        rc=proc.returncode,
        out=proc.stdout,
        err=proc.stderr,
        duration_s=round(time.monotonic() - start, 1),
    )


def ssh_argv(remote_cmd: str, *, connect_timeout: int = 15) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        BOX_HOST,
        remote_cmd,
    ]


async def run_ssh(remote_cmd: str, timeout: float = SSH_TIMEOUT_S) -> CmdResult:
    """Kommando auf der Box ausführen (Thread, damit der Event-Loop frei bleibt)."""
    log.info("ssh %s: %s", BOX_HOST, remote_cmd[:160])
    return await asyncio.to_thread(run_cmd, ssh_argv(remote_cmd), timeout)


async def run_local(argv: list[str], timeout: float) -> CmdResult:
    log.info("local: %s", " ".join(shlex.quote(a) for a in argv)[:200])
    return await asyncio.to_thread(run_cmd, argv, timeout)


def docker_exec(cmd: str) -> str:
    """`docker exec kickbase-agent <cmd>` — cmd ist bereits ein fertiger Shell-String."""
    return f"docker exec {CONTAINER} {cmd}"


def q(value: Any) -> str:
    """shlex.quote für Remote-Kommandos (Spielernamen mit Apostroph, Notizen …)."""
    return shlex.quote(str(value))
