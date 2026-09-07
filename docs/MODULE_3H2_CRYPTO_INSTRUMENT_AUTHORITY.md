# Module 3H.2: Crypto Instrument, Funding-Convention and Venue-Rule Authority

Status: **engineering authority only**. No exchange was contacted, no venue
instrument list or trading rule was retrieved, no provider was activated, and
nothing here grants any data, order, risk or trading authority. This is roadmap
**NEXT-02 phase 2**, completing the Multi-Asset Instrument Authority V2 work
begun in [3H.1](MODULE_3H1_FUTURES_CONTRACT_AUTHORITY.md).

## 1. Scope discipline

This module models **what a crypto contract is**. It deliberately does **not**
implement any of the NEXT-03 time-series market-data authorities:

| Concept | Here | NEXT-03 |
|---|---|---|
| Whether a contract is subject to funding | ✅ (a property of the kind) | — |
| Funding interval, offset, cap/floor, settlement asset | ✅ (versioned convention) | — |
| Observed funding **rates** | ❌ | ✅ |
| Which reference-price semantics a contract requires | ✅ (`NONE` / `INDEX_ONLY` / `MARK_AND_INDEX`) | — |
| Observed mark and index **prices** | ❌ | ✅ |
| Open interest, trades, order books | ❌ | ✅ |

A unit test asserts that `CryptoFundingConvention` has no `funding_rate`,
`mark_price`, `index_price` or `open_interest` field, so the boundary cannot
erode silently.

**Margin and leverage are absent by design.** On a crypto venue those are
continuously-revised, position-size- and account-dependent state. Recording them
as instrument metadata would invent an authority that belongs to the
account/risk layer (NEXT-10 prop-firm policy, NEXT-11 multi-account control
plane), and would make every historical replay quietly wrong. They are left
there deliberately, not forgotten.

## 2. Architecture decision

`professional_instruments` remains the single canonical instrument registry.
As in 3H.1, this module adds **sibling tables** keyed by `instrument_id` and
refuses to specify an instrument that is not already registered there with
`AssetClass.CRYPTO`. Symbol history, identifier mappings, delisting and
lifecycle all continue to run through the existing authority — the cross-venue
symbol resolver in this module *joins* `professional_symbol_mappings` rather
than keeping its own copy.

New tables (migration `20260907_0042`, all with the standard immutability
trigger):

| Table | Holds |
|---|---|
| `crypto_instrument_specifications` | Static contract identity: kind, venue, base/quote/settlement asset, linear/inverse/quanto, multiplier, expiry, reference-price requirement. |
| `crypto_funding_conventions` | A perpetual's funding *schedule*, versioned with two clocks. Never a rate. |
| `crypto_venue_trading_rules` | Venue-revised limits (tick size, quantity step, min quantity/notional, precisions), versioned with two clocks. |

### 2.1 Two in-place changes to existing objects

Neither alters column count or ordering, so the positional
`INSERT INTO professional_instruments VALUES (<31 placeholders>)` and
`_instrument_from_row()`'s indices are unaffected:

1. **The currency columns widen from `CHAR(3)` to `VARCHAR(12)`** — on
   `professional_instruments` and on `runtime_instruments`, which
   `register()` writes in the same transaction. ISO 4217 does not cover crypto
   assets, so a three-character column cannot represent `USDT`, `USDC` or most
   token tickers. Python-side validation stays **strictly ISO-4217 three-letter
   for every non-crypto asset class** (`_require_asset_code`), so a typo like
   `"USDD"` still fails immediately on an equity, ETF, FX or futures
   instrument. The widening is scoped to `AssetClass.CRYPTO`.
2. **`representation_kind` admits `PERPETUAL`.** A perpetual swap tracks spot
   through periodic funding instead of converging to it at an expiry, so it is
   neither `SPOT` nor `FUTURE`.

`InstrumentType` gains `CRYPTO_PERPETUAL` and `CRYPTO_DATED_FUTURE` (that
column has no CHECK constraint, so no migration was needed for it). They are
kept distinct from `FUTURE` because `FUTURE` requires contract-code, expiry,
last-trade and roll-rule metadata that neither a perpetual nor a crypto dated
future has.

### 2.2 Static identity vs venue-revised state

The boundary you asked about for minimum quantity/notional is drawn here:

- **Static, in the specification:** what makes an instrument *this* instrument
  — kind, venue, base/quote/settlement asset, settlement style, multiplier,
  contract size, expiry, reference-price requirement. Changing any of these
  makes it a different instrument.
- **Versioned, in `crypto_venue_trading_rules`:** what a venue revises — tick
  size, quantity step, minimum quantity, maximum quantity, minimum notional,
  price and quantity precision. Each version carries its own `effective_from`
  and `known_at`.

The instrument master's own `tick_size` / `lot_size` / precisions remain what
they always were — the registration-time snapshot that forms part of canonical
identity. **Point-in-time consumers must read `crypto_venue_trading_rules`**,
never the master snapshot, for what a venue actually enforced at a moment in
history.

## 3. Invariants

### 3.1 Venue identity is part of the instrument

`BTCUSDT` on one venue and `BTCUSDT` on another are different tradable
instruments with different order books, trading rules and counterparty risk.
Every specification is keyed by venue; `resolve()` requires a venue; and
`resolve_display_symbol()` **fails closed with `AmbiguousCryptoVenueError`,
naming the competing venues**, whenever a symbol exists on more than one venue
and no venue was supplied.

A reserved set of placeholder venue codes (`CRYPTO`, `SPOT`, `UNKNOWN`,
`DEFAULT`) is rejected outright. This means the shipped MVP universe's
`CRYPTO:SPOT:BTCUSD`, registered at the venue-less placeholder venue `CRYPTO`
before this module existed, can never receive a crypto specification — which is
correct: it is a provider-neutral placeholder, not something tradable.

### 3.2 Spot, perpetual and dated future never substitute for each other

`resolve()` takes the kind as a **required** argument, so a spot request can
never return a perpetual. Each kind is additionally bound to exactly one
instrument-master `InstrumentType`, so the canonical registry and this layer
cannot disagree about what an instrument is.

### 3.3 Settlement coherence

`LINEAR` settles the quote asset, `INVERSE` settles the base asset, `QUANTO`
settles a third asset that is neither. Enforced in Python and again as the
`crypto_settlement_style_asset_coherence` database CHECK.

### 3.4 Two clocks, in either order

A venue announces a funding-schedule or trading-rule change *before* it applies
(`known_at < effective_from`); a backfill records one long after
(`known_at > effective_from`). Neither ordering is asserted, both are retained,
and every point-in-time read gates them separately — so a replay predating an
announcement cannot see it even once its effective date has passed.

## 4. The fifteen negative invariants

| # | Condition | Enforced by |
|---|---|---|
| 1 | SPOT + expiry | Python + CHECK `crypto_expiry_matches_kind` |
| 2 | SPOT + funding convention | `register_funding_convention` requires `requires_funding` |
| 3 | PERPETUAL + expiry | Python + CHECK `crypto_expiry_matches_kind` |
| 4 | DATED_FUTURE without expiry | Python + CHECK `crypto_expiry_matches_kind` |
| 5 | Incoherent inverse/linear/quanto settlement | Python + CHECK `crypto_settlement_style_asset_coherence` |
| 6 | Zero/negative tick or quantity step | Python + column CHECKs |
| 7 | Venue mismatch against the instrument master | `specify_instrument` |
| 8 | Invalid base/quote/settlement combination | Python + CHECK `crypto_base_and_quote_differ` |
| 9 | Non-crypto or unregistered instrument | `_require_registered_crypto` |
| 10 | Duplicate/conflicting registration | Primary key + partial unique indexes |
| 11 | Query before `known_at` | `registered_at <= known_at` / `known_at <=` gating |
| 12 | Future-known metadata leaking backwards | Two-clock PIT reads |
| 13 | Same display symbol on two venues, no venue given | `AmbiguousCryptoVenueError` |
| 14 | Derivative resolved as spot | Kind is a required resolution key |
| 15 | Direct SQL UPDATE/DELETE | Immutability triggers on all three tables |

## 5. Shared-database discovery fragility (scoped fix)

Instrument discovery is paginated with `ORDER BY canonical_symbol`, and every
PostgreSQL integration test writes permanently into one shared database for a
whole CI run (the tables are immutable by trigger, so no test can clean up).
Any fixture instrument whose symbol sorts early therefore displaces real
instruments from the operator's first page. Module 3G.1f.2 hit exactly this and
worked around it by renaming fixtures to sort last (`ZAAPL`) — a convention
that holds only while every future author remembers it.

This module replaces the convention with something the code enforces:
`operator_dashboard.RESERVED_TEST_FIXTURE_PREFIX` (`TESTFIXTURE:`). Instruments
under that prefix are omitted from the **unfiltered** discovery page only. They
remain returned by an explicit search and by the instrument detail read, so the
rule can never hide a record from an auditor, and it matches on `instrument_id`
only — never on a symbol — so no real instrument can be caught by it
accidentally.

3H.1's fixtures were migrated onto the prefix as part of this change. A
dedicated integration test registers a fixture instrument whose symbol sorts to
the very front (`AAAAADISCOVERY`) and proves it does not appear on the
unfiltered page while remaining searchable and readable in detail.

Older fixtures in other modules (`SECTEST:`, `PILOT:`, `OPENFIGITEST:`, …) were
left alone to keep this PR focused; they are unaffected and can adopt the
prefix when those modules are next touched.

## 6. Disclosed limitations

- **All data is fixture data.** Venue names such as `BINANCE` and `COINBASE`
  and assets such as `BTC`/`USDT` are realistic placeholder strings used to
  make the tests legible. Nothing was retrieved from or verified against any
  exchange. No venue's real instrument list, trading rules, funding schedule or
  contract specification is claimed anywhere in this module.
- **No provider is activated and no network call exists** in this code path.
- **Margin/leverage deferred** to the account/risk layer, as described above.
- **No index composition.** `index_reference` names the venue's index; this
  module neither stores its constituents nor any of its values.
- **Options are out of scope.** Only SPOT, PERPETUAL and DATED_FUTURE are
  modelled.
- **The instrument master's own tick/lot snapshot is not automatically
  reconciled** against the first `crypto_venue_trading_rules` version. Callers
  performing point-in-time work must read the rules table.

## 7. Evidence

- `tests/test_crypto_instruments.py` — 35 pure unit tests covering every kind
  field invariant, settlement coherence for all three styles (rejections and
  accepted coherent cases), asset-code validation, placeholder-venue rejection,
  the three-kind separation, both funding clock orderings, funding schedule
  validation, venue-rule validation, and the assertion that no observation
  field exists on the funding convention.
- `tests/test_crypto_instruments_postgres.py` — two PostgreSQL integration
  tests proving: a four-character quote asset (`USDT`) now persists; fail-closed
  binding for unregistered, non-crypto, venue-mismatched and kind-mismatched
  instruments; duplicate rejection; **database-level** rejection of all four
  kind/settlement violations inserted directly by SQL, bypassing the Python
  contract; three-kind resolution separation; cross-venue symbol ambiguity and
  its venue-disambiguated resolution; the full two-clock matrix for both
  funding conventions and venue rules including a replay that predates an
  announcement; knowledge-time invisibility; immutability of all three tables
  against UPDATE and DELETE; restart durability; zero interference with a
  pre-existing equity instrument; and the reserved-prefix discovery fix.
- All three tables are registered in `scripts/verify_postgres_restore.py`
  (151 critical tables).
- `src/trade_platform/crypto_instruments.py` is in the CI zero-error mypy slice.

## 8. Authority boundary (unchanged)

No LLM or AI component can specify an instrument, register a funding
convention, record venue trading rules, or resolve anything on their behalf.
Nothing in this module produces a signal, an `OrderIntent`, a risk decision or
a promotion. `REAL_ACCOUNT_ENABLED` and `AUTO_EXECUTION_ENABLED` remain false
and are untouched.
