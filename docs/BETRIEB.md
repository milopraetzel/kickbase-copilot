# Betriebshandbuch (Runbook) — Kickbase-Auto-Manager

> Betriebsprüfung mit Release-Nachtrag:
> [Hermes-/Lernzyklus-Diagnose vom 11.09.2026](DIAGNOSE-2026-09-11.md).
> Hermes läuft auf der Kickbase-Box. Der Produktionsstand wurde am 11.09.2026
> im Release `af5f9d9` mit dem Repository abgeglichen. Compose stets mit `--env-file api/.env` aufrufen, damit
> Proxy-Header und API-Schreib-Token aus derselben Konfiguration stammen.

**Stand: 23.08.2026** · Branch `feature/phase-4-autonomous-agent` · Zielgruppe: ein Mensch
oder eine KI, die das System betreibt, deployt, prüft und Störungen behebt.

**Grundsatz:** Dieses Dokument enthält keine Secrets. Zugangsdaten (Kickbase-Login/Token,
Discord-Bot-Token, Anthropic-Key, Basic-Auth-Passwort) liegen **bei Emil** bzw. auf der Box
in `/opt/kickbase-agent/api/.env` — nie in Chat, Repo oder Logs.

**Begleitdokumente:** `docs/ARCHITEKTUR.md` (Bausteine), `docs/API.md` (REST-Endpunkte), `docs/GLOSSAR.md`
(Begriffe), `CLAUDE.md` (Arbeitsregeln), `.claude/skills/kickbase/SKILL.md` (Routinen + eiserne Regeln),
`.claude/skills/kickbase/LEARNINGS.md` (Betriebslektionen), `docs/STRATEGIE-SAISON-26-27.md`, `docs/archive/UEBERGABE-HERMES.md`,
`docs/superpowers/specs/2026-08-23-steuerzentrale.md` und `…/2026-08-23-kickbase-mcp.md`. Überholte
Anleitungen liegen in `docs/archive/` (systemd-Variante, erste Compose-Anleitung, KI-Review-Paket).

---

## 1. Überblick Betrieb

| Was | Wert |
|---|---|
| Box | `root@203.0.113.10` (Hostinger, „n8n-Box" — geteilt mit n8n + Traefik) |
| Projektpfad auf der Box | `/opt/kickbase-agent/` → `api/` (Code + **`api/.env`**), `deploy/cockpit-nginx.conf`, `docker-compose.yml`, `learnings.md`. **Kein Git auf der Box** — Deploy = rsync vom Mac. |
| Domain | `https://localhost:8080` = Cockpit (Web-App) · `https://localhost:8080/api/…` = FastAPI |
| Auth | Traefik-Basic-Auth, User `emil`, Passwort bei Emil. Middleware `kba-auth` ist **einmal** definiert (Label am Service `cockpit-web`) und von der API als `kba-auth@docker` referenziert — fällt cockpit-web weg, ist die API nicht erreichbar (fail-closed). |
| TLS | Traefik (`n8n-traefik-1`, Ports 80/443), certresolver `mytlschallenge`, Entrypoints `web,websecure` |
| Volume | `kba-data` (Docker-Name `kickbase-agent_kba-data`) — siehe unten |
| Zeitzone | Host UTC; Container `TZ=Europe/Berlin` (Compose) **und** CronTrigger-Wrapper in `worker/__main__.py` erzwingt `timezone="Europe/Berlin"`. DB-Zeitstempel sind UTC (MCP/`iso()` rechnet nach Berlin um). |

### Die drei Container (`docker-compose.yml`)

| Container | Service | Image | Zweck | Port / Route | Limits |
|---|---|---|---|---|---|
| `kickbase-agent` | `worker` | `build: ./api` (`kickbase-agent-worker`) | Discord-Bot + APScheduler: 22 Cron-Jobs + dynamische Spieltags-Wächter. Entrypoint `docker-entrypoint.sh`: `alembic upgrade head` → `kba-worker` (Schema-Hoheit!). | **kein Port** (nur ausgehend) | 1 GB RAM, 1 CPU, Logs 3×10 MB |
| `kickbase-api` | `api` | dasselbe Image (`kickbase-agent-api`), `entrypoint: uv run kba-serve` | FastAPI/Uvicorn `0.0.0.0:8000` — Steuerzentrale-API (`/api/intents`, `/api/protocol`, `/api/reports`, `/api/learning`, `/api/health` …). Schreibt **nur** `intents`-Zeilen, nie Kickbase. Läuft **ohne** Alembic (vermeidet Doppel-Migration), `depends_on: worker`. | Traefik ``Host(`localhost:8080`) && PathPrefix(`/api`)``, Priority 100, Service-Port 8000, Middleware `kba-auth@docker` | 512 MB, 0,5 CPU |
| `kickbase-cockpit-web` | `cockpit-web` | `nginx:alpine` | liefert `/app/data/cockpit/index.html` read-only aus dem Volume (`deploy/cockpit-nginx.conf`: `Cache-Control: no-store`, 503-Platzhalter bis zum ersten Build) | Traefik ``Host(`localhost:8080`)``, Middleware `kba-auth` | 128 MB |

- `api` und `cockpit-web` hängen in **zwei Netzen** (`default` + externes `n8n_default`) → Label
  `traefik.docker.network: n8n_default` ist Pflicht, sonst 504.
- Beide Python-Container lesen `env_file: ./api/.env`; Compose überschreibt darin `DATABASE_URL`
  (`sqlite:////app/data/kba.db`), setzt `TZ` und beim Worker `COCKPIT_HTML_PATH`.

### Volume `kba-data` (Inhalt, Stand 23.08.)

| Pfad im Container | Inhalt |
|---|---|
| `/app/data/kba.db` | SQLite — Audit (`transfer_actions`), Vorhaben (`intents`), Berichte (`reports`), Tipps (`community_tips`), Spieler/Historie/Performance, News |
| `/app/data/cockpit/index.html` | Cockpit-HTML, stündlich :22 vom Worker gebaut (~1 MB) |
| `/app/data/learnings.md` | Kopie von `.claude/skills/kickbase/LEARNINGS.md` (Lern-Schicht `knowledge/learnings.py` liest sie; Pfadkette: `KBA_LEARNINGS_PATH` → Repo → `/app/learnings.md` → `/app/data/learnings.md`) |
| `/app/data/agent.db` | 0 Byte, Altlast — ignorieren |

### Was außerhalb der Box läuft

- **Orchestrator** = Claude Code mit Skill `kickbase` + MCP-Server `kickbase` (`.mcp.json`, lokal
  auf Emils Mac). Morgen-Routine als Scheduled Task `kickbase-morgenroutine` (08:27,
  `~/.claude/scheduled-tasks/kickbase-morgenroutine/SKILL.md`) — läuft nur, wenn Mac + Claude Code laufen.
- **Discord-Kanal** (Bot-Token in `api/.env`): Ziel aller Job-Posts; Volltexte zusätzlich in `reports`.
- **Hermes** (Emils Agent auf KVM 1) im Schattenmodus über ein SSH-Gate (Abschnitt 8).

---

## 2. Lokale Entwicklung

Voraussetzungen: `uv` (verwaltet Python selbst; Projekt verlangt ≥ 3.12, lokale venv aktuell
CPython 3.14, Container `python:3.12-slim`), ssh-Key für die Box (nur für Deploy/Box-Check).
Der Pfad enthält ein Leerzeichen — **immer quoten**.

```bash
cd "/Users/emil/Mein Claude /Kickbase-Agent/api" && uv sync --extra dev
```

`.env` aus der Vorlage anlegen und ausfüllen (Kickbase-Auth Weg A Token/Refresh-Token oder Weg B
E-Mail/Passwort, `KICKBASE_LEAGUE_ID=1234567`, Discord-Variablen, Flags):

```bash
cp "/Users/emil/Mein Claude /Kickbase-Agent/api/.env.example" "/Users/emil/Mein Claude /Kickbase-Agent/api/.env"
```

Lokale DB (`DATABASE_URL=sqlite:///kba_local.db`, Datei `api/kba_local.db`, per `.gitignore` ausgeschlossen):

```bash
cd "/Users/emil/Mein Claude /Kickbase-Agent/api" && uv run alembic upgrade head
```

Tests (Stand 23.08.: **778 passed**, ~20 s) und Lint:

```bash
cd "/Users/emil/Mein Claude /Kickbase-Agent/api" && uv run --extra dev pytest -q
```

```bash
cd "/Users/emil/Mein Claude /Kickbase-Agent/api" && uv run --extra dev ruff check src/ tests/
```

Cockpit lokal bauen (Live-Daten, 1–2 min) und im Browser ansehen (`.claude/launch.json`
→ Konfiguration `cockpit-preview` = `python3 -m http.server 8765 --directory /tmp`, dann
`http://localhost:8765/kba-dashboard.html`):

```bash
cd "/Users/emil/Mein Claude /Kickbase-Agent/api" && uv run kba dashboard-html /tmp/kba-dashboard.html
```

MCP-Server lokal (stdio; Logs auf stderr). In Claude Code lädt `.mcp.json` ihn automatisch als
Server `kickbase` — **mit `env AGENT_DRY_RUN=0` + `AGENT_ALLOW_LIVE=1`, d. h. die Write-Tools
`place_bid`/`list_player`/`sell_player` schreiben bei `confirm=True` LIVE** (`collect_bonus` über
`AGENT_AUTO_BONUS=1` der lokalen `.env`). Neue Session nötig, damit `.mcp.json` greift.

```bash
cd "/Users/emil/Mein Claude /Kickbase-Agent" && uv run --directory api kba-mcp
```

Weitere lokale Werkzeuge: `uv run kba --help` (login, kader, markt, news, probe, snapshot, plan,
research, research-data, post-discord, report-add, reports, tip, tips, dashboard-html);
`uv run kba-serve` (API lokal auf 127.0.0.1:8000 mit Reload); `uv run kba-worker` (lokalen Worker
**nicht** dauerhaft laufen lassen — Doppel-Worker, siehe Abschnitt 7).

Zwei Datenbanken: Mac (`kba_local.db`) und Box (Volume). Tipps/Berichte immer **doppelt**
schreiben — die MCP-Tools `tip_add` und `discord_post` spiegeln automatisch auf die Box.

---

## 3. Deploy — der eine richtige Ablauf

**Sperrfenster: 21:25–22:25 Berlin NIE deployen.** `docker compose up --build` startet den
Worker neu; die APScheduler-Crons leben im Speicher, verpasste Läufe (21:33 Closing-Bell,
22:05 Snapshot, 22:15 Briefing, 22:20 Schatten-Scan) werden **nicht nachgeholt** (nur der
Bonus und die Spieltags-Wächter haben einen Start-Catch-up). Das MCP-Tool `deploy_verify`
verweigert im Fenster von sich aus.

### 3.1 Vor dem Deploy: parallele Sessions ausschließen

Mehrere Claude-Sessions arbeiten im selben Repo. rsync überschreibt die Box mit dem **lokalen**
Stand — wer mit altem Stand deployt, deployt fremde Änderungen weg.

```bash
cd "/Users/emil/Mein Claude /Kickbase-Agent" && git fetch && git status -sb && git log --oneline HEAD..@{u}
```

Gibt es Commits hinter dem eigenen HEAD → erst `git pull --rebase`. Dann Prüfsumme des Code-Baums
lokal und auf der Box vergleichen (gleiche Hash-Zeile = Box entspricht dem lokalen Stand; anders =
jemand hat dazwischen deployt oder lokal liegt Ungesichertes):

```bash
cd "/Users/emil/Mein Claude /Kickbase-Agent" && find api/src -name '*.py' -not -path '*/__pycache__/*' | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256
```

```bash
ssh root@203.0.113.10 'cd /opt/kickbase-agent && find api/src -name "*.py" -not -path "*/__pycache__/*" | LC_ALL=C sort | xargs sha256sum | sha256sum'
```

### 3.1b Schreib-Gate der API (seit 25.08.2026)

**Warum:** `kickbase-api` hängt im geteilten Docker-Netz `n8n_default` — am 25.08. gemessen
mit sieben fremden Containern (n8n, `n8n-ai-messenger-1`, `atoz-chat-preview`,
`pawnshop-preview`, `n8n-huber-dashboard-1`, `atoz-runtime-proxy`, Traefik). Die Basic-Auth
endet bei Traefik; ein `POST /api/refresh` aus `atoz-chat-preview` kam nachweislich bis in
die App durch (HTTP 429). Der CSRF-Header schützt nur vor Browsern, nicht vor einem Skript.

**Wie:** Traefik färbt jeden Request auf dem `/api`-Router mit `X-KBA-Write-Token`
(`customrequestheaders` **überschreibt** einen mitgeschickten Wert — fälschen geht nicht,
wer direkt aus dem Netz kommt, passiert Traefik ja gar nicht). Die App verlangt den Header
auf **allen** mutierenden Routen (`app.py`, Middleware `_write_token_guard`): `POST /intents`,
`veto`, `adjust`, `execute-now`, `refresh`. Lesen bleibt offen.

**Wert:** steht **nur** in `api/.env` (`KBA_API_WRITE_TOKEN`) — auf der Box und lokal
derselbe. Emils Hoheit; Agenten fassen die Datei nicht an. Leerer Wert = Gate aus, die API
schreibt dann eine laute Warnung ins Log (`Schreib-Gate AUS`).

**Zusätzlich:** `adjust` hat seit 25.08. eine Obergrenze von 150 % des ursprünglich geplanten
Betrags (`ADJUST_MAX_FACTOR`) — Emils +10-/+20-%-Buttons bleiben, absurde Beträge entstehen
gar nicht erst. Die echte Preisgrenze zieht weiterhin der Executor-Preflight (110 % MW).

### 3.2 Der Einzeiler (Stand 23.08.2026)

Synct `api/` + `docker-compose.yml` + `deploy/` (nginx-Konfiguration, die Compose per Bind-Mount
braucht), kopiert `LEARNINGS.md` als `learnings.md` auf die Box und ins Volume, baut **alle**
Services. **`--exclude='.env'` ist Pflicht** — sonst überschreibt die lokale `api/.env` die der Box
(andere Flags, andere Zugangsdaten).

```bash
cd "/Users/emil/Mein Claude /Kickbase-Agent" && rsync -az --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='*.db' --exclude='*.db-journal' --exclude='*.db-shm' --exclude='*.db-wal' --exclude='.env' --exclude='.env.*' --exclude='.pytest_cache' --exclude='.ruff_cache' --exclude='.mypy_cache' --exclude='.coverage' --exclude='node_modules' api docker-compose.yml deploy root@203.0.113.10:/opt/kickbase-agent/ && scp .claude/skills/kickbase/LEARNINGS.md root@203.0.113.10:/opt/kickbase-agent/learnings.md && ssh root@203.0.113.10 'cd /opt/kickbase-agent && docker compose --env-file api/.env up -d --build && docker cp /opt/kickbase-agent/learnings.md kickbase-agent:/app/data/learnings.md'
```

Hinweise:
- **`--env-file api/.env` ist Pflicht** (seit 25.08.): Compose speist daraus die Traefik-
  Middleware `kba-writetoken` — DIESELBE Quelle, aus der der Container sein
  `KBA_API_WRITE_TOKEN` bekommt, damit Proxy und App nie auseinanderlaufen. Ohne den Flag
  rendert Compose den Header leer, Traefik entfernt ihn, und **alle** schreibenden
  `/api`-Aufrufe enden im 401 (die Zentrale-Buttons hören auf zu funktionieren). Das ist
  gewolltes fail-closed-Verhalten — Heilung: denselben Befehl MIT Flag erneut ausführen.
  Lesende Aufrufe und der Worker sind nie betroffen.
- `docker compose --env-file api/.env up -d --build` rebaut das Image und ersetzt nur Container, deren Image/Config
  sich geändert hat. Der Worker migriert beim Start (`alembic upgrade head`); die API wartet per
  `depends_on` auf ihn.
- **Nach Änderungen an `api/.env`** reicht das nicht zuverlässig — Box-Erfahrung: Container laufen
  mit alter Umgebung weiter. Dann explizit:

```bash
ssh root@203.0.113.10 'cd /opt/kickbase-agent && docker compose --env-file api/.env up -d --force-recreate worker api'
```

- MCP-Tool `deploy_verify(markers=[...])` macht rsync (nur `api/`, ohne `.env`) + `compose up -d
  --build worker` + Marker-grep in einem — aber **nur den Worker**. Für Änderungen an
  `docker-compose.yml`, der API oder nginx den Einzeiler nehmen.
- **Nur nginx geändert** (`deploy/cockpit-nginx.conf`, etwa die Location für `jev.html` am
  01.10.2026): Die Datei ist per Bind-Mount eingehängt; rsync ersetzt sie durch eine neue
  Datei, der laufende Container sähe weiter die alte. Deshalb nach dem rsync von `deploy/`
  den Container neu erzeugen, nicht nur nginx neu laden:

```bash
ssh root@203.0.113.10 'cd /opt/kickbase-agent && docker compose --env-file api/.env up -d --force-recreate cockpit-web && docker exec kickbase-cockpit-web nginx -T 2>/dev/null | grep -c "location = /jev.html"'
```

### 3.3 Verify — Inhalt, nicht Status

„hab deployed" heißt nichts. Immer den **Code-Inhalt** im Container greppen (einen neuen
Funktions-/Textmarker der Änderung), dazu Jobs, Fehler, API, Cockpit:

```bash
ssh root@203.0.113.10 'docker exec kickbase-agent grep -rlF -- "<neuer-code-marker>" /app/src | head'
```

```bash
ssh root@203.0.113.10 'docker ps --filter name=kickbase --format "{{.Names}}\t{{.Status}}"; docker logs kickbase-agent 2>&1 | grep -c "Added job"; docker logs kickbase-agent 2>&1 | grep -ciE "error|traceback"'
```

Erwartung: drei Container `Up`; **22** „Added job" direkt nach dem Start (plus 4 Wächter
`kickoff-T-120/60/35/15`, sobald eine Spieltags-Deadline < 96 h entfernt ist; die Zahl wächst über
die Laufzeit, weil der 07:03-Planer die Wächter täglich neu setzt); **0** Fehlerzeilen.
Job-Beweis immer über die APScheduler-Zeilen („Added job", „Running job", „executed
successfully"), nie über `print()` (im Container gepuffert — „✅ Discord-Bot online" fehlt oft).

API von außen (401 ohne Auth = Route + Basic-Auth aktiv; mit `-u emil` → `{"status":"ok"}`):

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://localhost:8080/api/health
```

API von innen (ohne Traefik):

```bash
ssh root@203.0.113.10 'docker exec kickbase-api uv run python -c "import httpx; print(httpx.get(\"http://127.0.0.1:8000/api/health\").json())"'
```

Cockpit-Build sofort anstoßen (sonst erst zum nächsten :22) — MCP `cockpit_build()` oder:

```bash
ssh root@203.0.113.10 'docker exec kickbase-agent uv run python -c "import asyncio; from kickbase_agent.worker.cockpit_job import run_cockpit_build; print(asyncio.run(run_cockpit_build()))"'
```

Danach `https://localhost:8080` im Browser prüfen (Zeitstempel im Cockpit, Zentrale lädt
`/api/intents` nach). Bei Abend-Deploys nach 22:25: ausgefallene Abend-Posts ggf. nachholen.

### 3.4 Selbst-Deploy: läuft am Arbeitsplatz, NIE im Container (Entscheidung 26.08.2026)

**Wo er läuft.** Der Selbstumbau (`agent/self_change.py`, Flag `AGENT_SELF_CHANGE`, Default 0)
läuft ausschließlich dort, wo auch Emil deployt: **auf dem Mac, im Git-Checkout**
(`/Users/emil/Mein Claude /Kickbase-Agent`). Er legt einen Branch `selbst/JJJJMMTT-…` an,
committet genau einen `[selbst]`-Commit, deployt über denselben Weg wie Abschnitt 3.2
(rsync + `docker compose`, in der Praxis MCP `deploy_verify`) und nimmt sich bei roter
Gesundheitsprüfung per `git revert` selbst zurück.

**Warum nicht im Container.** Der Weg braucht drei Dinge, die es im Worker-Image nicht gibt und
bewusst nicht geben soll (geprüft 26.08.):

| Braucht | Im Image (`api/Dockerfile`, `python:3.12-slim`) |
|---|---|
| `guardrails.lock` (Sicherungs-Manifest) | **fehlt** — kopiert werden nur `pyproject.toml`, `src/`, `alembic.ini`, `docker-entrypoint.sh` |
| `tests/` + Testläufer | **fehlt** — kein `tests/`-COPY, und `uv sync --no-dev` lässt `pytest` weg |
| `git` (Branch, Commit, `revert`) | **fehlt** — `python:3.12-slim` bringt weder `git` noch `rsync` noch `ssh` mit; der Dockerfile installiert nichts nach |
| rsync/ssh gegen die Box | **fehlt** — und auf der Box liegt ohnehin kein Git-Repo (Abschnitt 1) |

Manifest und Tests ins Image zu kopieren wäre die bequeme, aber falsche Antwort: Es entstünde ein
**zweites, halbes Tor** — Prüfsummen prüfbar, Deploy trotzdem unmöglich, dafür ein größeres Image
und zwei Orte, an denen dieselbe Wahrheit gepflegt werden müsste. Der Container ist das **Ziel**
des Deploys, nicht sein Ausgangspunkt.

**Was passiert, wenn es jemand doch dort versucht.** Nichts Halbfertiges. Vor dem ersten
git-Aufruf prüft `agent/deploy_env.py` die Umgebung (Manifest lesbar · Sicherungs-Tests im Baum +
Testläufer auf PATH · Git-Work-Tree). Fehlt eines, endet der Umbau mit `schritt="umgebung"`,
schreibt eine `ERROR`-Zeile ins Worker-Log (`docker logs kickbase-agent`) und postet den Grund
nach Discord — kein Branch, kein Commit, kein Deploy. **Nicht** eingefroren wird dabei: Es liegt
nichts halb herum, und die falsche Umgebung sagt nichts über die Gesundheit des Handels; ein
Einfrieren machte aus einem verweigerten Umbau einen verlorenen Spieltag. Eingefroren wird nur,
wenn der Rückwärtsgang klemmt (`self_deploy.rollback`, A2).

Dasselbe Urteil liefert das Tor auf Zuruf — im Container erwartungsgemäß rot:

```bash
ssh root@203.0.113.10 'docker exec kickbase-agent uv run kba guardrails-check --no-suite --json'
```

Erwartung im Container: Exit 1, `"ok": false`, `umgebung.checks` = `{manifest:false, suite:false,
git:false}`. Erwartung im Repo auf dem Mac (`cd api && uv run kba guardrails-check`): Exit 0.
**Das rote Ergebnis im Container ist der Sollzustand, kein Defekt** — es ist der Beweis, dass die
Grenze wirkt.

---

## 4. Flags & Scharfschaltung

Alle Writes laufen durch den `ActionExecutor` (`agent/executor.py`): Idempotenz → Kill-Switch →
Live-Interlock (`_live_allowed`) → Audit `pending` vor dem API-Call → Preflight. Ein Flag ärmt
**genau einen Pfad** — nie „alles". Quelle: Flag-Matrix im Docstring von `_live_allowed`.

| Flag | Default (`config.py`) | ärmt | Box-Stand 23.08. | Wo gesetzt |
|---|---|---|---|---|
| `AGENT_DRY_RUN` | `1` | **Kill-Switch**: bei 1 wird nie live geschrieben (alles `skipped_dry_run`), egal welche Auto-Flags stehen | `0` | Box `api/.env` (Emil); lokal `.mcp.json` env `0` |
| `AGENT_ALLOW_LIVE` | `0` | NUR Trade-Typen `bid`, `cancel_bid`, `sell`, `list`, `delist`, `accept`, `decline` (manuelle Trades, MCP-Write-Tools) — nicht Lineup/Bonus | ungesetzt (= 0): Worker-Trades nur über Intents | lokal `.mcp.json` env `1` |
| `AGENT_AUTO_BONUS` | `0` | ausschließlich `bonus` | `1` | Box `api/.env` |
| `AGENT_AUTO_LINEUP` | `0` | ausschließlich `lineup` | `1` | Box `api/.env` |
| `AGENT_AUTO_PURGE` | `0` | `sell`/`cancel_bid` mit `rule_id` `kickoff-*` (T-120 Storno, T-15 Notbremse) | `1` | Box `api/.env` |
| `AGENT_AUTO_INTENTS` | `0` | `bid`/`sell`/`list` mit `rule_id` `intent-*` (Steuerzentrale: Planner :25 / Executor */5) | `1` (seit 23.08. ~15:55; erster autonomer Zyklus: zwei Listings) | Box `api/.env` (Emils „scharf") |
| `AGENT_TRADE_AUTOPILOT` | `1` | kein Arming, ein Halt: `0` = Handels-Autopilot legt keine neuen Handelsgebote an und räumt geplante; Verkäufe der Handelsspieler laufen weiter (ausgeführt wird ohnehin nur über `AGENT_AUTO_INTENTS`) | ungesetzt (= 1, Emils Auftrag 01.10.2026) | Box `api/.env` (Emil) |
| `AGENT_AUTO_TRADING` | `0` | `bid`/`sell`/`list` mit `rule_id` `auto-trading*` (22:30-Job, Stufe 2) — **dormant**, vom Intent-System abgelöst; nie gleichzeitig mit AUTO_INTENTS (doppelte Gebote) | `0` / ungesetzt | — |
| `AGENT_APPROVE_LIVE` | `0` | Discord-Freigabe-Pfad (`execute_staged` nach Button-Klick) auch ohne regulären Interlock | `0` / ungesetzt (Buttons antworten „nicht geärmt") | — |
| `AGENT_TEST_LEAGUE_ID` | leer | alle Typen, aber nur in dieser Liga | leer | — |
| `AGENT_TRADING_MAX_PER_PLAYER` | 1.500.000 | Obergrenze je Spieler für Auto-Trading | — | — |
| `AGENT_WRITE_SPACING_SECONDS` | 2.0 | Pause zwischen echten Geld-Writes | — | — |

Nicht-Arming-Variablen, die den Betrieb steuern: `DISCORD_BOT_TOKEN`/`DISCORD_CHANNEL_ID`
(Pflicht — fehlen sie, beendet sich der Worker sofort), `DISCORD_USER_ID` (leer = niemand darf
freigeben), `KICKBASE_LEAGUE_ID=1234567` (BASE; fail-closed bei falscher ID),
`AI_RESEARCH_ON_SERVER=false` (kein 08:30-Job im Container), `COCKPIT_HTML_PATH` (nur per
Compose gesetzt → Cockpit-Job aktiv), `KBA_API_HOST/PORT/RELOAD` (API-Service).

Regeln:
- **Kill-Switch und Scharfschaltung setzt Emil** in `/opt/kickbase-agent/api/.env` (nicht
  `/opt/kickbase-agent/.env` — die Datei gibt es nicht). Der Permission-Classifier blockt
  `.env`-Edits durch den Agenten; danach `--force-recreate worker api`.
- Nach jedem Scharfschalt-Schritt einen **echten Write beweisen**: Zeile in `transfer_actions`
  mit `status=executed` und `dry_run=0` (Abschnitt 6) — nie nur Flags zählen.
- `box_status` zeigt die Bool-Flags als 0/1; `AGENT_AUTO_INTENTS` erscheint dort nur als
  „set/empty" (nicht in der 0/1-Liste) — Wahrheit ist das `result` der Intent-Zeilen bzw.
  `docker exec kickbase-agent env | grep AGENT_`.
- Trades ≥ 5 M / Tier-1: Intents haben ein Mindest-Veto-Fenster von 2 h (außer Sniper); manuelle
  MCP-`place_bid`-Writes verlangen `emil_go`.

---

## 5. Tagesablauf (Cron-Plan, Europe/Berlin)

Quelle: `worker/__main__.py` (22 Jobs) + `worker/kickoff_guard.py` (Wächter). Alle Cron-Zeiten mit
Jitter (1–5 min). Jeder Post landet zusätzlich als Volltext in `reports` (Spalte `kind`).

**Discord seit 23.09.2026 (Emil: „Discord soll wieder senden. Aber nur einmal am Tag eine
Zusammenfassung"):** Im Digest-Modus (`DISCORD_DIGEST_ONLY`, Default an) steht „Discord" in
der Spalte Ausgabe nur noch für das Archiv. In den Kanal gehen allein: die Tageszusammenfassung
08:15 (einmal je Tag, atomarer Slot-Claim), Ergebnis-Wächter-Befunde Deckung/Gleitpfad binnen
24 h vor einer bekannten Spieltags-Frist, Alarme der versiegelten Anpfiff-Kaskade am Spieltag
(bewusste Ausnahme) und manuelle Posts mit `kind="blocker"`. Beleg:
`api/tests/worker/test_daily_summary.py`.

| Zeit | Job | Tut | Ausgabe |
|---|---|---|---|
| Start (`on_ready`) | Approval-Reload · Prämie · Anpfiff-Planer | Buttons reaktivieren, Bonus-Nachholer, Wächter (neu) planen inkl. Catch-up | Log; Discord nur bei echter Prämien-Gutschrift |
| 07:08 | Elf-Lücke-Planer | Später Elf-Lücken-Check bei T-8 (auch beim Start geplant): stellt einen kurz vor dem Anpfiff gewonnenen Spieler noch auf; bei Kader unter elf wird eine Teil-Elf gesetzt und per Rücklese geprüft (`unverified` = sofort in der App nachsehen) | Discord nur bei geschlossener Lücke oder Fehlschlag |
| 07:03 | Anpfiff-Planer | Spieltags-Deadline (erster Anpfiff) aus Fixtures; setzt `DateTrigger` T-120/T-60/T-35/T-15, 96-h-Horizont, Catch-up verpasster Marken | Log |
| 08:03 / 13:03 / 18:03 | Prämie | `bonus` via Executor; misst Konto-Delta (`executed` = Geld geflossen, `noop` = 0 €); Feed t=22 zeigt App-Kassierung; 18:03 warnt | Discord (08:03 primär), `transfer_actions` |
| 09:00 / 13:00 / 18:00 | Puls | Markt/Gebote/LigaInsider prüfen, nur bei Handlungsbedarf | Discord (leise) |
| 10:33 / 19:07 | News-Update | ① Lage (News/Transfers/Liga-Feed) ② Schlussfolgerungen | Discord, `news_items` |
| stündlich :22 | Cockpit-Build | `run_cockpit_build` → `/app/data/cockpit/index.html` (1–2 min, ~75 Detail-Calls) | Datei |
| stündlich :25 | Intent-Planner | Vorhaben aus `build_plan` anlegen/aktualisieren (Kickbase-Listing: Ablauf − 15 min; Manager: +2 h; Sniper < 30 min sofort; ≥ 5 M Mindestfenster 2 h). Wählt der Plan einen Kauf, räumt er geplante, unangepasste Handelsgebote vor dem Abgleich („Handel geräumt: n“); bindet ein festes Handelsgebot mehr, als der Plan zählte (währenddessen angepasst oder ausgeführt), ändert der Lauf keine Vorhaben („Plan überholt“); verworfene Engine-Gebote stehen mit Grund im Ergebnis („n verworfen“, Log `Engine-Gebot … verworfen`). Kein Write. | `intents`, Discord/`reports` kind `planner` |
| alle 5 min | Intent-Executor | fällige Vorhaben → Executor (`rule_id intent-<kind>`), atomarer Claim `executing`; Ergebnis ehrlich ins Vorhaben | `transfer_actions`, `intents.result`, kind `executor` |
| stündlich :47 | Gebots-Wächter | Eskalations-Erinnerung < 2 h Restzeit, Schnäppchen/Sniper-Hinweise (nie auto) | Discord, kind `bid_watch` |
| stündlich :52 | Reconcile | `unknown`/hängende `pending` gegen Live-Zustand auflösen; Alarm nur bei Unklärbarem | Log/Discord |
| Fr 19:30 | Lineup-Leak | LigaInsider-Startelf-Leak + Verletzungs-Alarm | Discord |
| Fr 19:45 · Sa/So 10:00 | Auto-Aufstellung (gated) | `lineup` via Executor, nur wenn der Spieltag noch nicht läuft | `transfer_actions`, Discord |
| 21:33 | Closing-Bell | Langzeit-Verletzte vor dem 22:00-MW-Update melden | Discord |
| 22:05 | Snapshot | Kader/Markt/Performance → `players`, `player_history`, `player_performance` (inkl. Selbstheilung Zukunftszeilen). `player_history` nur aus Kickbases Verlauf (je Spieler ein Abruf, Abweichungen berichtigt); steht das heutige Update dort noch nicht, fehlt der Tag bis zum nächsten Lauf. Log: „n ohne heutigen Wert, n berichtigt“ | DB, Log |
| Di 04:10 | Historie-Nachladen | Marktwert-Verlauf + Spieltage ALLER bekannten Spieler (gedrosselt, rein lesend gegen Kickbase); berichtigt Zeilen, die von Kickbases Verlauf abweichen; pausiert am Sperrfenster, bricht nach einer Fehlerserie ab. Von Hand: `kba history-reload`, nur zählen: `kba history-reload --check` (schreibt nichts, Exit 1 wenn nicht fertig) | DB, `job_runs` |
| 08:10 | Steiger-Radar | Bis zu fünf Marktwert-Steiger, rein lesend; sendet nicht selbst | kind `recherche` (Quelle `worker:steiger_radar`) |
| 08:11 | News-Radar | Kader-Warnungen („fehlt“ ≥ 0,95) und Rückkehrer am Transfermarkt (Jev ≥ 0,95) aus frischen kicker-Anreißern, rein lesend; sendet nicht selbst | kind `recherche` (Quelle `worker:news_radar`) |
| 08:12 · 22:35 | Handels-Radar (`Handels-Radar`, `Handels-Radar Abend`) | Kickbase-Auktionen 0,5–15 M, fit, KB-Stufe 1–3, ohne laufendes Gebot, mit laufender Welle (≥ +5 % in 3 Tagen, letzter Tag steigend; Verlauf von Kickbase); Höchstgebot = halber Median-Anstieg der Steigungsstufe bis zum Verkaufstermin (T−96 h bei offener Elf, sonst T−50 h), höchstens +30 %; Jev-Check je Kandidat; Handelsspieler aus dem Handelsbuch mit Verkaufssignal; rein lesend, sendet nicht selbst | kind `recherche` (Quelle `worker:trade_radar`) |
| :17 · :47 | Handels-Autopilot | Gebots-Vorhaben aus dem jüngsten Radar-Bericht (bis 130 % MW nur auf Kickbase-Auktionen, Rahmen 20 M, ≤ 4 offen, ≤ 4 je Tag; Deckung zum Anpfiff nach Verkauf zu 90 % MW ≥ 10.000 €; kein Handel auf einer Position unter dem Stamm-Minimum; kein neues Handelsgebot, solange die Engine ein Gebot plant, ihr Zyklus läuft oder ihr jüngster Plan (≤ 90 min) kauft, fehlt oder unlesbar ist), Sofortverkauf-Vorhaben nach dem ersten fallenden Update und am Verkaufstermin; kein Kickbase-Write, Ausführung im Intent-Executor | Vorhaben (`sources.trade`), kind `recherche` (Quelle `worker:trade_autopilot`, nur bei Änderung) |
| 08:15 | Tageszusammenfassung | Wächter-Befunde 24 h, Veto-Fristen 24 h, Handels-Radar, Steiger-Radar, News-Radar, dann das Briefing; ≤ 1900 Zeichen, sonst „… (mehr im Cockpit)" und Vollfassung als Bericht | **Discord (die eine Nachricht des Tages)**, kind `briefing` |
| :09/:24/:39/:54 | Ergebnis-Wächter | Gleitpfad, Deckung, Dauer-Minus, Statuswechsel, Dispo-Glättung; rein lesend | kind `blocker` (Quelle `worker:outcome_guard:befund`) → Tageszusammenfassung; sofort nur Deckung/Gleitpfad binnen 24 h vor der Frist |
| 22:15 | Briefing (Abend) | Tagesbriefing (Platz, Budget, Plan, Trading, Elf, LI-Status) | kind `briefing`, im Digest nur Archiv |
| 22:20 | Schatten-Scan | Proposals stagen + „hätte getan"-Report | `transfer_actions` (proposed), kind `schatten` |
| 22:30 | Auto-Trading | dormant: Schatten-Report | kind `auto_trading` |
| dynamisch | Spieltag T-120 / T-60 / T-35 / T-15 | Gebots-Storno (kickoff-bid-cancel) · Konto-Ping · Aufstellung final · Notbremse-Kaskade (kickoff-purge, nur mit AUTO_PURGE) | Discord, `transfer_actions` |
| (08:30, aus) | KI-Recherche | nur mit `AI_RESEARCH_ON_SERVER=true` (API-Guthaben) | — |

Außerhalb der Box: **08:27 Morgen-Routine** (Claude-Code Scheduled Task `kickbase-morgenroutine`):
`/kickbase recherche` (zweiteiliger Post, max 3 Tipps doppelt), `/kickbase cockpit`,
Selbstverbesserungs-Protokoll — keine Trades. Der alte launchd-Pfad `kba research` 08:30
(`deploy/com.emil.kickbase-research.plist`) ist Stand 23.08. **nicht** geladen.

Spieltags-Wächter: eine Deadline je Spieltag (Anpfiff des ersten Spiels, i. d. R. Fr 20:30) —
dort frieren Aufstellung **und** Konto ein. Konto beim Anpfiff < 0 = null Punkte für den ganzen
Spieltag; deshalb Purge-Kaskade (Trading-Material → Bank → Feld, Torwart zuletzt), Erfolg nur
nach frischem Konto-Reread ≥ 0.

---

## 6. Prüfen & Beobachten

**Box-Check** — MCP `box_status()` (Container, Startzeit, Flags 0/1, „Added job", Fehler, letzte
Bonus-Zeile; Stand 23.08. 17:30: `läuft · Jobs 22 · Fehler 0 · AUTO_BONUS=1 AUTO_LINEUP=1
AUTO_PURGE=1 DRY_RUN=0`). Fallback:

```bash
ssh root@203.0.113.10 'docker ps --filter name=kickbase --format "{{.Names}}\t{{.Status}}"; docker logs kickbase-agent 2>&1 | grep -c "Added job"; docker logs kickbase-agent 2>&1 | grep -ciE "error|traceback"; docker ps -a --filter name=worker-run --format "{{.Names}}"'
```

**Logs** (Worker, letzte 2 h; API analog):

```bash
ssh root@203.0.113.10 'docker logs kickbase-agent --since 2h 2>&1 | tail -n 200'
```

```bash
ssh root@203.0.113.10 'docker logs kickbase-api --since 2h 2>&1 | tail -n 100'
```

Nützliche Filter: `grep "API-WRITE"` (jeder echte Write mit Typ und Dauer), `grep "Running job"`,
`grep -i "refused\|skipped_dry_run\|noop"`.

**DB-Abfragen** (kein `sqlite3`-Binary im Image → Python; auf der Box nach `ssh root@203.0.113.10`):

```bash
docker exec kickbase-agent uv run python -c "import sqlite3; c=sqlite3.connect('/app/data/kba.db'); print(*c.execute('select id,created_at,action_type,status,dry_run,rule_id,amount,player_id from transfer_actions order by id desc limit 15'), sep='\n')"
```

```bash
docker exec kickbase-agent uv run python -c "import sqlite3; c=sqlite3.connect('/app/data/kba.db'); print(*c.execute('select id,kind,player_name,amount,status,execute_at,result from intents order by id desc limit 15'), sep='\n')"
```

```bash
docker exec kickbase-agent uv run python -c "import sqlite3; c=sqlite3.connect('/app/data/kba.db'); print(*c.execute('select id,created_at,kind,source,title from reports order by id desc limit 15'), sep='\n')"
```

Dasselbe über die API (Basic-Auth): `GET /api/protocol?limit=100`, `GET /api/intents?status=all`,
`GET /api/reports`, `GET /api/learning` — Details in `docs/API.md`. MCP: `audit_log`, `evaluation`.

**Cockpit-Zentrale** (`https://localhost:8080`): Tab „Zentrale" = Heute-Zeitachse
(Cron-Slots ✓/⏳), Vorhaben-Karten mit Countdown und Buttons (Jetzt · +10 % · +20 % · Betrag ·
Überspringen/Veto · Notiz), Protokoll (Aktionen, Vorhaben-Historie, KI-Entscheidungen aus
`knowledge/parameter_history.json` + LEARNINGS). Tab „Berichte" = Volltexte aus `reports`.

**Discord-Kanal:** jede Job-Nachricht ist dort; Fehler erscheinen als „⚠️ <Job>-Fehler: …"
(`_safe`-Wrapper — der Bot lebt weiter).

**Audit-Semantik** (`transfer_actions.status`, Status-Maschine aus `executor.py`):
`proposed` (wartet auf Freigabe, TTL 45 min) → `pending` (geclaimt, API-Call läuft) → `executed` /
`failed` / `unknown` (Timeout — kein blinder Retry, Reconcile klärt) / `refused` (Interlock oder
Preflight) / `rejected` / `expired` / `skipped_dry_run` / `noop` (200, aber nachweislich wirkungslos —
Prämie ohne Konto-Delta). Blockierend für Doppel-Writes: `pending`, `proposed`, `executed`, `unknown`;
retryable: `failed`, `refused`, `rejected`, `expired`, `skipped_dry_run`, `noop`. `duplicate` ist nur
ein Rückgabewert.

**Ehrliche Writes:** „200 OK" ist bei Kickbase kein Wirkungsnachweis. Beweis ist immer die Rücklese —
Konto-Delta, Feed-Ereignis (t=15 Transfer, t=22 Prämie), Kader `lo`, `own_bids`. Flags zählen nicht;
es zählt eine `executed`-Zeile mit `dry_run=0`.

---

## 7. Störungen & bekannte Fallen (Symptom → Ursache → Fix)

| # | Symptom | Ursache | Fix / Regel |
|---|---|---|---|
| 1 | Jobs feuern 2 h zu spät („22:15-Briefing" um 00:15) | nackte `CronTrigger` binden die Container-Systemzeit (UTC) bei der Konstruktion; Scheduler-TZ greift nicht | Wrapper `CronTrigger()` in `worker/__main__.py` (erzwingt Europe/Berlin) + `TZ` im Compose; prüfen: `docker exec kickbase-agent date` zeigt CEST, Log „next run at … +02:00" |
| 2 | Alle Container-Writes `skipped_dry_run`, „Box lief nie scharf" | `AGENT_DRY_RUN` fehlte in der Box-`.env`, Code-Default = 1 | Emil setzt `AGENT_DRY_RUN=0` in `api/.env` + `--force-recreate`; danach `executed`-Zeile mit `dry_run=0` beweisen |
| 3 | Flag gesetzt, wirkt nicht | in `/opt/kickbase-agent/.env` geschrieben — Compose liest `./api/.env` | nur `api/.env` pflegen |
| 4 | `.env` geändert, Container verhält sich alt | Compose startet Container nach reiner env-Änderung nicht zuverlässig neu | `docker compose --env-file api/.env up -d --force-recreate worker api`; `docker exec kickbase-agent env \| grep AGENT_` |
| 5 | Re-Bid auf Manager-Listing → HTTP 500 | `cancel_bid` verbrennt den Offer-Slot (ein Angebot je User+Listing) | **nie stornieren**, um den Preis zu ändern; stehen lassen oder per Chat verhandeln; Re-Versuch erst nach Re-Listing |
| 6 | Gebote „hängen" ewig / Preisanker falsch | zwei Mechaniken: Kickbase-Listing (`exs`-Countdown, Auto-Zuschlag ans höchste Gebot) vs. Manager-Listing (`u.n`, `exs=None`, Besitzer entscheidet manuell) | bei Manager-Listings an `prc` (Listing-Preis) ankern, Gebote aktiv managen; Planner: Manager = +2 h Fenster |
| 7 | „🎁 eingesammelt ✓", aber kein Geld | `/v4/bonus/collect` antwortet immer 200 + `it: []` | Konto-Delta messen (Executor: `executed` vs `noop`), Feed t=22 lesen; Offen: ob Collect die Auflaufprämie überhaupt auslöst (Test: Tag ohne App-Öffnung vor 08:03) |
| 8 | 200er-Ampel zeigt „≥N/200" + Engine-Sperre | Feed-Fenster voll (Saturations-Fail-Safe: Zählung ist nur Untergrenze) | Konstante `FEED_MAX_ITEMS=1000` in `trading/trade_budget.py` (Feed ist komplett abrufbar); wächst der Liga-Feed darüber: persistenter Zähler mit Dedupe via Item-ID `i` — Backlog |
| 9 | Gebote `refused`, obwohl Konto reicht | Preflight prüft die 33-%-Regel mit **offenen Geboten** (`pbaa`); ein großes offenes Gebot blockiert alles Weitere | auf Ablauf warten oder Verkäufe; bewusst kein Storno (siehe 5); Kaufkraft = Konto − offene Gebote + ⅓ Teamwert |
| 10 | `KickbaseAuthError` / 401 in Jobs | Token abgelaufen | Fabrik `make_kickbase_client` refresht automatisch (Refresh-Token bzw. Passwort-Relogin); dauerhaft: Emil erneuert Token/Login in `api/.env` + `--force-recreate`. Discord-Token: „Reset" **einmal**, jeder weitere Reset killt den vorigen |
| 11 | Streuner-Container mit altem Code, doppelte Posts | äußeres `timeout N docker compose run …` killt nur den Client, Container läuft weiter | `docker ps -a --filter name=worker-run` muss leer sein; Timeout **in** den Container; Einmal-Läufe lieber per `docker exec kickbase-agent …` im Hauptcontainer |
| 12 | Nach Deploy fehlen fremde Änderungen | parallele Sessions — rsync spiegelt den lokalen Stand | `git fetch` + Prüfsummen-Check (Abschnitt 3.1) vor jedem Deploy |
| 13 | Kaputte Umlaute (Mojibake) in Cockpit/Discord/Commit-Log | fehlende UTF-8-Deklaration bzw. Shell-Umwege (Heredoc, ssh-Quoting) | nginx `charset utf-8` + `<meta charset="utf-8">` sind gesetzt; Posts aus UTF-8-Dateien (`kba post-discord <datei>`); Commit-Messages ASCII (Repo-Konvention „fuer/naeher"), Single-Quotes für `-m` |
| 14 | Keine Spielerfotos im Artifact-Backup des Cockpits | Artifact-CSP blockt externe Bilder (`kickbase.b-cdn.net`) | erwartet — `theme.avatar()` fällt auf Initialen zurück; die Domain-Version zeigt Fotos |
| 15 | 504 unter localhost:8080 | Container in zwei Netzen, Traefik wählt die falsche IP | Label `traefik.docker.network: n8n_default` muss stehen |
| 16 | Doppelte Briefings/Writes | zweiter Worker (lokaler launchd `~/Library/LaunchAgents/com.emil.kickbase-agent.plist` oder Zweit-Bot) | Stand 23.08. nicht geladen — prüfen: `launchctl list \| grep kickbase` muss leer sein; nie zwei Worker |
| 17 | Abend-Post (Briefing/Scan) fehlt nach Deploy | Deploy im Sperrfenster 21:25–22:25, In-Memory-Crons ohne Catch-up | Fenster einhalten; nach Abend-Deploy Posts nachholen |
| 18 | Job meldet „executed successfully", tut nichts | `lambda: coro()` statt Coroutine-Funktion | immer `make_scheduled_job`-Wrapper |
| 19 | „✅ Discord-Bot online" fehlt im Log | `print()` gepuffert (kein TTY) | APScheduler-Logzeilen als Beweis |
| 20 | Tipps/Berichte fehlen in der Web-App | zwei DBs (Mac vs Box) | doppelt schreiben — MCP `tip_add`/`discord_post` spiegeln; CLI: zusätzlich `docker exec kickbase-agent uv run kba tip …` / `kba report-add …` |
| 21 | Elf oder Notbremse greifen nicht | Writes nach der Spieltags-Deadline sind wirkungslos (Elf + Konto sind eingefroren) | Wächter hängen an `matchday_deadline()`; Lineup-Jobs sind gated — kein Bug |
| 22 | Form-Scores aller Spieler ~0 / „alle Daten wirken falsch" | Zukunfts-Spieltage (0 Pkt/0 Min) in `player_performance` | Snapshot heilt selbst (löscht 0/0-Zeilen mit Datum ≥ heute); nur gespielte Spieltage importieren |
| 23 | Vorhaben steht mit „Lesefehler im Preflight (…, transient) — Versuch n/3“ | Kickbase antwortete beim frischen Lesen vor dem Write nicht (Netz, Timeout, 5xx, 429) | erwartet seit 02.10.2026 (L-123): bis zu 3 Wiederholungen je 10 min; bleibt Kickbase länger weg, endet es `failed` mit Tagessperre — am nächsten Tag plant der Planer neu. Inhaltliche Verweigerungen („Preflight: …“, „Spielplan leer“) werden nie wiederholt |

---

## 8. Hermes (zweiter Agent, Schattenmodus)

Hermes (Emils Claude-basierter Agent auf Hostinger KVM 1, `72.62.62.240`) soll den
Orchestrator-Job langfristig übernehmen (Emil spart einen Server). Bis zur Übergabe gilt das
**Ein-Schreiber-Prinzip**: nur die Claude-Session auf Emils Mac ist schreibberechtigt; Hermes
liest, recherchiert und postet mit Präfix `[HERMES]`. Protokoll + Checkliste:
`docs/archive/UEBERGABE-HERMES.md` (§5 Wechsel-Logistik), Erstauftrag `docs/archive/HERMES-ONBOARDING-PROMPT.md`.
Go/No-Go im SP1-Retro (So/Mo 30./31.08.).

**SSH-Gate** (verifiziert 23.08.): Der Key `hermes-kickbase` steht in `/root/.ssh/authorized_keys`
mit `command="/usr/local/bin/hermes-gate"` (forced command). Das Skript lässt nur diese
Kommandoformen durch, alles andere endet mit „hermes-gate: Befehl nicht auf der Allowlist" (exit 126):

| Erlaubt | Zweck |
|---|---|
| `docker ps …`, `docker logs …`, `docker inspect kickbase-agent …` | Box-Check |
| `docker exec kickbase-agent uv run kba research-data` | Recherche-Rohdaten |
| `docker exec kickbase-agent uv run kba tips` | aktive Tipps |
| `scp -t /tmp/hermes-*` | Datei-Upload **nur** nach `/tmp/hermes-*` |
| `docker cp /tmp/hermes-* kickbase-agent:/tmp/…` | Datei in den Container |
| `docker exec kickbase-agent uv run kba post-discord /tmp/hermes-*` | Bericht posten (Bot-Token bleibt auf der Box) |

Nicht erlaubt (und nicht auf die Allowlist setzen): `docker compose`/`build`, Executor-Aufrufe,
`.env`/DB-Zugriffe, `git push`, Wrapper wie `sh -lc`, Heredocs, `python -c`. Fehlt ein
Lese-Kommando → als neues `kba`-Subcommand vorschlagen, nicht umgehen. Emil kann ssh zur Box im
Hermes-Harness deshalb komplett auto-approven (Allowlist §7 in archive/UEBERGABE-HERMES.md, außer Kraft).

Übergabe-Schritte (Kurzfassung): Hermes liest SKILL/LEARNINGS/STRATEGIE/Protokoll →
2–3 Tage Schattenberichte → Emil vergleicht → Umschalten (Mac-Scheduled-Task aus, Hermes
übernimmt Morgen-Slot; Trades/Code nur noch durch Hermes) → alte Session bleibt Fallback.

---

## 9. Backup & Wiederherstellung

Der persistente Zustand liegt im Datenvolume: `kba.db` enthält Audit, Vorhaben und
Signalhistorie. Zusätzlich enthalten `league-history-<liga>.json` die archivierten
Managertransfers und Saisonleistungen sowie `strategy-sources-<liga>.json` die
beobachteten Spielerhistorien. Diese JSON-Dateien mit ihren Originalnamen und dem
Sicherungszeitpunkt ebenfalls sichern; die folgenden SQLite-Befehle erfassen sie nicht.
Eine spätere vollständige Wiederbeschaffung alter API-Daten ist nicht garantiert.
Der **Kickbase-Zustand selbst** (Kader, Konto, Gebote, Aufstellung) liegt bei Kickbase
und bleibt bei jedem lokalen Datenverlust die Wahrheit.
Konsistente SQLite-Sicherung (Backup-API statt roher Dateikopie, der Worker schreibt laufend):

```bash
ssh root@203.0.113.10 'docker exec kickbase-agent uv run python -c "import sqlite3; s=sqlite3.connect(\"/app/data/kba.db\"); d=sqlite3.connect(\"/app/data/kba-backup.db\"); s.backup(d); d.close(); s.close()" && mkdir -p /opt/kickbase-agent/backup && docker cp kickbase-agent:/app/data/kba-backup.db /opt/kickbase-agent/backup/kba-$(date +%F-%H%M).db && docker exec kickbase-agent rm /app/data/kba-backup.db && ls -la /opt/kickbase-agent/backup'
```

Kopie auf den Mac (Pfad quoten):

```bash
scp root@203.0.113.10:/opt/kickbase-agent/backup/kba-<datum>.db "/Users/emil/Mein Claude /Kickbase-Agent/api/backup-box-kba.db"
```

(`*.db` und die SQLite-Journale sind per `.gitignore` ausgeschlossen — landet nie im Repo. Die Endung zählt: bis 27.08. galt nur `*.db`, weshalb die SQLite-Journale `kba_local.db-shm`/`-wal` versioniert waren und per rsync auf der Box landeten.) Empfehlung: vor jedem Deploy
mit neuer Alembic-Migration und vor Spieltagen sichern; zusätzlich Hostinger-VPS-Snapshot für
die ganze Box (Emil, Panel/API).

**SQLite wiederherstellen** (Worker + API stoppen, Datei ins Volume, starten — der Worker-Entrypoint
fährt `alembic upgrade head`, eine ältere Sicherung wird dabei auf das aktuelle Schema gehoben):

```bash
ssh root@203.0.113.10 'cd /opt/kickbase-agent && docker compose stop api worker && docker cp /opt/kickbase-agent/backup/kba-<datum>.db kickbase-agent:/app/data/kba.db && docker compose start worker api'
```

Bei einer vollständigen Wiederherstellung zusätzlich die gesicherten
`league-history-<liga>.json` und `strategy-sources-<liga>.json` bei gestopptem Worker
und API in `/app/data/` zurückkopieren, **bevor** die Container gestartet werden.
Danach den Cockpit-Build und die Historienabdeckung in `/api/strategy` prüfen.

**Was bei Datenverlust weg wäre:** Audit-Log (`transfer_actions` — damit auch die Idempotenz-Anker
des Tages: der Executor könnte heute bereits erledigte Aktionen erneut versuchen), Vorhaben
(`intents`), Berichte (`reports`), Tipps (`community_tips`, wirken 4 Tage), Marktwert-/Performance-
Historie (Trend-Signale starten neutral; Backfill holt MW-Historie beim nächsten Snapshot nach),
News sowie die separat gespeicherten Transfer- und Spielerarchive, wenn deren
JSON-Sicherungen fehlen. Nicht weg: Code, Flags (`api/.env`), Kickbase-Zustand, LEARNINGS (Repo).

**Migrationen:** Schema-Hoheit hat ausschließlich der Worker-Entrypoint; die API migriert nie. Bei
neuen Migrationen lokal `uv run alembic upgrade head` testen, dann Deploy (Worker startet zuerst).
Migrationsstand im Container: `docker exec kickbase-agent uv run alembic current`.

**DB-Heilung (Erfahrung 20.08.):** 1938 Zukunfts-Zeilen (Saison 26/27, 0 Punkte/0 Minuten) in
`player_performance` drückten die Form-Scores aller Spieler auf ~0. Fix ist im Snapshot verankert:
nur gespielte Spieltage importieren, vorhandene Zukunftszeilen bei jedem 22:05-Lauf löschen
(idempotent, keine Migration nötig). Beim Einspielen alter Sicherungen heilt der nächste Snapshot also selbst.

---

## 10. Checklisten

**Vor Deploy**
- [ ] Uhrzeit außerhalb 21:25–22:25 Berlin
- [ ] `git fetch` + `git status -sb` sauber, keine fremden Commits hinter HEAD; Prüfsummen Box vs lokal verglichen
- [ ] Tests grün (`uv run --extra dev pytest -q`), `ruff check` sauber
- [ ] Neue Migration? → DB-Sicherung (Abschnitt 9)
- [ ] Marker für den Verify notiert (neue Funktion/Text)
- [ ] rsync mit `--exclude='.env'`; bei Compose-/API-/nginx-Änderungen Einzeiler statt `deploy_verify`

**Nach Deploy**
- [ ] Marker-grep im Container trifft
- [ ] `docker ps`: drei Container `Up`; „Added job" = 22 (+ Wächter); Fehlerzeilen 0
- [ ] `/api/health` extern 401 ohne Auth / 200 mit Auth, intern `{"status":"ok"}`
- [ ] Cockpit-Build getriggert, Seite lädt, Zentrale zeigt Vorhaben/Protokoll
- [ ] Bei `.env`-Änderung: `--force-recreate worker api` und `env | grep AGENT_` geprüft
- [ ] LEARNINGS auf der Box (`/app/data/learnings.md`) aktuell
- [ ] Abend-Deploy nach 22:25: ausgefallene Posts nachgeholt
- [ ] Eine Zeile in Discord: was deployt wurde + Verify-Ergebnis

**Vor Spieltag** (Do/Fr)
- [ ] `box_status` grün; Wächter stehen: `docker logs kickbase-agent 2>&1 | grep 'Added job "job_Spieltag'` zeigt T-120/T-60/T-35/T-15 (ab 96 h vor der Deadline; Job-IDs `kickoff-T-…-md<N>`)
- [ ] `AGENT_AUTO_LINEUP=1`, `AGENT_AUTO_PURGE=1`, `AGENT_DRY_RUN=0` (Box)
- [ ] `recommend_lineup()` geprüft — 11 aufstellbare Spieler, keine KB „Ausgeschlossen" in der Elf
- [ ] Konto ≥ 0 **inklusive** möglicher Zuschläge offener Gebote (`own_bids`); kein Gebot, dessen Zuschlag das Konto sprengen würde
- [ ] Kein Deploy zwischen T-120 und Anpfiff; Sperrfenster am Abend beachten
- [ ] Nach dem Spieltag: Elf vs LigaInsider-Prognose, Fazit in Discord (`/kickbase spieltag`)

**Scharf schalten / entschärfen** (Emil)
- [ ] Scharf: Flag in `/opt/kickbase-agent/api/.env` setzen (z. B. `AGENT_AUTO_INTENTS=1`), `docker compose --env-file api/.env up -d --force-recreate worker api`, dann echten Write beweisen (`executed`, `dry_run=0`) — nie nur Flags zählen
- [ ] Entschärfen (Not-Aus): `AGENT_DRY_RUN=1` in `api/.env` + `--force-recreate worker api` → alles `skipped_dry_run`; gezielt: einzelnes `AGENT_AUTO_*` auf 0; laufende Vorhaben in der Zentrale per Veto stoppen
- [ ] Nie `AGENT_AUTO_TRADING=1` zusammen mit `AGENT_AUTO_INTENTS=1` (doppelte Gebote)
- [ ] Lokal: `.mcp.json` ärmt MCP-Writes (`AGENT_DRY_RUN=0`, `AGENT_ALLOW_LIVE=1`) — wer das nicht will, entfernt das `env`-Objekt und startet die Session neu
- [ ] Änderung als Decision-Record festhalten (`knowledge/parameter_history.json` bzw. LEARNINGS), Discord-Hinweis

## Strategie-Workspace seit 11.09.2026

Der Cockpit-Build erzeugt `index.html` (drei neue Hauptansichten), `legacy.html`
(bisherige Bedienfunktionen), `strategy.json` (gemeinsame Operator-/UI-Daten) und seit
01.10.2026 `jev.html` (Jev-Seite: Protokoll, Trennschärfe, Treffer-Bilanz aus
`jev_judgments`; ein Lesefehler leert nur diese Seite, nie den Build).
Der Build nimmt `.build.lock` im Zielverzeichnis prozessübergreifend; Worker und
manueller API-Refresh können sich nicht gegenseitig unvollständige Snapshots schreiben.
Alle Renderer laufen vor dem ersten Dateitausch, index wird zuletzt atomar ersetzt.

Direkte historische API-Werte werden im Volume in `strategy-sources-<liga>.json`
zwischengespeichert. Zwölf Spieler je Build ergänzen die ältesten Daten. Fehlversuche
haben eigene Zeitstempel und blockieren keine anderen Spieler. Der einmalige Bootstrap-
Tag 20.08.2026 wird ohne direkte API-Bestätigung als unsicher geführt.

RSS: kicker, Sportschau und GEISSBLOG. Cache 20 Minuten; Abruf erfolgt beim bestehenden
Cockpit-Build, kein zusätzlicher Nachrichtenalarm. Kuratierte Artikel liegen in
`knowledge/editorial_news.json` und behalten ihre Veröffentlichungs-/Ablaufzeiten.
Ausfälle und nicht aktualisierte Quellen sind in `/api/strategy` sichtbar.

Prüfen: Hauptseite + legacy.html erreichbar; `/api/strategy` hat aktuellen Datenstand,
passende Liga-/Kaderbilanz und Frist. Eine lebende API oder erfolgreicher Build beweist
weder rentable Trades noch einen laufenden Modell-Judge.

### Gemeinsame Managerübersicht seit 12.09.2026

Die Hauptnavigation besteht aus Heute, Übersicht und Liga. Übersicht verwendet für
alle Manager dasselbe Profil; Liga-Zeilen öffnen dieses Profil. Die Saisonkurve,
Kaderkosten, Transferhistorie und Ergebniszerlegung stammen aus `manager_profiles`
in `/api/strategy`. Fremde Kontostände bleiben unbekannt: Transfergeldfluss allein
enthält weder alle Prämien noch den vollständigen Startzustand. Fehlende Kaufpreise
und Historienpunkte bleiben als Datenlücken sichtbar.

`league-history-<liga>.json` hält die paginierten Managertransfers und Leistungsdaten
im Datenvolume. Pro Cockpit-Build ergänzen höchstens acht Zusatzaufrufe den Cache;
Managerdaten werden rotiert (Transfers sechs Stunden, Leistung zwei Stunden,
Ligaübersicht eine Stunde). Der dokumentierte Saisonstart trennt frühere Saisons
von der aktuellen Bilanz. Ein Archivimport ergänzt ältere Daten, ohne jüngere
Cacheeinträge zu ersetzen. Die bestehenden Spielerhistorien bleiben in
`strategy-sources-<liga>.json` und werden mit ihrem tatsächlichen Abrufalter geführt.

Heute zeigt `action_plan`: offene Gebote, Vorhaben und nächste finanzierbare
Prüfschritte. Ein Vorschlag ist keine Ausführung; die bestehenden Planner- und
Executorregeln bleiben maßgeblich. Marktfristen beziehen sich auf den tatsächlichen
Abrufzeitpunkt und werden nach Abschluss der Datensammlung erneut geprüft.

Nach einem Release prüfen: zwölf Managerprofile, Spieltagshistorie, Managerwechsel,
Spielerdetail mit Kauf-/Verkaufsbilanz, negative Werte und unbekannte Kaufkosten.
