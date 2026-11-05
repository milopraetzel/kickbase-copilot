# Echte Marktwert-Historie (Snapshot-Befund 01.10.2026)

`scheller-7320-mv-history-92.json`: unveränderte Antwort von
`GET /v4/leagues/{liga}/players/7320/marketvalue/92` (Jan-Luca Scheller, Paderborn), gelesen am
01.10.2026 im Box-Container `kickbase-agent`, rein lesend — zuerst gegen 13:30 Berlin über
`KickbaseClient._get`, gegen 15:00 mit `kba probe mv --league 1234567 --player 7320 --timeframe 92`
(dem dokumentierten Fixture-Weg) erneut abgerufen: identisch. `dt` = Tage seit 1970
(20726 = 30.09.2026), `mv` = Marktwert nach dem 22-Uhr-Update dieses Tages. Keine erfundenen
Felder, keine Secrets (nur Marktwerte).

Zugehöriger Befund (L-118, Fix L-120): Die DB der Box führte für 7320 am 30.09. den Vortageswert
5.479.033 € (Snapshot 22:05 vor dem Update), Kickbase 5.580.536 € (`tfhmvt` am 01.10.
+101.503 €). Am 30.09. waren 9 von 16 geprüften Zeilen so veraltet, vom 10. bis 29.09. keine von
23 Spielern.
