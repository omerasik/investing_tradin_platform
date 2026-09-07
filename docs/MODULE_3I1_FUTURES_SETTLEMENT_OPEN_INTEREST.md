# Module 3I.1: Futures Settlement and Open-Interest Authority

Status: **engineering authority only**. No exchange or provider was contacted,
no real settlement price or open-interest figure was retrieved, no Databento
spend occurred, and nothing here grants any data, order, risk or trading
authority. This is roadmap **NEXT-03 phase 1**.

## 1. Scope

Added: futures settlement-price and open-interest observations, the minimum
generic infrastructure they require, explicit per-kind revision/finality
semantics, Data Health and sealed-dataset compatibility, point-in-time
two-clock visibility, and multi-asset source authorization.

Not added, by instruction: crypto series (3I.2), term-structure derivation
(3I.3), top-of-book quotes (3I.4, planned but unauthorized), and trades / L2
order book (3I.5, deferred pending a storage-tier architecture decision).

## 2. No second pipeline

Everything runs through the existing
`historical_market_data.PostgresHistoricalMarketDataPipeline`:

```
AuthorizedHistoricalSource → RawHistoricalObservation → NormalizedHistoricalObservation
    → HistoricalDatasetVersion (sealed) → research_query
```

No second source registry, dataset registry, Data Health system or observation
envelope exists. `ObservationKind` gained two values; the envelope, capture,
normalization, sealing and research paths were extended in place.

## 3. Typed-payload authority decision

**The typed table is the single canonical authority for the normalized
financial value.** This was the explicit constraint on this module, and it is
implemented as follows.

| Concern | Decision |
|---|---|
| Where the value lives | `futures_settlement_observations` / `futures_open_interest_observations`, one row per normalized observation. |
| `normalized_value` JSON | Stores a **non-financial pointer marker** only — `{"canonical_payload_table": "..."}`. No second copy of any number. A unit test asserts the marker contains no financial key. |
| Backward-compatible reads | `research_query()` LEFT JOINs the typed tables and synthesizes the JSON projection at read time via `as_normalized_projection()`. Callers expecting a `normalized_value` mapping keep working, reading one authority projected. |
| Dataset sealing | `seal_dataset()` joins the typed columns and folds `canonical_tuple()` into the SHA-256 digest, alongside the observation kind. Changing any typed financial value or the finality state changes dataset identity. |
| One-to-one integrity | Typed row → envelope by primary-key foreign key. Envelope → typed row by a **DEFERRABLE INITIALLY DEFERRED constraint trigger** that fires at COMMIT. A typed payload may also only attach to an envelope of its own kind, enforced by a second constraint trigger. |

Raw provider payload JSON remains untouched immutable source evidence; this
restriction concerns the normalized authority only.

## 4. Source authorization

`AuthorizedHistoricalSource.validate()` previously hard-coded
`asset_scope == "US_EQUITIES_ETFS"`. It is now a fail-closed model on two axes:

- **`AssetScope`** enum — `US_EQUITIES_ETFS`, `FUTURES`. An unrecognised string
  raises `unsupported_asset_scope`; arbitrary strings are never accepted.
- **Capability binding to observation kinds.** `authorized_observation_kinds`
  states exactly which kinds a source may write. `SCOPE_ELIGIBLE_KINDS` rejects
  a capability outside its scope, so scope and capability cannot disagree.

A source authorized for OHLCV does **not** gain SETTLEMENT_PRICE or
OPEN_INTEREST authority by sharing an asset class. This is enforced by the
database, not only by Python: `historical_source_capabilities` is a child of
the existing source registry (not a parallel one), and
`historical_raw_observations` carries a composite foreign key on
`(source_id, observation_kind)` into it — so an unauthorized kind cannot be
captured even by a caller that bypasses the pipeline class.

Backward compatibility: `authorized_observation_kinds=None` retains the
pre-3I.1 behaviour **for `US_EQUITIES_ETFS` only**, resolving to the five
equity corporate-action kinds those sources already wrote. Every other scope
must state its kinds explicitly. The migration backfills capabilities from the
kinds each existing source has actually captured — preserving exactly the
authority already exercised, widening none.

No external provider fact or entitlement is inferred anywhere.

## 5. Settlement semantics

`SETTLEMENT_PRICE` is a distinct kind from `OHLCV`. Nothing lets a settlement
price stand in for a close, a last trade or a mark price. Modelled:
instrument via existing `ProfessionalInstrument` resolution, settlement price
(`> 0`, database CHECK), price currency, settlement date, settlement effective
instant, `PRELIMINARY`/`FINAL` finality, provider revision identity from the
existing envelope, the pipeline's event/effective/ingestion clocks, and
immutable provenance via the raw observation and its payload hash.

## 6. Open-interest semantics

Open interest is never an unqualified number. Every observation carries an
explicit `OpenInterestUnit` — `CONTRACTS`, `BASE_ASSET`, `QUOTE_NOTIONAL` — and
**no conversion between units is ever performed**. `BASE_ASSET` and
`QUOTE_NOTIONAL` are modelled so the dimension is honest from the start but are
reserved for 3I.2; a FUTURES source supplying one fails closed rather than
being silently converted (that conversion would need a contract multiplier and
a price, neither of which an open-interest record carries). Contract counts may
not name a unit asset, and asset-denominated units must — a database CHECK.
Values must be `>= 0`; zero is valid, since an expiring contract reaches it.

## 7. Adjustment semantics

Equity-oriented adjustment statuses are not blindly inherited.
`POINT_IN_TIME_ADJUSTED` and `LATEST_ADJUSTED` have no defined meaning for a
settlement price or an open-interest count, so both kinds are restricted to
`RAW` / `AS_REPORTED` — rejected in Python and again by a table CHECK — rather
than letting those values acquire undefined meaning.

## 8. Revision semantics

| Behaviour | Mechanism |
|---|---|
| Same provider record replayed | Idempotent: `ON CONFLICT DO NOTHING` plus a payload-hash comparison returns the existing id. |
| Later legitimate revision | A new row at the next revision; nothing is overwritten. |
| Older revision must not overwrite newer | Capture is INSERT-only; the read ranks `revision DESC, ingested_at DESC`. |
| PIT query at T returns only evidence available by T | `ingested_at <= knowledge_at`, `normalized_at <= knowledge_at`, `dataset.created_at <= knowledge_at`. |
| Latest query returns the newest valid revision | The same ranking with a later knowledge time. |
| Conflicting same-revision values | Fails closed with `raw_historical_observation_conflict`. |

## 9. Instrument resolution

Settlement and open interest resolve to a **real 3H.1 futures contract**. The
pipeline requires the resolved instrument to be `InstrumentType.FUTURE`, to
have no `continuous_parent_id`, and to have a row in
`futures_contract_specifications`. A synthetic continuous series can therefore
never carry an exchange-published observation — it remains a derived research
view, which is what keeps 3I.3 term structure and later roll logic reproducible
from real contracts.

**No OI-based roll policy is enabled.** 3H.1 still fails closed for
`VOLUME_OPEN_INTEREST_CROSSOVER`. This module provides the open-interest
authority that could eventually unlock it; wiring that up is deliberately left
to a separate, separately-reviewed change.

## 10. Data Health

The existing authority was extended, not duplicated. `DataHealthAssessment`
gained `observation_kind` and `source_id`, and the assessment uniqueness
constraint now includes `observation_kind` — exactly as migration 0039 added
`interval`. One futures contract can therefore carry independent, coexisting
health for its bars, its settlement series and its open-interest series instead
of them colliding. `active_blocks()` partitions by the new dimension too, so a
healthy bar series cannot mask a blocked settlement series.

`detect_futures_series_health()` adds five checks
(`MISSING_EXPECTED_SESSIONS`, `NON_POSITIVE_SETTLEMENT`,
`NEGATIVE_OPEN_INTEREST`, `OPEN_INTEREST_UNIT_INCONSISTENCY`,
`SETTLEMENT_FINALITY_REGRESSION`). **No universal cadence is invented**:
`expected_sessions` is never inferred, and passing `None` simply makes no
completeness claim. Every check is assigned to a declared detector family, and
a test asserts the families exhaust the enum so a new check cannot be added
without saying which detector raises it.

## 11. Sealed-dataset identity

The content hash covers source identity (via the dataset row), observation
kind, resolved instrument and clocks (via the member's raw evidence),
revision/finality and the canonical typed payload (via `canonical_tuple()`),
normalization version, and the raw payload SHA-256. Changing any financial
value or the finality state changes dataset identity — proven by sealing two
datasets that differ only in a settlement price and asserting different hashes.

## 12. Architectural issue discovered

**A sealed dataset is a snapshot of what was known when it was sealed, so a
single dataset version cannot exhibit a preliminary-then-final transition.**
`seal_dataset()` refuses a member ingested after `created_at`, and
`research_query()` hides a dataset created after the requested knowledge time.
There is therefore no knowledge time at which one dataset is visible but one of
its members' revisions is not.

This is correct behaviour, not a defect, but it means "historical replay must
still see the preliminary settlement" is expressed across **two dataset
versions** — the dataset that existed then, and the one sealed after the final
arrived. That is how a real point-in-time research workflow versions its data,
and the integration test models it that way. Worth stating explicitly because a
consumer might otherwise expect one dataset to replay its own revision history.

A second, smaller observation: the read projection reproduces the stored
`NUMERIC(38,18)` scale, so `str()` of a projected value carries trailing zeros
that the pre-persist parse does not. Comparisons should be made as `Decimal`.

## 13. Evidence

- `tests/test_futures_market_observations.py` — 24 pure unit tests: settlement
  and open-interest parsing and rejection paths, unit modelling without
  conversion, projection/canonical agreement, canonical-tuple sensitivity to
  every financial field, marker contains no financial value, and the futures
  Data Health detector including the "no cadence is invented" case.
- `tests/test_futures_market_observations_postgres.py` — one PostgreSQL
  integration test covering the remaining invariants, several proven **directly
  at database level bypassing Python**: capability foreign key, settlement `<= 0`,
  open interest `< 0`, unit/asset coherence, typed payload without envelope,
  typed payload on the wrong kind, envelope without typed payload (deferred
  constraint trigger), and UPDATE/DELETE against immutable evidence.
- Full local matrix on a fresh PostgreSQL: 841 tests, 154 restore-critical
  tables reconciled after `pg_restore`, 117/117 mypy ratchet, ruff and bandit
  clean.

## 14. What remains fixture-only

Every settlement price, open-interest figure, contract date and provider
identifier in this module is fixture data. No exchange or provider was
contacted; no Databento, SEC or crypto-venue call was made or authorized. This
module establishes the authority to hold such data honestly — it does not
establish that any real data has been ingested.

## 15. Authority boundary (unchanged)

No LLM or AI component can register a source, grant a capability, capture,
normalize, seal or health-assess anything. Nothing here produces a signal, an
`OrderIntent`, a risk decision or a promotion. `REAL_ACCOUNT_ENABLED` and
`AUTO_EXECUTION_ENABLED` remain false and are untouched.
