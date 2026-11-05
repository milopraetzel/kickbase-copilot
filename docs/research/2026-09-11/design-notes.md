# Design- und Architekturprüfung · 11. September 2026

## Tatsächliche Stilreferenz

Referenz: `/Users/emil/Selbstständigkeit/WebMCP_Storefront`.
Die aktuelle Startseite `/` rendert `app/customer-portal.tsx`, nicht den alten Storefront-Prototyp.
Ihre Tokens stehen in `app/globals.css:61`, Komponenten in `app/customer-portal.css`.
`app/layout.tsx` lädt Geist und Geist Mono. Keine Screenshots im Repository gefunden; einziges Rasterasset ist ein Anbieterlogo.
Vorschau: `npm run dev -- --host 127.0.0.1 --port 5173`; es handelt sich um Vinext/Vite, nicht reguläres Next.js.
Optional kann die Vorlage mit `?view=overview` geöffnet werden. Ihr Kundenkontext ist privat; zum Stilabgleich reicht lokaler gerenderter Shell/CSS.

### Unverwechselbare Bestandteile, die wir übernehmen

- Ruhiges Anthrazit statt reines Schwarz, flächige Sidebar und sparsame Umrandungen.
- Geist als einzige Sachschrift; große Zahlen mit tabular-nums, keine kondensierten Sport-Schlagzeilen.
- Weiße Hauptaktionen, blauer Datenakzent, Farbe nur für Bedeutung.
- Titel plus erklärender Ein-Satz-Kontext; Belege direkt am Insight aufklappbar.
- Zahlenstreifen mit Trennlinien statt vieler kleiner Dashboard-Kacheln.
- Wenige große Module mit 24–32 px Innenabstand; sachliche Icons, keine Emoji, keine dekorativen Gradienten.

| Rolle | Vorlage | Übernahme im Cockpit |
|---|---|---|
| Canvas | `#171717` | `--bg` |
| Sidebar | `#141414` | neues `--sidebar` |
| Oberfläche | `#242424` | `--surface` |
| Leise Fläche | `#1c1c1c` | `--s2` |
| Hover | `#303030` | `--s3` |
| Trennlinie | `#3b3b3b` | `--line` |
| Haupttext | `#f2f2f0` | `--ink` |
| Nebenfarbe | `#b4b4b0` | `--soft` |
| Hauptaktion | `#f4f4f2` | neues `--accent` (kein positives Marktsignal) |
| Diagramme | `#85a7ff` | `--info` / neues `--chart` |
| Warnung | `#f0c884` | `--warn` |
| Warnfläche | `#332b1e` | `--warn-soft` |
| Negativ | `#ffa5a5` | `--bad` |
| Negativfläche | `#382222` | `--bad-soft` |
| Positiv | nicht kanonisch festgelegt | sanftes Grün nur für positive Ergebnisse, nicht als Brandfarbe |
| Focus | `#a5bfff` | `--ring` |

Vorlagenmaße: Sidebar 232 px (200 px bei schmalem Desktop), Hauptinhalt 40 px Rand und max. 1660 px, Headerhöhe 76 px, Radius 10–12 px, Überschrift 30–36 px/600, Fließtext 16 px/1.55, Tabellen 14–16 px. Mobile reduziert auf einen Contentstrom; Controls mindestens 40 px.

## Tatsächliche Kickbase-Web-App

`web/DEPRECATED.md` verbietet ausdrücklich, die alte Next.js-App zu starten/weiterzuentwickeln/deployen. Sie ist eingestellt.
**Aktiv ist Python → statisches HTML → nginx**, mit bestehenden HTTP-Interaktionen für Vorhaben.

- `api/src/kickbase_agent/cli/dashboard.py`: `NAV`, `build_page(d)`, `kba dashboard-html`.
- `api/src/kickbase_agent/cli/cockpit/theme.py`: zentrale Tokens, Komponenten, Shell, Navigation, Overlay- und Vorhaben-JavaScript.
- `.../data.py`: `collect()` holt Daten; Netzwerk-/DB-Zugriffe gehören hierher, nicht in Renderer.
- `.../pages_core.py`: Markt, Team, Liga, alte Deal-/Traderauswertung.
- `.../pages_zentrale.py`: Lage und ausführbare Vorhaben.
- `.../pages_info.py`: Wissen, News, Regeln.
- `.../pages_insights.py`: Berichte/Lernbilanz.
- `deploy/cockpit-nginx.conf`: liefert aus `/app/data/cockpit/index.html` (immer dieselbe Datei) mit no-store.
- `worker/cockpit_job.py`: aktueller HTML-Build; kein Next-Prozess vorhanden.

Lokal prüfen ohne reale Trades: reale eingefrorene collect-Daten an `build_page(d)` geben, HTML speichern, mit `python3 -m http.server` ausliefern. `kba dashboard-html /tmp/...html` ist ein Read-Build, braucht jedoch Livezugang und mehrere Quellen. Für Preview keine Secrets ins HTML schreiben. Produktive Code-/Containerstände zuerst vergleichen; keine blinde Auslieferung aus dem abweichenden Repository.

## Drei primäre Ansichten

### 1. Heute — Was muss als Nächstes besser werden?

Ein eindeutiges redaktionelles Urteil (etwa „Die nächste Elf zuerst finanzieren“), gefolgt von 3–4 Fakten: Startelfplätze, frei finanzierbares Budget, Rückstand aufs obere Drittel, Datenalter.
Darunter ein breites **Budgetdiagramm** mit Bargeld → gebundene Gebote → notwendige Elfreserve → verbleibendem Spielraum. Verifizierte Tatsachen von Szenarien sichtbar trennen.
Daneben maximal drei priorisierte Entscheidungen mit Spieler, Preisgrenze, Mehrwert und widersprechendem Beleg. Vorhaben bleiben optional ausführbar über die bestehende Intent-Schnittstelle; keine neue Schreibstelle.
News erscheinen dort, wo sie eine Entscheidung ändern: „Startelf unsicher nach Trainerzitat → Preis/Erwartung angepasst“, mit Datum und Quelle. Kein isolierter endloser Nachrichtenfeed.

### 2. Mannschaft — Warum halten, ersetzen oder ergänzen?

Kader als lesbare Spielerliste, sortierbar nach Rolle/Preis/Punkten/Risiko. Kein riesiger dekorativer Fußballplatz als erste Ebene.
Ein Diagramm **Kapitalbindung und Punktertrag** zeigt je Spieler Anteil am Kaderwert und tatsächlichen Beitrag, klar datiert und ohne aktuelle Kadersumme als Manager-Gesamtpunktzahl auszugeben.
Spieler öffnen Detailpanel mit historischem MW, realem Kaufpreis, realisierten/Buch-Ergebnissen, Startelfbelegen, 3–5 kommenden Gegnern und konkreter finanzierbarer Alternative.
Gebotsvergleich separat: benötigte Rolle → Marktoptionen → wirtschaftliche Preisgrenze → beobachtete Ligaaufschläge. Gleiche Positionsrolle und Budgetklasse vergleichen, Datenumfang angeben.

### 3. Liga & Lernen — Was erklärt die Unterschiede?

Aktuelle Tabelle plus Referenzlinie Rang 4 von 12. Zeitreihen nur aus echten historischen Managerständen; Dashboard-/Ranking-Zeitstände nicht vermischen.
**Punktdiagramm Preisaufschlag vs damaliger Marktwert**, mit Preisbuckets/Positionsfilter, einzelnen Käufen, Median und Stichprobengröße. 500k-Floor separat; fehlende Baseline nicht als 0 % zeichnen.
**Ergebnisbrücke** zerlegt eigenen Verlust in gezahlten Aufschlag, Wertänderung während Haltedauer und tatsächlichen Verkaufspreiseffekt. Initial zugeteilte Spieler ohne Anschaffungskosten separat.
**Managervergleich** als geordnete Balken/Tabelle: tatsächliche Saisonpunkte, beobachtete Kaufaufschläge, Transfervolumen, bekannte Positionsbesetzung. Keine versteckten Rivalengebote oder unbekannten Bargeldstände erfinden.
Lernabschnitt zeigt prüfbare Hypothese, Zeitraum, Stichprobe, Ergebnis; Regeln erst nach passenden Rücktests ändern.

Markt, Vorhaben, Protokoll und Methoden bleiben sekundär zugänglich. Falls ein kompletter Navigationsumbau unnötiges Risiko birgt, neue primäre „Analyse“-Startseite einführen und bisherige fünf Bereiche in einer leisen Detailnavigation erhalten; Links/Overlay/Intent-Ausführung müssen erhalten bleiben.

## Visualisierungsgrammatik

- Pro Chart ein Urteil, eine Maßeinheit, eine Zeitbasis und ein Datenabdeckungs-Hinweis.
- Weiß = relevante eigene Serie, Blau = Vergleich/Preisspanne, Grau = Kontext, Amber = unsicher/ausstehend, Rosa = beobachteter Verlust. Grün nur für tatsächlichen Gewinn.
- Keine Gauges für erfundene KI-Sicherheit. Schätzungen als Band/gestrichelt, Messungen durchgezogen.
- Tooltips enthalten Spieler, Ereignisdatum, Betrag, damaligen MW, Aufschlag und Quelle; gleiche Werte als Tastatur-/Screenreader-lesbare Tabelle erreichbar.
- Große prozentuale Floor-Ausreißer standardmäßig nicht still entfernen: separat schaltbar mit Hinweis, damit Achsen nützlich bleiben.
- Native Details/Buttons/Selects statt rein hoverbasierter Bedienung, sichtbarer Fokus und reduced-motion.
- Charts clientseitig aus eingebettetem überprüftem JSON; keine Kickbase-Credentials, Browser-Scraping oder externen Datenrequests im UI.
- Fehlende Daten explizit als „noch nicht erfasst“; null bleibt null, niemals eine täuschende 0.

## Prüfbedarf bei Umsetzung

Bestehende Tests `test_cockpit_fuenf_tabs.py` fixieren historische Labels/Anzahl. Bei gewolltem Navigationsumbau gezielt auf neue Nutzerzusagen anpassen (nicht jede alte Überschrift künstlich erhalten). `test_cockpit_theme_v4.py` fixiert alte Fonts. Manifest-gesiegelte Guardrails dürfen nicht geändert werden. Tests für Deep-Link/Overlay/Intent-Workflow, JS-Syntax und HTML-Escaping bleiben relevant. Neu eingeführte Datenfelder sollten Render-Tests aus echten Exporten bzw. reine analytische Datenobjekte verwenden, keine erfundenen Kickbase-API-Fixtures.
