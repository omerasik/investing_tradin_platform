# Module 3G.1e: Source-Backed Pilot Instrument Master Onboarding

Status: **identity onboarding only**. No market price data was downloaded, no
provider API call was made (Databento or Tiingo), and no real historical
ingestion occurred. This module prepares the canonical instrument master so
that a future, separately-authorized real Databento pilot has correct
identity, venue, and provider-mapping evidence to normalize against — nothing
more.

## 1. What this module adds

`src/trade_platform/pilot_instruments.py` — 16 real, individually-sourced
`ProfessionalInstrument` records (AAPL, MSFT, AMZN, GOOGL, NVDA, META, TSLA,
JPM, KO, XOM, SPY, QQQ, GLD, VTI, IVV, TWTR), registered through the exact
same `PostgresProfessionalInstrumentMaster` every other instrument in this
platform uses — no parallel instrument master, no second resolution path, no
schema change. Kept in a separate module from
`professional_instruments.mvp_instrument_universe()`, which remains completely
untouched and is still what existing fixture/demo tests use; the two
universes use disjoint `instrument_id` prefixes (`PILOT:` vs. the fixture's
`US:VENUE:SYMBOL`) and cannot collide.

Every date is sourced from a specific, citable reference — an SEC
filing/press release, a fund issuer's own published inception date, or (for
AAPL/MSFT/NVDA/SPY/META/TWTR) a value already independently verified via a
live Tiingo API response earlier in this project's research. No date reuses
the fixture's placeholder `date(2000, 1, 1)`. Where full confidence couldn't
be established (e.g. KO's 1919 IPO predates SEC EDGAR), that's disclosed in
the record's own `listing_source_reference`, not silently treated as
equivalent to an SEC-filing-backed date.

## 2. Historical identity cases

- **META**: one instrument, two time-bounded `SymbolMapping` rows — `FB`
  (2012-05-18 IPO through 2022-06-09) and `META` (2022-06-09 onward, SEC 8-K
  sourced). `resolve_symbol("META", ..., <2015>)` correctly fails to
  resolve — the ticker is never back-projected onto the FB era.
- **TWTR**: registered ACTIVE at its real 2013-11-07 IPO, then immediately
  delisted via a real lifecycle event at its real 2022-10-28 delisting date
  (independently corroborated by live Tiingo metadata). It resolves as
  ACTIVE before that date and DELISTED after — never as a currently-active
  instrument.
- **GOOGL**: modeled as beginning on 2014-04-03, the date the GOOGL ticker
  itself started trading (the Class A/Class C reclassification), not
  Google's original 2004-08-19 IPO under the *different* ticker GOOG. GOOG
  itself is not modeled in this universe at all — deliberately, so the two
  can never be confused.
- **ETFs**: SPY/GLD/VTI/IVV are modeled on NYSE Arca (ARCX); QQQ is modeled
  on Nasdaq (XNAS) — confirmed via research, not assumed. QQQ's actual
  1999–2004 AMEX-listing/QQQQ-ticker history is a disclosed, deliberate
  simplification: it predates and is irrelevant to the approved 2020–2026
  pilot window.
- **JPM/XOM**: modeled from the date their *current* ticker began trading
  post-merger (JPM: 2001-01-02, replacing Chase Manhattan's CMB; XOM:
  1999-11-30, replacing Exxon's XON) — each predecessor's own older,
  separately-ticked history is recorded as out of scope for this module
  rather than guessed.

## 3. A real correctness issue found and fixed during implementation

`PostgresProfessionalInstrumentMaster.get_as_of()`/`.delist()`/`resolve_*()`
all gate on a single timestamp serving two different roles: real-world
validity and system-knowledge-time. For a *live* ingestion pipeline these
coincide naturally (you learn a fact as it happens). For a *backfill* of
already-public historical fact — exactly what this module is — they don't:
if `ProfessionalInstrument.registered_at` were set to "today, when this
onboarding script happens to run" instead of the instrument's real listing
date, no historical point-in-time query before today could ever resolve it.

Fixed by setting `registered_at` (on the instrument) and `ingested_at` (on
every `SymbolMapping`/`IdentifierMapping`) equal to the record's own real
`valid_from`/`listing_date` — the same convention the pre-existing fixture
test already uses for its delisting event (`effective_at == ingested_at`).
This is a deliberate backfill-specific modeling choice, documented in
`pilot_instrument()`'s docstring, not a change to
`professional_instruments.py`'s resolution logic itself. A live discovery
pipeline elsewhere in this codebase should keep using real wall-clock time
for `ingested_at`; this module's job is only to backfill hindsight-complete
fact.

## 4. Databento identifier mapping — prepared, not activated

`pilot_databento_identifier_mappings()` builds `IdentifierMapping` rows under
a new `databento_raw_symbol` namespace, using the same time-bounded windows
as the real ticker history above (so a future Databento raw_symbol lookup for
`"FB"` during 2012–2022 and `"META"` after resolve to the same instrument).
This is pure data — nothing in this module contacts Databento.

**Proposed, not executed**, free/read-only Databento calls that would verify
this mapping semantically match Databento's real symbology once a credential
is available: `symbology.resolve` (dataset `EQUS.SUMMARY`, all 16 tickers as
`stype_in=raw_symbol`, a date range covering 2020-01-01–2026-09-01) and
`metadata.list_schemas`/`get_dataset_range` for `EQUS.SUMMARY`/`EQUS.MINI` to
confirm they still list the same schemas assumed since Module 3G.1a. Both are
documented Databento-free endpoints (see Module 3G.1d Phase 1); neither was
called in this module per the owner's explicit instruction to stop and report
rather than silently activate them.

## 5. Calendars

`professional_instruments.standard_calendars()` previously registered only
one US-equity venue calendar (ARCX). Extended to also register XNAS and XNYS
— the two venues this pilot universe actually needs and previously had zero
calendar coverage. All three follow the identical real, public, uncontroversial
core trading session (9:30am–4:00pm America/New_York, Monday–Friday) — not
generalized further, and not weakened: the pre-existing ARCX behavior (and
every assertion the pre-existing fixture test makes against it) is unchanged
and still passes. Note: as of this module, nothing in the historical
ingestion/normalization/Data-Health path actually calls `is_open()` yet — it
remains an available, tested capability rather than a wired-in gate.

## 6. A related, separately-tracked finding (not fixed here)

`historical_market_data._CONSOLIDATED_TAPE_ELIGIBLE_VENUES` currently
contains only `{"XNAS", "ARCX"}`. Four pilot symbols (JPM, KO, XOM, TWTR) are
NYSE-listed (`XNYS`) — and Databento's `EQUS.SUMMARY`/`EQUS.MINI` are
documented as consolidated across *all* NMS exchanges, NYSE included. Once a
real Databento pilot runs, `normalize()` would incorrectly reject those four
symbols' `CONSOLIDATED_TAPE_EXCHANGE` observations as `exchange_instrument_mismatch`
unless `XNYS` is added to that set. Deliberately **not fixed in this module**
— it's a `historical_market_data.py` change relevant only once real Databento
ingestion is authorized, not to instrument-identity onboarding. Flagged here
so it isn't rediscovered from scratch later.

## 7. Hard boundaries

- No price/OHLCV data was downloaded or stored.
- No Databento or Tiingo API call was made.
- No `AuthorizedHistoricalSource` was registered (that's a Databento-pilot-time
  step, not an instrument-identity step).
- `mvp_instrument_universe()` and every test depending on it are unchanged
  and still pass.
