## Beschreibung

Kurze Zusammenfassung der Änderung (First Principles: Was wird vereinfacht, repariert oder ergänzt?).

## Checkliste vor dem Merge

- [ ] Gegen die **Staging-Umgebung** (`make staging` / `uv run kba staging-seed`) getestet
- [ ] `cd api && uv run --extra dev ruff check src/ tests/` läuft mit **0 Fehlern** durch
- [ ] `cd api && uv run kba guardrails-check` ist **grün**
- [ ] `cd api && uv run --extra dev pytest -q` ist **grün**
- [ ] Keine Schreibzugriffe außerhalb von `api/src/kickbase_agent/agent/executor.py`
- [ ] Keine privaten Daten, Liga-IDs oder `.env`-Werte enthalten
