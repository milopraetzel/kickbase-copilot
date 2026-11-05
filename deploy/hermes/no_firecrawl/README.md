# Hermes-Recherche ohne Firecrawl

Profil-Plugin für Hermes `fc8d15d7797c36cdc78ca9cd4a07187c3cc61c91`.
Keine Änderung am Hermes-Core oder an Kickbases Kaufengine.

- Suche: vorhandenes Hermes-DDGS-Backend.
- Artikel: `plugin/web_direct_http`, direkter HTTP-Abruf mit lokaler Trafilatura.
- Konfiguration: `profile-web.patch.json` semantisch in das Kickbase-Profil mergen.
  Bestehende andere Konfiguration sowie Pluginlisten erhalten; Einträge hinzufügen.
- Abhängigkeiten: `requirements.lock`, Versionen und Hashes festgelegt.
- Betriebsanweisung: `research-note.md` ergänzt die bereits vom Scout gelesene
  Referenz `starter-growth-learning-loop.md`.

## Installation und Prüfung

Zuerst in einem separaten Verzeichnis auf dem Zielsystem installieren. Den
gesamten `plugin/web_direct_http`-Quellordner nach
`<kickbase-profile>/plugins/web/direct_http` übernehmen. Darin installieren:

```sh
<hermes-python> -m pip install --target <pluginpfad>/_vendor --require-hashes -r requirements.lock
```

Einen `_vendor`-Ordner niemals zwischen Betriebssystemen kopieren. Der Bootstrap
macht ihn im Kickbase-Prozess und dessen DDGS-Unterprozess verfügbar. Dateien der
gemeinsamen Pythonumgebung werden nicht ersetzt. `PYTHONPATH` wird nur im laufenden
Prozess ergänzt; keine `.env`-Datei ändern.

```sh
HERMES_SOURCE_DIR=<hermes-repo> <test-python> -m pytest -q tests/test_no_firecrawl.py
<hermes-python> smoke.py <hermes-repo>
```

Die lokalen Tests prüfen Textauswahl, HTTP-Fehler, Größen-/Formatgrenzen,
Redirects, Website-Policy sowie echte Hermes-Pluginregistrierung und Fehler ohne
Anbieterwechsel. Der Smoke-Test verwendet ein temporäres Profil und öffentliche
Quellen; er braucht keine Modellcredits und führt keine Kickbase-Aktion aus.
Seine Ausgabe muss auf erfolgreiche Suche **und** sinnvollen Artikeltext geprüft
werden. Ein Prozess-Exitcode allein beweist keinen erfolgreichen Quellenabruf.

Vor dem Profilwechsel Konfiguration und gelesene Scout-Referenz sichern, ihre
Prüfsummen gegen den gelesenen Ausgangsstand vergleichen und auf ein Leerlaufen
der Profiljobs warten. Nur den Kickbase-Hermes-Gateway neu starten. Danach
Backendnamen, tatsächlich geladene Provider, verfügbare Abhängigkeiten, beide
deaktivierten Fallback-Flags und deaktivierte Firecrawl-Plugins erneut prüfen.
Das Deploy-Sperrfenster aus `AGENTS.md` gilt.

## Brotli-Regression vom 14.09.2026

HTTPX 0.28.1 mit Hermes' `brotlicffi==1.2.0.1` scheitert an bestimmten
gestreamten Brotli-Antworten mit `can_accept_more_data() is False`. Der lokale
Regressionstest nutzt den tatsächlich am 14.09. gespeicherten komprimierten
Bundesliga-Abruf in `tests/fixtures/`; URL, Prüfsumme und Transportblockgrößen
stehen in der JSON-Begleitdatei. Er durchläuft den echten HTTPX-Decoder und
scheiterte vor dem Fix mit genau diesem Fehler. Die Spieltagsseite ist ein
Transportbeleg, kein Nachrichten- oder Spielerrollenbeleg.

Das Plugin handelt jetzt ausdrücklich `gzip, deflate` aus. Andere vom Server
gelieferte Kodierungen werden vor der Dekodierung als Quellenfehler gemeldet;
es gibt keinen Anbieterwechsel oder erneuten Firecrawl-Versuch. Bestehende
SSRF-/Redirect-/Website-Prüfungen und das Limit auf dekodierte Bytes gelten weiter.
Die gemeinsame Hermes-Installation und deren Abhängigkeiten werden nicht verändert.

Der Decoder-Regressionstest benötigt `brotlicffi`. Ohne dieses optionale Paket
wird nur dieser Test übersprungen. Für die vollständige Reproduktion in einer
**separaten lokalen Testumgebung** die betroffene Version `brotlicffi==1.2.0.1`
installieren und die oben genannte pytest-Zeile ausführen. Mit HTTPX 0.28.1,
brotlicffi 1.2.0.1, Trafilatura 2.2.0 und DDGS 9.16.0 bestanden am 14.09. alle
17 Tests ohne übersprungene Fälle. Ein Testlauf ohne Brotli-Unterstützung allein
belegt diesen Fix nicht.

DDGS läuft unabhängig von diesem Artikel-Client im eigenen Unterprozess. Dessen
`No results found`, Timeouts und Abrufsperren werden durch die Änderung nicht
behoben. Das erneute Laden öffentlich lesbarer Artikel ist auch kein Nachweis
für erfolgreiches Modelltraining oder eine neue Kaufentscheidung.

## Aussagegrenzen

Die Konfiguration unterbindet Firecrawl in den normalen Web-/Browserwerkzeugen
dieses Profils. Ein anderer, falsch geladener oder unlesbarer Profilpfad ist kein
solcher Nachweis: Hermes besitzt im Core weiterhin automatische Anbieterwahl.
Die Betriebsanweisung verbietet auch manuelle Firecrawl-Ausweichaufrufe.

HTTP 403, leere Texte und ungeeignete Formate werden als Quellenfehler gemeldet.
Öffentliche Teaser sind nicht automatisch vollständige Artikel. Ein extrahiertes
Datum ist noch kein unabhängig geprüftes Publikationsdatum. Weder technisch
lesbarer Text noch Suchtreffer bestätigen eine Spielerrolle oder begründen ein
Gebot. Die Quellenkopplung an ein neues Punkte-/Minutenmodell ist ein eigener
Ausbau, der durch dieses Plugin nicht aktiviert wird.
