# Module 3J.2b.2b (Proposal): Professional Validation Semantics for OPEN→OPEN Basis Research

Status: **proposal only — not implemented, not authorized, not started.** No
code, migration, schema, strategy, signal, parameter, or test in this
repository changes as a result of this document. This is
documentation/architecture analysis only. No implementation PR is opened by
this document, and none is authorized by it.

This proposal is written against exact current main
`779f25b5ca5bb8bf6a9c65c1a9a6113478e76372` (local and `origin/main` verified
identical, working tree clean at the time of writing). It assumes:

- **3J.2b.1** (Signed Research Exposure + Dataset-Bound Tradable Price
  Evidence) is merged and exact-main verified — `src/trade_platform/signed_research_exposure_v2.py`,
  `src/trade_platform/signed_price_return_v2.py`, `src/trade_platform/tradable_research_evidence_v2.py`
  (verify `34501560545` / CodeQL `34501560499` on merge commit `7750e1f526d9ec91c16d6534813cacba31472590`).
- **3J.2b.2a** (Pure Basis Mean-Reversion Strategy Core + Deterministic Trade
  Ledger) is merged and exact-main verified — `src/trade_platform/crypto_basis_mean_reversion_v1.py`,
  introducing `CryptoBasisMeanReversionDefinitionV1`, `BasisMeanReversionDecisionV1`,
  `BasisMeanReversionTradeV1`, `BasisMeanReversionResearchRunV1`. Implementation merge
  `94bddf258baff3a024afc91bb3ee8475cc944fdd`; traceability merge / current main
  `779f25b5ca5bb8bf6a9c65c1a9a6113478e76372`; exact-main verify `34718849885`;
  exact-main CodeQL `34718849893`.
- **3I.4** (top-of-book quotes) remains **not authorized**. This is unchanged
  by this proposal and drives §11, §18, and §19 below: no execution-realism or
  capacity claim can be promoted beyond `RESEARCH_ONLY` while 3I.4 does not
  exist.

`BasisMeanReversionResearchRunV1` deliberately exposes only raw decision/trade
counts and an ordered `trade_returns` tuple — its own docstring states it
"does not compute or claim Sharpe, Sortino, Calmar, PBO, DSR, validated OOS
alpha, capacity, execution realism, or paper eligibility." This document is
the professional-validation architecture that fills that gap, without
touching the approved 3J.2b.2a semantics:

```
PIT feature event → decision_at → strictly later OPEN → fixed-N-bar OPEN exit
  → SignedOpenToOpenReturnV2 → deterministic trade ledger
  → [THIS PROPOSAL] → OOS/robustness statistical evidence → scorecard (BLOCKED)
```

## 1. Objective

Validation must answer one question: **does
`CryptoBasisMeanReversionResearchRunV1` (implemented as
`BasisMeanReversionResearchRunV1`) show robust out-of-sample statistical
edge, or is the apparent performance explainable by chance, overfitting,
costs, timing, or fixture construction?**

The result of asking that question — however strong the apparent evidence —
remains `RESEARCH_ONLY` (§18). Nothing in this proposal grants a strategy,
signal, opportunity, paper, shadow, or live-trading authority, and nothing in
it makes an alpha/performance claim on behalf of the strategy. It designs the
architecture that would let a later, separately reviewed implementation PR
produce that evidence honestly, including reporting `UNAVAILABLE` where the
sample size or methodology genuinely cannot support a claim.

## 2. Audit method

Every validation primitive listed below was read in its actual current
implementation (not inferred from its name or docstring). The audit
distinguishes what a function's signature *accepts* (e.g., "any `Decimal`
sequence") from what its *internal accounting semantics assume* (e.g.,
"assumes bar `t`'s return is `close[t]/close[t-1]-1`, i.e., close-to-close").
An API is not classified `REUSE_UNCHANGED` merely because it type-checks
against `Decimal` — its temporal/accounting semantics must independently
match OPEN→OPEN, non-overlapping, event-driven trade research.

Classification vocabulary used throughout (§23 decision matrix):

- `REUSE_UNCHANGED` — semantics already match; call with OPEN→OPEN inputs, no code change.
- `REUSE_WITH_EXPLICIT_INPUT_CONTRACT` — semantics are generic/parametric; safe to reuse only if the caller supplies the correct OPEN→OPEN-shaped inputs and parameters explicitly (e.g. `periods_per_year=365`), and that contract is documented at the call site.
- `REQUIRES_OPEN_TO_OPEN_ADAPTER` — the function's internal accounting is hard-wired to close-to-close (`run_vectorized_backtest`) or to another incompatible representation; a new adapter is needed that reuses the same evidence/decisions but recomputes returns under OPEN→OPEN accounting.
- `REQUIRES_METHODOLOGY_CORRECTION` — the function computes a real statistic, but its current formula is not the canonical methodology its field name implies (PBO, DSR) or embeds an assumption (fixed `sqrt(252)`) that would silently mislabel crypto OPEN→OPEN evidence as something more rigorous than it is.
- `DEFER_UNTIL_3I4` — cannot be validated at all without real spread/fill/order-book authority that does not exist yet; must render as `BLOCKED`, never as a passing or estimated result.

## 3. Known incompatibilities (must be acknowledged before any reuse)

### 3.1 Close-to-close dependency

`run_vectorized_backtest()` (`src/trade_platform/research.py:348-369`) has a
load-bearing, hard-coded accounting contract: `gross = position *
(closes[index] / closes[index-1] - 1)`, i.e. **every unit of return it
produces is a close-to-close bar return with a T-1 signal applied to the T-1→T
close leg**, and its own docstring says exactly that ("Signal at t is
deliberately applied to t+1 return; it cannot trade on unknown close-to-close
data"). Its Sharpe uses a hard-coded `sqrt(252)`.

The following existing evidence functions call `run_vectorized_backtest()`
internally and therefore inherit its close-to-close contract:

| Function | Calls `run_vectorized_backtest()` | File:line |
|---|---|---|
| `run_purged_walk_forward` | yes, per test fold | `research_validation.py:44` |
| `evaluate_capacity` | yes, per capital level | `quant_validation.py:168` |
| `evaluate_slippage_sensitivity` | yes, per cost scenario | `quant_validation.py:227` |
| `evaluate_latency_sensitivity` | yes (bar-shift model), plus a second independent `EventDrivenBacktester` fill-count model | `quant_validation.py:306`, `280-295` |
| `evaluate_stress` | yes, per scenario | `quant_validation.py:431` |
| `run_golden_vector_event_reconciliation` / `run_realistic_golden_vector_event_reconciliation` (Trend V2 golden engine) | yes | `cross_engine.py:191`, `292` |

`BasisMeanReversionTradeV1.net_return` is produced by
`compute_signed_open_to_open_return()` / `SignedOpenToOpenReturnV2`
(`signed_price_return_v2.py:86-91`): `gross_return = exposure *
(exit_open/entry_open - 1)`, entry and exit costs charged once each at trade
boundaries, non-overlapping discrete trades — an entirely different return
representation from a dense per-bar close-to-close series. **None of the six
functions above can be pointed directly at
`BasisMeanReversionResearchRunV1.trade_returns` or at any close series derived
from it** without an adapter that recomputes the return arithmetic using
`SignedOpenToOpenReturnV2` semantics instead of `run_vectorized_backtest()`.
This is not a parameter-passing issue; it is an incompatible accounting
engine.

### 3.2 Bootstrap annualization

`evaluate_bootstrap()` (`quant_validation.py:345-363`) takes no
`periods_per_year` parameter at all. Its Sharpe-per-resample line hard-codes
`sqrt(252)` (`quant_validation.py:357`). Feeding it a UTC-daily crypto return
series today would silently misreport the annualized Sharpe distribution as
if it were a 252-trading-day equity/FX series, understating the true
annualized Sharpe/volatility by a factor of `sqrt(365/252) ≈ 1.204`. This must
not be presented as correct for OPEN→OPEN crypto research without an explicit
sampling-frequency contract (§12).

### 3.3 Existing PBO naming

`backtest_overfitting_probability` (`quant_validation.py:514`) is computed as:

```python
pbo = Decimal(sum(not item for item in oos_results)) / Decimal(len(oos_results))
```

`oos_results: tuple[bool, ...]` is a caller-supplied pass/fail vector — in the
one existing caller (`trend_research_v2.py:598,614`) it is
`tuple(item.result.total_return > -1 for item in walk_forward)`, i.e. "did
this fold not lose more than 100%," which is almost always `True` on any
non-degenerate fixture. **This is literally a failure-rate over a boolean
vector, not the canonical Combinatorially Symmetric Cross-Validation (CSCV)
Probability of Backtest Overfitting** (Bailey, Borwein, López de Prado &
Zhu). Canonical PBO requires a strategy/parameter-trial return matrix,
combinatorially symmetric train/test partitions, in-sample-winner selection
per partition, the corresponding out-of-sample rank of that winner, and a
logit transform of the fraction of partitions where the IS winner ranks below
the OOS median. None of that machinery exists in this function. **3J.2b.2b
must not call this quantity "PBO."**

### 3.4 Existing DSR diagnostic

`deflated_sharpe_probability` (`quant_validation.py:508-513`) is:

```python
observations = max(2, len(oos_results))
probabilistic = Φ(observed_sharpe * sqrt(observations - 1))
trial_count = max(1, len(p_values) * parameter_combinations_tested * feature_combinations_tested)
expected_max = sqrt(2 * ln(trial_count))   # if trial_count > 1 else 0
deflated = Φ((observed_sharpe - expected_max) * sqrt(observations - 1))
```

This is **not** the canonical Bailey & López de Prado Deflated Sharpe Ratio.
Canonical DSR requires the return series' skewness and excess kurtosis, the
variance of Sharpe ratios across the actual trial set, and an
expected-maximum-Sharpe-under-the-null term built from the Euler-Mascheroni
constant and the standard-normal inverse CDF evaluated at `1 - 1/N` and
`1 - 1/(N·e)`. None of skewness, kurtosis, or a per-trial Sharpe-variance term
appears anywhere in this function. What is implemented is a simplified
Gaussian diagnostic using the classical asymptotic bound for the expected
maximum of `trial_count` i.i.d. standard-normal draws — cruder than DSR's
actual order-statistics expectation — plugged into the same
probabilistic-Sharpe z-score formula used two lines above for
`probabilistic_sharpe`. The code's own comment already calls this "an
explicitly conservative diagnostic, not a magic correction." **3J.2b.2b must
not relabel this heuristic as canonical DSR**, and must not present a "DSR
passed" claim derived from it.

## 4. OWNER DIRECTION — dual return-series model

Two return representations are required because they answer different
questions; neither can silently stand in for the other (§18).

### 4.A Canonical trade-return series

The exact ordered `BasisMeanReversionTradeV1.net_return` sequence for
executed trades only — precisely `BasisMeanReversionResearchRunV1.trade_returns`
(`crypto_basis_mean_reversion_v1.py:222-223`), already exposed unchanged.
`FLAT`, `IGNORED_ACTIVE_TRADE`, `EXCLUDED_MISSING_ENTRY`, and
`EXCLUDED_MISSING_EXIT` decisions contribute **no entry** to this series —
they are not zero-return trades, and inserting a synthetic zero for them
would understate variance and bias hit-rate/payoff statistics. This series is
the correct basis for:

- trade-order Monte Carlo (`evaluate_monte_carlo_trade_sequence`, §13);
- hit rate, payoff ratio, profit factor, win/loss distribution (`performance_metrics` trade-level fields, §18);
- trade-level tail diagnostics (`tail_risk_metrics`, §18);
- synthetic-control comparison where the control needs to preserve exact trade count and timestamp set (§14).

### 4.B Time-normalized realized-equity series — `REALIZED_EXIT_DAILY_RETURN_SERIES_V1`

For annualized/OOS metrics (Sharpe, Sortino, annualized return/volatility,
realized daily max drawdown), propose a deterministic UTC-calendar-day
compounded series, named explicitly `REALIZED_EXIT_DAILY_RETURN_SERIES_V1` so
it is never confused with a mark-to-market equity curve:

For each UTC calendar day `d` between the evaluation window's start and end
(inclusive, explicit zero-return days included so the series has one entry
per calendar day with no gaps):

```
trades_on(d)   = executed trades whose canonical exit_time falls on UTC day d
daily_return(d) = product(1 + trade.net_return for trade in trades_on(d)) - 1     # if trades_on(d) non-empty
daily_return(d) = 0                                                              # otherwise
```

Annualization: `periods_per_year = 365` (not 252) — the instrument is a
crypto perpetual and the accounting grid is UTC calendar days, not exchange
trading days. This is the input fed to `performance_metrics(...,
periods_per_year=365)` (§18), to `evaluate_bootstrap` (§12), and to any
walk-forward fold-level Sharpe computation.

**Explicit limitations, recorded prominently wherever this series is used**:
it is not mark-to-market — a trade opened on day `d1` and closed on day `d2`
contributes its entire compounded return to day `d2` only, with zero
visibility into equity between `d1` and `d2`; it therefore cannot support any
intratrade drawdown claim, and "realized daily max drawdown" computed from it
measures drawdown of the exit-day-attributed equity curve, not of true
mark-to-market equity. If `holding_horizon_bars` is large relative to trade
frequency, this series can materially understate real intratrade risk. This
limitation must be copied verbatim into the scorecard (§18) and the
validation package (§19), not just this document.

### 4.C Alternative considered and rejected for v1

*Mark-to-market daily revaluation* (marking open positions to the day's
close/open price and attributing a partial daily P&L to every day a position
is held) would remove the "no intratrade drawdown visibility" limitation, but
it requires choosing a mark price for open exposure, which is exactly the
kind of unapproved intermediate-price/execution claim §10 (Section 9,
"Trend/close" avoidance) and 3J.2b.2a's Architecture 1 were built to avoid —
it would silently reintroduce a close-to-close-shaped assumption into an
explicitly OPEN→OPEN research design. **Rejected for v1**; flagged
`REQUIRES REVIEW` (§24) as a possible v2 extension once 3I.4 exists and a
genuine intratrade mark price authority can be defined.

## 5. Walk-forward: chronological wall-clock folds

`purged_walk_forward_splits` (`strategy_validation.py:132-152`) is a pure
integer-index generator: given a `length` and `train_size` /
`validation_size` / `test_size` / `step` / `purge` / `embargo`, it returns
`PurgedWalkForwardSplit` objects with strictly ordered index ranges. It does
not itself read timestamps, closes, or feature events — its `purge` and
`embargo` are already plain index-unit gaps, which is compatible with the
requirement below *provided the index it operates over is chosen correctly*.
`run_purged_walk_forward` (`research_validation.py:24-48`), by contrast, is
close-to-close (§3.1) and cannot be reused as-is.

**Proposed index**: the integer `length` passed to
`purged_walk_forward_splits` must be the position of each bar in the
authoritative `1m` tradable-bar timeline for the dataset/instrument (the same
`bar_series` `BasisMeanReversionResearchRunV1` decisions resolve entries and
exits against), **not** a count of `crypto_mark_index_basis` feature events.
Feature-event counts are irregular (materializations do not arrive on a fixed
grid); splitting by event count would let fold boundaries land at
economically arbitrary wall-clock times and would make purge/embargo widths
mean different real-world durations across folds.

Each `BasisMeanReversionDecisionV1` enters a fold by the **1m-bar index
corresponding to its canonical `decision_at`** (the bar index of the first
`1m` bar at or after `decision_at`, found via the same tradable-bar lookup
3J.2b.1/3J.2b.2a already uses for `first_eligible_bar_after`). Required
ordering per fold: `TRAIN → VALIDATION → TEST`, strictly increasing bar
index, no fold's TEST segment ever preceding another fold's TRAIN segment in
wall-clock time (a plain forward walk, not k-fold shuffling). Parameters
fixed for a TEST fold must have been selected using only TRAIN/VALIDATION
bar-index ranges strictly before that TEST fold's `test_start` — no
parameter, threshold, or cost-model choice may be informed by any bar at or
after `test_start`.

### 5.1 Purge

A decision's economic exposure window is `entry_open_at → exit_open_at`
(`entry.bar_open_at` through `entry.bar_open_at + holding_horizon_bars * 1m`
per 3J.2b.2a's exit rule). Any decision whose exposure window **crosses** a
fold boundary — i.e. `entry_open_at` falls in TRAIN/VALIDATION but
`exit_open_at` falls at or after `validation_end`/`test_start` (or
symmetrically for a TEST-fold decision whose entry precedes `test_start`) —
must be purged: excluded from both sides of that boundary, not just narrowed
to whichever side its `decision_at` happens to sit in. Because the maximum
possible exposure width for any decision is exactly `holding_horizon_bars`
(one of the three preregistered parameters, §6), the purge width in bar-units
is `purge_bars = holding_horizon_bars` — the same width for every fold,
computed once and passed as `purge=holding_horizon_bars` to
`purged_walk_forward_splits`, so no train/validation trade's exposure window
can reach into a later test window by construction.

### 5.2 Embargo

Embargo is the additional bar-unit gap after a TEST segment (or between
VALIDATION and TEST) reserved so that a fold's residual serial dependence in
`crypto_mark_index_basis` itself (not just trade exposure) cannot leak
backward into the next fold's TRAIN. **Proposed v1 semantics**: a fixed,
preregistered `embargo_bars` constant, defined independently of any
particular fold's data, defaulting to `embargo_bars = holding_horizon_bars`
(the same width as purge) as the conservative v1 default — i.e. embargo is
also expressed and enforced purely in `1m`-bar units via
`purged_walk_forward_splits(..., embargo=embargo_bars)`, never as an
arbitrary count of feature events. This default is a v1 placeholder, not an
owner-approved constant; it is listed `REQUIRES REVIEW` (§24) because the
correct embargo width is a statistical question about `crypto_mark_index_basis`'s
autocorrelation structure, not something this docs-only proposal can
determine without data.

## 6. Untouched holdout

The three preregistered parameters are exactly `basis_entry_threshold`,
`holding_horizon_bars`, and `maximum_absolute_exposure`
(`CryptoBasisMeanReversionDefinitionV1`, `crypto_basis_mean_reversion_v1.py:100-102`)
— no fourth parameter exists in the definition and none may be introduced by
this proposal (scope exclusion, below).

Proposed mechanism: reserve the final chronological segment of the 1m
tradable-bar timeline (a fixed calendar cutoff, e.g. "final N% of bars by
wall-clock time" or "final calendar month," to be fixed by the owner before
any implementation begins, not derived post hoc from where performance looks
best) as the **holdout bar range**. Every methodology choice this document
covers — parameter-neighborhood evaluation (§7), cost/latency/stress scenario
design (§8-§10), null-control construction (§14), multiple-testing trial
accounting (§15) — must be frozen (parameters fixed, scenarios fixed, trial
count fixed) using only bar indices strictly before the holdout's start.
Concretely: `purged_walk_forward_splits`'s last generated fold's `test_end`
must be `<= holdout_start_bar_index`, and the holdout range itself is never
passed through `purged_walk_forward_splits` at all — it is evaluated exactly
once, after every other methodology decision in this document is locked, by a
separate, single, non-repeated invocation of the OPEN→OPEN return/scorecard
pipeline. Re-running the holdout evaluation more than once (e.g. "let's also
try holding_horizon_bars=48 and see how the holdout looks") would void its
status as an untouched holdout; the implementation must make repeat holdout
evaluation structurally inconvenient (e.g. requiring a new, separately
content-hashed and dated evidence artifact each time, so repeat runs are
visible in the evidence trail rather than silently overwritten).

## 7. Parameter stability — diagnostic, not optimization

`evaluate_parameter_stability()` (`quant_validation.py:458-474`) does not run
any backtest or search a grid itself — it takes a caller-precomputed
`results: tuple[ParameterResult, ...]` plus a `selected_parameters` tuple and
only scores stability around the already-chosen point: `neighbours` are grid
points differing from `selected` in exactly one parameter; `plateau` is
neighbours scoring `>= 90%` of the selected point's `total_return`; `score`
is the fraction of neighbours scoring `>= 75%`; `narrow_performance_spike =
plateau count <= 1 or score < 0.5`. **This function can be reused unchanged
once its `results` tuples are populated from correct OPEN→OPEN
`total_return`/Sharpe values** (computed via the adapter in §8, not via
`run_vectorized_backtest`) — classification `REUSE_WITH_EXPLICIT_INPUT_CONTRACT`,
not `REQUIRES_OPEN_TO_OPEN_ADAPTER`, because the function itself has no
close-to-close dependency; only its caller-supplied inputs need to be
OPEN→OPEN-correct.

Proposed v1 neighborhood, centered on the owner-approved baseline parameter
set (the exact numeric baseline is an owner/implementation decision, not
fabricated by this docs-only proposal): one perturbation step in each
direction for each of the three preregistered parameters
(`basis_entry_threshold`, `holding_horizon_bars`, `maximum_absolute_exposure`),
i.e. up to `3 × 2 = 6` neighbor runs plus the 1 baseline run = 7 total runs in
the v1 grid — small enough to preregister exhaustively and log in the
multiple-testing trial count (§15). Every neighbor run must reuse the exact
same TRAIN/VALIDATION bar range as the baseline (never the untouched
holdout). Neighboring runs are stability evidence only; if a neighbor
outperforms the baseline, that fact is recorded in the stability report and
must not replace the baseline — there is no code path in this proposal that
lets a stability run promote itself to "selected."

## 8. Cost/slippage sensitivity

`evaluate_slippage_sensitivity()` (`quant_validation.py:214-238`) reruns
`run_vectorized_backtest()` per cost-multiplier scenario over the same
`closes`/`signals` pair — incompatible with a discrete OPEN→OPEN trade
ledger (§3.1). It must not be called directly.

**Proposed OPEN→OPEN adapter** (new, e.g. `evaluate_open_to_open_cost_sensitivity`,
implementation deferred — this document specifies its contract only): reuse
the exact same `BasisMeanReversionDecisionV1` sequence's entry/exit
open-price evidence (`entry_open`, `exit_open`, `entry_time`, `exit_time` —
already on `BasisMeanReversionTradeV1`), and for each of a fixed scenario set
— **base (1x)**, **1.5x**, **2x**, **3x** transaction-cost assumptions —
recompute `net_return` per trade using `SignedOpenToOpenReturnV2`'s exact
formula with a scaled `CostModel`, without re-running strategy selection,
entry/exit resolution, or direction logic at all (those are frozen 3J.2b.2a
evidence). Entry cost (`cost_model.cost(entry_turnover)`) and exit cost
(`cost_model.cost(exit_turnover)`) remain separately observable per trade in
the adapter's output — never merged into one "round-trip cost" figure — so a
reviewer can see whether cost sensitivity is driven by the entry or exit leg.
No top-of-book claim and no real-spread claim: the scaled `CostModel` remains
exactly as synthetic/assumed as 3J.2b.2a's baseline cost model, just scaled.

## 9. Latency sensitivity

Latency must shift **execution timing**, never mutate a historical feature
value. Proposed v1 semantics, matching the pattern the owner specified:

```
delayed_decision_time = decision_at + latency
entry = bar_series.first_eligible_bar_after(delayed_decision_time)   # strictly after, same rule as 3J.2b.2a's undelayed entry
holding horizon starts counting from this delayed entry's bar_open_at
```

If no eligible bar exists strictly after `delayed_decision_time` within the
dataset, or the delayed holding horizon's exit bar does not exist, the
decision becomes `EXCLUDED` under the delayed scenario — never a
nearest-bar substitution and never a synthetic price, exactly mirroring
3J.2b.2a's own `EXCLUDED_MISSING_ENTRY`/`EXCLUDED_MISSING_EXIT` semantics.

**Proposed v1 latency levels**, chosen as whole multiples of the `1m`
research grid so `delayed_decision_time` always resolves deterministically
against the same bar series without sub-bar interpolation: **0 (baseline),
1 minute, 5 minutes, 15 minutes**. These are validation-methodology
parameters (how sensitive is the strategy to realistic decision-to-order
latency), not strategy parameters, so pinning them here does not violate the
"no parameter optimization" scope exclusion — but they are still listed
`REQUIRES REVIEW` (§24) since the owner may want different levels calibrated
to a real target venue's expected latency once that is known.

This reuses `evaluate_latency_sensitivity`'s existing `EventDrivenBacktester`
fill/miss counting *pattern* conceptually, but not the function itself
(`REQUIRES_OPEN_TO_OPEN_ADAPTER`): a new adapter must apply the delayed-entry
rule above per latency level, using 3J.2b.2a's own entry/exit resolution
functions, and recompute `net_return` via `SignedOpenToOpenReturnV2` exactly
as in §8 (with the baseline, non-scaled cost model, and latency held as the
only varying input).

## 10. Stress tests

`evaluate_stress()` mutates only the last close of a `closes` array and
reruns `run_vectorized_backtest()` (§3.1) — not reusable. Proposed OPEN→OPEN
stress categories, each an explicit, separately labeled **synthetic
validation evidence** category (never written back into Feature Authority,
never presented as if it were real market data):

1. **Transaction-cost deterioration** — reuse the §8 adapter directly (it already is a cost-multiplier scenario engine); no separate implementation needed.
2. **Adverse exit-price shock** — for each executed trade, recompute `net_return` with `exit_open` shifted by a fixed adverse percentage (e.g. -1%, -2% magnitude in the position's losing direction) before feeding it to `SignedOpenToOpenReturnV2`; entry price, entry time, and exit time are untouched — only the exit price input to the return formula is shocked. This directly stress-tests exit-price risk without inventing a new price series.
3. **Missing-bar / data-gap stress** — recompute the trade ledger under a synthetic ablation that marks a fixed fraction of otherwise-eligible exit bars as unavailable (forcing `EXCLUDED_MISSING_EXIT` for those trades) and reports the resulting change in trade count, hit rate, and `REALIZED_EXIT_DAILY_RETURN_SERIES_V1`-based Sharpe — tests sensitivity to the exact "no synthetic exit" discipline 3J.2b.2a already enforces.
4. **Reduced liquidity / volume assumptions** — reuse the §11 capacity adapter's `order_participation_limit`/`impact_coefficient` inputs at tightened (more conservative) values, rather than building a second liquidity model.

Every one of these four evidence categories must carry an explicit
`"synthetic_validation_evidence": true` marker (or equivalent field) in its
artifact, distinct from `BasisMeanReversionResearchRunV1`'s untouched
Feature-Authority-sourced decisions.

## 11. Capacity

`evaluate_capacity()` is already explicitly `OHLCV_ESTIMATE` /
`OHLCV-only capacity estimate; no order-book or queue-priority precision`
(`quant_validation.py:180-181`) and already avoids claiming order-book
precision — but it still calls `run_vectorized_backtest()` internally
(§3.1), so its close-to-close return computation cannot be reused as-is.
**Proposed bounded trade-ledger capacity adapter**: reuse
`evaluate_capacity`'s existing square-root market-impact formula (`impact =
impact_coefficient * sqrt(participation)`) and its existing
spread/spread-deterioration inputs, but instead of feeding an inflated
`CostModel` into `run_vectorized_backtest()`, feed it into the §8 OPEN→OPEN
cost-sensitivity adapter's per-trade `SignedOpenToOpenReturnV2` recomputation,
using **entry-bar and exit-bar volume** (rather than a single dataset-wide
`average_daily_volume`) as the participation denominator for each trade's
own entry and exit leg respectively. Inputs: capital level, absolute
exposure (bounded by `maximum_absolute_exposure`), assumed participation
limit, explicit impact coefficient — all identical in spirit to
`evaluate_capacity`'s existing parameters. Its status field must remain
literally `OHLCV_ESTIMATE_ONLY`. Because 3I.4 does not exist, this adapter's
output — however favorable — can never justify promoting capacity or
execution-realism evidence beyond `RESEARCH_ONLY` (§18-§19); it exists only
to bound how large the theoretical capacity claim could ever be, once real
order-book/fill authority exists.

## 12. Bootstrap

Comparing the two options the owner posed:

**Option A** — extend `evaluate_bootstrap(..., periods_per_year: int = 252)`
with a new keyword parameter defaulting to `252` (preserving every existing
Trend V2 caller's behavior byte-for-byte with zero call-site changes), and
pass `periods_per_year=365` explicitly from the new OPEN→OPEN caller using
`REALIZED_EXIT_DAILY_RETURN_SERIES_V1` (§4.B) as `period_returns`.

**Option B** — a wholly new `evaluate_open_to_open_bootstrap` artifact type,
duplicating the resampling loop with `365` hard-coded.

**Recommendation: Option A.** It is the smallest change that remains
semantically correct: `evaluate_bootstrap`'s resampling logic
(`rng.choice(period_returns)` with replacement, compounded into equity) has
no inherent close-to-close or feature-event dependency — its only defect is
the missing parameter, not its algorithm. Adding a defaulted keyword
preserves every existing Trend V2 call site's behavior identically (default
unchanged = `252`) while letting the OPEN→OPEN caller pass `365` explicitly
and self-documentingly. Option B would duplicate ~15 lines of identical
resampling logic for no semantic gain and would need its own separate
maintenance/testing burden. Classification: `REQUIRES_METHODOLOGY_CORRECTION`
(the current signature cannot express the correct sampling frequency at all)
resolved by the smallest-footprint fix (Option A), not a rewrite.

## 13. Monte Carlo

`evaluate_monte_carlo_trade_sequence()`
(`quant_validation.py:380-397`) confirmed from code: it accepts
`trade_returns: tuple[Decimal, ...]` directly as a parameter — no close
series, no signal array, no backtest re-run. Its mechanism is a
deterministic `random.Random(seed)` **permutation** (`rng.shuffle`) of the
supplied trade-return sequence (trade-order risk), not resampling with
replacement, compounded sequentially into an equity curve, with
`ruin_or_threshold_breach_probability` measured against a configurable
`ruin_threshold` (default `0.5`). This has no accounting dependency on
close-to-close bars at all — its only input is an ordered `Decimal` sequence.

**Confirmed: recommend reuse unchanged**, classification
`REUSE_UNCHANGED`, called with the exact `BasisMeanReversionResearchRunV1.trade_returns`
sequence (§4.A canonical trade-return series — not the daily series from
§4.B, since Monte Carlo trade-order risk is a trade-level question). The
existing deterministic `seed` and `simulations` count parameters are
preserved unchanged and must be pinned in the artifact identity exactly as
today, so a repeat run with the same seed/count reproduces bit-identical
evidence.

## 14. Synthetic no-edge control

Required before any edge claim (§1). **Proposed v1 mechanism**: deterministic
permutation of the *basis values* across the existing feature decision
timestamp set, using a fixed seed:

- Take the ordered sequence of observed `crypto_mark_index_basis` values across all feature decision events in the evaluation window (TRAIN+VALIDATION+TEST, never touching the untouched holdout until §6's freeze is complete).
- Apply a deterministic `random.Random(seed).shuffle(...)`-style permutation to the basis *values* only, holding the decision timestamp set, tradable-bar evidence, strategy parameters (the frozen baseline, not the §7 neighborhood), and cost model completely fixed.
- Re-run the unmodified 3J.2b.2a decision/trade-ledger logic against the permuted basis series to produce a null-control `BasisMeanReversionResearchRunV1`-shaped run.
- Repeat for a fixed, preregistered number of permutations (the same seed-and-count discipline as §13's Monte Carlo — the exact count is an implementation-time decision, `REQUIRES REVIEW`, but must be fixed before any null run is inspected, not grown post hoc if the first batch looks inconclusive).

This preserves the number of feature events, the feature-value distribution
(a permutation, not a resample-with-replacement, so the exact multiset of
observed basis values is unchanged), the original decision timestamp set, the
tradable-bar evidence, the strategy parameters, and the cost model — only the
pairing between timestamp and basis value is randomized. The permuted values
must never be written to Feature Authority and must never be wrapped in a
real `FeatureMaterializationV2` — they exist only inside the validation
adapter's in-memory null-run construction, and the resulting evidence
artifact must be explicitly labeled synthetic/null-control evidence.

**Pinned performance statistic and empirical p-value**: the statistic
compared between the observed untouched-OOS run and each null run is the
OOS-fold Sharpe computed from `REALIZED_EXIT_DAILY_RETURN_SERIES_V1` at
`periods_per_year=365` (§4.B/§12) — chosen because it is the same statistic
`evaluate_bootstrap` and the scorecard's daily-equity metrics already use, so
the null comparison is apples-to-apples with the headline scorecard figure,
not a bespoke metric invented only for this test. Empirical p-value:

```
p = (1 + count(null_sharpe >= observed_sharpe)) / (1 + number_of_null_runs)
```

using the standard `+1` smoothing so `p` is never reported as exactly zero
regardless of permutation count.

## 15. Multiple testing

`evaluate_multiple_testing`'s Benjamini-Hochberg step (`quant_validation.py:504-507`)
is a standard BH step-up procedure over any `dict[str, Decimal]` of p-values
— generically reusable (`REUSE_WITH_EXPLICIT_INPUT_CONTRACT`) once the trial
p-values are supplied correctly, though it is a private inline duplicate of
the already-parameterized `benjamini_hochberg(p_values, *, alpha=Decimal("0.05"))`
in `research_validation.py:73-84`; the OPEN→OPEN caller should call the
`research_validation.py` version directly rather than re-duplicating the
algorithm a third time.

Required trial-count tracking for this strategy's research program: number
of parameter combinations examined (§7's 7-point neighborhood, plus any
prior exploratory work the owner already did before 3J.2b.2a — that prior
count must be reconstructed and included, not reset to zero at this
module's boundary), strategy hypotheses examined (Hypothesis A — pure basis
mean reversion — is the only one implemented so far; any hypotheses
considered and rejected before implementation must still be counted),
datasets/universes examined (the single fixture dataset/instrument used
throughout 3J.2b.2a, explicitly counted as `1`, not omitted because it is
fixture-only), feature combinations examined (the single required
`crypto_mark_index_basis` feature — 3J.2b.2a's `validate()` gate already
proves no other feature was used), rejected experiments, and the one
selected experiment. **No trial may be dropped from the denominator because
it performed badly** — the rejected-experiment list is exactly as
load-bearing as the selected one for this accounting.

## 16. Canonical PBO

Per §3.3, the existing `backtest_overfitting_probability` must not be reused
under the name PBO. This proposal specifies the smallest correct CSCV
implementation for a later, separately reviewed implementation PR:

- **Input**: a strategy/parameter-trial return matrix — one row per trial (each of the §7 parameter-neighborhood points, at minimum; ideally also any earlier exploratory trials per §15), one column per time-partition, populated from `REALIZED_EXIT_DAILY_RETURN_SERIES_V1` (§4.B) per trial per partition.
- **Partitions**: split the TRAIN+VALIDATION bar range (never the holdout) into `S` equal contiguous time blocks; CSCV requires forming all `C(S, S/2)` combinatorially symmetric splits of those blocks into an in-sample (IS) half and an out-of-sample (OOS) half.
- **IS winner selection**: for each of the `C(S, S/2)` splits, select the trial with the best IS performance statistic (Sharpe, from the same daily series) among the IS blocks.
- **OOS rank**: find that same trial's percentile rank among all trials' OOS performance over the complementary OOS blocks.
- **Logit transform**: `λ_c = logit(rank_c)` for each split `c`; PBO is the fraction of splits where `λ_c <= 0` (i.e. the IS winner performs below the OOS median) — the standard CSCV PBO estimator.
- **Insufficient trials/sample**: canonical CSCV needs enough trials and enough time blocks for `C(S, S/2)` to be statistically meaningful (the original CSCV literature typically works with `S=16` blocks and dozens+ of trials). Given this strategy's v1 trial count (§7's ~7-point neighborhood) and a single fixture dataset, **the correct v1 result is very likely `UNAVAILABLE`, not a fabricated PBO number** — this proposal explicitly does not promise a numeric PBO will be producible at 3J.2b.2b.1 scale; if trial/sample counts are insufficient, the implementation must report `UNAVAILABLE` rather than compute PBO on too few trials/blocks to be meaningful.

## 17. Canonical DSR

Per §3.4, `deflated_sharpe_probability` is not canonical DSR. Proposal:
introduce a separate, newly and explicitly reviewed artifact,
`DeflatedSharpeEvidenceV1`, rather than silently changing
`MultipleTestingEvidence.deflated_sharpe_probability`'s established
historical semantics (which Trend V2 callers already depend on). Required
inputs for `DeflatedSharpeEvidenceV1`:

- observed Sharpe (from `REALIZED_EXIT_DAILY_RETURN_SERIES_V1`, §4.B);
- sample length (`len(REALIZED_EXIT_DAILY_RETURN_SERIES_V1)`, i.e. calendar days in the evaluation window, not trade count);
- number of trials (the same trial count tracked in §15/§16 — the canonical DSR's "N" is the same quantity CSCV PBO needs, so both should read from one shared trial-count accounting, not two divergent ones);
- return skewness and excess kurtosis, computed from `REALIZED_EXIT_DAILY_RETURN_SERIES_V1` (or from §4.A trade returns if the owner determines trade-level skew/kurtosis is the more appropriate input — `REQUIRES REVIEW`, §24);
- expected maximum Sharpe / selection-bias adjustment, computed via the canonical order-statistics expectation over `N` trials (not the `sqrt(2 ln N)` asymptotic bound the current heuristic uses).

**Insufficient observations → `UNAVAILABLE`.** No "DSR passed" claim may be
derived from the current simplified diagnostic; if `DeflatedSharpeEvidenceV1`
cannot be computed (too few daily observations, too few trials for a
meaningful selection-bias term), it must report `UNAVAILABLE`, and the
scorecard (§18) must surface that `UNAVAILABLE` state rather than silently
omitting the metric.

## 18. Scorecard semantics

`StrategyScorecardV2` (`strategy_scorecard_v2.py:85-145`) already structurally
supports exactly the split this proposal requires: `MetricObservation` has a
`family` enum (`PERFORMANCE/ROBUSTNESS/EXECUTION/RISK/DATA_QUALITY/SIGNAL_DECAY`)
and a `state` enum (`MEASURED/ASSUMED/UNAVAILABLE`), and `status` is itself
constrained to only `BLOCKED`/`REVIEW_REQUIRED` — **there is no
"PASSED"/"APPROVED" state in the schema at all**, which already structurally
prevents this module from ever emitting an approval claim.

**Daily realized-equity metrics** (from `REALIZED_EXIT_DAILY_RETURN_SERIES_V1`,
`periods_per_year=365`, via `performance_metrics(...)` — which already
accepts `periods_per_year` as a keyword, so this call is
`REUSE_WITH_EXPLICIT_INPUT_CONTRACT`, not an adapter): total return,
annualized return, annualized volatility, Sharpe, Sortino, realized daily max
drawdown (§4.B's max-drawdown caveat applies verbatim).

**Trade-level metrics** (from §4.A's canonical trade-return series, via the
same `performance_metrics(...)` call's trade-oriented fields plus
`tail_risk_metrics(...)`, both `REUSE_WITH_EXPLICIT_INPUT_CONTRACT`): hit
rate, average trade, median trade, payoff ratio, profit factor, number of
trades, trade-level tail distribution (VaR/CVaR via `tail_risk_metrics`).

These two metric families must be tagged distinctly (e.g. via
`MetricObservation.dimensions`) so no downstream reader can conflate a daily
annualized Sharpe with a trade-level payoff ratio as if they were computed
from the same series.

**Scorecard limitations (must appear in `StrategyScorecardV2.limitations`,
verbatim, every run)**: fixture-only evidence (no real crypto venue or
provider contacted); no mark-to-market intratrade drawdown (§4.B); no real
spread/order book (3I.4 not authorized); no broker fill evidence; no funding
accounting (3J.2b.2a's Architecture 1 excludes funding in full).

**Scorecard status: `BLOCKED`, unconditionally, in 3J.2b.2b**, regardless of
how strong any synthetic result looks, because input market data remains
fixture-only, 3I.4 top-of-book is absent, and no real execution evidence
exists. `StrategyScorecardV2.validate()` already enforces `if
dataset_health_status == "BLOCKED" then status must also be BLOCKED`
(`strategy_scorecard_v2.py:137-138`) — this proposal's implementation must
ensure `dataset_health_status` for any 3J.2b.2b run is set to `"BLOCKED"` so
this existing invariant does the enforcement, rather than relying on a
separate, possibly-forgettable status assignment. Strong synthetic results
can justify further research only — never a promotion, paper-trading
authorization, or alpha claim.

## 19. Validation package

`build_validation_package()` (`quant_validation.py:706-806`,
`REQUIRED_EVIDENCE` at line 563) requires exactly the 13 categories named in
this proposal's brief: `data_quality, oos_walk_forward, golden_reconciliation,
execution_realism, capacity, slippage, latency, bootstrap, monte_carlo,
stress, parameter_stability, multiple_testing, scorecard`. Audited behavior:
it only checks that all 13 keys are **present** in the caller-supplied
`evidence_ids`/`evidence_hashes` maps and that each hash is a well-formed
64-hex-char string — **it does not itself inspect any individual evidence
artifact's `passed`/`status` field**, and `promotion_status` is unconditionally
hardcoded to the literal string `"REVIEW_REQUIRED_OR_BLOCKED"` regardless of
evidence content. This is an existing, pre-3J.2b.2b limitation of
`build_validation_package`, not something this module can silently fix by
reuse alone — classification `REUSE_WITH_EXPLICIT_INPUT_CONTRACT`: it can be
reused unchanged as a completeness+integrity aggregator, but the caller (the
new OPEN→OPEN orchestration layer, §22) is responsible for ensuring that any
category which cannot genuinely pass is represented by an explicit
deterministic failed/blocked evidence artifact and hash — never a
fabricated-success placeholder just to satisfy the completeness gate.

In particular: `execution_realism` must remain failed/blocked
(`DEFER_UNTIL_3I4`) until real spread/fill authority exists — there is no
adapter that can make this category pass honestly today. `golden_reconciliation`
must use the new independent OPEN→OPEN reconciliation path (§20), never the
existing Trend close-to-close golden engine, whose semantics do not match
(§3.1). The package as a whole may exist and be internally consistent while
the overall research state remains `BLOCKED` (§18) — completeness of the
evidence set is not the same claim as fitness for promotion.

## 20. Independent reconciliation

3J.2b.2a's OPEN→OPEN accounting (`compute_signed_open_to_open_return`) needs
an independent reconciliation before any robustness claim is trusted — using
the exact same calculator twice and calling that "independent" would not
actually detect a shared bug. `cross_engine.py`'s existing golden-vector
machinery (`run_golden_vector_event_reconciliation`,
`run_realistic_golden_vector_event_reconciliation`) is built entirely around
comparing `run_vectorized_backtest()`'s close-to-close equity/turnover
against an independent event-driven engine — its semantics do not genuinely
match OPEN→OPEN, non-overlapping discrete-trade accounting, so it must not be
invoked here (`REQUIRES_OPEN_TO_OPEN_ADAPTER`, in the sense that a
conceptually new, minimal engine is needed, not that the existing one can be
parametrized into fitting).

**Proposed second minimal arithmetic path** (new, e.g.
`reconcile_open_to_open_trade_ledger`): independently recompute, from the
same canonical decision/bar evidence (the same `entry_open`, `exit_open`,
`entry_time`, `exit_time`, `exposure`, and `CostModel` already on each
`BasisMeanReversionDecisionV1`/`BasisMeanReversionTradeV1`), each of:
**direction** (sign of exposure vs. the definition's threshold rule),
**entry** (bar resolved independently via the same `first_eligible_bar_after`
contract, checked for equality against the trade's own `entry_time`/`entry_open`),
**exit** (bar at `entry_time + holding_horizon_bars * 1m`, checked for
equality), **gross return** (`exposure * (exit_open/entry_open - 1)`,
computed by a second, textually separate implementation of that one-line
formula — not a call back into `SignedOpenToOpenReturnV2`), **costs**
(`cost_model.cost(abs(exposure))` computed twice, entry and exit, by the
independent path), and **net return** (gross minus both costs, independently
summed). Any unexplained difference between the independent recomputation
and the canonical trade ledger → validation `BLOCKED` for the entire package
(§19), not just a flagged warning on that one trade.

## 21. Persistence

**No migration and no new table proposed for the first implementation.**
Existing immutable stores are examined for fit:

- `strategy_scorecards` / `scorecard_metric_observations` / `scorecard_components` / `scorecard_data_health_assessments` / `scorecard_validation_packages` (migration `20260816_0015_strategy_scorecard_v2.py`) already model exactly the `StrategyScorecardV2` shape this proposal reuses (§18) — no new fields are needed since `MetricObservation.dimensions` (a free-form field) is sufficient to tag daily-vs-trade-level metric family without a schema change.
- `validation_packages` / `validation_package_artifacts` (migration `20260815_0003_validation_package_manifest.py`) already model `build_validation_package`'s 13-category manifest shape (§19) with immutability triggers and content-hash verification already in place — the new OPEN→OPEN evidence categories (§8-§17) are new *evidence_id/evidence_hash entries* inside that existing manifest shape, not new columns or tables.
- New evidence dataclasses this proposal introduces (the §8 cost-sensitivity adapter, §9 latency adapter, §11 capacity adapter, §14 null-control artifact, §16 `PbOEvidence`-shaped artifact, §17 `DeflatedSharpeEvidenceV1`, §20 reconciliation artifact) are all in-memory, content-hashed dataclasses in the same style as every existing `quant_validation.py` evidence type — they do not require durable persistence any more than `SlippageSensitivityEvidence` or `CapacityLevel` currently do, since they flow into the existing `validation_packages` manifest by hash reference, exactly like 3J.2b.2a's `BasisMeanReversionResearchRunV1` itself has no dedicated table.

If a later implementation discovers a genuine schema gap (e.g. a need to
durably store every null-control permutation run rather than just its
summary statistic), that would require a new migration and is explicitly
**not approved by this document** — any such migration proposal must stop
and present, separately: why the existing stores are insufficient, the exact
proposed schema, immutability/uniqueness/content-hash-identity design, and
restore implications, for a dedicated owner review before being written.

## 22. Proposed implementation decomposition

**Recommend two steps, not one bounded module**, so validation semantics and
orchestration are not reviewed simultaneously — a mistake in an OOS-split
purge/embargo rule or in the PBO/DSR formulas (§5, §16, §17) is a much higher-
stakes review item than how the results are assembled into a package, and
conflating them risks the orchestration layer's completeness getting
"reviewed by association" alongside genuinely novel statistical methodology.

- **3J.2b.2b.1 — OPEN→OPEN validation adapters + canonical statistical evidence.** The §8 cost-sensitivity adapter, §9 latency adapter, §10 stress adapters, §11 capacity adapter, §12 bootstrap extension (Option A), §13 Monte Carlo reuse, §14 null-control construction, §16 CSCV PBO, §17 `DeflatedSharpeEvidenceV1`, §20 independent reconciliation. No orchestration, no scorecard assembly, no `build_validation_package` wiring yet — each adapter/evidence type is implemented and unit-tested in isolation against 3J.2b.2a's existing trade-ledger evidence.
- **3J.2b.2b.2 — orchestration + scorecard + validation package.** The §5/§6 walk-forward/holdout orchestration that calls 3J.2b.2b.1's adapters per fold, the §7 parameter-neighborhood runner, the §15 multiple-testing trial accounting, the §18 scorecard assembly (daily vs. trade-level metric mapping, forced `BLOCKED` status), and the §19 `build_validation_package` wiring (including the explicit failed/blocked `execution_realism` entry).

## 23. Decision matrix

| # | Function/evidence | Reuse classification | Reason | Required adapter/change | Return series used | Annualization | OOS split unit | Purge rule | Embargo rule | Holdout policy | Null-control method | Empirical p-value method | PBO method | DSR method | Scorecard mapping | BLOCKED/REVIEW semantics | 3I.4 dependency | Decomposition step |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `run_purged_walk_forward` | REQUIRES_OPEN_TO_OPEN_ADAPTER | calls `run_vectorized_backtest()` (close-to-close) | new orchestration reusing `purged_walk_forward_splits` index generator + §8-style per-trade recompute | §4.B daily series per fold | 365 | 1m tradable-bar index (§5) | `purge=holding_horizon_bars` (§5.1) | `embargo=holding_horizon_bars` v1 default, REQUIRES REVIEW (§5.2) | see row "holdout policy" col | n/a | n/a | n/a | n/a | n/a | fold-level BLOCKED on purge violation | none | .2b.2b.2 |
| 2 | `purged_walk_forward_splits` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | pure integer-index generator, no accounting semantics | none — call with 1m-bar index length, not event count | n/a | n/a | 1m tradable-bar index | as above | as above | n/a | n/a | n/a | n/a | n/a | n/a | n/a | none | .2b.2b.2 |
| 3 | `evaluate_capacity` | REQUIRES_OPEN_TO_OPEN_ADAPTER | close-to-close via `run_vectorized_backtest()`; single dataset-wide ADV, not per-trade volume | new bounded trade-ledger capacity adapter (§11), status forced `OHLCV_ESTIMATE_ONLY` | §4.A trade returns, cost-adjusted | n/a | n/a | n/a | n/a | never applied to holdout post-freeze | n/a | n/a | n/a | n/a | EXECUTION family, capped RESEARCH_ONLY | can never exceed RESEARCH_ONLY | yes, permanently | .2b.2b.1 |
| 4 | `evaluate_slippage_sensitivity` | REQUIRES_OPEN_TO_OPEN_ADAPTER | close-to-close via `run_vectorized_backtest()` | §8 cost-sensitivity adapter (base/1.5x/2x/3x) | §4.A trade returns, cost-adjusted | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | EXECUTION family | evidence only, never promotes | no | .2b.2b.1 |
| 5 | `evaluate_latency_sensitivity` | REQUIRES_OPEN_TO_OPEN_ADAPTER | close-to-close bar-shift model | §9 delayed-entry adapter (0/1/5/15 min v1, REQUIRES REVIEW) | §4.A trade returns, cost-adjusted | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | EXECUTION family | evidence only | no | .2b.2b.1 |
| 6 | `evaluate_bootstrap` | REQUIRES_METHODOLOGY_CORRECTION | hardcoded `sqrt(252)`, no `periods_per_year` param | add defaulted `periods_per_year=252` param (Option A, §12); OPEN→OPEN caller passes 365 | §4.B daily series | 365 (OPEN→OPEN caller) / 252 (legacy, unchanged) | n/a | n/a | n/a | never applied to holdout post-freeze | n/a | n/a | n/a | n/a | PERFORMANCE family (robustness dist.) | evidence only | no | .2b.2b.1 |
| 7 | `evaluate_monte_carlo_trade_sequence` | REUSE_UNCHANGED | confirmed: accepts raw `trade_returns` tuple, no close/backtest dependency | none | §4.A trade returns | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | ROBUSTNESS family | evidence only | no | .2b.2b.1 |
| 8 | `evaluate_stress` | REQUIRES_OPEN_TO_OPEN_ADAPTER | close-to-close terminal-bar shock via `run_vectorized_backtest()` | §10 four OPEN→OPEN stress adapters, explicit synthetic-evidence marker | §4.A trade returns, cost/price-adjusted | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | RISK family | evidence only | no (except liquidity sub-case) | .2b.2b.1 |
| 9 | `evaluate_parameter_stability` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | pure diagnostic scorer over caller-supplied `ParameterResult`s, no backtest call | supply OPEN→OPEN `ParameterResult`s from §7's 7-point neighborhood | §4.B daily series (Sharpe/return per neighbor) | 365 | n/a | n/a | n/a | never optimizes against holdout | n/a | n/a | n/a | n/a | ROBUSTNESS family | evidence only, never replaces baseline | no | .2b.2b.2 |
| 10 | `evaluate_multiple_testing` (BH/FDR portion only) | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | generic BH step-up over any p-value dict; prefer calling `research_validation.benjamini_hochberg` directly over the inline duplicate | supply full trial p-value dict per §15 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | ROBUSTNESS family | evidence only | no | .2b.2b.2 |
| 11 | `backtest_overfitting_probability` field | REQUIRES_METHODOLOGY_CORRECTION | not canonical CSCV PBO — literal boolean failure-rate | new, separate CSCV PBO artifact (§16); do not reuse/relabel this field | §4.B daily series across §7 trial matrix | 365 | contiguous time blocks, `S` per CSCV | n/a | n/a | trials never include holdout | n/a | n/a | CSCV combinatorial split + logit (§16) | n/a | ROBUSTNESS family, may report UNAVAILABLE | UNAVAILABLE if trials/blocks insufficient, never fabricated | no | .2b.2b.1 |
| 12 | `deflated_sharpe_probability` field | REQUIRES_METHODOLOGY_CORRECTION | simplified Gaussian diagnostic, not canonical DSR (no skew/kurtosis/per-trial variance) | new `DeflatedSharpeEvidenceV1` artifact (§17); do not relabel existing field | §4.B daily series (or §4.A, REQUIRES REVIEW) | 365 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | canonical DSR (skew, kurtosis, trials, expected-max-Sharpe) | ROBUSTNESS family, may report UNAVAILABLE | UNAVAILABLE if observations/trials insufficient | no | .2b.2b.1 |
| 13 | `performance_metrics` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | already parameterized `periods_per_year`; needs correct series + explicit 365 | call twice: once with §4.B series (`periods_per_year=365`) for daily metrics, once with §4.A series for trade-level fields | both §4.A and §4.B (mapped separately, §18) | 365 (daily) / n/a (trade-level) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | PERFORMANCE + PERFORMANCE(trade) families, mapped separately | n/a | no | .2b.2b.2 |
| 14 | `tail_risk_metrics` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | pure quantile calc, no annualization, no close-to-close dependency | call with §4.A trade returns for trade-level tail distribution | §4.A trade returns | n/a (no annualization) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | RISK family (trade-level) | n/a | no | .2b.2b.2 |
| 15 | `complexity_components` | REUSE_UNCHANGED | pure parameter-count/turnover/sample-size penalty, no temporal semantics | none | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | non-authoritative navigation aid, unchanged | n/a | no | .2b.2b.2 |
| 16 | `StrategyScorecardV2` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | schema already fits (no PASSED state, family/state enums sufficient); status forced BLOCKED via existing `dataset_health_status` invariant | populate `dimensions` to distinguish daily vs. trade-level metrics; set `dataset_health_status="BLOCKED"` | both §4.A and §4.B | 365 (daily fields) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | see row above | status always BLOCKED in 3J.2b.2b | yes (structural cap) | .2b.2b.2 |
| 17 | `build_validation_package` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | only checks completeness + hash format, not per-category `passed`; `promotion_status` always `"REVIEW_REQUIRED_OR_BLOCKED"` | caller supplies explicit blocked/failed evidence hash for `execution_realism`; caller, not this function, enforces pass/fail semantics | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | package exists, overall state still BLOCKED | `execution_realism` permanently blocked | yes | .2b.2b.2 |
| 18 | Golden/vector reconciliation (`cross_engine.py`) | REQUIRES_OPEN_TO_OPEN_ADAPTER | built around `run_vectorized_backtest()` close-to-close comparison; semantics do not match OPEN→OPEN | new minimal independent arithmetic path (§20), not the existing golden engine | direction/entry/exit/gross/costs/net, independently recomputed | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | any unexplained difference → whole package BLOCKED | no | .2b.2b.1 |
| 19 | `run_vectorized_backtest()` | not reusable (excluded by design) | hard-coded close-to-close accounting, `sqrt(252)` Sharpe; architecturally incompatible with OPEN→OPEN, non-overlapping discrete trades | n/a — must never be called from any 3J.2b.2b adapter | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | no | n/a |
| 20 | Synthetic no-edge control (new) | new artifact | required before any edge claim, no existing equivalent | §14 permutation construction | §4.B daily series (Sharpe statistic) | 365 | n/a | n/a | n/a | never touches holdout | basis-value permutation, fixed seed(s) | `(1+count(null>=obs))/(1+N)` (§14) | n/a | n/a | ROBUSTNESS family | required precondition for any edge claim | no | .2b.2b.1 |

## 24. Architecture items still marked REQUIRES REVIEW

1. Exact `embargo_bars` v1 width (§5.2) — proposed default `= holding_horizon_bars`, but the correct width is a statistical question about `crypto_mark_index_basis` autocorrelation this docs-only proposal cannot resolve.
2. Exact holdout boundary (calendar cutoff or bar-count fraction) (§6) — must be fixed by the owner before implementation, not derived from where performance looks best.
3. Exact owner-approved baseline values for `basis_entry_threshold` / `holding_horizon_bars` / `maximum_absolute_exposure` used as the §7 stability neighborhood's center — not fabricated by this proposal.
4. Exact number of null-control permutations for §14 (a fixed count must be preregistered before any null run is inspected).
5. Whether §17's DSR skewness/kurtosis inputs should be computed from the §4.B daily series or the §4.A trade-return series — both are defensible; the owner should pick one before `DeflatedSharpeEvidenceV1` is implemented.
6. Exact §9 latency levels (proposed v1: 0/1/5/15 minutes) — calibratable once a real target venue's latency profile is known.
7. Whether prior, pre-3J.2b.2a exploratory parameter/hypothesis work (§15) needs to be reconstructed from history for an honest trial-count denominator, and if so, how far back.
8. §4.C's rejected mark-to-market alternative — whether it should become a v2 extension once 3I.4 exists.
9. Whether canonical CSCV PBO (§16) is even worth computing at 3J.2b.2b.1's likely trial-count scale, or should be explicitly deferred to a later module once more trials naturally accumulate, rather than implemented now only to report `UNAVAILABLE`.

## Scope exclusions

No code. No migration. No parameter optimization. No strategy change. No new
feature. No open-interest/funding input. No real provider. No paper trading.
No shadow/live. No 3I.4 implementation. No performance/alpha claim.
