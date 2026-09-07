# Module 3I.2: Crypto Funding, Mark/Index and Cross-Asset Open-Interest Authority

Status: **engineering authority only**. No crypto venue or data provider was
contacted, no real funding rate, mark price, index price or open-interest figure
was retrieved, no spend occurred, and nothing here grants any data, order, risk
or trading authority. This is roadmap **NEXT-03 phase 2**.

## 1. Scope

Added: a `CRYPTO` source scope, realized funding, venue/provider-published
indicative funding, mark price, index price, crypto open interest under the
existing single open-interest authority, the Data Health extensions those
families require, and their sealed-dataset / point-in-time / research-projection
integration.

Not added, by instruction: internally computed or ML-forecast funding rates,
term structure (3I.3), open-interest-driven futures roll activation,
top-of-book quotes (3I.4), trades and L2 order book (3I.5), and any
broker/live execution path.

## 2. No second pipeline, and no second open-interest system

Everything runs through the existing
`historical_market_data.PostgresHistoricalMarketDataPipeline`:

```
AuthorizedHistoricalSource → RawHistoricalObservation → NormalizedHistoricalObservation
    → HistoricalDatasetVersion (sealed) → research_query
```

`ObservationKind` gained four values; capture, normalization, sealing and
research reads were extended in place. Crypto open interest reuses the **same**
`OPEN_INTEREST` kind, the same payload dataclass, the same parser and the same
canonical serialization as Module 3I.1 futures open interest. There is no
`crypto_open_interest_observations` table and no crypto open-interest pipeline.

The 3H.2 crypto instrument authority is **read, never duplicated**: crypto
specifications and funding conventions have exactly one home, and this pipeline
consults `PostgresCryptoInstrumentAuthority` rather than restating anything.

## 3. The open-interest compatibility decision

3I.1 stored open interest in `futures_open_interest_observations`, while its
payload model already carried `CONTRACTS`, `BASE_ASSET` and `QUOTE_NOTIONAL` —
the semantics were always cross-asset and only the physical name was
futures-specific. Writing crypto rows into a table whose durable name claims
otherwise was not acceptable; neither was letting a storage rename change the
identity of historical evidence.

**Decision: rename the physical table, freeze the canonical payload identity.**

| Concern | Decision |
|---|---|
| Physical table | Renamed `futures_open_interest_observations` → `open_interest_observations` by `ALTER TABLE ... RENAME` in migration `20260907_0044`. Rows, primary key, foreign keys, CHECKs and the immutability trigger all carry over; nothing is copied, re-derived or rewritten. |
| Canonical payload identity | Frozen at the literal `futures_open_interest_observations` in `market_observation_payloads.OPEN_INTEREST_CANONICAL_PAYLOAD_IDENTITY`. It is a **serialization token, not a table name**. |
| Where the token appears | First element of `OpenInterestPayload.canonical_tuple()` (folded into sealed dataset content hashes) and the value of the `canonical_payload_table` marker stored in `normalized_value`. |
| Consequence | A dataset sealed under 3I.1 reconstructs to a bit-identical content hash under 3I.2. A futures and a crypto open-interest record serialize by one shared rule. |
| Discoverability | `COMMENT ON TABLE open_interest_observations` records the former name and why the token stays frozen. |

Two tests defend this and must never be "corrected":

- `test_futures_market_observations.py::test_open_interest_canonical_identity_survived_the_3i2_rename`
  asserts the exact frozen tuple.
- Both PostgreSQL suites seal an open-interest-only dataset and compare
  `seal_dataset()`'s digest against an **independent re-implementation** of the
  sealing formula with the token written out as a literal. Replacing the token
  with the current physical table name diverges immediately.

Every other typed kind's canonical identity equals its physical table name;
open interest is the one deliberate exception, and it is documented at the
constant, in the migration, and in the tests that assert the literal.

## 4. Observation kinds

| Kind | Typed table | Meaning |
|---|---|---|
| `FUNDING_RATE_REALIZED` | `crypto_funding_observations` | What a venue published as actually applied at a funding settlement. |
| `FUNDING_RATE_INDICATIVE` | `crypto_funding_observations` | A venue- or external-provider-published estimate of a funding rate that has not happened yet. |
| `MARK_PRICE` | `crypto_reference_price_observations` | The venue's mark price. |
| `INDEX_PRICE` | `crypto_reference_price_observations` | The venue's index price. |
| `OPEN_INTEREST` | `open_interest_observations` | Reused, unchanged, cross-asset. |

Required invariant, enforced by having five separate kinds with no fallback
path anywhere between them:

```
MARK_PRICE != INDEX_PRICE != OHLCV_CLOSE != LAST_TRADE != SETTLEMENT_PRICE
```

### Realized and indicative funding never substitute for each other

They are two kinds, not one kind with a status column. Four independent
mechanisms enforce it:

1. **Separate source capabilities.** A source authorized for
   `FUNDING_RATE_INDICATIVE` cannot capture a realized rate — refused by the
   database's composite `(source_id, observation_kind)` foreign key into
   `historical_source_capabilities`, not only by Python.
2. **The envelope kind is the sole authority.** `crypto_funding_observations`
   stores **no** `REALIZED`/`INDICATIVE` discriminator. A second stored
   authority could disagree with the envelope; there is none to disagree.
3. **A set-valued constraint trigger.** A funding payload may attach only to an
   envelope whose kind is one of the two funding kinds, and a reference-price
   payload only to a mark or index envelope — never across the pair.
4. **Mutually exclusive time semantics.** A realized record's funding instant
   *is* its event instant and its publication cannot precede it. An indicative
   record's event instant *is* its publication instant and its funding instant
   is strictly in the future. Neither shape satisfies the other's rules.

### Indicative funding is market data only when a market published it

Admissible: a venue- or external-provider-published estimate. Inadmissible and
structurally unreachable here: an internally calculated or ML-forecast future
funding rate, which belongs to Feature Authority / Model Registry / strategy
research. This module has no path that would let model output enter historical
market-data evidence.

Multiple successive estimates for the same future funding instant are all
preserved: each publication is its own observation keyed by its own event
instant, so a replay at time *T* sees exactly the estimates published on or
before *T* and never a later, better one.

## 5. Funding convention integration (two clocks)

Funding observations are validated against the **existing 3H.2**
`crypto_funding_conventions` authority, resolved point-in-time at:

- **effective** `target_funding_at` — the schedule in force at the funding
  instant being reported;
- **known** `ingested_at` — as this platform knew it when the record arrived.

A convention the venue announced later is invisible to an earlier record by
construction, so a historical replay keeps using the schedule that was actually
in force. A record whose funding instant lies on a *later* schedule version's
grid but not on the version effective at that instant is refused even when
ingested long after the change.

Validated at minimum: the instrument is a `PERPETUAL`; the funding settlement
asset matches both the convention and the instrument specification; the target
instant lies on the convention's schedule (interval and first-funding offset,
epoch-anchored); the rate respects the convention's declared floor/cap; and the
realized/indicative time semantics above hold. Cadence is checked only where it
is deterministically checkable — an interval or offset that is not a whole
number of seconds yields "unknown" rather than an invented tolerance. The
resolved `convention_id` and `convention_version` are stored on the row and
folded into the content hash, because a rate validated against one schedule is
different evidence from the same number validated against another.

## 6. Mark and index price

A shared typed table, because the financial shape is identical; distinct
envelope kinds, because the measurements are not. Eligibility comes from the
3H.2 `ReferencePriceRequirement`:

| Requirement | `MARK_PRICE` | `INDEX_PRICE` |
|---|---|---|
| `NONE` (every spot pair) | rejected | rejected |
| `INDEX_ONLY` | rejected | permitted |
| `MARK_AND_INDEX` | permitted | permitted |

Also required: price > 0 (Python and a database CHECK), the price asset is the
contract's own quote asset, the observation instant equals the envelope's event
instant, and the observation's venue matches the instrument's registered venue.

`methodology_reference` is **provenance metadata only**. It records what the
publisher said its methodology was; no code treats free text as evidence that a
methodology was followed, and it never widens what an instrument permits.

## 7. Crypto open-interest semantics

- `CONTRACTS` is valid for contract-count products; it may not name a unit asset.
- `BASE_ASSET` requires `unit_asset == crypto_spec.base_asset`.
- `QUOTE_NOTIONAL` requires `unit_asset == crypto_spec.quote_asset`.
- **No unit is ever converted into another.** Reaching a contract count from a
  base-asset quantity needs a mark, index or last price that an open-interest
  record does not carry; any cross-unit comparison is a derived research feature
  later, never an ingestion concern.
- An existing 3H.2 crypto instrument specification is required.
- Open interest is bound to `PERPETUAL` or `DATED_FUTURE`. Spot open interest
  fails closed: a spot pair has no open positions to count.

## 8. Source authorization and scope binding

`AssetScope` gains `CRYPTO`, eligible for
`OHLCV`, `OPEN_INTEREST`, both funding kinds, `MARK_PRICE` and `INDEX_PRICE`.
`SETTLEMENT_PRICE` is **not** crypto-eligible, and no reference-price kind is
futures-eligible; a capability outside its scope is refused at registration.

`OPEN_INTEREST` is deliberately eligible under both `FUTURES` and `CRYPTO` —
one authority, not two. Scope still binds, at the resolved instrument rather
than at the kind: `_require_source_scope_matches_instrument` refuses a
futures-authorized source describing a crypto perpetual, and vice versa. This
also replaced 3I.1's asset-class check for `OHLCV`, which had made the
`FUTURES` scope's own `OHLCV` capability unreachable.

## 9. Adjustment semantics

`RAW` / `AS_REPORTED` only, for all six typed kinds. Equity-oriented
`POINT_IN_TIME_ADJUSTED` / `LATEST_ADJUSTED` have no defined meaning for a
funding rate, a mark price, an index price, a settlement price or an
open-interest count, so a database CHECK refuses them rather than letting those
values acquire an undefined meaning.

## 10. Data Health

The existing authority is extended, not replaced.
`detect_crypto_series_health` adds
`MISSING_EXPECTED_FUNDING_EVENTS`, `DUPLICATE_FUNDING_SETTLEMENT`,
`FUNDING_RATE_OUTSIDE_CONVENTION_BOUNDS` and `NON_POSITIVE_REFERENCE_PRICE`,
and reuses `NEGATIVE_OPEN_INTEREST`, `OPEN_INTEREST_UNIT_INCONSISTENCY`,
`TIMESTAMP_REGRESSION` and `STALE_OBSERVATIONS`.

Three cadences are never invented:

- **Realized funding** is the first series here whose completeness is testable
  at all, and only because a versioned convention states the schedule as a
  recorded fact. Expected instants come solely from
  `crypto_market_observations.expected_funding_instants` applied to the
  applicable convention; passing `None` makes no completeness claim. Indicative
  records never satisfy realized completeness — an estimate is not evidence that
  a funding event settled.
- **Indicative funding** has no expected count. A venue may revise an estimate
  any number of times or not at all, so only explicitly sourced freshness rules
  apply.
- **Mark and index** have no assumed cadence either; absent explicitly sourced
  semantics, value-level problems are reported and gaps are not.

A restatement of a realized rate (a higher revision of the same funding
instant) is legitimate; two records at the **same** revision for one funding
instant is not, because the series then cannot say what was actually applied.

Assessment identity already carries `observation_kind` from 3I.1, so one
instrument holds five independently-tracked coexisting series — a healthy
estimate stream can never mask a broken settled one.

## 11. Migration `20260907_0044`

1. Four new observation kinds widen the CHECKs on
   `historical_raw_observations` and `historical_source_capabilities`;
   `CRYPTO` widens the source scope CHECK.
2. `futures_open_interest_observations` → `open_interest_observations`
   (`ALTER TABLE ... RENAME`, plus trigger renames and a table comment).
3. `crypto_funding_observations` and `crypto_reference_price_observations`,
   each with a primary-key foreign key to its envelope and an immutability
   trigger.
4. `require_matching_observation_kind` becomes set-valued
   (`actual = ANY(TG_ARGV)`) so one shared table can serve a kind pair without
   loosening anything.
5. `require_typed_payload_for_kind` covers the new kinds, so an envelope of any
   typed kind still cannot reach COMMIT without its canonical payload.

The migration is reversible: `downgrade()` restores the single-argument trigger
function, the pre-3I.2 table name and trigger names, and every prior CHECK.

## 12. Verification

Local evidence on fresh PostgreSQL: migration head `20260907_0044` applied and
downgraded/re-upgraded cleanly, the full suite green, all **156**
restore-critical tables reconciled after a `pg_dump`/`pg_restore` drill, the
**117/117** mypy ratchet unchanged, the zero-error mypy slice extended with
`market_observation_payloads.py` and `crypto_market_observations.py`, ruff and
bandit clean.

Exact merged-main CI evidence is recorded in
[MASTER_ROADMAP.md](MASTER_ROADMAP.md) once the module is on `main`.

**This verifies the engineering authority only.** Every funding rate, mark
price, index price, open-interest figure, venue name, funding schedule and
instrument identifier in this module's tests is fixture data. No crypto venue or
data provider was contacted, no network call is made, and nothing here grants
any data or trading authority.
