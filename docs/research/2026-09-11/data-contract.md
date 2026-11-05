# Datenvertrag für die vertiefte Kickbase-Ansicht

Stand 11.09.2026. Dieser Vertrag trennt den reproduzierbaren Analyse-Export vom
aktuellen Runtime-Anschluss. Der Export ist **kein Live-Zustand**. Die neue reine
Runtime-Funktion ist `kickbase_agent.intel.strategy.build_strategy_insights`;
ihre Ausgabe wird als `d['strategy']` an die Darstellung übergeben.

## Belegbare Ansichten

| Ansicht | Daten und Rechnung | Aussagegrenze |
|---|---|---|
| Marktwertkurve je Spieler | Datierter Wert aus `PlayerHistory`; direkte API-Historie gewinnt bei gleicher Spieler-/Datum-Kombination | Keine Verbindung über unbekannte Werte zeichnen; Tageslabel ist kein Aktualisierungszeitpunkt |
| Punkte und Minuten je Saison | `PlayerPerformance`: Spieler, Saison, Spieltag, Punkte, Minuten, Spieldatum; echte 0-Minuten-Zeilen erhalten | Fehlende Zeile bleibt unbekannt; keine erfundenen Nullpunkte, keine Vermischung der Saisons |
| Punkteverteilung | Anzahl beobachteter Spieltage, Mittel, Quartile und Klassen `<0`, `0`, `1–49`, `50–99`, `100–199`, `≥200` | Beschreibende Verteilung; keine erwarteten Punkte oder garantierte Schwankungsbreite |
| Tatsächliche Aufschläge | Feed `t=15`, Kaufpreis `trp`, damaliger Tages-MW; `(Preis / MW − 1) × 100` | Gewinnerstichprobe; keine fremden verlorenen Gebote, kein minimal notwendiges Gebot |
| Preisgruppen und Manager | Nur bestätigte Käufe; Median, Quartile, n beobachtet/n verwendbar | Managername im Feed kann umbenannt worden sein; keine Aussage über Absichten |
| Vollständige eigene Trades | Chronologisch bestätigter Kauf → bestätigter Verkauf desselben Spielers | Verkäufe ohne bekannten Einstand bleiben separat; nie saisonale Gesamtrendite behaupten |
| Buchgewinn der gehaltenen Spieler | Heutiger Kader-MW minus im Feed nachgewiesener Einstand | Nur gehaltene Spieler mit bekannter Kostenbasis; kein Kauf aus Audit-`won` ableiten |
| Finanzierung | Konto minus sämtliche offenen Gebote, Annahme aller Zuschläge | Listings sind keine Erlöse; Kredit-/Kaufkraft ist kein Bargeld; negative Werte erhalten |
| Aufstellung | Erfolgreich gelesener `get_lineup()`: eindeutige `lo`-Slots 0–10 | Gespeicherte Elf ist keine reale Fußball-Startelf und kein historischer Deadline-Beleg |
| Konzentration | Anteil des teuersten und der drei teuersten Spieler am aktuellen Kaderwert | Beschreibt Kapitalbindung; keine Erfolgsursache. Geschützter Anteil nur bei bekannten Schutzflags |
| Liga-Punkte | `ranking.points` als ausdrücklich laufender Tabellenstand | Dashboard `tp` kann während eines Spieltags nachziehen. `completed_points` bleibt ohne datierten Abschlussbeleg unbekannt; `ph` hat keine eindeutigen Saison-/Spieltagslabels |
| News und Verfügbarkeit | Bestehendes RSS-/Scout-System mit Quellenlink, Veröffentlichungs-/Abrufzeit, Status und Fehlern | Export enthält keine echten Artikel/Scout-Snapshots: unbekannt, nicht „keine News/Ausfälle“ |

## Historischer Export und Nachrechnung

`python3 analysis/strategy-v2/derive_insights.py` liest ausschließlich die Dateien
unter `analysis/strategy-2026-09-11`. Es schreibt `derived-insights.json` ohne Netz,
Datenbankzugriff, Zufall oder aktuelle Systemzeit. Die Hashes sämtlicher Eingaben
und des Skripts stehen in der Ausgabe. Derselbe Input erzeugt denselben Output.
`--source-dir` und `--output` sind optionale Pfadparameter.

Der Export enthält 155 Spieler aus eigenem Kader, aktuellem Markt und den zwölf
Managerkadern; 12.033 datierte Kurvenpunkte, 428 bestätigte Ligatransfers und
getrennte Leistungsdaten für 2025/26 und 2026/27. In der Produktions-DB liegen
34.815 Leistungszeilen insgesamt, für die laufende Saison aber nur 353 Zeilen:
233 für Spieltag 1 und 120 für Spieltag 2. Zwei Spieltage sind laut Fixture-Export
vollständig abgeschlossen. Diese ungleiche Abdeckung ist ein Beobachtungsproblem,
kein Nachweis für Nichteinsatz aller fehlenden Spieler.

Die reproduzierte historische Lage: 1.023 abgeschlossene Managerpunkte, Platz 12,
718 Punkte Abstand zum damaligen Platz 4; zusätzlich −248 Punkte im laufenden
Spieltag, daher 775 im Live-Ranking. Der teuerste gehaltene Spieler bindet
48,534076 % des damaligen Kaderwerts. Das sind Beobachtungen vom 11.09., keine
Fortschreibung des aktuellen Kontos oder künftige Erfolgsaussage.

### Marktwertqualität

Die direkte Historie umfasst 51 Spieler × 92 Tageswerte. 2.745 von 2.770 gemeinsam
beobachteten Spieler-/Tageswerten stimmen mit der DB überein. Von 25 Abweichungen
betreffen 23 den ersten DB-Snapshot vom 20.08.; 22 davon entsprechen dem API-Wert
des Vortags. Zwei weitere Werte betreffen Füllkrug am 17./18.08.; die Ursache der
historischen Quellenabweichung ist unbekannt. Für Aebischer am 20.08. lässt sich
die Vortagszuordnung ebenfalls nicht bestätigen.

Der Analyse-Export verwendet deshalb direkte API-Historie bevorzugt und markiert
am bekannten problematischen Seed-Tag übrige unbestätigte DB-Werte als unbekannt.
Der generische Runtime-Builder hardcodiert keinen historischen Sondertag: Der
Collector kann dafür `quality='unknown'` und `unknown_reason` setzen. Historische
Korrekturen müssen als Quellenkorrektur erkennbar bleiben.

Die API liefert ein Kalenderdatum um Mitternacht UTC; der gegenwärtige Parser
stellt es im Berliner Sommer als naive 02:00 dar. Das ist **nicht** die reale
Aktualisierungszeit. Verkäufe passen zur Zuordnung „vor 22 Uhr Berlin Vortag,
danach gleicher Tag“: 32/32 direkt vergleichbare Verkäufe, darunter alle 19 eigenen.
Aufschlagsstatistiken schließen 21:45–22:30 aus; betreffende Transfers bleiben
sichtbar. Bei sequenziellen Abrufen 22:01–22:06 kamen verschiedene jüngste
Tageslabels zurück. Keine atomare Momentaufnahme behaupten.

## Verbindliche Runtime-Eingaben

Die Funktion ist rein und ändert keine Eingabe. Alle Zeitwerte sind ISO-8601-Text
oder `datetime`; naive DB-Zeitwerte werden als UTC behandelt. `generated_at` ist
der tatsächliche Collector-Zeitpunkt. IDs bleiben Strings; Eurobeträge sind Integer.
`None` bedeutet nicht verfügbar, `[]` erfolgreich leer. Fehler nicht vorher in
leere Listen umwandeln.

| Parameter | Format |
|---|---|
| `feed_events` | Rohe Feed-Dicts mit `i`, `t`, `dt`, `data:{pi,pn,trp,byr?,slr?}`. Nur `t=15` zählt, Deduplikation nach `i` |
| `history_rows` | `{player_id, recorded_at, market_value, id?, source?, captured_at?, quality?, unknown_reason?}`. `source='api_history'` hat Vorrang; nichtpositive Werte sind unbekannt |
| `performance_rows` | `{player_id, season, matchday, points, minutes, match_date, id?, source?, captured_at?}`; direkte DB-Zeilen inklusive null Minuten. `api_performance` gewinnt unabhängig von Input-Reihenfolge. Zukunftszeilen werden ausgeschlossen; heutige Spieldaten vorläufig markiert |
| `squad_rows` | Cockpitzeilen `{pid,name,mv,position,kb_prob?,status?,protected?}`. `buy_price` und `elf` dienen nicht als Kauf-/Istaufstellungsbeweis |
| `market_rows` | `{pid,name,mv,position?,pos?,kb_prob?,status?}`; Listings beweisen keine rechtzeitige Erwerbbarkeit |
| `manager_rows` | `read_manager_squads`-Zeilen `{user_id,name,rank,spieler:[{pid,name,mv,position,...}],completed_points?,matchday_history?,punkte_saison?}` |
| `budget` | Tatsächlicher Kontostand in Euro oder `None` |
| `open_bids` | Liste mit `amount` je tatsächlichem offenen Gebot oder `None` bei Abrufausfall |
| `saved_lineup` | Rohe erfolgreiche Antwort `{'it': [{'i': ..., 'lo': 0}, ...]}` oder `None` bei Ausfall |
| `generated_at`, `user_id`, `user_name` | Collector-Zeitpunkt, eigene ID und Name. Name wird für Feedzuordnung getrimmt |

Der Collector muss `protected` aus der tatsächlichen Konfiguration ergänzen;
der reine Builder liest keine Settings. Fehlende Marktposition darf als unbekannt
bleiben. Der vollständige Feed und Historie für auch inzwischen verkaufte Spieler
sind erforderlich, wenn deren Aufschläge und P&L angezeigt werden sollen.

## Runtime-Ausgabe `d['strategy']`

- `version`, `generated_at`, `status`: Version 1, ISO-Zeit und `ok|partial`.
- `summary`: `headline`, `detail`, `complete_round_trips`, `realized_pnl_eur`,
  `unrealized_pnl_eur`, `unknown_cost_basis_sales`, `confirmed_purchases`,
  `confirmed_sales`, `holdings_with_cost_basis`, `transfer_count`,
  `median_premium_pct`, `median_premium_scope`. Eigene Handelsbilanz; der
  Übersicht-Median bezieht sich auf Ligakäufe von Kickbase über 500.000 € MW,
  ohne Updatefenster und ohne Managerdeals. Fehlender Feed macht Zählwerte unbekannt.
- `trades`: `feed_id`, `player_id`, `name`, `manager`, `buyer`, `seller`, `at`,
  `amount`, `market_value`, `premium_pct`, `position`, `bucket`, `kind`,
  `update_boundary`, `mw_day`, `mw_source`, `mw_unknown_reason`.
- `manager_stats`: `user_id`, `name`, `purchases`, `sales`, `completed_points`,
  `matchday_history`, `live_season_points`, `rank`, `squad_value`, `squad_size`,
  `top1_share_pct`, `top3_share_pct`, `budget_eur` und Aufschlagsstatistik.
  Fremdes Konto ist immer `null`. Aufschlag je Manager nur aus Kickbase-Käufen.
- `price_buckets`, `purchase_premiums`: `n`, `n_priced`, `n_boundary_excluded`,
  `n_missing_mw`, `median_premium_pct`, `q25_premium_pct`, `q75_premium_pct`,
  `spent_eur`, `premium_eur`; Gruppen zusätzlich `label`.
- `round_trips`: Spielername/ID, beide Feed-IDs, `bought_at`, `sold_at`,
  `buy_amount`, `sell_amount`, `pnl_eur`, `holding_days`.
- `portfolio`: `budget_eur`, `open_bid_exposure_eur`, `cash_after_all_bids_eur`,
  `team_value_eur`, `net_assets_eur`, `occupied_slots`, `empty_slots`,
  `lineup_status`, `squad_count`, `top1_share_pct`, `top3_share_pct`,
  `protected_share_pct`, `waterfall`, `holdings`. Waterfall ist Konto →
  offene Gebote → Konto bei allen Zuschlägen; keine Wunschverkäufe.
- `players`: Spieler-ID/-Name, Besitz, heutiger MW, Position, KB-/Scoutstatus,
  `history:[{date,value,raw_value,source,source_row_id,quality,unknown_reason,captured_at}]`,
  `performance:[{season,matchday,points,minutes,date,source,source_row_id,captured_at,provisional}]`,
  `season_stats` und `coverage`.
- `season_stats`: Saison, `n_rows`, `n_appearances`, `avg_points`, `avg_minutes`,
  `zero_minute_rows`, `q25_points`, `q75_points`, `points_distribution`,
  `small_sample`, `observed_matchdays`, `has_provisional_rows`.
- `insights`: rein berechnete Hinweise `{severity,title,detail,metric,source}`;
  keine erfundenen erwarteten Punkte. `coverage` und `limitations` bleiben sichtbar.

Quartile verwenden lineare Interpolation auf sortierten Beobachtungen
(`index=(n−1)×q`). n=0 liefert `null`; n=1 liefert den einzigen Wert und darf nicht
als belastbare Verteilung vermarktet werden. Mittelwerte verwenden beobachtete
Zeilen inklusive echter 0-Minuten-Zeilen; eine Einsatzauswertung muss den separaten
Nenner `n_appearances` beachten. `small_sample` kennzeichnet weniger als fünf Zeilen.

## Bestehende Runtime-Anschlüsse und nötige Grenzen

Die folgenden acht relevanten Dateien stimmen zwischen lokalem Repository und
dem aktuellen gesicherten Produktionsstand `/tmp/kba-strategy-v2-live/src` überein:
`kickbase/client.py`, `kickbase/models.py`, `jobs/snapshot.py`, `intel/history.py`,
`news/kicker_rss.py`, `server/routes/news.py`,
`scouts/ligainsider/scraper.py`, `scouts/ligainsider/service.py`.

- `KickbaseClient.get_player_mv_history`, `get_player_performance`,
  `get_manager_squad`, `get_manager_dashboard`, `get_lineup`, `get_activity_feed`
  sind bestehende lesende Anschlüsse. Authentifizierung über bestehende Factory;
  vorhandenen Rate-Limiter erhalten. Kein eigener Client pro Chartpunkt.
- `jobs/snapshot.py` importiert Performance für beobachtete Kader-/Marktspieler.
  Es ist kein Vollscan aller Ligaspieler. Historien werden beim ersten Kennenlernen
  nachgeladen; alle bekannten DB-Spieler sind deshalb nicht automatisch frisch.
- `intel.history.load_performance` filtert `minutes > 0` und nur zwei Saisons.
  Für Einsatzquoten, Nullspiele und vollständige Minutenverteilungen direkte
  DB-Zeilen verwenden. `PlayerHistory.points/is_starter` sind neutrale Platzhalter
  und dürfen keine Punkt- oder Startelfcharts speisen.
- `RankingEntry` verwirft `mdp`. Der Collector darf abgeschlossene Punkte nicht
  aus dem Dashboard ableiten; `teamwert` aus Dashboard/Kadersumme wird von zeitlich
  abweichendem Ranking-`tv` getrennt. Im Export stimmt die Kadersumme bei allen
  zwölf Managern mit Dashboard-`tv` überein.
- `GET /api/news` liest aktuell direkt Kicker-RSS; `worker/newsfeed.py` persistiert
  Artikel in `NewsItem`. Für die vertiefte Ansicht vorhandene Artikel mit Quelle,
  Link und Zeit lesen. `NewsItem` hat keinen Spieler-ID-Link; eine Namenszuordnung
  muss als Zuordnung kenntlich sein. Generierte Worker-Schlussfolgerungen sind
  keine journalistischen Meldungen und dürfen nicht als Quelle ausgegeben werden.
- `KickerRSSClient._parse_date` ersetzt fehlendes Veröffentlichungsdatum durch
  `datetime.now()`. Für strenge Newsfrische braucht der Collector zusätzlich die
  Information, ob das RSS-Datum tatsächlich vorhanden war; sonst Zeitstatus
  unbekannt. Ein Abrufzeitpunkt darf kein Publikationszeitpunkt werden.
- LigaInsider liefert `ScoutSnapshot` mit `scraped_tids`, `degraded`, Startelf und
  Ausfällen. `fetched_at` ist **monotone Prozesszeit**, kein Unix-Zeitstempel.
  Der Collector muss eine eigene UTC-Abrufzeit ergänzen. Ausfall-/Blockseiten
  bedeuten unbekannte Verfügbarkeit. Kein Veto ist kein Fit-Nachweis.
- `Availability.confidence` ist Namenszuordnungsqualität, keine Wahrscheinlichkeit
  eines realen Einsatzes. KB-`prob` ist eine diskrete Stufe, kein Prozentwert.

Der Runtime-Code und die Tests sind neue Dateien. Der Collector-/UI-Anschluss
wird separat integriert; diese Auswertung schreibt weder Datenbank noch Kickbase,
verändert keine Parameter und löst keine Deployments aus.

## Verifikation

`api/tests/intel/test_strategy.py` enthält eine unveränderte echte Teilmenge der
vorhandenen Exportantworten. Fünfzehn Regressionstests prüfen die unabhängige
Handelsbilanz (12 Käufe, 19 Verkäufe, sechs vollständige Paare, −8.880.870 €),
Duplikate, API-/DB-Vorrang, Updatefenster, unbekannte Einstandspreise trotz Audit-
`won`, Nullminuten, fehlende Spieltage, Zukunftszeilen, echte gespeicherte Slots,
unbekannte Quellen, fehlenden Feed, doppelte Slots, API-Performance-Vorrang in
beiden Reihenfolgen, unveränderte Eingaben und JSON-Ausgabe. Gezielte Suite und
Ruff sind grün.
