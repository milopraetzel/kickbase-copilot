# Reproduzierter Planungsfehler und begrenzte Korrektur

Der lokale `cli/plan.py` war vor der Korrektur bytegleich mit dem aktuellen
Produktionssnapshot unter `/tmp/kba-strategy-v2-live/src`. Die drei folgenden
Regressionen liefen am tatsächlichen `build_plan` rot:

1. 2 Mio. € Konto minus 8 Mio. € offene Gebote ergaben `plan_base=0` statt −6 Mio. €.
2. Bei acht verifizierten Spielern und 10 Mio. € Konto vergrößerte der aktive
   Pfandpfad die Planbasis auf 28.542.857 €. Der hierfür unterstellte Rückverkauf
   der Zielspieler hätte offene Starterlücken erneut erzeugt.
3. Ein positionsgleicher Ersatz mit identischem Gesamtelfindex wurde für
   12.013.337 € vorgeschlagen, obwohl der abzugebende Spieler 10 Mio. € wert war.

Nach der Korrektur bleibt `budget − open_bid_exposure` vorzeichengetreu.
Der bestehende Pfandhebel wird bei noch offenen verifizierten Formationslücken
nicht aktiviert. Die bestehenden Preis-, Mengen-, Kredit-, Schutz- und
Ausführungsgrenzen werden nicht verändert.

Ein zusätzlicher reiner Filter in `decision/roster_value.py` prüft die echte beste
vollständige Elf vor und nach einem Tausch. Ein gleicher Index begründet keine
zusätzlichen Kosten. Zulässig bleibt eine bessere vollständige Elf oder eine
gleichwertige Elf mit konservativ positivem Finanzierungseffekt. Der Filter
verwendet den bereits vorhandenen Reserve-Haircut, keine neue Renditeannahme.
Er prüft einen **notwendigen Mindestnachweis**, keine verifizierte Punktprognose
oder mathematisch bewiesenen fairen Kaufpreis.

Alle offenen Gebote sind bereits im freien Konto berücksichtigt, alle normalen
neuen Kaufvorschläge werden zusätzlich vor den Tauschen abgezogen. Akzeptierte
Tausche reservieren ihren **positiven** konservativen Nettobetrag nacheinander.
Ein Überschuss aus einem günstigen Tausch wird nicht zur Finanzierung anderer
unabhängiger Auktionen freigegeben: Nur die teuren Zuschläge zu erhalten muss
ebenfalls finanzierbar bleiben. Ein kleiner
lokaler Indexgewinn bei einem Bankspieler, der die beste Elf unverändert lässt,
rechtfertigt keine Mehrkosten. Noch unvollständige Elfen stellen Tausche zurück;
erst die belegten Starterlücken werden geschlossen.

Die unabhängige Nachprüfung ergänzte drei zuerst rote Regressionen:

1. Bei 4 Mio. € Deckung finanzierten bedingte Erlöse eines günstigen Tauschs zwei
   teure Tausche mit jeweils 3.013.337 € Nettobedarf. Fallen nur die beiden teuren
   Zuschläge an, fehlen 2.026.674 €. Jetzt bleibt genau einer davon finanzierbar.
2. Ein hoher Kaufindex bei unverändertem Elfindex verdeckte einen weiteren
   Kandidaten mit besserem Elfindex. Nach Ablehnung wird derselbe vorhandene
   Preis-/Schutzpfad mit dem nächsten Kandidaten erneut geprüft; Ausschlüsse
   gelten pro Abgang, bereits angenommene Ziele für den ganzen Plan.
3. Ein normaler Kauf mit Elfindex 0,90 verdrängte bereits den vorhandenen
   Verteidiger mit 0,50. Der zusätzliche Tausch auf 0,55 brachte bei beiden
   Zuschlägen keinen weiteren Elfgewinn. Der Vergleich berücksichtigt deshalb
   alle normalen Kaufvorschläge gemeinsam mit den zuvor angenommenen Tauschen.
   Diese bedingte Mannschaft gilt ausdrücklich nicht als heutiges Eigentum und
   darf den Nachweis einer bereits vollständig belegten Elf nicht ersetzen.

Der bestehende Worker-Pfad kauft weiterhin zuerst den Ersatz und wartet mit dem
Abgang auf den bestätigten Erwerb. Der Filter verändert diesen Ausführungspfad
nicht. Seine akzeptierte Begründung benennt Gesamtelfindex und Restfinanzierung;
`Plan.roster_value_notes` hält Ablehnungen für Diagnose/Anzeige fest.

Tests: elf neue Regressionen/Gegenproben sowie vorhandene Plan-, Swap- und
Worker-Swap-Tests: **36 bestanden**, Ruff für die berührten Dateien clean.
`api/guardrails.lock` wurde vorab geprüft; keine versiegelte Testdatei wurde
geändert. Änderungen sind begrenzt auf `cli/plan.py`, neues
`decision/roster_value.py` und neue `tests/cli/test_plan_roster_value.py`.
Root dokumentiert das neue Policy-Gate in einem Decision-Record; numerische
Parameter und Modelle bleiben unverändert. Kein Deployment oder Handel erfolgt
durch diese Korrektur.

Reproduktion:

```sh
cd "/Users/emil/Mein Claude /Kickbase-Agent/api"
uv run --extra dev pytest -q tests/cli/test_plan_roster_value.py tests/cli/test_plan.py tests/decision/test_swaps.py tests/worker/test_swap_pipeline.py
```
