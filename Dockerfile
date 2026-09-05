# syntax=docker/dockerfile:1.7
FROM python:3.12.14-slim-bookworm@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

RUN groupadd --gid 10001 tradeplatform \
    && useradd --uid 10001 --gid 10001 --no-log-init --home-dir /app \
        --shell /usr/sbin/nologin tradeplatform

COPY requirements-runtime.txt /tmp/requirements-runtime.txt
RUN python -m pip install --no-deps --requirement /tmp/requirements-runtime.txt \
    && python -m pip check \
    && rm /tmp/requirements-runtime.txt

WORKDIR /app
COPY --chown=10001:10001 src ./src

USER 10001:10001
EXPOSE 8000

# Liveness only: cheap process health, never a database round trip.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).read()"]

# Serves the canonical runtime composition root (trade_platform.runtime_app), not the
# unconfigured trade_platform.api:app default. This factory reads TRADE_PLATFORM_ENVIRONMENT
# and POSTGRES_DSN and fails closed for paper/production if PostgreSQL is missing or
# unreachable -- see src/trade_platform/runtime_app.py and
# docs/MODULE_3C_POSTGRES_RUNTIME_WIRING.md.
CMD ["python", "-m", "uvicorn", "trade_platform.runtime_app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
