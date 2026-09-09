# Module 3J.1 (Proposal): Multi-Asset Derivatives Feature Pack

Status: **proposal only — not implemented, not authorized, not started.** No
code, migration, schema, or test in this repository changes as a result of
this document. This is an evaluation of candidate features against the
evidence Modules 3H.1/3H.2, 3I.1, 3I.2, 3I.3 and 3J.0 actually established, so
an owner can approve a concrete, bounded scope before any implementation PR
is opened.

**Revision 2 (owner-reviewed):** the general direction was approved, with
four architecture decisions made explicit and several technical corrections
applied. See §8 for the decision log; every item that was `REQUIRES REVIEW`
in the first revision is now `OWNER DECIDED`. This revision recommends
**seven** feature definitions, not six.

## 0. Precondition

3J.0 (generalized Feature Authority subject identity — `INSTRUMENT` |
`FUTURES_SERIES`) is merged and exact-main verified (see
[MASTER_ROADMAP.md](MASTER_ROADMAP.md), commit `73c44e909f2bea63cdd2580b61f86f80d3d4422f`,
exact-main run `34249496551`). 3J.1 is the first module that would actually
*use* `materialize_subject()` / `latest_as_of_subject()` for a real feature
family — 3J.0 itself computed nothing.

## 1. What evidence already exists to build on

| Authority | What it holds | PIT shape |
|---|---|---|
| 3I.1 `futures_settlement_observations` | Per-contract settlement price, currency, finality, revision | event/effective/knowledge clocks, revision-ranked |
| 3I.1/3I.2 `open_interest_observations` | Per-instrument OI with explicit unit (`CONTRACTS`/`BASE_ASSET`/`QUOTE_NOTIONAL`) and, for asset-denominated units, `unit_asset` | same |
| 3I.2 `crypto_funding_observations` | `FUNDING_RATE_REALIZED` / `FUNDING_RATE_INDICATIVE`, resolved against a versioned 3H.2 funding convention (interval, floor/cap) | realized: event_at = funding instant; indicative: event_at = publication instant, target strictly future |
| 3I.2 `crypto_reference_price_observations` | `MARK_PRICE` / `INDEX_PRICE`, quote-asset-checked against the instrument | event_at = observation instant |
| 3I.3 `futures_term_structure_curves` / `_points` | An already-derived, immutable, PIT-keyed ordered curve per futures series, with a frozen settlement-price snapshot per point, proven (by deferred trigger) to match the canonical settlement row it was taken from | keyed by `(series_id, dataset_version_id, method_id, as_of, knowledge_at)` |

3I.3's own doc is explicit that curve-level derivatives (spread, slope,
curvature, carry) belong in "Feature Authority once a real formula, exact
maturity dates and an explicit day-count convention are reviewed together" —
that review is what this document is.

## 2. Design principles for every candidate below

1. **Curve-shaped features read the 3I.3 curve artifact, never raw settlement
   observations directly.** 3I.3 already did point selection (finality
   policy, staleness policy, no-fallback/no-interpolation, minimum point
   count) and froze it into an immutable, hash-identified curve. Re-deriving
   spread/curvature straight from `futures_settlement_observations` would
   create a second, divergent selection policy — exactly the kind of "second
   authority" both 3I.3 and 3J.0 were written to prevent.
2. **A feature must never diff or ratio two observations in different units,
   unit assets, currencies, or quote assets.** Direct extension of 3I.1 §6
   and 3I.2 §7's "no conversion, ever" rule into feature space.
3. **A feature must never blend two independently-published observations
   without an explicit, declared matching rule covering both time and
   source.** Exact-instant match by default (a tolerance window only if
   explicitly declared, mirroring 3I.3's `max_staleness_days`), **and** by
   default the two observations must share the same authorized
   source/dataset identity — matching timestamps alone is not sufficient,
   since two different providers/venues can each publish a value at the same
   instant using different methodologies. No feature invents interpolation.
4. **PIT gating is per-input, not per-feature.** Every observation/curve read
   into a feature computation must independently satisfy
   `knowledge_at <= decision_at`; a feature's own `knowledge_at` must be at
   least the maximum `knowledge_at` of every input it read, and a feature is
   never permitted to leak one input's future knowledge through another
   input's earlier one.
5. **A feature about a whole futures series is a `FUTURES_SERIES` subject; a
   feature about one contract or one crypto instrument is an `INSTRUMENT`
   subject.** This is exactly the distinction 3J.0 built the subject model
   for — no candidate below collapses it back into a single type.
6. **Annualization requires an explicit, declared day-count/year-fraction
   convention, applied through a named `year_fraction(T1, T2, convention)`
   function.** Never an implicit ACT/365 (or any other) assumption baked
   silently into a formula.
7. **A normalized quantity is not automatically dimensionless.** Dividing a
   price-rate-of-change by a price removes the price dimension but not a time
   dimension; units must be stated exactly, not asserted as "dimensionless"
   for convenience.

## 3. Recommended feature definitions (seven)

### 3.1 `futures_front_back_normalized_spread` — **include**

`(P_back - P_front) / P_front` between the two nearest-expiration points on an
already-derived 3I.3 curve for a given `(series_id, dataset_version_id,
method_id, as_of, knowledge_at)`. Dimensionless, same-currency by
construction (3I.3 curves already exclude mixed-currency evidence), no
interpolation. Subject: `FUTURES_SERIES`.

### 3.2 `futures_annualized_calendar_spread_rate` — **include, highest priority**

This is the one candidate 3I.3 explicitly named as deferred and the direct
reason 3J.0 exists (a series-level artifact needing a series-level subject).
**No independent `curve_slope` feature is registered.** §3.1 and this feature
share one internal deterministic calendar-spread helper but remain two
distinct feature definitions, because their output semantics and units
differ — §3.1 is a point-in-time dimensionless spread, this is an annualized
rate.

Formula, using the method's own declared `carry_day_count_convention` (3I.3's
method registry already reserves this field; 3J.1 is the first thing that
reads it) and each contract's real `expiration_date` from 3H.1 (never
symbol/ticker text):

```
normalized_spread = (P_back - P_front) / P_front                    # from §3.1
year_fraction     = year_fraction(T_front, T_back, declared_day_count_convention)
annualized_rate   = normalized_spread / year_fraction
```

No `periods_per_year` language and no implicit ACT/365 assumption —
`year_fraction()` is a named function parameterized by the method's declared
convention. Units: `1/year` (a rate per year of the declared convention).
Subject: `FUTURES_SERIES`.

### 3.3 `futures_curve_curvature` — **include, with corrected dimensional semantics**

Computed only when the curve has at least 3 surviving points after 3I.3's own
selection policy (finality/staleness/minimum-point-count) — never padded or
approximated when only 2 points survive.

Exact unequal-spacing three-point second-derivative estimator, using the
declared day-count convention's `year_fraction()` for the two gaps (not raw
calendar days, not point index):

```
h1 = year_fraction(T_front, T_mid, declared_day_count_convention)
h2 = year_fraction(T_mid,  T_back, declared_day_count_convention)

d2P_dT2 = (2 / (h1 + h2)) * ( (P_back - P_mid) / h2 - (P_mid - P_front) / h1 )

curvature = d2P_dT2 / P_front
```

**Corrected units:** `d2P_dT2` has units of `price / year²`. Dividing by
`P_front` (a price) removes the price dimension but **not** the time
dimension — the result has units of `year^-2`, not "dimensionless." The
proposal's first revision incorrectly called this dimensionless; that is
fixed here. No arbitrary extra scaling is introduced to force a dimensionless
result. Subject: `FUTURES_SERIES`.

### 3.4 `open_interest_change` — **include, one cross-asset definition, per-instrument only**

One definition, callable against any `INSTRUMENT` (futures contract or crypto
perpetual/dated future), because 3I.1/3I.2 intentionally created one
canonical open-interest authority (`open_interest_observations`, one kind,
one payload shape) — a `futures_oi_change` and a `crypto_oi_change` would
duplicate that authority in feature space for no reason.

**Only the change is registered, not the raw level.** `FeatureMaterialization`
represents one value with one semantic definition; open-interest *level* is
already canonical market evidence directly queryable from
`open_interest_observations` / `research_query()`, and mirroring it into
Feature Authority as a second read path is not justified without a concrete
downstream consumer that specifically needs it through the Feature Authority
API. If that need arises later, a `open_interest_level` feature can be
proposed on its own.

`OI[t] - OI[t-1]` requires, all four, or the pair is not eligible and no
value is produced:

1. same instrument (same `subject_id`);
2. same OI `unit`;
3. same `unit_asset` (for asset-denominated units — never converted);
4. same authorized source/dataset identity by default (never diffed across
   two providers whose OI methodology could differ).

`OI[t-1]` selection must be deterministic and PIT-safe: the most recent
eligible prior observation (by the above four criteria) whose own
`knowledge_at <= decision_at`, ranked identically to how 3I.1/3I.2 already
rank revisions (`revision DESC, ingested_at DESC`) — never "any" prior
observation. Subject: `INSTRUMENT`.

### 3.5 `crypto_mark_index_basis` — **include, exact-timestamp match plus source consistency**

`(mark_price - index_price) / index_price` for one crypto instrument. 3I.2
already guarantees both observations' price asset equals the instrument's own
quote asset. Two conditions must both hold, or no value is produced:

1. **Exact `event_at` match** — v1 has no tolerance/staleness window. A
   source-specific tolerance window can be considered later, but only after
   empirical real-provider timing evidence exists; it is not designed here.
2. **Source consistency** — the `MARK_PRICE` and `INDEX_PRICE` observations
   must, by default, share the same authorized source/dataset identity.
   Provider A's mark must never be paired with Provider B's index merely
   because their timestamps coincide, unless the 3I.2 authority is found (at
   implementation time) to already define a stronger canonical pairing
   relation between a venue's mark and its own index than "same source" —
   see §9 for this residual check.

Subject: `INSTRUMENT`.

### 3.6 `crypto_realized_funding_annualized` — **include**

Annualizes a realized funding rate using the funding interval already
recorded on the resolved 3H.2 convention (`periods_per_year = seconds_per_year
/ interval_seconds`). 3I.2 already fails closed ("unknown cadence") when an
interval is not a whole number of seconds — this feature must inherit that
same fail-closed behavior rather than guessing a periods-per-year figure.
The `seconds_per_year` constant used for the annualization must itself be
declared explicitly (e.g. on the convention or the feature definition), not
hard-coded as an implicit 365-day assumption, mirroring principle 6/7 above.
Subject: `INSTRUMENT`.

### 3.7 `crypto_funding_forecast_error` — **include, renamed and re-scoped from "indicative-vs-realized delta"**

Renamed from the first revision's "indicative-vs-realized funding delta" to
make explicit that this is a **forecast-error / estimate-error** feature, not
a pre-event funding prediction: it only becomes knowable *after* the realized
funding observation exists, and it must never appear in a training/backtest
row whose decision time precedes that realized observation.

`realized_rate - indicative_rate`, where `indicative_rate` is the most recent
eligible `FUNDING_RATE_INDICATIVE` observation for the same
`target_funding_at`, published strictly before it. Eligibility requires all
of:

1. same instrument;
2. same target funding instant (`target_funding_at`);
3. compatible source/venue identity between the realized and indicative
   observations;
4. the same applicable funding-convention identity and version resolved for
   both sides (not two different convention versions);
5. the indicative observation is the most recent one published strictly
   before the target funding instant — never "any" indicative estimate,
   since 3I.2 preserves every successive revision.

`feature.knowledge_at` must be at least the maximum `knowledge_at` of both
the qualifying indicative observation and the realized observation; a
decision-time read at `decision_at` must not see this feature before both
were themselves individually knowable by `decision_at`. Subject:
`INSTRUMENT`.

### Summary

| # | Feature | Verdict |
|---|---|---|
| 3.1 | `futures_front_back_normalized_spread` | Include |
| 3.2 | `futures_annualized_calendar_spread_rate` | Include — highest priority |
| 3.3 | `futures_curve_curvature` | Include, corrected units (`year^-2`, not dimensionless) |
| 3.4 | `open_interest_change` | Include — one cross-asset, per-instrument definition; level not mirrored |
| 3.5 | `crypto_mark_index_basis` | Include — exact-match + source consistency |
| 3.6 | `crypto_realized_funding_annualized` | Include |
| 3.7 | `crypto_funding_forecast_error` | Include — renamed/re-scoped |

No independent `curve_slope` feature, no `futures_oi_change` /
`crypto_oi_change` split, and no raw `open_interest_level` feature are
recommended — each was folded into, or deliberately excluded from, the seven
above.

### Implementation recommendation table

Seven feature definitions would be registered if this proposal is approved:

| Feature | Subject type | Canonical input authority | Formula | Units | PIT semantics | Decision |
|---|---|---|---|---|---|---|
| `futures_front_back_normalized_spread` | `FUTURES_SERIES` | 3I.3 `futures_term_structure_curves`/`_points` (read-only) | `(P_back - P_front) / P_front` over the two nearest-expiration surviving curve points | Dimensionless | Inherits the curve's own `(as_of, knowledge_at)`; no independent PIT read | **Include** |
| `futures_annualized_calendar_spread_rate` | `FUTURES_SERIES` | 3I.3 curve points + 3H.1 `expiration_date` | `normalized_spread / year_fraction(T_front, T_back, declared_day_count_convention)` | `1/year` (per the declared convention) | Same as above; day-count convention is part of the method's content hash | **Include** — highest priority |
| `futures_curve_curvature` | `FUTURES_SERIES` | 3I.3 curve points (≥3 surviving) | `(2/(h1+h2)) * ((P_back-P_mid)/h2 - (P_mid-P_front)/h1) / P_front`, `h1,h2` via `year_fraction()` | `year^-2` (not dimensionless) | Computed only when ≥3 points survive 3I.3's own selection policy; absent otherwise, never approximated | **Include** |
| `open_interest_change` | `INSTRUMENT` | 3I.1/3I.2 `open_interest_observations` (shared kind, both asset classes) | `OI[t] - OI[t-1]`, deterministic PIT-safe prior selection | As stored (`CONTRACTS`/`BASE_ASSET`/`QUOTE_NOTIONAL`) — never converted; refused if `unit`/`unit_asset`/source identity differ | Both sides independently gated by `knowledge_at <= decision_at` | **Include** — per-instrument only; level, and series-level aggregate, **deferred** |
| `crypto_mark_index_basis` | `INSTRUMENT` | 3I.2 `crypto_reference_price_observations` | `(mark_price - index_price) / index_price` | Dimensionless | Only when `MARK_PRICE`/`INDEX_PRICE` share identical `event_at` **and** source identity | **Include** |
| `crypto_realized_funding_annualized` | `INSTRUMENT` | 3I.2 `crypto_funding_observations` (`FUNDING_RATE_REALIZED`) + 3H.2 funding convention | `rate * (seconds_per_year / interval_seconds)`, `seconds_per_year` declared explicitly | `1/year` | Uses convention resolved at the funding instant; fails closed if interval isn't a whole number of seconds | **Include** |
| `crypto_funding_forecast_error` | `INSTRUMENT` | 3I.2 `crypto_funding_observations` (both kinds) | `realized_rate - indicative_rate`, indicative = most recent eligible estimate for the same `target_funding_at` published strictly before it | Rate (dimensionless) | `feature.knowledge_at >= max(knowledge_at)` of both inputs; never visible before both are individually knowable | **Include** |

Deferred (not among the seven): series-level/front-month/roll-selected
aggregate open interest, any OI-weighted continuous-series feature, a
mark-index tolerance window, and crypto term structure.

## 4. What this proposal explicitly excludes from 3J.1's scope

- **Total-series OI, front-month aggregate OI, roll-selected OI, and any
  OI-weighted continuous-series feature** — blocked on the separately
  reviewed roll/aggregation policy that 3I.1 §9 already deferred. Feature
  Authority must not manufacture a "front month" definition of its own.
- **Raw open-interest level as a Feature Authority feature** — remains
  canonical market evidence, read directly, unless a concrete downstream
  consumer justifies mirroring it later.
- **Any interpolation, extrapolation, or cross-unit conversion** anywhere —
  consistent with 3I.1/3I.2/3I.3's existing no-fallback rule.
- **Mark-index basis with a staleness/tolerance window** — v1 is exact-match
  (time and source) only; a tolerance window is a separate, later decision
  pending real-provider timing evidence.
- **Crypto term structure** — 3I.3 is futures-only by instruction; crypto has
  no settlement-price authority yet, so no crypto curve exists to build a
  crypto spread/rate/curvature feature on.
- **Options, quotes, trades, or L2** — no authority exists for any of these
  (3I.4/3I.5 remain planned-not-authorized).
- **Any ML-forecast or internally-modelled funding/price input** — every
  feature above reads only already-authorized, provider/venue-published
  market-data or already-derived 3I.3 evidence.

## 5. `FeatureFamily`: one new stable member, `DERIVATIVES`

**Owner decision:** 3J.1 adds exactly one new `FeatureFamily` member,
`DERIVATIVES`, for all seven features above. `DERIVATIVES_TERM_STRUCTURE`,
`DERIVATIVES_FUNDING`, `DERIVATIVES_POSITIONING`, or any other multi-member
taxonomy split is explicitly **not** introduced in 3J.1.

Rationale: `FeatureFamily` is a broad taxonomy, not a feature's complete
semantic identity — the immutable feature name, definition version, required
inputs, parameters, units, and calculation version already carry the
fine-grained meaning (this is exactly why `futures_curve_curvature` and
`crypto_funding_forecast_error` are unambiguous as *names* without needing a
matching family split). A single `DERIVATIVES` family avoids prematurely
freezing multiple taxonomy names into durable, hard-to-rename definitions
before real usage patterns are known.

`FeatureDefinitionVersion.required_dataset_types` already accepts free-form
strings (no schema change): `FUTURES_TERM_STRUCTURE` (pointing at a 3I.3
curve, not a raw `ObservationKind`), `OPEN_INTEREST`, `FUNDING_RATE_REALIZED`,
`FUNDING_RATE_INDICATIVE`, `MARK_PRICE`, `INDEX_PRICE`.

## 6. Manifest/provenance contract

Every `source_observation_manifest` entry must be a resolvable canonical id —
never a human-readable label alone.

**Curve-derived features** (`futures_front_back_normalized_spread`,
`futures_annualized_calendar_spread_rate`, `futures_curve_curvature`) must
manifest:

- `curve_id` and the curve's own `content_hash`;
- the method's id, version, and `content_hash`;
- the sealed dataset's version id and `content_hash` (the same identity the
  curve itself was derived against — see §7);
- the exact ordered list of curve point ids actually used (front/mid/back as
  applicable to the feature).

**Observation-derived features** (`open_interest_change`,
`crypto_mark_index_basis`, `crypto_realized_funding_annualized`,
`crypto_funding_forecast_error`) must manifest:

- the exact canonical normalized observation id(s) read (e.g. two OI
  observation ids for a change; one mark and one index observation id; one
  realized funding observation id; one realized plus one indicative
  observation id for forecast error);
- each observation's source/authorization identity (the id used for the
  source-consistency check in §3.4/§3.5/§3.7);
- for funding features, the resolved funding convention's id and version.

## 7. Dataset identity (`FeatureMaterializationV2.dataset_version`)

- **Curve-derived feature:** `dataset_version` is the **same** sealed
  `HistoricalDatasetVersion` identity the consumed 3I.3 curve was itself
  derived against — i.e. copy the curve row's own `dataset_version_id`
  verbatim. The curve's `curve_id` and `content_hash` are carried separately
  as manifest provenance (§6); a curve id or a curve's content hash must
  never be substituted for the sealed dataset version.
- **Observation-derived feature:** `dataset_version` is the sealed
  `HistoricalDatasetVersion` version that the read normalized observation(s)
  belong to via `historical_dataset_members`. Where a feature reads two
  observations (OI change, mark-index basis, funding forecast error), the
  ordinary case is both resolving to the same sealed dataset version; a
  feature reading two observations that resolve to two *different* sealed
  dataset versions is a case this proposal does not resolve — see §9.

## 8. Decision log — all four `REQUIRES REVIEW` items are now `OWNER DECIDED`

1. **`FeatureFamily` naming — OWNER DECIDED.** One new member, `DERIVATIVES`,
   for all seven features (§5). No per-shape family split in 3J.1.
2. **Open-interest scope — OWNER DECIDED.** Per-instrument/per-contract
   `open_interest_change` only. Total-series OI, front-month aggregate OI,
   roll-selected OI, and any OI-weighted continuous-series feature are
   deferred until the separately reviewed roll/aggregation policy exists; no
   "front month" definition is manufactured inside Feature Authority.
3. **Mark/index matching — OWNER DECIDED.** Exact timestamp matching only for
   v1, **plus** a same-authorized-source/dataset-identity requirement by
   default (§3.5) — not timestamp matching alone. A tolerance window is
   deferred pending real-provider timing evidence.
4. **Duplicate formulas — OWNER DECIDED (revised).** No independent
   `curve_slope` feature; `futures_front_back_normalized_spread` and
   `futures_annualized_calendar_spread_rate` are two distinct feature
   definitions sharing one internal calendar-spread helper, using
   `year_fraction()` rather than `periods_per_year` language for
   annualization. Futures and crypto open-interest change are one
   cross-asset `open_interest_change` definition, but raw OI *level* is
   **not** mirrored into Feature Authority as its own feature. Net scope:
   **seven** feature definitions, not six.

## 9. Remaining unresolved item for the implementation PR

The exact database column(s) that define "authorized source/dataset
identity" for the cross-provider consistency checks in §3.4 `open_interest_change`,
§3.5 `crypto_mark_index_basis`, and §3.7 `crypto_funding_forecast_error` (a
bare `source_id`, a `(source_id, venue)` composite, or the resolved
`dataset_version_id`) has **not** been pinned to specific column names here.
This proposal states the requirement (§2 principle 3) and recommends
"same authorized source by default," but resolving it to an exact schema
reference — and confirming whether 3I.2 already defines a stronger canonical
mark/index pairing relation than "same source" — requires inspecting the
exact 3I.1/3I.2 envelope schema at implementation time. This should be
resolved and written into the implementation PR's design doc before any code
is merged, not assumed here. Similarly, the two-observations-resolve-to-two-
different-sealed-dataset-versions case noted in §7 needs an explicit rule at
implementation time; no existing feature in this codebase has exercised that
case.

## 10. No authority granted by this document

This is analysis and a recommendation only. No feature, migration, or API
described here exists in the codebase. No strategy, signal, opportunity,
order, or risk authority is implied. 3J.1 does not begin until the owner
approves a scope from this document and a separate implementation PR is
opened, reviewed, and merged following the same branch → PR → CI → merge →
exact-main verification discipline as every prior module.
