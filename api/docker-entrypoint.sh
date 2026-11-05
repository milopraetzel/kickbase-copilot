#!/bin/sh
set -e
# Datenbank-Schema aktuell halten, dann den Worker (Bot + Scheduler) starten.
mkdir -p /app/data
uv run alembic upgrade head
exec uv run kba-worker
