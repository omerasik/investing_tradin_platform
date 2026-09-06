# Module 3F: PostgreSQL Historical Market-Data Authority + Data Health Worker Wiring

Companion to
[MODULE_3E_STAGING_DEPLOYMENT_AND_SCHEDULER.md](MODULE_3E_STAGING_DEPLOYMENT_AND_SCHEDULER.md).
Closes the specific gap that module left open: the scheduler/worker runtime
intentionally did not execute Data Health because there was no PostgreSQL-backed
historical-bar/observation authority for it to read. This module does not activate a
real external market-data provider — that remains explicitly out of scope until
Module 3G.

## 1. What existed before this module

- `SQLiteBarStore` (`market_data.py`) was the only concrete implementation of the
  provider-neutral historical OHLCV bar authority: validated batch ingestion,
  PIT-safe `available_as_of`, deterministic per-batch version digest.
- `ingest_from_provider()` depended on `SQLiteBarStore` by name in its type
  signature, not on a store-protocol boundary.
- `provider_ingestion.py` already had a PostgreSQL-backed *checkpoint* store
  (`PostgresHistoricalIngestionCheckpointStore`) for raw-ingestion resume evidence,
  and `historical_market_data.py` already had a separate, more elaborate
  authorized-source raw→normalize→seal→research-query pipeline
  (`PostgresHistoricalMarketDataPipeline`) for multi-observation-kind (OHLCV,
  dividends, splits, symbol changes, delistings), point-in-time-correct backtest
  datasets. Neither of those is what `ingest_from_provider`/`SQLiteBarStore`
  actually use — they solve a different, more general problem (authorized,
  versioned research datasets), and this module does not duplicate or replace them.
- `PostgresDataHealthStore` (`data_health.py`) already existed and was already used
  to gate signal validation (`enforce_data_health_signal_validation` trigger,
  Module 3D-era migration `20260815_0010`) — but nothing in the worker ever called
  `detect_data_health`/`build_assessment` against real persisted market data.

## 2. Provider-neutral historical bar authority boundary

`market_data.py` now defines `HistoricalBarStore`, a structural `Protocol`:

```python
class HistoricalBarStore(Protocol):
    def ingest(self, bars: list[OHLCVBar]) -> str: ...
    def available_as_of(self, instrument_id: str, interval: str, decision_at: datetime) -> list[OHLCVBar]: ...
    def read_range(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[OHLCVBar]: ...
    def known_series(self) -> list[tuple[str, str]]: ...
```

`ingest_from_provider()` now types its `store` parameter against this boundary
instead of `SQLiteBarStore` by name. `SQLiteBarStore` gained `read_range` and
`known_series` (satisfying the protocol structurally, no inheritance needed) and is
otherwise unchanged — it remains the LOCAL_RESEARCH default. Validation
(`assess_bars`) and the batch version digest (`batch_digest`, factored out of
`SQLiteBarStore.ingest`) are shared by every implementation, so there is exactly one
place that decides what makes a batch valid.

## 3. `PostgresHistoricalBarStore` (`postgres_market_data.py`)

A production-capable `HistoricalBarStore`, backed by a new `historical_bars` table
(migration `20260906_0038`):

- Exact `NUMERIC(30,12)` price/volume columns — psycopg passes `Decimal` straight
  through, no binary float ever enters the round trip.
- Primary key `(instrument_id, interval, event_at, revision)`: a correction is a new
  revision row, never a mutation of an old one. The table also carries the
  `prevent_immutable_mutation` trigger used by every other append-only evidence
  table in this codebase (`immutable_trigger_sql`, `postgres_schema.py`) — `UPDATE`
  and `DELETE` are rejected at the database level, not just by convention.
- **Idempotent-or-reject ingestion**: `ingest()` attempts
  `INSERT ... ON CONFLICT (instrument_id,interval,event_at,revision) DO NOTHING
  RETURNING ...` per bar (the same idiom `PostgresHistoricalMarketDataPipeline.capture_raw`
  already uses for raw observations). No row returned means the identity already
  exists; the store then compares a content hash (every field except
  `ingested_at`, which a retried attempt naturally advances) against what's
  stored — identical content is a silent no-op (a retried ingestion is safe to
  replay), differing content raises `DataQualityError("conflicting_duplicate_bar_revision")`
  rather than silently overwriting history.
- `available_as_of` reproduces `SQLiteBarStore`'s exact PIT predicate
  (`effective_at <= decision_at AND ingested_at <= decision_at`) — a record is
  only visible once both its effective-time and ingestion-time semantics make it
  historically knowable at the decision timestamp.
- `read_range` and `known_series` support the Data Health worker job and future
  research reads without needing a `decision_at` (they are not PIT gates; they
  answer "what does this store hold").

## 4. Wiring — no SQLite fallback in protected runtimes

- `PostgresPaperCoreAuthorities` (`postgres_runtime.py`) gained a `bars:
  PostgresHistoricalBarStore` field, composed in `build_postgres_paper_core()`
  exactly like every other Postgres authority there — constructed from the same
  `PostgresDatabase`, closed exactly once by the composition root.
- `JobContext` (`scheduler.py`) gained `bar_store: HistoricalBarStore` and
  `data_health_store: PostgresDataHealthStore` fields.
  `create_worker_runtime_from_environment()` (`worker_app.py`) composes both from
  the worker's single required `POSTGRES_DSN` — there is no code path in the
  worker that can construct a SQLite or in-memory bar/data-health store; a missing
  or unreachable `POSTGRES_DSN` fails closed with `RuntimeCompositionError` exactly
  as it already did for every other worker authority.
- LOCAL_RESEARCH is untouched: `SQLiteBarStore` remains the default there, and
  nothing about this module changes how local/demo/test workflows construct it.

## 5. The `data_health_evaluation` job

Registered in `scheduler.default_job_registry()` as a fourth entry alongside the
three Module 3E jobs, so it inherits the exact same advisory-lock claim/release,
idempotency-keyed evidence, and failure-alert model as every other registered job —
no new scheduling mechanism was introduced.

`run_data_health_evaluation(context, as_of)`:

1. Reads `context.bar_store.known_series()` — every `(instrument_id, interval)`
   pair the PostgreSQL bar authority currently holds.
2. **No series at all** (the expected state until a real provider is activated in
   Module 3G): persists a single `GLOBAL` assessment over an empty observation set.
   `detect_data_health` already classifies an empty dataset as blocking
   (`MISSING_BARS`/`INCOMPLETE_DATASET`) — the job never fabricates a healthy
   result just because no data has been ingested yet.
3. **One or more series present**: reads the trailing 24-hour window
   (`read_range`) for each, maps each `OHLCVBar` to a `DataHealthObservation`, and
   persists an `INSTRUMENT`-scoped assessment per series. (No independent
   trading-calendar/session authority is wired to this internal evaluation yet —
   that requires a real, authorized market-data provider, out of scope here — so a
   bar's own recorded timezone is treated as both "actual" and "expected"; the
   `TIMEZONE_SESSION_MISMATCH` check is honestly a no-op today rather than a
   fabricated pass against real session data.)
4. Is safe with zero rows, safe with partial data, and never raises for
   "insufficient data" — that is a normal, successfully-recorded outcome. It only
   raises (surfacing the existing `OPERATIONAL_JOB_EXECUTION_FAILED` alert path)
   on a genuine infrastructure failure (e.g. PostgreSQL unreachable).

Verified end-to-end against a live staging Compose stack
(`docker-compose.staging.yml`): after registering a `data_health_evaluation` policy,
the worker's own tick loop claimed it, executed it, and recorded
`SUCCEEDED` with `{"series_checked": "0", "assessments_persisted": "1",
"blocking_assessments": "1"}`, and the corresponding row appeared in
`data_health_assessments` — no manual invocation, no SQLite, no fabricated health.

## 6. Testing

- `tests/test_scheduler.py`: pure-fake unit tests for `run_data_health_evaluation`
  (no PostgreSQL) covering the empty-series/GLOBAL path, the never-fabricate-health
  path, the trailing-window read, and the unparseable-interval fallback.
- `tests/test_postgres_market_data.py` (PostgreSQL-gated): exact `Decimal`
  round-trip, PIT/as-of correctness, multiple revisions, idempotent duplicate
  ingestion, conflicting-duplicate rejection, provider-provenance enforcement via
  `ingest_from_provider`, and restart persistence.
- `tests/test_scheduler_postgres.py`: the job registered and executed through the
  real `SchedulerWorker`/advisory-lock mechanism, including a concurrency test
  proving two workers never double-execute the same scheduled evaluation. These
  tests deliberately avoid persisting a real `GLOBAL` assessment into the shared
  CI database (`data_health_assessments` is immutable/append-only and a `GLOBAL`
  block would otherwise permanently affect every other test's signal validation
  for the life of that database) — the empty-series and concurrency tests use a
  minimal stand-in `data_health_store`/`bar_store` for exactly that one assertion,
  while the "real data" test still round-trips through the genuine PostgreSQL bar
  store for its own uniquely-suffixed, `INSTRUMENT`-scoped series.
- `tests/test_postgres_runtime.py`, `tests/test_worker_app.py`: composition tests
  updated to assert `PostgresHistoricalBarStore`/`PostgresDataHealthStore` presence
  and the continued absence of any SQLite authority in protected runtimes.

## 7. What this module does not do

- No real external market-data, news, or fundamentals provider was activated.
- No broker integration, sandbox, or network-connected execution.
- No change to Module 3D identity/RBAC/secrets/audit controls or to deterministic
  risk/OMS controls.
- Live trading remains disabled.

Module 3G (real-provider activation) is a separate, explicitly-gated future module.
