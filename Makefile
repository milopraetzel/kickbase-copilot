.PHONY: help dev staging staging-down test lint onboarding seed-staging

help:
	@echo "Kickbase KI-Copilot — Befehle:"
	@echo "  make staging       Startet isolierte Staging-Umgebung (Demo-Daten, Port 8081)"
	@echo "  make staging-down  Stoppt die Staging-Umgebung"
	@echo "  make dev           Startet Live-/Self-Hosted-Umgebung (mit api/.env, Port 8080)"
	@echo "  make onboarding    Startet den interaktiven 5-Schritte-Onboarding-Assistenten"
	@echo "  make lint          Fuehrt Ruff Linter & Guardrail-Check aus"
	@echo "  make test          Fuehrt die komplette Pytest-Suite aus"

staging:
	docker compose -f docker-compose.staging.yml up -d --build
	@echo "Staging-Cockpit laeuft auf http://localhost:8081 (Dry-Run aktiv)"

staging-down:
	docker compose -f docker-compose.staging.yml down

dev:
	docker compose --env-file api/.env up -d --build
	@echo "Cockpit laeuft auf http://localhost:8080"

onboarding:
	cd api && uv run kba onboarding

seed-staging:
	cd api && uv run kba staging-seed --out /tmp/kba-staging-cockpit.html

lint:
	cd api && uv run --extra dev ruff check src/ tests/ && uv run kba guardrails-check

test:
	cd api && uv run --extra dev pytest -q
