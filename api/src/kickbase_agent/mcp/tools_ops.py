"""Ops-Tools (Nebenwirkung, aber KEIN Kickbase-Write): Box-Status, Deploy, Cockpit,
Discord, Tipps, Lern-Belege.

Box-Kommandos laufen via `ssh root@203.0.113.10` (BatchMode, Timeouts); alle
Prozessaufrufe gehen durch `_util.run_cmd` — das ist der einzige Mock-Punkt der Tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from typing import Any

import httpx

from kickbase_agent.config import get_settings
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.intel.community import save_tip
from kickbase_agent.knowledge.evidence import record_agent_evidence
from kickbase_agent.mcp import _util
from kickbase_agent.mcp._util import (
    API_DIR,
    BOX_DIR,
    BOX_HOST,
    COCKPIT_TIMEOUT_S,
    CONTAINER,
    DEPLOY_BLOCK_END,
    DEPLOY_BLOCK_START,
    DEPLOY_TIMEOUT_S,
    CmdResult,
    ToolError,
    berlin_now,
    deploy_window_blocked,
    docker_exec,
    iso,
    q,
    tool_guard,
)
from kickbase_agent.notify.report_store import (
    DEFAULT_KIND,
    REPORT_KINDS,
    infer_kind,
    save_report,
)

#: Boolesche Arming-Flags des Containers (Settings) — nur diese werden als 0/1 gezeigt,
#: jede andere AGENT_-Variable nur als „set"/„empty" (nie der Wert; Invariante 2).
_BOOL_FLAGS = (
    "AGENT_DRY_RUN",
    "AGENT_ALLOW_LIVE",
    "AGENT_APPROVE_LIVE",
    "AGENT_AUTO_BONUS",
    "AGENT_AUTO_LINEUP",
    "AGENT_AUTO_PURGE",
    "AGENT_AUTO_TRADING",
    "AGENT_AUTO_INTENTS",
)
_TRUE = {"1", "true", "yes", "on"}
#: rsync-Ausschlüsse des Deploys (Spec: ohne .env/.git/.venv/DB).
RSYNC_EXCLUDES = (
    ".git",
    ".venv",
    "__pycache__",
    # SQLite legt neben der Datei die Journale `-shm`/`-wal` an, die auf
    # `*.db` NICHT matchen — sie landeten dadurch per rsync auf der Box
    # (gefunden 27.08., ohne zugehoerige DB daneben). Bewusst EXPLIZITE
    # Endungen statt `*.db*`: Das Sternchen haette auch `schema.dbml`,
    # `seed.db.py` oder `config.db-backup` geschluckt und solche Dateien
    # kuenftig lautlos vom Deploy ausgeschlossen (Codex-Fund 27.08.).
    "*.db",
    "*.db-journal",
    "*.db-shm",
    "*.db-wal",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".env",
    ".env.*",
)
DISCORD_MAX_CHARS = 1900
COCKPIT_URL = "https://localhost:8080"
#: Quelle der Berichte-Ablage für discord_post (reports.source).
REPORT_SOURCE = "mcp:discord_post"
#: Quelle des Deploy-Berichts (Paket 1, P0-9): Commit-Hash + Prüfsumme der Schreibstelle.
DEPLOY_REPORT_SOURCE = "mcp:deploy_verify"
#: Zweiter Container aus demselben Quellbaum (docker-compose: api → build ./api).
API_CONTAINER = "kickbase-api"
#: Pfad der Schreibstelle im Container — ihre Prüfsumme belegt den deployten Stand.
EXECUTOR_IN_CONTAINER = "/app/src/kickbase_agent/agent/executor.py"
#: Nur dieser Branch geht auf die Box, und nur wenn er origin entspricht.
DEPLOY_BRANCH = "main"
#: Box-Spiegelung: „Bericht #12 (recherche, mcp:discord_post) gespeichert." (kba report-add)
_BOX_REPORT_RE = re.compile(r"Bericht #(\d+)")


# ------------------------------------------------------------------ Helfer


def _sections(out: str) -> dict[str, list[str]]:
    """Ausgabe mit `@@name`-Markern in Abschnitte zerlegen."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in out.splitlines():
        if line.startswith("@@"):
            current = line[2:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line.rstrip())
    return sections


def _first_int(lines: list[str]) -> int | None:
    for line in lines:
        if line.strip().lstrip("-").isdigit():
            return int(line.strip())
    return None


def _flags(lines: list[str]) -> dict[str, Any]:
    """`AGENT_X=wert`-Zeilen → {AGENT_X: 0|1} für Bool-Flags, sonst nur Präsenz."""
    flags: dict[str, Any] = {}
    for line in lines:
        if "=" not in line or not line.startswith("AGENT_"):
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name in _BOOL_FLAGS:
            flags[name] = 1 if value.strip().lower() in _TRUE else 0
        else:
            flags[name] = "set" if value.strip() else "empty"
    return flags


def _box_probe_script() -> str:
    logs = f"docker logs {CONTAINER} 2>&1"
    return "; ".join(
        [
            f"echo @@status; docker ps --filter name=^/{CONTAINER}$ --format '{{{{.Status}}}}'",
            f"echo @@started; docker inspect -f '{{{{.State.StartedAt}}}}' {CONTAINER} 2>/dev/null",
            f"echo @@flags; {docker_exec('env')} 2>/dev/null | grep '^AGENT_' | sort",
            f"echo @@jobs; {logs} | grep -c 'Added job'",
            f"echo @@errors; {logs} | grep -ciE 'error|traceback'",
            f"echo @@bonus; {logs} | grep -iE 'type=bonus|pr..?mie|bonus' | tail -n 1",
        ]
    )


def _box_health(sections: dict[str, list[str]]) -> dict[str, Any]:
    status_lines = [ln for ln in sections.get("status", []) if ln.strip()]
    flags = _flags(sections.get("flags", []))
    jobs = _first_int(sections.get("jobs", []))
    errors = _first_int(sections.get("errors", []))
    bonus = [ln for ln in sections.get("bonus", []) if ln.strip()]
    started = [ln for ln in sections.get("started", []) if ln.strip()]
    running = bool(status_lines) and status_lines[0].lower().startswith("up")
    armed = " ".join(
        f"{k.removeprefix('AGENT_')}={v}" for k, v in flags.items() if k in _BOOL_FLAGS
    )
    return {
        "running": running,
        "container_status": status_lines[0] if status_lines else "nicht gefunden",
        "started_at": started[0] if started else None,
        "flags": flags,
        "jobs_added": jobs,
        "error_lines": errors,
        "last_bonus_line": bonus[-1][-300:] if bonus else None,
        "summary": (
            f"{'läuft' if running else 'STEHT'} · Jobs {jobs if jobs is not None else '?'} · "
            f"Fehler {errors if errors is not None else '?'} · {armed or 'keine AGENT_-Flags'}"
        ),
    }


def _failed(step: str, res: CmdResult, **extra: Any) -> dict[str, Any]:
    return {
        "status": "failed",
        "step": step,
        "rc": res.rc,
        "stdout_tail": res.tail(),
        "stderr_tail": res.tail(stream="err"),
        "duration_s": res.duration_s,
        **extra,
    }


# ------------------------------------------------------------------ box_status


@tool_guard
async def box_status() -> dict[str, Any]:
    """Box-Check per ssh: Container-Status, „Added job"-Zahl, Fehlerzeilen, AGENT_-Flags
    (nur 0/1, nie Werte/Secrets) und die letzte Prämien-Logzeile. Erwartung laut SKILL:
    läuft · 22 Jobs · 0 Fehler (Stand 23.08.2026)."""
    res = await _util.run_ssh(_box_probe_script())
    sections = _sections(res.out)
    if "status" not in sections:
        return _failed("ssh", res, hint="Box per ssh nicht erreichbar (Key/BatchMode/Netz)?")
    return {"status": "ok", "host": BOX_HOST, **_box_health(sections), "duration_s": res.duration_s}


# ------------------------------------------------------------------ deploy_verify


def _refused_git(reason: str, hint: str) -> dict[str, Any]:
    return {"ok": False, "status": "refused", "step": "git", "reason": reason, "hint": hint}


async def git_deploy_gate() -> dict[str, Any]:
    """Git-Riegel vor dem Deploy (Paket 1, P0-9). rsync schickt api/ so, wie es auf der
    Platte liegt — deshalb geht nur ein sauberer `main` auf die Box, der nach einem
    frischen fetch `origin/main` entspricht. Als Schmutz zählt jede Änderung an
    versionierten Dateien und jede unversionierte Datei unter api/ (rsync nähme sie
    mit). Unversionierte Dateien außerhalb von api/ erreichen die Box nie; sie werden
    nur ausgewiesen. Ein fehlgeschlagener fetch verweigert: ein veraltetes
    origin/main ist kein Beleg."""
    repo = str(API_DIR.parent)
    git = ["git", "-C", repo]
    status = await _util.run_local(
        [*git, "status", "--porcelain", "--untracked-files=all"], timeout=60
    )
    if not status.ok:
        return _refused_git(
            f"git status fehlgeschlagen: {status.tail(2, stream='err')}",
            "deploy_verify aus einem Git-Checkout starten.",
        )
    dirty: list[str] = []
    ignored: list[str] = []
    for line in status.out.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        if line.startswith("??") and not path.startswith("api/"):
            ignored.append(path)
        else:
            dirty.append(f"{line[:2].strip()} {path}")
    if dirty:
        return _refused_git(
            "Arbeitsbaum nicht sauber: " + "; ".join(dirty[:8])
            + (f" … (+{len(dirty) - 8})" if len(dirty) > 8 else ""),
            "Erst committen und nach origin/main bringen; nie von der Platte deployen.",
        )
    branch = await _util.run_local([*git, "rev-parse", "--abbrev-ref", "HEAD"], timeout=30)
    name = branch.out.strip()
    if not branch.ok or name != DEPLOY_BRANCH:
        return _refused_git(
            f"Branch ist {name or '?'}, deployt wird nur {DEPLOY_BRANCH}.",
            "Paketarbeit im Worktree bleibt dort; nach Review und Merge von main deployen.",
        )
    fetch = await _util.run_local(
        [*git, "fetch", "--quiet", "origin", DEPLOY_BRANCH], timeout=120
    )
    if not fetch.ok:
        return _refused_git(
            f"git fetch origin {DEPLOY_BRANCH} fehlgeschlagen: {fetch.tail(2, stream='err')}",
            "Netz/Zugang prüfen — ohne frischen Stand von origin kein Deploy.",
        )
    heads = await _util.run_local(
        [*git, "rev-parse", "HEAD", f"origin/{DEPLOY_BRANCH}"], timeout=30
    )
    lines = heads.out.split()
    if not heads.ok or len(lines) != 2:
        return _refused_git("HEAD/origin nicht lesbar.", "git rev-parse von Hand prüfen.")
    head, origin = lines
    if head != origin:
        return _refused_git(
            f"HEAD {head[:7]} entspricht nicht origin/{DEPLOY_BRANCH} {origin[:7]}.",
            "Erst pushen bzw. pullen — deployt wird nur, was auf origin liegt (CLAUDE.md §5.10).",
        )
    executor = API_DIR / "src/kickbase_agent/agent/executor.py"
    return {
        "ok": True,
        "commit": head,
        "branch": name,
        "executor_sha256": hashlib.sha256(executor.read_bytes()).hexdigest(),
        "ignored_untracked": ignored,
    }


async def _record_deploy(
    gate: dict[str, Any], status: str, found: dict[str, int]
) -> dict[str, Any]:
    """Deploy-Beleg als Bericht kind='deploy' — lokal und in der Box-DB. Nie fatal."""
    body = (
        f"Deploy {status}: Commit {gate['commit']} (origin/{DEPLOY_BRANCH})\n"
        f"agent/executor.py sha256 {gate['executor_sha256']}\n"
        "Marker: " + ", ".join(f"{m}={n}" for m, n in found.items())
    )
    meta = {
        "commit": gate["commit"],
        "executor_sha256": gate["executor_sha256"],
        "status": status,
        "markers": found,
    }
    report_id = save_report(
        kind="deploy", title=f"Deploy {gate['commit'][:7]}", body=body,
        source=DEPLOY_REPORT_SOURCE, meta=meta,
    )
    box = await _mirror_report_to_box(
        "deploy", body, {**meta, "local_report_id": report_id}, source=DEPLOY_REPORT_SOURCE
    )
    return {"report_id": report_id, "box": box}


@tool_guard
async def deploy_verify(markers: list[str], settle_seconds: int = 25) -> dict[str, Any]:
    """Deploy + inhaltliche Verifikation: rsync api/ (ohne .env/.git/.venv/DB) auf die Box,
    `docker compose --env-file api/.env up -d --build worker` (der Flag speist die
    Traefik-Middleware `kba-writetoken` aus derselben Quelle wie den Container —
    ohne ihn rendert Compose den Header leer und ALLE Schreibzugriffe enden im 401),
    danach Marker-grep im Container (/app/src) plus
    Jobs/Fehler. VERWEIGERT im Sperrfenster 21:25–22:25 Berlin (Abend-Jobs) und ohne
    sauberen `main` = `origin/main` (Git-Riegel). Hinterlässt Commit-Hash und
    Prüfsumme von agent/executor.py als Bericht kind='deploy'. Dauert Minuten."""
    t0 = time.monotonic()
    now = berlin_now()
    if deploy_window_blocked(now):
        return {
            "status": "refused",
            "reason": (
                f"Deploy-Sperrfenster {DEPLOY_BLOCK_START:%H:%M}–{DEPLOY_BLOCK_END:%H:%M} Berlin "
                f"(jetzt {now:%H:%M}): `compose up --build` frisst die Abend-Jobs "
                "21:33/22:05/22:15 (In-Memory-Crons)."
            ),
            "hint": f"Nach {DEPLOY_BLOCK_END:%H:%M} erneut aufrufen.",
        }
    clean = [m.strip() for m in markers if m and m.strip()]
    if not clean:
        raise ToolError(
            "Mindestens ein Code-Marker ist Pflicht.",
            hint="»hab deployed« heißt nicht deployed — einen neuen Funktions-/Textmarker "
            "angeben, der im Container gegrept wird.",
        )
    if not (API_DIR / "pyproject.toml").exists():
        raise ToolError(
            f"Lokales api/-Verzeichnis nicht gefunden ({API_DIR}).",
            hint="kba-mcp aus dem Repo (uv run --directory api) starten.",
        )
    gate = await git_deploy_gate()
    if not gate["ok"]:
        return {k: v for k, v in gate.items() if k != "ok"}

    rsync_argv = [
        "rsync",
        "-az",
        *[f"--exclude={pat}" for pat in RSYNC_EXCLUDES],
        str(API_DIR),
        f"{BOX_HOST}:{BOX_DIR}/",
    ]
    rs = await _util.run_local(rsync_argv, timeout=DEPLOY_TIMEOUT_S)
    if not rs.ok:
        return _failed("rsync", rs, hint="rsync/ssh-Key prüfen; Box erreichbar?")

    # Worker UND API: beide Container bauen aus demselben api/-Baum. Nur den Worker zu
    # bauen ließ die API auf altem Stand, während Marker und Bericht „ok" sagten
    # (Review-Befund 21.09.2026; Versionsversatz „unbekannte Schemaversion", 17.09.).
    up = await _util.run_ssh(
        f"cd {BOX_DIR} && docker compose --env-file api/.env up -d --build worker api",
        timeout=DEPLOY_TIMEOUT_S,
    )
    if not up.ok:
        # Nach dem rsync liegt neuer Code auf der Box — auch das braucht einen Beleg.
        await _record_deploy(gate, "failed_compose", {})
        return _failed("compose", up, hint="`docker compose logs worker api` auf der Box lesen.")

    # Der Scheduler meldet „Added job" erst nach Discord-on_ready — kurz setzen lassen.
    if settle_seconds > 0:
        await asyncio.sleep(settle_seconds)

    # Marker-grep IM Container (Code-Inhalt, nicht nur Status), Zählung auf dem Host.
    parts = [
        f"echo @@marker{idx}; "
        f"{docker_exec(f'grep -rlF --include=*.py -- {q(marker)} /app/src')} | wc -l"
        for idx, marker in enumerate(clean)
    ]
    parts += [
        f"echo @@apimarker{idx}; docker exec {API_CONTAINER} "
        f"grep -rlF --include=*.py -- {q(marker)} /app/src | wc -l"
        for idx, marker in enumerate(clean)
    ]
    parts.append(f"echo @@executor; {docker_exec(f'sha256sum {EXECUTOR_IN_CONTAINER}')}")
    logs = f"docker logs {CONTAINER} 2>&1"
    parts.append(
        f"echo @@status; docker ps --filter name=^/{CONTAINER}$ --format '{{{{.Status}}}}'"
    )
    parts.append(f"echo @@jobs; {logs} | grep -c 'Added job'")
    parts.append(f"echo @@errors; {logs} | grep -ciE 'error|traceback'")
    ver = await _util.run_ssh("; ".join(parts), timeout=120)
    sections = _sections(ver.out)
    found = {
        marker: (_first_int(sections.get(f"marker{idx}", [])) or 0)
        for idx, marker in enumerate(clean)
    }
    found_api = {
        marker: (_first_int(sections.get(f"apimarker{idx}", [])) or 0)
        for idx, marker in enumerate(clean)
    }
    box_sha = next(
        (ln.split()[0] for ln in sections.get("executor", []) if ln.strip()), None
    )
    executor_matches = box_sha == gate["executor_sha256"]
    health = _box_health(sections)
    all_found = all(n > 0 for n in found.values()) and all(n > 0 for n in found_api.values())
    ok = (
        all_found
        and executor_matches
        and health["running"]
        and (health["error_lines"] or 0) == 0
    )
    deploy_report = await _record_deploy(gate, "ok" if ok else "check", found)
    return {
        "status": "ok" if ok else "check",
        "git": {k: v for k, v in gate.items() if k != "ok"},
        "deploy_report": deploy_report,
        "markers": found,
        "markers_api": found_api,
        "all_markers_found": all_found,
        "executor_matches_box": executor_matches,
        "running": health["running"],
        "container_status": health["container_status"],
        "jobs_added": health["jobs_added"],
        "error_lines": health["error_lines"],
        "rsync_rc": rs.rc,
        "compose_tail": up.tail(4, stream="err") or up.tail(4),
        "finished_at": iso(berlin_now()),
        "duration_s": round(time.monotonic() - t0, 1),
        "hint": None
        if ok
        else "Marker fehlt (Worker oder API) / Prüfsumme der Schreibstelle weicht ab / "
        "Container steht / Fehlerzeilen — `docker logs kickbase-agent` lesen.",
    }


# ------------------------------------------------------------------ cockpit_build


@tool_guard
async def cockpit_build() -> dict[str, Any]:
    """Cockpit auf der Box neu bauen (docker exec … run_cockpit_build()) — liefert Bytes und
    Zeitstempel; danach live unter https://localhost:8080. Dauert 1–2 Minuten."""
    t0 = time.monotonic()
    py = (
        "import asyncio; from kickbase_agent.worker.cockpit_job import run_cockpit_build; "
        "print(asyncio.run(run_cockpit_build()))"
    )
    res = await _util.run_ssh(docker_exec(f"uv run python -c {q(py)}"), timeout=COCKPIT_TIMEOUT_S)
    if not res.ok:
        return _failed("cockpit", res, hint="Container läuft? `docker logs kickbase-agent`.")
    out = res.out.strip()
    match = re.search(r"([\d.,]+)\s*Bytes", out)
    size = int(re.sub(r"[.,]", "", match.group(1))) if match else None
    return {
        "status": "ok" if size else "check",
        "bytes": size,
        "output": out[-300:],
        "url": COCKPIT_URL,
        "finished_at": iso(berlin_now()),
        "duration_s": round(time.monotonic() - t0, 1),
        "hint": None if size else "Kein Byte-Wert in der Ausgabe — cockpit_html_path gesetzt?",
    }


# ------------------------------------------------------------------ discord_post


def _resolve_report_kind(kind: str, body: str) -> str:
    """Berichtsart für die Ablage: explizit (validiert) oder aus der ersten Zeile geraten.

    "info" (Default) und leer/"auto" heißen „raten" — so landet eine per Chat gepostete
    Morgen-Recherche („Lage & Gebote", „Teil 1/2") automatisch unter `recherche`.
    """
    value = (kind or "").strip().lower()
    if value in ("", "auto", DEFAULT_KIND):
        return infer_kind(body, default=DEFAULT_KIND)
    if value not in REPORT_KINDS:
        raise ToolError(
            f"Unbekannte Berichtsart {kind!r}.",
            hint="kind = " + " | ".join(REPORT_KINDS) + " (oder weglassen = automatisch).",
        )
    return value


@tool_guard
async def discord_post(text: str, kind: str = "info") -> dict[str, Any]:
    """Nachricht per Discord-REST in Emils Kanal posten (Bot-Token/Kanal aus Settings).
    Maximal 1900 Zeichen — längere Texte werden abgelehnt, nicht gekürzt. Nach dem Post
    bleibt der Volltext als Bericht nachlesbar (Steuerzentrale, Zone „Berichte"; source
    mcp:discord_post): lokal UND gespiegelt in die Box-DB (`kba report-add` via ssh — die
    Web-App liest die Box; zwei DBs wie bei tip_add). Antwort: report_id (lokal), kind,
    box{ok, report_id}. kind: briefing | recherche | schatten | auto_trading | planner |
    executor | retro | bid_watch | info | sonstiges — "info" (Default) rät die Art aus der
    ersten Zeile (z. B. „Lage & Gebote"/„Teil 1" → recherche)."""
    body = text.strip()
    if not body:
        raise ToolError("Leere Nachricht.", hint="Text übergeben.")
    if len(body) > DISCORD_MAX_CHARS:
        raise ToolError(
            f"Nachricht zu lang ({len(body)} Zeichen, max {DISCORD_MAX_CHARS}).",
            hint="Kürzen oder in zwei Posts teilen.",
        )
    report_kind = _resolve_report_kind(kind, body)  # Validierung VOR dem Post
    explicit_kind = (kind or "").strip().lower()
    settings = get_settings()
    from kickbase_agent.notify.report_store import discord_delivery_allowed

    deliver = discord_delivery_allowed(explicit_kind, settings.discord_digest_only)
    if deliver and not (settings.discord_bot_token and settings.discord_channel_id):
        raise ToolError(
            "Discord nicht konfiguriert.",
            hint="DISCORD_BOT_TOKEN und DISCORD_CHANNEL_ID in api/.env setzen.",
        )
    message_id = None
    if deliver:
        url = f"https://discord.com/api/v10/channels/{settings.discord_channel_id}/messages"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bot {settings.discord_bot_token}"},
                json={"content": body},
            )
            resp.raise_for_status()
        try:
            message_id = resp.json().get("id")
        except ValueError:
            message_id = None
    # Ablage erst NACH erfolgreichem Post (ein Fehlschlag wird erneut versucht — keine
    # Duplikate); save_report stört den Erfolg nie (DB-Fehler → None, nur Log).
    meta = {"delivered": deliver, "chars": len(body), "message_id": message_id}
    report_id = save_report(
        kind=report_kind, title=None, body=body, source=REPORT_SOURCE, meta=meta
    )
    box = await _mirror_report_to_box(report_kind, body, {**meta, "local_report_id": report_id})
    return {
        "posted": deliver,
        "chars": len(body),
        "message_id": message_id,
        "report_id": report_id,
        "kind": report_kind,
        "box": box,
        "hint": None
        if box["ok"]
        else "Box-Spiegelung fehlgeschlagen — Bericht ist lokal gespeichert; auf der Box "
        "nachholen: docker exec kickbase-agent uv run kba report-add … (Web-App liest die Box).",
    }


async def _mirror_report_to_box(
    kind: str, body: str, meta: dict[str, Any], source: str = REPORT_SOURCE
) -> dict[str, Any]:
    """Bericht in die BOX-DB spiegeln (`kba report-add` im Container) — nie fatal."""
    # `--` vor dem Body: Texte, die mit „-" beginnen („-# Hinweis"), sind keine Optionen.
    remote = docker_exec(
        f"uv run kba report-add --kind {q(kind)} --source {q(source)} "
        f"--meta {q(json.dumps(meta, ensure_ascii=False))} -- {q(body)}"
    )
    res = await _util.run_ssh(remote, timeout=120)
    match = _BOX_REPORT_RE.search(res.out)
    report_id = int(match.group(1)) if match else None
    return {
        "ok": res.ok and report_id is not None,
        "report_id": report_id,
        "output": (res.out.strip() or res.err.strip())[-300:],
    }


# ------------------------------------------------------------------ tip_add


@tool_guard
async def tip_add(
    name: str, weight: float = 0.6, source: str = "ki-recherche", note: str = ""
) -> dict[str, Any]:
    """Recherche-Tipp DOPPELT persistieren (zwei DBs!): lokal wie `kba tip` + Box via
    `docker exec kickbase-agent uv run kba tip …`. weight +1.0 Kauf-Tipp … -1.0 meiden;
    wirkt 4 Tage (±0.08·Gewicht) auf die Kauf-Scores."""
    player = name.strip()
    if not player:
        raise ToolError("Spielername fehlt.", hint="Name wie in Kickbase angeben.")
    if not -1.0 <= float(weight) <= 1.0:
        raise ToolError("weight außerhalb [-1.0, 1.0].", hint="+1.0 Kauf … -1.0 meiden.")
    with SessionLocal() as session:
        tip = save_tip(session, player, float(weight), source, note)
        local = {
            "id": tip.id,
            "player_name": tip.player_name,
            "weight": tip.weight,
            "source": tip.source,
            "created_at": iso(tip.created_at),
        }
    remote = docker_exec(
        f"uv run kba tip {q(player)} -w {q(f'{float(weight):.2f}')} -s {q(source)} -n {q(note)}"
    )
    res = await _util.run_ssh(remote, timeout=120)
    return {
        "local": local,
        "box": {"ok": res.ok, "output": (res.out.strip() or res.err.strip())[-300:]},
        "hint": None
        if res.ok
        else "Box-Schreibung fehlgeschlagen — Tipp ist lokal gespeichert, auf der Box nachholen.",
    }


# ------------------------------------------------------------------ learning_evidence


def _evidence_remote() -> tuple[str, tuple[str, str] | None] | None:
    """Box-API als Ziel, wenn konfiguriert (identisch zu `tools_write._remote`)."""
    from kickbase_agent.cli.learn import remote_target

    return remote_target()


@tool_guard
async def learning_evidence(
    learning_id: str, verdict: str, note: str, ref: str
) -> dict[str, Any]:
    """Beleg zu einer Erkenntnis eintragen (Lern-Bilanz, Spec 2026-08-25).

    `learning_id` ist der Anker aus LEARNINGS.md (`L-065`), `verdict` confirm|refute,
    `note` ein Satz Beobachtung, `ref` eine eindeutige Ereignis-Referenz (`bid:34`,
    `action:39`, `feed:12364455874`) — derselbe `ref` zählt nie zweimal.

    `source` ist IMMER `agent`: Der Beleg ist mein Urteil, keine Messung, und die
    Zentrale zeigt beides getrennt. Gemessene Belege schreibt allein der Sammler-Job.

    Zwei-DB-Regel (24.08.): Mit `KBA_REMOTE_API_URL` geht der Beleg an die Box-API —
    eine Wahrheit, ein Schreiber. Ohne Remote landet er in der lokalen DB.
    """
    payload = {
        "learning_id": learning_id.strip(),
        "verdict": verdict.strip().lower(),
        "note": note.strip(),
        "ref": ref.strip(),
    }
    remote = _evidence_remote()
    if remote is not None:
        base, auth = remote
        headers = {"X-Requested-With": "kba"}
        token = (os.environ.get("KBA_API_WRITE_TOKEN")
                 or get_settings().kba_api_write_token or "").strip()
        if token:
            headers["X-KBA-Write-Token"] = token
        async with httpx.AsyncClient(auth=auth, timeout=30, headers=headers) as http:
            resp = await http.post(f"{base}/learning/evidence", json=payload)
        resp.raise_for_status()
        body = resp.json() or {}
        return {"remote": True, "message": body.get("note", ""), **(body.get("evidence") or {})}

    with SessionLocal() as session:
        try:
            row = record_agent_evidence(
                session, payload["learning_id"], payload["verdict"],
                note=payload["note"], ref=payload["ref"],
            )
        except ValueError as exc:
            raise ToolError(str(exc), hint="learning_id wie 'L-065', verdict confirm|refute.") \
                from exc
        if row is None:
            return {
                "remote": False, "created": False, **payload,
                "message": "Ereignis war für diese Erkenntnis schon belegt — nichts angelegt.",
            }
        return {
            "remote": False, "created": True, "id": row.id, "source": row.source,
            "learning_id": row.learning_id, "verdict": row.verdict, "ref": row.ref,
            "note": row.note, "observed_at": iso(row.observed_at),
            "message": "Beleg gespeichert.",
        }
