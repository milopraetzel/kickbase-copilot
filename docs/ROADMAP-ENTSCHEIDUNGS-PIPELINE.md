# Roadmap: Entscheidungs-Pipeline mit harten Gates (P2, aus Hermes-Review 21.08.)

Zielbild für die nächste Ausbaustufe — nach SP1 priorisieren, nichts davon ist
SP1-kritisch. Quelle: Hermes-Schatten-Review (P2-Punkte 11–15), von Claude
geprüft und übernommen.

## Leitgedanke

Nicht „Score hoch = kaufen", sondern eine Kette harter Gates, die JEDE
Empfehlung durchlaufen muss:

```
Score über Schwelle
+ Daten frisch (Alter je Quelle unter Limit)
+ Budget ok            + Deadline ok
+ Trade-Limit ok       + keine Contra-News zum Spieler
+ Preis im Korridor    + Preflight bestätigt (Sekunden vor dem Write)
= Handlung erlaubt
```

Pipeline: Daten sammeln → Qualität bewerten → Signale normalisieren →
Vorschlag → Guardrails → Live-Preflight → **Action-Inbox** → Freigabe/erlaubte
Klasse ausführen → Ergebnis verifizieren → Audit.

## Action-Inbox (Cockpit-Ausbau)

Statt reiner Tabellen eine Entscheidungs-Ansicht in vier Zonen:
- 🔥 **Jetzt entscheiden** — je Karte: Aktion, Top-3-Gründe, Top-3-Risiken,
  Grenze (Maximalbetrag/Deadline), Datenfrische, nächster Schritt
- 🟡 **Beobachten** — offene Gebote, auslaufende Listings, fragliche Spieler
- 🟢 **Sicher** — Elf ok, Konto ok, Limits ok, keine Deadline < 24 h
- 🔴 **Blockiert** — Daten zu alt, API-Fehler, Guardrail verletzt, Freigabe fehlt

Jede Empfehlung maschinenlesbar: {aktion, gruende[3], risiken[3], grenze,
datenfrische, confidence, next_step}.

## Einzelpunkte (Reihenfolge nach SP1)

1. Datenfrische-Status je Quelle (Kickbase/Markt/LigaInsider/News) erfassen
   und im Cockpit-Kopf zeigen; Empfehlungen tragen das Alter ihrer Daten.
2. News→Spieler-Matching (Contra-News blockt Kauf-Gates).
3. Markt-Seite mit Empfehlungs-Spalte verbinden (Score + Budget + Kontra →
   „kaufen bis X / nicht kaufen / beobachten").
4. Action-Inbox als neue Cockpit-Startseite.
5. Confidence je Empfehlung (Datenlage + Signal-Einigkeit).
6. mypy-Bereinigung (46 Fehler): echte Logikfehler von Stub-Lärm trennen.
7. **Reconcile-Job** (Hermes-Audit 2, P0 vor Autonomie): `unknown`- und
   hängende `pending`-Writes gegen den echten Kickbase-Zustand abgleichen
   (Kader/Gebote/Budget lesen → Status auflösen) — Pflicht, bevor irgendein
   Auto-Trading-Pfad live geht.
8. **Mini-Auto-Trading als SEPARATER Executor-Pfad** (Hermes-Stufenplan):
   nur Trading-Zone, nie Core, Cap je Spieler (0,5–1,5 M), harte Daily-Caps,
   Frische-Pflicht, Contra-News-Block, Auto-Stopp beim ersten unknown/failed.
9. **Parameter-Agent mit harten Leitplanken** (erst danach): Schwellen nur in
   Grenzen anpassbar (Buy 0.55–0.75, Premium 1.02–1.10, Daily-Cap 0–3,
   Autotrade-Budget ≤ 5–10 % Liquidität).
10. mypy-Bereinigung (48 Fehler) + EINE Deployment-Quelle der Wahrheit
   (Docker/Compose ist produktiv; systemd-Doku als veraltet markieren).
11. Persistenter Trade-Zähler (Dedupe über Feed-Item-ID `i`), sobald der
   Liga-Feed 1000 Einträge übersteigt (Fail-Safe warnt dann von selbst).
12. **Agenten-Schreibpfad auf CLI-Ebene** — OPTIONAL, KEIN Sicherheitsthema mehr
   (Stand 25.08.): Das Sicherheitsloch war nicht der Transport, sondern die
   fehlende App-Authentifizierung an ALLEN mutierenden Routen; das ist mit dem
   Traefik-Schreib-Gate (6f69a6b, `docs/BETRIEB.md` §3.1b) geschlossen — inklusive
   `adjust`/`execute-now`, die ein CLI-Umbau gar nicht erreicht hätte. Was bliebe,
   sind Bequemlichkeiten: ein einziger Auth-Mechanismus (ssh-Key) für Agent→Box,
   Schreiben auch wenn der api-Container steht, Gleichklang mit `tip_add`/
   `report-add`. Falls es doch gebaut wird:
   `kba intent add` (bid/list/sell) mit der Anlage-Logik aus
   `server/routes/intents.py` (Validierung, Dedupe inkl. `executing`,
   emil_go-Pflicht ≥ 5 M — in gemeinsame Funktion extrahieren, Route und CLI
   rufen sie); die MCP-Write-Tools senden dann via ssh + `docker exec` statt
   `POST /api/intents` (derselbe Kanal wie `tip_add`/`report-add`, ssh-Key =
   Auth). Die POST-Route könnte danach browser-only werden. Das Schreib-Gate
   bleibt in jedem Fall bestehen — die Browser-Routen (veto/adjust/execute-now)
   brauchen es weiterhin, egal welchen Weg der Agent nimmt.

## Neu durch Review 31.08.: Doktrin-Luecken

- **48-h-Listing-Automatismus** — ERLEDIGT 01.09. (Emils GO): Der Planner
  ueberfuehrt eigene Manager-Listings ohne ein einziges Angebot nach 48 h in
  Sofortverkaufs-Vorhaben (`propose_failed_listing_sells` in decision/policies,
  `failed_listings_from_raw` in worker/intents). Guards wie Accept-Pfad:
  nur LI-Bank (fail-safe ohne LI-Daten, L-090), Schutzliste, Kader-Deckung,
  45-min-Veto-Karte (>= 5 M: 2 h). Der STRATEGIE-Satz traegt den
  UMGESETZT-Vermerk.
- **Pfand-Hebel-Spec neu rechnen** vor jeder Umsetzung (Warnblock in der Spec:
  0.28-Beweis deckt 135-%-Cap und 1,5x-Adjust nicht).

## Neu durch Member-Abo (23.08.): prob-Validierung nach SP1

- Kickbase-Member-Felder sind live (prob-Startelf-Stufe, stxt-Verletzungstext,
  echte Fotos). Nach SP1: `prob` gegen die echten Aufstellungen validieren
  (welche Stufe startet wie oft?) — erst DANN als Signal/Zweitquellen-Dämpfer
  in lineup_service verdrahten (LI bleibt primär; Konfliktfälle als
  Quarantäne-Ping). `stxt` als Contra-News-Quelle in Punkt 2 einspeisen.

## Erledigt (31.08.): Discord-Push für Vorhaben — Veto-Buttons im Kanal

- Spec `docs/superpowers/specs/2026-08-31-discord-veto-buttons.md`, umgesetzt
  31.08.: Jedes vom Planner NEU angelegte Vorhaben erscheint als Discord-Karte
  mit Buttons (▶ Jetzt ausführen · +10 % · +20 % · Überspringen) — dieselben
  Eingriffe wie die Zentrale, über den extrahierten gemeinsamen Service
  `kickbase_agent/intents/actions.py` (Route und Buttons rufen identische
  Funktionen; atomare `UPDATE … WHERE status='planned'`).
- Restart-sicher über EIN `discord.ui.DynamicItem`-Template
  (`kba:i:<verb>:<id>`, `notify/intent_cards.py`); fail-closed auf
  `DISCORD_USER_ID`; Buttons mutieren ausschließlich Intent-Zeilen — kein
  neuer Kickbase-Schreibpfad.
- Kehrer im Executor-Tick (*/5 min) zieht veraltete Karten nach (auch
  Web-Vetos aus der Zentrale, Latenz ≤ 5 min); Endzustände machen die Karte
  final (Buttons weg); gelöschte Karte (404) wird nie neu gepostet.
- Stufe 2 bewusst offen (Spec §9): „Betrag ändern"-Modal, DM statt Kanal,
  Rückbau der alten Approval-Buttons auf DynamicItem.

## Erledigt (21.08. abends): Phase-2-Bausteine GEBAUT, dormant

- **Reconcile-Job** läuft stündlich :52 (unknown/pending gegen Live-Zustand,
  konditionale Auflösung, Alarm bei Unklärbarem) — Punkt 7 ✅
- **Schatten-Decision-Scan** täglich 22:20 (strukturierte Proposals + „hätte
  getan"-Report mit Gate-Status je Kandidat)
- **Auto-Trading-Executor** täglich 22:30, Flag AGENT_AUTO_TRADING=0 (dormant):
  alle Gates hart im Code (≤1,5M, nie Approval-pflichtige, Core-Schutz, Daily-
  Caps, Incident-Selbststopp, nur list nie sell) — Punkt 8 ✅ gebaut; SCHARF
  erst nach den Stufe-2-Kriterien + Emils GO
- Damit läuft ab sofort die Beweis-Uhr: fehlerfreie Writes zählen, der
  Schatten sammelt die „hätte gekauft"-Bilanz für den Backtest.

## Erledigt im Zuge des Reviews (21.08.)

- AGENT_ALLOW_LIVE nur noch für Trade-Typen (Lineup/Bonus ausschließlich über
  ihre eigenen Flags) · max_bid_premium hart erzwungen · Sell/List-Preflight
  fail-closed · zentrale Client-Fabrik mit Token-Refresh in Worker/CLI ·
  Purge-Erfolg erst ab 10.000-Puffer (drei Zustände) · Discord-Texte
  präzisiert · Approval-Buttons restart-sicher · alte Next.js-App (web/)
  eingestellt (DEPRECATED.md) — Cockpit ist die einzige Oberfläche.
