# Module 3I.3: Deterministic Futures Term-Structure Derivation

Status: **engineering authority only**. No exchange or data provider was
contacted, no real settlement price or contract specification is claimed, no
spend occurred, and nothing here grants any data, order, risk or trading
authority. This is roadmap **NEXT-03 phase 3**.

## 1. Scope

**FUTURES ONLY.** This module derives an ordered term-structure curve from
already-sealed `SETTLEMENT_PRICE` evidence for a single futures series. It
deliberately does **not** cover crypto dated futures: crypto instruments
currently have `MARK_PRICE` and `INDEX_PRICE` (Module 3I.2) but no first-class
exchange settlement-price authority, so a curve built from mark/index would be
a different semantic artifact than an exchange settlement curve and must never
be silently treated as equivalent to one. Crypto term structure remains a
later, separately reviewed module.

Also not added, by instruction: options volatility surfaces, open-interest-driven
roll activation, continuous price adjustment, quote/trade/L2 ingestion, live
provider calls, broker connectivity, signal generation, portfolio allocation or
trading execution.

## 2. A term structure is derived evidence, not raw market data

The dependency chain is:

```
AuthorizedHistoricalSource -> RawHistoricalObservation -> SETTLEMENT_PRICE
    -> sealed HistoricalDatasetVersion -> Term Structure Derivation
    -> (later) Feature Authority / Strategy Lab
```

3I.3 introduces no new `ObservationKind`, writes nothing into
`historical_raw_observations`, creates no second market-data source registry,
and never mutates or reinterprets the canonical settlement observations 3I.1
already sealed. It consumes exactly one sealed `HistoricalDatasetVersion` and
produces immutable, reproducible derived artifacts in a **separate
derived-artifact authority** (migration `20260908_0045`,
`futures_term_structure.py`).

## 3. Three tables

### `futures_term_structure_methods`

A versioned, content-hashed method definition. Every field that governs
derivation behaviour — settlement finality policy, same-session/staleness
policy, classification threshold, carry day-count convention — is folded into
`content_hash`, so a changed method cannot masquerade as the same version
(`PostgresFuturesTermStructureAuthority.get_method` recomputes and compares the
hash on every read, exactly like 3H.1's `ContinuousSeriesPolicy`). Three CHECK
constraints make several invariants **schema-impossible**, not merely
application-refused:

| Constraint | What it forbids |
|---|---|
| `term_structure_staleness_only_under_tolerance` | A staleness tolerance, staleness flag or stale-classification permission declared under the strict same-session policy, or a tolerance policy with no positive staleness bound. |
| `term_structure_classification_requires_declaration` | `CONTANGO`/`BACKWARDATION`/`FLAT` classification enabled without both an explicit minimum point count and a positive threshold — or either declared without classification enabled. |
| `term_structure_carry_requires_day_count` | Carry/annualization declared supported without an explicit day-count convention, or a day-count convention declared without carry support. |

Each CHECK explicitly tests `IS [NOT] NULL` on every nullable column it
touches, because Postgres only rejects a definite `FALSE` — a bare `x > 0`
comparison against a `NULL` column evaluates to `NULL`, which a CHECK silently
treats as satisfied. An early draft of this migration had exactly that gap;
the Postgres integration test that tries to insert `classification_enabled =
true` with a `NULL` threshold directly (bypassing the dataclass) caught it.

### `futures_term_structure_curves`

One immutable derived curve instance, keyed by its natural PIT identity:
`(series_id, dataset_version_id, method_id, as_of, knowledge_at)`. Re-deriving
the same key is idempotent — the second call returns the existing row rather
than inserting a duplicate — and a hash mismatch on the same key is a hard
conflict, never a silent overwrite, because these rows are INSERT-only
(immutable trigger, like every other evidence table in this codebase).

### `futures_term_structure_points`

One row per real 3H.1 contract that participated. `instrument_id` is a foreign
key into `futures_contract_specifications`, which makes a continuous synthetic
series, an equity, a crypto instrument, or any instrument never specified as a
futures contract **mechanically impossible** as a point. A deferred constraint
trigger (`require_term_structure_point_matches_canonical`) additionally proves,
at COMMIT, that:

1. the referenced `normalized_observation_id` actually has a canonical
   `futures_settlement_observations` row (rejects a non-settlement, e.g. OHLCV,
   observation used as a point);
2. the point's frozen `settlement_price` and `settlement_finality` snapshot
   equal that canonical row's values exactly (rejects any divergence);
3. the point's contract belongs to the same `series_id` as its own curve
   (rejects a contract from the wrong futures series, even one with its own
   genuine settlement evidence).

### The settlement-price snapshot decision

The point's `settlement_price` is a **copied snapshot** of the canonical
`futures_settlement_observations` value — kept for the derived artifact's own
immutability and read efficiency — never a second competing financial
authority. The trigger above is what keeps that snapshot from ever diverging
from the canonical payload it was taken from, and the curve's `content_hash`
covers the actual settlement prices, finalities, revisions and contract
identities of every point, not merely their observation IDs — so a settlement
price change (a different revision) or a method version/config change both
change the curve's identity.

## 4. Point-in-time semantics

A derivation's inputs are exactly one sealed dataset version, one method
version, one futures series, one `as_of` and one `knowledge_at`. A settlement
observation participates only if:

- it belongs to the sealed dataset (`historical_dataset_members`);
- its contract belongs to the requested futures series (3H.1 identity);
- its own two clocks (`event_at`, `ingested_at`) and its normalization clock
  were knowable at or before `knowledge_at`;
- the dataset itself was sealed at or before `knowledge_at`
  (`historical_dataset_versions.created_at <= knowledge_at`, checked
  explicitly and fail-closed rather than silently returning no rows).

Nothing here ever queries "latest known today" while deriving a historical
curve. Replaying an earlier knowledge time means deriving against the sealed
dataset that existed then — exactly the pattern the 3I.1/3I.2 test suites
already establish for `research_query`. Because curves are immutable and keyed
by their own `knowledge_at`, a later final settlement cannot mutate an earlier
preliminary curve in place; deriving again at the later knowledge time produces
a **new**, separately identified curve row.

## 5. Point selection: no fallback, no interpolation

The only observation kind this module ever reads is `SETTLEMENT_PRICE` — never
OHLCV close, last trade, mark, index or a stale prior value. A contract with no
eligible settlement for the requested snapshot is simply absent from the
curve; there is no synthetic point, no interpolation and no extrapolation
anywhere in the base method. Ordering follows the 3H.1 contract's own
`expiration_date`, never symbol/ticker text.

**Finality policy** (`FINAL_ONLY` or `LATEST_KNOWN_ALLOW_PRELIMINARY`) decides
whether a contract whose latest-known-at-`knowledge_at` revision is
`PRELIMINARY` is excluded or included; every included point retains its own
finality, and `contains_preliminary_point` is set on the curve whenever any
point does.

**Session policy** (`SAME_SESSION_STRICT` or
`SAME_SESSION_WITH_STALENESS_TOLERANCE`) decides whether a contract's
settlement must fall exactly on `as_of` or may fall within an explicit,
method-declared `max_staleness_days` window; a stale point is flagged
(`is_stale`) and, unless the method explicitly says otherwise, excluded from
classification.

If fewer than the method's declared `minimum_point_count` survive selection,
derivation fails closed rather than emitting a partial or under-evidenced
curve.

## 6. Classification

`CONTANGO`/`BACKWARDATION`/`FLAT` are deterministic method outputs, never
implicit: `classify_curve` (a pure function, unit-tested independently of any
database) compares the ordered front and back settlement prices against the
method's own declared relative threshold, and is only invoked when the method
declares classification enabled, a minimum point count is met, and (if any
point is stale) the method explicitly permits classifying a curve that
contains one.

## 7. Carry / roll yield

Deliberately **not computed** in this module. The method registry only records
a day-count convention for forward compatibility with a later, separately
reviewed derivation — carry, roll-yield-proxy and annualized-slope metrics
belong on the curve artifact or in Feature Authority once a real formula, exact
maturity dates and an explicit day-count convention are reviewed together, not
folded quietly into the point/curve structure here.

## 8. Relationship to other authorities

- **3H.1 futures contracts** (`futures_contracts.py`): read-only. Every point
  resolves to a real, already-specified contract; no roll policy or
  open-interest-driven roll is enabled as a side effect of this module.
- **3I.1/3I.2 market observations**: read-only. No settlement observation is
  mutated, reinterpreted, or given a second normalized authority.
- **Feature Authority**: not extended here. Front/back spread, normalized
  slope, annualized carry, curvature and roll-yield-proxy features belong in a
  later module that consumes this curve artifact, not in a second feature
  system grafted onto it.

## 9. Negative-test coverage

`tests/test_futures_term_structure_postgres.py` proves, against real
PostgreSQL, every invariant above: a non-settlement observation, a continuous
series, an equity/crypto instrument, a wrong-series contract, a duplicate
contract point, and a price/finality mismatch are all rejected by the deferred
trigger or a foreign key; mixed currency, mixed quote unit, mixed session, and
stale-beyond-tolerance evidence are all excluded rather than silently blended;
a future-known settlement cannot leak into an earlier `knowledge_at`, and a
dataset not yet sealed at `knowledge_at` is rejected outright; identical inputs
replay idempotently while a method or price change provably changes the curve
hash; every table rejects `UPDATE`/`DELETE`; and an undeclared classification
threshold or carry day-count convention is rejected by the schema itself, not
only by the dataclass. `tests/test_futures_term_structure.py` covers the
dataclass validation and `classify_curve` logic without a database.
