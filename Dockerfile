# syntax=docker/dockerfile:1

########################################
# Stage 1: frontend build (Node/Vite/Tailwind)
########################################
FROM node:22-slim AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

########################################
# Stage 2: python dependency build (uv)
########################################
FROM python:3.12-slim AS backend-build
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/
# WORKDIR must match the runtime stage's path: uv bakes an absolute shebang
# into console-script entrypoints (e.g. .venv/bin/alembic), so the venv has
# to be built at the exact path it will live at in the final image.
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
	UV_LINK_MODE=copy \
	UV_PYTHON_DOWNLOADS=never
COPY backend/pyproject.toml backend/uv.lock* ./
RUN uv sync --no-install-project --no-dev
COPY backend/ ./
RUN uv sync --no-dev

########################################
# Stage 3: runtime
########################################
FROM python:3.12-slim AS runtime

# gosu drops root privileges after PUID/PGID setup; tini reaps zombie processes.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends gosu tini \
	&& rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 cinechain && useradd -u 1000 -g cinechain -M -d /nonexistent cinechain

WORKDIR /app
COPY --from=backend-build /app/.venv /app/.venv
COPY backend/app /app/app
COPY backend/migrations /app/migrations
COPY backend/alembic.ini /app/alembic.ini
COPY --from=frontend-build /src/frontend/dist/ /app/app/static/
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/healthcheck.py /healthcheck.py
RUN chmod +x /entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
	PYTHONUNBUFFERED=1 \
	PYTHONDONTWRITEBYTECODE=1 \
	PUID=1000 \
	PGID=1000

EXPOSE 8787
VOLUME ["/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
	CMD python3 /healthcheck.py

ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--no-access-log"]
