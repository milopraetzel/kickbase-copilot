# Glossar des Kickbase-Agenten

Stand 23.08.2026 · Begriffe, Kennzahlen und Kickbase-Rohfelder — belegt mit `Datei:Funktion`
(relativ zu `api/src/kickbase_agent/`) oder Dokument. Schnittstellen: [`API.md`](API.md).
Gruppen: (a) Kickbase-Spielbegriffe · (b) Engine · (c) Betrieb · (d) API-Feld-Tabelle.

## (a) Kickbase-Spielbegriffe

| Begriff | Bedeutung | Beleg |
|---|---|---|
| **Marktwert (MW), 22-Uhr-Update** | Von Kickbase täglich um 22:00 Berlin neu gesetzter Wert je Spieler; Teamwert = Summe der Kader-MW. Beim Kauf steigt der Teamwert nur um den MW, nie um den Kaufpreis (Overpay verpufft). Snapshot-Job 22:05 schreibt den Tageswert aus Kickbases Verlauf (oder aus dem Live-Wert, wenn der sich gegenüber gestern schon bewegt hat); kam das Update später, fehlt der Tag bis zum nächsten Lauf (Kader-/Marktspieler am nächsten Abend, sonst Nachladen Di 04:10). | `trading/finance.py` (Modul-Docstring), `intel/evaluation.py:MV_UPDATE_HOUR`, `worker/__main__.py` |
| **Kickbase-Listing** | Listing des Computers: kein Verkäufer (`u` fehlt), `exs`-Countdown → **Auto-Zuschlag** ans höchste Gebot beim Ablauf. `listing_type: "kickbase"`. | LEARNINGS 21.08.; `mcp/tools_read.py:market` |
| **Manager-Listing** | Listing eines Mitspielers (`u.n` = Verkäufer, kein Countdown): der Besitzer **nimmt manuell an**, Gebote hängen beliebig lange und binden Budget; Anker für Gebote ist `prc` (Listing-/Mindestpreis), nicht der MW. | LEARNINGS 21.08.; `kickbase/client.py:get_own_bids` (`seller`, `listing_price`) |
| **Gebot / Überbieten** | Gebot = Angebot auf ein Listing (`POST market/{pid}/offers`). Tiebreak: bei Gleichstand gewinnt das frühere Gebot; krumme Beträge schlagen glatte (Psycho-Offset 13 337). Kickbase prüft beim Bieten nur die 33-%-Regel über `pbaa`, nicht das Bargeld. | LEARNINGS Meta-Wissen + 23.08.; `decision/guardrails.py` |
| **Underpay** | Gebot unter MW: offiziell möglich, Zuschlag nachfrageabhängig, Empfehlung ≥ 90 % MW; MW zum Transferzeitpunkt zählt (22-Uhr-Anstieg kann ein Gebot nachträglich „unter MW“ machen). **Empirie BASE: 81 % MW verlor** (Friedrich, 22.08.). | LEARNINGS 21./22.08. |
| **Sofortverkauf an Kickbase** | Verkauf zum aktuellen MW, jederzeit in Sekunden (`POST market/{pid}/sell`); Regel: nur Langzeit-Verletzte (vor 22:00 zum alten Preis), sonst listen (~103 % Computer-Angebot). | `kickbase/endpoints.py:player_sell`, `mcp/tools_write.py:sell_player`, LEARNINGS |
| **Storno (cancel_bid)** | Eigenes Gebot zurückziehen (`DELETE …/offers/{oid}`). **Verbrennt den Offer-Slot**: danach wirft jedes neue Gebot auf dasselbe Manager-Listing 500 — nie stornieren, um den Preis zu ändern. Ausnahme: T-120-Wächter (`kickoff-bid-cancel`). | LEARNINGS 21.08.; `worker/kickoff_guard.py:run_bid_cancel_guard` |
| **Auflaufprämie (Login-Bonus)** | Tägliche Prämie; Feed-Ereignis **t=22** (`data.bn` Betrag, `data.day` Streak-Tag; Tagesgrenze Mitternacht Berlin). `/v4/bonus/collect` antwortet immer 200 + `it: []` — Wirkung nur über Konto-Delta messbar (Status `noop` bei 0 €). Slots 08:03/13:03/18:03. | LEARNINGS 23.08.; `agent/executor.py` (noop); `worker/__main__.py` |
| **Transfer (Feed t=15)** | Abgeschlossener Kauf/Verkauf im Liga-Feed: `byr` Käufer, `slr` Verkäufer, `trp` Preis, `pn` Spielername (+ `pi`, `pim`). t=3 = Markt-Event. Grundlage der Trade-Zählung. | `trading/trade_budget.py:count_my_trades`, `mcp/tools_read.py:_feed_summary` |
| **Member-Abo** | Emils Kickbase-Member-Abo liefert Zusatzfelder: `prob` Startelf-Stufe, `st`/`stxt` Fitness, echte Porträts (`pim`), Partner `plpt: "Ligainsider"`. Skala bis SP1 unvalidiert → nur Anzeige/Zweitmeinung. | LEARNINGS 23.08.; `kickbase/models.py:Player` |
| **Startelf-Stufe (prob)** | Offizielle Member-Legende: 1 **Sicher** ✦ (nahezu sicherer Starter) · 2 **Erwartet** ✓ · 3 **Unsicher** ? · 4 **Unwahrscheinlich** ! · 5 **Ausgeschlossen** ✕ · ohne Angabe Strich. MCP `lineup_label`, Cockpit-Icons. | `cli/cockpit/theme.py:LINEUP_LEGEND`, `mcp/_util.py:LINEUP_LABELS` |
| **Fitness (st/stxt)** | `st` 0 = fit, > 0 angeschlagen/verletzt (live: 2 bei Achilles-Problemen), `stxt` Text (englisch), `dt` Status-Datum. **Kein** Starter-Flag (Altlast-Bug). | `kickbase/models.py:SquadPlayer`, LEARNINGS 23.08. |
| **Tier-1** | Top-Spieler ≥ 20 M MW; jedes Gebot braucht Emils GO (auch ≥ 5 M generell) — in jeder Autonomie-Stufe. | `docs/STRATEGIE-SAISON-26-27.md`, `mcp/tools_write.py:EMIL_GO_THRESHOLD_EUR` |
| **Startelf / S11** | Die 11 aufgestellten Spieler; API-Marker `lo` (Lineup-Order 0–10) im Squad; `smc` = S11-Einsätze im Detail. Engine wählt die beste aus 9 Formationen (3-4-3 … 5-4-1, je + TW). | `decision/lineup.py:_FORMATIONS`, LEARNINGS API-Funde |
| **Ø Punkte (ap) / Punkte (tp)** | Kickbase-Spieltagspunkte: `tp` gesamt, `ap` Schnitt, `p` letzte Punkte; Spieltagspunkte liegen grob bei 0–300. | `kickbase/models.py:PlayerDetail`, `intel/signals.py` |
| **33-%-Regel** | Konto darf bis ⅓ des bereinigten Teamwerts (Teamwert + negatives Konto) ins Minus: `overdraft_floor`; Höchstgebot = Konto − floor (`max_single_bid`); Kickbase rechnet mit `pbaa` (Konto nach Annahme aller offenen Gebote). | `trading/finance.py`, `agent/executor.py:_preflight_veto` |
| **Kaufkraft** | Spielraum für EIN Gebot: `kaufkraft` = max_single_bid(Konto, Teamwert); `kaufkraft_after_bids` = pbaa − floor („Konto − offene Gebote + ⅓ Teamwert“). | `mcp/tools_read.py:league_state`, LEARNINGS 23.08. |
| **Liga BASE** | Emils Liga (ID 1234567, 12 Manager, User-ID 2663242, Liga-Name „emil“); Club-Limit `lpc = 11` (praktisch frei), Kaderlimit-Annahme 18 (`kickbase_squad_limit`). | `docs/UEBERGABE-HERMES.md`, `kickbase/client.py:get_league_rules`, `config.py` |
| **200-Trades-Regel / Trade-Ampel** | Interne (mündliche) Liga-Regel: max. 200 Transfers je Halbserie. Zählung über Feed t=15 (byr/slr = eigener Name). Ampel 🟢 · 🟠 ab 85 % · 🔴 ab 195 (Limit − Notreserve 5 → Engine schlägt nichts mehr vor); `saturated` = Feed voll (≥ 1000 Items) → Zählung nur Untergrenze, Sperre aktiv. | `trading/trade_budget.py` |
| **Spieltag / Deadline / Anpfiff-Regel** | **Eine** Deadline pro Spieltag = offizieller Anpfiff des ersten Spiels: Aufstellung eingefroren, Konto geprüft — **Konto < 0 beim Anpfiff = 0 Punkte für den ganzen Spieltag**. `matchday_deadline` (Fixtures je Spieltag), `next_kickoff` nur Anzeige. | `worker/kickoff_guard.py`, LEARNINGS „Offizielle Spielregeln“ |
| **Positionen** | Kickbase `pos` 1–4: Torwart, Abwehr, Mittelfeld, Sturm. Kürzel je Schicht: MCP/Cockpit `TW/ABW/MIT/STU`, Intents-API `TW/ABW/MF/ST`, Squad-/Market-API `GK/DEF/MID/STR`. | `mcp/_util.py:_POS_NAMES`, `server/routes/intents.py:_POS`, `server/routes/squad.py:POSITION_LABELS` |
| **Angebot annehmen / ablehnen** | Auf eigene Listings kommen Angebote (Computer ~103 %, Manager); Engine-Regel `accept_good_offer` ab `accept_offer_floor` 1.03; Executor-Typen `accept`/`decline` (`…/offers/{oid}/accept` bzw. `…/decline`). | `decision/policies.py`, `kickbase/endpoints.py:market_offer_accept` |
| **Listing / Delist** | Eigenen Spieler zum Preis auf den Markt stellen (Aktionstyp `list`, Client `list_player`); Rücknahme `delist` (`DELETE market/{pid}`). Eigene Listings ≥ 95 % MW (`listing_floor`), Ziel ~103 % für Computer-Angebote. | `agent/executor.py:WriteClient`, `kickbase/endpoints.py:market_delist` |
| **Vereinslimit (lpc)** | Max. Spieler je Bundesliga-Club (Liga-Einstellung 1–11; BASE 11). Engine zählt Kader **und** offene Gebote je Club (`club_counts`). | `kickbase/client.py:get_league_rules`, `cli/plan.py:build_plan` |
| **Kaderlimit** | Maximale Kadergröße (Liga-Admin 11–25); im Code Annahme 18 (`kickbase_squad_limit`, echter API-Wert ungeprüft). Trading-Slots = Limit − Kadergröße. | `config.py`, `trading/zones.py:free_trading_slots` |
| **Halbserie / SP1** | Hinrunde = „70 % Börse“ (Strategie); SP1 = 1. Spieltag 28.08.2026 (ab dann Validierung von `prob`, Elf-Score, Punkte-Backtests). | `docs/STRATEGIE-SAISON-26-27.md`, `knowledge/parameters.py:OPEN_CALIBRATIONS` |
| **Mitspieler-Daten** | Kader eines Managers `/managers/{id}/squad`, Dashboard `/managers/{id}/dashboard` (tv, prft); Liga-Konten ±5 M aus dem paginierten Feed rekonstruierbar (nicht im Client modelliert). | LEARNINGS 23.08. |

## (b) Engine-Begriffe

| Begriff | Bedeutung | Beleg |
|---|---|---|
| **Signal** | Teilbewertung 0..1 (0.5 neutral) mit Gewicht und Begründung: `base_points` 0.40 (25-%-Perzentil der Spieltagspunkte, sättigt bei 100 Pkt), `form` 0.25 (Ø letzte 3 gespielte vs. Saisonschnitt, ±120 Pkt sättigt), `matchup` 0.20 (Siegwahrscheinlichkeit aus Quoten, zentriert auf ⅓), `trend` 0.15 (MW-Steigung ≤ 7 Tage, ±5 % sättigt). Unter 3 gespielten Spielen Shrinkage Richtung 0.5. | `intel/signals.py` |
| **Kauf-Score** (`total_score`) | Gewichtetes Mittel aller Signale (`RuleEngine.score`) + LigaInsider-Multiplikator + Community-Boost (Markt symmetrisch ±0.08; Kader seit 31.08. nur POSITIV als Verkaufs-Schutz) — steuert Kauf/Verkauf. Schwellen: `buy_score_threshold` 0.53 (31.08., Momentum), `sell_score_threshold` 0.35. | `intel/engine.py`, `intel/collector.py`, `cli/plan.py:build_plan` |
| **Elf-Score** (`lineup_score`) | Wie Kauf-Score, aber **ohne** `trend` (MW-Momentum erzeugt keine Punkte; Restgewichte renormiert) — steuert die Aufstellung. | `intel/collector.py:collect_from_players` |
| **Community-Tipp / Boost** | Tipp (`community_tips`: Name, Gewicht −1…+1, Quelle, Notiz) verschiebt den Kauf-Score um max. ±0.08·Gewicht (`MAX_BOOST`), verfällt nach 4 Tagen (`TIP_TTL_DAYS`), fuzzy Namens-Match ≥ 0.90, als Signal `community` sichtbar. | `intel/community.py` |
| **Conviction-Ziel** | Kandidat mit Community-Signal ≥ 0.85 oder positionsgleicher Profiteur → höherer Aufschlag (`bid_overpay_conviction` 0.09). | `cli/plan.py:build_plan` |
| **LigaInsider-Status** | `starter` ×1.0 · `bench` ×0.4 · `out` ×0.0 (Hard-Veto) · `unknown` ×1.0 (kein Veto). Primäre Startelf-Wahrheit; Member-`prob` ist Zweitmeinung. | `scouts/ligainsider/models.py:Status, MULTIPLIER` |
| **Quarantäne** | LI-Ausfall-Match mit Confidence in [0.72, 0.90): wird **nicht** genullt, sondern gemeldet (`quarantines` in `recommend_lineup`). Ab 0.90 scharf. | `scouts/ligainsider/availability.py:CONFIDENT, QUARANTINE_FLOOR` |
| **Profiteur** | Markt-Spieler in der voraussichtlichen Startelf, dessen Teamkollege ausfällt; `same_position` = direkter Job-Erbe (Kauf vor MW-Anstieg). | `scouts/ligainsider/profiteur.py`, `cli/plan.py` |
| **Core vs. Trading-Zone** | Trading = als Trading-Kauf erworben (`transfer_actions.rule_id = trading_buy`, executed); alles andere Core (Punkte-Kader). Trading-Slots = Kaderlimit − Kadergröße. | `trading/zones.py`, `trading/orchestrator.py:load_trading_holdings` |
| **Finanz-Phase** | `invest` Mo–Do (Konto darf ins Minus) · `purge` Fr (Trading-Material verkaufen bis Konto ≥ Puffer, `plan_purge`) · `hold` Sa/So. | `trading/finance_cycle.py:finance_phase` |
| **Guardrails** (`decision/guardrails.py:Guardrails`, Defaults 26.08.) | `max_bid_pct_of_budget` 0.60 (ein Gebot ≤ 60 % Budget) · `bid_overpay_fraction` 0.08 (Ziel-Aufschlag über MW) · `bid_psych_offset` 13 337 (krummer Zuschlag) · `bid_overpay_conviction` 0.09 · `bid_overpay_need` 0.20 (Ziel bei Kader-Bedarf) · `bid_ladder_opening` 0.02 (Erstgebot) · `bid_ladder_raise_hours` 2.0 (Erinnerung vor Ablauf) · `league_trade_limit_per_half` 200 · `min_balance` 0 · `max_bid_premium` 1.10 (Standard-Gebot ≤ 110 % MW, hart) · `max_bid_premium_need` 1.35 (markierter Kader-Bedarf ≤ 135 % MW, hart) · `buy_score_threshold` 0.53 (31.08., Momentum) · `sell_score_threshold` 0.35 · `listing_floor` 0.95 (eigene Listings ≥ 95 % MW) · `accept_offer_floor` 1.03 (Angebote annehmen ≥ 103 %) · `daily_buy_cap` 5 · `daily_sell_cap` 5 · `approval_threshold` 5 000 000 · `protected_player_ids` **Verfassung**, Code-Default 8329 (Olise), nur über `PROTECTED_PLAYER_IDS` (.env) änderbar — Inhalt entscheidet Emil, der Agent schreibt die Liste nie selbst um; seit 16.09.2026 auf Emils Anweisung **leer** (Olise zu 100 M angeboten), Ist-Zustand im Container prüfen. | `decision/guardrails.py`, Historie `knowledge/parameter_history.json` |
| **Kader-Bedarf** (`PlannedAction.squad_need`, 26.08.) | Markierung „ohne diesen Spieler steht die Formation nicht". Gesetzt vom Planner bei Positions-Not (`positions_in_need`) oder manuell per `place_bid(..., squad_need=True)`. Einzige Wirkung: Overpay-Kappe 135 % statt 110 % MW und Eskalations-Ziel 120 % statt 108 %. Reist über `sources.squad_need` durch die Vorhaben-Zeile bis in den Executor-Preflight. | `decision/policies.py`, `worker/intents.py`, `agent/executor.py` |
| **Verfassung** (26.08.) | Regeln, die der Agent **nicht** ändern darf — im Gegensatz zu den Stellschrauben mit Decision-Record. Heute: `protected_player_ids`. Sichtbar in `current_parameters()` (als Text), aber nicht in `GUARDRAIL_KEYS`. | `knowledge/parameters.py: CONSTITUTION_KEYS` |
| **Weitere Parameter** | `FEED_MAX_ITEMS` 1000 · Signal-Gewichte 0.40/0.25/0.20/0.15 · `MAX_BOOST` 0.08 · `TIP_TTL_DAYS` 4 · `PROPOSAL_TTL` 45 min · `PREFLIGHT_MAX_PRICE_DRIFT` 0.10 · `PREFLIGHT_MAX_VALUE_DRIFT` 0.20 · `agent_trading_max_per_player` 1 500 000 · `PURGE_MIN_BALANCE` 10 000 · `EMERGENCY_RESERVE` 5 · `WARN_FRACTION` 0.85. | `knowledge/parameters.py:current_parameters`, `worker/kickoff_guard.py`, `trading/trade_budget.py` |
| **Ladder (Gebots-Staffelung)** | Pay-as-bid: Erstgebot = MW × 1.02 + 13 337; Ziel = MW × 1.08 (Conviction 1.09) + 13 337; Deckel MW × 1.10. Gebots-Wächter (:47) erinnert bei < 2 h Restzeit an die Erhöhung — führt sie nie selbst aus. | `decision/policies.py:propose_buys`, `worker/bid_watch.py` |
| **Psycho-Offset 13 337** | Krummer Aufschlag auf jedes Gebot, schlägt glatte Rivalen-Gebote. | `guardrails.py:bid_psych_offset` |
| **Preflight** | Frische Prüfung unmittelbar vor Live-Geld-Writes, fail-closed: bid → Spieler noch am Markt? Preis ≤ +10 % über Staging? 33-%-Regel mit `pbaa`; sell/list → Spieler im Kader? MW-Drift ≤ ±20 % (Kickoff-Notverkäufe `kickoff-*` ausgenommen). Verstoß → `refused`. | `agent/executor.py:default_preflight`, `_preflight_veto` |
| **Vorhaben (Intent)** | Von der Engine beschlossene Aktion (`kind` bid/list/sell/lineup) mit `execute_at` und Veto-Fenster; Planner :25 legt an, Executor */5 führt aus (rule_id `intent-<kind>`, geärmt nur mit `AGENT_AUTO_INTENTS=1`). Regeln: Kickbase-Listing → execute_at = Ablauf − 15 min; Manager-Listing/list/sell → +2 h; Sniper < 30 min → sofort; ≥ 5 M Mindestfenster 2 h. Höchstens ein `planned` je (kind, player). | `worker/intents.py` (Modul-Docstring), Spec Steuerzentrale |
| **Intent-Status** | `planned` geplant → `executing` (Sekunden, Claim) → `executed` ausgeführt / `failed` fehlgeschlagen; `vetoed` übersprungen (Emil) · `expired` abgelaufen · `superseded` ersetzt (nicht mehr im Plan). `result` trägt den Executor-Grund. | `worker/intents.py:STATUS_*`, `server/routes/intents.py:_INTENT_STATUS_DE` |
| **Emil-Eingriff** | Veto (mit Notiz), Betrag anpassen (absolut oder ±%; `adjusted_from` hält den Erst-Betrag), „Jetzt ausführen“ (`execute_at = jetzt`). Nur bei `planned`. | `server/routes/intents.py` |
| **Decision-Record** | Pflicht-Eintrag je Parameter-Änderung in `parameter_history.json`: `date`, `parameter`, `old`, `new`, `reason`, `commit`, `expect` (was soll besser werden), `review` (wann/wie prüfen); erscheint im Protokoll als KI-Entscheidung (`kind: decision`), Lektionen als `lesson`. | `knowledge/parameters.py`, `knowledge/__init__.py:decision_entries`, SKILL.md |
| **Treffer-Bilanz** | Kaufvorschläge (echt + Schatten, rule_id `buy_explosion`/`auto-trading-buy`/`intent-bid`/`mcp-bid`/`trading_buy`) gegen MW nach 1 und 7 Tageswerten: Treffer = MW gestiegen; `buy_hits`/`shadow`/`executed` mit n, hit_rate, avg/median delta_pct; Tipp-Bilanz (7 d); `coverage`. Dedupe je Spieler+Tag. | `intel/evaluation.py:evaluate` |
| **Flip-Bilanz** | Echte Gebote (executed): Kaufpreis = Gebot vs. letzter bekannter MW (`flips`, `flips_total_delta_eur`); ob der Zuschlag kam, vermerkt das Audit nicht. | `intel/evaluation.py:FLIPS_NOTE` |
| **Schatten-Lauf / Schatten** | Lauf ohne Write („hätte getan“): Schatten-Scan 22:20 (Proposals via `ActionExecutor.stage`), Auto-Trading 22:30 im Schatten-Modus, Dry-Run-Vormerkungen. Schatten-Status: `proposed`, `skipped_dry_run`, `refused`, `expired`, `rejected`, `failed`, `noop`; Intents `vetoed/expired/failed/superseded`. | `worker/decision_scan.py`, `worker/auto_trading.py`, `intel/evaluation.py:SHADOW_*` |
| **Audit-Status-Maschine** (`transfer_actions.status`) | `proposed` (wartet auf Freigabe, TTL 45 min) · `pending` (geclaimt, Dispatch läuft) · `executed` · `failed` · `unknown` (Timeout — kein blinder Retry, Reconcile :52) · `refused` (Interlock/Preflight) · `rejected` (Mensch) · `expired` · `skipped_dry_run` · `noop` (200 ohne Wirkung). Nur Ergebnis: `duplicate`. Blockierend: pending/proposed/executed/unknown; retryable: failed/refused/rejected/expired/skipped_dry_run/noop. Idempotenz-Key = Liga+Typ+Spieler+Betrag+Offer+Formation+Tag. | `agent/executor.py` (Docstring, `_BLOCKING`, `_RETRYABLE`, `idempotency_key`) |
| **Freigabe / Approval** | Vorschlag > `approval_threshold` 5 M (`requires_approval`) → Discord-Buttons; `PROPOSAL_TTL` 45 min; Live nur mit Interlock oder `AGENT_APPROVE_LIVE`. Seit 23.08. ersetzt das Vorhaben-Modell die Vorab-Freigabe; ≥ 5 M/Tier-1 bleibt Emils GO (`emil_go`). | `decision/policies.py`, `agent/executor.py:execute_staged`, `mcp/tools_write.py` |
| **Autonomie-Stufen** | Stufe 2 = Auto-Trading ≤ 1,5 M je Spieler (`AGENT_AUTO_TRADING`, dormant bis Kriterien + GO); Steuerzentrale = Vorhaben mit Veto (`AGENT_AUTO_INTENTS`); nicht beide gleichzeitig ärmen. | `config.py`, `worker/auto_trading.py` |
| **Kandidat (ExplosionCandidate)** | Bewerteter Spieler: `player_id`, `name`, `team_id`, `position`, `current_market_value`, `total_score` (Kauf), `lineup_score` (Elf), `signals[]`. | `intel/models.py`, `intel/collector.py` |
| **Proposal** | Vorschlag der Entscheidungs-Engine (Kauf/Verkauf/Schutz/Angebot) mit `rule_id`, `rationale`, `requires_approval` (> 5 M) — Basis für Briefing, Schatten-Scan, Intents. | `decision/models.py`, `decision/policies.py` |
| **Momentum / Riser / Wendepunkt** | Trading-Scanner: Median der Tages-MW-Änderungen (`daily_change`, `pct_per_day`), `rising`, `flattening` (Anstieg lässt nach = Verkaufssignal), `spike` (Einmal-Sprung). Riser = stärkste Steiger für freie Trading-Slots; Mindest-Momentum 0,5 %/Tag, Haltedauer 4 Tage (`TradingConfig`). | `trading/scanner.py`, `trading/pipeline.py` |
| **Kill-Switch** | `AGENT_DRY_RUN=1` (Code-Default): kein Live-Write, egal welche Flags/Parameter — Ergebnis `skipped_dry_run`. Scharfschalten macht nur Emil (Box-`.env`). | `agent/executor.py` (Schutzschicht 2), LEARNINGS 21.08. |
| **Live-Interlock** | Welcher Schalter welchen Aktionstyp ärmt (Tabelle in `_live_allowed`); außerhalb der Test-Liga ohne passenden Schalter → `refused` mit Grund. | `agent/executor.py:_live_allowed`, `_live_refusal_reason` |
| **Idempotenz** | Unique `idempotency_key` je Aktion und Tag; paralleler Insert/Claim → `duplicate`; Dry-Run-/noop-Zeilen werden für den Live-Versuch recycelt. | `agent/executor.py:idempotency_key`, `_recycle` |
| **Tages-Caps** | Max. 5 Käufe / 5 Verkäufe pro Tag (`daily_buy_cap`/`daily_sell_cap`), gezählt über heutige `executed`-Zeilen des jeweiligen Pfads (`intent-*`, `auto-trading*`). | `worker/intents.py`, `worker/auto_trading.py` |
| **Veto-Fenster / Sniper** | Zeit zwischen Anlage und `execute_at` eines Vorhabens (Default 2 h, ≥ 5 M mindestens 2 h); Sniper = Listing mit < 30 min Restzeit → sofort fällig. | `worker/intents.py:VETO_WINDOW, SNIPER_WINDOW` |
| **Offene Kalibrierungen** | Ehrliche Liste ungemessener Parameter (Signal-Gewichte, Schwellen, `prob`-Skala, Aufschläge, Caps, Boost, Elf-Score) — im Tab Lernen und in `evaluation()`. | `knowledge/parameters.py:OPEN_CALIBRATIONS` |

**rule_id-Übersicht** (Spalte `transfer_actions.rule_id`, steuert Interlock, Zählungen und Bilanzen):

| rule_id | Pfad / Bedeutung | Quelle |
|---|---|---|
| `buy_explosion`, `sell_weak`, `protect_player`, `accept_good_offer` | Vorschläge der Decision-Engine (Kauf ab Schwelle, Verkauf unter Schwelle, Schutz, Angebot annehmen) | `decision/policies.py` |
| `trading_buy` | Trading-Kauf (markiert die Trading-Zone) | `trading/orchestrator.py:TRADING_RULE` |
| `auto-trading-buy`, `auto-trading-list` | Stufe-2-Autopilot (geärmt durch `AGENT_AUTO_TRADING`) | `worker/auto_trading.py` |
| `intent-bid`, `intent-list`, `intent-sell` | Steuerzentrale (geärmt durch `AGENT_AUTO_INTENTS`) | `worker/intents.py:INTENT_PREFIX` |
| `mcp-bid`, `mcp-list`, `mcp-sell` | MCP-Write-Tools (Orchestrator-Session) | `mcp/tools_write.py` |
| `kickoff-bid-cancel`, `kickoff-purge` | T-120-Storno, T-15-Notbremse (geärmt durch `AGENT_AUTO_PURGE`; Preflight-Drift ausgenommen) | `worker/kickoff_guard.py` |
| `auto-lineup`, `auto-bonus` | Aufstellung (`AGENT_AUTO_LINEUP`), Prämie (`AGENT_AUTO_BONUS`) | `worker/lineup_job.py`, `worker/bonus_job.py` |

## (c) Betriebsbegriffe

| Begriff | Bedeutung | Beleg |
|---|---|---|
| **Box** | n8n-Box `root@203.0.113.10`, Verzeichnis `/opt/kickbase-agent`, Docker Compose; Volume `kba-data` mit SQLite `/app/data/kba.db`; Traefik-Netz `n8n_default`; TZ Europe/Berlin. | `mcp/_util.py`, `docker-compose.yml` |
| **Container** | `kickbase-agent` (Service `worker`: Scheduler, Engine, Discord-Bot; Alembic-Migration im Entrypoint) · `kickbase-api` (`kba-serve`, `/api` hinter Basic-Auth) · `kickbase-cockpit-web` (nginx, `index.html`, no-store). | `docker-compose.yml` |
| **Zwei DBs** | Mac-lokal (`api/kba_local.db`/`DATABASE_URL`) und Box-Volume — Tipps/Berichte immer doppelt schreiben (`tip_add`, `discord_post` spiegeln per ssh). | LEARNINGS „Fallen“, `mcp/tools_ops.py` |
| **Flags** (Box-`.env`, `config.py:Settings`) | `AGENT_DRY_RUN` (Kill-Switch, Default 1 = nie live) · `AGENT_ALLOW_LIVE` (ärmt nur Trade-Typen bid/cancel_bid/sell/list/delist/accept/decline) · `AGENT_APPROVE_LIVE` (Freigabe-Pfad) · `AGENT_AUTO_BONUS` · `AGENT_AUTO_LINEUP` · `AGENT_AUTO_PURGE` (sell/cancel_bid mit `kickoff-*`) · `AGENT_AUTO_TRADING` (`auto-trading*`) · `AGENT_AUTO_INTENTS` (`intent-*`) · `AGENT_TEST_LEAGUE_ID` (alles, nur dort) · `COCKPIT_HTML_PATH` (Stundenbuild) · `AI_RESEARCH_ON_SERVER`. Geärmt (23.08.): DRY_RUN=0, AUTO_BONUS/LINEUP/PURGE=1. | `agent/executor.py:_live_allowed`, SKILL.md |
| **Sperrfenster** | 21:25–22:25 Berlin: kein `compose up --build` — frisst die In-Memory-Crons der Abend-Jobs (21:33/22:05/22:15/22:20/22:30); `deploy_verify` erzwingt es. | `mcp/_util.py:DEPLOY_BLOCK_*`, LEARNINGS 21.08. |
| **Wächter T-120 / T-60 / T-35 / T-15** | Relativ zur Spieltags-Deadline: T-120 Gebots-Storno (Gebote, deren Zuschlag das Konto sprengen könnte) · T-60 Ping mit Notverkaufs-Kaskade · T-35 Aufstellung setzen · T-15 Notbremse (Kaskade S1 Trading → S2 Bank → S3 Startelf, Torwart zuletzt, bis Konto ≥ 10 000 €, nur mit AUTO_PURGE). Geplant täglich 07:03 mit 96-h-Vorlauf, Catch-up bei Neustart. | `worker/kickoff_guard.py:schedule_kickoff_guards, build_purge_cascade` |
| **Closing-Bell** | 21:33 täglich: Langzeit-Verletzte im Kader vor dem 22-Uhr-Update melden (Sofortverkauf zum alten Preis). | `worker/kickoff_guard.py:run_closing_bell` |
| **Tagesplan Worker** | Snapshot 22:05 · Briefing 22:15 · Schatten-Scan 22:20 · Auto-Trading 22:30 · Puls 09/13/18 · Prämie 08:03/13:03/18:03 · Anpfiff-Planer 07:03 · News 10:33/19:07 · Lineup-Leak Fr 19:30 · Auto-Aufstellung Fr 19:45, Sa/So 10:00 · Gebots-Wächter :47 · Reconcile :52 · Intent-Planner :25 · Intent-Executor */5 · Cockpit-Build :22. | `worker/__main__.py` |
| **Morgen-Routine** | Claude-Code Scheduled Task `kickbase-morgenroutine` 08:27: `/kickbase recherche` → `/kickbase cockpit` → Selbstverbesserung; keine Writes. | `~/.claude/scheduled-tasks/kickbase-morgenroutine/SKILL.md` |
| **Hermes** | Emils Claude-basierter Agent auf Hostinger KVM 1 (72.62.62.240); fährt Schatten-Modus, soll den Orchestrator-Job übernehmen; Go/No-Go im SP1-Retro (30./31.08.). | `docs/UEBERGABE-HERMES.md`, SKILL.md |
| **Cockpit-Build** | Stündlich :22 baut der Worker `index.html` ins Volume (`run_cockpit_build`); manuell MCP `cockpit_build`; Artifact-Backup via `kba dashboard-html`. Live: https://localhost:8080. | `worker/cockpit_job.py`, `cli/dashboard.py` |
| **Reports / Berichte** | Tabelle `reports`: alles, was nach Discord geht, als Volltext. `kind` ∈ briefing · recherche · schatten · auto_trading · planner · executor · retro · bid_watch · info · sonstiges; `source` z. B. `worker:<kind>`, `cli:research`, `cli:post-discord`, `cli:report-add`, `mcp:discord_post`; ohne expliziten kind nur Texte ≥ 120 Zeichen. | `notify/report_store.py` |
| **Protokoll (KI-Protokoll)** | `GET /api/protocol`: Aktionen + Vorhaben + Entscheidungen/Lektionen, neueste zuerst; Zone 3 der Zentrale. | `server/routes/intents.py:get_protocol` |
| **Puls / Briefing / Schatten-Scan** | Puls = Intra-Day-Recherche 09/13/18 (nur bei Handlungsbedarf); Briefing = Abendbericht 22:15 nach dem MW-Update; Schatten-Scan = 22:20-Proposals ohne Write. | `worker/__main__.py` |
| **Reconcile** | :52 stündlich: `unknown`/hängende `pending` gegen den echten Kickbase-Zustand auflösen. | `worker/__main__.py` |
| **Deploy-Einzeiler** | `rsync -az --exclude=… api docker-compose.yml root@…:/opt/kickbase-agent/ && ssh … 'docker compose up -d --build --force-recreate'`; danach **inhaltlich** verifizieren (Marker-grep im Container) — MCP `deploy_verify` macht beides (nur Service `worker`). | SKILL.md „System-Landkarte“, `mcp/tools_ops.py:deploy_verify` |
| **Discord-Kanal / Bot** | Worker postet Briefings, Wächter, Reports (`DiscordChannel`); Approval-Buttons nur für `discord_user_id`; der Bot liest **keine** Chat-Befehle („mach den Trade“ gehört an Claude im Chat). REST-Posts ≤ 1900/1990 Zeichen. | `notify/discord_channel.py`, `worker/kickoff_guard.py` (Textpräzision) |
| **Auth-Modi** | Token-Modus (`KICKBASE_TOKEN` + `KICKBASE_REFRESH_TOKEN`, bevorzugt) oder E-Mail/Passwort; Refresh bei 401 im `TokenCache`; `kba login` zeigt Modus und Ligen. | `config.py`, `server/auth.py`, `cli/login.py` |
| **Rate-Limits / Caches** | Kickbase 1 Request/s (`kickbase_rate_limit_per_second`); LigaInsider ~0,7 req/s, Snapshot-TTL 6 h (`ligainsider_ttl_seconds`; Closing-Bell leert den Cache). | `config.py`, `worker/kickoff_guard.py:run_closing_bell` |
| **Fixtures-Regel** | Test-Fixtures (`api/tests/fixtures/`) entstehen nur aus `kba probe`-Ausgaben (redigiert) — nie erfunden (eiserne Regel 8). | `cli/probe.py`, SKILL.md |
| **Artifact** | Privates Claude-Artifact als Cockpit-Backup (feste URL im Skill, Favicon 🎛️), täglich am Ende der Morgen-Routine; Primär-Hosting bleibt die Box. | SKILL.md „/kickbase cockpit“ |

## (d) API-Feld-Tabelle (Kickbase v4 → Bedeutung → Modellfeld → Quelle)

Endpunkte: `kickbase/endpoints.py`; Parser: `kickbase/models.py`; Client: `kickbase/client.py`.
„Member“ = nur mit Member-Abo im Payload.

| Rohfeld | Bedeutung | Unser Modellfeld | Quelle (Endpoint) |
|---|---|---|---|
| `i` | Spieler-/Liga-/Item-ID | `Player.id`, `League.id`, Feed `i` | squad, market, player_detail, selection, activitiesFeed |
| `fn` / `n` / `ln` | Vorname / Nachname (Squad, Markt, Teamprofil) / Nachname im Detail | `first_name`, `last_name` | squad, market, teamprofile, player_detail |
| `pos` | Position 1 TW · 2 ABW · 3 MF · 4 ST | `Player.position` (`Position`) | alle Spieler-Payloads |
| `mv` / `mvt` | Marktwert / Trend (beobachtet 0/1/2; vermutlich mehrtägig, 2 eher fallend — Hypothese) — nicht die Richtung des letzten Updates, dafür `tfhmvt` (L-118) | `market_value`, `market_value_trend` | squad, market, player_detail, teamprofile |
| `tfhmvt` | 24-h-MW-Änderung in € („seit gestern“, Pfeil der App) | `mv_change_24h` (MCP `mv24`) | squad, market, player_detail |
| `sdmvt` / `mvgl` | 7-Tage-MW-Änderung / MW-Gewinn-Verlust | `TeamProfilePlayer.mv_change_7d` (MCP `mv7d`), `mv_gain_loss` | teamprofile |
| `p` | Punkte (zuletzt) | `Player.points` | squad, market |
| `tp` / `ap` | Punkte gesamt / Ø Punkte (Member) | `PlayerDetail.total_points`, `avg_points`; `TeamProfilePlayer.avg_points` | player_detail, teamprofile |
| `g` / `a` / `y` / `r` | Tore / Vorlagen / Gelbe / Rote Karten | `goals`, `assists`, `yellow`, `red` | player_detail |
| `ismc` / `smdc` / `smc` | Einsätze / Einwechslungen / S11-Einsätze | `appearances`, `sub_appearances`, `s11` | player_detail |
| `sec` | Gespielte Sekunden (Ø Minuten = sec/60/ismc) | `seconds_played`, `avg_minutes` | player_detail |
| `ph` | Formleiste letzte 5 Spiele: `hp` gespielt?, `p` Punkte | `last_points: list[FormEntry]` | player_detail |
| `mdsum` | Spieltags-Übersicht: `day`, `t1`/`t2`, `t1g`/`t2g`, `md` Anpfiff, `cur`, `t1im`/`t2im`, `mdst` Status (0 = nicht gespielt; ≠ 0 unverifiziert) | `matchdays: list[MatchdaySummary]` | player_detail |
| `prob` | Startelf-Stufe 1 Sicher … 5 Ausgeschlossen (Member) | `lineup_prob` | squad, market, player_detail, teamprofile |
| `st` / `stxt` / `dt` | Fitness-Status (0 fit, > 0 angeschlagen) / Text / Status-Datum; `stl` Status-Liste | `fitness_status`, `fitness_text`, `status_since` | squad, player_detail |
| `lst` | Für alle Kaderspieler 1 — **kein** Startelf-Flag (roter Hering) | — | squad |
| `lo` | Lineup-Order 0–10 = echter Starter-Marker | MCP `in_lineup`/`lineup_order`; `lineup_ids` im Wächter | squad |
| `tid` / `tn` / `tim` | Team-ID / Teamname / Club-Logo-Pfad | `team_id`, `team_name`, `team_image` | squad, market, player_detail, teamprofile |
| `pim` | Porträt-Pfad (CDN `https://kickbase.b-cdn.net/<pim>`) | `image_path` (MCP `img`) | alle Spieler-Payloads |
| `shn` / `oui` | Rückennummer / Besitzer-ID („0“ = frei) | `shirt_number`, `owner_id` | player_detail |
| `plpt` / `plpim` / `plpurl` | Datenpartner („Ligainsider“) / Logo / URL | `partner`, `partner_image`; MCP `plpt`, `plpurl` | player_detail |
| `prc` (alt `price`) | Listing-/Mindestpreis eines Markt-Spielers | `MarketPlayer.price` | market |
| `exs` (alt `dt` ISO, `exp`) | Listing-Restzeit in Sekunden (nur Kickbase-Listings) | `expires_at` (= jetzt + exs), MCP `expires_in_s` | market |
| `u` / `u.n` | Verkäufer (Manager-Listing); fehlt bei Kickbase-Listings | `seller`, `listing_type` | market |
| `ofs[]` / `ofc` | Eigene offene Gebote auf ein Listing: `u` User, `uop` Betrag, `uoid` Offer-ID (User-keyed); `ofc` Angebots-Zahl (`ofc: 0` = Gebot verfallen) | `get_own_bids` → amount, offer_id; `TeamProfilePlayer.offer_count` | market, teamprofile |
| `iotm` | „is on the market“ | `TeamProfilePlayer.on_market` | teamprofile |
| `it` | Item-Liste (Spieler, Ligen, Spieltage) | — | squad, market, teamprofile, selection, matchdays |
| `b` / `pbaa` / `pbas` | Konto / Konto nach Annahme aller offenen Gebote (b − pbaa = Gebotssumme) / (ungeklärt) | `get_budget`, `get_budget_fields` | `/me/budget` |
| `lpc` / `adm` / `tv` | Club-Limit je Liga / Admin-Flag / Teamwert | `get_league_rules` | `/leagues/selection` |
| `spl` / `sp` / `psp` / `pspts` | Tabellenplatz / Punkte / Vorsaison-Platz / -Punkte | `RankingEntry` | `/leagues/{id}/ranking` |
| `t` (Feed) | Ereignistyp: 15 Transfer (`byr`, `slr`, `trp`, `pn`, `pi`, `pim`) · 3 Markt-Event (`ln`, `mv`) · 22 Prämie (`bn`, `day`); `dt` Zeit UTC; `af` Liste | `get_activity_feed`, MCP `feed` | `/leagues/{id}/activitiesFeed?max=` |
| `day` / `t1` / `t2` / `bo{o1,ox,o2}` / `dt` / `st` | Spieltag / Heim / Gast / Quoten / Anpfiff / Status (2 = beendet) | `Fixture` | `/competitions/1/matchdays` |
| `dt` (MW-Historie) | Tage seit 1970 (bzw. ms/s-Epoche), `mv`/`v` Wert | `MarketValueSnapshot` | `/players/{pid}/marketvalue/{tage}` |
| `day` / `p` / `mp` / `md` | Spieltag / Punkte / Minuten („102'“) / Datum — nur Saisons `n == "Bundesliga"` | `MatchdayPerformance` | `/players/{pid}/performance` |
| `tkn` / `u` | Login-Token / User-ID (redigiert in `kba probe`) | `LoginResponse` | `/user/login`, `/user/refreshtokens` |
| `cv`, `stud`, `pes`, `cs`, `sl`, `opl`, `iposl` | Ungeklärt — bewusst **nicht** modelliert | — | player_detail |
| `start` (Feed) | Offset-Parameter der Feed-Paginierung (`activitiesFeed?max=100&start=N`, LEARNINGS 23.08.) — im Client nicht genutzt (dort nur `max`) | — | activitiesFeed |

Schreib-Endpunkte (nur Executor): `POST /v4/bonus/collect`, `POST …/market/{pid}/offers`
(Gebot), `DELETE …/offers/{oid}` (Storno), `…/accept` / `…/decline`, `DELETE …/market/{pid}`
(Delist), `POST …/market/{pid}/sell` (Sofortverkauf), `POST …/lineup` (`kickbase/endpoints.py`).

## Handel (Handels-Autopilot, seit 01.10.2026)

- **Handelsspieler:** Kaderspieler, dessen jüngstes echtes Gebot der Handels-Autopilot abgab
  (Vorhaben mit Quelle `trade`). Steht nie in der Elf (Emil 01.10.2026: „sie bleiben Bank“), ist
  keine Verkaufs- oder Tauschware der Engine. Quelle: `trading/trade_book.py`.
- **Handelsbuch:** `load_trade_book` — Handelsspieler, laufende und geplante Handelsgebote. Engine,
  Aufstellung, Lücken- und Ergebnis-Wächter, Radar und Autopilot lesen dasselbe Buch (L-121).
- **Festes Handelsgebot:** abgegeben, gerade in Ausführung oder von Emil angepasst. Bindet das Geld
  der Engine und sperrt den Spieler für sie (`TradeBook.firm_bids`). Ein nur geplantes Handelsgebot
  bindet nichts: Wählt der Mannschaftsplan einen Kauf, räumt der Planer es (`yield_trade_bids`,
  „Engine zuerst“, Befund 03.10.2026, L-124).
- **Plan überholt:** Lauf des Planers, in dem ein festes Handelsgebot mehr bindet, als der Plan beim
  Bau zählte (`Plan.trade_bound`) — etwa weil Emil es währenddessen anpasste oder es ausgeführt
  wurde. Der Plan rechnete dieses Geld als frei; der Lauf ändert keine Vorhaben, der nächste plant
  neu.
- **Verkaufstermin:** spätester Verkauf aller Handelsspieler vor der Spieltags-Frist: T−96 h, solange
  die Elf ohne Handelsspieler offene Plätze hat (besetzbare Plätze der besten Formation, nicht Köpfe:
  `regular_fill`), sonst T−50 h (`sale_deadline`). Die Haltezeit eines Kandidaten zählt nur die
  Marktwert-Updates bis dahin.
- **Handelsdeckung:** was der Handel bis zum Verkaufstermin + 3 h zurückbringt (danach nichts):
  0,9 × MW der Handelsspieler und der Ziele laufender Handelsgebote (`sale_cover`). Gemeinsame Rechnung von
  Autopilot (Deckung zum Anpfiff je neuem Gebot), Ergebnis-Wächter und Pfand-Einlöser.
- **Handelsrahmen:** Kaufpreise offener Handelsspieler plus Handelsgebote, höchstens 20 M (Emil:
  „Ja, bis 20 M“ ins Minus).
- **Stufe „Handel“:** Kappen-Stufe der Schreibstelle: Gebote mit Kaufzweck `handel` auf sicher
  erkannten Kickbase-Auktionen bis 130 % MW (`max_bid_premium_trade`); Manager-Listings 110 %.
- **Steigungsstufe:** 3-Tage-Anstieg 5–10 · 10–15 · ≥ 15 %; je Stufe ein eigener Median-Anstieg als
  Erwartung (unter 15 Spielern die Preisklasse).
