"""Canonical worker composition root: environment -> PostgreSQL authorities -> scheduler loop.

Mirrors :mod:`trade_platform.runtime_app`'s fail-closed, no-SQLite-fallback
composition pattern for the API -- environment variables are read once, PostgreSQL
is the only backing store, and missing/unreachable configuration raises
:class:`~trade_platform.runtime_app.RuntimeCompositionError` rather than degrading to
an unconfigured or partially-wired worker -- but composes the durable operational-job
authorities (:mod:`trade_platform.scheduler`) instead of the read/audit API surface.

Unlike ``runtime_app``, composition here happens inside the ASGI ``lifespan`` rather
than at module import time. A worker has no safe no-op mode analogous to
``RuntimeMode.LOCAL_RESEARCH`` -- it always needs PostgreSQL -- so composing eagerly
at import time would make importing this module (which test discovery does) fail
whenever ``POSTGRES_DSN`` is unset. Composing in ``lifespan`` keeps import always safe
while still failing closed the moment the ASGI server actually starts the process
(``uvicorn`` runs ``lifespan`` startup before serving any request), which is the point
in the worker's lifecycle that matters operationally.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import FastAPI, HTTPException

from .domain import utc_now
from .operational_alerts import PostgresOperationalAlertStore
from .operational_jobs import PostgresOperationalJobStore
from .persistence import PersistenceError, PostgresDatabase
from .retention_evidence import PostgresRetentionEvidenceStore
from .runtime_app import RuntimeCompositionError
from .scheduler import JobContext, SchedulerWorker, default_job_registry

__all__ = ["WorkerRuntime", "app", "create_worker_runtime_from_environment"]

_DEFAULT_POLL_SECONDS = 30
_MINIMUM_POLL_SECONDS = 5


def _require_env(env: Callable[[str], str | None], name: str) -> str:
    value = env(name)
    if not value:
        raise RuntimeCompositionError(f"required_environment_variable_missing:{name}")
    return value


@dataclass(slots=True)
class WorkerRuntime:
    """Owns the worker's single PostgreSQL connection and its background tick loop."""

    database: PostgresDatabase
    worker: SchedulerWorker
    poll_seconds: int
    last_tick_at: datetime | None = field(default=None, init=False)
    last_tick_ok: bool = field(default=False, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _stopping: bool = field(default=False, init=False, repr=False)

    async def _loop(self) -> None:
        while not self._stopping:
            as_of = utc_now()
            try:
                self.worker.run_tick(as_of)
                self.last_tick_ok = True
            except Exception:  # noqa: BLE001 - a failed tick must never crash the worker process
                self.last_tick_ok = False
            self.last_tick_at = as_of
            try:
                await asyncio.sleep(self.poll_seconds)
            except asyncio.CancelledError:
                break

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Graceful shutdown: stop scheduling new ticks, let an in-flight one finish, close the connection."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.database.close()

    def is_ready(self, *, now: datetime) -> bool:
        if self.last_tick_at is None:
            return False
        staleness_seconds = (now - self.last_tick_at).total_seconds()
        return staleness_seconds <= self.poll_seconds * 3


def create_worker_runtime_from_environment(
    *, env: Callable[[str], str | None] = os.environ.get
) -> WorkerRuntime:
    """The single canonical entrypoint: environment -> composed scheduler/worker runtime.

    Reads:
      - ``POSTGRES_DSN``: required; a ``postgres://`` or ``postgresql://`` connection
        string. There is no fallback -- this worker never touches SQLite.
      - ``TRADE_PLATFORM_WORKER_POLL_SECONDS``: optional, default 30; the interval
        between due-state checks. Must be at least 5 seconds.

    Fail-closed behavior mirrors :func:`trade_platform.runtime_app.create_runtime_app_from_environment`:
    missing or invalid configuration, or an unreachable database, raises
    :class:`RuntimeCompositionError` before any runtime object is returned.
    """
    dsn = _require_env(env, "POSTGRES_DSN")
    raw_poll_seconds = env("TRADE_PLATFORM_WORKER_POLL_SECONDS")
    try:
        poll_seconds = _DEFAULT_POLL_SECONDS if raw_poll_seconds is None else int(raw_poll_seconds)
    except ValueError as error:
        raise RuntimeCompositionError("worker_poll_seconds_must_be_an_integer") from error
    if poll_seconds < _MINIMUM_POLL_SECONDS:
        raise RuntimeCompositionError("worker_poll_seconds_too_small")

    try:
        database = PostgresDatabase(dsn)
    except PersistenceError as error:
        raise RuntimeCompositionError("postgres_unreachable_or_invalid_dsn") from error

    try:
        alerts = PostgresOperationalAlertStore(database)
        context = JobContext(
            database=database,
            job_store=PostgresOperationalJobStore(database, alerts=alerts),
            alerts=alerts,
            retention_store=PostgresRetentionEvidenceStore(database),
        )
        worker = SchedulerWorker(context=context, registry=default_job_registry())
    except Exception:
        database.close()
        raise
    return WorkerRuntime(database=database, worker=worker, poll_seconds=poll_seconds)


def _build_app() -> FastAPI:
    holder: dict[str, WorkerRuntime] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime = create_worker_runtime_from_environment()
        holder["runtime"] = runtime
        runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    application = FastAPI(
        title="Trade Investing Panel Worker",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.get("/health/live")
    def liveness() -> dict[str, str]:
        """Cheap process health only -- never touches the database."""
        return {"status": "ok"}

    @application.get("/health/ready")
    def readiness() -> dict[str, str]:
        runtime = holder.get("runtime")
        if runtime is None or not runtime.is_ready(now=utc_now()):
            raise HTTPException(status_code=503, detail="worker heartbeat missing or stale")
        return {
            "status": "ready",
            "last_tick_ok": str(runtime.last_tick_ok),
            "last_tick_at": "" if runtime.last_tick_at is None else runtime.last_tick_at.isoformat(),
        }

    return application


# ASGI entrypoint (served like the API: ``uvicorn trade_platform.worker_app:app``).
# Composition is deliberately deferred to ``lifespan`` -- see module docstring --
# so importing this module (e.g. during test collection) is always safe.
app = _build_app()
