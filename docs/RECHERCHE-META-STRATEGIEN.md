# Meta-Recherche: Was die besten Kickbase-Manager anders machen

> **Zweck.** Emils Auftrag vom 26.08.2026: „Such dir die besten Kickbase-Spieler
> weltweit und leite daraus die besten Strategien ab." Dieses Dokument sammelt,
> was außerhalb unseres Systems über Kickbase bekannt ist — sortiert nach Thema,
> jede Erkenntnis mit Quelle, Einstufung und einer Zeile „Was das für uns heißt".
>
> **Einstufung — die einzige Regel, die hier zählt:**
> - **BELEGT** = offizielle Kickbase-Quelle (help.kickbase.com, Punktetabelle)
>   ODER mindestens zwei unabhängige Quellen, die sich nicht voneinander abschreiben.
> - **GESTÜTZT** = zwei Community-Quellen gleicher Herkunft, plausibel, aber ohne
>   offizielle Bestätigung.
> - **HYPOTHESE** = eine Quelle, Meinung, Selbstauskunft eines Anbieters, oder
>   unsere eigene Rechnung auf Basis belegter Zahlen.
> - **STRITTIG** = Quellen widersprechen sich oder widersprechen unserer Empirie.
>
> Bei Widersprüchen gilt unsere Rangfolge: **LigaInsider > Presse > Community.**
> Nichts aus diesem Dokument ist automatisch eine Regel. Was Regel werden soll,
> steht in Abschnitt 8 als Umsetzungsvorschlag — entscheiden Emil und der
> Orchestrator.
>
> Stand: 26.08.2026 · Recherche per WebSearch/WebFetch · nächster Turnus: 26.09.2026

---

## 0. Die fünf wichtigsten Funde in einem Absatz

1. **Die offizielle Punktetabelle ist beschaffbar** — und sie sagt Dinge, die wir
   in keinem Signal abbilden: Der Zu-Null-Bonus zählt **pro 10 Minuten** und ist
   positionsabhängig (Abwehr 30 Punkte für ein Vollspiel, Torwart 50), Sieg und
   Niederlage sind **±15**, jedes Gegentor auf dem Platz **−5**.
2. **Punkte hängen an der TATSÄCHLICHEN Position im Spiel**, nicht an der
   Kickbase-Position. Unsere gesamte Positions-Logik rechnet mit dem falschen Feld.
3. **Die Erfolgs-Prämien sind wiederholbar und zahlen für Ausreißer**, nicht für
   Verlässlichkeit: 500 Punkte von einem Spieler = 2 Mio. €. Unser Kauf-Score
   bestraft ausdrücklich „tor-abhängige Ausreißer".
4. **Der Marktwert entsteht in der gesamten Kickbase-Community über alle Ligen** —
   wir können ihn nicht bewegen, und der MW-Trend ist das einzige beobachtbare
   Aggregat dieser Nachfrage.
5. **Unsere 103-%-Annahme für Computer-Angebote ist nicht belegt** und steht gegen
   zwei externe Angaben (±1 % bzw. +1–10 %). Sie steckt als harter Parameter im
   Verkaufspfad.

---

## 1. Marktwert-Mechanik

### 1.1 Update-Zeitpunkt: täglich gegen 22:00 (Seasonal)
**BELEGT** — help.kickbase.com/help/kickbase-marktwert. Im Seasonal-Modus täglich
gegen 22:00 Uhr; in Challenges und Arena wöchentlich montags 22:00.
Von ligabase.de und buli-tipphilfe.de unabhängig wiederholt.

*Was das für uns heißt:* Bestätigt unseren Taktgeber. Snapshot 22:05, Briefing
22:15, Schatten-Scan 22:20 sitzen richtig. Die Closing-Bell 21:33 für
Langzeitverletzte ist mechanisch korrekt begründet: nach 22:00 ist der alte Preis weg.

### 1.2 Der Marktwert entsteht community-weit über ALLE Ligen
**BELEGT** — help.kickbase.com („wie gefragt er in der gesamten Kickbase Community
ist"); ligabase.de präzisiert „über alle Ligen hinweg – und nur indirekt nach der
tatsächlichen Leistung".

*Was das für uns heißt:* Der wichtigste konzeptionelle Fund. **Wir können den
Marktwert nicht beeinflussen** — unsere zwölf Manager sind statistisches Rauschen.
Daraus folgt dreierlei: (a) Ein Kauf von uns treibt den Preis nicht, „Kaufdruck
erzeugen" ist keine Strategie. (b) Der MW-Trend ist nicht Rauschen, sondern das
aggregierte Urteil von hunderttausenden Managern — er verdient mehr Gewicht als
0,15. (c) Community-Tipps aus YouTube/Reddit sind **Frühindikatoren echter
Nachfrage**, weil dieselben Videos die Masse bewegen, die den MW macht.

### 1.3 Die drei Treiber, in dieser Reihenfolge
**BELEGT** — help.kickbase.com nennt: (1) Transfermarkt-Aktivität (häufig gekauft →
steigt; häufig verkauft/gelistet → fällt), (2) Leistung und Form, (3) Verfügbarkeit
(Verletzung, Sperre, Trainings-/Pressehinweise).

*Was das für uns heißt:* Nachfrage steht VOR Leistung. Ein Spieler mit guten Punkten,
über den niemand redet, steigt nicht. Unser Trend-Signal misst genau den
Nachfrage-Kanal — es misst damit die Ursache, während `base_points` und `form` die
Begleitumstände messen.

### 1.4 Werte laufen über mehrere Tage, nicht in einem Sprung
**GESTÜTZT** — ligabase.de: „Marktwerte bewegen sich meist nicht in einem einzigen
Sprung, sondern über mehrere Tage zunehmend. Wer einen Aufwärtstrend früh erwischt,
nimmt die volle Steigerung mit." BaseXI baut genau darauf eine 14-Tage-Balkenansicht,
„ob ein Anstieg gleichmäßig läuft oder von einzelnen Tagen getragen wird".

*Was das für uns heißt:* Momentum ist real und mehrtägig. Ein gleichmäßiger Anstieg
ist ein besseres Kaufsignal als ein Einmal-Sprung (der ist meist schon der Peak).
Unser 7-Tage-Trend misst nur die Steigung, nicht die **Gleichmäßigkeit** —
Verbesserungsvorschlag 8.5.

### 1.5 Maßgeblich ist der Marktwert zum TRANSFER-Zeitpunkt
**BELEGT** — help.kickbase.com: „Der maßgebliche Marktwert ist derjenige zum Zeitpunkt
des Transfers, nicht zum Zeitpunkt der Gebotsabgabe. Da Marktwerte täglich
aktualisiert werden, kann Ihr Gebot nachträglich unter Marktwert fallen und abgelehnt
werden."

*Was das für uns heißt:* Bestätigt unsere zentrale Underpay-Sperre im Preflight und
den Overpay-Puffer. Die Regel ist offiziell, nicht Folklore.

### 1.6 Untergrenze 500.000 €
**HYPOTHESE** (giga.de, eine Quelle): Marktwerte fallen nicht unter 500.000 €.

*Was das für uns heißt:* Wenn es stimmt, ist ein 500-k-Spieler ein Asset ohne
Abwärtsrisiko im Preis — nur Kaderplatz kostet. Für Restposten-Käufe relevant.
Vor Nutzung an unseren eigenen MW-Historien prüfen (billigster je beobachteter MW).

### 1.7 Was NICHT bekannt ist
Es gibt **keine veröffentlichte Formel**, keine belegte Obergrenze der
Tagesänderung, keine bekannte Gewichtung von Käufen gegen Verkäufe. Alle
kursierenden Prozentregeln („max. 5 % pro Tag" o. ä.) fanden sich in keiner Quelle.
*Was das für uns heißt:* Jede Aussage über „wie viel" der Marktwert sich bewegt,
muss aus unseren eigenen Snapshots kommen — nicht aus dem Netz.

---

## 2. Punkte-Mechanik — der größte Fund

Die vollständige offizielle Punktetabelle ist über `kickbase.com/points-table`
abrufbar (die deutsche Seite antwortet auf Bots mit 403; die identische Tabelle
liegt auf `us.kickbase.com/en-us/points-table`). **Unabhängige Gegenprobe:**
fussball-orakel.de/kickbase_points.php führt dieselbe Tabelle auf Deutsch — alle
gemeinsamen Werte stimmen exakt überein (Tore 120/100/90/80, Vorlagen 55/45/35,
Zu Null Torwart 50, Elfmeter verwandelt 80, Elfmeter gehalten 100, Startelf 5,
Ballgewinn 5, Geklärt 5). Damit: **BELEGT**.

Datengrundlage laut help.kickbase.com: **Stats Perform / Opta**, über 95 bewertete
Aktionen, 1.600–1.900 Events je Spiel, mindestens zwei Analysten live plus Checker
und Supervisor.

### 2.1 Positionsabhängige Kernwerte
| Aktion | TW | ABW | MIT | STU |
|---|---:|---:|---:|---:|
| Tor | 120 | 100 | 90 | 80 |
| Vorlage | 55 | 45 | 35 | 35 |
| Tor eingeleitet | 45 | 35 | 25 | 25 |
| **Zu Null, je 10 Min** | **5** | **3** | **2** | **1** |
| Zu Null, Vollspiel-Bonus | 5 | 3 | 2 | 1 |
| **Zu Null gesamt (90 Min)** | **50** | **30** | **20** | **10** |

Zu-Null-Bedingung (**BELEGT**, help.kickbase.com): mindestens **eine Halbzeit**
Spielzeit; maßgeblich ist „die Position, die der Spieler am längsten während des
Spiels innehatte"; er geht an **alle** verteidigenden Spieler, nicht nur den Torwart.

### 2.2 Team- und Einsatz-Punkte (positionsunabhängig)
| Aktion | Punkte |
|---|---:|
| Spiel gewonnen | +15 |
| Spiel verloren | −15 |
| Teamtor, während auf dem Platz | +5 |
| Gegentor, während auf dem Platz | −5 |
| Startelf | +5 |
| Eingewechselt | +2 |
| Gespielte Minuten | +1 je 10 Min |

### 2.3 Zweikampf- und Ballaktions-Ökonomie
| Aktion | Punkte | | Aktion | Punkte |
|---|---:|---|---|---:|
| Zweikampf gewonnen | +5 | | Ballgewinn | +5 |
| Zweikampf verloren | −1 | | Pass abgefangen (Strafraum) | +3 |
| Kopfball gewonnen | +3 | | Pass abgefangen (außerhalb) | +1 |
| Kopfball verloren | −1 | | Geklärt (im Strafraum) | +5 |
| Zweikampf klar verloren | −3 | | Geklärt (außerhalb) | +3 |
| Wichtiger Tackle | +15 | | Klärung auf der Linie | +15 |
| Schuss geblockt (5 m) | +15 | | Schuss geblockt (Strafraum) | +10 |
| Schuss geblockt (außerhalb) | +5 | | Flanke geblockt | +2 |
| Pass ins letzte Drittel | +1 | | Pass in gegn. Hälfte | +1 |
| Präziser langer Ball | +1 | | Erfolgreiche Flanke | +3 |
| Ballverlust | −1 | | Ball zu weit gespielt | −1 |

### 2.4 Torabschluss und Standards
Torschuss aufs Tor **+12** · geblockter Schuss aufs Tor **+8** · Pfosten/Latte **+10** ·
Schuss knapp vorbei **+10** · Schuss weit vorbei **+5** · Großchance vergeben **−15** ·
Großchance kreiert **+15** · Torschuss-Vorlage **+5** · Todespass **+15** ·
Elfmeter verwandelt **+80** · Elfmeter gehalten **+100** · Elfmeter herausgeholt **+30** ·
Elfmeter verschossen **−60** · Elfmeter verschuldet **−45**.

### 2.5 Torwart-spezifisch
Großchance pariert **+15** · Flugparade **+15** · Ball im Zweikampf erobert **+15** ·
Schuss pariert **+10** · Herauslaufen erfolgreich **+10** · Flanke abgefangen **+5** ·
Distanzschuss pariert **+5** · Faustabwehr **+5** · Flanke nicht gefangen **−5** ·
Rückpass-Foul **−5**.

### 2.6 Strafen
Gelbe Karte **−10** · Gelb-Rot **−50** · Eigentor **−60** (fussball-orakel nennt −75 —
**STRITTIG**, Wert vor Nutzung prüfen) · Fehler vor Gegentor **−45** ·
Fehler vor Torschuss **−15** · Foul **−2** · Handspiel **−2** · Abseits **−3** ·
falscher Einwurf **−5**.

### 2.7 Die Punkte richten sich nach der tatsächlichen Position im Spiel
**BELEGT** — help.kickbase.com/help/spielerposition, wörtlich: „Die Position, unter
der ein Spieler bei Kickbase geführt wird, bestimmt lediglich, auf welcher Position
du ihn in deinem Team aufstellen kannst." Und: „Die Punkte der Spieler:innen basieren
immer auf ihrer tatsächlichen Position im jeweiligen Spiel."

*Was das für uns heißt:* **Das ist ein Modellfehler bei uns**, siehe 7.2 (b). Es
entwertet außerdem den in Blogs kursierenden „Positions-Hack" (Spieler kaufen, die
defensiver geführt sind als sie spielen) — das war einmal richtig, ist es laut
offizieller Regel heute nicht mehr. **STRITTIG zugunsten der offiziellen Quelle.**

### 2.8 Was aus der Tabelle folgt (unsere Rechnung, HYPOTHESE)
Ein Abwehrspieler, dessen Team **gewinnt und zu Null spielt**, hat vor der ersten
eigenen Aktion: 15 (Sieg) + 30 (Zu Null) + 5 (Startelf) + 9 (90 Min) = **+59 Punkte**.
Derselbe Spieler bei einer 0:2-Niederlage: −15 − 10 + 14 = **−11 Punkte**.
**Spanne ≈ 70 Punkte, rein team-getrieben, ohne dass der Spieler etwas tut.**
Torwart bei Sieg zu Null: 15 + 50 + 14 = **+79**. Stürmer bei Sieg zu Null: 15 + 10 + 14 = **+39**.

Für einen ballführenden Sechser mit 60 Pässen in die gegnerische Hälfte, 14
gewonnenen und 6 verlorenen Zweikämpfen, 4 Ballgewinnen: 60 + 70 − 6 + 20 = **+144**
aus reiner Grundlast — ohne Torbeteiligung. Das erklärt die Community-Weisheit
„Rohpunkter schlagen Scorer" **mechanisch**.

---

## 3. Transfermarkt, Gebote, Zuschlag

### 3.1 Bei Gleichstand gewinnt das frühere Gebot
**BELEGT** — help.kickbase.com: „Bei identischen Geboten erhält den Zuschlag, wer
zuerst geboten hat."
*Was das für uns heißt:* Bestätigt unsere Leiter-Logik (früh ein niedriges Erstgebot
setzen) und den krummen Psycho-Offset.

### 3.2 Ein Gebot scheitert auch ohne Konkurrenz
**BELEGT** — help.kickbase.com nennt drei Gründe: höheres Gebot eines anderen,
**voller Kader oder Vereinslimit überschritten (offene Gebote zählen mit)**, Gebot
unter Marktwert zum Transferzeitpunkt.
*Was das für uns heißt:* Unser Vereinslimit-Guardrail inklusive offener Gebote ist
offiziell begründet, nicht vorsorglich.

### 3.3 Underpay: erlaubt, aber nachfrageabhängig
**BELEGT** (help.kickbase.com): „Ein Gebot unter dem aktuellen Marktwert ist
grundsätzlich möglich", ohne Garantie, Chancen sinken bei gefragten Spielern.
Empfehlung: mindestens den aktuellen Marktwert bieten.

**STRITTIG:** giga.de behauptet, Gebote **mehr als 10 % unter Marktwert seien gar
nicht absendbar**. Das ist mit unserer eigenen Empirie unvereinbar: Das
Friedrich-Gebot vom 21.08. lag bei **81 % MW auf einem Kickbase-Listing** und wurde
angenommen (es verfiel nur ohne Zuschlag). Ein Forum (sportforen.de) berichtet
ebenfalls die 10-%-Grenze, aber mit dem Zusatz „100–200 k drunter klappt meistens".
*Bewertung:* Die 10-%-Schranke gilt vermutlich für **Manager-Listings** — dort haben
wir mit err 5080 „UnderpayNotAllowed" die härtere Regel (≥ 100 % MW) selbst gemessen.
Für **Kickbase-Listings** gilt sie in BASE nachweislich nicht.
*Was das für uns heißt:* Unsere zweigeteilte Regel (Manager ≥ 100 % MW, Kickbase
90–97 % als Sniper) bleibt richtig — sie ist genauer als die Netz-Quellen.

### 3.4 Computer-Angebote auf eigene Listings
**STRITTIG — und das betrifft einen unserer Parameter.**
- giga.de: automatische Kickbase-Angebote liegen „bei höchstens 1 Prozent über oder
  unter dem aktuellen Marktwert".
- Eine zweite Fundstelle nennt „1 bis 10 Prozent Aufschlag".
- Unsere Doktrin (LEARNINGS, Strategie, `accept_offer_floor = 1.03`) unterstellt
  **~103 %**.

*Was das für uns heißt:* Siehe 7.2 (a) — die Zahl ist nirgends belegt, und wenn die
untere Angabe stimmt, lehnt unser Floor systematisch jedes Computer-Angebot ab.
**Das ist aus unserem eigenen Audit messbar und muss gemessen werden, bevor wieder
jemand die 103 % zitiert.**

### 3.5 Angebote sind 72 Stunden gültig
**GESTÜTZT** — ligabase.de und wettboost.com nennen unabhängig 72 Stunden für
Kickbase-Angebote auf eigene Listings.
*Was das für uns heißt:* Ein Listing ist kein Sofort-Erlös. Wer 72 h vor Anpfiff
listet, um Liquidität zu schaffen, hat sie im Zweifel nicht rechtzeitig — genau der
Grund, warum unser T−48-h-Exposure-Gate mit Worst-Case-Deckung rechnet. Bestätigt.

### 3.6 Verkaufen: Mindestpreis ≥ 90 % MW, Sofortverkauf = 100 % MW
**BELEGT** — help.kickbase.com/help/kickbase-transfermarkt: Listing mit Mindestpreis,
Empfehlung „mindestens 90 % des Marktwerts"; Sofortverkauf schreibt „sofort den
aktuellen Marktwert" gut.
*Was das für uns heißt:* Bestätigt `listing_floor = 0.95` als konservativ-vernünftig
und den Sofortverkauf als Sekunden-Liquidität zum vollen MW.

---

## 4. Prämien und Erfolge — die vergessene Ertragsquelle

Alle Werte **BELEGT** über help.kickbase.com/help/welche-erfolge-kann-ich-in-der-app-erhalten
und .../staffelung-auflaufpramie.

### 4.1 Auflaufprämie (täglicher Login-Bonus)
Tag 1–9: **10.000 € bis 90.000 €** (+10.000 je Tag). **Ab Tag 10: konstant 100.000 €.**
Ein verpasster Tag setzt **Streak und Prämie auf Tag 1** zurück. Ein Liga-Reset setzt
die Prämie auf Tag 1, die Streak-Zählung bleibt. Andere Ligen (nicht Bundesliga)
zahlen entsprechend weniger.

*Was das für uns heißt:* Erklärt endlich unsere eigene Beobachtung (BASE Tag 6 = 50 k,
Liga „Base" Tag 7 = 60/70 k) — die Staffel ist einfach Tag × 10 k, und die
Liga-Resets erklären die Verschiebung. **Ein verpasster Tag kostet rund 450.000 €**
(Wiederaufstieg 10+20+…+90 statt 9 × 100). Damit ist der Prämien-Job in Euro
beziffert: ~3 Mio. € pro Monat, ~9 Mio. € pro Saison, wenn die Kette nie reißt.

### 4.2 Wiederholbare Spieltags-Erfolge
| Erfolg | Bedingung | Prämie |
|---|---|---:|
| Spieltagssieger | Spieltagssieger der Liga werden | 1.000.000 € |
| Spieltagspunkte Silber | ≥ 1.000 Punkte an einem Spieltag | 250.000 € |
| Spieltagspunkte Gold | ≥ 1.500 Punkte an einem Spieltag | 500.000 € |
| Jahrhundertspiel | ≥ 2.000 Punkte an einem Spieltag | 1.000.000 € |
| Topscorer | ≥ 200 Punkte von **einem** Spieler | 100.000 € |
| Matchwinner | ≥ 300 Punkte von **einem** Spieler | 500.000 € |
| Weltklasse | ≥ 400 Punkte von **einem** Spieler | 1.000.000 € |
| Fussballgott | ≥ 500 Punkte von **einem** Spieler | 2.000.000 € |
| MVP | den stärksten Spieler des Spieltags besitzen | 1.000.000 € |
| Tormaschine | die meisten Tore der Liga | 250.000 € |

### 4.3 Wiederholbare Transfer-Erfolge („Händchen")
| Erfolg | Bedingung | Prämie |
|---|---|---:|
| Bronzenes Händchen | 3 Mio. € Gewinn mit **einem** Spieler | 250.000 € |
| Silbernes Händchen | 5 Mio. € Gewinn mit einem Spieler | 500.000 € |
| Goldenes Händchen | 10 Mio. € Gewinn mit einem Spieler | 1.000.000 € |
| Königstransfer | 25 Mio. € Gewinn mit einem Spieler | 2.000.000 € |

Auszahlung: Spieltagsprämien automatisch mit den finalen Punkten; Erfolge meist
Montagabend oder in der Nacht darauf. Für den Teamwert-Erfolg muss das Konto im
Plus sein.

*Was das für uns heißt:* Zwei harte Konsequenzen.
**(1) Die Prämien belohnen Extreme, nicht Erwartungswerte.** Ein einzelner
500-Punkte-Spieltag ist 2 Mio. € wert — mehr als die meisten unserer Trades im
ganzen Monat. Unser Kauf-Score bewertet ausdrücklich das **untere Quartil**
(`base_points`) und dämpft „tor-abhängige Ausreißer". Für die Punkte-Tabelle ist
das richtig, für die Prämien-Tabelle ist es falsch.
**(2) Die Transfer-Prämien haben Schwellen.** Ein Verkauf bei 2,9 Mio. € Gewinn
verschenkt 250.000 €; bei 9,8 Mio. € verschenkt er 1 Mio. €. Der Verkaufs-Trigger
kennt diese Schwellen heute nicht (siehe 7.2 (e)).

---

## 5. Spieltag, Aufstellung, Konto

### 5.1 Eine Deadline: der Anpfiff des ersten Spiels
**BELEGT** — help.kickbase.com (regel-4-kontostand-aufstellung, kontoimminus).
Die Community konkretisiert: **Änderungen ab 20:29 Uhr wirken erst zum nächsten
Spieltag** — es zählt die offiziell angesetzte Zeit, nicht der Schiedsrichterpfiff.
*Was das für uns heißt:* Unsere Wächter-Kaskade (T−120 Storno, T−60 Ping, T−35
Aufstellung, T−15 Notbremse) hat mit T−35 genug Luft. Die 20:29-Angabe ist ein
Argument, T−15 nicht weiter nach hinten zu schieben.

### 5.2 Konto im Minus zum Anpfiff = null Punkte für den ganzen Spieltag
**BELEGT** — help.kickbase.com/help/kontoimminus.
*Was das für uns heißt:* Unverändert die teuerste Einzelregel des Spiels. Purge und
Notbremse bleiben unantastbar.

### 5.3 33-%-Regel
**BELEGT** (ligabase.de mit Rechenbeispiel, deckt sich mit help.kickbase.com):
Nach dem Anpfiff des ersten Spiels darf das Konto bis **33 % des angepassten
Mannschaftswerts** ins Minus. Beispiel: 100 Mio. Teamwert, −10 Mio. Konto ⇒
angepasst 90 Mio. ⇒ Untergrenze −30 Mio.
*Was das für uns heißt:* Deckt sich exakt mit `overdraft_floor`. Wichtig ist die
Formulierung „**angepasster** Mannschaftswert" — der Overdraft schrumpft die Basis.

### 5.4 Kaderbegrenzung 11–25, vom Liga-Admin gesetzt
**BELEGT** — help.kickbase.com/help/kaderbegrenzung. Bei Überschreitung sind keine
Käufe mehr möglich, bis wieder verkauft wurde.
*Was das für uns heißt:* Wir sollten das Limit genauso live lesen wie `lpc`
(Vereinslimit) — heute steht es nirgends explizit im Guardrail.

### 5.5 Nachholspiele und englische Wochen
help.kickbase.com führt einen eigenen Artikel „Nachholspiele / abgesagte Spiele".
**Nicht ausgewertet** — unser Anpfiff-Planer liest die echten Kickoff-Zeiten und ist
davon vermutlich abgedeckt; für den Turnus im September vorgemerkt.

---

## 6. Was die Community-Profis anders machen

Diese Erkenntnisse stammen aus Guides, Blogs und Podcasts. Sie sind Meinung, nicht
Mechanik — aber mehrere von ihnen sind durch die Punktetabelle aus Abschnitt 2
mechanisch erklärbar, was sie deutlich glaubwürdiger macht.

### 6.1 Anker-Strategie: 1–3 Top-Stars statt elf Durchschnittsspieler
**GESTÜTZT** — wettboost.com („Investiere in einen Top-Star mit 50 Mio. € oder mehr,
fülle den Rest mit 500-k-Spielern"), web.de/1&1 („Du brauchst neben Talenten,
Edeljokern, Bundesliga-Dinos auch Top-Spieler wie Olise, Kane oder Kimmich" —
mindestens einer, besser zwei aus den Top 15–20), buli-tipphilfe.de.
*Was das für uns heißt:* Steht in Spannung zu „Kohle in die Defensive" und zu unserer
Einschätzung, Kimmich sei „zu teuer für die Arbitrage-Phase". Die Prämien-Tabelle
(4.2) gibt der Anker-Strategie einen harten ökonomischen Grund, den unsere
Bewertung nicht kennt. Olise ist faktisch bereits unser Anker — das war aber eine
Einzelentscheidung, keine Doktrin.

### 6.2 Rohpunkter schlagen Scorer
**GESTÜTZT + mechanisch belegt** — buli-tipphilfe.de („Investiere in Spieler mit
häufigen Ballkontakten"), wettboost.com, Punktelieferanten-Podcast
(„ballsichere Mittelfeldspieler und aufbaustarke Innenverteidiger sammeln
kontinuierlich Punkte, nicht nur über Tore und Vorlagen"). Rechnung in 2.8.
*Was das für uns heißt:* Bestätigt `BASE_POINTS_WEIGHT = 0.40` und die
Khedira/Schäfer/Ulrich/Højlund-Achse. Unsere Doktrin liegt hier richtig.

### 6.3 Standard-Ausführer sind der größte Einzelhebel
**HYPOTHESE (eine Quelle) + mechanisch plausibel** — wettboost.com nennt Freistöße,
Ecken und Elfmeter „Cheat-Codes". Mechanik: Elfmeter verwandelt +80, Großchance
kreiert +15, Todespass +15, erfolgreiche Flanke +3, Torschuss-Vorlage +5 — ein
Eckenschütze sammelt pro Spiel 6–10 Flanken und mehrere Torschuss-Vorlagen.
*Was das für uns heißt:* Wir haben **kein** Standard-Merkmal in den Daten. Siehe
Vorschlag 8.4.

### 6.4 Torwart-Paradox: starke Gegner sind gut für Torhüter
**HYPOTHESE** (web.de/1&1: „Torhüter profitieren besonders gegen starke Gegner durch
mehr Paraden"). Mechanisch plausibel: Schuss pariert +10, Flugparade +15, Großchance
pariert +15 — aber Gegentor −5 und kein Zu Null wirken dagegen.
*Was das für uns heißt:* Unser `matchup_signal` bewertet den Torwart eines
Außenseiters genauso ab wie dessen Stürmer. Das ist mindestens fragwürdig — siehe
Vorschlag 8.1.

### 6.5 Core-Team ~13, Rest sind Handels-Slots
**HYPOTHESE** (offtopic.online: „Core Team von 13 Spielern, davon zwei zum Rotieren.
Den Rest benutzt ihr, um Geld zu generieren"; ein Ligenregelwerk nennt 14 als
angenehmste Kadergrenze).
*Was das für uns heißt:* Deckungsgleich mit unserer S11-Kern-plus-Trading-Slots-Idee.
Keine Änderung nötig, aber eine externe Bestätigung der Aufteilung.

### 6.6 Antizyklisch kaufen, in die Euphorie verkaufen
**GESTÜTZT** — ligabase.de nennt es die wichtigste Trader-Heuristik.
*Was das für uns heißt:* Scheinbarer Widerspruch zu 1.4 (Momentum). Auflösung: Der
Katalysator (Ausfall des Stammspielers, Neuzugang, Comeback) kommt vor der Nachfrage.
Antizyklisch heißt „vor der Masse kaufen", Momentum heißt „die mehrtägige Welle
mitnehmen". Beides ist dieselbe Regel aus zwei Blickwinkeln — und beides beschreibt
genau unseren Profiteur-Scout.

### 6.7 Overpay nur bis zur Amortisation
**HYPOTHESE (eine Quelle, aber ungewöhnlich konkret)** — ein BaseXI-Nutzerbericht
beschreibt als erfolgreiche Vorgehensweise: Spieler mit **positivem Tagestrend
(~+225 k/Tag)** suchen und **maximal das 2- bis 3-Fache des Tagestrends** über dem
Marktwert bieten.
*Was das für uns heißt:* Das ist die eleganteste Overpay-Regel, die die Recherche
hergegeben hat, weil sie den Aufschlag an die **Amortisationsdauer** bindet statt an
einen festen Prozentsatz. Siehe Vorschlag 8.3.

### 6.8 Am Spieltags-Wochenende handeln
**HYPOTHESE** (web.de/1&1): Wenn die Rivalen aufs Spiel schauen statt auf den Markt,
entsteht ein Zeitfenster.
*Was das für uns heißt:* Unsere Puls-Jobs laufen 09/13/18 Uhr an allen Tagen gleich.
Ein Wochenend-Fokus ist billig zu testen, aber ohne Beleg — vormerken, nicht bauen.

### 6.9 LigaInsider-Trefferquote
**HYPOTHESE / Anbieter-Selbstauskunft** — LigaInsider nennt für die
Startelf-Prognosen **88,6 %**, basierend auf Trainingseindrücken, Pressekonferenzen
und einem Expertennetzwerk. Kickbase kooperiert offiziell mit LigaInsider (das Feld
`plpt: "Ligainsider"` aus unserem Member-Abo ist der Beleg dafür).
*Was das für uns heißt:* Wenn 88,6 % stimmen, sitzt bei elf gesetzten „Startern"
statistisch **etwa 1,2 Spieler pro Spieltag doch auf der Bank**. Unsere KPI
„11/11 LI-Starter" ist also eine Prognose-KPI, keine Ergebnis-KPI — wir sollten
sie nach dem Spieltag gegen die echten Aufstellungen zurückmessen.

### 6.10 Die verbreiteten Fehler (Konsens mehrerer Quellen)
Panikverkauf am Tiefpunkt · 33-%-Limit ausreizen ohne Exit-Plan · Vereins- und
Kaderlimit übersehen (Gebote platzen) · spekulative Freitagskäufe ohne Rückkaufplan ·
emotionale Käufe des Lieblingsvereins · elf Durchschnittsspieler statt Anker ·
Restprogramm ignorieren (Mittelfeld-Team mit vier Top-7-Gegnern in Folge).

---

## 7. Abgleich mit unserer Doktrin

Verglichen wurden `docs/STRATEGIE-SAISON-26-27.md`, `.claude/skills/kickbase/LEARNINGS.md`
und der Code in `api/src/kickbase_agent/` (Stand 26.08.2026).

### 7.1 (a) Was bestätigt wird

| Unsere Regel | Bestätigt durch | Status |
|---|---|---|
| Konto ≥ 0 zum Anpfiff, sonst null Punkte | help.kickbase.com | offiziell |
| Eine Deadline = Anpfiff des ersten Spiels | help.kickbase.com | offiziell |
| 33-%-Regel, offizielle Anstoßzeit zählt | help.kickbase.com, ligabase | offiziell |
| Marktwert-Update 22:00 als Taktgeber | help.kickbase.com | offiziell |
| Langzeitverletzte VOR 22:00 verkaufen | folgt aus 1.1 + 1.3 | mechanisch |
| Kurzzeitverletzte halten (Comeback-Anstieg) | ligabase, buli-tipphilfe | gestützt |
| Vereinslimit inkl. offener Gebote | help.kickbase.com | offiziell |
| Gleichstand: früheres Gebot gewinnt | help.kickbase.com | offiziell |
| Manager-Listings ≥ 100 % MW (err 5080) | eigene Empirie, genauer als das Netz | eigen |
| Overpay-Puffer wegen MW zum Transferzeitpunkt | help.kickbase.com | offiziell |
| Rohpunkte-Gewicht 0,40 („Sechser/IV") | Punktetabelle-Rechnung 2.8 + 3 Quellen | belegt |
| „Kohle in die Defensive" | Zu-Null 30 P für ABW, 10 P für STU | belegt |
| Aufsteiger/Neuzugänge zum Saisonstart | ligabase, wettboost, buli-tipphilfe | gestützt |
| Profiteur-Scout (Vertreter bei Ausfall) | ligabase, wettboost, LigaInsider | gestützt |
| Listing-Mindestpreis ≥ 90–95 % MW | help.kickbase.com | offiziell |
| Bieten zuerst, verkaufen bei Zuschlag | folgt aus 33-%-Regel | mechanisch |
| S11-Kern + Trading-Slots | offtopic, Ligen-Regelwerke | gestützt |
| Antizyklisch handeln | ligabase | gestützt |

Zusatzbefund: **Unsere Empirie ist an mehreren Stellen genauer als das öffentliche
Wissen.** Die Zweiteilung Kickbase-Listing / Manager-Listing, die err-5080-Leiter
(90 % ✗ / 95 % ✗ / 100 % ✓), die Zombie-Listings und die 157-%-Ljubičić-Beobachtung
stehen in keiner der recherchierten Quellen. Das ist ein echter Vorsprung.

### 7.2 (b) Wo wir nachweislich falsch liegen

**(a) Die 103 % für Computer-Angebote sind eine erfundene Zahl.**
`accept_offer_floor = 1.03` und die LEARNINGS-Regel „gelistete Spieler bringen rund
103 % Marktwert" stützen sich auf keine auffindbare Quelle. Zwei externe Angaben
sagen ±1 % bzw. +1–10 %. Wenn die untere stimmt, lehnt unser Floor **jedes**
Computer-Angebot ab: Wir listen, warten 72 Stunden, verkaufen am Ende doch sofort zu
100 % — und haben in der Zwischenzeit Kapital gebunden und ein Anpfiff-Risiko
getragen. **Das ist der teuerste Einzelfehler in diesem Abgleich, weil er einen
kompletten Verkaufspfad lahmlegen kann, ohne dass es auffällt.**
*Prüfung:* aus `transfer_actions` alle angenommenen/eingegangenen Computer-Angebote
gegen den MW des Tages rechnen. Bis dahin gilt die 103 % als **unbelegt**, nicht als
Regel.

**(b) Wir verwechseln die Kickbase-Position mit der Punkte-Position.**
Offiziell (2.7) bestimmt die Kickbase-Position nur den Aufstellungs-Slot; gepunktet
wird nach der tatsächlichen Position im Spiel — und beim Zu-Null-Bonus sogar nach
„der Position, die der Spieler am längsten innehatte". Unsere Formations-Minima,
die Positions-Perzentile in `intel/dimensions.py`, die Positions-Not-Toleranz und
die gesamte Defensiv-Doktrin rechnen mit `position` aus der Kickbase-API. Für einen
als ABW geführten Außenverteidiger, der als Flügel spielt, ist unsere Erwartung
systematisch zu hoch (Zu Null 20 statt 30, Tor 90 statt 100).

**(c) `matchup_signal` ist positions-blind — bei einer Defensiv-Doktrin.**
Heute: ein zentrierter Score aus der Siegwahrscheinlichkeit, gleiches Gewicht (0,20)
für jede Position. Tatsächlich hängen am Spielausgang für einen **Abwehrspieler bis
zu 45 Punkte** (15 Sieg + 30 Zu Null), für einen **Stürmer 25** (15 + 10) — und beim
**Torwart** wirkt ein starker Gegner in beide Richtungen (mehr Paraden, weniger
Zu Null). Wir setzen ausdrücklich auf die Defensive und modellieren ausgerechnet den
Kanal nicht, über den defensive Punkte hauptsächlich entstehen.

**(d) Wir optimieren den Erwartungswert und ignorieren die Prämien-Extreme.**
In `intel/signals.py` steht wörtlich: „Rohpunkte (konstante Grundlast) schlagen
tor-abhängige Ausreißer." Für die Punkte-Tabelle stimmt das. Für die
Erfolgs-Tabelle (4.2) ist es falsch: 200/300/400/500 Punkte von **einem** Spieler
zahlen 0,1 / 0,5 / 1 / 2 Mio. €, MVP zahlt 1 Mio. €. Ein Spieler mit hoher Varianz
ist für die Prämien mehr wert als einer mit derselben Erwartung und geringer
Varianz. Wir rechnen nur eine Seite der Bilanz.

**(e) Trading-Prämien-Schwellen sind seit dem 20.08. „vorgemerkt" und nirgends im Code.**
Die Strategie schreibt: „Trading-Prämien … Gratis-Geld, das der Verkaufs-Trigger
künftig mitdenken sollte." Sechs Tage später kennt kein Guardrail und keine Policy
die Schwellen 3 / 5 / 10 / 25 Mio. €. Ein Verkauf knapp unter einer Schwelle
verschenkt 250 k bis 2 Mio. €.

**(f) Die Kappe `max_bid_premium_need = 1.35` ist an unserem Verlust kalibriert,
nicht am Gewinnpreis.** Der einzige beobachtete Zuschlagspreis für einen begehrten
Spieler auf einem Kickbase-Listing ist **157 %** (Ljubičić an Lukas M.). Wir haben
daraus 135 % gemacht — die höchste Stufe, die unsere eigene Erwartungsformel noch
abbildet. Ehrlich benannt: Bei einem zweiten Ljubičić verlieren wir wieder, nur
knapper. Es fehlt die Gegenrechnung, ab wann ein Overpay ökonomisch falsch ist
(dafür 6.7 / 8.3).

**(g) „Marktwert-Trend" ist mit 0,15 das schwächste Gewicht — obwohl er die einzige
beobachtbare Größe des eigentlichen Preistreibers ist.** Nach 1.2/1.3 ist Nachfrage
der Haupttreiber und Leistung nur indirekt. Unser Modell gewichtet umgekehrt:
Punkte-Grundlast 0,40, Form 0,25, Matchup 0,20, Trend 0,15. Für den **Kauf-Score
eines Trading-Slots** (Ziel: Marktwertgewinn) ist das nachweislich verkehrt herum;
für den **Elf-Score** (Ziel: Punkte) ist es richtig. Wir benutzen aber dieselbe
Signal-Familie für beides.

**(h) Kein Anker in der Doktrin.** Drei unabhängige Quellen sagen: mindestens ein,
besser zwei Spieler aus den Top 15–20. Unsere Strategie nennt Kimmich ausdrücklich
„frisst die Liquidität der Arbitrage-Phase — deshalb NICHT sofort" und listet ihn
unter „Beobachten". Der Anker ist bei uns durch Zufall entstanden (Olise-Zuschlag,
weil niemand mitbot), nicht durch Plan.

### 7.3 (c) Was uns komplett fehlt

1. **Zu-Null-Erwartung** als eigenes Signal (Gegner-Offensivstärke, Under-2.5-Quote).
2. **Standard-Ausführer-Merkmal** (Elfmeter, Ecken, Freistöße) — nirgends in den Daten.
3. **Echte Minuten-Erwartung.** `einsatz_dim` ist ein Proxy aus der KB-Stufe. Weil
   der Zu-Null-Bonus **pro 10 Minuten** zählt, kostet eine Auswechslung in Minute 60
   einen Abwehrspieler ein Drittel des Bonus plus Minuten-Punkte — rund 13 Punkte.
4. **Prämien-Ökonomie** in Kauf- und Verkaufsentscheidung (4.2, 4.3).
5. **Amortisations-Rechnung fürs Overpay** (Tage, bis der Trend den Aufschlag zurückholt).
6. **Torwart-Sonderlogik** (starker Gegner = mehr Paraden).
7. **Gleichmäßigkeit des Trends** statt nur seiner Steigung.
8. **Kaderlimit live lesen** (11–25, Admin-Einstellung) — wir lesen nur `lpc`.
9. **Ein wiederholbares Verfahren, um genau diese Recherche zu erneuern** — jetzt
   in `.claude/skills/kickbase/SKILL.md` als Quellen-Radar.

---

## 8. Umsetzungsvorschläge

Für jede belegte Erkenntnis, die uns fehlt, ein konkreter Vorschlag.
**Nichts davon ist umgesetzt** — das entscheiden Emil und der Orchestrator.
Aufwand: S = unter einer Stunde, M = ein halber Tag, L = mehr.

### 8.1 Positions-abhängiges Team-Signal statt blankem `matchup` — **M / Nutzen hoch**
`intel/signals.matchup_signal` liefert heute einen Score aus der
Siegwahrscheinlichkeit. Vorschlag: daraus **erwartete Team-Punkte** rechnen und
positionsabhängig skalieren.
```
erwartete_teampunkte(pos) = 15·(p_sieg − p_niederlage)
                          + zu_null_wert(pos) · p_zu_null
                          − 5 · erwartete_gegentore
zu_null_wert: TW 50 · ABW 30 · MIT 20 · STU 10   (Punktetabelle 2.1)
```
`p_zu_null` und `erwartete_gegentore` lassen sich aus denselben Quoten schätzen,
aus denen wir schon `win_prob` ziehen (Über/Unter-Markt, sonst Näherung aus der
Siegwahrscheinlichkeit). Für den Torwart die Paraden-Gegenrichtung als Dämpfer
(6.4) — erst als Hypothese mit kleinem Gewicht.
*Nutzen:* Trifft direkt den Kanal, über den unsere defensive Doktrin ihre Punkte
holt. Ersetzt eine willkürliche Skalierung (`0.5 + (win_prob − 1/3)·1.2`) durch
Punkte aus der offiziellen Tabelle.
*Risiko:* Braucht Quoten-Qualität; ohne Quoten muss das Signal sauber auf neutral
zurückfallen (wie heute).

### 8.2 `accept_offer_floor` messen statt behaupten — **S / Nutzen hoch**
Vor jeder Parameter-Änderung: aus `transfer_actions` und dem Feed alle bisherigen
Computer-Angebote gegen den MW desselben Tages rechnen. Drei Ausgänge:
- Median ≥ 103 % → Parameter bleibt, LEARNINGS bekommt endlich einen Beleg.
- Median 99–102 % → `accept_offer_floor` auf den gemessenen Median minus Sicherheits-
  abstand senken (Decision-Record mit `expect`/`review`), sonst blockiert der Floor
  jeden Listing-Verkauf.
- Zu wenige Datenpunkte (< 5) → **nichts behaupten**, Messung als offene Kalibrierung
  eintragen und die 103 % im Text als „unbelegt" kennzeichnen.
*Nutzen:* Kann einen kompletten Verkaufspfad reparieren. Kostet eine Auswertung.

### 8.3 Overpay an die Amortisation binden — **S–M / Nutzen mittel-hoch**
Zusätzlich zu `max_bid_premium` (und `..._need`) eine zweite, **weichere** Grenze:
```
overpay_€ ≤ amortisation_tage · aktueller_tagestrend_€     (Vorschlag: 3 Tage)
```
Für einen Spieler mit +225 k/Tag Trend erlaubt das ~675 k Aufschlag; für einen
stagnierenden Spieler nahezu keinen. Wirkt als **Begründungs-Pflicht**, nicht als
Sperre: Wer darüber will, braucht `squad_need` (Formation steht sonst nicht) —
dann zählt Punkte-Not, nicht Rendite.
*Nutzen:* Beantwortet die Frage, die unsere Ljubičić-Lehre offen ließ — nicht „wie
viel Prozent", sondern „wann lohnt sich Prozent". Quelle 6.7 ist nur eine Hypothese;
deshalb als Zusatzgrenze mit großzügigem Startwert einführen und nach 10 Geboten
zurückmessen.

### 8.4 Standard-Ausführer als Merkmal — **M–L / Nutzen hoch, Datenfrage offen**
Elfmeter (+80), Großchance kreiert (+15), erfolgreiche Flanke (+3) und
Torschuss-Vorlage (+5) konzentrieren sich auf sehr wenige Spieler pro Team.
Datenwege, in dieser Reihenfolge zu prüfen: (1) LigaInsider-Spielerseiten
(Standard-Hinweis), (2) unsere eigene Performance-Historie — wer Elfmeter schießt,
hat auffällige Punkte-Spitzen, (3) manuelle Pflege als kleine Liste, die die
Morgen-Recherche fortschreibt (24 Vereine × 1–2 Namen).
*Nutzen:* Der einzige Hebel, den die Community geschlossen als „Cheat-Code"
bezeichnet und den wir null abbilden.
*Aufwand:* Steht und fällt mit der Datenquelle — deshalb zuerst eine
Machbarkeitsprüfung, kein Bau.

### 8.5 Trend-Qualität statt nur Trend-Steigung — **S / Nutzen mittel**
`trend_signal` nimmt heute `(letzter − erster) / erster` über 7 Tage. Vorschlag:
zusätzlich die **Anzahl positiver Tage** im Fenster. Ein Anstieg über 6 von 7 Tagen
ist Nachfrage; derselbe Anstieg an einem einzigen Tag ist meist der Peak (1.4).
Score-Wirkung erst nach Rückmessung; zunächst als Begründungs-Text und als Spalte.

### 8.6 Prämien-Schwellen im Verkaufs-Trigger — **S–M / Nutzen direkt in Euro**
Beim Verkauf/Listing prüfen: `gewinn = erlös − kaufpreis`. Liegt der Gewinn innerhalb
von ~10 % unterhalb einer Schwelle (3 / 5 / 10 / 25 Mio. €), das Vorhaben mit dem
Hinweis versehen und den Preis so wählen, dass die Schwelle gerissen wird — oder
kurz halten. Umgekehrt: kein Halten „auf Verdacht" gegen die Kader-Doktrin.
*Nutzen:* 250 k bis 2 Mio. € je Fall, ohne Risiko.
*Voraussetzung:* Kaufpreis je Spieler muss zuverlässig im Audit stehen.

### 8.7 Zwei Score-Familien statt einer — **M / Nutzen mittel-hoch**
Heute speist dieselbe Signal-Familie den Kauf-Score (Ziel: Marktwertgewinn) und den
Elf-Score (Ziel: Punkte). Nach 1.2/1.3 sind das verschiedene Zielgrößen. Vorschlag:
- **Handels-Score**: Trend 0,40 · Nachfrage-Katalysator (Ausfall/Neuzugang/Comeback)
  0,25 · Form 0,20 · Grundlast 0,15
- **Punkte-Score**: Grundlast 0,40 · Team-Signal (8.1) 0,25 · Form 0,20 ·
  Minuten-Erwartung 0,15
*Nutzen:* Beendet den Zielkonflikt in einem Score. *Risiko:* größerer Eingriff,
Backtest nötig — nach SP1, nicht davor.

### 8.8 Kleinigkeiten — **S je Stück**
- Kaderlimit (11–25) live lesen und als Guardrail führen, wie schon `lpc` (5.4).
- Nach jedem Spieltag die LI-Prognose gegen die echte Aufstellung zurückmessen —
  die 88,6 % (6.9) sind eine Anbieter-Angabe, unsere eigene Quote ist die relevante.
- Eigengoal-Wert klären (−60 oder −75, 2.6) — reine Anzeige-Korrektheit.

---

## 9. Quellenverzeichnis

**Offiziell (Kickbase)**
- [Wie wird der Marktwert berechnet?](https://help.kickbase.com/help/kickbase-marktwert)
- [Wie werden die Punkte berechnet?](https://help.kickbase.com/help/wie-werden-die-punkte-berechnet)
- [Spielerpositionen und Punktevergabe](https://help.kickbase.com/en/help/spielerposition)
- [Punktetabelle](https://us.kickbase.com/en-us/points-table) (deutsche Seite `kickbase.com/de/points-table` blockt Bots)
- [Zu-Null-Bonus](https://help.kickbase.com/help/star-star-was-ist-der-zu-null-bonus-bei-kickbase-star-star-der-zu-null-bonus-wird-an-spieler-vergeben-wenn-das-von-ihnen-verteidigte-team-wahrend-des-spiels-kein-gegentor-hinnehmen)
- [Welche Erfolge kann ich in der App erhalten?](https://help.kickbase.com/help/welche-erfolge-kann-ich-in-der-app-erhalten)
- [Auflaufprämie](https://help.kickbase.com/help/auflaufpraemie)
- [Wann werden die Punkteprämien ausgeschüttet?](https://help.kickbase.com/help/wann-werden-die-punktepramien-ausgeschuttet)
- [Der Transfermarkt](https://help.kickbase.com/help/kickbase-transfermarkt)
- [Warum habe ich den Spieler nicht bekommen?](https://help.kickbase.com/en/help/ich-habe-auf-einen-spieler-geboten-warum-habe-ich-ihn-nicht-zum-transferzeitpunkt-bekommen)
- [Kaderbegrenzung](https://help.kickbase.com/help/kaderbegrenzung)
- [Konto im Minus zum Anpfiff](https://help.kickbase.com/help/kontoimminus)
- [Startelf-Wahrscheinlichkeiten](https://help.kickbase.com/help/startelfwahrscheinlichkeiten)
- [Spieltag & Punkte (Übersicht)](https://help.kickbase.com/en/col/rund-um-den-spieltag)

**Community / Fachseiten**
- [Ligabase Trading-Guide](https://ligabase.de/blog/kickbase-trading-guide) · [Ligabase Blog-Übersicht](https://ligabase.de/blog)
- [Fußball-Orakel Punktetabelle](https://fussball-orakel.de/kickbase_points.php)
- [wettboost: 8 Profi-Tricks](https://wettboost.com/kickbase-insider-8-profi-tricks-fuer-deinen-kickbase-erfolg/) · [wettboost: Tipps & Tricks](https://wettboost.com/kickbase-tipps-und-tricks/)
- [buli-tipphilfe: Marktwerte, Transfers & Aufstellung](https://www.buli-tipphilfe.de/blog/kickbase-tipps-lizenzen-vergleich)
- [WEB.DE / 1&1: Tipps und Tricks für den Erfolg](https://web.de/magazine/sport/fussball/bundesliga/tipps-tricks-erfolg-kickbase-liga-41279788)
- [giga: Unter Marktwert bieten](https://www.giga.de/tipp/kickbase-unter-marktwert-bieten-das-sollte-man-beachten/)
- [BaseXI Updates](https://www.base-xi.de/updates) · [KickbaseNerd Rating-Tool](https://kbnerd.de/das-kickbase-rating-tool/)
- [sportforen.de Kickbase-Thread](https://sportforen.de/threads/kickbase.1078/page-6)
- [Punktelieferanten-Podcast](https://podcasts.apple.com/us/podcast/punktelieferanten-der-kickbase-bundesliga-podcast/id1578261081)
- [LigaInsider](https://www.ligainsider.de/) · [LigaInsider-Trefferquote 88,6 %](https://welt-der-legenden.de/ligainsider-news-aufstellungen-tipps-2026/)

**Nicht erreichbar / für den nächsten Turnus**
`kickbase.com/de/points-table` (403) · `offtopic.online` (403) · `base-xi.de/players` (403) ·
Reddit (Crawler gesperrt — nur über Zitate Dritter) · YouTube-Transkripte (nicht abrufbar,
Kanäle müssen manuell gesichtet werden) · Ligabase-Guides „Marktwert-Prognose",
„Gebots-Strategie", „Punkte & Prämien", „Aufstellung/Formation" (angekündigt, noch nicht
veröffentlicht — im September erneut prüfen).

---

## 10. Fund-Protokoll (wächst mit jedem Turnus)

> Das Verfahren steht in `.claude/skills/kickbase/SKILL.md` → „News- & Meta-Radar —
> der Quellen-Radar". Jeder Turnus hängt hier einen Block an, auch wenn nichts
> gefunden wurde. Neue Erkenntnisse werden oben in die Fach-Abschnitte eingearbeitet;
> hier steht nur, **wann gesucht wurde, wonach, und was daraus wurde.**

### Turnus 26.08.2026 (Erstaufnahme, Emils Auftrag)

- **Gesucht:** Marktwert-Mechanik, Punkteregeln, Gebots-/Trading-Strategien,
  Kadergrößen, Punkte pro Euro, Spieltags-Timing, verbreitete Fehler.
- **Neu und BELEGT:** vollständige Punktetabelle (Abschnitt 2) · Zu-Null-Bonus je
  10 Minuten und positionsabhängig · Punkte nach tatsächlicher Spielposition ·
  Erfolgs- und Auflaufprämien mit Beträgen (Abschnitt 4) · Marktwert entsteht
  community-weit über alle Ligen · Kaderbegrenzung 11–25.
- **Neu und STRITTIG:** Höhe der Computer-Angebote (unsere 103 % gegen ±1 % bzw.
  +1–10 %) · Underpay-Schranke „10 % unter MW" gegen unsere Friedrich-Empirie ·
  Eigentor-Wert (−60 vs. −75).
- **Daraus geworden:** 8 Umsetzungsvorschläge (Abschnitt 8), noch keiner umgesetzt —
  Entscheidung liegt bei Emil und dem Orchestrator. Keine Parameter geändert,
  kein Produktionscode berührt.
- **Nächster Turnus:** 26.09.2026. Offene Punkte mitnehmen: Ligabase-Guides (waren
  nur Ankündigungen), Nachholspiel-Regeln, YouTube-Kanäle manuell sichten.
