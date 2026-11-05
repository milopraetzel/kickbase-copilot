# Laufende Nachrichten: geprüfte Zugänge und Grenzen

Die am 11. September 2026 geprüften Feeds sind ohne Login abrufbar. Der Collector nutzt ausschließlich diese drei festgelegten URLs. Das [Abrufprotokoll](feed-probes.json) enthält HTTP-Status, MIME-Typ, tatsächliche XML-Felder, Zeitstempel und Beispielartikel.

| Quelle | Geprüfter Endpunkt | Tatsächliche Antwort | Entdeckung und Einordnung |
|---|---|---|---|
| kicker | [Bundesliga-RSS](https://newsfeed.kicker.de/news/bundesliga) | HTTP 200; `text/xml`; RSS 2.0; 20 Einträge | [Offizielle RSS-Erklärung](https://www.kicker.de/mit_rss_immer_informiert-371919/artikel) verweist auf die Feedübersicht; redaktionelle Sportquelle |
| Sportschau | [Bundesliga-RSS](https://www.sportschau.de/fussball/bundesliga/index~rss2.xml) | HTTP 200; `application/xml`; RSS 2.0; 46 Einträge | [Offizielle RSS-Hilfe](https://www.sportschau.de/service/rssfeeds-sp-102.html) beschreibt die Endpunkte; öffentlich-rechtliche Sportredaktion |
| GEISSBLOG | [Klub-RSS](https://geissblog.koeln/feed/) | HTTP 200; `application/rss+xml`; RSS 2.0; 10 Einträge | Als `rel=alternate` im HTML der Webseite ausgewiesen; Kölner Spezialredaktion, teils Kommentar/Einordnung |

Alle drei liefern `channel/item/title`, `link`, `guid`, `pubDate` und `description`. Die wiederkehrende Identität wird für die App über Spieler-ID plus Artikel-URL gebildet. `guid` bleibt im Prüfprotokoll erhalten; es ist kein Spieler-Identifier. Das Feed-Datum wird mit der angegebenen Zeitzone in UTC umgerechnet. Ein fehlendes oder unverständliches Datum bleibt `null`. Abrufdatum und Veröffentlichungsdatum bleiben getrennt.

Der im Repository neu angelegte Einstiegspunkt lautet:

```python
await fetch_strategy_news(players=[{"pid": "8329", "name": "Olise", "team": "Bayern"}],
                          cache_path=Path("strategy-news.json"))
```

Er liefert `articles`, `sources`, `errors`, `generated_at`, `cache_hit` und `cache_ttl_seconds`. Der Cache speichert die normalisierten Quelleninhalte und wird höchstens alle 20 Minuten erneuert; unterschiedliche Spielerlisten werden anschließend separat gefiltert. Damit braucht die App keine eigene Netzabfrage je Spielerklick. Die drei Abfragen laufen parallel, jeweils mit absoluter Begrenzung auf zwölf Sekunden. Jede Antwort wird auf eine Million Bytes und 250 Einträge begrenzt. HTML wird zu reinem Text; Script-/Style-Inhalte, unsichere Link-Schemata, DTD und XML-Entity-Deklarationen werden verworfen.

Namen werden ohne Groß-/Kleinschreibung und mit normalisierten Akzenten als ganze Wörter geprüft. Kurze oder häufig mehrdeutige Nachnamen wie Kim, Ache oder Müller reichen allein nicht. Für die acht eigenen Spieler sind die geprüften vollständigen Namen mit Kickbase-ID verknüpft. Doppelte Artikel-URLs werden je Spieler entfernt; maximal zwölf Artikel je Spieler bleiben sichtbar. Diese konservative Zuordnung verpasst bewusst Meldungen, deren Anreißer nur „der Kapitän“ sagt. Der vollständige Artikel wird durch den Collector nicht heimlich als neue Quelle geladen.

Aus einem RSS-Treffer entsteht ausschließlich `event_type=news_mention`. Der Collector setzt weder Verletzungsstatus noch Startelfchance, verändert keine Gebote und behauptet keine sportliche Wirkung. `expires_at` ist bei datierten Meldungen eine technische Prüffrist von 48 Stunden nach Veröffentlichung; sie ist keine vom Verlag bestätigte Gültigkeit. Ohne Veröffentlichungsdatum bleibt auch diese Frist unbekannt. Ein zukünftiger Ausbau sollte je Ereignis eine fachliche Frist verwenden.

Scheitert eine Quelle, bleiben ihre zuletzt erfolgreichen Einträge erhalten, aber `sources.status` wird `stale`; ohne solche Einträge lautet der Status `error`. Die App muss beide Fälle neben dem letzten erfolgreichen Abruf zeigen. Ein frischer Gesamtbuild macht eine alte Nachricht nicht frisch. ETag und Last-Modified werden dokumentiert; bedingte HTTP-Abrufe sind noch nicht implementiert, der 20-Minuten-Cache begrenzt bereits den Verkehr.

## Abdeckung nach echtem Abruf

Der Live-Abruf um 21:00 UTC las 76 Feed-Einträge fehlerfrei. Mit der Liste aus acht eigenen Spielern und drei Prüfkandidaten ergaben sich zu diesem Zeitpunkt **keine sicheren Namenstreffer** in den aktuellen Anreißern. Das ist eine Abdeckungsgrenze der kurzen Feeds, kein Grund, eine Nachricht zu erfinden. Mit allen 402 Spielern aus dem vorhandenen Liga-/Markt-Audit ergaben sich 20 Artikelzuordnungen. Diese Zahl beinhaltet auch ältere, bereits abgelaufene Artikel. Die Belege stehen in [rss-live-result.json](rss-live-result.json) und [rss-live-league-result.json](rss-live-league-result.json).

Die gesonderte Datei [player-news.json](player-news.json) enthält zwölf tatsächlich gelesene Artikel: alle acht eigenen Spieler sowie Prass, Poulsen und Dallinga. Sie enthält redaktionell eingeordnete Sachverhalte, Unsicherheit und individuelle Prüffristen. Bei Ullrich, Kim, Schwäbe, Vidović und der älteren Ebimbe-Meldung ist die Frist bereits abgelaufen; diese Einträge sind Hintergrund, keine aktuellen Einsatzzusagen. Als zwölfter Artikel ist die bestätigte Aufstellung für Union–Schalke am dritten Spieltag ergänzt: Dina Ebimbe begann auf der Bank. Dieser datierte Einzelbeleg ersetzt keine Prognose für den nächsten Spieltag. Neue Abfragen dürfen das Abrufdatum älterer Rechercheeinträge nicht künstlich erneuern. Die Quellen aus dem Hauptbericht bleiben in [sources.json](sources.json).

Der redaktionelle Mehrwert ist am stärksten, wenn eine allgemeine Nachricht eine konkrete Entscheidung berührt: Aches Konkurrenz durch den genesenen Dallinga, Olises belegte Belastungsrotation oder Prass' weiterhin fehlende Spielverfügbarkeit trotz Trainingsfortschritt. Dafür braucht es Artikelprüfung und Ereigniszuordnung über den automatischen RSS-Treffer hinaus. Die zwölf Einträge sind ein begrenzter Startbestand für diese Stufe.

## Betrieb und Pflegeaufwand

Bei 20 Minuten Cache-Laufzeit liegen höchstens 216 Abrufe am Tag über alle drei Feeds an, sofern die App entsprechend oft angefragt wird; beim stündlichen Build sind es etwa 72. Das sind Obergrenzen der vorgeschlagenen Nutzung, keine vom Anbieter zugesicherten Limits. Ein Collector ohne Aufruf plant keine eigene Hintergrundarbeit. Die root-seitige Integration in den vorhandenen Cockpit-Build übernimmt den wiederkehrenden Aufruf.

Für 18 Bundesliga-Klubs reichen ein Köln-Feed und zwei allgemeine Feeds nicht zur vollständigen Verletzungsabdeckung. Deshalb bleiben aktuelle LigaInsider-Meldungen, der PK-Kalender und Vereinsnachrichten die gezielte zweite Rechercheebene. Auf den zusätzlich geprüften LI-, HSV- und FC-Seiten wurde kein `rel=alternate`-RSS-Link gefunden; daraus folgt nicht, dass diese Anbieter grundsätzlich keine anderen Zugänge haben. Neue Endpunkte müssen erst mit echten Antworten geprüft werden. Ein kostenpflichtiger Vollfeed ist erst sinnvoll, wenn die fehlende Abdeckung und ein messbarer Entscheidungsnutzen dokumentiert sind.

Prüfung des neuen Codes: zehn gezielte Tests zu Datumsangaben, Cache, Identitätsgrenzen, abgelaufenen Quellen, HTML/Link-Sicherheit und Größenlimits. Zusammen mit den vorhandenen News-Tests waren 18 Tests erfolgreich; Ruff war für die beiden neuen Dateien sauber. Der echte Live-Abruf belegt zusätzlich die heutige Erreichbarkeit aller drei Quellen.
