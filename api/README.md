# Kickbase-Agent — Backend (`api/`)

Python-Paket `kickbase_agent`: Engine, Worker (Scheduler), ActionExecutor, MCP-Server,
FastAPI, CLI und das serverseitig gerenderte Cockpit. Gesamtüberblick und Regeln stehen
in [`../CLAUDE.md`](../CLAUDE.md); Architektur in [`../docs/ARCHITEKTUR.md`](../docs/ARCHITEKTUR.md);
Schnittstellen in [`../docs/API.md`](../docs/API.md); Betrieb in [`../docs/BETRIEB.md`](../docs/BETRIEB.md).

## Setup (lokal, SQLite)

```bash
uv sync --extra dev
```
```bash
cp .env.example .env   # Kickbase-Login, Liga-ID, Discord — Werte von Emil; Flags siehe docs/BETRIEB.md
```
```bash
uv run alembic upgrade head   # lokale SQLite (DATABASE_URL aus .env)
```

## Einstiegspunkte (pyproject scripts)

| Script | Zweck |
|---|---|
| `kba` | Typer-CLI (`kba --help`): research-data, post-discord, tip, reports, kader, plan, probe, dashboard-html … |
| `kba-worker` | Scheduler + Discord-Bot (Produktion: Container `kickbase-agent`) |
| `kba-serve` | FastAPI (`/api/health`, `/api/intents`, `/api/reports`, `/api/learning`, `/api/protocol` …); Env `KBA_API_HOST/PORT/RELOAD` |
| `kba-mcp` | MCP-Server `kickbase` (stdio, 21 Tools) — in Claude Code über `../.mcp.json` |

## Qualität

```bash
uv run --extra dev pytest -q          # 778 Tests (23.08.2026)
```
```bash
uv run --extra dev ruff check src/ tests/
```

Regeln: Fixtures nur aus echten API-Antworten (`kba probe`), Kickbase-Writes nur über
`agent/executor.py`, keine Secrets im Code, 100 Spalten, deutsche Docstrings.
