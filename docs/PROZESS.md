# Der Prozess — was nachts eigentlich passiert

> Für Emil geschrieben. Zehn Minuten Lesezeit, kein Code, keine Abkürzungen ohne
> Erklärung. Wer die technische Fassung will: `docs/ARCHITEKTUR.md`.
> Stand: 26.08.2026.

---

## In einem Satz

Der Agent sammelt jeden Tag Zahlen über alle Bundesliga-Spieler, gibt jedem eine
Note, macht aus den besten Noten **Vorhaben mit einer Wartezeit**, führt sie durch
**eine einzige verschlossene Tür** aus — und schreibt danach auf, ob er recht
hatte.

---

## Die vier Stationen

```
   ZAHLEN            NOTE              VORHABEN           AUSFÜHRUNG
   sammeln    →    bilden      →    mit Wartezeit   →    durch eine Tür
                                    (du kannst nein         (mit Wächtern)
                                     sagen)
                                                              ↓
                                                      NACHSCHAU: hatte ich recht?
                                                              ↓
                                                      (zurück zu „Note bilden")
```

Jede Station kann die nächste blockieren. Nichts überspringt eine Station — auch
nicht, wenn es eilig ist.

---

## Station 1 — Woher die Zahlen kommen

Fünf Quellen, in dieser Reihenfolge des Vertrauens:

**1. Kickbase selbst.** Dein Konto, dein Kader, der Markt, alle offenen Gebote, der
Liga-Feed (wer hat wen für wie viel gekauft), die Marktwert-Historie jedes Spielers.
Seit deinem Member-Abo zusätzlich: die Kickbase-eigene Startelf-Einschätzung, ein
Verletzungstext je Spieler und die echten Spielerfotos.
*Grenze:* Die Kickbase-Startelf-Einschätzung ist bis zum ersten Spieltag nur
**Anzeige** — wir wissen noch nicht, ob „Stufe 2" wirklich heißt „spielt". Erst
nachdem wir sie einmal gegen echte Aufstellungen geprüft haben, darf sie in die
Note einfließen.

**2. LigaInsider.** Wer startet, wer sitzt, wer fällt aus. Das ist unsere
Hauptwahrheit für die Frage „spielt er überhaupt?". Bei Widerspruch zu Kickbase
gewinnt LigaInsider. (LigaInsider gibt seine eigene Trefferquote mit 88,6 % an —
bei elf aufgestellten „Startern" sitzt also im Schnitt gut einer doch auf der Bank.
Das ist kein Fehler des Systems, das ist die Realität einer Prognose.)

**3. Nachrichten.** kicker-Meldungen, Kickbase-YouTube-Kanäle, das Kickbase-Forum
auf Reddit. Daraus entstehen Warnungen („Trainerwechsel", „angeschlagen") und
Community-Tipps.

**4. Wettquoten.** Aus den Quoten für das nächste Spiel eines Vereins rechnen wir
die Siegwahrscheinlichkeit — sie sagt viel darüber, wie viele Punkte die Spieler
dieses Vereins am Wochenende holen werden.

**5. Unser eigenes Gedächtnis.** Jede Aktion, die wir je gemacht haben, steht in
einem Protokoll: was, wann, warum, zu welchem Preis, mit welchem Ergebnis. Das ist
die einzige Quelle, die uns wirklich gehört — und deshalb die, die im Streitfall
gewinnt.

---

## Station 2 — Wie ein Spieler eine Note bekommt

Es gibt **zwei Noten**, weil es zwei verschiedene Fragen gibt.

### Der Kauf-Score: „Ist dieser Spieler ein gutes Geschäft?"

Er setzt sich aus vier Bausteinen zusammen. Die Prozentzahlen sind ihr Gewicht:

| Baustein | Gewicht | Was er misst |
|---|---:|---|
| **Grundlast** | 40 % | Wie viele Punkte holt er auch an schlechten Tagen? Wir nehmen bewusst nicht den Durchschnitt, sondern das **untere Viertel** seiner Spiele. Ein Sechser, der jede Woche 90 Punkte macht, schlägt einen Stürmer, der zwischen 20 und 200 schwankt. |
| **Form** | 25 % | Wie waren die letzten drei Spiele, verglichen mit seinem eigenen Schnitt? |
| **Gegner** | 20 % | Wie stark ist der nächste Gegner? Ein offenes Spiel ist neutral, nicht schlecht — das war früher ein Fehler bei uns. |
| **Marktwert-Trend** | 15 % | Steigt oder fällt sein Wert in den letzten sieben Tagen? |

Dazu kommt ein kleiner Aufschlag, wenn die Community den Spieler gerade empfiehlt.

**Ab 0,53 gilt: kaufenswert (seit 31.08., Momentum-Doktrin — vorher 0,55). Unter 0,35: verkaufenswert.**

Zwei Ausnahmen von der Kauf-Schwelle:
- Fehlt uns auf einer Position die Besetzung (etwa nur noch drei Abwehrspieler),
  sinkt die Schwelle für **genau diese Position** auf 0,48. Ein Loch in der
  Aufstellung kostet rund 60 bis 90 Punkte pro Spieltag — mehr als ein etwas
  schwächerer Kauf.
- Fällt ein eigener Spieler drei Tage im Wert **und** hat keinen Weg in die
  Startelf, wird er gelistet, auch wenn die Gesamtnote noch über 0,35 liegt.

### Der Elf-Score: „Wen stelle ich am Wochenende auf?"

Derselbe Aufbau, aber **ohne den Marktwert-Trend** — beim Aufstellen ist völlig
egal, was ein Spieler kostet, es zählen nur Punkte. Darüber liegt LigaInsider als
Veto: Wer laut LigaInsider ausfällt, wird mit Faktor null bewertet und fliegt aus
der Elf, egal wie gut seine Note ist. Wer auf der Bank sitzt, wird gedämpft.

### Signale, die zusätzlich eingreifen

- **Ausfall eines Stammspielers** → sein Vertreter wird zum Kandidaten
  (der „Profiteur-Scout").
- **Langzeitverletzung im eigenen Kader** → sofort verkaufen, möglichst vor 22 Uhr,
  weil um 22 Uhr der alte Preis verschwindet.
- **Kurzzeitverletzung** → halten. Der Wert kommt beim Comeback zurück.
- **Namensgleichheit** → gegenprüfen. Wir haben einmal Oscar Højlund für verletzt
  gehalten, weil Emil Højlund verletzt war.

---

## Station 3 — Vom Urteil zum Vorhaben

Der Agent kauft nichts direkt. Er legt ein **Vorhaben** an: *„Ich will Spieler X
für Y Euro bieten, weil Z."* Das Vorhaben erscheint sofort in der Zentrale
(localhost:8080) und in Discord — **bevor** etwas passiert.

Dann läuft eine Uhr. Wie lange, hängt davon ab, wie eilig es ist:

| Lage | Wartezeit |
|---|---|
| Ein Kickbase-Listing läuft ab | Ausführung 15 Minuten vor Ablauf |
| Ein Manager-Listing (ein Mensch entscheidet) | plus 2 Stunden |
| Weniger als 30 Minuten Restzeit | sofort |
| Betrag ab 5 Mio. € | mindestens 2 Stunden, ohne Ausnahme |

In dieser Zeit kannst du in der Zentrale: **überspringen** (Veto), **+10 %**,
**+20 %**, **Betrag ändern** oder **jetzt ausführen**. Tust du nichts, führt der
Agent aus. Das ist die Umkehrung von früher: Nicht du gibst frei, sondern du
widersprichst.

Ein Planer legt stündlich Vorhaben an, ein Ausführer schaut alle fünf Minuten nach,
was fällig ist.

---

## Station 4 — Die eine Tür

**Jede** Aktion, die Geld bewegt oder den Kader ändert, geht durch dieselbe Stelle
(im Code heißt sie `ActionExecutor`). Es gibt keinen zweiten Weg — nicht für den
Planer, nicht für die Notbremse, nicht für mich in einer Chat-Sitzung, nicht für
einen künftigen zweiten Agenten.

Das ist die wichtigste Bauentscheidung des ganzen Systems, und sie hat einen Grund:
Wenn eine Schutzregel an dieser Tür hängt, gilt sie **für alle Wege gleichzeitig** —
auch für Wege, die es noch gar nicht gibt. Wir haben das teuer gelernt: Der
Anpfiff-Wächter räumte brav die Gebote weg, und der Ausführer setzte drei Minuten
später neue.

An der Tür passiert bei jeder Aktion dasselbe:

1. **Sekunden vorher noch einmal frische Preise holen.** Der Marktwert kann sich seit
   der Planung geändert haben.
2. **Alle Wächter durchlaufen** (nächster Abschnitt).
3. **Ausführen und das Ergebnis zurücklesen.** Ein „200 OK" von Kickbase beweist
   nichts — wir prüfen den Kontostand, den Kader, die Gebotsliste.
4. **Ins Protokoll schreiben**, samt einer Zuschlag-Erwartung: nicht „ich biete",
   sondern „ich biete mit 45 % Chance, weil …". Damit fallen Fehleinschätzungen von
   selbst auf.

Bricht die Verbindung **nach** dem Senden ab, wird die Aktion als „unklar" geführt
und stündlich gegen den echten Kickbase-Zustand aufgelöst — niemals blind
wiederholt. Doppelte Käufe sind teurer als späte Gewissheit.

---

## Die Sicherungen, von hart nach weich

**1. Die Verfassung — geschützte Spieler.** Olise wird nicht verkauft. Punkt. Diese
Sperre sitzt an der Tür und steht **vor** allem anderen, auch vor der
Anpfiff-Notbremse. Sie steht bewusst nicht in der Parameterliste, die der Agent
selbst ändern darf — sonst könnte er sich die Erlaubnis selbst ausstellen.
*(Diese Regel stand fünf Tage lang nur im Strategiepapier und war technisch
wirkungslos. Seit dem 26.08. ist sie eine echte Sperre.)*

**2. Der Hauptschalter.** Solange er auf „Trockenlauf" steht, tut das System gar
nichts — es tut nur so. Diesen Schalter legst nur du um, nie der Agent.

**3. Konto ≥ 0 zum Anpfiff.** Wer beim Anpfiff des ersten Spiels im Minus steht,
bekommt für den **gesamten** Spieltag null Punkte. Das ist die teuerste Regel des
Spiels, und deshalb hängt eine ganze Kaskade daran:

| Zeitpunkt | Was passiert |
|---|---|
| 48 Stunden vorher | Neue Gebote nur noch, wenn das Konto sie auch dann trägt, wenn **alle** offenen Gebote gleichzeitig zuschlagen. Der „ein Drittel Teamwert"-Hebel zählt hier nicht mehr — ein Listing braucht bis zu 72 Stunden, um Geld zu bringen. |
| 2 Stunden vorher | Kein neues Gebot mehr, ohne Ausnahme. Offene Gebote, die das Konto sprengen könnten, werden storniert. |
| 1 Stunde vorher | Meldung an dich: Konto so und so, hier wäre der Notverkaufs-Plan. Du hast 45 Minuten. |
| 35 Minuten vorher | Aufstellung wird endgültig gesetzt. |
| 15 Minuten vorher | Notbremse: verkaufen, bis das Konto stimmt. |

**4. Die Drittel-Regel.** Kickbase erlaubt ein Minus bis zu einem Drittel des
Teamwerts — aber erst **nach** dem Anpfiff des ersten Spiels. Unsere Kaufkraft ist
deshalb: Konto minus offene Gebote plus ein Drittel Teamwert. Ein großes offenes
Gebot blockiert alle weiteren.

**5. Preisgrenzen.** Höchstens 60 % des Budgets in ein einziges Gebot. Höchstens
110 % des Marktwerts — außer der Spieler ist ausdrücklich als **Kaderbedarf**
markiert (ohne ihn steht die Formation nicht), dann bis 135 %. Fünf Käufe und fünf
Verkäufe pro Tag.

**6. Liga-Grenzen.** Das Vereinslimit lesen wir live aus Kickbase (offene Gebote
zählen mit!). Die mündlich vereinbarte 200-Transfers-Regel zählen wir aus dem
Liga-Feed und sperren ab 195 — fünf bleiben als Notreserve.

**7. Kader-Gesetz.** Ab 48 Stunden vor dem Anpfiff stehen mindestens elf
aufstellbare Spieler im Kader. Keine Startelf-Spieler verkaufen, außer als Tausch
gegen einen besseren auf derselben Position.

**8. Zeitfenster.** Zwischen 21:25 und 22:25 wird nicht deployt. Ein Neustart in
diesem Fenster verschluckt die Abendjobs ersatzlos — der Zeitplan lebt nur im
Arbeitsspeicher und holt nichts nach. Das hat uns schon ein Briefing gekostet.

---

## Der Tag im Rhythmus

| Uhrzeit | Was läuft |
|---|---|
| 08:03 / 13:03 / 18:03 | **Tagesprämie holen.** Ab Tag 10 sind das 100.000 € täglich; ein verpasster Tag setzt auf Tag 1 zurück und kostet rund 450.000 €. |
| 08:27 | **Morgen-Recherche.** Lage, offene Gebote mit Restlaufzeit, Kandidaten mit Noten, Bericht nach Discord. |
| 09:00 / 13:00 / 18:00 | **Markt-Puls.** Leise — meldet sich nur, wenn wirklich etwas zu tun ist. |
| 10:33 / 19:07 | **Nachrichten-Update.** Lage und Schlussfolgerungen. |
| stündlich :22 / :52 | **Zentrale neu bauen** (die Web-App, die du im Browser öffnest). |
| stündlich :25 | **Planer.** Legt Vorhaben an — schreibt nie selbst bei Kickbase. |
| alle 5 Minuten | **Ausführer.** Führt fällige Vorhaben aus. |
| stündlich :38 | **Cron-Wächter.** Prüft, ob alle Jobs wirklich gelaufen sind. Meldet nur Probleme; um 07:38 kommt die Entwarnung. |
| stündlich :47 | **Gebots-Wächter.** Erinnert daran, ein Gebot zu erhöhen, bevor ein Listing ausläuft. |
| stündlich :52 | **Abgleich.** Löst „unklare" Aktionen gegen den echten Kickbase-Zustand auf. |
| stündlich :57 | **Gebots-Nachschau.** Bucht das Ergebnis gegen die vorher notierte Erwartung. |
| 21:33 | **Closing Bell.** Langzeitverletzte melden — noch vor dem 22-Uhr-Update. |
| 22:05 | **Datenaufnahme**, direkt nach dem Marktwert-Update. |
| 22:15 | **Tagesbriefing:** Stand, Trades, Elf, Ausfälle. |
| 22:20 / 22:30 | **Schattenlauf.** Was hätte ein vollautomatischer Händler heute getan? Nur Bericht, keine Aktion. |
| Fr 19:30 / 19:45 | LigaInsider-Aufstellungsleak → Aufstellung setzen. |
| Sa/So 10:00 | Spätausfälle → Aufstellung nachziehen. |
| Fr–So | Anpfiff-Kaskade (siehe oben). |

---

## Ein Fall, komplett durchgerechnet: der Ljubičić-Verlust

Der lehrreichste Fall der letzten Woche — weil er durch **jede** Station lief und
trotzdem verloren ging.

**Die Zahlen.** Marktwert 2,71 Mio. €. Kickbase-Listing, Ablauf 00:33 Uhr. Ein
Kickbase-Listing bedeutet: Kein Mensch entscheidet, beim Ablauf gewinnt automatisch
das höchste Gebot — und der Gewinner zahlt **sein eigenes Gebot**, nicht das des
Zweiten.

**Station 1 — Zahlen.** Kickbase liefert Marktwert, Restzeit und die Zahl der
Bieter. Die stand bis zuletzt bei **eins: uns.** LigaInsider führte ihn als
Startelf-Kandidaten.

**Station 2 — Note.** Über der Kaufschwelle. Ein Spieler, den wir haben wollten.

**Station 3 — Vorhaben.** Gebot 2,93 Mio. € = **108 % des Marktwerts**. Diese Zahl
kam aus unserer Regel: Ziel-Aufschlag 8 %, dazu der krumme Betrag, der glatte
Rivalengebote schlägt. Vorhaben angelegt, Wartezeit lief, kein Veto.

**Station 4 — Ausführung.** Gebot platziert, Rückgelesen, im Protokoll notiert —
mit einer Zuschlag-Erwartung. Bis zum Ablauf blieb die Bieterzahl bei eins.

**Das Ergebnis.** Der Zuschlag ging an Lukas M. für **4,23 Mio. € = 157 % des
Marktwerts.** Er bot in der letzten Minute. Wir hatten nie eine Chance, und wir
haben es bis zur letzten Sekunde nicht gesehen.

**Warum das so kommen musste — zwei Denkfehler auf einmal:**

1. **„Wir sind allein" ist keine Information.** Die Kickbase-Schnittstelle zeigt nur,
   wer **jetzt** bietet. Wer in der letzten Minute kommt, ist vorher unsichtbar.
   Unsere Sicherheit war ein Anzeigefehler, den wir für einen Vorsprung hielten.
2. **108 % war eine Preisregel ohne Bezug zur Nachfrage.** Bei begehrten Spielern
   zahlt der Markt 157 %. Unsere Zahl kam aus einem Durchschnitt über alle
   Spielertypen.

**Die Gegenrechnung — warum es trotzdem nicht einfach „mehr bieten" heißt.** Beim
Kickbase-Listing zahlt der Gewinner sein eigenes Gebot. Hätten wir pauschal 157 %
geboten und niemand hätte mitgeboten, hätten wir 1,3 Mio. € zu viel bezahlt — für
denselben Spieler. Teurer bieten ist also nur dann richtig, wenn wir den Spieler
**wirklich brauchen**, nicht wenn wir ihn nur gern hätten.

**Zum Vergleich: der Olise-Kauf, derselbe Mechanismus, anderes Ergebnis.** 68,02
Mio. € auf ein 64,79-Mio.-Listing = **105 %**. Zuschlag, niemand bot mit. Der
Unterschied lag nicht an uns, sondern an der Liga: Wir hatten vorher aus dem
Liga-Feed die Kontostände aller zwölf Manager rekonstruiert — zehn von zwölf waren
illiquide, nur einer hätte mithalten können. Bei einem 2,7-Mio.-Spieler kann
**jeder** mitbieten. Genau deshalb ist ein billiger Spieler manchmal schwerer zu
bekommen als ein teurer.

---

## Der Lernzyklus: Überraschung → Urteil → Änderung → Beweis

So wurde aus dem Ljubičić-Verlust eine Verhaltensänderung. Dieser Vierschritt ist
das Verfahren für **jede** Überraschung.

**1. Überraschung.** Etwas passiert, das unsere Regeln nicht vorhergesagt haben.
Hier: einziger Bieter und trotzdem verloren, zu 157 %. Eine Überraschung ist kein
Ärgernis, sondern der einzige Rohstoff, aus dem Lernen entsteht — deshalb notiert
der Agent zu **jedem** Gebot vorher eine Erwartung. Ohne Erwartung gibt es keine
Überraschung, nur Pech.

**2. Urteil.** Was genau war falsch — die Zahl, die Regel, oder die Annahme
dahinter? Hier: die Annahme („allein = sicher") **und** die Zahl (108 % bei
begehrten Spielern). Zwei Fehler, nicht einer.

**3. Änderung.** Die neue Regel wird aufgeschrieben — mit dem, was sie bewirken
soll, und mit dem Termin, an dem wir nachschauen. Hier: Bei Spielern, die wir
wirklich brauchen, mindestens 120 % bieten; Erwartung entsprechend gestaffelt
(0,45 unter 120 %, 0,60 ab 120 %, 0,80 ab 135 %).

**4. Beweis.** Und hier kommt der Teil, den die meisten überspringen: **Prüfen, ob
die neue Regel überhaupt ausführbar ist.** Genau das ging beim nächsten Mal fast
schief. Einen Tag nach der Ljubičić-Lehre fiel auf, dass unser eigener Wächter
**jedes** Gebot über 110 % abgelehnt hätte. Die frische Regel wäre an der eigenen
Sicherung gescheitert, ohne dass es jemand gemerkt hätte — sie hätte im Dokument
gestanden und im Betrieb nie stattgefunden.

Aufgelöst wurde das nicht durch pauschales Anheben, sondern durch **zwei Stufen**:
Standard bleibt 110 % (weil jedes unnötige Overpay sofort bezahlt wird), und nur
ausdrücklich als Kaderbedarf markierte Gebote dürfen bis 135 %.

> **Die Regel, die daraus wurde:** Nach jeder neuen Preis- oder Verhaltensregel
> prüfen, ob eine eigene Sicherung sie sperrt. **Eine Regel ohne passenden Wächter
> ist Doktrin, kein Verhalten.**

Dasselbe Muster hatte kurz davor Olise erwischt: „Olise wird nicht verkauft" stand
fünf Tage im Strategiepapier und war technisch wirkungslos — die Schutzliste war in
jedem Produktionspfad leer.

**Wo das Gelernte landet:**
- Verhaltensregeln → `.claude/skills/kickbase/LEARNINGS.md`, jede mit zwei Zeilen:
  *Kurz* (ein Satz für Menschen) und *Regel* (was der Agent ab jetzt tut). Ein
  Eintrag ohne Regel ist kein Learning, sondern Nachschlagewerk.
- Zahlen → als Entscheidungs-Eintrag mit *alt*, *neu*, *Grund*, *Erwartung*,
  *Prüftermin*. Er erscheint automatisch im Protokoll der Zentrale.
- Einzelne Spieler → als Tipp.
- Wissen von außen → `docs/RECHERCHE-META-STRATEGIEN.md`, mit Einstufung
  „belegt" oder „Hypothese". Ungeprüftes wird nie zur Regel.

**Und einmal im Monat:** der Quellen-Radar. Eine halbe Stunde gezielte Suche nach
Regeländerungen, neuen Werkzeugen und dem, was die guten Manager gerade anders
machen — mit festen Suchbegriffen, festen Quellen und der Pflicht, jeden Fund als
„belegt" oder „Hypothese" einzustufen. Das Verfahren steht in
`.claude/skills/kickbase/SKILL.md`.

---

## Was das System nicht kann (ehrlich)

- **Es kann den Marktwert nicht bewegen.** Der entsteht in der gesamten
  Kickbase-Community über alle Ligen. Unsere zwölf Manager sind Rauschen.
- **Es sieht keine Gebote, die noch nicht abgegeben wurden.** Siehe Ljubičić.
- **Es kennt keine Aufstellung vor dem Trainer.** LigaInsider liegt in etwa 11 %
  der Fälle daneben, und das ist gut.
- **Es rechnet manches noch nicht.** Der Zu-Null-Bonus (für einen Abwehrspieler
  30 Punkte pro Spiel!) und die wiederholbaren Erfolgsprämien stehen in keiner
  unserer Noten. Das ist der wichtigste Fund der Meta-Recherche vom 26.08. und
  liegt als Vorschlag auf dem Tisch — umgesetzt ist er nicht.
- **Es lernt nicht von selbst.** Es gibt kein Training, keine Blackbox. Jede
  Änderung ist eine Zeile, die jemand geschrieben und begründet hat. Genau deshalb
  kannst du jede Entscheidung im Protokoll bis zu ihrem Grund zurückverfolgen.
