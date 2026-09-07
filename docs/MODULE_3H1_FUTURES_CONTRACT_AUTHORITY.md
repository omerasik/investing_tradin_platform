# Module 3H.1: Futures Contract, Margin and Continuous-Series Authority

Status: **engineering authority only**. No exchange was contacted, no contract
reference data was retrieved, no market data was ingested, and nothing here
grants any data, order, risk or trading authority. This is roadmap **NEXT-02
phase 1** (Multi-Asset Instrument Authority V2 — futures semantics). Crypto
instrument types (SPOT / PERPETUAL / DATED_FUTURE, funding and mark/index
semantics) are deliberately **not** in this module; they are 3H.2.

## 1. Why this module exists

The existing `ProfessionalInstrument` authority (migration `20260815_0008`)
already carried five futures-shaped fields — `contract_code`,
`expiration_date`, `first_notice_date`, `last_trade_date`,
`continuous_parent_id`, `roll_rule` — and required four of them for any
`InstrumentType.FUTURE`. That is enough to record *that* an instrument is a
futures contract. It is not enough to trade, size, or backtest one:

- **No tick value.** A futures P&L is `ticks × tick_value`, and tick value is
  not recoverable from tick size alone. GC and MGC both quote gold in USD per
  troy ounce at a `0.10` tick; they differ only in multiplier (100 vs 10) and
  therefore in what a tick is worth ($10.00 vs $1.00).
- **No root/series identity.** `contract_code` describes one delivery month.
  Nothing tied the twelve listed months of a product together, so "the GC
  curve" had no canonical representation.
- **No settlement semantics.** Physically delivered and cash-settled products
  have materially different lifecycle risk, and only the former can issue a
  delivery notice.
- **No margin.** Position sizing on a futures account is margin-bound, and
  exchange margin is revised over time.
- **`roll_rule` was an unversioned free-text string.** Two consumers reading
  `"quarterly"` could legitimately roll on different days, producing two
  different backtests of the same strategy with no way to tell which was run.

## 2. Architecture decision

**Extend the existing instrument master with sibling tables; do not add
columns to `professional_instruments`, and do not create a second instrument
authority.**

`PostgresProfessionalInstrumentMaster` writes that table with a positional
`INSERT INTO professional_instruments VALUES (<31 placeholders>)` and reads it
back by positional index in `_instrument_from_row()`, where index 31 is a
lifecycle status appended by a `SELECT p.*, COALESCE(...)` subquery. Adding a
column would silently shift every one of those indices. Five sibling tables
keyed by `instrument_id` extend the model without touching a working read path,
and `professional_instruments` remains the single canonical registry: this
module **refuses** to specify a contract whose instrument is not already
registered there as an active `InstrumentType.FUTURE`.

New tables (migration `20260907_0041`, all with the standard immutability
trigger):

| Table | Holds |
|---|---|
| `futures_contract_series` | The product root — GC, MGC, ES. Multiplier, unit of measure, tick size/value, settlement type, session, currency. |
| `futures_contract_specifications` | One listed delivery month, 1:1 with a registered FUTURE instrument. Full date lifecycle plus its own economics. |
| `futures_margin_requirements` | Versioned initial/maintenance margin per tier, with independent effective and knowledge clocks. |
| `futures_continuous_series_policies` | An approved, content-hashed roll rule. |
| `futures_continuous_series_members` | The materialized, non-overlapping mapping from a continuous series to the real contract it referenced over each window. |

The migration also widens the two `session_type` CHECK constraints from
migration 0008 (`US_EQUITY` / `FX_24X5` / `CRYPTO_24X7`) to admit
`FUTURES_23X5`. None of the three original values describes a nearly-24-hour
weekday futures session that still observes exchange holidays.

## 3. The three invariants

### 3.1 Tick value is bound to the multiplier

`tick_value = tick_size × contract_multiplier` is enforced twice: in
`_require_tick_value_identity()` and again as a PostgreSQL CHECK constraint
(`futures_series_tick_value_identity`, `futures_contract_tick_value_identity`).
Exact `NUMERIC` arithmetic makes this a real constraint, not an approximation.

This is the structural answer to "never treat GLD, XAUUSD, GC and MGC as the
same instrument": those four already had distinct `instrument_id`s, but only
this identity makes a full-size and a micro contract *impossible* to record
with interchangeable economics.

### 3.2 Margin has two clocks, in either order

Exchanges publish margin changes **before** they take effect. A live feed
therefore produces `known_at < effective_from`; a historical backfill produces
`known_at > effective_from`. Neither ordering is asserted. Both timestamps are
always retained, and `margin_point_in_time(effective_at=..., known_at=...)`
gates them separately, so a replay run as-of a past date sees the margin that
was both in force *and* known then.

This is the same two-clock correction Module 3G.1f.2 made for SEC filings,
applied at the point where it first matters for futures: an announced-but-not-
yet-effective margin increase must not size a position that predates it.

### 3.3 A roll is a versioned policy decision

`ContinuousSeriesPolicy` is content-hashed over its own parameters and
re-verified on every read; a stored policy whose hash does not re-derive is
rejected. `materialize_continuous_series()` derives the schedule from the
approved policy plus **only the contracts registered at the stated knowledge
time**, then persists immutable members that a database-level GiST exclusion
constraint keeps non-overlapping per (policy, depth). Any consumer resolving
the same series at the same two timestamps gets the same real contract.

Resolution always returns a real contract's `instrument_id`. A continuous
series is a derived view; it is never itself a tradable instrument.

## 4. Fail-closed behaviour

| Condition | Behaviour |
|---|---|
| `VOLUME_OPEN_INTEREST_CROSSOVER` roll trigger | Raises. The trigger is enumerated because it is the professional standard, but no open-interest authority exists yet (roadmap NEXT-03). It is never silently substituted with a date rule. |
| First-notice roll on a contract with no notice date | Raises, naming the contract. |
| Cash-settled contract carrying a first-notice date | Rejected — a cash-settled product cannot issue a delivery notice. |
| Contract not yet listed when the policy says it should be the depth-N leg | Raises. Emitting the segment would fabricate history; narrowing it would leave a silent gap in a supposedly continuous series. |
| Beyond the final known roll | Resolution raises. The last member is closed, never open-ended — the successor contract is simply not known yet. |
| Materializing the same (policy, depth) twice | Rejected by the exclusion constraint. |
| Instrument not registered, not a FUTURE, or not active | Rejected before any write. |
| Venue / currency / settlement type differing from the series | Rejected. |
| No margin recorded for the requested tier | Raises. It never falls back to another tier. |
| Roll offset on an exact-date trigger | Rejected, so two policies cannot hash differently yet behave identically. |

## 5. Disclosed limitations

- **Roll offsets are calendar days, not exchange sessions.** A session-aware
  offset requires the professional calendar authority to hold a real futures
  calendar for the venue, which no module has onboarded. A calendar-day offset
  is reproducible and never silently wrong about which days an exchange was
  open — it simply does not claim to know. Recorded here so it is not later
  mistaken for a business-day rule.
- **No price adjustment.** A policy records its declared
  `adjustment_method` (`NONE` / `BACK_ADJUSTED_DIFFERENCE` /
  `BACK_ADJUSTED_RATIO`), but nothing in this module adjusts a price series.
  That belongs with the historical bar authority and is not implemented.
- **No exchange data.** Every contract, calendar date and margin figure in the
  tests is a fixture modelled on publicly documented CME/COMEX product
  parameters. None was retrieved from an exchange or verified against one. No
  real futures instrument has been onboarded into any environment.
- **Series-level margin is a default, not a rule.** Real exchanges vary margin
  by delivery month (notably the spot month); the contract-level override
  exists for that, but nothing populates it automatically.
- **Only one calendar-day roll family and depth ≤ 12.** No volume-weighted,
  liquidity-aware, or open-interest-aware roll is available.

## 6. Evidence

- `tests/test_futures_contracts.py` — 27 pure unit tests: the tick-value
  identity (including the GC/MGC discrimination case), contract date-lifecycle
  validation, cash-settled notice rejection, both margin clock orderings,
  policy hash stability and parameter sensitivity, every fail-closed roll path,
  front-month and depth-2 schedule construction, knowledge-time exclusion, and
  order-independence of the derived schedule.
- `tests/test_futures_contracts_postgres.py` — one PostgreSQL integration test
  proving the widened session-type constraint, database-level rejection of an
  inconsistent tick value inserted directly by SQL (bypassing the Python
  contract), fail-closed contract binding, duplicate delivery-month rejection,
  the full two-clock margin resolution matrix including contract-level
  precedence and hedger-tier fail-closed, continuous materialization,
  resolution, re-materialization rejection, knowledge-time gating, closure past
  the final roll, coexisting depths, immutability of all five tables against
  both UPDATE and DELETE, restart durability, and zero interference with a
  pre-existing equity instrument.
- Migration `20260907_0041` is registered in `scripts/verify_postgres_restore.py`,
  so all five tables participate in the backup/restore reconciliation drill.
- `src/trade_platform/futures_contracts.py` is added to the CI zero-error mypy
  slice in `.github/workflows/verify.yml`.

## 7. Authority boundary (unchanged)

No LLM or AI component can create a series, specify a contract, record a margin
requirement, approve a continuous-series policy, or materialize a schedule.
Nothing in this module produces a signal, an `OrderIntent`, a risk decision or
a promotion. `REAL_ACCOUNT_ENABLED` and `AUTO_EXECUTION_ENABLED` remain false
and are untouched by this module.
