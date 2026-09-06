# Module 3G.1c: Pilot Readiness and Activation Gates

Status: **pre-activation readiness layer only**. No real Databento request is made
by this module, no credential is provisioned, no provider is activated, and no
corporate-action product is purchased. This module makes the existing
Databento → `provider_ingestion` → raw capture → `normalize()` →
`historical_bar_bridge` → `ingest_from_provider()` → `PostgresHistoricalBarStore` →
Data Health → sealed `HistoricalDatasetVersion` path (Modules 3G.1a/3G.1b/3G.1b.1)
operationally ready for a bounded, owner-authorized **Module 3G.1d** real-data
pilot, without introducing a second authority, registry, or hidden activation path.

## Module numbering (for the record)

| Module | What it is | Status |
|---|---|---|
| 3G.0 | Provider selection and licensing preflight | Done — Databento recommended, owner-approved |
| 3G.1a | Disabled-by-default Databento historical adapter | Done — merged, exact-main verified |
| 3G.1b | OHLCV bridge into `PostgresHistoricalBarStore` | Done — merged, exact-main verified |
| 3G.1b.1 | Multi-interval Data Health correctness fix | Done — merged, exact-main verified |
| 3G.1c | Pilot readiness / activation gates (this module) | In progress |
| 3G.1d | Bounded **real** Databento pilot | **NOT YET AUTHORIZED** — requires a separate, explicit owner YES |
| 3G.2 | Corporate actions / adjustment factors | Not started — separately approved later, after a successful 3G.1d |

Nothing in 3G.1c marks real provider ingestion `VERIFIED`. The only thing this
module verifies is that a fully-specified, fully-approved configuration would
reach the point immediately before the first network request.

## 1. Authorized source registration

No second registry. A Databento pilot source is registered through the existing
`AuthorizedHistoricalSource` (`historical_market_data.py`) and
`PostgresHistoricalMarketDataPipeline.register_source()` (unchanged), exactly as
every other historical source in this codebase is registered. The exact recipe:

```python
from datetime import UTC, datetime
from trade_platform.historical_market_data import AuthorizedHistoricalSource

source = AuthorizedHistoricalSource(
    provider="databento",
    dataset_name="databento-us-equities-pilot",       # descriptive label for this rights grant
    provider_identifier_namespace="DATABENTO:INSTRUMENT_ID",
    provider_terms_version="<exact accepted terms/plan version string>",
    authorization_reference="<operator's own reference to the signed agreement/order form>",
    authorized_at=<datetime the rights were actually granted, tz-aware>,
    created_at=<datetime this record is created, tz-aware, >= authorized_at>,
    # asset_scope defaults to "US_EQUITIES_ETFS" -- the only value this module's
    # readiness gate accepts, matching the approved first vertical slice.
)
pipeline.register_source(source)
```

One source covers both pilot legs (daily via `EQUS.SUMMARY`/`ohlcv-1d` and minute
via `EQUS.MINI`/`ohlcv-1m`) because both datasets are used under the same
Databento account/contract — `dataset`/`schema` are properties of each
*pilot configuration* (`DatabentoPilotConfiguration`), not of the source record.
`register_source()` already rejects a duplicate `(provider, dataset_name,
provider_terms_version)` identity (`HistoricalDataAuthorizationError`) rather than
silently re-registering — verified in
`tests/test_databento_pilot_readiness_postgres.py`.

## 2. Disabled-by-default activation

Real Databento access remains unreachable by default. `assess_pilot_readiness()`
(`databento_pilot_readiness.py`) requires **every** one of the following
simultaneously, failing closed on the first missing one, strictly before any
network-capable object beyond the already-disabled adapter is even constructed
with real transport:

1. Operator activation checklist fully attested (`ActivationAttestation.is_complete()` — see §7).
2. `source.provider == "databento"` and `source.asset_scope == "US_EQUITIES_ETFS"`, and `source.validate()` passes.
3. `provider_configuration.provider == "databento"` and `provider_configuration.terms_accepted is True`.
4. Symbol universe non-empty, no duplicates, and within both this module's pilot
   ceiling (30) and Databento's documented per-request cap (2,000).
5. `schema` is one of the two approved schemas, and `dataset` matches the schema
   it belongs with (`EQUS.SUMMARY` ↔ `ohlcv-1d`, `EQUS.MINI` ↔ `ohlcv-1m`) — a
   mismatched pairing is rejected, not silently corrected.
6. Date range valid and bounded (≤ 20 years for daily, ≤ 90 days for minute).
7. A cost estimate has actually been obtained (`estimated_cost_usd is not None`)
   and is within the owner-approved ceiling.
8. `execution_approved is True` — explicit, per-configuration approval.
9. The **real** `DatabentoHistoricalAdapter`'s own `preflight()` succeeds — this
   re-uses the actual adapter code (not a parallel reimplementation), and is the
   step that would fail if `terms_accepted` were false or the secret reference
   were unresolvable, exactly as a real `fetch_raw_page()` call would fail at
   exactly the same point.

There is no startup ingestion, no background job, and no "if the key exists,
start downloading" path anywhere in this codebase. The only way any of this code
executes is a caller explicitly constructing a `DatabentoPilotConfiguration` and
calling `assess_pilot_readiness()` (research/CI/dry-run use), or, in the
separately-gated Module 3G.1d, explicitly invoking a real ingestion run after that
assessment has already passed.

## 3. Secret handling

Unchanged from Module 3G.1a: the actual Databento API key is never accepted,
stored, or referenced in this repository, CI configuration, fixtures, tests, or
documentation — only a secret **reference name** (`env:DATABENTO_API_KEY` or
similar), resolved at runtime by the existing `EnvironmentSecretResolver`
(`config.py`). This module adds no second secrets mechanism. Tests use
allowlisted fake reference names and fake values only (`# pragma: allowlist
secret`, matching the convention already used throughout this codebase's tests).

## 4. Proposed pilot universe (owner review required — no Databento availability claimed)

Every instrument below is labeled `CANDIDATE — availability not yet verified
against Databento`. No real Databento lookup was performed to produce this list;
selection is based on public, well-documented real-world facts about each
company (splits, renames, dividends, delisting), not on any provider-specific
claim. Availability, exact identifiers, and history depth in Databento's actual
data are verified only at the gated Module 3G.1d pilot.

| # | Symbol | Venue (this repo's convention) | Type | Why included |
|---|---|---|---|---|
| 1 | AAPL | XNAS | Equity | Large-cap NASDAQ; already modeled in `professional_instruments.mvp_instrument_universe()` |
| 2 | MSFT | XNAS | Equity | Large-cap NASDAQ |
| 3 | AMZN | XNAS | Equity | Large-cap NASDAQ |
| 4 | GOOGL | XNAS | Equity | Large-cap NASDAQ |
| 5 | NVDA | XNAS | Equity | Large-cap NASDAQ; **split case** — real 4:1 split (Jul 2021) and 10:1 split (Jun 2024) |
| 6 | META | XNAS | Equity | Large-cap NASDAQ; **ticker/name-change case** — real symbol change from `FB` to `META` (Jun 2022) following the Facebook Inc. → Meta Platforms Inc. rebrand |
| 7 | TSLA | XNAS | Equity | Large-cap NASDAQ; alternate split history (5:1 2020, 3:1 2022) |
| 8 | JPM | XNYS | Equity | Large-cap NYSE |
| 9 | JNJ | XNYS | Equity | Large-cap NYSE |
| 10 | KO | XNYS | Equity | Large-cap NYSE; **dividend case** — real, long-documented consecutive-increase dividend history |
| 11 | XOM | XNYS | Equity | Large-cap NYSE |
| 12 | WMT | XNYS | Equity | Large-cap NYSE |
| 13 | TWTR | XNYS (former) | Equity | **Delisted-security candidate** — real going-private acquisition and delisting (Oct 2022); exercises delisting/identity-continuity handling |
| 14 | SPY | ARCX | ETF | Broad-market ETF, NYSE Arca; already modeled in `professional_instruments.mvp_instrument_universe()` |
| 15 | QQQ | XNAS | ETF | Large NASDAQ-listed ETF |
| 16 | GLD | ARCX | ETF | NYSE Arca; already modeled in `professional_instruments.mvp_instrument_universe()` |
| 17 | VTI | ARCX | ETF | NYSE Arca broad-market ETF |
| 18 | IVV | ARCX | ETF | NYSE Arca S&P 500 ETF |
| 19 | DIA | ARCX | ETF | NYSE Arca Dow-tracking ETF |
| 20 | XLF | ARCX | ETF | NYSE Arca sector ETF |

20 instruments — within the owner-approved 15–30 bound. Covers: 12 large-cap
equities across NASDAQ and NYSE, 6 ETFs (5 NYSE Arca, 1 NASDAQ), one real split
case, one real ticker/name-change case, one real dividend case, and one real
delisted-security candidate. **The owner selects the final universe** (this list,
a subset, or a replacement) before any 3G.1d activity.

## 5. Pilot scope configuration

Two independent `DatabentoPilotConfiguration` "legs" sharing the same
`AuthorizedHistoricalSource`:

| | Daily (primary) | Minute (secondary) |
|---|---|---|
| Dataset | `EQUS.SUMMARY` | `EQUS.MINI` |
| Schema | `ohlcv-1d` | `ohlcv-1m` |
| Universe | the approved subset of §4 | same or smaller subset |
| Date range | operator-selected, bounded to ≤ 20 years by this module's readiness gate (`MAXIMUM_WINDOW_FOR_SCHEMA`) | operator-selected, bounded to ≤ 90 days — **no claim of long-history intraday capability**, consistent with `EQUS.MINI`'s documented 2023-03-28 history start (Module 3G.1 proposal §7) |
| Chunk size | operator-selected (e.g. 31 days), drives `plan_chunks()` | operator-selected, typically smaller |

Both legs are validated independently by `assess_pilot_readiness()`; both must
pass before either is eligible for 3G.1d execution.

## 6. Pre-flight cost gate

`assess_pilot_readiness()` requires `estimated_cost_usd` to already be populated
and within `cost_ceiling_usd` — it does **not** compute or fetch a cost estimate
itself, and no code in this repository calls Databento's real cost-estimation
endpoint (documented in Databento's own client as `metadata.get_cost()`). Module
3G.1d's required sequence, not implemented here:

```
Databento metadata.get_cost() (real call, 3G.1d only, read-only pricing lookup)
  -> compare returned estimate against the owner-approved ceiling / available signup credit
  -> explicit owner approval of that specific number
  -> only then does DatabentoPilotConfiguration.estimated_cost_usd get set to
     the real value, execution_approved get set to True, and the actual
     historical batch request get submitted
```

No automatic paid fallback exists anywhere in this codebase. Current budget
policy (unchanged from the Module 3G.1 OWNER DECISION): target $0 out-of-pocket,
stay within the available Databento signup credit, and any projected charge
beyond that credit requires new, separate, explicit owner approval before it is
incurred.

## 7. Activation checklist (A–O)

Represented in code as `ActivationChecklistCode` (`databento_pilot_readiness.py`)
and enforced via `ActivationAttestation.is_complete()` — every one of the 15
items below must be individually, explicitly attested by the operator (never
defaulted to true, never inferred from another item) before
`assess_pilot_readiness()` will proceed past its first gate:

| Code | Item |
|---|---|
| A. `ACCOUNT_EXISTS` | Databento account exists |
| B. `LICENSE_REVIEWED` | Applicable contract/license reviewed |
| C. `RETENTION_RIGHTS_CONFIRMED` | Indefinite/local-retention rights confirmed for our immutable raw archive |
| D. `NON_DISPLAY_RIGHTS_CONFIRMED` | Internal non-display/algorithmic research rights confirmed |
| E. `API_KEY_CREATED` | API key created by owner/operator |
| F. `SECRET_STORED_EXTERNALLY` | Secret stored outside the repository |
| G. `SOURCE_REGISTRATION_REVIEWED` | Source registration (§1) reviewed |
| H. `PILOT_UNIVERSE_REVIEWED` | Pilot universe (§4) reviewed |
| I. `DATE_RANGES_RESOLUTIONS_REVIEWED` | Date ranges/resolutions (§5) reviewed |
| J. `COST_ESTIMATE_OBTAINED` | Cost estimate obtained (§6) |
| K. `COST_WITHIN_CEILING` | Cost is within the approved ceiling |
| L. `RAW_UNADJUSTED_CONFIRMED` | Data remains RAW/unadjusted |
| M. `CORPORATE_ACTION_LIMITATIONS_ACKNOWLEDGED` | Corporate-action limitations acknowledged (§12) |
| N. `PER_RUN_AUTHORIZATION_RECEIVED` | Explicit per-run owner authorization received |
| O. `RESTORE_RECOVERY_PLAN_CONFIRMED` | Restore/recovery plan confirmed before ingestion |

If any item is not attested, `assess_pilot_readiness()` raises
`PilotReadinessError("activation_checklist_incomplete:<missing codes>")` before
evaluating anything else — pilot execution is blocked, not warned-and-continued.

## 8. Acceptance / dry-run support

`assess_pilot_readiness()` **is** the dry run: configuration validation → source
authorization → provider terms/secret-reference validation → pilot-scope
validation (universe/dataset/schema/date-range/cost/approval) → real adapter
construction → real adapter `preflight()` (secret *resolution*, never network) →
ingestion plan (`plan_chunks()`) → return. `tests/test_databento_pilot_readiness.py`
proves the fully-valid case reaches this point using a
`NetworkCallForbiddenTransport` double that raises immediately if either HTTP
method is ever invoked — proving the boundary by construction, not by an
after-the-fact call-count assertion — and proves each of the following causes a
fail-closed rejection strictly before that boundary: missing checklist item,
wrong source provider/asset-scope, missing terms acceptance, missing/unresolved
secret reference, empty/oversized/duplicate universe, unsupported schema,
mismatched dataset/schema pairing, invalid/unbounded date range, missing/
excessive cost estimate, and missing execution approval.

## 9. Idempotency / resume plan (documented, not newly implemented)

This is the exact restart semantics already implemented by
`provider_ingestion.ingest_raw_historical_pages()` and
`PostgresHistoricalIngestionCheckpointStore` (unchanged since Module 3G.1a),
made explicit for the operator:

- **Checkpointed completed chunks**: every chunk `fetch_raw_page()` returns for
  is durably recorded via `ProviderIngestionCheckpoint.resume_cursor` in
  `historical_ingestion_checkpoints` immediately after that call returns. A
  resumed run reads `PostgresHistoricalIngestionCheckpointStore.latest(...)` and
  starts from that cursor.
- **Not-yet-started chunks**: everything from the last checkpointed cursor to the
  configured `end` date — untouched, nothing to reconcile.
- **Submitted-but-not-persisted provider jobs (the real gap)**: if the process
  crashes *during* one `fetch_raw_page()` call — after `_submit_job()` has
  already succeeded on Databento's side, but before that call returns and the
  checkpoint is written — the checkpoint still shows the *previous* chunk's
  cursor. A resumed run will call `fetch_raw_page()` for that same chunk again
  and **submit a brand-new Databento batch job for it**, not resume the
  in-flight one. This is disclosed in `databento_provider.py`'s module
  docstring (Module 3G.1a) and is not hidden here.
- **Possible bounded resubmission cost**: bounded by one chunk's size (the
  operator's configured `chunk_size`, e.g. ~31 days of daily bars) — small
  relative to the whole pilot, and within the $0-out-of-pocket/credit-bounded
  budget policy for the approved pilot scale.
- **Operator detection/reconciliation**: before resuming after any crash, the
  operator should cross-check `historical_ingestion_checkpoints` (last
  successful `resume_cursor` per source) against Databento's own
  `batch.list_jobs` account dashboard/API for a `done` job already covering the
  about-to-be-resubmitted chunk's date range — this is a **manual** operator
  step in Module 3G.1d, not automated here. No durable job-resume (reusing the
  original Databento job id across a crash) is implemented or claimed by this
  module; implementing it would require checkpointing the job id itself
  mid-call, which is explicitly out of scope for this pilot-readiness layer.

## 10. Data Health / multi-interval readiness

Module 3G.1b.1 fixed the collision that previously prevented the same
instrument from having independently-evaluated daily and minute Data Health
assessments in one tick. This module's pilot shape (§5: one daily leg, one
minute leg, same universe) is exactly the shape
`tests/test_historical_bar_bridge_postgres.py`'s
`test_daily_and_minute_ohlcv_flow_from_raw_capture_through_the_bar_store_to_data_health`
test already proves works end to end through the real, unmodified
`scheduler.run_data_health_evaluation()` — no provider-specific Data Health path
exists or is added here.

## 11. Sealed-dataset readiness — evidence required before any real seal

Before a real Databento-sourced batch may be sealed as a `HistoricalDatasetVersion`
(`historical_market_data.PostgresHistoricalMarketDataPipeline.seal_dataset()`,
unchanged), the following must all hold — enforced by that method's existing
checks, restated here as the pilot's acceptance bar:

- Every raw record for the batch is captured immutably in
  `historical_raw_observations` (`capture_raw()`).
- Source/provenance is valid: `source_id`, `provenance_uri`, and
  `raw_payload_sha256` are present and attributable to the real Databento job
  that produced them.
- Canonical instrument resolution is complete for every record — no ad hoc or
  partial instrument mapping.
- Malformed/rejected rows are accounted for as `REJECTED` normalized
  observations with recorded `quality_issues` — never silently dropped or
  repaired (`seal_dataset()` already refuses any normalized member whose
  `quality_status` is not `VALIDATED`: `HistoricalDataQualityError`).
- A Data Health assessment has been recorded for the resulting bars (§10) —
  a blocking assessment must not be worked around to force a seal.
- `content_hash`/dataset version evidence is complete (`seal_dataset()`
  computes this from the exact member set; it cannot be sealed without it).
- PIT/knowledge timestamps are preserved: `seal_dataset()` already rejects a
  dataset whose `created_at` precedes any member's `normalized_at`/`ingested_at`.
- `AdjustmentStatus.RAW` is explicit on every OHLCV record from this pilot (see
  §12) — no adjusted-price claim is embedded in the sealed dataset.

A failed or incomplete pilot batch has no bypass to a "successful real dataset"
state — every one of the above is a hard `raise`, not a warning, in the existing
`historical_market_data.py` code this pilot reuses unchanged.

## 12. No corporate-action claims

Restated from the Module 3G.1 OWNER DECISION and unchanged by this module: 3G.1c
and the eventual 3G.1d pilot remain RAW-price work. Until a separately-approved
Module 3G.2 exists:

- No split-adjusted return claims.
- No dividend-adjusted return claims.
- No total-return strategy claims.
- No professional promotion of results that require corporate-action correctness.

This is structurally true today, not merely a policy statement: the adapter
(`databento_provider.py`) hardcodes `AdjustmentStatus.RAW` on every record it
produces, and no code path in this repository purchases or ingests Databento's
separate corporate-actions product.

## 13. Tests and verification

- `tests/test_databento_pilot_readiness.py` (20 tests, pure/fast): every gate in
  §2 individually, the happy path with a network-call-forbidding transport
  double, daily+minute legs both independently ready, deterministic
  configuration identity, and chunk-plan correctness.
- `tests/test_databento_pilot_readiness_postgres.py` (2 tests, real PostgreSQL):
  pilot source registration persists and rejects a duplicate identity; a
  registered source feeds a configuration that passes full readiness assessment.
- `tests/test_databento_provider.py` (unchanged 12 tests, still green): proves
  the refactor extracting `preflight()`/`plan_chunks()` out of `fetch_raw_page()`
  changed no observable adapter behavior.
- Verified locally: full unittest suite green both without and with a real,
  freshly-migrated PostgreSQL 16 container; ruff clean; mypy baseline ratchet
  unchanged; bandit clean; detect-secrets clean. No schema/migration change in
  this module, so no new restore-drill coverage was required (verified the
  existing drill is unaffected).

## Hard boundaries (unchanged)

No real Databento API call. No Databento account created on the owner's behalf.
No real API key provisioned. No real historical cost lookup performed. No paid
data. No provider activation. No corporate-actions purchase. No broker
integration. No prop-firm integration. No live trading. No order submission. No
weakening of Risk, identity, RBAC, CSRF, secrets, audit, Data Health, branch
protection, or CI.

Module 3G.1d does not begin as a result of this module merging. It requires a
separate, explicit owner YES against the exact activation packet delivered
alongside this module.
