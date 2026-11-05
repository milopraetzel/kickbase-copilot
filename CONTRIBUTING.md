# Contributing Guide & Staging-Workflow

Vielen Dank für dein Interesse, am **Kickbase KI-Copilot** mitzuentwickeln! Damit niemand beim Entwickeln versehentlich echte Gebote in einer laufenden Liga auslöst, arbeiten wir mit einer strikten **Staging-Umgebung** und einem klaren Branch-Modell.

---

## 1. Branch-Modell (`feature/*` → `staging` → `main`)

* **`main`**: Stabiler Produktions-Branch für Self-Hosted-Deployments.
* **`staging`**: Integrations-Branch zum gemeinsamen Testen neuer Features gegen die Staging-Umgebung.
* **`feature/<thema>` oder `fix/<thema>`**: Bitte stelle Pull Requests zunächst gegen den **`staging`**-Branch (oder aus deinem Fork).

---

## 2. Isolierte Staging-Umgebung (`http://localhost:8081`)

Du benötigst für die Entwicklung **keinen echten Kickbase-Account**. Unsere Staging-Umgebung erzeugt automatisch einen realistischen Bundesliga-Datensatz (`kba staging-seed`) und erzwingt `AGENT_DRY_RUN=true`:

```bash
# Staging-Container bauen & starten (Port 8081)
make staging
```

Öffne danach **[http://localhost:8081](http://localhost:8081)**:
* Alle 6 Haupt-Ansichten (`Heute`, `Übersicht`, `Alle Spieler`, `Liga`, `Jev-Analyse`, `⚙ Regelwerk`) sind sofort mit realistischen Daten gefüllt.
* Die API (`/api/rules`, `/api/intents`, `/api/health`) läuft im Container `kickbase-api-staging`.

Alternativ ohne Docker direkt lokal:
```bash
cd api
uv sync --extra dev
uv run kba staging-seed --out /tmp/staging.html
open /tmp/staging.html
```

---

## 3. Eiserne Architektur-Regeln (First Principles)

1. **Einzige Schreibstelle nach Kickbase (`api/src/kickbase_agent/agent/executor.py`):**
   Kein Modul außerhalb von `executor.py` darf jemals schreibende HTTP-Calls an Kickbase senden.
2. **Versiegelte Sicherheits-Tests (`api/guardrails.lock`):**
   Die 9 Kern-Sicherheitstests sind per SHA-256 versiegelt (`uv run kba guardrails-check`). Sie dürfen durch PRs nicht aufgeweicht werden.
3. **Code-Qualität vor jedem Pull Request:**
   ```bash
   make lint
   make test
   ```
   - Maximal 100 Zeichen pro Zeile (`ruff check src/ tests/`)
   - Deutsche Kommentare & Docstrings
   - Keine Emojis in `.py`-Quelltexten
