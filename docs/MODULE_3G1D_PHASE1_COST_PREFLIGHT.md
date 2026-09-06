# Module 3G.1d Phase 1: Real Databento Cost/Metadata Preflight

Status: **read-only preflight tooling only**. No historical data was
downloaded, no batch job was submitted, no dataset was sealed, and no money
was spent to produce this document. `databento_cost_preflight.py` adds a
client that can only reach the metadata/symbology endpoints Databento's own
documentation states are billed at **$0.00** — it has no method capable of
reaching `batch.submit_job` or `timeseries.get_range`. Companion to
[MODULE_3G0_PROVIDER_SELECTION_AND_LICENSING_PREFLIGHT.md](MODULE_3G0_PROVIDER_SELECTION_AND_LICENSING_PREFLIGHT.md)
and [MODULE_3G1C_PILOT_READINESS_AND_ACTIVATION_GATES.md](MODULE_3G1C_PILOT_READINESS_AND_ACTIVATION_GATES.md),
which this module extends rather than replaces.

## 1. Purpose

Determine, from Databento's own real API (not documentation, not assumption),
the exact viable configuration and real dollar cost of the candidate pilot
below — **before** any owner authorization of a real historical batch
retrieval (Module 3G.1d Phase 2, separately gated, not started by this
module).

## 2. Candidate pilot configuration (subject to real availability findings)

**Daily leg:** dataset `EQUS.SUMMARY`, schema `ohlcv-1d`, `2020-01-01` through
`2026-09-01`, universe: AAPL, MSFT, AMZN, GOOGL, NVDA, META, TSLA, JPM, KO,
XOM, SPY, QQQ, GLD, VTI, IVV (15 symbols).

**Minute leg:** dataset `EQUS.MINI`, schema `ohlcv-1m`, `2026-08-01` through
`2026-09-01`, universe: AAPL, MSFT, NVDA, SPY, QQQ (5 symbols).

TWTR is deliberately excluded from this pilot — reserved as a later
delisting/point-in-time symbology validation case, per owner instruction, once
this pilot's real symbology-resolution behavior for a delisted/renamed ticker
is understood from a live account.

## 3. What this module's code does and does not do

`src/trade_platform/databento_cost_preflight.py` exposes
`DatabentoCostPreflightClient`, which wraps exactly these real, confirmed
Databento HTTP endpoints (verified 2026-09-06 against Databento's current live
API reference, not the Python SDK and not this repository's own
`databento_provider.py`):

- `GET /v0/metadata.list_datasets`
- `GET /v0/metadata.list_schemas`
- `GET /v0/metadata.get_dataset_range`
- `GET /v0/metadata.get_cost` — returns a bare JSON number, "the cost in US
  dollars"
- `GET /v0/metadata.get_billable_size` — returns a bare JSON number, "the size
  in number of bytes used for billing"
- `POST /v0/symbology.resolve` — free symbology resolution; response's
  `result`/`partial`/`not_found` keys drive this module's
  `SymbolAvailability` classification (`AVAILABLE` /
  `AMBIGUOUS_REQUIRES_SYMBOLOGY_RESOLUTION` / `UNAVAILABLE`)

Databento's own Historical API documentation states: *"You will only be
billed for usage of time series data. Access to metadata, symbology, and
account management is free."* This module's client class defines no method
that can reach `batch.submit_job`, `timeseries.get_range`,
`timeseries.get_range_async`, `batch.list_jobs`, or `batch.list_files` — those
RPC families simply have no code path here. `run_pilot_cost_preflight()`
orchestrates only the calls above, once per configured pilot leg, and returns
a combined-cost report; it never downloads a record.

Reused without modification: `DatabentoHttpTransport`
(`databento_provider.py`)'s Basic Auth wire format — confirmed to match
Databento's documented `curl -u YOUR_API_KEY:` convention exactly — and
`EnvironmentSecretResolver` (`config.py`), so no new secret-handling path is
introduced. As with the disabled-by-default adapter, this client also refuses
to run without `ProviderConfiguration.terms_accepted=True` and a resolvable
`secret_reference`; this is a repository-level policy stricter than
Databento's own billing rules for metadata calls, kept for a single uniform
gate in front of every real network call this codebase can make to Databento.

## 4. What this module has not yet done

No real credential exists in this environment (`DATABENTO_API_KEY` is unset),
so this module's client has never made a real network call. Its parsing logic
is modeled from Databento's documented example requests/responses (curl
examples on the live HTTP API reference pages, fetched 2026-09-06), the same
disclosed-but-unverified posture already accepted for the batch-workflow
parsing in `databento_provider.py`. It must be run for real, once, before the
findings in the accompanying preflight report are treated as ground truth
rather than a documented contract.

## 5. Hard boundaries (unchanged)

- No historical data was downloaded to produce this document.
- No batch job was ever submitted.
- No `HistoricalDatasetVersion` was created.
- No money was spent; every endpoint this module's code can reach is
  Databento-documented as free.
- Live trading remains disabled; no broker or prop-firm integration exists.
- No credential is stored, logged, or committed anywhere in this repository —
  referenced only as `env:DATABENTO_API_KEY` via the existing
  `EnvironmentSecretResolver` pattern.
