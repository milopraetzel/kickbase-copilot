# Umsetzung und Abnahme der Mannschaftsengine

Stand: 13.09.2026. Auftrag: Emils Freigabe „Okay bau das alles so“ zum recherchierten Engine-Vorschlag und anschließende ausdrückliche Live-Freigabe. Die vormals vorgeschlagene starre Zweierkuration wurde ersetzt, ihr unbenutzter Implementierungscode entfernt. Die Deployment-Belege im Arbeitsverzeichnis dokumentieren den tatsächlichen Aktivierungszeitpunkt.

## Tatsächlich angeschlossene Entscheidungskette

`worker.engine_cycle` prüft alle zehn Minuten den relevanten Zustand und plant bei Änderungen, spätestens nach einer Stunde. `cli.plan.build_plan` verbindet aktuelle Daten, Prognosen und gemeinsame Optimierung. Die bisherige Vorhabenverwaltung und `ActionExecutor` bleiben der Ausführungspfad. Der Planer schreibt selbst nicht nach Kickbase.

Auch HTTP-/Remote-MCP-Käufe benötigen jetzt eine serverseitig frisch berechnete Mannschaftsprüfung. `GET /api/purchase-plan` liefert die ausgewählten Spieler und Preise; `POST /api/intents` prüft den Wunsch erneut und übernimmt Position, Verein, Listingart und Bedarf aus den beobachteten Plan-Eingaben. `manual=True` bleibt als Schutz vor automatischem Rückzug erhalten, gestattet aber keinen Kauf ohne Engine-Beleg. Alte offene Vorhaben ohne Beleg werden verweigert; bereits gesetzte Kickbase-Gebote werden deshalb nicht zurückgezogen. Der authentifizierte Hermes-Operator ist an diesen Weg angeschlossen. Lokaler MCP-Kauf ohne Remote-Ziel und ohne vollständigen Engine-Beleg ist ausdrücklich gesperrt.

`decision.team_optimizer` vergleicht alle neun Formationen über drei aufeinanderfolgende Optimierungen: besetzbare Plätze, prognostizierte Punkte der nächsten vier Spieltage, Restgeld. Tatsächlicher Besitz, angenommene offene Verpflichtungen und neue Käufe sind getrennt. Alle gleichzeitig erfolgreichen Käufe müssen finanzierbar bleiben; mögliche Verkaufserlöse zählen nicht zum Geld. Technische Einzelpreis-, Marktwert-, Kader-, Vereins- und Tagesgrenzen bleiben zusätzliche Bedingungen. Zeitbegrenzte Solverergebnisse werden nicht als bewiesen optimal ausgegeben.

`decision.bidding` ermittelt den wirtschaftlichen Preisbereich durch erneute Optimierung der Mannschaft bei verschiedenen Kaufpreisen und Vergleich mit der besten Alternative. Ein kalibriertes Konkurrenzmodell kann innerhalb dieses Bereichs den Erwartungswert bestimmen. Ohne qualifiziertes Modell bleibt die Zuschlagschance unbekannt; der Einstieg ist ein ausdrücklich konservatives Minimumgebot, kein behauptetes Auktionsoptimum. Bereits offene Beträge werden nicht heimlich verändert. Ersatzkäufe nach verlorenen Auktionen brauchen noch erreichbare Fristen.

Die Live-Abnahme erkannte nach Freigabe des gebundenen Budgets eine zu frühe Bankspieler-Ausnahme: Güther wurde bei KB5 und fallendem Marktwert mehrere Tage vor der Deadline als Ergänzung vorgeschlagen. Vorhaben 186 wurde vor Ausführung vetoed. Die Ausnahme gilt jetzt im Planer und im frischen Executor-Preflight nur bei tatsächlicher Lücke und `0 < Deadline − jetzt ≤ 24 Stunden`; fehlende, naive oder vergangene Deadlines erlauben keinen solchen Kauf. Die unveränderte aufgezeichnete Spieler-Detailantwort ist die Regressionseingabe. Der Parameter-Record nennt Anlass und Prüftermin.

`decision.funding_recovery` repariert eine vorhandene Überbindung zunächst über noch nicht ausgeführte automatische Reservierungen. Für nötige vollständige Gebotsaufgaben werden Identität, Betrag, Konto, Besitz, eigene Gebote und Auktionsende erneut geprüft. Manuelle Eingriffe bleiben geschützt. Eine aufgegebene Auktion erhält eine Wiedereinstiegssperre. Negative echte Konten erfordern tatsächliche Erlöse: Der Plan nennt eine konkrete Verkaufsalternative und die bestehende Anpfiff-Notbremse, öffnet aber keinen neuen Verkaufspfad unter den Kadersicherungen.

## Modelle und Datenwahrheit

- `intel.forecasting`: Einsatzbedingte Punkte-/Minutenprognosen; Ridge-Training mit zeitlichen Spieltaggruppen und getrenntem letzten Test. Nachgeladene Daten ohne früheren Veröffentlichungsnachweis bleiben rückblickende Forschung.
- `intel.forecasting.prospective`: Frühere unveränderte Wochenexporte liefern die damals vorhandenen Merkmale, spätere abgeschlossene Werte die Ergebnislabels. Auch die Trainingsbasis muss vor der ersten Vergleichsperiode verfügbar gewesen sein. Spätere Korrekturen überschreiben keine alten Merkmale. Ohne genügend solcher Gruppen bleibt ein Kandidat unqualifiziert.
- `worker.forecast_training_job`: Mittwochs 05:10 Uhr Berlin plus bis zu zwei Minuten Jitter; speichert datierte Seeds, Kandidaten und `model_evaluation`-Berichte. Aktiviert selbst kein Modell.
- `worker.competition_training_job`: Mittwochs 05:30 Uhr Berlin plus bis zu zwei Minuten Jitter; trainiert getrennte Kandidaten aus bestätigten eigenen Auktions-/Managerergebnissen. Leere oder unzureichende Bestände erzeugen einen ehrlichen Bericht. Keine automatische Modellaktivierung.
- `trading.competition`: Getrennte Auktionsarten, eingeschränkte Ergebnisinformation korrekt als obere/untere Schranke oder beobachteter Konkurrenzpreis. Eigene verschwundene Gebote gelten ohne passenden Beleg nicht automatisch als verloren. Herkunft, Betrag, Marktwert zur Entscheidung und tatsächliche Ergebniszeit werden geprüft.
- `kba forecast` und `kba competition`: Training, Prüfung beziehungsweise Status, explizite qualifizierte Freigabe und Rollback. Aktive Konkurrenzartefakte sind an unveränderte Kandidaten gebunden. Kein aktueller Forschungskandidat wurde aktiviert.
- `kba market-forecast`: Tagesänderungen für 1/3/7 Tage trainieren und gegen unveränderten Preis und letzten Tagestrend prüfen. Das getestete Ridge-Modell verliert diesen Vergleich; die Laufzeit zeigt die datierte Trendbasis mit unkalibrierter empirischer Bandbreite.

Fehlende API-Felder werden nicht länger zu vermeintlich beobachteten Null-Labels. `PlayerPerformance` erhält nullable Angaben zur Feldpräsenz und Erfassung; Migration `e62c391a5b08`. Teilantworten dürfen vorhandene vollständige Ergebniszeilen nicht zerstören. Ein weiterer realer Parserfehler betraf das Namensfeld `n` statt `ln` in Spieler-Details; sämtliche 34 unveränderten aufgezeichneten Antworten sind mit dem neuen Parser lesbar.

## Messungen aus echten Daten

Die vollständigen Artefakte liegen im lokalen Arbeitsverzeichnis `analysis/season-engine-2026-09-13/`.

| Prüfung | Ergebnis |
|---|---|
| Punkte-Datensatz | 47.089 Rohzeilen, 24.686 verwendbare Einsätze, 363 Spieler |
| Punkte im letzten historischen Test | MAE 50,23 gegenüber Basis 52,27; ungefähr 3,9 % weniger Fehler |
| Minuten im selben Test | MAE 19,84 gegenüber 19,79; schlechter, Kandidat bleibt inaktiv |
| Marktwert-Tageswerte | 34.000 Werte, 30.792 Beispiele |
| Marktwert-MAE nach 1/3/7 Tagen | Ridge 189.279 / 620.511 / 1.672.928 €; Trend 52.512 / 242.649 / 822.537 € |
| Konkurrenzmodell | 95 belegte Auktionen; keine ausreichende prospektive Eigenkalibrierung, daher inaktiv |
| Aufgezeichneter Liga-Replay | 8 Besitzer, 3.120.437 € Konto, 8.074.347 € offenes Gebot, 4.953.910 € Unterdeckung |

Das Potulski-Gebot stammt laut ausgeführter Auditzeile 189 vom 13.09., 00:05 Uhr Berlin aus `intent-bid-manual`. Die Alternative zur Aufgabe wird sichtbar; es wird nicht als automatisch stornierbares Agentengebot behandelt. Der Replay verwendet reale aufgezeichnete Kickbase-Daten und zusätzlich deren Audit, keine echten Aktionen. Nicht aufgezeichnete LigaInsider-/Community-Signale sind im Replay ausdrücklich nicht enthalten.

## Grenzen und Freigabe

Keine trainierte Start-/Einsatzwahrscheinlichkeit ohne zuverlässige Start- und Nichteinsatzlabels. Vier Spieltage bedeuten zunächst viermal die aktuelle Formprognose; Gegner- und Belastungsmodelle sind noch nicht gelernt. Gewinnerpreise sind keine Mindestgebote, fremde Konten und unterlegene Gebote keine bekannten Fakten. Die Trainingsverbesserung beweist weder zusätzliche Saisonpunkte noch einen Platz im oberen Drittel.

Die Migration ist lokal geprüft. Emils anschließende Live-Freigabe umfasst den zuvor konkret gezeigten Patch für den geschützten Interlock-Test und dessen Neuversiegelung. Der Patch wurde angewendet; die eigentlichen Flag-/Präfix-Assertions bleiben erhalten. Alle neun Sicherungsdateien sind gültig versiegelt. `.env` und geschützte Spieler-IDs bleiben unverändert. Vor dem Deploy wurden der tatsächliche Produktionsquellstand mit dem Ausgangscommit verglichen und Datenbank, Quellstand sowie Containerimages für eine Rückkehr gesichert.

## Abschließende Verifikation

Nach der freigegebenen Testkorrektur, Neuversiegelung, dem HTTP-/MCP-Anschluss und der zeitlichen Begrenzung der Bankspieler-Ausnahme: **2.373 Tests der vollständigen Original-Suite bestanden**, drei bestehende Deprecation-Warnungen, 48,22 Sekunden. Unabhängige Integrationsprüfungen meldeten keinen kritischen Restbefund. Die vollständige abschließende Release-Suite steht in `pytest-release.log`.

```bash
uv run --python 3.12 --extra dev pytest -q
uv run --python 3.12 --extra dev ruff check src/ tests/
git diff --check
uv run --python 3.12 kba guardrails-check --no-suite --json
```

Ruff und Diffprüfung sind sauber. Die Integritätsprüfung bestätigt neun korrekt versiegelte Sicherungsdateien; der vollständige Pytest-Lauf ist separat belegt. Mypy prüft die 19 neuen beziehungsweise isolierten Kern-/ML-Quelldateien erfolgreich mit `--ignore-missing-imports` (SciPy-Typstubs sind nicht installiert); der zusätzliche Konkurrenz-Wochenjob und die API-Route sind separat geprüft. Die Migration wurde in Test-SQLite angewendet und geprüft. Der Liga-Replay enthält die konkrete manuelle Gebotsalternative; er führt keine Aktionen aus.

Belege im lokalen Arbeitsverzeichnis: `pytest-approved-predeploy.log`, `pytest-release.log`, `deploy-preflight.json`, `deploy-backup.json`, `deploy-release.json`, `deploy-verification.json`, `deploy-exercise.json`, `live-replay-plan.json`, `live-bid-audit.json`, `live-details-parser-check.json`, `forecast-training-report.json`, `forecast-frozen-evaluation.json`, `mv-report-mv-ridge-22346a13536ec980.json` und `sealed-test-update.patch`. Ältere Testprotokolle bleiben als Fehlerverlauf erhalten und sind kein aktueller Abnahmestand.
