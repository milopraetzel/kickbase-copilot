# Schnittstellen des Kickbase-Agenten

Stand 23.08.2026 · Alle Wege, über die ein Mensch oder eine KI das System bedient.
Belege stehen als `Datei:Funktion` (relativ zu `api/src/kickbase_agent/`). Begriffe:
[`GLOSSAR.md`](GLOSSAR.md).

## 1. Überblick

| Bedienweg | Für wen | Einstieg | Code |
|---|---|---|---|
| **MCP-Server `kickbase`** (24 Tools) | KI-Agenten (Claude Code, Morgen-Routine, später Hermes) | `.mcp.json` → `uv run --directory api kba-mcp` (stdio) | `mcp/server.py`, `mcp/tools_read.py`, `mcp/tools_ops.py`, `mcp/tools_write.py`, `mcp/_util.py` |
| **HTTP-API** (FastAPI) | Web-App „Cockpit“ (Zentrale, Berichte, Lernen) | `kba-serve`; auf der Box `https://localhost:8080/api/…` hinter Basic-Auth | `server/app.py`, `server/routes/*.py`, `server/schemas.py` |
| **CLI `kba`** (Typer) | Terminal, Cron, `docker exec` auf der Box | `cd api && uv run kba <kommando>` | `cli/__init__.py` + `cli/*.py` |
| **Skill-Routinen `/kickbase …`** | Orchestrierung der drei Wege durch Claude Code | Skill `kickbase` (`.claude/skills/kickbase/SKILL.md`) | — |

**Sicherheitsprinzip.** Jeder Kickbase-Write (Gebot, Listing, Verkauf, Storno,
Aufstellung, Prämie) läuft ausschließlich durch `agent/executor.py:ActionExecutor.execute`
— mit Audit-Zeile in `transfer_actions`, Idempotenz, Kill-Switch `AGENT_DRY_RUN`,
Live-Interlock je Aktionstyp (`ActionExecutor._live_allowed`) und Preflight vor
Geld-Writes (`default_preflight`, `_preflight_veto`). MCP-Write-Tools rufen nur den
Executor (`mcp/tools_write.py:_execute`), die HTTP-API mutiert nur Zeilen der Tabelle
`intents` (`server/routes/intents.py`), die CLI enthält keinen Trade-Befehl. Zusätzliche
Gates der Tools: `confirm=True` für live, `emil_go` (Emils Wortlaut + Zeit) ab 5 M €
bzw. bei jedem Sofortverkauf, Deploy-Sperrfenster 21:25–22:25 Berlin.

## 2. MCP-Server `kickbase`

### 2.1 Start und Registrierung

- **Registrierung** (`.mcp.json` im Repo-Root): `{"mcpServers": {"kickbase": {"command": "uv",
  "args": ["run", "--directory", "api", "kba-mcp"], "env": {"AGENT_DRY_RUN": "0",
  "AGENT_ALLOW_LIVE": "1"}}}` — die beiden `env`-Werte ärmen den lokalen MCP-Prozess für
  Trades (Regel „nur Executor“ bleibt; ohne `confirm=True` entsteht trotzdem nur eine
  Dry-Run-Vormerkung).
- **Prozess:** Console-Script `kba-mcp = kickbase_agent.mcp.server:main` (`api/pyproject.toml`),
  FastMCP, Transport **stdio**; Logs ausschließlich nach stderr (`mcp/server.py:main`).
  Der Server läuft **lokal** mit `api/.env`; Box-Operationen gehen per
  `ssh root@203.0.113.10` (`mcp/_util.py:BOX_HOST`, `run_ssh`, `docker_exec`).
- **Liga/Client:** je Aufruf `make_kickbase_client()`; Liga aus `KICKBASE_LEAGUE_ID`,
  sonst die priorisierte Account-Liga (`mcp/tools_read.py:_ctx`).
- **Tool-Annotationen** (`mcp/server.py:_register`): Read `readOnlyHint=True`; Ops
  `destructiveHint=False`; Write `destructiveHint=True`. Die Server-`INSTRUCTIONS`
  fassen Gruppen, Gates und Geldformat zusammen.

### 2.2 Rückgabekonventionen (`mcp/_util.py`)

| Konvention | Beleg |
|---|---|
| Geld immer doppelt: `<prefix>_eur` (int) + `<prefix>_fmt` (lesbar: „7,41 M“, „850 k“, „120 €“) | `money_fields`, `fmt_money` |
| Fehler nie als Traceback, sondern `{"error": "<Typ>: <Text>", "hint": "<Handlung>"}` | `tool_guard`, `error_dict`, `hint_for` (Auth → `kba login`; Timeout; HTTP-Status …) |
| Zeitstempel ISO in Europe/Berlin (`iso`), Kickbase-Rohzeiten UTC → Berlin | `iso`, `BERLIN` |
| Positionen als `TW`/`ABW`/`MIT`/`STU` (`pos_name`), Startelf-Stufe `lineup_label` (1 Sicher … 5 Ausgeschlossen) | `_POS_NAMES`, `LINEUP_LABELS` |
| Club-Namen aus `scouts/ligainsider/teams.py:TEAMS` (`club_name`) | — |
| Keine Secrets: Flags nur als 0/1 (`settings_flags`, `tools_ops._flags`) | Invariante 2 der Spec |
| Langläufer melden `duration_s` | `research_data`, `recommend_lineup`, `deploy_verify`, `cockpit_build`, `league_state` |

### 2.3 Tool-Katalog (alle 24 Tools)

**Gruppe READ — ohne Nebenwirkung** (`mcp/tools_read.py`)

| Tool | Parameter (Typ = Default) | Liefert | Dauer / Hinweise |
|---|---|---|---|
| `league_state` | — | `budget`, `budget_after_bids` (Kickbase `pbaa`), `team_value`, `squad_count`, `open_bids`, `open_bid_exposure`, `kaufkraft`, `kaufkraft_after_bids`, `overdraft_floor`, `phase` (invest/purge/hold), `trade_budget{used,limit,saturated,blocked,line,my_name}`, `next_kickoff`, `matchday_deadline{matchday,at}`, `now` | Sekunden; 33-%-Regel aus `trading/finance.py`, Ampel aus `trading/trade_budget.py`, Deadline aus `worker/kickoff_guard.py:matchday_deadline` |
| `squad` | — | `players[]` mit id, name, pos, club, mv/mv24, `mv_trend`, `lineup_prob`, `fitness_status` (0 fit, 2 angeschlagen), `in_lineup`/`lineup_order` (`lo`), `listed`, `points`, `img`; Summen `team_value`, `in_lineup`, `listed` | nach MW sortiert |
| `market` | `limit: int = 60`, `sort: str = "score"` (score·mv·mv24·price·expires·prob) | `players[]` mit price, `price_vs_mv_pct`, `seller` (""=Kickbase), `listing_type` (kickbase/manager), `expires_in_s`/`expires_at`, `bid_ours`, `in_squad`, `score`; `score_note` | Kauf-Score ohne LigaInsider-Veto (vollständig: `research_data`); unbekannter `sort` → Fehler mit Hint |
| `own_bids` | — | `bids[]`: player_id, name, club, `amount`, `listing_price`, `amount_vs_listing_pct`, `seller`, `listing_type`, `expires_in_s`, `offer_id`; `exposure` | live aus dem Markt (`client.get_own_bids`) |
| `player` | `player_id: str` | Member-Detail: `lineup_prob`, `fitness_status`/`fitness_text` (st/stxt), `status_list`, `total_points`/`avg_points` (tp/ap), `last_matches` (ph, letzte 5), mv/mv24 (tfhmvt), goals/assists/yellow/red, `seconds_played`, `plpt`/`plpurl`, `img`, `raw_keys` | Endpoint `player_detail`; Nachname kommt als `ln` |
| `feed` | `limit: int = 50`, `types: list[int] \| None = None` | `items[]`: `i`, `t` (15 Transfer, 3 Markt-Event, 22 Prämie), `dt`, `summary`, `data` | ohne `types` werden `limit` Items geholt, mit Filter bis zu 1000 (`FEED_MAX_ITEMS`) und dann gefiltert |
| `audit_log` | `limit: int = 30`, `status: str \| None = None` | `actions[]` aus `transfer_actions`: created_at, resolved_at, type, status, dry_run, rule_id, player_id, amount, rationale (≤ 300 Zeichen) | neueste zuerst; mit Remote-Konfiguration Box `/audit`, sonst lokale DB |
| `active_tips` | — | `tips[]` (id, player_name, weight, source, note, created_at), `ttl_days` = 4 | mit Remote-Konfiguration Box `/tips`, sonst lokale DB |
| `research_data` | — | `text` (Ausgabe von `kba research-data`), `lines`, `duration_s` | **~1 min** (LigaInsider + Feeds); `cli/research.py:_dump_data` |
| `recommend_lineup` | — | `formation`, `expected_score`, `starters[]`/`bench[]` (elf_score, kauf_score, li_status/li_reason/li_confidence, lineup_prob + label), `quarantines[]`, `li_status_counts`, `note` | **~20–60 s**; `decision/lineup_service.py:recommend_lineup`, 9 Formationen (`decision/lineup.py:_FORMATIONS`) |
| `team_profile` | `team: str` (Team-ID oder Clubname/Teilstring), `min_prob: int \| None = None`, `sort: str = "avg_points"` (avg_points·mv·mv7d·prob) | `team{id,name,img}`, `players[]` (name, pos, mv, `mv7d` = sdmvt, mv_trend, avg_points, lineup_prob + label, fitness_status, on_market, offers, img), `count`, `total_in_squad`, `filter` | alle Spieler eines Clubs, auch ohne Markt-Listing; mehrdeutiger Name → Fehler mit Kandidatenliste |
| `evaluation` | — | identisch mit `GET /api/learning`: `evaluation{buy_hits,shadow,executed,tips,flips,…}`, `learnings[]` (je Eintrag `id`, `art`, `status`, `evidence`, `evidence_items`), `parameters{history,current}`, `open_calibrations[]`, `bilanz`, `to_fix[]`, `selbstverbesserungen[]` | mit Remote-Konfiguration Box `/learning`, sonst lokale DB |

**Gruppe OPS — Nebenwirkung, aber kein Kickbase-Write** (`mcp/tools_ops.py`)

Remote-Konfiguration: `KBA_REMOTE_API_URL` und `KBA_REMOTE_API_AUTH`, alternativ
`KBA_API_URL` und `KBA_API_AUTH`. Remote-Leseantworten der drei DB-Tools tragen
`remote=true` und `data_source=box`; Auth-/Verbindungsfehler liefern keine lokale
Ersatzbilanz. `market`, `research_data` und `recommend_lineup` verwenden weiterhin
lokale Signalhistorie. Die neuen `/audit`-/`tips`-Routen müssen zuvor auf der Box
bereitgestellt sein; ein alter API-Stand liefert einen ausdrücklichen Fehler.

| Tool | Parameter | Liefert | Gates / Dauer |
|---|---|---|---|
| `box_status` | — | `running`, `container_status`, `started_at`, `flags` (AGENT_* nur 0/1 bzw. set/empty), `jobs_added`, `error_lines`, `last_bonus_line`, `summary` | ssh (Timeout 60 s); `status: failed` wenn Box unerreichbar |
| `deploy_verify` | `markers: list[str]` (Pflicht, ≥ 1), `settle_seconds: int = 25` | `status` ok/check/failed/refused, `markers{marker: Treffer}`, `all_markers_found`, `running`, `jobs_added`, `error_lines`, `rsync_rc`, `compose_tail`, `finished_at` | **verweigert 21:25–22:25 Berlin** (`_util.deploy_window_blocked`); rsync `api/` ohne .env/.git/.venv/DB (`RSYNC_EXCLUDES`) → `docker compose up -d --build worker` → Marker-grep in `/app/src`; **Minuten** (Timeout 600 s) |
| `cockpit_build` | — | `status`, `bytes`, `output`, `url` (https://localhost:8080), `finished_at` | `docker exec … run_cockpit_build()`; **1–2 min** (Timeout 300 s) |
| `discord_post` | `text: str`, `kind: str = "info"` (briefing·recherche·schatten·auto_trading·planner·executor·retro·bid_watch·info·sonstiges) | `posted`, `chars`, `message_id`, `report_id` (lokal), `kind`, `box{ok, report_id}` | **≤ 1900 Zeichen, sonst Fehler (keine Kürzung)**; `kind` „info“/leer → aus erster Zeile geraten (`notify/report_store.py:infer_kind`); Bericht lokal + Box-Spiegel (`kba report-add` via ssh) |
| `tip_add` | `name: str`, `weight: float = 0.6` (−1.0 … +1.0), `source: str = "ki-recherche"`, `note: str = ""` | `local{id,…}`, `box{ok,output}` | schreibt **doppelt** (lokale DB + Box via `docker exec … kba tip`); wirkt 4 Tage ±0.08·Gewicht |
| `learning_evidence` | `learning_id: str` (`L-065`), `verdict: str` (confirm\|refute), `note: str`, `ref: str` | `{created, source: "agent", learning_id, verdict, ref, message}` | Beleg zu einer Erkenntnis (Lern-Bilanz); `source` ist IMMER `agent` und nie wählbar; derselbe `ref` zählt je Erkenntnis nur einmal; mit `KBA_REMOTE_API_URL` an die Box-API statt lokal |

**Gruppe WRITE — nur über den ActionExecutor** (`mcp/tools_write.py`)

| Tool | Parameter | Gates | Antwort-Felder |
|---|---|---|---|
| `collect_bonus` | — | scharf nur mit `AGENT_AUTO_BONUS=1` + `AGENT_DRY_RUN=0`; Executor misst Konto-Delta | `status` (executed / noop / skipped_dry_run / refused), `messages[]`, `audit`, `flags` |
| `place_bid` | `player_id: str`, `amount_eur: int`, `rationale: str`, `confirm: bool = False`, `emil_go: str = ""` | `confirm=False` → Dry-Run-Vormerkung; live nur geärmt (`AGENT_DRY_RUN=0` + `AGENT_ALLOW_LIVE=1`); **≥ 5 000 000 € → `emil_go` Pflicht** (`EMIL_GO_THRESHOLD_EUR`; Tier-1 liegt immer darüber), sonst `refused` ohne Executor-Aufruf; rule_id `mcp-bid`; Preflight (Markt, +10 % Drift, 33 %) | `status`, `action_id`, `message`, `live_requested`, `executor_called`, `emil_go_required`, `flags`, `audit`, `raw_response`, `amount_*` |
| `list_player` | `player_id: str`, `price_eur: int`, `rationale: str`, `confirm: bool = False` | wie oben, kein `emil_go`-Gate; rule_id `mcp-list`; Preflight Kader + ±20 % MW-Drift | `status`, `price_*`, … |
| `sell_player` | `player_id: str`, `rationale: str`, `confirm: bool = False`, `emil_go: str = ""` | **live immer mit `emil_go`**; Betrag = aktueller MW aus dem Kader; rule_id `mcp-sell`; Spieler nicht im Kader → Fehler | `status`, `player_name`, `market_value_*`, … |
| `delist_player` | `player_id: str`, `rationale: str`, `confirm: bool = False`, `emil_go: str = ""` | Eigenes Listing zurückziehen (seit 22.09.2026, Emils Auftrag 21.09.) — im Remote-Modus als Vorhaben `kind=delist` auf der Box (`POST /api/intents`, `via=mcp`, Betrag 0), lokal rule_id `mcp-delist`; **kein `emil_go`-Gate** (baut Risiko ab wie `cancel_bid`), kein Engine-Plan, Verfassung sperrt nicht (Delist ist kein Abgang); `confirm=False` → `preview_only` ohne Netzaufruf bzw. lokale Dry-Run-Vormerkung; Spieler nicht im Kader → Fehler. Nur auf Emils namentlichen Auftrag für genau diesen Spieler (CLAUDE.md §5.12). Hinweis: `AGENT_AUTO_INTENTS` ärmt im Executor nur bid/sell/list/accept — ein `intent-delist-manual` wird live nur mit `AGENT_ALLOW_LIVE=1` (oder Test-Liga) ausgeführt, sonst ehrlich `refused` | `status`, `player_name`, `intent` (remote) bzw. `audit`, … |

Status-Bedeutung (`agent/executor.py`, Modul-Docstring): `executed` = Live-Write bestätigt ·
`skipped_dry_run` = nur protokolliert (Dry-Run/Kill-Switch) · `refused` = Interlock oder
Preflight · `failed` · `unknown` (Timeout) · `noop` (Prämie ohne Konto-Delta) · `duplicate`.

### 2.4 Beispiele

Read/Scouting:

```
team_profile("Bayern", min_prob=2)
→ {"team": {"id": "2", "name": "Bayern", …}, "count": 14, "total_in_squad": 27,
   "filter": {"min_prob": 2, "sort": "avg_points"},
   "players": [{"id": "8329", "name": "Olise", "pos": "MIT", "mv_eur": 64000000, "mv_fmt": "64,00 M",
                "mv7d_eur": 1200000, "mv7d_fmt": "1,20 M", "avg_points": 171, "lineup_prob": 1,
                "lineup_label": "Sicher", "fitness_status": 0, "on_market": false, "offers": 0, …}, …]}

recommend_lineup()
→ {"formation": "4-3-3", "expected_score": 0.61, "count": 11,
   "starters": [{"name": "Minjae Kim", "pos": "ABW", "elf_score": 0.72, "kauf_score": 0.64,
                 "li_status": "starter", "li_reason": "…", "lineup_prob": 1, "lineup_label": "Sicher", …}, …],
   "bench": [...], "quarantines": [], "li_status_counts": {"starter": 11, "bench": 3, "unknown": 1},
   "note": "Elf-Score steuert die Aufstellung (ohne Markt-Trend); Kauf-Score den Handel. …",
   "duration_s": 38.2}

evaluation()
→ {"now": "…", "evaluation": {"buy_hits": {"1d": {"n": 12, "hit_rate": 0.58, …}, "7d": {…}},
                              "shadow": {…}, "executed": {…}, "tips": {…}, "flips": [...],
                              "flips_total_delta_eur": -120000, "lineup": {"status": "ab SP1 (28.08.) messbar", "n": 0},
                              "coverage": {…}},
   "learnings": [{"date": "2026-08-23", "title": "…", "text": "…"}],
   "parameters": {"history": [{"date": "2026-08-23", "parameter": "bid_overpay_fraction",
                               "old": "0.05", "new": "0.08", "reason": "…", "commit": "da06d03",
                               "expect": "…", "review": "…"}], "current": {"buy_score_threshold": 0.53, …}},
   "open_calibrations": ["Signal-Gewichte 0.40 / 0.25 / 0.20 / 0.15 …", …]}
```

Write — Vorschau (kein `confirm`): Dry-Run-Vormerkung mit Audit-Zeile

```
place_bid(player_id="99", amount_eur=5_120_000, rationale="Starter, unterbewertet")
→ {"status": "skipped_dry_run", "action_id": 812, "live_requested": false, "executor_called": true,
   "emil_go_required": true, "amount_eur": 5120000, "amount_fmt": "5,12 M",
   "flags": {"agent_dry_run": false, "agent_allow_live": true, …},
   "audit": {"type": "bid", "status": "skipped_dry_run", "dry_run": true, "rule_id": "mcp-bid", …}}
```

Write — live ohne Emils GO über der Schwelle: verweigert, Executor wird nicht gerufen

```
place_bid("99", 5_120_000, "Starter", confirm=True)
→ {"status": "refused", "reason": "Gebot 5,12 M ≥ 5,00 M — Emils GO (Wortlaut + Zeit) ist für live Pflicht.",
   "hint": "emil_go=\"Emil 23.08. 10:42: mach den Trade\" mitgeben — oder confirm=False für die Dry-Run-Vorschau.",
   "executor_called": false, "player_id": "99", "amount_eur": 5120000, "amount_fmt": "5,12 M"}
```

Write — live, geärmt, mit GO: ausgeführt (bzw. `refused` mit `message` „… AGENT_ALLOW_LIVE=0 …“, wenn nicht geärmt)

```
place_bid("99", 5_120_000, "Starter", confirm=True, emil_go="Emil 23.08. 10:42: mach den Trade")
→ {"status": "executed", "action_id": 813, "live_requested": true, "executor_called": true,
   "raw_response": {…}, "audit": {"status": "executed", "dry_run": false, "rule_id": "mcp-bid",
   "rationale": "Starter | Emil-GO: Emil 23.08. 10:42: mach den Trade", …}}

list_player("17", 26_000_000, "Listing ~103 % für Computer-Angebot")
→ {"status": "skipped_dry_run", "price_eur": 26000000, "price_fmt": "26,00 M", …}

sell_player("2", "Achilles — Langzeit", confirm=True)            # ohne emil_go
→ {"status": "refused", "reason": "Sofortverkauf — Emils GO (Wortlaut + Zeit) ist für live Pflicht.", "executor_called": false, …}

collect_bonus()
→ {"status": "noop" | "executed" | "skipped_dry_run", "messages": ["…"], "audit": {…}, "flags": {…}}
```

## 3. HTTP-API (FastAPI `kba-serve`)

**Basis.** `server/app.py:create_app` — Titel „Kickbase Agent API“ 0.1.0, alle Router
unter `/api`. Lokal `uv run kba-serve` → `127.0.0.1:8000` mit Reload; im Container
(`docker-compose.yml`, Service `api` = Container `kickbase-api`) `KBA_API_HOST=0.0.0.0`,
`KBA_API_PORT=8000`, `KBA_API_RELOAD=0` (`server/__main__.py:serve_config`). Öffentlich:
`https://localhost:8080/api/…` über Traefik mit **Basic-Auth** (Middleware
`kba-auth`, User `emil`). CORS erlaubt nur `http://localhost:3000`. Kickbase-Auth läuft
über `server/auth.py:TokenCache` (Token- oder Passwort-Modus, Refresh bei 401).

**Regel:** Die API **mutiert nur Intent-Zeilen** (`POST /api/intents/{id}/…`; seit 01.10.2026
schreibt der Cockpit-Build beim Aktualisieren zusätzlich Jev-Protokollzeilen) — nie
Kickbase, nie `transfer_actions`; jeder Kickbase-Write läuft weiter über den Intent-Executor
→ ActionExecutor (`server/routes/intents.py`, Modul-Docstring). Zustandswechsel sind atomare
`UPDATE … WHERE status='planned'` — seit 31.08. im gemeinsamen Service
`kickbase_agent/intents/actions.py` (`_claim_planned`), den Route UND Discord-Buttons
rufen. Keine Secrets im JSON; die internen Karten-Spalten `discord_msg_id`/
`discord_card_synced_at` erscheinen nie in einer Antwort.

**Discord-Karten (31.08., Spec `2026-08-31-discord-veto-buttons.md`):** Jedes vom
Planner NEU angelegte Vorhaben erscheint zusätzlich als Karte im Discord-Kanal
(`notify/intent_cards.py`, ein `discord.ui.DynamicItem`-Template `kba:i:<verb>:<id>`,
restart-sicher): **▶ Jetzt ausführen · +10 % · +20 % · Überspringen** — dieselben
Eingriffe wie die Zentrale, Antworten ephemer, fail-closed auf `DISCORD_USER_ID`.
Der Executor-Tick (*/5 min) zieht veraltete Karten nach (Kehrer — auch Web-Vetos);
Endzustände machen die Karte final (Buttons weg). Buttons mutieren ausschließlich
Intent-Zeilen — kein neuer Kickbase-Schreibpfad, kein neues Flag.

| Endpunkt | Methode | Parameter / Body | Antwort (Felder) | Fehler | Code |
|---|---|---|---|---|---|
| `/api/health` | GET | — | `{"status": "ok"}` | — | `routes/health.py` |
| `/api/audit` | GET | `limit` 0–1000 (30), `status?` | `{actions[], count}` aus `transfer_actions`, Statusfilter vor Limit; keine Rohantworten | 422 ungültiges Limit | `routes/operator.py` |
| `/api/tips` | GET | — | `{tips[], count, ttl_days}` aus der Worker-DB | — | `routes/operator.py` |
| `/api/me` | GET | — | `MeOut{user_id, leagues[{id,name,budget}]}` (Budget live, Fallback selection) | 502 bei Kickbase-Auth-Fehler | `routes/me.py` |
| `/api/leagues/{league_id}/squad` | GET | Pfad `league_id` | `SquadOut{league_id, players[{player{id,first_name,last_name,position 1–4,position_label GK/DEF/MID/STR,team_id,market_value,market_value_trend,points}, is_starter}]}` | 502 | `routes/squad.py` |
| `/api/leagues/{league_id}/market` | GET | Pfad | `MarketOut{league_id, players[{player, price, expires_at}]}` nach Ablauf sortiert | 502 | `routes/market.py` |
| `/api/leagues/{league_id}/intel/candidates` | GET | Pfad | `[ExplosionCandidateOut{player_id,name,team_id,position,position_label,current_market_value,total_score,signals[{name,score,weight,reason}]}]` (Kader-Kandidaten) | 502 | `routes/intel.py:get_candidates` |
| `/api/leagues/{league_id}/players/{player_id}/story` | GET | Pfad | `PlayerStoryOut{player_id,headline,narrative,confidence}` (Claude-Story) | 503 ohne `ANTHROPIC_API_KEY`, 404 Spieler unbekannt | `routes/intel.py:get_player_story` |
| `/api/news` | GET | `limit` (Default 20) | `[NewsArticleOut{source,url,title,body,published_at}]` (kicker-RSS) | — | `routes/news.py` |
| `/api/intents` | GET | `status` = planned (Default) · all · executing · executed · vetoed · expired · failed · superseded; `limit` 1–500 (50) | `{now, count, intents[]}`; Intent: id, created_at, updated_at, kind (bid/list/sell/cancel_bid/delist/lineup), player_id, player_name, team_id, position, amount, rationale, sources, listing_type, deadline, execute_at, status, emil_note, adjusted_from, executed_action_id, result + abgeleitet `seconds_left`, `deadline_seconds_left`, `amount_fmt`, `big` (≥ 5 M), `club`, `pos` (TW/ABW/MF/ST), `img` | 422 bei ungültigem `status`/`limit` | `routes/intents.py:list_intents` |
| `/api/intents/{id}` | GET | Pfad | `{intent}` | 404 | `get_intent` |
| `/api/intents` | POST | `schemas.py:IntentCreateIn` — `kind` bid · list · sell · cancel_bid · delist, `player_id`, `amount` (bid/list > 0; sell 0 = MW; cancel_bid/delist 0), `rationale` (Pflicht), `via` (nur web · mcp · claude-session · owner, sonst 403), `delay_s`, `emil_go` (Pflicht ≥ 5 M und bei sell), `offer_id` (cancel_bid) | `{intent}` als manuelles Vorhaben (`sources.manual`, nie vom Planner supersedet); bid nur mit aktuellem Engine-Plan | 400 Feld fehlt/ungültig; 403 `via`; 409 offenes Vorhaben gleicher Art/Spieler oder Kaufprüfung lehnt ab | `create_manual_intent` |
| `/api/intents/{id}/veto` | POST | Body optional `{"note": str ≤ 2000}` (`schemas.py:IntentVetoIn`) | `{intent}` mit `status: vetoed` | 404 unbekannt; 409 wenn nicht mehr `planned` | `veto_intent` |
| `/api/intents/{id}/adjust` | POST | genau **eins** von `{"amount": int > 0}` oder `{"pct": −50…100}` (`IntentAdjustIn`) | `{intent}`; `adjusted_from` hält den Erst-Betrag (nur beim ersten Anpassen gesetzt) | 422 Body ungültig; 400 Kind nicht anpassbar (nur bid/list) oder Betrag < 1; 404; 409 nicht `planned` / parallel geändert | `adjust_intent` |
| `/api/intents/{id}/execute-now` | POST | — | `{intent, note: "wird beim nächsten Executor-Tick (≤ 5 min) ausgeführt"}` (setzt nur `execute_at = jetzt`) | 404; 409 | `execute_intent_now` |
| `/api/protocol` | GET | `limit` 1–1000 (100) | `{now, count, entries[]}` — Einträge `kind: action` (transfer_actions) / `intent` (mit verknüpftem `action_status`, `rule_id`, `dry_run`) / `decision` + `lesson` (Parameter-Änderungen, Lektionen aus `knowledge/__init__.py:decision_entries`); je `ts,title,why,sources,status,emil` | — | `get_protocol` |
| `/api/reports` | GET | `kind` (≤ 32 Zeichen, exakt), `limit` 1–200 (30) | `{now, count, reports[{id,created_at,kind,title,source,chars,preview ≤ 200,delivered}]}` | — | `routes/reports.py:list_reports` |
| `/api/reports/{id}` | GET | Pfad | `{report: {…, body, meta}}` (Volltext) | 404 | `get_report` |
| `/api/learning` | GET | — | `{now, evaluation, learnings, parameters{history,current}, open_calibrations, bilanz, to_fix, selbstverbesserungen}` (= MCP `evaluation`); je Erkenntnis `id`, `art` (hypothese\|fakt\|panne), `status{status,label,tone,needs_action,successor}`, `evidence{confirm,refute,auto,agent,last_at}`, `evidence_items[≤10]`; `selbstverbesserungen[]` = Emils Tabelle (`{kind: parameter\|urteil, at, wer, quelle, titel, old, new, grund, beleg, fall, learning, commit, einzelfall_ausnahme}`, neueste zuerst, max. 30) aus `knowledge/self_improvement.py` | — | `routes/learning.py` |
| `/api/learning/evidence` | POST | `{learning_id, verdict: confirm\|refute, note, ref, observed_at?}` (`LearningEvidenceIn`) | `{evidence{learning_id,verdict,ref,note,source:"agent",created}, note}` — bekannter `ref` ⇒ `created:false`, es entsteht nichts | 400 ungültige ID/Verdikt/Notiz/Referenz; 422 Body | `post_learning_evidence` |

Fehlerformat ist FastAPI-Standard `{"detail": "…"}`; 409-Texte nennen den aktuellen Status
auf Deutsch (`_INTENT_STATUS_DE`). Die Web-App ruft `GET /api/intents?status=all`,
`/api/protocol`, `/api/reports…`, `/api/learning` und die drei POSTs per `fetch` same-origin
(`cli/cockpit/theme.py:ZENTRALE_JS`, `pages_insights.py:INSIGHTS_JS`).

## 4. CLI `kba` (Typer, `cli/__init__.py`)

Aufruf lokal `cd api && uv run kba <kommando>`, auf der Box
`docker exec kickbase-agent uv run kba <kommando>`. Konfiguration aus `api/.env`
(`config.py:Settings`). Es gibt **keinen** CLI-Trade-Befehl.

| Kommando | Optionen | Zweck | Code |
|---|---|---|---|
| `kba login` | — | Authentifizieren (Token- oder E-Mail/Passwort-Modus), User-ID und Ligen mit Budget ausgeben | `cli/login.py` |
| `kba kader` | `--league-id/-l` (Pflicht) | Kader-Tabelle (Pos, Fitness 🚑, KB-Elf-Stufe, Name, MW); nutzt E-Mail/Passwort-Login | `cli/kader.py` |
| `kba markt` | `--league-id/-l` (Pflicht) | Transfermarkt (Name, Pos, MW, Preis, Ablauf); E-Mail/Passwort-Login | `cli/markt.py` |
| `kba news` | `--limit/-n` (10) | kicker-Bundesliga-RSS ausgeben | `cli/news.py` |
| `kba probe <what>` | `what` ∈ login·leagues·me·squad·lineup·market·player·mv; `--league/-l`, `--player/-p`, `--timeframe/-t` (92) | Rohe Kickbase-Antwort mit redigierten Secrets — **einzige erlaubte Quelle für Test-Fixtures** | `cli/probe.py` |
| `kba snapshot` | `--league/-l`, `--timeframe/-t` (92) | Tages-Snapshot Kader + Markt in die DB (Spieler, MW-Historie aus Kickbases Verlauf inkl. Berichtigung, Spieltags-Punkte) | `cli/snapshot.py` → `jobs/snapshot.py:run_snapshot` |
| `kba history-reload` | `--league/-l`, `--pause` (0,35 s), `--check` | Voll-Nachladen aller bekannten Spieler (MW-Verlauf + Spieltage), berichtigt Zeilen, die von Kickbases Verlauf abweichen; `--check` zählt nur (fehlend/abweichend), schreibt nichts; verweigert 21:25–22:25 | `cli/snapshot.py:history_reload_command` → `jobs/snapshot.py:run_full_history_reload` |
| `kba plan` | `--league/-l` | Kauf-/Verkaufsvorschläge der Engine berechnen (Dry-Run, führt nichts aus) | `cli/plan.py:build_plan` |
| `kba research` | — | KI-Morgen-Recherche über die lokale `claude`-CLI (Abo) → Discord; Bericht `kind=recherche`, `source=cli:research` | `cli/research.py:_run` |
| `kba research-data` | — | Recherche-Rohdaten nach stdout (Budget/Phase/Kaufkraft/Slots/Ampel, KADER, MARKT, PROFITEURE, NEWS, COMMUNITY, AKTIVE TIPPS) | `cli/research.py:_dump_data` (auch MCP `research_data`) |
| `kba post-discord <datei>` | `--kind` (recherche) | Dateiinhalt per Discord-REST posten (≤ 1990 Zeichen gekürzt) + Bericht ablegen (`source=cli:post-discord`) | `cli/research.py:post_discord_command` |
| `kba report-add <body>` | `--kind/-k` (info), `--source/-s` (cli:report-add), `--title/-t`, `--meta/-m` (JSON-Objekt) | Bericht in `reports` ablegen — Spiegel-Befehl auf der Box für MCP `discord_post` | `cli/reports.py:report_add_command` |
| `kba reports` | `--kind/-k`, `--limit/-n` (20, 1–200) | Letzte Berichte anzeigen | `cli/reports.py:reports_command` |
| `kba tip <name>` | `--weight/-w` (1.0; +1 Kauf … −1 meiden), `--source/-s` (ki-recherche), `--note/-n` | Recherche-Tipp speichern → boostet Kauf-Scores 4 Tage | `cli/tips.py:tip_command` |
| `kba tips` | — | Aktive Tipps (letzte 4 Tage) | `cli/tips.py:tips_command` |
| `kba learn-migrate` | `--path/-p`, `--write` | LEARNINGS.md einmalig mit stabilen IDs (`[L-001]`) und `**Art:**` versehen; ohne `--write` nur Vorschau, idempotent, ändert keine Inhalte | `cli/learn.py:learn_migrate_command` |
| `kba learn-evidence <L-ID> <confirm\|refute>` | `--note/-n` (Pflicht), `--ref/-r` (Pflicht), `--at` (ISO) | Beleg zu einer Erkenntnis eintragen (`source=agent`); mit `KBA_REMOTE_API_URL` an die Box-API | `cli/learn.py:learn_evidence_command` |
| `kba dashboard-html [out]` | `out` (Default `/tmp/kba-dashboard.html`) | Cockpit-Seite aus Live-Daten bauen (~1–2 min), danach als Artifact veröffentlichen | `cli/dashboard.py:dashboard_html_command` |

Weitere Console-Scripts (`api/pyproject.toml [project.scripts]`):

| Script | Zweck | Optionen |
|---|---|---|
| `kba-worker` | Discord-Bot + APScheduler (Europe/Berlin): Snapshot 22:05, Briefing 22:15, Puls 09/13/18, Prämie 08:03/13:03/18:03, Anpfiff-Planer 07:03, Closing-Bell 21:33, News-Update 10:33/19:07, Lineup-Leak Fr 19:30, Auto-Aufstellung Fr 19:45 + Sa/So 10:00, Gebots-Wächter :47, Reconcile :52, Schatten-Scan 22:20, Auto-Trading 22:30, Intent-Planner :25, Intent-Executor */5, Lern-Belege 07:20, Cockpit-Build :22 (nur mit `COCKPIT_HTML_PATH`), KI-Recherche 08:30 (nur `AI_RESEARCH_ON_SERVER`) + dynamische Spieltags-Wächter | `--scan-now`, `--briefing-now`, `--pulse-now`, `--research-now`, `--lineup-now`, `--news-now` (`worker/__main__.py:main`) |
| `kba-serve` | FastAPI/Uvicorn (Abschnitt 3) | Env `KBA_API_HOST`, `KBA_API_PORT`, `KBA_API_RELOAD` |
| `kba-mcp` | MCP-Server (Abschnitt 2) | stdio, keine Optionen |

## 5. Skill-Routinen `/kickbase …` (`.claude/skills/kickbase/SKILL.md`)

Die Routinen orchestrieren MCP-Tools (primär) mit CLI/ssh als Fallback.

| Routine | Zweck | Tools / Befehle | Ergebnis |
|---|---|---|---|
| `/kickbase recherche` | tägliche zweiteilige Morgen-Recherche | `research_data()` + `own_bids()` + `league_state()`; Analyse gegen `docs/STRATEGIE-SAISON-26-27.md`; `discord_post(text)` je Teil; max. 3 neue Tipps via `tip_add` (doppelt). Fallback `kba research-data`, `kba tip`, `kba post-discord` | Teil 1 (Lage & Gebote mit Restlaufzeiten) + Teil 2 (Risiken, Wochenplan, Trade des Tages) in Discord + `reports` |
| `/kickbase trade` | Gebot platzieren („mach den Trade“ = bieten) | `market()`/`player(id)` → Gebot = MW × 1,05 (Conviction × 1,09) + 13 337, bei Manager-Listings am `listing_price` ankern → `place_bid(…, confirm=True, emil_go=…)`, 2 s Abstand; danach `own_bids()` + `league_state()`. Nie `cancel_bid` zum Preisändern | Audit-Zeile `mcp-bid`; Verkäufe analog (`sell_player` nur Verletzte, sonst `list_player` ~103 %) |
| `/kickbase zuschlag-check` | Zuschlag/Verfall nach Listing-Ablauf erkennen | `own_bids()` vs. `squad()` + `feed(types=[15])` | gewonnen (Gebot weg + Spieler im Kader) / verfallen (→ Re-Bid mit neuem Preis); ab 2 Zuschlägen Verkaufs-Tranche |
| `/kickbase box-check` | Server verifizieren | `box_status()`; bei neuem Code `deploy_verify(markers=[…])`; Fallback ssh/docker-Einzeiler | Erwartung: läuft · Flags DRY_RUN=0 + AUTO_BONUS/LINEUP/PURGE=1 · 0 Fehler · „Added job“ = 22 feste Jobs (`worker/__main__.py`, mit Cockpit-Build) + dynamische Wächter — SKILL.md nennt 20, der `box_status`-Docstring 13 (beide älter) |
| `/kickbase spieltag` | Spieltags-Betreuung Fr–So | `recommend_lineup()` (Elf + LI/KB-Abgleich, Quarantäne); Wächter T-120/T-60/T-35/T-15 prüfen (`worker/kickoff_guard.py`) | Punkte vs. LI-Prognose, KPI 11/11 Starter, Fazit in Discord |
| `/kickbase cockpit` | Dashboard aktualisieren | `cockpit_build()` (Box, Stundenbuild :22 läuft ohnehin); Artifact-Backup `kba dashboard-html` + Artifact-Tool mit fester URL | https://localhost:8080 aktuell |
| `/kickbase retro` | Wochen-Rückblick (So/Mo) | `audit_log()` + `feed()` + `evaluation()`: Trades, Gewinn, Tipp-Hit-Rate, Ampel, KPI-Check | Discord-Bericht (`kind=retro`) + LEARNINGS.md; im SP1-Retro Hermes-Go/No-Go |

**Morgen-Routine** (Claude-Code Scheduled Task `kickbase-morgenroutine`, 08:27,
`~/.claude/scheduled-tasks/kickbase-morgenroutine/SKILL.md`): Skill `kickbase` laden →
`/kickbase recherche` vollständig → `/kickbase cockpit` → Selbstverbesserungs-Protokoll
(ein Kandidat) → **keine Trades/Writes an Kickbase** → Ein-Zeilen-Zusammenfassung.

## 6. Web-App „Cockpit“ (https://localhost:8080)

Gebaut als eine statische `index.html` aus `cli/cockpit/` (`cli/dashboard.py:build_page`,
Daten-Contract `cli/cockpit/data.py:collect`); nginx-Container `kickbase-cockpit-web`
liefert sie mit `Cache-Control: no-store` hinter Traefik/Basic-Auth. Tabs sind per
`#hash` verlinkbar (`cli/dashboard.py:NAV`).

**Fünf Tabs** (Emil 26.08. — vorher acht; jeder Tab beantwortet EINE Frage).
Subtabs sind als `#tab/subtab` verlinkbar (`theme.SUBTAB_JS`).

| Tab (`#hash`) | Frage | Subtabs | Renderer |
|---|---|---|---|
| **Lage** (`#zentrale`, Startseite) | Wie steht's, was passiert als Nächstes? | KPI-Leiste + Zone *Heute* (Cron-Slots ✓/●/○ + Spieltags-Wächter) immer oben; *Vorhaben* (Intent-Karten mit Countdown, Begründung, Quellen-Chips, Buttons + Panel „Offene Gebote"), *Protokoll* (Zeitleiste Kickbase / KI-Arbeit mit Feinfiltern) | `pages_zentrale.py:render_zentrale` |
| **Markt** (`#markt`) | Wen kaufen, wen verkaufen? | *Transfermarkt* (Engine-Boxen, Fair-Value-Radar, komplette Marktliste), *Radar* (Scout-Karten je bietbarem Kandidaten), *Alle Spieler* (durchsuchbare Saison-Tabelle der Liga-Wolke) | `pages_core.py:render_markt`, `pages_scout.py:render_radar`, `pages_stats.py:render_stats` |
| **Team** (`#kader`) | Wer spielt, wie steht mein Kader? | *Elf* (Fußballfeld + Kader-Tabelle mit Elf-/Kauf-Score), *Analyse* (Positions-Perzentile gegen die Liga-Wolke + Teamwert-Verlauf, Marktwert, Positionen, Trends), *Berichte* (Scout-Karten des eigenen Kaders) | `pages_core.py:render_kader`, `pages_scout.py:render_spielerberichte` |
| **Liga** (`#liga`) | Was machen die anderen? | Markt-Insights oben; *Tabelle* (mit Vorsaison-Chips), *Manager* (Karten inkl. geschätztem Konto), *Handel* (Deal-Analyse + Trader-Ranking + alle Transfers) | `pages_core.py:render_liga` |
| **Wissen** (`#wissen`) | Was weiß der Agent, warum handelt er so? | *Berichte* (Volltext im Overlay via `GET /api/reports/{id}`), *News* (kicker-RSS + Community), *Lernen* (Treffer-Bilanz, Erkenntnisse, Parameter-Historie, offene Kalibrierungen), *Regeln* (Score-Formel, Signale, LI-Faktor, Score → Tat, Regel-Karten, Parameter read-only), *Ablauf* (2 Diagramme + Klartext zu Tagesrhythmus und Autonomie-Stufen) | `pages_info.py:render_wissen`, `pages_insights.py`, `flowcharts.py:render_ablauf` |

**Alte Links.** `theme.HASH_ALIASES` schreibt entfallene Hashes beim Laden um:
`#scout` → `#markt/radar`, `#stats` → `#markt/alle`, `#news` → `#wissen/news`,
`#system` → `#wissen/regeln`, `#berichte` → `#wissen/berichte`,
`#lernen` → `#wissen/lernen`, `#log` → `#zentrale/protokoll`,
`#uebersicht` → `#zentrale`.

**Interaktionen.** Vorhaben-Buttons nur bei `planned` (`theme.py:ZENTRALE_JS`, Funktion
`actions`): *Jetzt ausführen* → `POST …/execute-now`; *+10 %* / *+20 %* → `POST …/adjust {pct}`;
*Betrag…* → `POST …/adjust {amount}`; *Überspringen* → `POST …/veto {note?}` — bei `sell`
nur *Jetzt ausführen* und *Überspringen*. Die Karte wird aus der Antwort neu gerendert.
Klick auf einen Spieler öffnet das Detail-Overlay (`#ppop`) mit Member-Stats und der
**KI-Einschätzung** (`cli/cockpit/ki.py:ki_payload`: Signale, Kauf-/Elf-Score, Tipp, LI-Status,
Core/Trading, Empfehlung, Flip, Historie).

**Aktualisierung.** Der Worker baut **stündlich um :22** (Job „Cockpit-Build“,
`worker/cockpit_job.py:run_cockpit_build`, nur mit `COCKPIT_HTML_PATH`); das JS lädt
danach Vorhaben/Protokoll (`/api/intents?status=all`, `/api/protocol`, Polling alle 60 s
bei sichtbarem Tab), Berichte (`/api/reports`) und Lernen (`/api/learning`) nach — ohne API
bleibt die statische Seite vollständig (Artifact-Backup der Morgen-Routine). Manuell:
MCP `cockpit_build()`.

## 7. Verweise

- MCP-Spec: `docs/superpowers/specs/2026-08-23-kickbase-mcp.md` · Steuerzentrale (API/Intents/Web-App):
  `docs/superpowers/specs/2026-08-23-steuerzentrale.md` · Cockpit v4: `docs/superpowers/specs/2026-08-23-cockpit-v4-kickbase-look.md`
- Betrieb (Box, Flags, Deploy, Störungen): `docs/BETRIEB.md` · Architektur: `docs/ARCHITEKTUR.md`
- Routinen + eiserne Regeln: `.claude/skills/kickbase/SKILL.md` · Lektionen/API-Funde: `.claude/skills/kickbase/LEARNINGS.md`
- Strategie (Tier-1, Stufen, KPIs): `docs/STRATEGIE-SAISON-26-27.md` · Hermes: `docs/archive/UEBERGABE-HERMES.md`
- Begriffe und Kickbase-Rohfelder: [`GLOSSAR.md`](GLOSSAR.md)

## Strategieansicht seit 11.09.2026

`GET /api/strategy` liefert denselben datierten Snapshot wie die Hauptansicht:
Finanzierung, bestätigte Transfers, abgeschlossene Managerpunkte, Kurs-/Minuten- und
Punkteverläufe, Nachrichten, nächste wirksame Kaderfrist und Datenlücken. Kein neuer
Kickbase-Aufruf beim Lesen. Fehlt die Datei oder ist sie ungültig, HTTP 503 statt leerer
Erfolgsantwort. Die vorhandene Zugangsabsicherung bleibt unverändert.

MCP `strategy_insights(player_id="")` liest kompakte Übersichten; mit Spieler-ID zusätzlich
seine Kurven, bestätigten Transfers und Nachrichten. Bei gesetzter Remote-API wird
nur die Box gelesen. Ein Remote-Fehler fällt niemals still auf lokale Daten zurück.
`generated_at`, `source_refresh` und `coverage` immer mit auswerten.
