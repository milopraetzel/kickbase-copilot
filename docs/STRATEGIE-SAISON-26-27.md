# Saison-Strategie 26/27 — Kickbase-Agent (Liga BASE)

> Aktualisierung 11.09.2026: Emils neuer Strategieauftrag priorisiert einen nachhaltig
> starken, bezahlbaren Kader und das obere Drittel. Für neue Entscheidungen den
> [Strategie-Skill](../.claude/skills/kickbase-strategie/SKILL.md) anwenden.
> Ältere Aktivitäts-/Auslastungsziele und pauschale Aufschläge begründen allein keinen
> Trade. Bestehende Schutzregeln und Autorisierung gelten weiter. Dieser Verweis
> ändert keine Worker-Parameter; aktuelle Runtimewerte separat prüfen.

> **Referenzdokument des Agenten.** Die tägliche KI-Recherche und alle Kauf-/Verkaufs-
> Empfehlungen messen sich an diesem Plan. Aktualisiert bei Phasenwechsel.
> Stand: 31.08.2026 (nach Spieltag 1) · Momentum-Doktrin aktiv · Kader 11 · Konto 3,5 M · Teamwert 145,9 M

## Momentum-Doktrin (31.08.2026 — Emils GO: „Automatisierungsstufen freistellen")

Emils Auftrag im Wortlaut-Kern: *zu wenig gekauft, zu wenig verkauft. Spieler, die
im Wert sinken, verkaufen; Spieler, die steigen, kaufen, halten, teurer verkaufen.
Langfristig den gesamten Marktwert des Teams steigern — am besten auf elf Spieler,
die immer spielen, damit maximal Punkte entstehen.* Dazu seine Doktrin vom 29.08.:
*„Das Spiel ist ein Geldspiel — zum Spieltag möglichst Richtung null Konto, alles
auf die elf Spieler verteilt"* und *„nicht die Gewinner verkaufen — der Marktwert
steigt, WEIL der Spieler punktet."*

**Die fünf Sätze der Doktrin:**

1. **Steiger kaufen.** Kauf-Schwelle 0,53 (vorher 0,55 — lag über dem organischen
   Saisonstart-Maximum), Trend-Gewicht im Kauf-Score 0,25 statt 0,15 (effektiv
   22,7 %). Der Elf-Score bleibt trendfrei — die Aufstellung folgt Punkten, nie
   der Börse.
2. **Handlungsfähig trotz Kader-Minimum — seit 01.09. per Pfand-Hebel.** Jeder
   Zuschlag liefert seinen eigenen, sofort verkäuflichen Spieler mit; im Feuer
   stehen nur Overpay und Drift. Die **Plan-Basis** darf deshalb
   risk_budget ÷ 0,35 betragen (risk_budget = Konto − Gebote +
   Momentum-Reserve − Puffer; Reserve = LI-Bank-Spieler ×0,90, nie die letzte
   Besetzung einer Position), hart gedeckelt durch die 33-%-Kaufkraft. Die
   exakte Sicherung ist die Pfand-Buchführung je Lauf: Σ(Gebot − 0,9×MW) ≤
   risk_budget — „alle schlagen zu, alles wird zurückverkauft" endet per
   Konstruktion über dem Anpfiff-Puffer. Dazu wacht der **Einlöser** stündlich
   (ab 96 h vor der Deadline) und zieht Deckungs-Sofortverkäufe mit
   45-min-Veto-Karten aus der Bank, lange bevor die Wächter-Kette müsste.
   Aktiv nur weiter als 48 h vor der Deadline und bei scharfer Notbremse; die
   scharfen Netze (T-120-Storno, T-48h-Gate, T-15-Purge, 33-%-Preflight, jetzt
   inkl. gehärteter Overpay-Kappe) bleiben unangetastet.
3. **Verlierer schneller raus — auch aus der Elf.** Trend-Verkaufsschwelle 0,29
   (≈ −2 %/7 Tage, vorher −3,5 %) für Spieler ohne Startelf-Pfad; ein Verlierer
   MIT Startelf-Pfad wird GETAUSCHT (Emil 02.09.: eine Strategie ist nicht,
   Spieler zu halten, die im Wert sinken): Gebot auf den Ersatz derselben
   Position plus Sofortverkauf, der wartet, bis der Ersatz im Kader steht
   (`after_squad_size`). Ein Gebot wird zur Ausführung am aktuellen Marktwert
   nachverankert (Nachanker ≤ +10 %, L-096). Listings, die nach 48 h kein
   Angebot gesehen haben, gelten als gescheitert (L-081) und werden in
   Sofortverkauf überführt. *(UMGESETZT 01.09., Emils GO: Der Planner überführt stündlich — nur LigaInsider-Bank, Schutzliste + Kader-Deckung wie im Accept-Pfad, 45-min-Veto-Karte; `propose_failed_listing_sells` + `failed_listings_from_raw`.)*
4. **Gewinne realisieren am Wendepunkt.** Neu: Ein starker Steiger (≥ +3,5 %/7d)
   OHNE Startelf-Pfad wird sofort verkauft, sobald sein jüngster Tagesschritt
   kippt (`take_profit`). Steiger MIT Startelf-Pfad werden NIE zur Kasse gemacht
   — sie sind die Punktelieferanten (Emils Regel).
5. **Recherche schlägt Trend.** LigaInsider-Veto und Verletzungsregeln gelten
   unverändert; Community-/Recherche-Tipps wirken jetzt auf BEIDE Seiten
   (Kauf-Boost UND Verkaufs-Schutz für Kaderspieler, ±0,08) — „die Ausnahme, die
   die Recherche längst kennt".

**Nicht verhandelbar bleiben:** Konto ≥ 0 zum Anpfiff, Kader ≥ 11 + stellbare
Formation **ab 48 h vor dem Anpfiff** (Kader-Gesetz — vorher ist der Kader ein
Portfolio: Verlierer gehen raus, Positions-Minima 1 TW/3 ABW/2 MF/1 ST bleiben;
seit 02.09. spiegelt der Executor genau das, L-095), Verfassung (Olise),
200er-Ampel (Stand 31.08.: 16/200), Tages-Caps
5 Käufe / 5 Verkäufe / 12 Listings (Listings zählen seit 31.08. auch in der
Vorhaben-Pipeline nicht mehr als Verkäufe — L-077 vollendet).

**Bewusst NICHT umgesetzt** (Siegel-Zusicherungen, Entscheidung liegt bei Emil):
Direktgebot 120 % bei Kader-Bedarf (versiegelt: „Erstgebot bleibt die niedrige
Staffel"), Sofortverkauf schon in der Trend-Regel (versiegelt: „Wertverlierer
werden gelistet"), Plan-Basis auf voller 33-%-Kaufkraft (widerlegt 29.08.:
bei Kader-Minimum unheilbar).

**Messung (Review SP3):** Teamwert-Wachstum/Woche, Vorhaben je Tag (Soll ≥ 2),
Treffer-Bilanz der Momentum-Käufe nach 1/7 Tagen, Whipsaw-Quote der
Trend-Verkäufe, take_profit-Bilanz (Verkaufspreis vs. MW nach 7 Tagen).

## Leitprinzip

**Hinrunde = 70 % Börse, 30 % Fußball.** Es gewinnt, wer Kapital aufbaut, nicht wer
die schönsten Namen hat. Zwei getrennte Portfolios:
- **S11-Kern**: 11 + 2 Reserve, die *wirklich spielen* (LI-Starter) und Base-Points sammeln.
- **Trading-Slots**: Rest des Kaders für Marktwert-Steiger (kaufen → halten → Wendepunkt → verkaufen).

**Emils Direktive: „Kohle in die Defensive."** DEF-Starter sind die stabilsten
Base-Points pro Euro (weniger Rotation, mehr Minuten, billiger als MIT/STU) — der
S11-Kern wird defensiv-lastig gebaut (5-4-1/4-5-1-tauglich).
*Review-Ergänzung (externes Gutachten): das beste Punkte-pro-Euro-Verhältnis haben
oft zentrale SECHSER/Ballverteiler — Pässe, Zweikämpfe und Ballaktionen liefern
ergebnisunabhängig 100–150 Rohpunkte. Unsere Khedira/Schäfer/Ulrich/Højlund-Achse
spielt genau diese Karte; bei künftigen Käufen zählt „Defensive ODER Sechser".*

## Warum JETZT die wichtigste Phase ist (bis Spieltag 3)

1. **Preisfindung:** Marktwerte starten auf Baseline und beginnen erst mit den ersten
   Spieltagen stark zu laufen. Wer *vor* dem ersten Anstieg kauft, nimmt die volle Welle mit.
2. **Aufsteiger-Arbitrage:** Paderborn-/Elversberg-Stammspieler sind systematisch
   unterbewertet (4–8 M für garantierte Minuten). Die Community entdeckt sie erst
   nach 2–3 Spieltagen → klassischer Season-Start-Edge.
3. **Markt-Flut:** Zum Start werden viele Spieler gelistet — größte Auswahl, geringste
   Konkurrenz-Preise der ganzen Saison.

## Harte Regeln (ändern sich nicht)

| Regel | Wert |
|---|---|
| Jeder S11-Kauf | muss **LI:starter** sein (kein Bank-Kauf für den Kern) |
| Einzelgebot | Richtwert ≤ 25 % Budget; Tier-1 nur mit klarem Börsen- oder S11-Grund. **Seit 21.09.2026 (Turnaround-Studie): Die Engine handelt über Vorhaben mit Veto-Fenster; Sessions schreiben nur mit Emils namentlichem Auftrag für genau diesen Spieler.** Die Delegation vom 23.08. ist abgelöst |
| Starter-Schutz (Emil, 23.08.) | **Keine Starter verkaufen** (LI-Startelf UND in unserer Elf) — außer als positionsgleicher Tausch gegen einen besseren Starter; **immer ≥ 11 einsatzfähige Spieler + Bank**; Finanzierung zuerst aus Nicht-Startern |
| Overdraft | nie unter −Teamwert/3 · **Fr 17:00 Purge** auf ≥ 0-Puffer |
| Beschaffung (Emil, 25.08.) | **Kickbase-Listings zuerst** — dort gewinnt das höchste Gebot automatisch (Bilanz 25.08.: 4 von 5 Zuschlägen kamen von dort, 4 von 5 Manager-Deals scheiterten). Manager-Listings NUR mit ordentlichem Aufschlag (≥ +15 %, Hoffmeier-Muster) oder gar nicht. **Kein Kapital in unwahrscheinlichen Wetten binden** — ein Gebot unter dem Marktwert eines Manager-Listings ist tot und blockiert nur den 33-%-Spielraum |
| Ein Schreiber (21.09.2026) | Das Pauschalmandat vom 25.08. ist abgelöst (archiviert: `docs/archive/MANDAT-AKTIVER-MANAGER.md`). Geld bewegt die Engine über geprüfte Vorhaben mit Veto-Fenster; jede Session braucht Emils namentlichen Auftrag je Spieler. Verweigert eine Sicherung: stoppen und melden. Transparenz bleibt Pflicht (Discord + Audit) |
| Reihenfolge (Emil, 23.08.) | **Bieten ZUERST, verkaufen nur bei Zuschlag.** Kickbase verlangt beim Bieten kein Bargeld (33-%-Regel zählt offene Gebote mit), erst zum Anpfiff ≥ 0 — nie mehr „erst verkaufen, dann bieten“ |
| Verkauf S11-Kern | nur bei klarer Verbesserung (Ersatz-Duell) oder LI:out-Serie |
| Aufstellung | nur LI-Starter, sofern Kader es erlaubt (Auto-Set Fr 19:45 + Sa/So 10:00) |

## Phase 0 — Kader-Chirurgie (DIESE WOCHE, vor Spieltag 1)

**Problem:** 6 von 13 Kaderspielern sind laut LigaInsider ohne Stammplatz (~21 M totes
Kapital) → die Aufstellung MUSS aktuell Nicht-Spieler aufstellen. **Lösung: erst kaufen,
dann gestaffelt verkaufen** (nie unter 11 aufstellbare Spieler fallen).

### Käufe (Priorität, alle LI-Starter, Gebote +5–8 % über MW)

| # | Spieler | Pos | Team | MW | Gebot | Warum |
|---|---|---|---|---|---|---|
| 1 | **Laurin Ulrich** | MIT | Paderborn | 5,3 M | *läuft* | Aufsteiger-Starter, Gebot platziert ✓ |
| 2 | **Marcel Hoffmeier** | ABW | Paderborn | 8,2 M | ~8,7 M | **Job-Erbe** (Awortwie-Grant out) + „Kohle in die Defensive" |
| 3 | **Otávio** | ABW | Frankfurt | 7,9 M | ~8,4 M | Stärkster MW-Trend, Profiteur (Ngankam out) |
| 4 | **Oscar Højlund** | MIT | Frankfurt | 3,4 M | ~3,6 M | Starter für 3,4 M = Preisfehler |
| 5 | **Edin Džeko** | STU | Schalke | 9,4 M | ~9,9 M | Löst das Sturm-Problem (Vogt=Bank); Base-Points, Profiteur |
| 6 | **Luca Schnellbacher** | STU | Elversberg | 4,8 M | ~5,1 M | Billiger STU-Starter als Trading-Wette |

**Summe Gebote ≈ 41 M** → aus Cash (59,8 M) bezahlbar, **kein Overdraft nötig**,
~19 M Reserve bleibt für Tages-Chancen.

### Verkäufe (NACH mindestens 2 erfolgreichen Zugängen, gestaffelt)

| Spieler | MW | LI | Reihenfolge |
|---|---|---|---|
| Scally | 4,6 M | bench (0.05) | sofort |
| Hanche-Olsen | 3,7 M | bench | sofort |
| Reggiani | 4,0 M | bench | sofort |
| Ndiaye | 2,7 M | bench | nach Zugang MIT |
| Osterhage | 4,6 M | bench | nach Zugang MIT |
| Vogt | 1,5 M | bench | erst wenn STU-Ersatz im Kader |

→ **+21 M zurück**, Netto-Invest ≈ 20 M, Kader danach ~13–14 mit **12–13 echten Startern**.

### Ziel-Elf nach Phase 0 (defensiv, alles LI-Starter)

**TW** Schwäbe · **ABW** Minjae, Leszczynski, Bernardo, Hoffmeier, Otávio (5er-Kette möglich)
· **MIT** Khedira, El Khannouss, Schäfer, Ulrich/Højlund · **STU** Džeko

## Premium-Optionen (Beobachten, nicht Woche 1)

| Spieler | MW | Status | Einstiegs-Trigger |
|---|---|---|---|
| **Kimmich** | 59,8 M | Starter, Base 0.99 — DIE Punktemaschine | Ab SP2–3, wenn Kaderchirurgie durch + Budget > 65 M. Tier-1 → Emil-Approval. Frisst die Liquidität der Arbitrage-Phase — deshalb NICHT sofort. |
| **Musiala** | 34,4 M | bench (Comeback nach Verletzung) | Kaufen BEVOR LI ihn zurück in die Startelf nimmt (MW-Explosion beim Comeback). Watchlist der Morgen-Recherche. |
| **Aleix García** | 38,4 M | Starter (Ben Seghir out) | Nur falls MW-Trend anspringt — sonst zu teuer für Profiteur-Logik. |

## Wochen-Rhythmus (läuft automatisch)

| Wann | Was | Wer entscheidet |
|---|---|---|
| Mo–Do | Invest-Fenster: Gebote auf Steiger/Starter | Agent schlägt vor → **Emil: „mach den Trade"** |
| Täglich 08:27 | KI-Recherche (Tipps/Risiken/Trade des Tages) gegen DIESE Strategie | Agent |
| Täglich 09/13/18 | Markt-Puls (neue Listings ↔ passt es in die Strategie?) | Agent (leise) |
| Fr 17:00 | Purge-Check (Overdraft glattstellen) | Agent (Regel) |
| Fr 19:30/19:45 | Lineup-Leak → **Aufstellung automatisch setzen** | Agent (nur LI-Starter) |
| Sa/So 10:00 | Spätausfall-Check → Aufstellung nachziehen | Agent |
| 22:15 | Tagesbriefing: Stand, Trades, Elf, LI-Status | Agent |

## KPIs (daran messen wir uns)

1. **Bis Spieltag 3: 11/11 der gesetzten Elf sind LI-Starter** (heute: 7/11).
2. **Teamwert-Wachstum > +1,5 M/Woche** in der Invest-Phase.
3. Kein Kaderplatz länger als 7 Tage mit Score < 0.15 (totes Kapital raus).
4. Jeder Trade nachvollziehbar im Audit-Log (`rule_id`), keine Guardrail-Verletzung.

## Profi-Benchmark (20.08.2026 — Ligabase-Trading-Guide, Community-Guides)

**Deckungsgleich mit dem Profi-Konsens:** Saisonstart als stärkstes Kauf-Fenster
(„Neuzugänge/Hype-Spieler steigen in den ersten Wochen"), billige Abwehr-Stammspieler
als Saisonstart-Basis („Abwehr-Beton", Zu-Null-Boni), Verletzungs-Vertreter kaufen
(= unser Profiteur-Scout), Overpay-Gebote wegen 22-Uhr-Bewertung, antizyklisch handeln,
33-%-Regel. **Kickbase wird nicht am 1. Spieltag gewonnen, sondern durch tägliche
Aktivität** — genau dafür existieren Puls/Briefing/Recherche.

**Vom Benchmark gefundene Lücken (Regeln ab sofort):**
1. **Vereinslimit — GELÖST (20.08.):** Das Limit steht im API-Feld `lpc` des
   selection-Objekts und wird jetzt **live gelesen und als Guardrail erzwungen**
   (Kader + offene Gebote + eigene Vorschläge zählen pro Club — keine toten
   Doppel-Gebote). **BASE steht auf lpc=11 = praktisch kein Limit** — die alte
   „max 2/Verein"-Proxy-Regel entfällt, ein 3. Paderborner (z. B. Müller) ist erlaubt.
   Der Guardrail schützt trotzdem dynamisch, falls der Admin das Limit je senkt.
2. **Anpfiff-Deadline ist härter als gedacht:** Wer beim Spieltags-Anpfiff im Minus
   steht, bekommt für den GESAMTEN Spieltag NULL Punkte. Der Fr-17:00-Purge ist damit
   überlebenswichtig, nicht nur Hygiene. Zudem: Minus erst NACH Anpfiff des ersten
   Spiels eines Spieltags erlaubt → vor Spieltag 1 gibt es noch keinen Overdraft
   (Phase 0 ist ohnehin cash-only ✓).
3. **Trading-Prämien** („Händchen": 3 M Gewinn → 250 k … 25 M → 2 M) — Gratis-Geld,
   das der Verkaufs-Trigger künftig mitdenken sollte (knapp vor Prämienschwelle halten).

**Konsequenz für Phase 0 — gestaffelt statt alles sofort:**
- **Welle 1 (jetzt, hochsicher):** Ulrich (läuft) + Hoffmeier + Otávio + Højlund ≈ 26 M.
  Job-Erben + bestätigte Starter ≤ 9 M — hier ist Warten teurer als Handeln.
- **Welle 2 (nach Spieltag 1):** Džeko + Schnellbacher (± Alternativen) — erst wenn die
  ECHTEN Startaufstellungen von SP1 die LI-Prognosen bestätigt haben. Halbiert das
  Fehlkauf-Risiko der unsichersten Phase und kostet fast kein Preisfenster.

## Interne Liga-Regel: 200 Transfers pro Halbserie (20.08. von Emil eingebracht)

Mündliche Abmachung der Liga (nicht in den App-Einstellungen sichtbar): **max.
200 abgeschlossene Transfers je Halbserie.** Das System zählt live über den
Activity-Feed (eigene Käufe mit Zuschlag + Verkäufe), zeigt den Stand in
Recherche und Briefing (🟢/🟠/🔴) und **sperrt die Engine ab 195** — die letzten
5 Trades bleiben als Notreserve für Verletzungen und die Anpfiff-Notbremse.
Konsequenz für die Trading-Fabrik: Qualität vor Frequenz — ~11 Trades/Woche
Budget, kein Hochfrequenz-Churning um Kleinstgewinne.

## Gebots-Psychologie: Conviction-Aufschlag (20.08., Emils Punkt zu emotionalen Bietern)

Menschen überbieten aus Emotion. Reine Value-Gebote (MW +5 %) verlieren dann
genau die Spieler, die wir strategisch brauchen. Entscheidung: **zweistufig** —
Standard-Ziele MW +5 % +13.337; **Conviction-Ziele** (Community-Tipp ≥0.7 oder
direkter Job-Erbe) MW **+9 %** +13.337. Value-Disziplin bleibt über die
25-%-Budget-Grenze und die 5-M-Approval-Schwelle erhalten — wir zahlen bewusst
einen kleinen Emotions-Aufschlag auf wenige Ziele, statt leer auszugehen.

## Harte Kriterien für Autopilot Stufe 2 (verschärft nach externem Review)

Stufe 2 (Trading autonom ≤ 1,5 M) wird erst gezündet, wenn ALLE erfüllt sind:
1. **14 Tage fehlerfreie API-Writes** (keine failed-Einträge im Audit-Log).
2. **Realisierter Trading-Gewinn > +500 k €** aus freigegebenen Empfehlungen.
3. **Anpfiff-Wächter hat ≥ 1 Spieltag fehlerfrei überwacht** (T-120/T-60/T-35
   gefeuert, Konto beim Anpfiff ≥ 0).
4. Emils explizites GO. — Ab 5 M € und Tier-1 bleibt IMMER Emil, in jeder Stufe.

## Verletzungs-Regel (die Profi-Praxis, seit 21.08. im System)

Wenn ein eigener Spieler ausfällt, entscheidet die **Ausfalldauer**, nicht der Reflex:

| Schwere (aus LI-Grund-Text) | Regel | Warum |
|---|---|---|
| **Langzeit** (Kreuzband, Bruch, OP, Monate, Aufbautraining) | **SOFORT verkaufen** — idealerweise vor dem 22-Uhr-Update | Sofortverkauf zahlt den AKTUELLEN MW; der Wert fällt danach wochenlang. Geschwindigkeit = Geld. |
| **Kurzzeit** (angeschlagen, fraglich, muskulär, Infekt) | **HALTEN** | Der kleine Dip ist eingepreist; „Rückkehrer ziehen beim Comeback an" (Ligabase) — Panikverkauf am Tiefpunkt ist der klassische Anfängerfehler. |
| **Unklar** | beobachten | Nächste LI-/News-Lage abwarten, dann einstufen. |

Bonus-Play: Bei Langzeit-Ausfall eines Stars → verkaufen und den **Rückkauf zum
Comeback-Zeitpunkt** auf die Watchlist (Musiala-Muster: kaufen, BEVOR LigaInsider
ihn zurück in die Startelf nimmt). Der Kader-Alarm im News-Update nennt die
Empfehlung jetzt automatisch je Schwere; die Aufstellung sortiert Verletzte ohnehin
sofort aus (LI-Veto ×0).

## Ehrliche Annahme-Grenzen

- Vor Spieltag 1 gibt es kaum Punkte-/Trend-Historie → die Strategie stützt sich auf
  **LI-Starter-Status + Meta-Heuristik** (Aufsteiger-Arbitrage). Ab SP2 übernehmen
  wieder Base-Points/Form als Hauptsignal; Gewichte werden dann nachkalibriert.
- LI-Prognosen vor SP1 sind selbst unsicherer als im Saisonverlauf → Käufe brauchen
  den Doppel-Check der Morgen-Recherche (News: Verletzung? Wechselgerücht?).

## Umsetzung des Strategieauftrags vom 11.09.2026

Die [Expertenrecherche](research/2026-09-11/expert-research.md) bewertet 40 Quellen
und zusätzliche Datenangebote nach tatsächlichem Entscheidungsnutzen und Aufwand.
Der [Datenvertrag](research/2026-09-11/data-contract.md) trennt beobachtete Preise,
verfügbare Historie, bestätigte Transfers, Minuten, Punkte und Nachrichten.

Der neue Planfilter erhält Schulden und sperrt den Pfandhebel bei fehlenden
verifizierten Startern. Tausche werden am besten vollständigen Mannschaftsindex und
an gemeinsamer konservativer Finanzierung geprüft. Gleiche Mannschaftsstärke mit
Mehrkosten wird abgelehnt. Das verhindert konkrete Fehler; ein wirtschaftlich optimaler
Höchstpreis oder validierte erwartete Punkte folgen daraus noch nicht.

Für das obere Drittel in BASE zählt der Abstand zu Platz vier nach abgeschlossenen
Spieltagen. Die neue App zeigt laufende Spieltagspunkte getrennt. Datenfehler,
Prognosefehler und Ausführungsfehler werden bei der Retro getrennt geprüft.
