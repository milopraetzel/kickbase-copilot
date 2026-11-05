> Überholter, nicht aktivierter Entwurf. Ersetzt durch [Mannschaftsengine](../../superpowers/specs/2026-09-13-season-engine.md) nach Emils ausdrücklicher Korrektur.

# Kuratierte Käufe – 13. September 2026

Status: lokal umgesetzt und mit Produktionsdaten lesend geprüft; noch nicht
ausgerollt. Ein versiegelter Alt-Test muss vor Freigabe bewusst angepasst werden.

## Entscheidung

Emils neuer Auftrag ersetzt die frühere Erwartung möglichst vieler Kaufvorhaben:
wenige gezielte Angebote, sportlich brauchbare und aktuell steigende Spieler,
begründete individuelle Preislimits. Ein voller Beobachtungszettel ist kein Grund
für ein Gebot. Diese Änderung betrifft automatische Käufe; ausdrückliche manuelle
Aufträge bleiben als solche erkennbar und zählen zu bestehenden Verpflichtungen.

1. **Wenige Ziele:** höchstens zwei gleichzeitig gebundene Kaufziele, darunter
   höchstens ein Angebot an einen Mitmanager. Vorhandene Gebote und ausstehende
   Kaufvorhaben zählen gemeinsam; doppelte Einträge reservieren je Spieler den
   höheren tatsächlich gebundenen Betrag. Das sind konservative Prozessgrenzen,
   keine empirisch ermittelte optimale Anzahl. Bereits platzierte Gebote werden
   durch eine Ablehnung im neuen Plan nicht automatisch storniert.
2. **Qualität vor Preis:** frische Startrollen-/Fitnessprüfung, kein Ausfall,
   gemessener positiver 24-Stunden-Marktwertschritt, Kauf- und Elfindex jeweils
   mindestens die bestehende Kaufschwelle von 0,53. Offene Positionen senken die
   Schwelle nicht. Der Elfindex enthält keinen Marktwert-/Tippbonus; er ist trotzdem
   eine Heuristik, keine erwartete Punktzahl. Bei Starterlücken haben deren
   Positionen Vorrang; bei vollständiger Elf muss ein Kauf die beste Elf verbessern.
   Gleichwertiger günstiger Ersatz darf nur als finanzierter Tausch Kapital lösen.
3. **Individuelles Limit:** maximal der kleinere Betrag aus regulärer technischer
   Aufschlagsgrenze und Marktwert der günstigsten aktuell geprüften Kickbase-Auktion
   gleicher Position mit mindestens gleichem Elfindex. Ohne solchen Vergleich gilt
   Marktwert als automatisches Limit. Ein Verkäuferwunschpreis ist kein Wertbeleg.
   Beispiel: Ziel 9 Mio. €, gleich gute Alternative 10 Mio. €, technische Grenze
   10 % → höchstens 9,9 Mio. €. Das garantiert keinen Zuschlag. Knappheit allein
   rechtfertigt keinen pauschalen Bedarfsaufschlag mehr.
4. **Gemeinsam finanzieren:** offene Verpflichtungen, andere ausgewählte Käufe,
   Vereins-/Positionsgrenzen und konkrete Tauscherlöse berücksichtigen. Eine
   Alternative bleibt ohne Gebot, wenn bereits ein gleichwertiges Ziel den Bedarf
   abdeckt. Das bestehende Liquiditäts-/Aufstellungsfristenmodell bleibt verbindlich.
   Ein wartender Tauschverkauf bleibt bei Neuplanung mit seinem Kauf verbunden,
   solange diese befristete Bindung gültig ist. Ein beliebiger anderer Neuzugang
   löst ihn nicht aus: Genau der vorgesehene Ersatz muss tatsächlich im Kader sein.
5. **Ausführung kontrollieren:** die Kaufprüfung mit Spieler, absolutem Euro-Limit,
   Begründung und Ablaufzeit begleitet Plan, Vorhaben, Gebotsanpassung und Executor.
   Sie gilt höchstens drei Stunden bzw. bis Auktionsende. Unmittelbar vor einem
   automatischen Kauf werden Rolle, Fitness und positiver Marktwertschritt erneut
   gelesen. Abgelaufene Prüfung, verschlechterter Zustand oder überschrittenes
   Limit verhindern die Ausführung. Nachgebotsempfehlungen benötigen ebenfalls
   eine gültige individuelle Prüfung.

## Belege und Grenzen

Im historischen Snapshot vom 12.09., 00:34 Berlin, ergeben sechs vollständig
belegte Kauf-/Verkaufspaare 8.880.870 € realisierten Verlust: 4.410.435 € gezahlter
Aufpreis und 4.470.435 € Marktwertverlust während der Haltedauer. Das ist eine
kleine rückblickende Stichprobe, kein Beleg, dass unser Kauf den Marktwert senkte.
Schmetgens kostete 20,17 % über dem damaligen Marktwert; die damalige Begründung
nannte unter anderem Ø 40 Punkte und einen positiven Tagestrend. Steigen allein
reichte erkennbar nicht als Kaufthese.

Die lesende Produktionsprüfung am 13.09., 00:56 Berlin, betrachtete 26 Listings.
Nur Maxime Estève bestand die Kaufprüfung; kein zusätzlicher Kauf war finanzierbar.
Das vorhandene Potulski-Gebot bleibt eine echte Verpflichtung. Potulski stieg frisch
um 311.717 € pro Tag und hatte Startelfnachweise, verfehlte aber die Indexschwelle.
Er wird deshalb nicht fälschlich als fallender Bankspieler beschrieben.

Pro Plan werden höchstens zwölf priorisierte Kandidaten frisch im Detail gelesen:
bestehende Verpflichtungen zuerst, dann benötigte Positionen und Elfindex. Andere
Listings gelten in diesem Lauf als ungeprüft, nicht als nachweislich schlecht.
Die Höchstgebote sind konservative Ausgabenlimits. Gegnerische verdeckte Gebote,
Startelfentscheidungen und künftige Marktwerte sind nicht sicher bekannt. Ein
kontrolliertes Gebotsmodell aus Liga-Zuschlägen oder eine über mehrere Spieltage
kalibrierte Punkteprognose ist damit noch nicht implementiert. Die bestehenden
News-/LigaInsider-Signale werden genutzt; dieser Patch baut keinen neuen Newsdienst.

## Prüfung und Nachkontrolle

Die Fehler wurden am echten `build_plan`-Aufruf mit Domainobjekten reproduziert:
zu schwacher Zusatzspieler, fallender Spieler und drittes paralleles Ziel wurden
vor der Änderung zugelassen; nach der Änderung abgelehnt. Erfolgsfälle prüfen,
dass ein passender steigender Spieler weiterhin ein ausführbares, begrenztes
Kaufvorhaben bekommt. Weitere Tests decken gemeinsame Budgets, Mitmanager-Angebote,
günstigere Alternativen, Limits bei Anpassungen, frische Ausführungsprüfungen und
den echten Detailantwort-Parser ab. Der Nachnamenfehler (`ln` statt `n`) wurde
an einer vorhandenen echten API-Fixture reproduziert und korrigiert.

Nach zwei abgeschlossenen Spieltagen auswerten: parallele Angebote, erfüllte
Starterpositionen, gezahlte Aufschläge, tatsächliche Minuten/Punkte der Neuzugänge,
Marktwert nach 1/3/7 Tagen, realisierte Ergebnisse und wegen der Filter verpasste
gute Kandidaten. Kaufpreisaufschlag und anschließenden Marktwertverlust getrennt
zeigen. Die Schwellen nicht allein wegen ausbleibender Käufe wieder absenken;
Fehlablehnungen und Datenlücken müssen konkret belegt werden.

Die vollständige Testsuite und die unveränderte Versiegelung sind Freigabekriterien.
Aktueller Gesamtlauf: **2.094 Tests bestanden, ein unveränderter versiegelter
Alt-Test fehlgeschlagen**, der noch einen automatischen Kauf ohne Kaufprüfung
erwartet. Ruff und Diffprüfung sind sauber; die Prüfsummen aller neun geschützten
Testdateien stimmen. Die zwei Mypy-Meldungen im Spielerparser sind auch im
unveränderten HEAD reproduzierbar; die neue Kuration hat keine Mypy-Meldungen.
Der vorbereitete separate Patch ergänzt im versiegelten Autotrading-Test die jetzt
erforderliche gültige Kaufprüfung und den frischen Spielerzustand; seine bisherige
Zusicherung zum Scharfschalten bleibt bestehen. Keine Neuversiegelung durch diesen
Arbeitsstand, kein Produktionsneustart und keine echten Trades.
