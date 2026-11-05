# Gebotspreis und Marktwertprognose — Analyse vom 25.09.2026

> Auftrag Emil (24./25.09.2026): statt eines festen Aufschlags schätzen, was ein Spieler
> wert sein wird; klären, wie Kickbase den Marktwert bildet, ob Anbieter ihn vorhersagen,
> ob wir an die Daten kommen; aus allen Liga-Transfers und unseren eigenen Geboten eine
> Regel ableiten. **Stand: Analyse, keine Engine-Änderung.** Vor jedem Einbau: Emils GO,
> Decision-Record, Tests, Review.

## 1. Datenbasis (rein lesend)

| Quelle | Umfang |
|---|---|
| Box-DB-Kopie `.local-backups/analyse-2026-09-25/kba.db` (24.09.2026 abends) | Marktwert-Tagesreihen (`player_history`) für rund 440 Spieler seit Juni, Leistungsdaten, `transfer_actions`, `engine_decision`-Berichte |
| Liga-Feed + Managerhistorien (Studie 19.09. + Live-Feed 24.09.) | 794 eindeutige Transfers seit Saisonreset 10.08.: 377 Käufe von Kickbase, 396 Verkäufe an Kickbase, 21 Managerdeals |
| Eigene Gebote (`transfer_actions`, `action_type='bid'`) | 32 ausgeführte Gebote auf Kickbase-Listings, 22 auf Manager-Listings |

Skripte (lokal, nicht versioniert): `.local-backups/analyse-2026-09-25/skripte/`
(`build.py` → `listings.py` → `prep.py` → `a_premium.py`, `b_ownbids.py`, `c_model.py`,
`c_qmodel.py`, `d_wincurve.py`, `d_backtest.py`, `d_robust.py`, `d_ownbt.py`, `d_frist.py`).
Zweiter unabhängiger Lauf am 25.09. reproduziert die Zahlen (Aufschläge und Backtest identisch
bis auf einen einzelnen Kauf, der durch eine nachgetragene Marktwertzeile die Trendgruppe wechselt).

**Konvention geprüft:** Ein Transfer um Berliner Zeit *t* gehört zum Marktwert des Tages
`(t − 22 h).date()` (Update gegen 22 Uhr). Belegt an den Verkäufen an Kickbase: 361 von 363
mit bekanntem Marktwert liefen exakt zum Tagesmarktwert (zwei Abweichungen unter 1 %).
Ein Verkauf an Kickbase realisiert also genau den Marktwert — der „Buchwert" unten ist
damit auch der Erlös bei sofortigem Weiterverkauf.

## 2. Wie Kickbase den Marktwert bildet

- Offizielle Hilfe: Der Marktwert spiegelt die Nachfrage in der **gesamten Kickbase-Community**
  (häufig gekauft → steigt; häufig verkauft oder gelistet → fällt), dazu Leistung/Form und
  Verletzungen/Nachrichten. Update im Saisonmodus täglich gegen 22:00 Uhr. Eine Formel wird
  nicht veröffentlicht. (help.kickbase.com, „Wie wird der Marktwert berechnet?")
- Unsere Liga (12 Manager) bewegt den Marktwert praktisch nicht; er folgt der Community.
- Anbieter mit Marktwertprognosen: Ligabase (Prognose + 14-Tage-Trends, offene Beta),
  BaseXI (Trend-Algorithmen auf Markthistorie), Kickbest, Kickly, KickbaseNerd. Keiner
  veröffentlicht Formel oder Trefferquote; alle arbeiten mit demselben öffentlichen Signal,
  der Marktwerthistorie — die haben wir selbst.

## 3. Befund A — der Marktwert ist kurzfristig gut vorhersagbar

Walk-forward (Training nur mit Vergangenheit, 21-Tage-Fenster), alle Spieler ≥ 1 M,
Testtage 24.08. bis 21.09.:

| Horizont | Fehler (MAE) Momentum-Modell | Fehler „bleibt gleich" | Rangkorrelation je Tag |
|---|---|---|---|
| 1 Tag | 1,1 Prozentpunkte | 3,7 | 0,93 |
| 3 Tage | 5,3 | 10,8 | 0,85 |
| 7 Tage | 16,0 | 22,5 | 0,69 |

Der wichtigste Einzelprädiktor ist der **letzte Tagesschritt** (Momentum): Wer gestern
gestiegen ist, steigt meist weiter. Leistungs- und Beschleunigungsmerkmale verbessern das
kaum. Über 3 Tage ist die Prognose brauchbar, über 7 Tage sehr unscharf. Das untere
Quartil ist leicht zu optimistisch (29–30 % der Fälle liegen darunter statt 25 %).

## 4. Befund B — die Liga zahlt den erwarteten Anstieg bereits mit

Siegerpreise aller 347 Liga-Käufe von Kickbase (ohne Update-Fenster):

| Gruppe | Median-Aufschlag auf MW | Anteil ≤ 5 % |
|---|---|---|
| alle | 9,5 % | 33 % |
| MW 5–10 M | 10,2 % | 25 % |
| MW 10–20 M | 6,8 % | 41 % |
| letzter Tagesschritt steigend (> +0,5 %) | 12,1 % | 20 % |
| letzter Tagesschritt fallend (< −0,5 %) | 4,1 % | 54 % |
| 3-Tage-Trend > +5 % | 16,8 % | 15 % |

Die Mitbieter sehen denselben Trend und bieten ihn ein. Ein Gebot zum Marktwert schlägt
**keinen einzigen** Siegerpreis (Kickbase lässt kein Gebot unter MW zu; wer genau MW bietet,
gewinnt nur ohne Konkurrenz).

## 5. Befund C — unsere eigenen Gebote

Seit 11.09. lagen 8 Gebote auf Kickbase-Listings exakt bei **MW + 0 %**: gewonnen 0.
Verloren: Arthur (Sieger +7,3 %), Aouchiche (+10,1 %), Engelhardt (+1,6 %), Regeer (+13,0 %);
zurückgezogen/abgelöst: Führich, Scally, Suzuki, Potulski (Sieger +3,3 % bis +6,9 %). Die drei
Gebote mit Aufschlag im selben Zeitraum: Vozar (+9,2 %) und Yamamoto (+7,0 %) gewonnen,
Seol (+2,5 %) verloren (Sieger +14,1 %). Bis 10.09. mit +2 bis +20 % gewannen wir 6 von 15
entschiedenen Kickbase-Auktionen.

## 6. Backtest der Gebotsregeln

Methode: Wir bieten als **zusätzlicher Bieter** in jeder Liga-Auktion seit 24.08. (236 Käufe
von Kickbase, MW > 500 k) und gewinnen, wenn unser Gebot über dem tatsächlichen Siegerpreis
liegt (verdecktes Höchstgebot, der Sieger zahlt sein Gebot). Alle Prognosen und die
Zuschlagskurve sind walk-forward geschätzt, ohne Zukunftswissen. „Buch 7T" = Marktwert nach
7 Tagen geteilt durch den gezahlten Preis minus 1 (= Erlös bei Verkauf an Kickbase).

| Regel | Zuschlagsquote | Ø Aufschlag | Buch 7T Median | Buch 7T Mittel | je Zuschlag |
|---|---|---|---|---|---|
| S0 MW + 0 % (Engine heute) | 0 % | — | — | — | — |
| S1 fest +5 % | 28 % | 5,0 % | −7,1 % | −2,1 % | — |
| S2 fest +10 % | 48 % | 10,0 % | −10,8 % | −5,8 % | — |
| S4 Studie 19.09. (×1,12 / ×1,10, Faller MW) | 39 % | 11,5 % | −5,8 % | −4,1 % | −0,80 M |
| S6 Emils Idee: 7 × letzter Tagesschritt (max. 30 %) | 48 % | 20,4 % | −5,7 % | −5,7 % | −0,74 M |
| S7 erwarteter 7-Tage-Anstieg (Median) | 47 % | 32,4 % | −1,8 % | −5,3 % | — |
| **V3 Elf: min(p_win80, 3-T-Prognose + 3 Pp), Deckel 15 %, Faller +0,5 %** | **36 %** | **8,0 %** | **−1,7 %** | **−3,2 %** | **−0,39 M** |
| F Frist: min(p_win80, 15 %), Faller +2 % | 52 % | 12,1 % | −6,1 % | −6,0 % | −0,89 M |
| S9 Handel: nur Steiger, min(p_win75, unteres Quartil 3-T-Prognose) | 22 % | 16,8 % | **+7,1 %** | +9,6 % | +0,29 M |

Teilmenge MW 5–20 M (typische Elf-Käufe, n = 136): V3 gewinnt 45 % bei −0,3 % Median
(S4: 53 % bei −5,8 %; F: 68 % bei −6,6 %).

`p_win80` = Aufschlag, der 80 % vergleichbarer Siegerpreise geschlagen hätte; walk-forward
kalibriert (Soll 80 %, Ist 76 %). Aktuelle Form: ln(1 + p) = 0,190 + 1,794 · r1 − 0,075 · ln(MW / 5 M),
r1 = letzter Tagesschritt (begrenzt auf −10 % … +20 %). 3-Tage-Median-Prognose im jüngsten
21-Tage-Fenster: g3 = −0,004 + 2,73 · r1.

Robustheit nach Teilzeiträumen (Buch 7T Median): V3 +0,4 % (24.08.–04.09.), −4,6 % (05.–15.09.);
S4 −5,8 % bzw. −4,6 %. 95-%-Konfidenzintervalle des Medians: V3 [−11,2; +2,0], S4 [−10,1; −2,0],
S9 [−0,8; +9,9]. **Der Vorsprung von V3 vor S4 beim Buchwert ist plausibel, aber statistisch
nicht gesichert.** S9 wurde nach Sicht auf die Daten aus mehreren Varianten gewählt, hat nur
29 Fälle und einen negativen Teilzeitraum (−9,8 %, n = 5) — **kein Beleg für Handelsgewinn.**

Auf 29 unserer eigenen Kickbase-Gebote (mit bekanntem Trend) hätte V3 9 gewonnen und
14 verloren (6 unklar), S4 12 gewonnen und 12 verloren (5 unklar).

## 7. Warum „20 % drüber, wenn er 20 % steigt" Geld verliert

1. Die 7-Tage-Prognose hat einen Fehler von rund 16 Prozentpunkten.
2. Wer den erwarteten Anstieg voll bietet, gewinnt genau die Auktionen, in denen er den
   Anstieg **überschätzt** hat (Fluch des Gewinners): Die anderen sehen denselben Trend.
3. Der Aufschlag ist sofort weg (Verkauf an Kickbase nur zum MW); der Anstieg ist unsicher.

Die Prognose taugt deshalb vor allem als **Deckel**: nie mehr zahlen, als der Spieler in
wenigen Tagen voraussichtlich wert ist — und für die Reihenfolge, wen man zuerst kauft.

## 8. Vorschlag

1. **Elf-Käufe (jetzt, Ziel volle Elf zu ST5):** Gebotsaufschlag nach V3 statt „kleinster
   sinnvoller Einstieg ≈ MW". Kosten im Mittel rund 0,4 M Buchwert je gewonnenem Spieler
   nach 7 Tagen — gegen 100 Punkte Abzug je leerem Elf-Platz und Spieltag. Die Obergrenzen
   aus Kaufbeleg, Finanzierung, 33-%-Regel und Guardrails bleiben unverändert.
2. **Offener Platz kurz vor der Frist (Option):** Variante F (Zuschlagsquote 52 %, teurer:
   −0,9 M je Zuschlag) nur, wenn ein Elf-Platz binnen 72 h vor der Frist noch leer ist.
3. **Handel auf Gewinn:** nicht automatisch. S9 als Schattenlauf (nur protokollieren, was
   die Regel geboten hätte) drei Wochen lang; im Steiger-Radar pro Kandidat das
   „sinnvolle Höchstgebot" anzeigen. Entscheidung danach mit echten Out-of-Sample-Daten.
4. **Datenlücke schließen:** Liga-Transfers aus dem Feed dauerhaft speichern (Grundlage der
   Zuschlagskurve, wöchentlich neu geschätzt, Aktivierung nur per Record) und Kickbase-Listings
   ohne Käufer protokollieren — die fehlen heute ganz, dort hätte ein MW-Gebot gewonnen.

## 9. Grenzen

- Sechs Wochen Daten, eine Liga; die Zuschlagskurve gilt für diese Mitbieter.
- Unverkaufte Listings fehlen im Backtest (nicht gespeichert); Zuschlagsquoten sind deshalb
  eher vorsichtig, Buchwerte unverkaufter Spieler unbekannt.
- Der Backtest bewertet nur den Buchwert, nicht die Punkte des gekauften Spielers.
- Einzelne Einbrüche bis −59 % in 7 Tagen (Verletzung, Rollenverlust) bleiben bei jeder
  Regel möglich; die Rollen-/Fitnessprüfung der Engine bleibt vorgeschaltet.
