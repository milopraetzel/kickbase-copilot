# Kickbase-Agent Worker — Discord-Bot + Scheduler (24/7)
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Abhängigkeiten zuerst (Layer-Cache), dann Code
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# SQLite-Datenbank liegt im Volume unter /app/data
ENV DATABASE_URL=sqlite:////app/data/kba.db
VOLUME ["/app/data"]

ENTRYPOINT ["./docker-entrypoint.sh"]
