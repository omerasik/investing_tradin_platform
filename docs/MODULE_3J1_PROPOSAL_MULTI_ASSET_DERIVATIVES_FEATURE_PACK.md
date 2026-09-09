# Module 3J.1 (Proposal): Multi-Asset Derivatives Feature Pack

Status: **proposal only — not implemented, not authorized, not started.** No
code, migration, schema, or test in this repository changes as a result of
this document. This is an evaluation of candidate features against the
evidence Modules 3H.1/3H.2, 3I.1, 3I.2, 3I.3 and 3J.0 actually established, so
an owner can approve a concrete, bounded scope before any implementation PR
is opened.

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

These are non-negotiable given the instruction not to force every candidate
in, and given what has already gone wrong once in this codebase (the 3I.3
CHECK-on-NULL gap, the 3J.0 collision-on-textual-identity risk):

1. **Curve-shaped features read the 3I.3 curve artifact, never raw settlement
   observations directly.** 3I.3 already did point selection (finality
   policy, staleness policy, no-fallback/no-interpolation, minimum point
   count) and froze it into an immutable, hash-identified curve. Re-deriving
   spread/slope/curvature straight from `futures_settlement_observations`
   would create a second, divergent selection policy — exactly the kind of
   "second authority" both 3I.3 and 3J.0 were written to prevent.
2. **A feature must never diff or ratio two observations in different units,
   unit assets, currencies, or quote assets.** This is the direct extension
   of 3I.1 §6 and 3I.2 §7's "no conversion, ever" rule into feature space.
3. **A feature must never blend two observations whose instants disagree**
   without an explicit, declared matching rule (exact-match by default; a
   tolerance window only if explicitly declared, mirroring 3I.3's
   `max_staleness_days`). No feature invents interpolation.
4. **PIT gating is per-input, not per-feature.** Every observation/curve read
   into a feature computation must independently satisfy
   `knowledge_at <= decision_at` (or the feature's own `knowledge_at`); a
   feature is not permitted to leak one input's future knowledge through
   another input's earlier one.
5. **A feature about a whole futures series is a `FUTURES_SERIES` subject; a
   feature about one contract or one crypto instrument is an `INSTRUMENT`
   subject.** This is exactly the distinction 3J.0 built the subject model
   for — no candidate below should collapse it back into a single type.
6. **Annualization requires an explicit, declared day-count/periods
   convention.** Never an implicit "assume calendar days" or "assume 8h
   funding" default silently baked into the formula.

## 3. Candidate-by-candidate evaluation

### 3.1 Futures front/back normalized spread — **recommend**

`(P_back - P_front) / P_front` between the two nearest-expiration points on an
already-derived 3I.3 curve for a given `(series_id, dataset_version_id,
method_id, as_of, knowledge_at)`. Dimensionless, same-currency by
construction (3I.3 curves already exclude mixed-currency evidence), no
interpolation. Subject: `FUTURES_SERIES`. Manifest: the curve id plus the two
point ids used. This is the simplest, lowest-risk candidate — it adds no new
rigor requirement beyond what 3I.3 already enforces.

### 3.2 Futures curve slope — **recommend, but must be defined as a single formula shared with §3.4 carry, not a second one**

"Slope" and "annualized carry" are the same underlying calendar-spread
formula at two different normalizations: raw price-per-unit-time vs.
price-per-unit-time expressed as an annualized percentage. Recommending both
as independently-designed features would let two features silently diverge
on maturity-date handling or day-count. **3J.1 should define one shared
primitive** — "annualized normalized calendar-spread rate," parameterized by
an explicit day-count convention — and expose §3.1's raw spread and §3.4's
annualized carry as two presentations of it, not two derivations.

### 3.3 Futures three-point curvature — **recommend, with an explicit unequal-spacing formula requirement**

Futures contract expirations are not evenly spaced (e.g. quarterly vs.
monthly listings within the same series), so a naive second difference
`P3 - 2*P2 + P1` conflates curvature with spacing irregularity. This must use
the standard unequal-spacing three-point second-derivative estimator (weighted
by the actual `expiration_date` gaps from 3H.1, not point index), and must
only be computed when the curve has at least 3 surviving points after 3I.3's
own selection policy — i.e., it is computed *conditionally*, never padded or
approximated when only 2 points survive. Subject: `FUTURES_SERIES`.

### 3.4 Futures annualized carry with explicit day-count convention — **recommend; highest-priority candidate**

This is the one candidate 3I.3 explicitly named as deferred and the direct
reason 3J.0 exists (a series-level artifact needing a series-level subject).
Uses the two nearest curve points' frozen settlement prices and their
contracts' real `expiration_date` (from 3H.1, never symbol/ticker text) as
`T1`/`T2`, and an explicit day-count convention. 3I.3's method registry
*already reserves* a `carry_day_count_convention` field for exactly this —
3J.1 would be the first thing that actually reads it. Subject:
`FUTURES_SERIES`.

### 3.5 Futures open-interest level/change, unit-consistent only — **recommend, scoped to per-contract only**

Per-contract OI level and OI change (`OI[t] - OI[t-1]`) are straightforward:
`INSTRUMENT` subject, and the feature must refuse to diff two observations
whose `unit` or (for asset-denominated units) `unit_asset` disagree — never
convert. **Series-level aggregate OI (e.g. "front-month OI" or "total series
OI") is explicitly excluded from this proposal's recommended scope**: 3I.1 §9
already states no OI-based roll policy exists yet, so there is no reviewed
definition of which contract is "front month" for aggregation purposes, and
summing across contracts would need every summed contract to share a unit —
an aggregation policy that has not been reviewed. Per-contract OI needs no
such policy and is safe to recommend now; series-level OI should wait for the
roll-policy decision 3I.1 deferred.

### 3.6 Crypto mark-index basis — **recommend, with an exact-timestamp-match rule (no tolerance window in v1)**

`(mark_price - index_price) / index_price` for one crypto instrument. 3I.2
already guarantees both observations' price asset equals the instrument's own
quote asset, so the dimensional risk is smaller than the futures candidates.
The remaining risk is temporal: mark and index are independently published
and not guaranteed to share a timestamp. **Recommend exact `event_at` match
only for the first version** (a basis value is computed only when a mark and
an index observation share the identical instant); a declared tolerance
window, if ever added, should follow 3I.3's staleness-tolerance precedent
explicitly rather than being assumed. Subject: `INSTRUMENT`.

### 3.7 Crypto realized-funding annualized rate — **recommend**

Annualizes a realized funding rate using the funding interval already
recorded on the resolved 3H.2 convention (`periods_per_year = seconds_per_year
/ interval_seconds`). 3I.2 already computes "unknown cadence" fail-closed when
an interval is not a whole number of seconds (non-deterministic-cadence
conventions) — the feature must inherit that same fail-closed behavior rather
than guessing a periods-per-year figure. Subject: `INSTRUMENT`.

### 3.8 Crypto indicative-vs-realized funding delta, where temporally meaningful — **recommend, with an explicit "most recent prior estimate" selection rule**

Compares a realized funding rate against the most recent `INDICATIVE`
estimate for the *same* `target_funding_at`, published strictly before that
instant — never "any" indicative estimate, since 3I.2 preserves every
successive revision. Each side's own `knowledge_at`/`event_at` gates the PIT
read independently (this is a backward-looking accuracy feature computed
*after* the realized event is known, which is legitimate — the requirement is
that a decision-time read at `decision_at` must not see this feature before
both the qualifying indicative estimate and the realized settlement were
themselves individually knowable by `decision_at`). Subject: `INSTRUMENT`.

### 3.9 Crypto open-interest change, identical unit/unit_asset only — **recommend — unify with §3.5, not a separate feature**

Crypto and futures open interest already share one physical table, one kind,
and one payload shape (3I.2 §3). There is no reason for "futures OI change"
and "crypto OI change" to be two feature definitions with two formulas; they
should be **one** `INSTRUMENT`-subject OI-change feature definition, callable
for any instrument type, with the same unit/unit_asset-consistency guard as
§3.5. Recommending them separately would recreate the exact kind of
duplicate-authority problem 3I.2 avoided by renaming rather than forking the
OI table.

### Summary

| # | Candidate | Verdict |
|---|---|---|
| 3.1 | Futures front/back spread | Recommend |
| 3.2 | Futures curve slope | Recommend, merged with 3.4 as one shared formula |
| 3.3 | Futures 3-point curvature | Recommend, with unequal-spacing estimator |
| 3.4 | Futures annualized carry | Recommend — highest priority |
| 3.5 | Futures OI level/change | Recommend, per-contract (`INSTRUMENT`) only |
| 3.6 | Crypto mark-index basis | Recommend, exact-timestamp match only |
| 3.7 | Crypto realized-funding annualized rate | Recommend |
| 3.8 | Crypto indicative-vs-realized funding delta | Recommend, explicit prior-estimate selection rule |
| 3.9 | Crypto OI change | Recommend, unified with 3.5 as one definition |

### Implementation recommendation table

Six feature definitions (after merging 3.2 into 3.4 and 3.9 into 3.5) would
be registered if this proposal is approved:

| Feature | Subject type | Canonical input authority | Formula | Units | PIT semantics | Decision |
|---|---|---|---|---|---|---|
| Front/back normalized spread | `FUTURES_SERIES` | 3I.3 `futures_term_structure_curves`/`_points` (read-only) | `(P_back - P_front) / P_front` over the two nearest-expiration surviving curve points | Dimensionless | Inherits the curve's own `(as_of, knowledge_at)`; no independent PIT read | **Include** |
| Annualized calendar-spread rate (slope + carry, one primitive) | `FUTURES_SERIES` | 3I.3 curve points + 3H.1 `expiration_date` | `((P_back - P_front) / P_front) / ((T_back - T_front) / periods_per_year)` using the method's `carry_day_count_convention` | %/year (dimensionless × 1/time) | Same as above; day-count convention is part of the method's content hash | **Include** — highest priority |
| Three-point curvature | `FUTURES_SERIES` | 3I.3 curve points (≥3 surviving) | Unequal-spacing three-point second-derivative estimator weighted by real `expiration_date` gaps | Price / time² (normalize by `P_front` for a dimensionless form) | Computed only when ≥3 points survive 3I.3's own selection policy; absent otherwise, never approximated | **Include** |
| Per-instrument open-interest level/change | `INSTRUMENT` | 3I.1/3I.2 `open_interest_observations` (shared kind, both asset classes) | Level: raw value; Change: `OI[t] - OI[t-1]` | As stored (`CONTRACTS` / `BASE_ASSET` / `QUOTE_NOTIONAL`) — never converted; change refused if `unit`/`unit_asset` differ between the two reads | Each side of a change independently gated by its own `knowledge_at <= decision_at` | **Include** — per-contract only; series-level aggregate **deferred** (blocked on 3I.1's undecided roll policy) |
| Crypto mark-index basis | `INSTRUMENT` | 3I.2 `crypto_reference_price_observations` | `(mark_price - index_price) / index_price` | Dimensionless | Computed only when a `MARK_PRICE` and `INDEX_PRICE` row share the identical `event_at` (exact match, no tolerance window in v1) | **Include** |
| Crypto realized-funding annualized rate | `INSTRUMENT` | 3I.2 `crypto_funding_observations` (`FUNDING_RATE_REALIZED`) + 3H.2 funding convention | `rate * (seconds_per_year / interval_seconds)` | %/year | Uses the convention resolved at the funding instant (existing two-clock resolution); fails closed (no feature value) if the interval is not a whole number of seconds | **Include** |
| Crypto indicative-vs-realized funding delta | `INSTRUMENT` | 3I.2 `crypto_funding_observations` (both kinds) | `realized_rate - indicative_rate`, where `indicative_rate` is the most recent `FUNDING_RATE_INDICATIVE` for the same `target_funding_at` published strictly before it | Rate (same units as funding rate, dimensionless) | Each side's own `knowledge_at` independently gates when the delta itself becomes visible at a given `decision_at` | **Include** |

Series-level aggregate open interest, a mark-index tolerance window, and
crypto term structure are the three explicitly **deferred** items (§4), not
included in the six above.

No candidate is rejected outright; three (curve slope, futures OI, crypto OI)
are recommended only after collapsing an apparent duplicate into a single
rigorous definition rather than shipping two.

## 4. What this proposal explicitly excludes from 3J.1's scope

- **Series-level aggregate open interest** (front-month or total-series) —
  blocked on the roll-policy decision 3I.1 §9 deferred.
- **Any interpolation, extrapolation, or cross-unit conversion** anywhere —
  consistent with 3I.1/3I.2/3I.3's existing no-fallback rule.
- **Mark-index basis with a staleness/tolerance window** — v1 is exact-match
  only; a tolerance window is a separate, later decision.
- **Crypto term structure** — 3I.3 is futures-only by instruction; crypto has
  no settlement-price authority yet, so no crypto curve exists to build a
  crypto slope/carry/curvature feature on.
- **Options, quotes, trades, or L2** — no authority exists for any of these
  (3I.4/3I.5 remain planned-not-authorized).
- **Any ML-forecast or internally-modelled funding/price input** — every
  candidate above reads only already-authorized, provider/venue-published
  market-data or already-derived 3I.3 evidence.

## 5. What 3J.1 would need from the definition contract (not built here)

- `FeatureDefinitionVersion.required_dataset_types` already accepts free-form
  strings (no schema change): `FUTURES_TERM_STRUCTURE` (pointing at a 3I.3
  curve, not a raw `ObservationKind`), `SETTLEMENT_PRICE`, `OPEN_INTEREST`,
  `FUNDING_RATE_REALIZED`, `FUNDING_RATE_INDICATIVE`, `MARK_PRICE`,
  `INDEX_PRICE`.
- `FeatureFamily` (a closed `StrEnum`) has no derivatives-shaped member.
  3J.1 implementation would need to add at least one — a plausible name is
  `DERIVATIVES_TERM_STRUCTURE` covering §3.1–3.4, with open-interest and
  funding features possibly warranting their own member(s) (e.g.
  `OPEN_INTEREST`, `FUNDING`) rather than overloading one label across
  dimensionally unrelated feature shapes. This is a design question for the
  implementation PR, not resolved here, per the instruction not to broaden
  `FeatureFamily` speculatively ahead of need.
- Each feature's `source_observation_manifest` needs a per-family convention:
  curve-based features cite the curve id and the specific point ids used;
  direct-observation features (OI, funding, mark/index) cite the raw
  normalized observation ids read. This should be designed once, in the
  implementation PR, and applied consistently across all nine.

## 6. Open questions for the owner before any implementation PR

1. Confirm the `FeatureFamily` addition(s) named above (or an alternative
   naming scheme) before implementation, since it is a closed enum and a
   later rename would be a breaking change to stored definitions.
2. Confirm per-contract-only OI scope (§3.5/§3.9) is acceptable, with
   series-level aggregate OI explicitly deferred pending the 3I.1 roll-policy
   decision.
3. Confirm exact-timestamp-match-only is acceptable for mark-index basis v1
   (§3.6), with a tolerance window deferred.
4. Confirm the shared-formula merge for curve slope/carry (§3.2/§3.4) and for
   futures/crypto OI change (§3.5/§3.9) rather than building four feature
   definitions where two suffice.

## 7. No authority granted by this document

This is analysis and a recommendation only. No feature, migration, or API
described here exists in the codebase. No strategy, signal, opportunity,
order, or risk authority is implied. 3J.1 does not begin until the owner
approves a scope from this document and a separate implementation PR is
opened, reviewed, and merged following the same branch → PR → CI → merge →
exact-main verification discipline as every prior module.
