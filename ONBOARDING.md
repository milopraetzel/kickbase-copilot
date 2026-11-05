# Interaktives KI-Onboarding & Regelwerk (`agent_rules.json`)

Nicht jeder Manager möchte, dass der Agent sofort vollautomatisch Stammspieler tauscht. Deshalb besitzt der **Kickbase KI-Copilot** ein zentrales Regelwerk (`data/agent_rules.json`), das **synchron über zwei Wege** bedient wird:

1. **In der Web-App (`http://localhost:8080`)** im eigenen, vollwertigen 6. Haupt-Tab **„⚙ Regelwerk“** (mit 3 Schnell-Profilen, allen Reglern und anklickbarer Kader-Schutzliste).
2. **Direkt über den KI-Chat (Claude Code, Cursor, Antigravity, Codex) oder im Terminal** über das geführte 5-Schritte-Interview (`uv --directory api run kba onboarding`).

Beide Wege lesen und schreiben **dieselbe Datei (`data/agent_rules.json`)**.

---

## Das 5-Schritte-Interview für KI-Assistenten (Protokoll)

> **Verbindliche Anweisung für Claude Code, Cursor, Antigravity und Codex:**
> Sobald der Nutzer im Chat Sätze schreibt wie:
> - *„Mach ein Onboarding mit mir“*
> - *„Richte meinen Agenten ein / Was darfst du alles?“*
> - *„Ich will, dass du nur unter der Woche tradest, aber Olise nie anfasst“*
>
> **führe als KI-Assistent diesen strukturierten 5-Schritte-Dialog durch (stelle echte Fragen und erkläre kurz das Warum):**

### Schritt 1: Aktuellen Stand prüfen & die 3 Grund-Profile erklären
Prüfe zunächst den Stand via `uv --directory api run kba rules show` und erkläre dem Nutzer die 3 Grund-Profile:
1. **Nur Berater (`dry_run`)**: Rechnet Kader, 4-Säulen-Prognose, Jev-News und Markt komplett durch, gibt aber niemals echte Gebote oder Verkäufe in Kickbase ab.
2. **Co-Pilot mit Veto-Countdown (`veto_copilot`)**: Plant Transfers und Marktwert-Trades mit Countdown im Cockpit. Der Nutzer sieht jede Begründung vorab und behält das letzte Wort.
3. **Voll-Autopilot (`full_autopilot`)**: Führt Stammelf-Upgrades, Unter-der-Woche-Trading, automatische Startelf-Aufstellung und den Freitags-Dispo-Ausgleich komplett selbstständig aus.

### Schritt 2: Gebots-Obergrenze & Umgang mit der Startelf klären
Frage den Nutzer:
* *„Bis zu wie viel Mio. € darf ich maximal ohne Rückfrage auf einen Spieler bieten (`max_bid_million`), und wie viel % maximal über Marktwert (`max_overpay_pct`)?“*
* *„Welche Spieler darf ich verkaufen (`allow_sells`)? Gar keine (`none`), nur Bank- & Trading-Spieler (`bench_only`), oder auch Startelf-Spieler, nachdem ich zuvor ein stärkeres Upgrade ersteigert habe (`swap_allowed`)?“*

### Schritt 3: Unter-der-Woche Marktwert-Trading im Dispo
Erkläre dem Nutzer kurz: *„Unter der Woche (Mo–Do) kann ich deinen 33-%-Dispo nutzen, um steigende Spieler zu kaufen und beim ersten Kippen der Kurve mit Gewinn wieder zu verkaufen. Ein automatischer Sicherheits-Haircut (`friday_buffer_haircut_pct`) schützt davor, dass ein Rücksetzer am Donnerstag deine Stammelf gefährdet.“*
* Frage: *„Soll ich Unter-der-Woche-Trading aktivieren (`allow_mw_trading`), und wie viel Mio. € darf maximal gleichzeitig in Trading-Spielern stecken (`trade_budget_cap_million`, z. B. 20 Mio. € konservativ / 50 Mio. € offensiv)?“*

### Schritt 4: Freitagabend-Vorbereitung (Startelf & Konto-Wächter)
* Frage: *„Darf ich vor Spieltagsbeginn automatisch die beste Startelf in Kickbase aufstellen (`allow_lineup`), und soll der Freitagabend-Wächter dafür sorgen, dass dein Konto vor Anpfiff garantiert ≥ 0 € ist (`allow_friday_rescue`)?“*

### Schritt 5: Unantastbare Lieblingsspieler (`protected_players`) & Speichern
* Frage: *„Welche deiner Lieblingsspieler (z. B. Olise, Musiala, Guirassy) soll ich auf die eiserne Schutzliste setzen, sodass sie niemals verkauft werden?“*
* Speichere alle Antworten mit einem einzigen Befehl:
  ```bash
  uv --directory api run kba rules set \
    --preset veto_copilot \
    --max-bid-million 18.0 \
    --max-overpay-pct 5.5 \
    --allow-sells bench_only \
    --mw-trading \
    --trade-budget-cap 20.0 \
    --friday-haircut-pct 3.5 \
    --lineup \
    --friday-rescue \
    --protected "Olise, Musiala"
  ```
* Bestätige dem Nutzer anschließend die Tabelle und weise darauf hin, dass alle Regler im Web-Cockpit unter **„⚙ Regelwerk“** sofort synchronisiert sind.
