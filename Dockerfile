# syntax=docker/dockerfile:1.7
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

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

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).read()"]

CMD ["python", "-m", "uvicorn", "trade_platform.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
