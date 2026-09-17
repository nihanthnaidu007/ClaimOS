# syntax=docker/dockerfile:1

# ---- Stage 1: builder — uv resolves the pinned requirements into a venv ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependency layers before source for cache hits on unchanged requirements
COPY requirements.txt ./
# Vendored wheel for emergentintegrations==0.1.0 — see vendor/README.md
COPY vendor/ ./vendor/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python \
        --find-links /app/vendor \
        -r requirements.txt

# ---- Stage 2: runtime — slim, non-root, healthchecked ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --chown=appuser:appuser . /app/

RUN groupadd --gid 10001 appuser && \
    useradd --uid 10001 --gid appuser --create-home appuser

USER appuser

EXPOSE 8000

# Liveness probe (process-up only, per the CI/CD research doc). /health is added
# by the production-hardening task; until then /api/ is the existing endpoint
# that proves the app is serving.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/', timeout=2)" || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
