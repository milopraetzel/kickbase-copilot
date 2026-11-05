# Von Platz 12 nach oben — Studie und Umbauplan (final)

Stand Sa 19.09.2026, 01:35 MESZ. Rein lesend erstellt. Nächste Frist: Spieltag 5, Fr 09.10.2026 20:30 (Länderspielpause, rund drei Wochen).

**Grundlage.** Endplan-Entwurf der Synthese, gehärtet durch drei Prüfrollen (Vollständigkeit, Sicherheit, Wirkung) und drei Nachrechnungen (zweiter Schreiber, Abgangspfade, lebende Elf). Pflichtlektüre ABSCHLUSS-2026-09-18 gelesen. Die sicherheitskritischen Codestellen habe ich am 19.09. selbst nachgelesen (nur gelesen, keine Tests, Repo unverändert, keine Live-Zugriffe): der 10.000-Euro-Puffer der Wächter, die Finanzierungsprüfung des Plans („erst über dem Konto wird verweigert"), der Annahmepfad mit selbst erzeugtem emil_go, der frei wählbare Absender, das Operator-Tor ohne emil_go-Abfrage, die Wiedereinstiegssperre nur nach Deckungsrückzug, das fehlende Git-Tor im Deploy, guardrails.lock (9 Dateien, Siegel vom 13.09.) und der geparkte Stash. Alle Zahlen stammen aus der Befundlage; Fortschreibungen sind als solche markiert. Die Befunde sofortplan-* sind veraltet und nicht verwendet; maßgeblich ist die Lage vom 19.09.: Kader 10, Konto 72.334 €, Teamwert 128,57 M, keine Gebote, Transfers 23/200.

## 1. Kurzfassung

- **Was falsch lief:** Der Rückstand ist ein Platz- und Kaufproblem, kein Star- und kein Handelsproblem. 15 von 33 Aufstellungsplätzen waren tot oder leer (−313 P; ST3 = 630 − 300 = 330). Eigene Käufe brachten 25–27 P je Spieler-Spieltag, die Liga 48–52. Fünf Manager-Käufe verloren 16,41 M (62 % des Handelsverlusts).
- **Warum es nicht heilte:** Der Plan zählte Gebote auf Manager-Listings als besetzte Plätze (0 von 8 Annahmen seit 13.09.), bot immer das Mindestgebot (0 von 8 Zuschlägen bei ≤ +4 %), Schulden wurden erst 15 Minuten vor Anpfiff geheilt (3 Verkäufe statt 1), die Doktrin wechselte wöchentlich ohne ein einziges Review (0 von 29).
- **Zweiter Schreiber:** Hermes schrieb 53 von 116 Handels-Writes, 92 % aller Agenten-Writes liefen unter Pauschalfreigabe, 31 % der eigenen Gebote wurden selbst storniert. Die Vollfreigabe steht bis heute in Doku, Skill und Auto-Memory.
- **Was sich ändert:** eine Steuergröße (elf besetzte, möglichst lebende Plätze; Konto zur Frist ≥ 10.000 €), ein autonomer Kaufkanal (Kickbase-Auktion), eine offene Preisregel statt Mindestgebot, Alarm entlang eines Gleitpfads statt Notbremse bei T-15, Abgänge nur noch per Emils Klick, ein Schreiber, ein Änderungspaket je Woche.
- **Reihenfolge:** erst Paket 1 live und belegt (Di 22.09.), dann der Ache-Verkauf, dann Kauf 1. Überstimmen darf nur Emil: heute in der App, später per eigenem Discord-Tap. Der emil_go-Patch wird verworfen.
- **Erwartete Wirkung:** von 490–510 auf 595–630 P je Spieltag zu ST5 (Mitte rund 610); davon sind +100 der elfte Platz. Das ist Augenhöhe mit Platz 10–11 (579–600 je Spieltag), nicht mit Platz 8–9 (727–755). Platz 12 verlassen wir voraussichtlich schon durch ST4 — das ist Olises Spieltag (664 P), nicht der Plan.
- **Ehrliche Rückrechnung:** Die neuen Regeln hätten ST1–3 rund +150 bis +350 P gebracht, Rang 12 wäre geblieben. Sie verhindern Schaden, sie erzeugen keine Punkte. Mehr geht nur über Kapital in lebenden Plätzen, also über die Olise-Frage, und die entscheidet allein Emil.

## 2. Bestätigte Ursachen, nach Schaden sortiert

| # | Ursache | Zahl, n | Befund | Status |
|---|---|---|---|---|
| 1 | Tote und leere Plätze | 15 von 33 Plätzen, −313 P; ST3 630 − 3×100 = 330 (−100-Regel an n=1 Spieltag exakt); Nicht-Olise-Plätze 41,5 / 23,4 / 9,8 P gegen 71–74 Liga | forensik-1, punkte-1, meta-1, rivalen-2 | bestätigt. Rang 8–10 wäre nur die Obergrenze gewesen (Liga-Schnitt auf 9 erkennbar toten Plätzen = 45–90 M, die das Konto nie hatte) |
| 2 | Kaufqualität | 25–27 P je gekauftem Spieler-Spieltag (n=13–14) gegen 48–52 bei gleicher Messung (n=30–38); selbst Käufe mit ≥ 60 min brachten nur 41,8 P (8 von 13) | punkte-2 | bestätigt, Größe unsicher („Hälfte", nicht „Drittel") |
| 3 | Manager-Käufe | 5 von 5 im Minus, −16,41 M auf 48,12 M; 93 % davon Wertverfall nach Kauf | forensik-2, rivalen-4, markt-2, engine-4 | **umstritten:** Kanal oder unsere Auswahl? Andere Käufer liegen 7 von 14 im Plus |
| 4 | Phantomplätze im Plan | 359 von 528 Zielen waren Manager-Listings; 0 von 8 Annahmen seit 13.09.; Plan meldete „8 → 11"; band 7,64 M von 11,65 M | engine-1, betrieb-4, sicherungen-2 | bestätigt; Gegenbeleg n=1 (Osei nahm Tigges zu 100 % MW an) |
| 5 | Mindestgebot | 528 von 528 Geboten = Minimum; eigene Kickbase-Auktionen 0 von 8–9 bei ≤ +4 %, 5 von 6–7 bei +4–10 %, 3 von 4–5 darüber (n=19–20 je Zählung, Schwelle nachträglich gewählt); Liga-Median +9,2 bis +10,2 % | markt-1, engine-2, meta-2 | bestätigt; Siegerpreise sind Obergrenzen |
| 6 | Überziehung und späte Notbremse | 30,0 M Käufe bei 4,2 M Konto → −25,7 M; 94 h ohne Handels-Write; 3 Verkäufe (11,52 M) für 8,6 M Fehlbetrag, 1 hätte gereicht: −300 statt −100 | forensik-3, sicherungen-1, betrieb-1 | bestätigt (ein Ereignis) |
| 7 | Deckungsverkäufe 16.09. | zwei Spieler mit festen Minuten (Ø 31,3 und 33,7 P) für 10,43 M verkauft, Kader blieb 8; 81 Sekunden vorher lag ein Plan ohne Verkauf vor; Emils „Ache verkaufen" wurde auf Dina umgedeutet; das Gebot, für das Vidovic ging, hat nie existiert | forensik-5, engine-5, Nachrechnung | bestätigt |
| 8 | Zweiter Schreiber | 116 Handels-Writes: Hermes 53, Engine 25, Pauschalmandat-Session 16, mcp 12; 81 von 88 Agenten-Writes (92 %) unter Pauschalfreigabe, nur 7 mit namentlichem Auftrag; 17 von 54 Geboten selbst storniert (31 %, 121,1 M), 5 davon gegen fremde Gebote (27,87 M); nur 5 von 53 Hermes-Writes änderten den Kader; 7,73 M von 12,56 M realisiertem Verlust aus Käufen unter Pauschalmandat; rund 10 Tage ohne Kauf | betrieb-3, Nachrechnung | bestätigt; Urheber der Vetos nur vermutet |
| 9 | Doktrinwechsel ohne Review | 60 Records in 29 Tagen, 29 Reviews versprochen, 0 geliefert; dadurch ermöglichte Käufe −9,25 bis −12,35 M | betrieb-2 | bestätigt (Daka-Kausalität offen) |
| 10 | Zu spät verkauft | bei 79 % des 10-Tage-Hochs, Median 4 Tage nach Signal (Liga 96–100 %); vermeidbar rund 11,9 M | markt-4, meta-3 | bestätigt, Papier-Obergrenze |
| 11 | Prognose rollenblind | Bias +16 P, Fehler 53,4 → 44,6 mit Einsatzquote; ohne Historie 0 Punkte (4 von 15 lebenden Marktkandidaten) | punkte-6, engine-3 | teils ungeprüft |
| 12 | Frist-Mechanik | Auktionen mit Ende T-105 bis T-5 planbar, aber nie bietbar; 0,70-Rahmen lehnte Remberg 47 von 47 Mal ab (allein fast wirkungslos: 1 von 330 Plänen) | sicherungen-3, sicherungen-2, engine-6 | bestätigt / ungeprüft-niedrig |
| 13 | Abgänge ohne Aufseher (latent) | 14 Abgangspfade; Annahmepfad live und vom Starter-Schutz ausgenommen; 5 von 6 Listungspreisen stammen von Agenten, der Code nennt sie „von Emil gesetzt"; 42 von 42 Vetos kamen von Agenten, die heute keine Tokens haben | Nachrechnung Abgangspfade | noch kein Schaden; heute schützt nur der Zufall (genau 3 Abwehrspieler, 1 Torwart) |
| 14 | Puffer-Widerspruch (latent) | Plan rechnet „Konto ≥ 0", drei Wächter verlangen 10.000 €: ein Gebot über 100 % des freien Geldes würde bei T-120 storniert oder löste bei T-15 einen Elf-Verkauf aus | Härtung Sicherheit, nachgelesen | im Code belegt, noch kein Fall |

**Widerlegt oder nicht tragfähig:** Handelsgewinn als Punktehebel (rho −0,03, n=12); hartes Trend-Tor (hätte 13–15 % verhindert und den einzigen Gewinner gesperrt); Mindesthaltedauer (Verlierer früh abgeben war 5 von 5 Mal richtig); Wochentag und Fristabstand (Mischungseffekt); Formation (Spanne 18 P = 2 %); 30-%-Konzentrationsriegel (hätte nur Emils Olise-Kauf getroffen); „Sicherungen kosten uns Spieler" (in 10 von 14 Fällen zahlte der Gewinner mehr); Listings als Geldquelle (27 Listings, 0 Annahmen; 350 von 350 Verkäufen exakt zum MW); „5–10 M schlägt 10–20 M". Aus dem Entwurf gestrichen: „Rang 8–10 war erreichbar"; „+100 sicher"; „die Marktwert-Historie fiel von 427 auf 46" (sie wurde nie breiter geführt, die Lücke ist nachladbar); „55 von 116 Writes ohne Absender" (richtig: 30); „die Engine erzeugt keine Verkaufsvorhaben" (sie kann, die Pfade ruhen nur); „der T-120-Storno lässt gedeckte Gebote stehen" (nur bei ≥ 10.000 € Rest).

## 3. Neue Doktrin — zehn harte Regeln

1. **Elf zuerst.** Zur Frist 11 besetzte Plätze und Konto ≥ 10.000 €. Gleitpfad für offene Plätze: höchstens 3 bis T-7 Tage (nur Länderspielpause), höchstens 2 bis T-96 h, 0 ab T-72 h. Alarm gibt es nur, wenn der Gleitpfad verletzt ist oder eine bezahlbare Auktion ohne Gebot bleibt, und dann genau eine Karte je Zustandswechsel. Die Pflichthandlung ist ein Gebot auf eine Kickbase-Auktion, sonst Emil in der App, nie ein Verkauf zur Deckung geplanter Gebote. (forensik-1, betrieb-1)
2. **Ein Platz zählt nur, wenn der Zuschlag automatisch kommt:** Besitz oder Kickbase-Auktion mit Ende spätestens 12 Minuten vor der Frist (der letzte Aufstellungslauf liegt bei T-8) UND einem Gebot, das spätestens bei T-150 ausgeführt wird. Manager-Gebote zählen null Plätze. Über das System gibt es 0 Manager-Gebote, auch nicht als Ausnahme für Emil: 14 von 19 Manager-Deals der Liga schlossen freitags 14:26–19:33, 9 davon im T-120-Fenster; wer das will, nutzt die App. (engine-1, sicherungen-3, markt-2)
3. **Rolle vor Preis und Trend.** Punktende Plätze nur mit KB-Stufe ≤ 2, fit, ≥ 60 min in beiden letzten Spieltagen (77,8 P, 15 % tot, n=2.780; preisbedingt in 5–10 M diese Saison nur 53,7 P im Mittel, Median 44, n=15). Rückfall 2 von 3 (73,1 P, 19 % tot), wenn der Markt sonst leer ist. Unter 5 M = Füller (74–88 % ≤ 10 P). **„Lebend"** heißt: fit, KB-Stufe 1–3, bei LigaInsider weder Bank noch Ausfall; eine Null-Prognose mangels Daten heißt „unbekannt", nicht „tot". Fehlen Minutendaten, wird nachgeladen; bis dahin kauft die Engine ab 5 M nicht autonom (Karte an Emil), bewertet aber nie mit 0. Ausnahmen: Füller und Pflichtplätze ab T-72 h. Fehlende Fitness- oder Rollenangaben sperren wie bisher. Spieler über 20 M ausgenommen. (punkte-2, punkte-3)
4. **Preisregel statt Mindestgebot** (unkalibriert, braucht Emils Ja). Solange weniger als 11 lebende Plätze: Starter 2,5–10 M MW × 1,12; 10–20 M × 1,08 bis 1,10 (Start 1,10 bei Rollenbeleg: × 1,08 schlug in der Zielgruppe nur 6 von 14 Siegerpreisen, × 1,10 9 von 14). Echtes Upgrade einer lebenden Elf ≤ × 1,05. Pflichtplatz ab T-72 h bis × 1,17. Faller (letzter Tagesschritt ≤ −0,5 %): Mindestgebot; ein unbekannter Tagesschritt gilt nicht als „kein Faller". Füller: bis MW + 0,3 M, ab T-96 h bis MW + 0,5 M (68 % der Siegerpreise lagen darunter, n=40; zum Mindestgebot gingen nur 5 % weg). Gebot erst nach dem letzten 22-Uhr-Update vor Auktionsende. Verloren heißt nächste Alternative, nie nachlegen, nie stornieren. Die Zuschlagschance bleibt „unbekannt". Review nach 10 beendeten Auktionen (Ziel ≥ 40 % Zuschläge bei Nicht-Fallern), höchstens +2 Punkte je Woche. Die Regel erzeugt keine Punkte; sie sorgt nur dafür, dass vorhandenes Geld zu Plätzen wird. (markt-1, engine-2, markt-6)
5. **Marktwert-Trend ist Preis, kein Tor.** Bei fallendem Wert kein Aufschlag und 12–14 % erwarteter 7-Tage-Verlust in der Vergleichsrechnung. Hartes Veto nur für Verbesserungskäufe bei 3-Tage-MW < −5 % mit bezahlbarer Alternative, nie für Platzbesetzung. Keine Mindesthaltedauer. (markt-3, forensik-4)
6. **Verkaufen und Abgänge.** Autonom verkauft nur noch die Anpfiff-Notbremse. Annahmen von Angeboten, Verkäufe nach erfolglosem Listing, Schwäche-, Trend- und Tauschverkäufe laufen nur noch als Karte mit Emils Klick. Ab T-96 h kein Agenten-Verkauf und keine Annahme, die Plätze senkt, solange Kader ≤ 11 (Ausnahmen: Konto minus Gebote unter 10.000 €, Anpfiff-Notbremse, Tausch nach bestätigtem Zugang, Emils spielerbezogener Auftrag). „Deckung für geplante Gebote" ist nie ein Grund. Verweigert eine Sicherung die von Emil genannte Aktion, wird gestoppt und gemeldet — kein Ersatzspieler ohne neuen namentlichen Auftrag. Spielende Starter werden nur getauscht, wenn der Ersatz im Kader steht. Tote Faller gehen direkt und vor 22 Uhr an Kickbase, nie per Listing, aber nur, wenn ihr Erlös einen belegten Starter bezahlt; sonst bleiben sie Platzhalter (0 statt −100). (forensik-5, markt-4)
7. **Nur echtes Geld, mit Puffer, in jedem Pfad.** Konto minus alle offenen Gebote minus neues Gebot ≥ 10.000 € — derselbe Puffer, den T-120-Storno, Notbremse und Pfand-Einlöser verlangen. Das gilt auch für Emils Auftrag über das System; vor einem App-Gebot rechnet die Session die Deckung lesend vor (wie am 18.09.: 11.648.786 − 2.976.452 − 8.600.000 = 72.334). Fällt die Deckung unter den Puffer, kommt sofort eine Karte, unabhängig von der Frist. Die 33-%-Regel bleibt technische Grenze. Notbremse: kleinste deckende Zahl Elf-Verkäufe; ein Spieler über 25 % des Teamwerts geht nur, wenn keine andere Kombination deckt; einziger Torwart oder Stürmer zuletzt. (forensik-3, sicherungen-1)
8. **Ein Schreiber, eine Überstimmung.** Nur die Engine erzeugt Kauf- und Verkaufsvorhaben. Sessions lesen, bereiten vor und vetoen (ein Veto wird nie wegen fehlender Begründung abgelehnt). Pauschalmandate („stehende Freigabe", „Vollmandat", „volle Kontrolle") gelten nicht mehr; ein Session-Write braucht Emils namentlichen Auftrag für genau diesen Spieler. Kein Code darf ein emil_go erzeugen. Nach jedem Storno kein Wiedereinstieg bis Auktionsende. Überstimmen darf nur Emil (Abschnitt 6). (betrieb-3)
9. **Änderungsdisziplin und Deploy-Hygiene.** Ein Paket je Woche (Mo/Di), keines ohne ausgefülltes Review des Vorgängers, kein Deploy an Schreibstelle oder Wächtern in den 72 h vor einer Frist, nie 21:25–22:25. Paketarbeit nur in einem eigenen git worktree, genau eine Session deployt, vor jedem Deploy: Arbeitsbaum sauber, Branch main, HEAD = origin/main, Stash gesichtet. guardrails.lock setzt nur Emil. (betrieb-2)
10. **Erfolg = besetzte, lebende Plätze und Punkte je Platz**, nicht Handelsgewinn (rho −0,03, n=12). Zwei KPI-Leitern (Abschnitt 8): Pfad A mit dem vorhandenen Geld, Pfad B erst nach einer Kapitalentscheidung. Spieler ≥ 10 M und Top-1-Anteil nur als Diagnose. Keine Handelstasche im Umbaufenster; frühestens nach ST8 als Papierhandel. (forensik-6, rivalen-7)

## 4. Was abgeschaltet oder gestrichen wird

- Autonome Gebote auf Manager-Listings und ihre Zählung als Plätze; auch die im Entwurf vorgesehene System-Ausnahme für Emil entfällt. Wiedereinplanen gesperrter Ziele (Pieringer, Al Dakhil).
- Mindestgebot auf umkämpfte Starter; Gebote, die 13–38 h vor Auktionsende stehen.
- Verkäufe „zur Deckung geplanter Gebote"; Listings über Marktwert als Geldquelle.
- Der zweite Schreiber: Hermes, Codex und die Mac-Loops; alle Pauschalmandate in Doku, Skill, Hermes-Skript und Auto-Memory; uncodierte Gates („Startergate", „trend=2/fallend"); selbst erzeugte emil_go-Texte.
- Der emil_go-Patch im git stash: verwerfen (Emil entscheidet jetzt: löschen oder als Patch-Datei außerhalb des Repos).
- Sofort abgemeldet (Code bleibt): Job „Überraschungen" (10 von 10 rot), Gebots-Wächter (falscher Sniper-Text „~90 % MW"), Schatten-Auto-Trading 22:30. Weitere Jobs erst nach ST5 und nur mit Emils Ja.
- Momentum-/Pfand-Logik, „Hinrunde = 70 % Börse", Pfand-Hebel-Spec, Formationsziele, Wochentagsregeln, Mindesthaltedauer, harte Trend-Tore, 30-%-Riegel, Handelstasche, „Stilles Nichtstun ist ein Mandatsbruch".

**Inventar der 14 Abgangspfade** (Nachrechnung Abgangspfade; die Engine selbst hat noch nie verkauft: 23 erzeugte Abgangs-Vorhaben, 0 Verkäufe):

| Pfad | Heute | Künftig |
|---|---|---|
| Annahme eines Angebots auf ein eigenes Listing | live; nur Positions-Minima schützen; schon heute erzeugten ≥ 100 M auf Olise oder ≥ 15,55 M auf Ache ein Annahme-Vorhaben, mit einem vierten Abwehrspieler auch ≥ 13,52 M auf Minjae | nur per Emils Klick; jedes Angebot wird gemeldet (heute verschwinden Angebote zwischen Listungspreis und 103 % MW spurlos) |
| Sofortverkauf nach erfolglosem Listing | ruht bis Kader 12; würde Olise zum Marktwert statt zu 100 M vorschlagen, sobald LigaInsider ihn einmal als Bank führt | aus |
| Schwäche-, Trendverkauf, Gewinnmitnahme | ruhen bis Kader 12; 5 von 8 Bestandsspielern erfüllen schon ein Kriterium | aus; speisen nur die Verkaufskarte |
| Tauschverkauf | ruht, bis eine Elf stellbar ist; nie live gelaufen | aus bis Replay und Trockenlauf, dann Emils Entscheid |
| Deckungsverkauf des Pfand-Einlösers | ruht | aus, der Alarm bleibt |
| Anpfiff-Notbremse und T-120-Gebotsstorno | scharf | bleiben |
| Manuelle Verkäufe und Listings über API und MCP | offen für jeden mit Schreib-Token (64 von 64 solcher Vorhaben kamen von Agenten) | nur mit namentlichem Emil-Auftrag, später Inhaber-Auftrag |
| Freigabe-Karten, Alt-Auto-Trading 22:30, ungenutzte Angebotsbewertung | ungenutzt oder tot | fliegen raus |

## 5. Umbauliste (Details im Feld change_list und kompakt in Anhang B)

| Paket | Wann | Inhalt | Bedingung |
|---|---|---|---|
| 1 (P0) | Di 22.09., Doku schon Mo 21.09. | Plan zählt nur erreichbare Plätze; gesperrte Ziele raus; Geldrechnung mit 10.000-€-Puffer und Einzelrahmen 1,0 bei Elf-Lücke; minimaler Rollenfilter ab 5 M; Ergebnis-Wächter als eigener Job; zweiter Schreiber aus Doku, Skill, Skript, Memory; Absender-Stolperdraht; Git-Tor im Deploy; Voll-Nachladen der Marktwert- und Leistungsdaten; Kommentare richtigstellen, drei Jobs abmelden | kein Eingriff an der Schreibstelle, keine versiegelte Datei; reine Verschärfungen |
| 2 (P1) | Di 29.09. | Gebot vor dem Freeze (T-150, Platzpuffer 12 min); Preisregel samt geprüften Knopf-Beträgen; lebende Elf als Steuergröße; voller Rollenfilter; Abgänge nur per Klick; Verkaufskarte; Notbremse im Schattenlauf; „extern"-Zeilen für App-Trades; Review-Pflichtfelder; Schattenportfolio | Preisregel, lebende Elf und Annahme-per-Klick nur mit Emils Ja; Schwellen erst nach Neuberechnung auf vollen Daten festschreiben |
| 3 (P1) | Di 13.10., nach ST5-Retro | Verschärfungen an der Schreibstelle: Wiedereinstiegssperre nach jedem Storno, Verkaufs-Floor ab T-96 h (auch für Annahmen), Absender-Feld ohne Veto-Blockade, Werkzeuge Veto und Gebotsaufgabe, Siegel-Riegel; Teilmengen-Notbremse live | versiegelte Tests müssen unverändert grün bleiben; Notbremse nur mit Emils OK und Schattenlauf-Beleg |
| 4 (P2) | ab Di 20.10. | Inhaber-Auftrag per Discord-Tap; Aufarbeitung Commit 8eed081; Füller-Bedingung statt 96-h-Fenster; Trend als Kostenposition; Prognose-Kandidat v2; Werkzeuge 23 → 12–14, Jobs 32 → ≤ 20; Marktsnapshots; eine Regelstelle; Rückbau der Altpfade; KPI-Bericht | jeweils Emils Entscheid; Siegel setzt nur Emil |

## 6. Plan bis Fr 09.10.2026 20:30 und Zielbild

**Geldpfad** (Fortschreibung aus der Lage vom 19.09.): Konto 0,07 M; Prämie 0,1 M je Tag (21 Tage = 2,10 M); ST4-Geld geschätzt 1,0–1,1 M (beobachtet 1.000 € je Punkt plus 100–200 k, n=3). Ohne Verkauf belastbar rund 3,2 M. Mit Ache (MW 19.09. 10,03 M; bei −0,31 M je Tag am Di 22.09. rund 9,1 M, bei −0,49 M aus dem 7-Tage-Mittel rund 8,6 M) stehen ab Di 22.09. abends rund 10,1–10,6 M bereit. Das reicht für EINEN belegten Starter plus später einen Füller. Ein zweiter echter Starter ist nicht finanziert. Yamamoto und Vozar bleiben Platzhalter, solange ihr Erlös keinen Starter bezahlt.

**Woche 0 — Sa 19.09. bis Mo 21.09.** (ST4 läuft, Elf eingefroren)
- Kein Trade, kein Deploy. Die Session bereitet Paket 1 in einem eigenen git worktree vor (nicht per Branch-Wechsel im geteilten Verzeichnis), nur gezielte Testdateien, danach ein lesendes Zweitreview.
- **[EMIL/APP, Vorbedingung für Kauf 1, am besten sofort]** Alt-Listings der spielenden Starter entfernen: Minjae (97,1 % MW, unter Marktwert), Schwäbe (104,5 %), Scheller (112 %); empfohlen auch Ullrich. Alle fünf Listings hat am 07.09. ein Agent gesetzt, nicht Emil; die drei Starter-Listings beruhen auf einer Fehllesung des Trendfelds. Ache (155 %) und Olise (154 %) sind Emils Sache; dort wäre eine Annahme finanziell gut, das Risiko ist nur das Kaderloch.
- **[EMIL]** Ein datierter Satz, der die Vollfreigabe vom 02.09. und alle Pauschalmandate widerruft; Web-App-Passwort und Schreib-Token wechseln; Hermes-Crons auf der Box abschalten; Stash-Entscheid.
- Mo 21.09. vormittags [SESSION, nur lesend]: Frist ST5 gegen league_state prüfen (Tag-1-Pflicht). Retro ST4 mit Zahlen: Punkte, Rang, gebuchtes Spieltagsgeld (ob eine Zusatzprämie für Olises 664 Punkte bucht, ist nie beobachtet — nur messen). KPI-Nullmessung, Start des Schattenportfolios. Doku-Teil von Paket 1 committen.
- Bis Paket 1 live ist, läuft die alte Engine. Kommt das ST4-Geld vorher, kann sie es wieder in Manager-Geboten binden; Emil vetot solche Vorhaben in der Zentrale (2-h-Fenster) oder pausiert die Auto-Vorhaben selbst.

**Woche 1 — Di 22.09. bis So 27.09.**
- Di 22.09. [SESSION]: Deploy Paket 1 (außerhalb 21:25–22:25, mit Checkliste). **Wirkungsnachweis** am nächsten Engine-Bericht: 0 Manager-Ziele, 0 gesperrte Ziele, Plätze = Besitz + erreichbare Kickbase-Auktionen, jedes Preisszenario lässt ≥ 10.000 € übrig, alle Ziele ab 5 M mit Rollenbeleg, Deploy-Bericht mit Commit-Hash.
- **Erst danach [EMIL-ENTSCHEID] Ache:** Verkauf zum Marktwert an Kickbase, vor 22:00, kein Listing — wenn ST4 unter 60 min oder der Tagesschritt weiter ≤ −0,5 %. Weg: App oder MCP-Verkauf mit Emils wörtlichem Auftrag samt Spielernamen. Tigges hält das Stürmer-Minimum. Kader danach 9. Das ist eine Geldentscheidung (Wertverlust stoppen), kein Punktehebel: erwartet +5 bis +25 P, Spanne −10 bis +40. Der Tag Wartezeit kostet rund 0,31 M. Rutscht Paket 1, rutscht der Verkauf mit.
- Di/Mi: erster Voll-Nachladelauf (Marktwerte und Leistungen aller rund 427 Spieler). Danach die Schwellen (Faller, Rollenfilter, Preisleiter, Marktangebot) auf vollen Daten neu rechnen, bevor Paket 2 sie festschreibt.
- **Mi 23.09. bis So 27.09. — KAUF 1:** ein belegter Starter (KB ≤ 2, fit, ≥ 60 min in ST3 UND ST4, Tagesschritt > −0,5 %, MW 5,0 bis rund 9,0 M) aus einer Kickbase-Auktion. Gebot = MW × 1,12, Höchstgebot = Konto − offene Gebote − 10.000 €, nach dem letzten 22-Uhr-Update vor Auktionsende. Weg: **[EMIL/APP]**; die Session rechnet vorher lesend vor (Kandidaten, Tagesschritt, Deckung gegen alle offenen Gebote, auch Engine-Gebote). Verloren = nächste Alternative, nie nachlegen. Solange die Alt-Listings stehen und Paket 2 nicht live ist: kein Abwehrspieler, kein Torwart. Ab etwa 8,5 M Marktwert bleiben nach dem Kauf nur rund 0,9–1,4 M; mit den Prämien werden daraus bis Mo 05.10. rund 1,9–2,4 M für den Füller (fortgeschrieben). Erwartung: 45–55 P je Spieltag bei 30–50 % Risiko eines Spieltags ≤ 10 P. Beobachtungsliste (frei laut 18./19.09., kaufbar erst bei Listung): Obermair 7,70 M, Skhiri 8,81 M, Castañeda 8,97 M, ter Horst 7,52 M (Abwehr). Ohne Ache-Geld: kein Starterkauf.

**Woche 2 — Mo 28.09. bis So 04.10.**
- Mo 28.09.: Wochenkontrolle, Review für Paket 1. Emil entscheidet Paket 2 (Preisregel, lebende Elf, Annahme per Klick).
- Di 29.09.: Deploy Paket 2. Mi 30.09. 05:10/05:30 Wochenjobs, keine Aktivierung.
- Ab 29.09., sobald Geld da ist: Füller-Versuche mit billigen Feldspielern der fehlenden Position (KB-Stufe 3 ist jederzeit zulässig, das 96-h-Fenster sperrt nur KB 4/5), MW ≤ 2,5 M, Gebot bis MW + 0,3 M.
- Kauf 2 nur, wenn nach Kauf 1 ein belegter Starter ab 5 M voll bezahlbar ist (nicht finanziert; nur bei Zusatzprämie oder billigem Kauf 1).
- Fr 02.10. 20:30 = T-7 Tage: höchstens 3 offene Plätze; Ende des Fensters für Verkäufe spielender Spieler.

**Woche 3 — Mo 05.10. bis Fr 09.10.**
- Mo 05.10. 20:30 = T-96 h: kein platzsenkender Verkauf mehr, höchstens 2 offene Plätze; das 96-h-Fenster für Bankkörper öffnet; Füller bis MW + 0,5 M.
- Di 06.10. 20:30 = T-72 h **Pflichtprüfung:** Kader ≥ 11? Konto minus alle Gebote ≥ 10.000 €? Keine Manager-Gebote offen? Wenn rot: am selben Abend Füller-Gebot bzw. Pflichtplatz bis × 1,17 auf eine Kickbase-Auktion mit Ende vor Fr 20:18; **[EMIL/APP]**, falls die Engine nichts Bietbares findet. Ab jetzt kein Deploy an Schreibstelle oder Wächtern.
- Mi 07.10. 20:30 letzter regulärer Fülltermin. Do 08.10. 12:00 Änderungsstopp bis Mo 12.10. 12:00.
- Fr 09.10.: bis 18:00 Deckung nachrechnen; 18:00 letzte reguläre Gebotsausführung (T-150), 18:30 Freeze, 19:45 Aufstellung, 20:22 T-8-Check, Rücklese 11/11 als Beleg.
- Danach: Sa 10.10. bis Di 13.10. Retro ST5 und Reviews; Di 13.10. Paket 3; ab Di 20.10. je ein Paket pro Woche. Bis zum ST7-Review keine weitere Doktrinänderung.

**Zielbild des Umbaus**
- **Bleibt:** eine Schreibstelle mit Audit, Preflight und Kill-Switch; Vorhaben mit Veto-Fenster für Käufe; der Rechenkern des Mannschaftsplans (330 von 330 Optima reproduziert; das Replay-Werkzeug wird Testmittel für jede Regeländerung); Nur-echtes-Geld seit 13.09.; Anpfiff-Kaskade T-120/60/35/15; Teil-Elf und T-8-Check; Prämie, Snapshot, Job-Historie; versiegelte Tests, Decision-Records, LEARNINGS; Cockpit Heute/Übersicht/Liga; Modellfreigabe nur per promote.
- **Fliegt raus:** Abschnitt 4. Physisch gelöscht wird frühestens nach ST6, bei versiegelten Altpfaden erst nach Emils eigenhändigem Neu-Siegel. Eine Wahrheit: die Box-Datenbank; die lokale bleibt Entwicklungshilfe, Werkzeuge ohne Box-Verbindung melden einen Fehler statt stiller Lokaldaten.
- **Neu, in dieser Reihenfolge:** (0) ein Schreiber in Doku, Memory und Zugang, (1) ehrliche Platzzählung, Geld mit Puffer, minimaler Rollenfilter, (2) Ergebnis-Wächter, (3) vollständige Datenbasis, (4) Preisregel und lebende Elf, (5) Abgänge nur per Klick, (6) Notbremse erst im Schattenlauf, dann live, (7) Verschärfungen der Schreibstelle, Absender, Werkzeuge, (8) Inhaber-Auftrag, (9) weniger Oberfläche, (10) eine Regelstelle.

**Wer darf die Engine überstimmen?** Nur Emil als Kontoinhaber.
- **Heute:** Stoppen („Überspringen") geht in der Zentrale; kaufen oder verkaufen gegen die Auswahl der Engine geht ausschließlich in der Kickbase-App. Der Discord-Weg ist in Produktion faktisch aus: seit 13.09. trägt keines von 25 Vorhaben eine Karte, in 209 Vorhaben gab es 0 Taps und 0 Betragsanpassungen. App-Trades spiegelt der Abgleich künftig als „extern" ins Audit, ohne dass sie irgendwo als ausgeführter Engine-Write zählen.
- **Übergang:** die Knöpfe „+10 %", „+20 %", „Betrag", „Überspringen" an einem von der Engine ausgewählten Vorhaben — erst wenn Paket 2 die Beträge als geprüfte Szenarien führt, eine Anpassung das 22-Uhr-Update übersteht und ein Trockenlauf bestanden ist. Das Cockpit mit Basic-Auth und Schreib-Token ist kein Inhaberkanal: dieselben Zugangsdaten liegen in der Agenten-Umgebung.
- **Künftig: Inhaber-Auftrag.** Agent oder Cockpit dürfen ihn nur vorbereiten; scharf wird er ausschließlich durch Emils Discord-Tap (die Prüfung seiner User-ID existiert und lehnt ohne Konfiguration alles ab). Vorbedingung in Emils Hand: Karten für Inhaber-Auftrag und Alarme wieder einschalten, User-ID bestätigen, gegen den Digest-Entscheid vom 09.09. abwägen; dann Trockenlauf (Emils Tap wirkt, fremder Tap wird abgelehnt).
- Der Auftrag ersetzt nur die Vorauswahl der Engine. Es bleiben: echtes Geld mit Puffer, T-120-Freeze, Preiskappen, Vereinslimit, Tages-Caps, Wiedereinstiegssperre, 200er-Regel, Kill-Switch, Verfassung und eine harte Fitness- und Ausfallprüfung. Ein emil_go-Text aus einer Session öffnet weiterhin nichts. Versiegelte Tests versiegelt nur Emil neu.

## 7. Olise — nur Rechnung, Entscheidung bei Emil

Fakten: MW 64,94 M (7 Tage −123 k, seitwärts), Kauf 68,02 M, 50,5 % des Teamwerts. Punkte 249/125/232/664; Schnitt 202 (ST1–3), 211 über 37 Spieltage (Streuung 143), 318 mit ST4. 3,1–4,9 P je Mio. gegen 5–6 in der Klasse 5–20 M.

- **A Halten und füllen (finanzierter Plan):** 595–630 P je Spieltag, mit einem nicht finanzierten zweiten Starter bis rund 680. Fällt Olise aus, bleiben rund 390–430.
- **B Verkauf an Kickbase zum MW** (350 von 350 Verkäufen exakt MW): 64,94 M plus Plan-Geld ≈ 79–80 M, nach rund 9 % Aufschlag rund 73 M Marktwert, sieben Käufe zu etwa 10,5 M. Ergebnis je Kaufqualität 38 / 48 / 62 / 75 P: 554 / 624 / 722 / 813. Differenz zu A (Mitte 610): −56 / +14 / +112 / +203. Nur mit tatsächlich beobachteten Nach-Kauf-Punkten der Liga gerechnet liegt B bei rund 750, konservativ bei rund 645, auf heutiger Marktbasis realistisch bei 685–740.
- **Gleichstand** bei rund 44–53 P je gekauftem Spieler-Spieltag; hält Olise 318, erst bei rund 60. Unsere gemessene Qualität: 25–27 (alle Käufe), 38 im Band 7–12 M (n=3). Liga 48–52. Auf Liga-Niveau wäre B also nur knapp besser als A.
- **C 100-M-Listing:** +54 % über MW; höchster je gezahlter Aufschlag für Spieler ≥ 30 M +36 % (Vorsaison) bzw. +18,6 %; geschätztes Maximalgebot eines Rivalen 57–67 M. Wirkung null, gleichbedeutend mit A.
- **D Option offen halten:** Kickbase zahlt jederzeit sofort exakt MW, es muss nichts im Voraus entschieden werden. Der Auslöser des Entwurfs (eigene Kaufqualität über 10–15 Spieler-Spieltage) wäre mit ein bis zwei Käufen erst bei ST9–ST14 messbar; D liefe damit bis November still auf „Halten" hinaus. Deshalb neu: ein **Schattenportfolio** (je Woche die 3–5 besten regelkonformen Kickbase-Auktionen; „gewonnen", wenn der Regelpreis über dem später sichtbaren Siegerpreis lag; ihre Folgepunkte zählen; Füller raus, Remberg rein) liefert n ≥ 30 bis ST7 ohne Kapital. Rechnerischer Auslöser: Schattenqualität ≥ 45 P UND ≥ 3 gleichzeitig bietbare Feldspieler-Auktionen passender Position (8–15 M, KB ≤ 2). Die alte Bedingung „≥ 6 bietbare Starter je Woche" trennt nichts: in 5,2 Tagen waren 20 solche Kandidaten sichtbar (5 davon Torhüter, Listingart unbekannt).
- Wer B noch für ST5 will: sieben Käufe brauchen bei 43–50 % Zuschlagsquote 14–16 Versuche; das Fenster schließt eher Fr 25.09. als Mo 28.09., jeder offene Platz kostet −100. Die eigene Kaufqualität ist vor ST5 nicht messbar; ein Verkauf davor wäre eine Wette.
- **Sicherheit:** Die Schutzliste ist seit 16.09. leer, die Notbremse kann Olise erreichen. Die Teilmengen-Regel allein würde ihn ab 21,65 M Fehlbetrag (64,94 / 3) sogar zur ersten Wahl machen (ein Verkauf schlägt Minjae + Ache = 23,16 M); deshalb die 25-%-Klausel in Regel 7. Dazu kommt eine rein meldende Karte, wenn sich bei einem Spieler über 30 % des Teamwerts Fitness-, KB- oder LigaInsider-Status ändert.

## 8. Erwartete Wirkung und Messung

| Pfad | P je Spieltag | Herleitung |
|---|---|---|
| Ohne Handeln | 490–510 | 10 Spieler, 2–3 tot, 1 leer (−100) |
| Nur Lücke füllen (ohne Ache-Geld) | 590–610 bei besetztem Platz | +100 je Platz; der Füller-Zuschlag ist nicht sicher (eigene Füller zum Mindestgebot 2 von 3), erwartet deshalb rund 575–600 |
| Plan A finanziert (1 Starter + 1 Füller, Ache raus) | 595–630, Mitte rund 610 | nur +5 bis +25 über „Lücke füllen"; der Ache-Tausch ist eine Geldentscheidung |
| Zweiter Starter | bis rund 680 | **nicht finanziert;** nur bei Zusatzprämie oder billigem Kauf 1 |
| Olise-Szenario B | 554–813 | hängt allein an der Kaufqualität |

Ein einzelner Spieltag streut mit 150–230 P; bewertet wird über mindestens drei Spieltage, ohne ST4 (330 + rund 1.050 + 600 ergäbe geschönte 660). **Rang:** Rückstand auf Platz 11 nach ST3 384 P. ST4 (live 752 nach 2 von 10 Spielern, geschätzt 1.000–1.100 gegen 579–600 der Plätze 10–11) dürfte reichen, um Platz 12 zu verlassen. Mit 595–630 hält Plan A danach Platz 10–11 und verliert auf Platz 8–9 weiter rund 100–150 P je Spieltag. Platz 8–9 verlangt dauerhaft rund 750 und damit Pfad B. **Rückrechnung:** Die neuen Regeln hätten ST1–3 rund 1.500–1.700 statt 1.353 ergeben (Platz 11: 1.737). Dauerhaft: keine −100 je Leerplatz mehr (ST3 real −300), Notbremse höchstens 1 Elf-Verkauf.

**KPI-Leiter A (mit dem vorhandenen Geld, ab ST5):** 11/11 Plätze zur Frist; ≥ 6 Plätze mit ≥ 60 min (ab ST7 ≥ 7); tote Plätze ≤ 4 (ab ST7 ≤ 3); ≥ 40 P je Nicht-Olise-Platz im Mittel ST5–ST7 (bisher 41,5 / 23,4 / 9,8); Ziel Platz 10–11.
**KPI-Leiter B (erst nach einer Kapitalentscheidung):** ≥ 9 lebende Plätze; ≥ 50, später ≥ 60 P je Nicht-Olise-Platz; Ziel Platz 8–9.
**Kauf und Markt:** Punkte je gekauftem Spieler-Spieltag ohne Füller, mit Remberg, Richtwert 45 (Liga 48–52), dazu die Schattenzahl; Zuschlagsquote bei Nicht-Fallern ≥ 40 % bei Median-Aufschlag ≤ 12 %; Anteil Käufe mit Tagesschritt ≤ −0,5 % ≤ 15 % (bisher 5 von 14).
**Betrieb:** Manager- und gesperrte Ziele im Plan 0; Stunden mit Deckung unter 10.000 € = 0; Notbremsen 0; Writes mit Absender Hermes/Codex nach dem 22.09. = 0; Session-Writes mit namentlichem Emil-Auftrag 100 % (bisher 8 %); Vetos ohne Grundcode von Agenten 0; Stornoquote ≤ 5 % (bisher 31 %); Vorhaben je Zuschlag ≤ 5:1 (bisher 209:11); Suchmuster für Pauschalmandate: 0 Treffer außerhalb des Archivs und im Memory; 1 Paket je Woche, alle fälligen Reviews ausgefüllt; Commit-Hash der Box = origin/main; Marktwert-Historie ≥ 400 Spieler je Tag lückenlos ab 21.08.; Tagesschritt bekannt bei ≥ 90 % der Marktkandidaten (bisher 6 %); Prognose-Bias ≤ 5–10 P (bisher +16).

## 9. Risiken, Datenlücken, Entscheidungen

- **Kleine Stichproben:** 3–4 Spieltage, 20 eigene Auktionen, 5 Manager-Käufe, 1 Notbremse, eine Marktphase. Alle Schwellen sind Startwerte mit Review nach ST5–ST8.
- **Die Preisregel überzahlt sicher** (Kickbase nimmt nur zum MW zurück): rund 0,8–1,0 M je 8-M-Starter; Fluch des Gewinners. Sie braucht Emils Ja.
- **Das Veto-Fenster schützt nur, wenn jemand hinschaut.** Alle 42 bisherigen Vetos kamen von Agenten. Mit einem Schreiber und ohne Aufseher tragen der Rollenfilter (Käufe) und die Klick-Pflicht (Abgänge) die Last.
- **Paket 1 ist groß** (zehn Punkte). Es bleibt außerhalb der Schreibstelle und versiegelter Dateien; rutscht es, rutschen Ache-Verkauf und Kauf 1 mit (rund 0,31 M je Tag). Notlösung: nur die ehrliche Platzzählung als Mini-Paket vorziehen.
- **Erst verkaufen, dann kaufen** kann „Kader 8" wiederholen. Gegenmittel: Gleitpfad 3/2/0, Platzhalter bleiben, Füller ab 29.09., Pflichtprüfung T-72 h.
- **Marktangebot:** am 19.09. kein KB-1/2-Stürmer am Markt; nach dem Ache-Verkauf ist Tigges (Joker, bei LigaInsider Bank) einziger Stürmer. Im Band 7,0–8,9 M waren in 5,2 Tagen nur 3 belegte Starter sichtbar.
- **Upgrade-Pfad nie live gelaufen** (331 von 331 Plänen liefen als Platzbesetzung): vor dem ersten Einsatz Replay gegen gespeicherte Zustände und Trockenlauf der Tauschverkäufe.
- **Deadlock-Rückfall:** Geldregel plus Verkaufs-Floor plus Starter-Schutz können sich verklemmen. Deshalb gilt der Floor nur ab T-96 h bei bekannter Frist und Kader ≤ 11; die versiegelten Fälle bleiben nur grün, solange das Fenster unter 10 Tagen liegt (dort: bekannte Frist in 10 Tagen, Kader 12).
- **Versiegelte Tests:** Alarme nicht in die Anpfiff-Planung (genau vier Stufen sind gepinnt); der 10.000-€-Puffer der Wächter wird nicht geändert (zwei versiegelte Fälle pinnen ihn). Bricht ein versiegelter Test, wird gestoppt; versiegelt wird nur von Emil.
- **Datenlücken:** keine Marktsnapshots, kein Kontoverlauf; Rivalenkonten und verlorene Fremdgebote unsichtbar; Eingangszeit von Manager-Angeboten unbekannt; Marktverhalten in der Länderspielpause nie beobachtet; Frist ST5 nicht live verifiziert; Zusatzprämien nie als Buchung gesehen; Zustand der Hermes-Crons und der Box-Schalter lokal nicht prüfbar. Die Marktwert-Lücke ist nur bis etwa 20.11. rückwirkend schließbar (92-Tage-Fenster).
- **Parallele Session im Repo:** vor jedem Deploy git fetch und Prüfsummenvergleich.
- Dieser Plan ist selbst ein Doktrinwechsel. Er zählt nur, wenn nach Paket 3 bis zum ST7-Review nichts Weiteres geändert wird.

**Entscheidungen für Emil** (Richtungsentscheide haben keinen Default; reine Verschärfungen gelten als Ja, wenn bis zum Termin kein Nein kommt — sie schwächen keine Sicherung und laufen unter dem bestehenden Selbstumbau-Mandat, das Emil jederzeit einschränken kann):

| Nr | Entscheidung | Art | Termin | Bei Schweigen | Blockiert |
|---|---|---|---|---|---|
| E1 | Preisregel statt Mindestgebot | Richtung | Mo 28.09. | bleibt Mindestgebot | Preisregel in Paket 2 |
| E2 | Manager-Listings raus aus allen autonomen Pfaden, keine System-Ausnahme | Verschärfung | Mo 21.09. | Ja | Paket 1 |
| E3 | Teilmengen-Notbremse mit 25-%-Klausel (Tabelle „ab welchem Fehlbetrag trifft es Olise" liegt bei) | Richtung | vor Di 13.10. | alte Kaskade bleibt | Notbremse live in Paket 3 |
| E4 | Ache an Kickbase verkaufen, nach Paket-1-Nachweis | Richtung | Di 22.09. | kein Verkauf, nur Füller | Kauf 1 |
| E5 | Olise: halten, verkaufen oder Option offen (Abschnitt 7); Schutzliste wieder füllen? | Richtung | offen; für ST5 bis etwa Fr 25.09. | faktisch Halten | nichts |
| E6 | Inhaber-Auftrag per Discord-Tap: Stash jetzt löschen oder auslagern; Karten und User-ID (deine .env) einschalten; Ja/Nein zum Entwurf | Richtung | Stash sofort, Rest bis ST5 | App bleibt einziger Inhaberweg | Paket 4 |
| E7 | Commit 8eed081: bestätigen mit Record und eigenem Neu-Siegel, Revert oder neuer Wert (Empfehlung: nicht still bestätigen) | Richtung | nach ST5 | bleibt offen | Paket 4 |
| E8 | Verkaufs-Floor ab T-96 h (auch Annahmen) und Wiedereinstiegssperre nach jedem Storno | Verschärfung | vor Di 13.10. | Ja | Paket 3 |
| E10 | Ein Schreiber: datierter Widerruf der Pauschalmandate; Passwort und Schreib-Token wechseln; Hermes-Crons abschalten; Memory-Einträge als abgelöst kennzeichnen | nur Emil | Mo 21.09. | Doku wird trotzdem bereinigt, der Zugang bleibt offen | Abnahme Paket 1 |
| E11 | Referenzdatei mit dem Siegel-Hash auf der Box anlegen (Stolperdraht) | nur Emil | vor Di 13.10. | Prüfung entfällt | Teil von Paket 3 |
| E12 | Lebende Elf als Steuergröße: Ersatzkäufe für tote Plätze laufen mit Platzbesetzungs-Rahmen (Kappe 135 %, Einzelrahmen 1,0); zählt KB-Stufe 3 als lebend (Minjae, Ache, Ullrich)? | Richtung | Mo 28.09. | bleibt bei „11 Körper" | Teil von Paket 2 |
| E13 | Annahmen nur noch per Klick (dreht deinen Entscheid vom 16.09., dass Olise zu 100 M automatisch geht) | Richtung | Mo 28.09. | Annahmepfad bleibt, Listings müssen weg | Teil von Paket 2 |
| E14 | Welche Jobs nutzt du wirklich (Morgen-Briefing, News, Tipps)? | Richtung | nach ST5 | nichts wird abgemeldet | Paket 4 |
| H1 | (früher E9) Handlung, keine Entscheidung: Alt-Listings von Minjae, Schwäbe, Scheller (und Ullrich) in der App entfernen | nur Emil | vor Kauf 1 | Kauf 1 dann kein Abwehrspieler oder Torwart | Kauf 1 |

## Anhang A: Umgang mit den Einwänden der Härtung

| Einwand | Entscheidung |
|---|---|
| Zweiter Schreiber steht weiter in Doku und Memory (Blocker) | eingearbeitet: eigener Punkt in Paket 1 (Doku, Skill, Hermes-Skript, Memory), Absender-Stolperdraht, drei Abnahmekriterien, Emils Schritte in E10. Prämisse korrigiert: 30 statt 55 Writes ohne Absender |
| Ache-Verkauf vor Paket 1; alte Engine bindet das Geld, Auswahl rollenblind (2 Blocker) | eingearbeitet: Reihenfolge gedreht, Wirkungsnachweis als Bedingung, minimaler Rollenfilter in Paket 1, Regel für App-Gebote |
| Plan rechnet ≥ 0, Wächter 10.000 € (Blocker) | eingearbeitet: Puffer in Planrechnung, Regel 7, Alarmen und Floor-Ausnahme; der Wächter-Puffer bleibt unangetastet |
| Annahme- und Verkaufspfade nicht inventarisiert (3 Einwände) | eingearbeitet: Inventar in Abschnitt 4, Klick-Pflicht, Listings als Vorbedingung. Abweichung: die Code-Änderung kommt in Paket 2 statt Paket 1, weil die Vorbedingung ohne Code schützt und Paket 1 sonst zu groß wird; bis dahin ist Kauf 1 kein Abwehrspieler oder Torwart, falls Listings stehen |
| „Lebende Elf" existiert im Code nicht | eingearbeitet als Teil von Paket 2 mit Emils Ja (E12). Paket 1 war nicht nötig: 11 Körper sind vor dem 05.10. nicht erreichbar |
| Discord-Tap ist in Produktion aus und nie benutzt | eingearbeitet: bis zum bestandenen Trockenlauf ist die App der einzige Inhaberweg; Alarme laufen über den Blocker-Kanal |
| „+10 %"-Brücke übersteht das 22-Uhr-Update nicht (2 Einwände) | eingearbeitet, aber verschoben: die Brücke kommt mit der Preisregel in Paket 2 (gleiche Codestelle, gleiche Tests), mit Bestandsschutz für angepasste Beträge und Meldung bei Verfall. Kauf 1 läuft über die App |
| KPIs und Auslöser D unerreichbar (3 Einwände) | eingearbeitet: zwei Leitern, ST4 nicht im gleitenden Mittel, Schattenportfolio, Platz 8–9 an Pfad B gebunden |
| Punkteband 600–680 zu optimistisch | eingearbeitet: 595–630, zweiter Starter eigene Zeile, Ache als Geldentscheidung |
| Replay: Regeln hätten Rang 12 nicht verlassen | eingearbeitet in Kurzfassung, Abschnitt 2 und 8 |
| Marktwert-Historie: Fehldiagnose | eingearbeitet: wöchentliches Voll-Nachladen, erster Lauf in Woche 1, Schwellen danach neu rechnen |
| Faller-Weiche kaum berechenbar (6 %) | eingearbeitet: Nachladen vor der Preisregel; „unbekannt" gilt nicht als Nicht-Faller |
| Kein Alarm auf die Worst-Case-Deckung | eingearbeitet im Ergebnis-Wächter |
| Alarme widersprechen dem Gleitpfad und wären wochenlang rot (2 Einwände) | eingearbeitet: Bindung an den Gleitpfad, eine Karte je Zustandswechsel, eigener Job, Alarmtext nennt die erlaubte Handlung |
| Teilmengen-Notbremse macht Olise ab 21,65 M zur ersten Wahl | eingearbeitet: 25-%-Klausel, Tabelle für E3. Abweichung vom Entwurf: erst Schattenlauf (Paket 2), live in Paket 3 — eine „Einlaufzeit" übt einen Pfad nicht, der nur im Ernstfall läuft |
| Deploys aus dem geteilten Verzeichnis ohne Git-Prüfung | eingearbeitet: Prozessregeln ab sofort, Git-Tor in Paket 1, Stash-Entscheid vorgezogen |
| Absender-Pflicht kann Emils Veto blockieren | eingearbeitet: ein Veto wird nie abgelehnt; fehlender Absender wird markiert und gemeldet |
| Gebotsaufgabe über den Deckungspfad taugt nicht für gedeckte Gebote | eingearbeitet: eigenes dünnes Werkzeug auf dem manuellen Storno-Pfad, nur vollständige Aufgabe mit Sperre |
| Platzpuffer T-5 gegen letzten Aufstellungslauf T-8; T-130 zu knapp | eingearbeitet: T-12 und T-150 |
| „extern"-Zeilen verfälschen Caps und Beweisregel | eingearbeitet: eigener Status, zählt nirgends als ausgeführt |
| Sperrliste schlösse auch stehende Kickbase-Gebote aus | eingearbeitet: nur Veto, Fehlschlag, Aufgabe |
| Nachladen verlängert die Aufstellungspfade | eingearbeitet: nur im Planer-Pfad, Zeitbudget 10 s |
| Zwei Begründungen falsch (Floor-Tests, Verkaufsvorhaben) | eingearbeitet |
| Fehlende Minutendaten: „nie sperren" gegen „kein Auto-Kauf" | aufgelöst: kein autonomer Starterkauf ab 5 M ohne Beleg (das einzige finanzierte Geld darf nicht in eine rollenblinde Wahl gehen; die Pause bietet rund 9 neue lebende Kandidaten je Tag), aber Nachladen, nie Null-Bewertung, Ausnahme Füller und Pflichtplatz ab T-72 h |
| × 1,15 für den einen finanzierten Pflichtkauf von Beginn an | zurückgewiesen: in der Pause sind rund 6 Versuche nacheinander möglich (Risiko ohne Zuschlag rund 3 %), jeder Aufschlag ist sicher verloren; die Eskalation bis × 1,17 ab T-72 h bleibt. Übernommen: Zuschlagsziel 40 % statt 50 %, Band für Kauf 1 geweitet |
| Füller-Regel: Kappe bindet nie, „+100 sicher" stimmt nicht | eingearbeitet: absolute Kappe, früher Start mit KB-3-Feldspielern |
| Preisleiter 10–20 M nicht kalibriert; Marktangebot messbar | eingearbeitet: Spanne 1,08–1,10, Szenario B mit 14–16 Versuchen, Band 5,0–9,0 M, Auslöser D geschärft |
| E2-Ausnahme für Emils Manager-Gebote ist leer | eingearbeitet: gestrichen |
| Olise-Klumpen ohne Meldepfad | eingearbeitet: meldende Karte |
| Entscheidungslast, keine Defaults | eingearbeitet mit Einschränkung: Defaults nur für reine Verschärfungen; Richtungsentscheide bleiben ohne Default |
| Tauschverkäufe „bleiben" gegen „aus" (zwei Prüfer uneins) | aufgelöst: aus bis Replay und Trockenlauf, dann Emils Entscheid; vor ST6 wird der Pfad nicht gebraucht |

Unverändert aus dem Entwurf übernommen: Alarme im eigenen Wächter statt als fünfte Anpfiff-Stufe; Verkaufs-Floor T-96 h statt zustandslos oder 7 Tage; Leiter nach Preisklasse statt pauschal +10 %; Starterregel 2 von 2 mit Rückfall 2 von 3; 96-h-Füllerfenster bleibt bis zum ST5-Review; Inhaberkanal nur Discord-Tap; Eingriffe an der Schreibstelle erst nach ST5; 8eed081 nicht still ratifizieren; keine Handelstasche, keine harten Trend-Tore; Ausgangsniveau 490–510.

## Anhang B: Umbauliste kompakt

**Paket 1 (Di 22.09.)**
- P0-1 Plan: Manager-Listings ablehnen; Manager-Gebote reservieren Geld, zählen keinen Platz; nur vetoed, failed und aufgegebene Ziele vor dem Optimierer ausschließen, stehende Kickbase-Gebote bleiben gebunden. Record nötig.
- P0-2 Plan: Listingart je Beleg aus dem Verkäufer; Verkäufer und Listingart auch für abgelehnte Kandidaten speichern.
- P0-3 Plan und Papier-Engine: Planungsgeld = Konto − Gebote − 10.000 €; Einzelrahmen 1,0 bei Elf-Lücke, 0,70 bei Upgrade; Guardrail-Default bleibt 0,70. Zwei Records.
- P0-4 Neuer Job „Ergebnis-Wächter": Gleitpfad-Alarme, Deckung unter Puffer sofort, Konto unter Puffer länger als 24 h, Statuswechsel bei Klumpenspieler; eine Karte je Zustandswechsel über den Blocker-Kanal. Record nötig.
- P0-5 Plan: minimaler Rollenfilter ab 5 M (KB ≤ 2, ≥ 60 min in beiden letzten beobachteten Spieltagen; sonst „Rollenbeleg fehlt"). Record nötig.
- P0-6 Kommentare zu emil_go richtigstellen, Sniper-Text streichen, drei Jobs abmelden.
- P0-7 Doku, Skill, Hermes-Skript, Memory: Pauschalmandate archivieren oder ersetzen; CLAUDE.md und Memory nur mit Emils Einverständnis.
- P0-8 API: Absender-Stolperdraht (Hermes, Codex, Unbekannte → 403; leer bleibt vorerst „mcp"); Veto-Route unverändert offen.
- P0-9 Deploy-Werkzeug: verweigert bei schmutzigem Arbeitsbaum oder ungepushtem HEAD; Deploy-Bericht mit Commit-Hash.
- P0-10 Wöchentliches Voll-Nachladen von Marktwerten und Leistungen aller bekannten Spieler, gedrosselt, außerhalb des Sperrfensters; erster Lauf in Woche 1.

**Paket 2 (Di 29.09.)**
- P1-1 Gebot vor dem Freeze: Ausführung T-150, Platzpuffer 12 min, verworfene Entwürfe mit Grund speichern. Record nötig.
- P1-2 Preisregel (E1). P1-2a Knopf-Beträge und Regelpreis als geprüfte Szenarien, Bestandsschutz für angepasste Beträge, Meldung bei Verfall. P1-2b lebende Elf als Steuergröße (E12). Records nötig.
- P1-3 Voller Rollenfilter mit Rückfall 2 von 3; Nachladen nur im Planer-Pfad, Zeitbudget 10 s. Record nötig.
- P1-4a Notbremse im Schattenlauf: alte und neue Vorauswahl nebeneinander als Bericht; Raster-Replay 1–40 M für E3.
- P1-5 Verkaufskarte statt Autoverkauf; Engine-Verkaufsvorschläge speisen nur Karten.
- P1-7 „extern"-Zeilen für App-Trades mit eigenem Status. P1-8 Review-Pflichtfelder in den Decision-Records.
- P1-14 Abgangspfade: Tabelle „bleibt / aus / Emil-Klick", Annahme nur per Klick (E13), kein selbst erzeugtes emil_go, jedes Angebot melden, Inventar-Test. Record nötig.
- P1-15 Messung: Schattenportfolio, Writes je Absender, Marktangebot wöchentlich (nur lesend).

**Paket 3 (Di 13.10.)**
- P1-4b Teilmengen-Notbremse live mit 25-%-Klausel, alte Kaskade als Rückfall, Permutations-Invariante (E3). Versiegelter Wächter-Test muss unverändert grün bleiben.
- P1-9 Wiedereinstiegssperre nach jedem Storno. P1-10 Verkaufs-Floor T-96 h samt Annahmen; Fenster-Konstante unter 10 Tagen dokumentiert. Versiegelte Executor-Tests vorher einzeln laufen lassen.
- P1-11 Absender-Feld für alle Routen; fehlend = „unknown" plus Alarm; Veto nie abgelehnt; strukturierter Auftrag (Spieler, Aktion, Wortlaut, Zeit).
- P1-12 Werkzeuge Veto und Gebotsaufgabe (eigener manueller Pfad, Sperre bis Listing-Ende), Lesefehler beheben, Fehler statt stiller Lokaldaten.
- P1-13 Siegel-Hash-Prüfung im Deploy (E11); Neu-Siegeln verweigert nicht-interaktive Aufrufe.

**Ab Di 20.10. (P2)**
- P2-1 Inhaber-Auftrag per Discord-Tap. P2-2 Aufarbeitung 8eed081 (versiegelt, nur Emil). P2-3 Füller-Bedingung statt 96-h-Fenster. P2-4 Trend als Kostenposition. P2-5 Prognose-Kandidat v2 über promote. P2-6 Werkzeuge 23 → 12–14, Jobs ≤ 20. P2-7 Marktsnapshots und Kontoverlauf. P2-8 eine Regelstelle. P2-9 Rückbau der Altpfade (versiegelt, nur nach Emils Neu-Siegel). P2-10 KPI-Bericht mit zwei Leitern.

**Betriebshinweis außerhalb der Studie:** Die MCP-Server figma, neon, plugin:cloudflare:cloudflare und supabase verlangen eine Autorisierung. Das geht nur interaktiv (/mcp bzw. die claude.ai-Connector-Einstellungen); bis dahin sind sie nicht nutzbar. Für diese Arbeit wurden sie nicht gebraucht.
