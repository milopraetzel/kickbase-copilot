# Übergabe der Turnaround-Studie an die bauende Session — Stand Mo 21.09.2026 09:10 MESZ

> Für die KI, die den Kickbase-Agenten gebaut hat und den Umbau ausführt. Diese Datei
> ist ohne Vorwissen aus der Studien-Session lesbar. Sie sagt, was jene Session getan
> und was sie NICHT getan hat, wo die Ergebnisse liegen, was daraus folgt und was offen ist.

## 1. Was die Studien-Session getan hat — und was nicht

Die Session (Claude Code, 18.–21.09.2026) hat **keinen Code gebaut**. Sie hat eine
Read-only-Studie durchgeführt und einen Umbauplan geschrieben.

| Getan | Beleg |
|---|---|
| Retro der Saison aus Box-Protokoll, Liga-Transfers und Spieltagsverlauf | Chat; Zahlen in der Studie nachgerechnet |
| Nur-Lese-Snapshot der Produktions-DB gezogen (`sqlite3 backup`, Temp-Dateien auf der Box wieder entfernt) | `.local-backups/studie-2026-09-19/kba_snapshot.db` |
| Multi-Agent-Studie: 9 Forscher, 64 Widerlegungsprüfungen, 4 Entwürfe, 3 Richter, Synthese, 3 Härtungsrollen, 3 Nachrechnungen (89 Agenten, alle lesend) | `.local-backups/studie-2026-09-19/workflow-journal.jsonl` (Rückgabe jedes Agenten), `workflow-script.js` |
| Studie und Anhang ins Repo gelegt, **uncommittet** auf `main` | `docs/STUDIE-TURNAROUND-2026-09-19.md`, `docs/STUDIE-TURNAROUND-2026-09-19-anhang.json` |
| Ein Memory-Eintrag | `project_turnaround_studie_2026-09-19.md` im Auto-Memory |

| NICHT getan | Anmerkung |
|---|---|
| Kein Code geändert, kein Commit, kein Deploy, keine `.env`-Änderung | `git status`: nur die zwei Studien-Dateien und diese Datei sind neu |
| Kein Kickbase-Write | Ein `place_bid` auf Remberg (8,60 M, mit Emils wörtlicher Auswahl als `emil_go`) wurde vom Executor **verweigert** („Kaufprüfung ungültig — unbekannte Schemaversion", Operator-Tor). Das Tor wurde nicht umgangen. Emil hat Remberg danach selbst in der App ersteigert; Osei nahm das Tigges-Gebot an. |
| Der `emil_go`-Override im `git stash` wurde nicht angefasst | Empfehlung der Studie: verwerfen, Entscheidung bei Emil (E6) |
| Keine Memory-Einträge zu Pauschalmandaten geändert | wartet auf Emils Einverständnis (E10) |

## 2. Lage heute (Mo 21.09.2026 09:07, live gelesen)

- Platz **11 von 12** (nach Spieltag 4; Olise holte 664 Punkte). Konto 372.334 €, Teamwert 127,10 M, Kader 10 von 11, keine offenen Gebote, Transfers 23/200.
- Nächste Frist: **Spieltag 5, Fr 09.10.2026 20:30** (Länderspielpause). Spieltagsgeld ST4 war um 09:07 noch nicht gebucht.
- Box seit 19.09. nur Prämien-Writes. `main` = `origin/main`, keine neuen Commits seit `60a3732` (Abschlussbilanz der Parallelsession, `docs/ABSCHLUSS-2026-09-18.md`).
- Sechs eigene Spieler stehen noch am Markt gelistet (Olise 100 M, Ache, Minjae unter Marktwert, Schwäbe, Scheller, Ullrich). Die Starter-Listings hat am 07.09. ein Agent gesetzt, nicht Emil (Studie §6, H1).

## 3. Kernbefunde (gegengeprüft; Details und n in der Studie §2)

1. **Tote und leere Plätze** erklären den größten Teil des Punkterückstands: 15 von 33 Aufstellungsplätzen in ST1–3 lieferten zusammen −313 Punkte; die übrigen lagen auf Liga-Niveau.
2. **Kaufqualität:** eigene Käufe 25–27 Punkte je Spieler-Spieltag, Liga 48–52. Rollenlabels allein (LI/KB) haben versagt; beobachtete Einsatzminuten trennen besser.
3. **Käufe von Managern:** 5 von 5 im Minus, −16,41 M (62 % des Handelsverlusts). Ob Kanal oder Auswahl die Ursache ist, bleibt umstritten.
4. **Phantomplätze:** der Mannschaftsplan zählte Gebote auf Manager-Listings als besetzte Plätze (359 von 528 Zielen; 0 von 8 Annahmen seit 13.09.) und band damit das Geld für echte Auktionen.
5. **Mindestgebot:** 528 von 528 Geboten waren das Minimum; bei ≤ +4 % über Marktwert 0 von 8 Zuschlägen.
6. **Überziehung und späte Notbremse:** 30,0 M Käufe bei 4,2 M Konto; 94 h ohne Handeln; bei T-15 drei Verkäufe, wo einer gereicht hätte (−300 statt −100 Punkte).
7. **Zweiter Schreiber:** Hermes 53 von 116 Handels-Writes, 92 % der Agenten-Writes unter Pauschalfreigabe, 31 % der eigenen Gebote selbst storniert.
8. **Doktrinwechsel ohne Review:** 60 Decision-Records in 29 Tagen, 0 von 29 versprochenen Reviews.

**Nicht tragfähig (nicht bauen):** Handelsgewinn als Punktehebel, harte Trend-Tore, Mindesthaltedauer, Wochentags- und Formationsregeln, 30-%-Konzentrationsriegel. Der Kaufaufschlag (Median 8,3 %) ist Liga-Normalität und keine Verlustursache.

**Ehrliche Wirkung:** Die neuen Regeln verhindern Schaden, erzeugen aber keine Punkte. Erwartung mit vorhandenem Geld: 595–630 Punkte je Spieltag (Platz 10–11). Platz 8–9 (~750) verlangt Kapital in lebenden Plätzen, also die Olise-Entscheidung — die trifft nur Emil (Studie §7).

## 4. Der Plan in Kürze (Studie §3–§6)

Zehn Regeln: Elf zuerst (11 Plätze, Konto ≥ 10.000 € zur Frist, Gleitpfad 3/2/0 offene Plätze) · ein Platz zählt nur bei Besitz oder Kickbase-Auktion mit Ende ≥ 12 min vor der Frist · Rolle vor Preis (ab 5 M nur KB ≤ 2 und ≥ 60 min in den letzten beiden Spieltagen) · Preisregel statt Mindestgebot (braucht Emils Ja) · Marktwert-Trend ist Preis, kein Tor · Abgänge nur per Emils Klick, autonom verkauft nur die Anpfiff-Notbremse · nur echtes Geld mit 10.000-€-Puffer in jedem Pfad · ein Schreiber, überstimmen darf nur Emil · ein Änderungspaket je Woche mit Review des Vorgängers · Erfolg = lebende Plätze und Punkte je Platz.

Vier Wochenpakete; volle Liste mit Datei, Änderung, Test, Siegel- und Record-Kennzeichen im Anhang-JSON (`change_list`, 37 Einträge).

**Paket 1 (Ziel Di 22.09., reine Verschärfungen, kein Eingriff an `agent/executor.py`, keine versiegelte Datei):**

| ID | Ort | Änderung | Record |
|---|---|---|---|
| P0-1 | `cli/plan.py` (reject_reason ~440, 455–458, 469–488), Aufrufer `worker/intents.py` | Manager-Listings im autonomen Plan ablehnen; laufende Manager-Gebote reservieren Geld, zählen keinen Platz; vetoed/failed/aufgegebene Ziele vor dem Optimierer ausschließen, stehende Kickbase-Gebote bleiben gebunden | ja |
| P0-2 | `cli/plan.py:953` | `listing_type` je Beleg aus `seller_id` statt veralteter Schleifenvariable (25 % der Belege falsch); auch für abgelehnte Kandidaten speichern | nein |
| P0-3 | `cli/plan.py:379/429/468`, `local_engine.py:216/299` | Planungsgeld = Konto − Gebote − 10.000 €; Einzelrahmen 1,0 bei Elf-Lücke, 0,70 bei Upgrade; Guardrail-Default und Wächter-Puffer bleiben | ja (2) |
| P0-4 | neu `worker/outcome_guard.py` | Ergebnis-Wächter als eigener Job: Gleitpfad-Alarm, Deckung unter Puffer sofort, Konto unter Puffer > 24 h, Statuswechsel bei Klumpenspieler; eine Karte je Zustandswechsel; nicht als fünfte Anpfiff-Stufe (vier Stufen sind versiegelt gepinnt) | ja |
| P0-5 | `cli/plan.py` reject_reason | minimaler Rollenfilter ab 5 M; ohne Minutendaten „Rollenbeleg fehlt" statt Null-Bewertung | ja |
| P0-6 | `executor.py:314–319`, `intents.py:2001–2002` (nur Kommentare), `bid_watch.py:268`, `worker/__main__.py` | falsche `emil_go`-Kommentare richtigstellen; Jobs Überraschungen, Gebots-Wächter, Auto-Trading 22:30 abmelden | nein |
| P0-7 | Doku, Skill, Hermes-Skript, Memory | Pauschalmandate archivieren; CLAUDE.md und Memory nur mit Emils Einverständnis | nein |
| P0-8 | `server/routes/intents.py:373` | Absender-Whitelist als Stolperdraht (hermes/codex/unbekannt → 403); Veto-Routen bleiben offen | ja |
| P0-9 | `mcp/tools_ops.py:201–241` | `deploy_verify` verweigert bei schmutzigem Baum, falschem Branch oder ungepushtem HEAD; Deploy-Bericht mit Commit-Hash | nein |
| P0-10 | `jobs/snapshot.py`, `worker/__main__.py` | wöchentliches Voll-Nachladen von Marktwert- und Leistungsdaten aller ~427 Spieler; danach Schwellen auf vollen Daten neu rechnen, bevor Paket 2 sie festschreibt | nein |

Reihenfolge laut Studie: Paket 1 live und mit Wirkungsnachweis belegt → erst dann Ache-Verkauf (Emils Entscheid E4) → Kauf 1 über die App. Pakete 2–4: 29.09., 13.10., ab 20.10. (Preisregel, lebende Elf, Abgänge per Klick, Notbremse erst im Schattenlauf, Schreibstellen-Verschärfungen, Inhaber-Auftrag per Discord-Tap, Aufarbeitung Commit `8eed081`, weniger Oberfläche).

## 5. Regeln für die bauende Session

- Arbeit nur in einem eigenen `git worktree`, genau eine Session deployt; vor jedem Deploy: Baum sauber, Branch `main`, HEAD = `origin/main`, Stash gesichtet; nie 21:25–22:25; kein Deploy an Schreibstelle oder Wächtern in den 72 h vor der Frist.
- Bricht ein versiegelter Test: stoppen. `guardrails.lock` setzt nur Emil.
- Jede Parameteränderung mit Decision-Record (`old/new/reason/expect/review/commit`); kein neues Paket ohne ausgefülltes Review des Vorgängers.
- Kein Kickbase-Write ohne Emils namentlichen Auftrag für genau diesen Spieler. Verweigert eine Sicherung die genannte Aktion: stoppen und melden, kein Ersatzspieler.
- Alle Schwellen der Studie sind Startwerte aus kleinen Stichproben (3–4 Spieltage, 20 eigene Auktionen, 5 Manager-Käufe, 1 Notbremse). Review nach ST5–ST8.

## 6. Grenzen der Studie

- Prüftiefe war nach einem Sitzungslimit reduziert: Forensik voll geprüft; Rivalen, Markt, Punkte mit zwei Linsen; Engine, Sicherungen, Betrieb, Meta nur „Nachrechnen" bei hohem Hebel, mittlere dort ungeprüft. Code-Stellen (file:line) vor dem Bauen selbst nachlesen.
- Die Sofortplan-Befunde vom 18.09. sind veraltet und wurden nicht verwendet.
- Die Marktwert-Historie im Snapshot deckt nur Spieler „auf dem Radar" ab; P0-10 schließt das. Marktsnapshots, Kontoverlauf und Rivalenkonten fehlen.
- Der Snapshot enthält keine Tokens (Tabelle `users` leer). Er liegt gitignored unter `.local-backups/` und gehört nicht ins Repo.

## 7. Offene Entscheidungen für Emil (volle Tabelle: Studie §9)

| Wann | Was |
|---|---|
| heute Mo 21.09. | E2 Manager-Listings raus (Schweigen = Ja) · E10 Pauschalmandate widerrufen, Web-App-Passwort und Schreib-Token wechseln, Hermes-Crons abschalten · H1 Alt-Listings von Minjae, Schwäbe, Scheller (und Ullrich) in der App entfernen · E6 Stash löschen oder auslagern |
| Di 22.09. | E4 Ache zum Marktwert an Kickbase verkaufen (nach Paket-1-Nachweis) |
| Mo 28.09. | E1 Preisregel · E12 lebende Elf als Steuergröße · E13 Annahmen nur per Klick |
| vor Di 13.10. | E3 Teilmengen-Notbremse mit 25-%-Klausel · E8 Verkaufs-Floor und Wiedereinstiegssperre · E11 Siegel-Hash-Datei |
| offen | E5 Olise halten, verkaufen oder Option offen; Schutzliste wieder füllen? · E7 Commit `8eed081` · E14 welche Jobs werden wirklich genutzt |

## 8. Vorschlag für den ersten Schritt der bauenden Session

1. Studie §2–§6 und den Anhang (`change_list`, IDs P0-1 bis P0-10) lesen, die genannten Code-Stellen selbst prüfen.
2. Worktree anlegen, P0-1/-2/-3/-5 gemeinsam (alle in `cli/plan.py`, nicht parallel in getrennten Bäumen), danach P0-4, P0-8, P0-9, P0-10, P0-6; P0-7 nach Emils Einverständnis.
3. Tests zuerst (Testideen stehen je Eintrag im Anhang), Suite und `ruff` grün, versiegelte Tests unverändert, lesendes Zweitreview (`codex exec -s read-only` oder zweite Session).
4. Deploy frühestens Di 22.09. mit Wirkungsnachweis am nächsten Engine-Bericht: 0 Manager-Ziele, 0 gesperrte Ziele, jedes Preisszenario lässt ≥ 10.000 € übrig, Ziele ab 5 M mit Rollenbeleg.
