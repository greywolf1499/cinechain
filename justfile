set dotenv-load := true

# Backend (run from repo root; targets backend/ via uv --project)
backend-dev:
    cd backend && CONFIG_DIR=${CONFIG_DIR:-./.dev-config} uv run alembic upgrade head && \
    CONFIG_DIR=${CONFIG_DIR:-./.dev-config} uv run uvicorn app.main:app --reload --port 8787

backend-test:
    cd backend && uv run pytest -q

backend-lint:
    cd backend && uv run ruff check .

backend-migrate:
    cd backend && CONFIG_DIR=${CONFIG_DIR:-./.dev-config} uv run alembic upgrade head

backend-migration name:
    cd backend && CONFIG_DIR=${CONFIG_DIR:-./.dev-config} uv run alembic revision --autogenerate -m "{{name}}"

# Frontend
frontend-dev:
    cd frontend && npm run dev

frontend-build:
    cd frontend && npm run build

# Docker
docker-build:
    docker compose build

docker-up:
    docker compose up -d

docker-dev:
    docker compose -f docker-compose.dev.yml up --build
