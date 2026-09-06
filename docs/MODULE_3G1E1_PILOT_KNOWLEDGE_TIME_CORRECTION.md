# Module 3G.1e.1: Pilot Instrument Knowledge-Time / Lineage Correction

Status: **corrective identity onboarding only**. No provider API call was made
(Databento or Tiingo), no price data was touched, and no Git history was
rewritten. Corrects three issues found in Module 3G.1e after merge, before
any real Databento pilot preflight.

## 1. Root cause: real time vs. system-knowledge time were conflated

Module 3G.1e set `ProfessionalInstrument.registered_at` to each instrument's
real `listing_date`, and every `SymbolMapping`/`IdentifierMapping.ingested_at`
to that mapping's own historical `valid_from` — to make historical
point-in-time queries pass. This was wrong: it represented this platform as
having *known* facts in 1980, 2012, or 2022, when in reality this backfill
happened in 2026. A historical fact's effective/validity time and this
platform's ingestion/knowledge time are different dimensions, and collapsing
them fabricates audit history.

## 2. Corrected timestamp model

- `ProfessionalInstrument.registered_at` = `onboarded_at` — the real
  wall-clock time this backfill actually ran — for every one of the 16
  instruments, uniformly. `listing_date` (the historical fact) is unchanged.
- `SymbolMapping`/`IdentifierMapping.ingested_at` = `onboarded_at` for every
  row. `valid_from`/`valid_until` (the historical facts) are unchanged.
- `TWTR`'s delisting: `effective_at` = the real 2022-10-28 date;
  `ingested_at` = `onboarded_at`.
- Historical point-in-time resolution now goes exclusively through
  `resolve_identifier_point_in_time()` (pre-existing, unmodified) and a new
  `resolve_symbol_point_in_time(symbol, venue, effective_at, known_at)` on
  `PostgresProfessionalInstrumentMaster`, mirroring it exactly for symbol
  mappings — added because `resolve_symbol()`'s single-timestamp shape cannot
  correctly separate "was this ticker valid then" from "did we know about it
  by now." `resolve_symbol()`/`get_as_of()` are otherwise unchanged and
  remain correct for their actual purpose: current-state queries at/after
  `onboarded_at`.

**Two further real bugs surfaced once timestamps were made honest** (both
found by actually running the corrected code against a real database, not by
inspection):

- `delist()` validated the instrument's existence via
  `get_as_of(instrument_id, effective_at)` — using the real-world delisting
  date. For any backfilled historical delisting, `effective_at` (e.g. 2022)
  necessarily precedes `registered_at` (2026, honest onboarding time), so this
  check could never pass. Fixed to check existence as of `ingested_at`
  instead (the moment we're actually recording the delisting) — provably
  behavior-preserving for a live delisting, where `ingested_at ≈ effective_at`
  already (and `ingested_at >= effective_at` is separately enforced).
- `get_as_of()`'s lifecycle-status subquery picked the qualifying event with
  the latest `effective_at`. The `ACTIVE` bootstrap event recorded at
  registration uses `registered_at` as its own `effective_at` — which, for a
  backfilled instrument, is *later* than the real historical delisting's
  `effective_at`, so the ordering picked the bootstrap row and reported
  `ACTIVE` for an instrument that is, in the real world, long since delisted.
  Fixed by making a qualifying `DELISTED` event always win over the `ACTIVE`
  bootstrap, rather than relying on `effective_at` ordering between two
  different kinds of event — this model has no re-listing state, so that's
  the correct general semantics, not a special case for backfills.

Both fixes were verified against the pre-existing fixture test
(`test_professional_instrument_master.py`) before and after, with identical
results — neither changes behavior for a live (non-backfill) delisting.

## 3. GOOGL — corrected lineage

Re-audited against SEC/contemporaneous primary sources. **GOOGL is the
continuous Class A security since Google, Inc.'s real 2004-08-19 IPO**, not a
new instrument beginning at the 2014 ticker change (Module 3G.1e's error).
Class A traded as ticker `GOOG` from 2004-08-19 until the 2014-04-03
reclassification, then as `GOOGL` from 2014-04-03 onward — modeled as one
instrument (`PILOT:GOOGL`, `listing_date=2004-08-19`) with two time-bounded
`SymbolMapping` rows. The ticker `GOOG` today belongs to a separate,
newly-created, non-voting Class C security (created in the same 2014 event) —
economically and legally distinct from Class A, and deliberately **not**
modeled in this pilot universe. `PILOT:GOOG` does not exist as an instrument.

## 4. QQQ — corrected, disclosed scope

QQQ's fund inception (1999-03-10, on AMEX) is recorded as the instrument's
real `listing_date`. Its `SymbolMapping` to `QQQ`/`XNAS`, however, is
**deliberately scoped** to begin 2015-01-01 — an explicit, disclosed
placeholder chosen only to comfortably precede the approved 2020-2026 pilot
window, not a claimed real event date. QQQ's real history (AMEX 1999-2004,
Nasdaq migration in 2004, ticker `QQQQ` 2004-2011, reverted to `QQQ` in 2011)
is **not** modeled — modeling it correctly would require adding a fourth,
pilot-irrelevant venue (AMEX/NYSE American) purely for history outside the
approved window. `resolve_symbol_point_in_time("QQQ", "XNAS", <before
2015-01-01>, ...)` correctly fails rather than silently claiming coverage
this module never verified.

## 5. Source-provenance replacements

Every `tiingo-meta:*` source reference has been replaced. Tiingo is not
used as a source anywhere in this module — consistent with Tiingo Starter's
Terms of Use §1.6(a) prohibiting persistent storage of Tiingo Data, which
this instrument master is.

| Symbol | Old (Tiingo) | New source |
|---|---|---|
| AAPL | `tiingo-meta:AAPL` | Well-established public record (pre-EDGAR era; same confidence tier as KO, already non-Tiingo) |
| MSFT | `tiingo-meta:MSFT` | Well-established public record (pre-EDGAR era) |
| NVDA | `tiingo-meta:NVDA` | SEC EDGAR CIK 0001045810 (S-1 filed 1998-03-06, 424B4 prospectus) |
| SPY | `tiingo-meta:SPY` | State Street Global Advisors / SPDR S&P 500 ETF Trust fund documents |
| META (FB era) | `tiingo-meta:META` | SEC EDGAR CIK 1326801 |
| TWTR (IPO + delisting) | `tiingo-meta:TWTR` | SEC EDGAR CIK 1418091 (S-1 filed 2013-10-03; 8-K re: merger consummation and NYSE delisting notice) |

No Tiingo API call was made to produce these replacements — all came from
web/public-record research already independently corroborating the
previously-observed values.

## 6. TWTR — verified PIT result

- Real IPO: 2013-11-07 (SEC EDGAR CIK 1418091, S-1).
- Real delisting: 2022-10-28 — the economically meaningful last trading day
  (merger consummation, per Twitter's 8-K notice to NYSE). Distinct from the
  formal SEC Form 25 deregistration effective date (2022-11-08, a later
  administrative step under NYSE Rule 12d2-2) — the earlier, trading-cessation
  date is what this platform's lifecycle model uses.
- `get_as_of("PILOT:TWTR", onboarded_at)` → `DELISTED` (current, correct state).
- `resolve_symbol_point_in_time("TWTR", "XNYS", <2021>, onboarded_at)` →
  resolves correctly (historical validity + honest knowledge time).
- `resolve_symbol_point_in_time("TWTR", "XNYS", <after 2022-10-28>, onboarded_at)`
  → fails, as required.

## 7. Preserved from PR #89 (not reverted)

Separate `PILOT:` instrument universe with no parallel instrument master;
`XNAS`/`XNYS` calendar support (unaffected by this correction); real
venue/MIC work for JPM/KO/XOM/ETFs; the META/TWTR lifecycle *concepts*
(corrected in timestamp handling, not removed); the `databento_raw_symbol`
namespace preparation; fixture isolation (`mvp_instrument_universe()`
untouched).

## 8. Known, deliberately unfixed blocker

`historical_market_data._CONSOLIDATED_TAPE_ELIGIBLE_VENUES` still lacks
`XNYS` (JPM/KO/XOM/TWTR are NYSE-listed). Per owner instruction, **not**
touched in this corrective PR — it must be fixed, separately reviewed and
tested, before the first real Databento batch is normalized, not before.

## 9. Testing

Full local verification against a fresh, single-use PostgreSQL 16 container,
matching CI's `verify` job exactly: `python -m unittest discover -s tests` →
702 tests, `OK` (9 skipped), run twice (before and after the `delist()`/
`get_as_of()` fixes) with identical pass counts except for the fix itself.
`ruff` and `mypy` clean on all touched files.
