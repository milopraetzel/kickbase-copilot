# CLAUDE.md — Anleitung für Claude Code & KI-Assistenten

Siehe [`AGENTS.md`](./AGENTS.md), [`ONBOARDING.md`](./ONBOARDING.md) und [`CONTRIBUTING.md`](./CONTRIBUTING.md).

## Wichtige Befehle
* **5-Schritte-Onboarding starten (Terminal / KI-Chat):**
  ```bash
  uv --directory api run kba onboarding
  ```
* **Regeln anzeigen oder per KI-Chat setzen (synchron mit Web-App Tab `⚙ Regelwerk`):**
  ```bash
  uv --directory api run kba rules show
  uv --directory api run kba rules set --preset veto_copilot --max-bid-million 18 --allow-sells bench_only --protected "Olise, Musiala"
  ```
* **Staging-Umgebung starten (`http://localhost:8081`, ohne echten Kickbase-Login):**
  ```bash
  make staging
  ```
* **Linter, Guardrail-Siegel & Tests prüfen:**
  ```bash
  make lint && make test
  ```
