# Module 3J.2b.2b (Proposal): Professional Validation Semantics for OPEN→OPEN Basis Research

Status: **proposal only — not implemented, not authorized, not started.** No
code, migration, schema, strategy, signal, parameter, or test in this
repository changes as a result of this document. This is
documentation/architecture analysis only. No implementation PR is opened by
this document, and none is authorized by it.

**Revision note.** This revision incorporates owner review of the original
proposal (PR #123). Every architecture item previously logged as REQUIRES
REVIEW in §24 has been resolved by explicit owner decision: the dual
return-series architecture is approved unchanged (§4); trade-level scorecard
metrics are corrected to a new non-annualized `trade_return_metrics_v1`
helper rather than misusing `performance_metrics` on an irregular trade
sequence (§18); capacity is reclassified to explicit `BLOCKED`
(`MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS`) for v1, not implemented as an
adapter (§11); the walk-forward fold authority becomes a new timestamp-aware
`OpenToOpenWalkForwardProtocolV1`, not the existing integer-only
`purged_walk_forward_splits` (§5); purge/embargo are pinned to
`holding_horizon_bars` (§5.1/§5.2); the untouched holdout is defined
mechanically as the final 20% of elapsed UTC time, snapped to `00:00 UTC`,
with explicit minimum-evidence gates (§6); baseline parameter values remain
an owner/implementation input, never an architecture constant (§7); the
primary synthetic no-edge null becomes a deterministic circular time-shift of
basis values, with ordinary permutation demoted to a secondary diagnostic
(§14); latency levels (0/1/5/15 min, labeled `COARSE_1M_GRID_LATENCY_STRESS`)
are approved (§9); historical trial accounting is resolved via a new
`ResearchTrialLedgerV1` with an explicit counting rule (§15); canonical CSCV
PBO is authorized for implementation now, with concrete eligibility gates
(§16); canonical DSR is pinned to the daily return series evaluated on
pre-holdout evidence (§17); and mark-to-market remains deferred by explicit
decision, not merely by default (§4.C). One new architecture item surfaced
during this revision and is recorded, not resolved: a capacity/liquidity
volume-unit data-authority gap (§24). No implementation is authorized by this
revision.

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
Canonical DSR requires the return series' skewness and kurtosis (Pearson
convention — see the exact v1 contract pinned in §17), the variance of
Sharpe ratios across the actual trial set, and an
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

## 4. OWNER DECIDED — dual return-series model (approved)

Two return representations are required because they answer different
questions; neither can silently stand in for the other (§18). Both are
**realized-on-exit accounting** — a return is attributed to the calendar day
or trade at which it becomes realized (the exit), never to the days a
position was merely held. This is a deliberate, approved choice, not a
placeholder pending mark-to-market (§4.C).

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
- hit rate, payoff ratio, profit factor, win/loss distribution (the new non-annualized `trade_return_metrics_v1` helper, §18 — never `performance_metrics`, which would give this irregular sequence an annualized-period semantics it does not possess);
- trade-level tail diagnostics (`tail_risk_metrics`, dimensioned `TRADE_LEVEL`, §18);
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
trading days. This is the daily series fed to `performance_metrics(...,
periods_per_year=365)` (§18), to `evaluate_bootstrap` (§12), and to
walk-forward fold-level Sharpe diagnostics (§5) as an annualized
(`periods_per_year=365`) figure. The same underlying series is also the
statistical core for the canonical CSCV PBO artifact (§16.1) and
`DeflatedSharpeEvidenceV1` (§17), and for the primary null-control statistic
(§14.3) — but for those three, it is consumed at its native non-annualized,
one-day return horizon (`SR_hat`, §17.1), never annualized. The annualized
and non-annualized uses of this one series are never mixed within a single
statistical computation.

**Explicit limitations, recorded prominently wherever this series is used**:
it is realized-on-exit, not mark-to-market — a trade opened on day `d1` and
closed on day `d2` contributes its entire compounded return to day `d2` only,
with zero visibility into equity between `d1` and `d2`; it therefore cannot
support any intratrade drawdown claim, and "realized daily max drawdown"
computed from it measures drawdown of the exit-day-attributed equity curve,
not of true mark-to-market equity. If `holding_horizon_bars` is large
relative to trade frequency, this series can materially understate real
intratrade risk. This limitation must be copied verbatim into the scorecard
(§18) and the validation package (§19), not just this document.

### 4.C Alternative considered and rejected for v1 — mark-to-market remains deferred (owner decided)

*Mark-to-market daily revaluation* (marking open positions to the day's
close/open price and attributing a partial daily P&L to every day a position
is held) would remove the "no intratrade drawdown visibility" limitation, but
it requires choosing a mark price for open exposure, which is exactly the
kind of unapproved intermediate-price/execution claim §10's exit-price-shock
design and 3J.2b.2a's Architecture 1 were built to avoid — it would silently
reintroduce a close-to-close-shaped assumption into an explicitly OPEN→OPEN
research design. **Rejected for v1; not a v1 REQUIRES REVIEW item.**
Realized-on-exit daily accounting (§4.B) is kept for v1 by owner decision.
Mark-to-market becomes a separately reviewed v2 concern only after an
explicit intratrade valuation-price authority exists — **3I.4 alone does not
automatically authorize the choice of mark price**; a distinct, separately
reviewed methodology decision is required even after 3I.4 exists.

## 5. Walk-forward: `OpenToOpenWalkForwardProtocolV1` (owner decided — timestamp-aware, not the integer-index authority)

`purged_walk_forward_splits` (`strategy_validation.py:132-152`) is a pure
integer-index generator: given a `length` and `train_size` /
`validation_size` / `test_size` / `step` / `purge` / `embargo`, it returns
`PurgedWalkForwardSplit` objects with strictly ordered index ranges. It does
not itself read timestamps, closes, or feature events. `run_purged_walk_forward`
(`research_validation.py:24-48`), by contrast, is close-to-close (§3.1) and
cannot be reused as-is.

**Owner decision**: the canonical 3J.2b.2b fold authority is **not** the
existing integer-only `purged_walk_forward_splits`. It is a new, dedicated,
timestamp-aware, immutable protocol, conceptually `OpenToOpenWalkForwardProtocolV1`,
whose fold boundaries are UTC wall-clock timestamps, not bar or event
indices. `purged_walk_forward_splits` may still be reused *internally* by an
implementation of that protocol (e.g. to generate the underlying index
arithmetic once boundaries are translated to 1m-bar positions) only if exact
timestamp/bar-index equivalence is proven by test — it is never the public
research contract itself. Fold boundaries are snapped to UTC calendar-day
boundaries (`00:00:00 UTC`) because the headline OOS return series
(`REALIZED_EXIT_DAILY_RETURN_SERIES_V1`, §4.B) is itself a daily grid; a
sub-day fold boundary would create a fold whose "day" is only partially
covered by either side.

Each fold remains ordered `TRAIN → VALIDATION → TEST` in wall-clock time.
**TEST windows across folds must not overlap.** An earlier fold's TEST period
may become historical information available to a chronologically later
fold's TRAIN/VALIDATION — that is not look-ahead, provided the later fold
only ever sees it after its own original timestamp (i.e. a plain forward
walk where later folds legitimately retrain on more history, exactly as a
live deployment would accumulate history over time). **No future TEST data
may inform an earlier fold** — an earlier fold's parameter/threshold choices
must never be revised using a later fold's TEST result.

Feature decisions are assigned to a fold by their canonical `decision_at`
timestamp directly (compared against the fold's UTC boundaries) — not via an
intermediate bar-index translation for assignment purposes, though bar
indices remain the unit for purge/embargo width (§5.1/§5.2) since a trade's
economic exposure window is itself bar-denominated
(`holding_horizon_bars * 1m`). A trade's complete `entry_open_at → exit_open_at`
economic exposure window must fall entirely inside the fold it is assigned
to, after purge/embargo (§5.1/§5.2) — a decision whose exposure window
would cross into the next fold is purged (§5.1), never truncated or
partially counted.

### 5.1 Purge (owner decided: `purge_bars = holding_horizon_bars`)

A decision's economic exposure window is `entry_open_at → exit_open_at`
(`entry.bar_open_at` through `entry.bar_open_at + holding_horizon_bars * 1m`
per 3J.2b.2a's exit rule). Any decision whose exposure window **crosses** a
fold boundary must be purged: excluded from both sides of that boundary, not
just narrowed to whichever side its `decision_at` happens to sit in. Owner
decision, v1: `purge_bars = holding_horizon_bars` — the maximum possible
exposure width for any decision, so no train/validation trade's exposure
window can reach into a later test window by construction. This is
**conservative preregistered engineering semantics**, not a claim that
`crypto_mark_index_basis`'s autocorrelation has been empirically shown to
decay after exactly `holding_horizon_bars` bars.

### 5.2 Embargo (owner decided: `embargo_bars = holding_horizon_bars`)

Embargo is the additional bar-unit gap after a TEST segment (or between
VALIDATION and TEST) reserved so that a fold's residual serial dependence in
`crypto_mark_index_basis` itself (not just trade exposure) cannot leak
backward into the next fold's TRAIN. Owner decision, v1:
`embargo_bars = holding_horizon_bars` — the same width as purge, expressed
and enforced purely in `1m`-bar units. Like §5.1, this is a conservative
preregistered engineering default, not an empirically-derived autocorrelation
decay window. **Future real-data research may revise the embargo width only
through a new, separately versioned methodology (e.g.
`OpenToOpenWalkForwardProtocolV2`), never by silently adjusting the v1
constant in place** — this preserves the ability to compare v1 evidence
against itself over time.

## 6. Untouched holdout (owner decided — mechanical, sealed-dataset-derived)

The three preregistered parameters are exactly `basis_entry_threshold`,
`holding_horizon_bars`, and `maximum_absolute_exposure`
(`CryptoBasisMeanReversionDefinitionV1`, `crypto_basis_mean_reversion_v1.py:100-102`)
— no fourth parameter exists in the definition and none may be introduced by
this proposal (scope exclusion, below).

**Owner-decided v1 mechanism** — the holdout boundary is derived mechanically
from the sealed dataset, before any performance is inspected, never chosen
to make the holdout look a particular way:

1. Derive the full UTC wall-clock evaluation span from the sealed dataset's first and last available `1m` bar.
2. Reserve the final **20%** of that elapsed UTC wall-clock time as the holdout range.
3. Snap `holdout_start` forward to the next `00:00:00 UTC` boundary (never backward — the holdout only ever shrinks toward exactly 20%, it is never enlarged past it by rounding).
4. The holdout is evaluated **exactly once**, after every other methodology decision in this document (parameters, scenarios, trial count, null-control construction) is frozen, by a single, non-repeated invocation of the OPEN→OPEN return/scorecard pipeline.

**Minimum evidence requirements**: at least **30 complete UTC calendar days**
in the holdout range, and at least **90 complete UTC calendar days** before
the holdout start (i.e. available to TRAIN/VALIDATION/TEST folds). **If
either requirement is not satisfied, `UNTOUCHED_HOLDOUT = UNAVAILABLE`** —
the implementation must report unavailability rather than shrinking the 20%
rule or the snap-to-midnight rule post hoc to manufacture a usable holdout
from a too-short dataset.

Every methodology choice this document covers — the fold protocol (§5),
parameter-neighborhood evaluation (§7), cost/latency/stress scenario design
(§8-§10), null-control construction (§14), multiple-testing trial accounting
(§15), CSCV PBO block construction (§16), and DSR trial accounting (§17) —
must be frozen using only evidence strictly before `holdout_start`. Repeat
holdout evaluation is illegitimate by construction ("let's also try
`holding_horizon_bars=48` and see how the holdout looks" would void the
holdout's status); the implementation must make repeat holdout evaluation
structurally inconvenient (e.g. requiring a new, separately content-hashed
and dated evidence artifact each time, so repeat runs are visible in the
evidence trail rather than silently overwritten).

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

**Owner decided — baseline is not an architecture constant.** 3J.2b.2b does
not hardcode numerical values for `basis_entry_threshold`,
`holding_horizon_bars`, or `maximum_absolute_exposure`. The validation system
consumes one already-preregistered `CryptoBasisMeanReversionDefinitionV1`
instance as its baseline, supplied by the caller; fixture tests for this
module may use clearly labeled engineering values only (values obviously
chosen for test determinism/coverage, never presented as a real research
recommendation). Before any future real-market research run, the actual
baseline values must be frozen in a separately content-hashed
preregistration artifact **before** the untouched holdout (§6) is opened —
this is a process requirement on the implementation, not a numeric constant
this docs-only proposal fabricates.

Proposed v1 neighborhood, centered on whatever baseline
`CryptoBasisMeanReversionDefinitionV1` the caller supplies: one perturbation
step in each direction for each of the three preregistered parameters, i.e.
up to `3 × 2 = 6` neighbor runs plus the 1 baseline run = 7 total runs in the
v1 grid — small enough to preregister exhaustively. Every neighbor run must
reuse the exact same TRAIN/VALIDATION fold range as the baseline (never the
untouched holdout, per §6). Neighboring runs are stability evidence only; if
a neighbor outperforms the baseline, that fact is recorded in the stability
report and must not replace the baseline — there is no code path in this
proposal that lets a stability run promote itself to "selected." Every one of
the 7 runs (baseline + 6 neighbors) is entered into `ResearchTrialLedgerV1`
(§15) as a performance-bearing trial — none may be silently excluded from
the multiple-testing/CSCV trial count because it performed badly.

## 8. Cost/slippage sensitivity — owner decided: 1.0x/1.5x/2.0x/3.0x approved

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

**Owner-approved v1 latency levels**, chosen as whole multiples of the `1m`
research grid so `delayed_decision_time` always resolves deterministically
against the same bar series without sub-bar interpolation: **0 (baseline),
1 minute, 5 minutes, 15 minutes**. These must be explicitly labeled
`COARSE_1M_GRID_LATENCY_STRESS` in the evidence artifact — they are a
methodology stress test on the `1m` research grid, not a claim about actual
exchange/network latency for any real venue. Real venue calibration is
deferred until real execution evidence exists (3I.4 or later); this item is
resolved and removed from `REQUIRES REVIEW` (§24).

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
4. **Reduced liquidity / volume assumptions** — since §11's capacity evidence is now `BLOCKED` (`MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS`), this stress category cannot be built on the same unproven volume-unit assumption capacity was blocked for. This sub-category is **also `BLOCKED` for v1**, for the identical reason, rather than fabricated from an unauthorized unit guess; it must be represented in the stress evidence artifact as an explicit blocked sub-result, not silently omitted.

Every one of these four evidence categories must carry an explicit
`"synthetic_validation_evidence": true` marker (or equivalent field) in its
artifact, distinct from `BasisMeanReversionResearchRunV1`'s untouched
Feature-Authority-sourced decisions.

## 11. Capacity — owner decided: BLOCKED for v1, not implemented

**Revision note**: this section materially revises the original proposal's
plan to build a bounded trade-ledger capacity adapter now. On review, the
owner identified a data-authority gap this proposal had not accounted for:
the historical OHLCV normalization carries a `volume` field as a bare
numeric quantity, but does **not** carry an authoritative `volume_unit`,
`volume_asset`, or a contracts/base-asset/quote-notional discriminator at the
tradable-bar evidence boundary. A capital/notional participation calculation
(`adv_notional = average_daily_volume × average_price`, the pattern
`evaluate_capacity` already uses at `quant_validation.py:154`) against that
unitless `volume` field would therefore silently assume units — e.g. "volume
is already in quote-asset notional" or "volume is in base-asset contracts
convertible via `average_price`" — that no existing authority in this
codebase actually proves. Building a capacity adapter on top of that
assumption would produce a number that looks like a bounded engineering
estimate but is actually built on an unverified unit guess.

**Decision: `evaluate_capacity` is reclassified from
`REQUIRES_OPEN_TO_OPEN_ADAPTER` to effectively `DEFER / BLOCKED UNTIL
AUTHORIZED VOLUME-UNIT + EXECUTION EVIDENCE`.** The proposed bounded
trade-ledger capacity adapter (entry/exit-bar volume, capital level, absolute
exposure, participation limit, impact coefficient) described in the prior
revision of this document is **not implemented in 3J.2b.2b.1**. For v1:

```
capacity = UNAVAILABLE / BLOCKED
reason   = MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS
```

This must still be produced as an **explicit, deterministic evidence
artifact** so `build_validation_package`'s completeness gate (§19) is
satisfied honestly — a `capacity` entry that is present, content-hashed, and
labeled `BLOCKED`/`MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS`, not an absent
key and not a fabricated `OHLCV_ESTIMATE_ONLY` number. The implementation
must not fabricate ADV notional, a participation rate, usable capital, or a
market-impact estimate from the unitless `volume` field to make this
category "pass." A future, separately reviewed authority may unblock
capacity once explicit venue/tradable-size semantics (a proven
`volume_unit`/`volume_asset`/contracts-vs-notional discriminator) and/or
3I.4 exist — until then, capacity remains permanently capped at `BLOCKED`
regardless of any other evidence's strength, exactly as execution-realism
already is (§3, §19).

## 12. Bootstrap — owner decided: Option A approved

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

Implementation must include a regression test proving every existing Trend V2
caller's bootstrap output is bit-for-bit unchanged after the parameter is
added (i.e. calling `evaluate_bootstrap` with no `periods_per_year` argument
reproduces the exact same resample outputs, for a fixed seed, as before the
change) — the new parameter must be additive, never a behavioral change to
any existing call site.

## 13. Monte Carlo — owner decided: reuse unchanged, confirmed

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

## 13A. Statistical estimator convention V1 — owner decided: shared by §14, §16, §17

This section pins the exact scalar estimators used by every non-annualized
daily Sharpe, DSR moment, and trial-Sharpe-distribution calculation in this
proposal, so no later implementation can silently depend on a numeric
library's default convention (population vs. sample standard deviation, or
Fisher/excess vs. Pearson kurtosis, differ by library and by call-site
flag). §14 (null-control), §16 (CSCV PBO), and §17 (DSR) each reference this
section rather than duplicating or re-deriving their own formulas.

### 13A.1 Non-annualized daily Sharpe

For any daily return vector `r_1 ... r_T` (a full
`REALIZED_EXIT_DAILY_RETURN_SERIES_V1`, or the same series restricted to a
CSCV IS/OOS block subset, §16.1):

```
arithmetic_mean = sum(r_i) / T
```

Daily Sharpe uses the **sample** standard deviation (`ddof = 1`):

```
s =
    sqrt(
        sum((r_i - arithmetic_mean)^2)
        / (T - 1)
    )

SR_hat = arithmetic_mean / s
```

`ddof = 1` everywhere this proposal refers to the non-annualized daily
Sharpe — never the population (`ddof = 0`) convention.

**Undefined cases**: if `T < 2` or `s == 0`, Sharpe is `UNAVAILABLE`. Never
silently substitute zero for an undefined Sharpe.

This exact convention is used, with no exception, in: the circular-shift
null-control's `observed_stat`/`null_stat` (§14.3); the CSCV IS Sharpe and
OOS Sharpe for every one of the 70 splits (§16.1); every trial Sharpe
consumed by DSR (§17); the selected strategy's `SR_hat` (§17.1); and any
parameter-stability Sharpe computed from this daily series (§7, §23 row 9).

### 13A.2 DSR moment estimators (skewness, Pearson kurtosis)

DSR's skewness and kurtosis inputs (`gamma3`, `gamma4`, §17.2) must not
rely on a numeric library's default skew/kurtosis convention. Pin the v1
estimators explicitly. Let:

```
mean = arithmetic_mean

m2 = (1/T) * sum((r_i - mean)^2)
m3 = (1/T) * sum((r_i - mean)^3)
m4 = (1/T) * sum((r_i - mean)^4)
```

First compute the moment coefficients:

```
g1 = m3 / m2^(3/2)
g2_excess = m4 / m2^2 - 3
```

Then use bias-corrected sample estimators:

```
gamma3 =
    sqrt(T * (T - 1))
    / (T - 2)
    * g1
```

and:

```
excess_kurtosis_corrected =
    ((T - 1) / ((T - 2) * (T - 3)))
    * (
        (T + 1) * g2_excess
        + 6
      )

gamma4 =
    excess_kurtosis_corrected + 3
```

`gamma4` computed this way remains Pearson kurtosis — §17.2's "not excess
kurtosis" rule applies to this bias-corrected value exactly as it does to
any library-reported value.

**Eligibility for these moment estimates**:

```
T >= 4
m2 > 0
```

The existing, stronger DSR gate (§17.5, `T >= 30`) remains controlling —
this `T >= 4` / `m2 > 0` floor only guards the moment arithmetic itself
against division by zero and is never sufficient on its own to make DSR
numeric.

### 13A.3 Trial-Sharpe distribution convention

For the `N` eligible trial Sharpes `SR_1 ... SR_N` (each computed per
§13A.1, one per `ResearchTrialLedgerV1` trial, §15), pin:

```
mu_SR = arithmetic mean of trial Sharpes
```

and:

```
sigma_SR =
    sample standard deviation of trial Sharpes
    with ddof = 1
```

**Never use population standard deviation for `sigma_SR`.** Require:

```
N >= 2
sigma_SR finite
```

The existing, stronger DSR gate (§17.5, `N >= 6`) remains controlling —
this `N >= 2` / finite-`sigma_SR` floor only guards `mu_SR`/`sigma_SR`
arithmetic itself and is never sufficient on its own to make DSR numeric.

### 13A.4 Reference vectors

Every consumer of this convention (§14.3, §16, §17) must validate its
implementation against independent hand-computed (or independently-tooled)
reference vectors, never against output generated by calling the production
function itself. The exact reference-vector coverage required for DSR is
pinned in §17.5; for CSCV PBO in §16.

## 14. Synthetic no-edge control — owner decided: circular time-shift is the primary null

Required before any edge claim (§1). **Revision note**: the original
proposal's primary null was an ordinary random permutation of basis values.
On review, the owner determined that ordinary full permutation destroys the
time-series dependence structure (autocorrelation) of `crypto_mark_index_basis`
and can make the null artificially easy to beat — a strategy that merely
exploits basis autocorrelation, not genuine mean-reversion edge, could still
look like it beats a fully-shuffled null. **The v1 primary null is a
deterministic circular time shift instead.**

### 14.1 Primary null: deterministic circular shift

Take the ordered sequence of observed `crypto_mark_index_basis` values across
all feature decision events in the evaluation window (TRAIN+VALIDATION+TEST,
never touching the untouched holdout until §6's freeze is complete), aligned
to the fixed, original decision timestamp set. For a non-zero integer offset
`k` (`1 <= k < eligible_feature_event_count`), construct a null basis path by
circularly shifting the ordered value sequence by `k` positions while holding
the timestamp set fixed — i.e. the value originally observed at timestamp
`t_i` is now assigned to timestamp `t_{(i+k) mod n}`. **Zero shift is
forbidden** (it would trivially reproduce the observed run). This preserves:
the exact basis-value multiset, the sequential/autocorrelation structure of
the basis-value path (a circular rotation, not a shuffle, so runs of
consecutive similar values stay consecutive), the feature-event count, the
original timestamp set, the tradable-bar evidence, the strategy parameters,
and the cost model — only the alignment between basis state and the
subsequent OPEN→OPEN return path is broken.

**Run count, v1**:

```
target_null_runs = 999
actual_null_runs = min(999, eligible_feature_event_count - 1)
```

Deterministically choose `actual_null_runs` distinct non-zero circular-shift
offsets **without replacement** from the `eligible_feature_event_count - 1`
possible non-zero shifts, using a fixed, recorded seed (the same
seed-and-count discipline as §13's Monte Carlo — chosen once, recorded in the
evidence artifact, never regenerated to get a more favorable-looking null
batch). **Require at least 99 valid non-zero shifts**; if
`eligible_feature_event_count - 1 < 99`, the null-control significance result
is `UNAVAILABLE` rather than computed on too few shifts to be meaningful.

Re-run the unmodified 3J.2b.2a decision/trade-ledger logic against each
shifted basis series to produce a null-control `BasisMeanReversionResearchRunV1`-shaped
run per offset. Synthetic null values must never be written to Feature
Authority and must never be wrapped in a real `FeatureMaterializationV2` —
they exist only inside the validation adapter's in-memory null-run
construction, and every resulting evidence artifact must be explicitly
labeled synthetic/null-control evidence.

### 14.2 Secondary diagnostic: ordinary full permutation

Ordinary full random permutation of basis values (the original proposal's
mechanism) **may remain as a secondary diagnostic only** — reported alongside
the primary circular-shift result for comparison (e.g. to illustrate how much
easier the null is to beat once autocorrelation is destroyed), but it must
never be the primary empirical p-value and must never itself be presented as
the edge-claim gate.

### 14.3 Pinned performance statistic and empirical p-value

The statistic compared between the observed untouched-OOS (or, pre-holdout,
dedicated-OOS) run and each null run is `observed_stat` / `null_stat` —
both computed identically as the **non-annualized** daily Sharpe `SR_hat`
under the canonical estimator contract (§13A.1 — sample standard deviation,
`ddof = 1`) from `REALIZED_EXIT_DAILY_RETURN_SERIES_V1` (§4.B) — the same
statistical-core statistic and estimator convention the canonical CSCV PBO
artifact (§16.1) and `DeflatedSharpeEvidenceV1` (§17) use, so the null
comparison is apples-to-apples on a like-for-like daily-series statistic,
never a mix of statistical-core and presentation units. If `observed_stat`
or a given `null_stat` is `UNAVAILABLE` under §13A.1 (`T < 2` or `s == 0`
within the window being scored), that run/shift is excluded from the
null-control computation and the exclusion is recorded — never treated as
zero and never silently counted as a pass or fail. The scorecard may
separately render the annualized Sharpe (`performance_metrics(...,
periods_per_year=365)`, §18) for presentation, but that annualized figure is
never the statistic compared here. Empirical p-value, using the primary
circular-shift null:

```
p = (1 + count(null_sharpe >= observed_sharpe)) / (1 + actual_null_runs)
```

using the standard `+1` smoothing so `p` is never reported as exactly zero
regardless of shift count.

## 15. Multiple testing — owner decided: `ResearchTrialLedgerV1` required

`evaluate_multiple_testing`'s Benjamini-Hochberg step (`quant_validation.py:504-507`)
is a standard BH step-up procedure over any `dict[str, Decimal]` of p-values
— generically reusable (`REUSE_WITH_EXPLICIT_INPUT_CONTRACT`) once the trial
p-values are supplied correctly, though it is a private inline duplicate of
the already-parameterized `benjamini_hochberg(p_values, *, alpha=Decimal("0.05"))`
in `research_validation.py:73-84`; the OPEN→OPEN caller should call the
`research_validation.py` version directly rather than re-duplicating the
algorithm a third time.

**Owner-decided trial-counting rule**: for this basis-strategy research
program, count every **performance-bearing** strategy/parameter trial whose
result was actually inspected or used in a research decision. Concretely:

- **Do count**: the preregistered baseline (§7); every one of the §7 six-neighbor stability runs; any additional parameter/hypothesis run whose performance is inspected during this or a later module; any rejected performance experiment (a rejected experiment is exactly as load-bearing as the selected one for this accounting — it must never be dropped from the denominator because it performed badly).
- **Do not count**: architecture-only discussions that were never performance-tested (e.g. the mark-to-market alternative rejected in §4.C); documentation alternatives never run against data; ordinary unit-test fixtures exercising this module's code paths; null-control permutations/circular shifts (§14) — those are null-distribution samples, not strategy trials.
- **Prior-history resolution**: prior to 3J.2b.2a, there was no formal, measured basis-strategy performance-selection run in the canonical research chain (3J.2b.2a's own docstring already establishes it as the first tradable crypto-perpetual strategy). **No speculative reconstruction of conversational/design alternatives is required** — the trial ledger for this program starts at 3J.2b.2a's baseline, not at zero-with-an-unknown-gap and not at a fabricated pre-history.
- Datasets/universes examined: the single fixture dataset/instrument used throughout 3J.2b.2a, explicitly counted as `1`, not omitted because it is fixture-only.
- Feature combinations examined: the single required `crypto_mark_index_basis` feature — 3J.2b.2a's `validate()` gate already proves no other feature was used.

**Recommend/introduce `ResearchTrialLedgerV1`**: a deterministic, in-memory,
content-hashed evidence artifact (same style as every other `quant_validation.py`
artifact, no new table — §21) that records every performance-bearing trial
above — its parameters, whether it was selected/rejected, and the evaluation
window it was scored against — so the trial count feeding BH/FDR (§15), CSCV
PBO (§16), and DSR (§17) cannot silently disappear or shrink later; all three
of those consumers must read from this one shared ledger rather than
maintaining separate, potentially divergent trial counts.

## 16. Canonical PBO — owner decided: implement now in 3J.2b.2b.1

Per §3.3, the existing `backtest_overfitting_probability` must not be reused
under the name PBO. **Revision note**: the prior revision of this proposal
suggested deferring the algorithm because the first real result would likely
be `UNAVAILABLE`. The owner rejected that deferral — the correct evidence
type must be implemented in 3J.2b.2b.1 now, and eligibility gates (below)
determine whether a numeric result is supportable for any given research
run; `UNAVAILABLE` is a valid, expected output of a correctly implemented
gate, not a reason to skip building the gate.

**V1 methodology**:

- **Input**: a strategy/parameter-trial return matrix — one row per trial recorded in `ResearchTrialLedgerV1` (§15), one column per time-block, populated from `REALIZED_EXIT_DAILY_RETURN_SERIES_V1` (§4.B) per trial per block.
- **Blocks**: `cscv_blocks = 8` — split the pre-holdout (TRAIN+VALIDATION+TEST, never the untouched holdout, §6) daily realized-return timeline into 8 equal contiguous chronological blocks.
- **Eligibility gate** (all three required, else `PBO = UNAVAILABLE`):
  1. at least **8 non-empty chronological blocks** (i.e. `cscv_blocks = 8` and every block must actually contain daily observations — a block with zero trading days does not count);
  2. at least **5 complete UTC daily observations per block**;
  3. at least **6 distinct performance-bearing strategy/parameter trials** in `ResearchTrialLedgerV1`.
- **CSCV process** (once eligible): form all `C(8, 4) = 70` combinatorially symmetric splits of the 8 blocks into an in-sample (IS) half (4 blocks) and an out-of-sample (OOS) half (the complementary 4 blocks). The exact ranking statistic, IS-winner tie-break, OOS-rank convention, and PBO aggregation are pinned in §16.1-§16.3 below, so implementation cannot choose its own variant.
- **Insufficient trials/sample**: if the eligibility gate above is not met, `PBO = UNAVAILABLE` rather than a fabricated number computed on too few trials/blocks to be meaningful — this is an expected, not exceptional, v1 outcome given a single fixture dataset and a small preregistered trial count.
- **Testing**: the implementation must ship with independent reference/test vectors for the CSCV computation — a hand-verifiable small trial-matrix example with known per-trial IS/OOS Sharpes (computed under the §13A.1 `ddof = 1` convention), known tie-break/midrank outcomes, and a known PBO value — computed independently, never by calling the production function itself to generate the expected values, so the logit/combinatorial-split/tie-break/zero-variance-handling arithmetic itself is validated independently of any live strategy run.

Never reuse the current boolean failure-rate field (`backtest_overfitting_probability`)
as canonical PBO under any circumstance — the new artifact is entirely
separate.

### 16.1 Ranking statistic

For every one of the 70 CSCV splits, the IS/OOS ranking statistic is the
**non-annualized** daily Sharpe `SR_hat` computed under the canonical
estimator contract (§13A.1 — sample standard deviation, `ddof = 1`) from
each trial's `REALIZED_EXIT_DAILY_RETURN_SERIES_V1`, restricted to the
split's IS or OOS blocks respectively. If a block subset yields `T < 2` or
`s == 0` for a given trial, that trial's Sharpe is `UNAVAILABLE` for this
split under §13A.1, which triggers the zero-variance handling in §16.3 (the
split is invalid; never substitute `Sharpe = 0`). Annualizing by `sqrt(365)`
would not change any rank (annualization is a strictly monotonic per-trial
rescaling), but the statistical core stays daily/per-period, consistently
with §13A, §14.3, and §17.

### 16.2 IS winner and OOS rank

**IS winner**: the trial with the highest IS daily Sharpe among the IS
blocks wins the split. If an exact IS-Sharpe tie occurs among two or more
trials, the tie is broken deterministically using the immutable canonical
trial identity/content hash (lowest hash wins) — **never** using OOS
information to break an IS tie. The number of IS ties encountered across all
70 splits is recorded in the CSCV evidence artifact as diagnostic evidence.

**OOS rank**: rank all `N` trials by OOS daily Sharpe over the complementary
OOS blocks:

```
rank 1 = worst
rank N = best
```

For an exact OOS-Sharpe tie among two or more trials, use the
average/midrank convention (each tied trial receives the mean of the ranks
its tied group spans).

For the IS-selected trial in a given split, its OOS rank gives:

```
omega_c = oos_rank / (N + 1)
```

therefore:

```
0 < omega_c < 1
```

and:

```
lambda_c = ln(omega_c / (1 - omega_c))
```

### 16.3 PBO aggregation and zero-variance handling

```
PBO =
    count(lambda_c <= 0)
    / number_of_valid_CSCV_splits
```

Preserve the already-approved `cscv_blocks = 8`, minimum 5 complete UTC
daily observations per block, and minimum 6 trials (eligibility gate above).

**Zero-variance handling (fail-closed, deterministic)**: if any statistic
required for a split (an IS or OOS trial's daily Sharpe) is `UNAVAILABLE`
per §13A.1 (`T < 2` or `s == 0` within that split's blocks), the entire
split is invalid: record the reason, do not silently substitute
`Sharpe = 0` or any other placeholder value, and exclude the split from
`number_of_valid_CSCV_splits`. **All 70 splits must be valid for a numeric
v1 PBO result; otherwise `PBO = UNAVAILABLE`.**

## 17. Canonical DSR — owner decided: exact v1 mathematical contract, daily series only

Per §3.4, `deflated_sharpe_probability` is not canonical DSR. Proposal:
introduce a separate, newly and explicitly reviewed artifact,
`DeflatedSharpeEvidenceV1`, rather than silently changing
`MultipleTestingEvidence.deflated_sharpe_probability`'s established
historical semantics (which Trend V2 callers already depend on).

**Owner-decided return-series choice**: `DeflatedSharpeEvidenceV1` operates
on the pre-holdout `REALIZED_EXIT_DAILY_RETURN_SERIES_V1` (§4.B) exclusively
— **not** irregular trade returns (§4.A) — for the selected strategy, for
every eligible research trial, and for the expected-maximum-Sharpe
calculation alike. Mixing a daily-series statistic's shape with trade-level
skew/kurtosis would deflate one distribution's statistic using another
distribution's shape.

### 17.1 Non-annualized daily Sharpe (statistical core)

The DSR statistical core is the non-annualized, one-day return-horizon
Sharpe, computed under the canonical estimator contract pinned in §13A.1
(sample standard deviation, `ddof = 1`; `UNAVAILABLE` if `T < 2` or
`s == 0`, never a substituted zero):

```
SR_hat = arithmetic_mean / s   # §13A.1
```

The scorecard's annualized `SR_hat * sqrt(365)` must never be fed into the
PSR/DSR finite-sample formula below. This exact one-day-horizon `SR_hat`,
under §13A.1's estimator convention, is used consistently across: the
selected strategy; every research trial; and the expected-maximum-Sharpe
calculation (§17.3). Annualized Sharpe remains a presentation/scorecard
metric only (§18).

### 17.2 Pinned inputs

```
T = number of complete daily observations
N = number of eligible trials from ResearchTrialLedgerV1
gamma3 = bias-corrected sample skewness of daily returns (§13A.2)
gamma4 = bias-corrected sample Pearson kurtosis of daily returns (§13A.2)
```

`gamma3` and `gamma4` are computed exactly as pinned in §13A.2 — never via
a numeric library's default skew/kurtosis function, whose bias-correction
and Fisher-vs-Pearson convention vary by library and by call-site flag.

**Important: `gamma4` is not excess kurtosis.** Normal-distribution Pearson
kurtosis is `3`. If an implementation nonetheless reads a library's raw
excess/Fisher kurtosis value instead of computing `gamma4` via §13A.2 (the
convention where a normal distribution has excess kurtosis `0`), it must
convert before using the value as `gamma4`:

```
pearson_kurtosis = excess_kurtosis + 3
```

Any proposal or implementation wording stating that the canonical formula
directly consumes "excess kurtosis" is incorrect; `gamma4` is always Pearson
kurtosis as pinned in §13A.2 (see the consistency note in §23).

### 17.3 Expected-maximum-Sharpe benchmark

For all `N` eligible trials' daily Sharpes (each computed per §13A.1, from
`ResearchTrialLedgerV1`, §15), compute `mu_SR` and `sigma_SR` exactly as
pinned in §13A.3 (sample standard deviation, `ddof = 1`; never population
standard deviation):

```
mu_SR
sigma_SR
```

and pin the expected-maximum-Sharpe benchmark:

```
gamma_EM = 0.5772156649015329

SR_star =
    mu_SR
    + sigma_SR * (
        (1 - gamma_EM) * Phi^-1(1 - 1/N)
        + gamma_EM * Phi^-1(1 - 1/(N*e))
      )
```

### 17.4 DSR statistic

Then:

```
denominator =
    sqrt(
        1
        - gamma3 * SR_hat
        + ((gamma4 - 1) / 4) * SR_hat^2
    )

z =
    (SR_hat - SR_star)
    * sqrt(T - 1)
    / denominator

DSR = Phi(z)
```

### 17.5 V1 eligibility floor

```
T >= 30 complete UTC daily observations
N >= 6 distinct performance-bearing trials
finite trial Sharpe values
finite sigma_SR
denominator > 0
```

Otherwise: `DSR = UNAVAILABLE`.

These `30`/`6` values are explicit conservative v1 evidence gates, not
claims of universal statistical sufficiency. DSR must remain computed
pre-holdout, over the same pre-holdout research/OOS evidence used in
strategy selection (TRAIN+VALIDATION+TEST daily series and trial Sharpes,
§6); the final untouched holdout is a separate confirmatory result and must
never be used to tune or redefine DSR methodology (e.g. choosing a different
skewness/kurtosis window because the pre-holdout DSR looked weak).

**Insufficient observations/trials or a failed eligibility check →
`DSR = UNAVAILABLE`.** No "DSR passed" claim may be derived from the current
simplified diagnostic (§3.4) or from a DSR computed outside this exact
contract; the scorecard (§18) must surface `UNAVAILABLE` rather than
silently omitting the metric.

**Testing**: the implementation must ship with independent hand-computed
reference vectors for this exact formula — computed by hand or by an
independent tool, never by calling the production function itself to
generate its own expected values — covering at minimum: daily mean; sample
standard deviation (`ddof = 1`); daily non-annualized Sharpe (`SR_hat`,
§13A.1); bias-corrected sample skewness (`gamma3`) and bias-corrected
Pearson kurtosis (`gamma4`, §13A.2); trial-Sharpe mean (`mu_SR`) and
trial-Sharpe sample standard deviation (`sigma_SR`, `ddof = 1`, §13A.3);
`SR_star`; `denominator`; `z`; and `DSR`. This is the same independent
hand/reference-vector discipline required for CSCV PBO (§16), including its
IS/OOS Sharpes computed under the same `ddof = 1` rule (§16.1), so the
statistical core is validated independently of any live strategy run.

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

**Trade-level metrics — owner-corrected sourcing.** The prior revision of
this proposal called `performance_metrics(trade_returns, ...)` to obtain the
trade-level fields. On review, the owner identified this as incorrect:
`performance_metrics` also emits annualized volatility, Sharpe, Sortino, and
CAGR-style fields (it is a single function producing both period-annualized
and non-annualized fields together), and calling it on a trade-return
sequence would implicitly give that irregular, non-fixed-frequency sequence
a period semantics it does not possess — a trade-return series has no
consistent "periods per year," so any annualized figure `performance_metrics`
computed from it would be meaningless despite looking like a normal Sharpe
number. **`performance_metrics` must never be called with §4.A's trade-return
series.**

**Proposed replacement — a small, generic, non-annualized helper for
3J.2b.2b.1/.2, conceptually `trade_return_metrics_v1(trade_returns: tuple[Decimal, ...])`**,
covering only: number of trades, hit rate, average trade, median trade,
win/loss ratio, payoff ratio, profit factor, and win/loss distribution — no
annualization field of any kind, so it structurally cannot emit a
"trade-level Sharpe." This is a new, small artifact, not a reuse of an
existing function; it may share arithmetic with `performance_metrics`'s
existing hit-rate/payoff/profit-factor logic if convenient, but its public
contract never exposes annualized fields.

`tail_risk_metrics(...)` may still be reused unchanged
(`REUSE_WITH_EXPLICIT_INPUT_CONTRACT`) for trade-level VaR/CVaR — it has no
annualization and no close-to-close dependency — but its resulting
`MetricObservation`s must be explicitly dimensioned `TRADE_LEVEL` so a
downstream reader cannot mistake a trade-level tail statistic for a
daily-series one.

Daily annualized performance metrics continue to use exactly
`performance_metrics(REALIZED_EXIT_DAILY_RETURN_SERIES_V1, periods_per_year=365)`
— never a trade-return sequence. These two metric families
(`trade_return_metrics_v1` + `tail_risk_metrics(..., dimension=TRADE_LEVEL)`
vs. `performance_metrics(daily_series, periods_per_year=365)`) must be tagged
distinctly (e.g. via `MetricObservation.dimensions`) so no downstream reader
can conflate a daily annualized Sharpe with a trade-level payoff ratio as if
they were computed from the same series, and trade-level returns must never
be allowed to produce an annualized Sharpe under any code path.

**Scorecard limitations (must appear in `StrategyScorecardV2.limitations`,
verbatim, every run)**: fixture-only market data (no real crypto venue or
provider contacted); no real top-of-book (3I.4 not authorized); no broker
fill evidence; no authorized execution-realism evidence (§19); no funding
accounting (3J.2b.2a's Architecture 1 excludes funding in full);
**realized-on-exit rather than mark-to-market risk path** (§4.B/§4.C — no
intratrade drawdown visibility, by deliberate v1 design, not an oversight).

**Scorecard status: `BLOCKED`, unconditionally, for all 3J.2b.2b fixture-only
runs**, regardless of how strong any synthetic result looks, for exactly the
reasons listed above. `StrategyScorecardV2.validate()` already enforces `if
dataset_health_status == "BLOCKED" then status must also be BLOCKED`
(`strategy_scorecard_v2.py:137-138`) — this proposal's implementation must
ensure `dataset_health_status` for any 3J.2b.2b run is set to `"BLOCKED"` so
this existing invariant does the enforcement, rather than relying on a
separate, possibly-forgettable status assignment. Strong synthetic
performance grants **zero promotion authority** — it can justify further
research only, never a promotion, paper-trading authorization, or alpha
claim.

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

In particular, two categories are explicit `BLOCKED` evidence in v1, each for
its own reason:

- `execution_realism = BLOCKED` (`DEFER_UNTIL_3I4`) — no real spread/fill authority exists; there is no adapter that can make this category pass honestly today.
- `capacity = BLOCKED` (`MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS`, §11) — the historical OHLCV `volume` field's unit/asset semantics are not authorized, so any capacity estimate built on it would rest on an unverified unit guess.

`golden_reconciliation` must use the new independent OPEN→OPEN reconciliation
path (§20), never the existing Trend close-to-close golden engine, whose
semantics do not match (§3.1). The package as a whole may exist and be
internally consistent while the overall research state remains `BLOCKED`
(§18) — package completeness must never imply promotion eligibility.

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
`BasisMeanReversionDecisionV1`/`BasisMeanReversionTradeV1`), **without
calling `compute_signed_open_to_open_return()` (or `SignedOpenToOpenReturnV2`
in any form) from the independent implementation at all** — every one of the
seven quantities below must be resolved/recomputed by textually separate
code:
**direction** (sign of exposure vs. the definition's threshold rule),
**entry** (bar resolved independently via the same `first_eligible_bar_after`
contract, checked for equality against the trade's own `entry_time`/`entry_open`),
**exit** (bar at `entry_time + holding_horizon_bars * 1m`, checked for
equality), **gross return** (`exposure * (exit_open/entry_open - 1)`,
computed by a second, textually separate implementation of that one-line
formula), **entry cost** (`cost_model.cost(abs(exposure))` computed
independently for the entry leg), **exit cost** (computed independently for
the exit leg), and **net return** (gross minus entry cost minus exit cost,
independently summed). Any unexplained difference between the independent
recomputation and the canonical trade ledger → validation `BLOCKED` for the
entire package (§19), not just a flagged warning on that one trade.

## 21. Persistence

**No migration and no new table proposed for the first implementation.**
Existing immutable stores are examined for fit:

- `strategy_scorecards` / `scorecard_metric_observations` / `scorecard_components` / `scorecard_data_health_assessments` / `scorecard_validation_packages` (migration `20260816_0015_strategy_scorecard_v2.py`) already model exactly the `StrategyScorecardV2` shape this proposal reuses (§18) — no new fields are needed since `MetricObservation.dimensions` (a free-form field) is sufficient to tag daily-vs-trade-level metric family without a schema change.
- `validation_packages` / `validation_package_artifacts` (migration `20260815_0003_validation_package_manifest.py`) already model `build_validation_package`'s 13-category manifest shape (§19) with immutability triggers and content-hash verification already in place — the new OPEN→OPEN evidence categories (§8-§17) are new *evidence_id/evidence_hash entries* inside that existing manifest shape, not new columns or tables.
- New evidence dataclasses this proposal introduces (the §8 cost-sensitivity adapter, §9 latency adapter, the §10 stress adapters, the §11 explicit `capacity = BLOCKED` evidence artifact, the §14 circular-shift null-control artifact, `ResearchTrialLedgerV1` (§15), the canonical CSCV PBO artifact (§16), `DeflatedSharpeEvidenceV1` (§17), the new `trade_return_metrics_v1` helper's output (§18), and the §20 independent reconciliation artifact) are all in-memory, content-hashed dataclasses in the same style as every existing `quant_validation.py` evidence type — they do not require durable persistence any more than `SlippageSensitivityEvidence` or `CapacityLevel` currently do, since they flow into the existing `validation_packages` manifest by hash reference, exactly like 3J.2b.2a's `BasisMeanReversionResearchRunV1` itself has no dedicated table.

If a later implementation discovers a genuine schema gap (e.g. a need to
durably store every null-control permutation run rather than just its
summary statistic), that would require a new migration and is explicitly
**not approved by this document** — any such migration proposal must stop
and present, separately: why the existing stores are insufficient, the exact
proposed schema, immutability/uniqueness/content-hash-identity design, and
restore implications, for a dedicated owner review before being written.

## 22. Implementation decomposition — owner approved

**Two steps, not one bounded module**, so validation semantics and
orchestration are not reviewed simultaneously — a mistake in an OOS-split
purge/embargo rule or in the PBO/DSR formulas (§5, §16, §17) is a much higher-
stakes review item than how the results are assembled into a package, and
conflating them risks the orchestration layer's completeness getting
"reviewed by association" alongside genuinely novel statistical methodology.

**3J.2b.2b.1 — OPEN→OPEN Validation Adapters + Canonical Statistical
Evidence.** Implement only:

- the `REALIZED_EXIT_DAILY_RETURN_SERIES_V1` daily realized-return builder (§4.B);
- the `trade_return_metrics_v1` non-annualized trade-level metric helper (§18);
- the OPEN→OPEN cost-sensitivity adapter (§8);
- the OPEN→OPEN latency-sensitivity adapter (§9);
- the deterministic OPEN→OPEN stress adapters, **excluding** any false capacity/liquidity claim (§10 — the reduced-liquidity sub-category stays `BLOCKED`, per §11);
- the `evaluate_bootstrap` `periods_per_year` extension, Option A (§12);
- the Monte Carlo reuse boundary — confirming `evaluate_monte_carlo_trade_sequence` is called unchanged with canonical trade returns (§13);
- the circular-shift null control, primary + secondary permutation diagnostic (§14);
- `ResearchTrialLedgerV1` (§15);
- the canonical CSCV PBO artifact, with independent reference/test vectors (§16);
- the canonical `DeflatedSharpeEvidenceV1` artifact (§17);
- the independent OPEN→OPEN reconciliation path (§20);
- the explicit `capacity = BLOCKED` evidence artifact (§11).

No walk-forward orchestration, no scorecard assembly, and no
`build_validation_package` wiring yet — each adapter/evidence type is
implemented and unit-tested in isolation against 3J.2b.2a's existing
trade-ledger evidence.

**3J.2b.2b.2 — Chronological OOS Orchestration + Scorecard + Validation
Package.** Implement:

- the `OpenToOpenWalkForwardProtocolV1` timestamp-aware UTC fold protocol (§5);
- purge/embargo enforcement (§5.1/§5.2);
- the final-20%-with-UTC-midnight-snap untouched holdout (§6);
- the parameter-stability neighborhood runner (§7);
- multiple-testing trial accounting and BH/FDR (§15);
- metric assembly (daily vs. trade-level mapping, §18);
- the forced-`BLOCKED` scorecard (§18);
- `build_validation_package` wiring, including the explicit `execution_realism`/`capacity` `BLOCKED` entries (§19).

## 23. Decision matrix (revised — owner decisions incorporated)

| # | Function/evidence | Reuse classification | Reason | Required adapter/change | Return series used | Annualization | OOS split unit | Purge rule | Embargo rule | Holdout policy | Null-control method | Empirical p-value method | PBO method | DSR method | Scorecard mapping | BLOCKED/REVIEW semantics | 3I.4 dependency | Decomposition step |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `run_purged_walk_forward` | REQUIRES_OPEN_TO_OPEN_ADAPTER | calls `run_vectorized_backtest()` (close-to-close) | superseded as public contract by `OpenToOpenWalkForwardProtocolV1` (§5); `purged_walk_forward_splits` may be reused internally only if timestamp/index equivalence is proven | §4.B daily series per fold | 365 | UTC wall-clock timestamp, snapped to UTC calendar-day boundaries (§5) | `purge_bars = holding_horizon_bars`, owner-approved (§5.1) | `embargo_bars = holding_horizon_bars`, owner-approved (§5.2) | final 20% of elapsed UTC span, snapped to next `00:00 UTC`; min 30 holdout days + 90 pre-holdout days else `UNAVAILABLE` (§6) | n/a | n/a | n/a | n/a | n/a | fold-level BLOCKED on purge violation; holdout `UNAVAILABLE` if minimums unmet | none | .2b.2b.2 |
| 2 | `purged_walk_forward_splits` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | pure integer-index generator, no accounting semantics; not the canonical fold authority (§5) | may back `OpenToOpenWalkForwardProtocolV1`'s internal bar-index arithmetic only if exact equivalence to the timestamp protocol is proven by test | n/a | n/a | 1m tradable-bar index (internal use only) | as above | as above | n/a | n/a | n/a | n/a | n/a | n/a | n/a | none | .2b.2b.2 |
| 3 | `evaluate_capacity` | DEFER / BLOCKED UNTIL AUTHORIZED VOLUME-UNIT + EXECUTION EVIDENCE (owner decided) | close-to-close via `run_vectorized_backtest()`; **and** OHLCV `volume` has no authorized `volume_unit`/`volume_asset`/contracts-vs-notional semantics at the tradable-bar boundary — any capacity number built on it would rest on an unverified unit guess | none implemented in v1; explicit deterministic `capacity = UNAVAILABLE/BLOCKED, reason=MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS` evidence artifact only (§11) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | EXECUTION family, permanently BLOCKED | can never exceed BLOCKED without a new authorized volume-unit authority | yes, permanently | .2b.2b.1 (blocked-evidence artifact only) |
| 4 | `evaluate_slippage_sensitivity` | REQUIRES_OPEN_TO_OPEN_ADAPTER | close-to-close via `run_vectorized_backtest()` | §8 cost-sensitivity adapter, owner-approved 1.0x/1.5x/2.0x/3.0x | §4.A trade returns, cost-adjusted | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | EXECUTION family | evidence only, never promotes | no | .2b.2b.1 |
| 5 | `evaluate_latency_sensitivity` | REQUIRES_OPEN_TO_OPEN_ADAPTER | close-to-close bar-shift model | §9 delayed-entry adapter, owner-approved 0/1/5/15 min, labeled `COARSE_1M_GRID_LATENCY_STRESS` | §4.A trade returns, cost-adjusted | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | EXECUTION family | evidence only; not a real-venue latency claim | no | .2b.2b.1 |
| 6 | `evaluate_bootstrap` | REQUIRES_METHODOLOGY_CORRECTION | hardcoded `sqrt(252)`, no `periods_per_year` param | add defaulted `periods_per_year=252` param, owner-approved Option A (§12); OPEN→OPEN caller passes 365; regression test proves legacy 252 output bit-for-bit unchanged | §4.B daily series | 365 (OPEN→OPEN caller) / 252 (legacy, unchanged) | n/a | n/a | n/a | never applied to holdout post-freeze | n/a | n/a | n/a | n/a | PERFORMANCE family (robustness dist.) | evidence only | no | .2b.2b.1 |
| 7 | `evaluate_monte_carlo_trade_sequence` | REUSE_UNCHANGED, owner-confirmed | confirmed: accepts raw `trade_returns` tuple, no close/backtest dependency | none | §4.A trade returns (never the §4.B daily series) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | ROBUSTNESS family | evidence only | no | .2b.2b.1 |
| 8 | `evaluate_stress` | REQUIRES_OPEN_TO_OPEN_ADAPTER | close-to-close terminal-bar shock via `run_vectorized_backtest()` | §10 OPEN→OPEN stress adapters (cost deterioration, exit-price shock, missing-bar/data-gap); reduced-liquidity sub-category BLOCKED (same reason as row 3) | §4.A trade returns, cost/price-adjusted | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | RISK family | evidence only; liquidity sub-case BLOCKED | liquidity sub-case only | .2b.2b.1 |
| 9 | `evaluate_parameter_stability` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | pure diagnostic scorer over caller-supplied `ParameterResult`s, no backtest call | supply OPEN→OPEN `ParameterResult`s from §7's baseline + 6-neighbor grid (baseline supplied by caller, not hardcoded) | §4.B daily series (Sharpe/return per neighbor) | 365 | n/a | n/a | n/a | never optimizes against holdout | n/a | n/a | n/a | n/a | ROBUSTNESS family | evidence only, never replaces baseline; every run enters `ResearchTrialLedgerV1` | no | .2b.2b.2 |
| 10 | `evaluate_multiple_testing` (BH/FDR portion only) | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | generic BH step-up over any p-value dict; prefer calling `research_validation.benjamini_hochberg` directly over the inline duplicate | supply full trial p-value dict sourced from `ResearchTrialLedgerV1` (§15) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | ROBUSTNESS family | evidence only | no | .2b.2b.2 |
| 11 | `backtest_overfitting_probability` field | REQUIRES_METHODOLOGY_CORRECTION | not canonical CSCV PBO — literal boolean failure-rate | new, separate CSCV PBO artifact, implemented now (owner decided, §16); do not reuse/relabel this field | §4.B daily series across `ResearchTrialLedgerV1` trial matrix | 365 | 8 equal contiguous chronological blocks (`cscv_blocks=8`) | n/a | n/a | trials never include holdout | n/a | n/a | CSCV: `C(8,4)=70` splits, IS/OOS Sharpe per §13A.1 (`ddof=1` sample std), IS winner (ties broken by canonical trial content hash, never OOS), OOS rank (midrank on ties), `omega_c=oos_rank/(N+1)`, `lambda_c=ln(omega_c/(1-omega_c))`, fraction `λ<=0` (§16.1-§16.3); gate: ≥8 non-empty blocks, ≥5 daily obs/block, ≥6 distinct trials; zero-variance/undefined-Sharpe splits (`T<2` or `s==0`, §13A.1) invalid and excluded (reason recorded, never `Sharpe=0`); all 70 splits required valid for numeric v1 PBO, else UNAVAILABLE | n/a | ROBUSTNESS family, may report UNAVAILABLE | `UNAVAILABLE` if eligibility gate unmet, never fabricated | no | .2b.2b.1 |
| 12 | `deflated_sharpe_probability` field | REQUIRES_METHODOLOGY_CORRECTION | simplified Gaussian diagnostic, not canonical DSR (no skew/kurtosis/per-trial variance) | new `DeflatedSharpeEvidenceV1` artifact (§17); do not relabel existing field | §4.B daily series only (owner decided — never §4.A trade returns), evaluated on pre-holdout research/OOS evidence | 365 | n/a | n/a | n/a | never uses holdout to tune methodology | n/a | n/a | n/a | canonical DSR: exact v1 formula pinned in §17.1-§17.5, using the shared estimator contract in §13A — non-annualized `SR_hat` (`ddof=1` sample std, §13A.1), `gamma3`/`gamma4` = bias-corrected sample skewness/Pearson kurtosis (§13A.2; convert library excess kurtosis via `+3` if a library function is used instead), `mu_SR`/`sigma_SR` = trial-Sharpe mean/`ddof=1` sample std (§13A.3), Euler-Mascheroni `SR_star` expected-max-Sharpe over `N` trial Sharpes from `ResearchTrialLedgerV1`, eligibility floor `T>=30`/`N>=6`/finite `sigma_SR`/`denominator>0`, else UNAVAILABLE | ROBUSTNESS family, may report UNAVAILABLE | `UNAVAILABLE` if observations/trials insufficient | no | .2b.2b.1 |
| 13 | `performance_metrics` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT — daily series only | already parameterized `periods_per_year`; must never be called with an irregular trade-return sequence (owner-corrected, §18) | call once with §4.B daily series, `periods_per_year=365`, for daily metrics only | §4.B daily series only | 365 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | PERFORMANCE family (daily) | n/a | no | .2b.2b.2 |
| 13a | `trade_return_metrics_v1` (new, owner-directed) | new artifact | needed because `performance_metrics` would give trade returns a false annualized-period semantics | new small non-annualized helper: trade count, hit rate, average/median trade, win/loss ratio, payoff ratio, profit factor, win/loss distribution (§18) | §4.A trade returns | none (no annualized field exists) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | PERFORMANCE family (trade-level) | n/a | no | .2b.2b.1 |
| 14 | `tail_risk_metrics` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | pure quantile calc, no annualization, no close-to-close dependency | call with §4.A trade returns; resulting metrics dimensioned `TRADE_LEVEL` (§18) | §4.A trade returns | n/a (no annualization) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | RISK family, dimensioned `TRADE_LEVEL` | n/a | no | .2b.2b.2 |
| 15 | `complexity_components` | REUSE_UNCHANGED | pure parameter-count/turnover/sample-size penalty, no temporal semantics | none | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | non-authoritative navigation aid, unchanged | n/a | no | .2b.2b.2 |
| 16 | `StrategyScorecardV2` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | schema already fits (no PASSED state, family/state enums sufficient); status forced BLOCKED via existing `dataset_health_status` invariant | populate `dimensions` to distinguish daily vs. `TRADE_LEVEL` metrics; set `dataset_health_status="BLOCKED"`; limitations include realized-on-exit risk-path caveat | both §4.A and §4.B, distinctly dimensioned | 365 (daily fields only) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | see row above | status always BLOCKED, unconditionally, for all 3J.2b.2b fixture-only runs | yes (structural cap) | .2b.2b.2 |
| 17 | `build_validation_package` | REUSE_WITH_EXPLICIT_INPUT_CONTRACT | only checks completeness + hash format, not per-category `passed`; `promotion_status` always `"REVIEW_REQUIRED_OR_BLOCKED"` | caller supplies explicit blocked/failed evidence hash for both `execution_realism` and `capacity`; caller, not this function, enforces pass/fail semantics | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | package exists, overall state still BLOCKED; completeness ≠ promotion eligibility | `execution_realism` and `capacity` both permanently blocked | yes | .2b.2b.2 |
| 18 | Golden/vector reconciliation (`cross_engine.py`) | REQUIRES_OPEN_TO_OPEN_ADAPTER | built around `run_vectorized_backtest()` close-to-close comparison; semantics do not match OPEN→OPEN | new minimal independent arithmetic path (§20), not the existing golden engine; must not call `compute_signed_open_to_open_return()` | direction/entry/exit/gross/entry-cost/exit-cost/net, all independently recomputed | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | any unexplained difference → whole package BLOCKED | no | .2b.2b.1 |
| 19 | `run_vectorized_backtest()` | not reusable (excluded by design) | hard-coded close-to-close accounting, `sqrt(252)` Sharpe; architecturally incompatible with OPEN→OPEN, non-overlapping discrete trades | n/a — must never be called from any 3J.2b.2b adapter | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | no | n/a |
| 20 | Synthetic no-edge control (new) | new artifact | required before any edge claim; ordinary permutation destroys basis autocorrelation and is too easy to beat (owner decided, §14) | primary: deterministic circular time-shift of basis values, non-zero offsets only, `target_null_runs=999`, `actual_null_runs=min(999, eligible_feature_event_count-1)`, min 99 valid shifts else `UNAVAILABLE`; secondary diagnostic only: ordinary full permutation | §4.B daily series (Sharpe statistic) | 365 | n/a | n/a | n/a | never touches holdout | primary: deterministic circular shift, fixed seed; secondary (diagnostic only): full permutation | `(1+count(null_sharpe>=observed_sharpe))/(1+actual_null_runs)` (§14) | n/a | n/a | ROBUSTNESS family | required precondition for any edge claim; `UNAVAILABLE` if <99 valid shifts | no | .2b.2b.1 |
| 21 | `ResearchTrialLedgerV1` (new, owner-directed) | new artifact | trial counts must not silently disappear from BH/FDR, CSCV PBO, or DSR (§15) | deterministic in-memory content-hashed ledger of every performance-bearing trial (baseline, 6 neighbors, any additional inspected/rejected trial); excludes architecture-only discussions, untested alternatives, unit-test fixtures, and null-control runs | n/a | n/a | n/a | n/a | n/a | n/a | n/a | shared trial-count source for rows 10, 11, 12 | shared trial-count source for rows 10, 11, 12 | n/a | n/a | no | .2b.2b.1 |

## 24. Architecture items still marked REQUIRES REVIEW

No item from the prior revision remains open after this revision — embargo
width, holdout boundary, baseline-parameter handling, null-run count, DSR
series choice, latency levels, historical trial accounting, the
mark-to-market alternative, and whether to implement canonical PBO now have
all been resolved by explicit owner decision above (§5.2, §6, §7, §14, §17,
§9, §15, §4.C, §16 respectively).

**No architecture item blocks 3J.2b.2b.1.** This is a distinct statement
from the capacity gap recorded below: the two coexist by design —

```
No architecture item blocks 3J.2b.2b.1.
```

and

```
Capacity remains intentionally unavailable until a separately reviewed
authority resolves volume-unit semantics.
```

The capacity/liquidity data-authority gap (item 1 below) is a known
external/future authority gap, not an architecture blocker for starting
3J.2b.2b.1 — `evaluate_capacity`'s reclassification to explicit `BLOCKED`
evidence (§11) is itself the v1 architecture decision, and it is fully
specified and ready to implement now.

One item surfaced *during* this revision that was not present in the prior
REQUIRES REVIEW list, and is recorded here rather than resolved silently:

1. **Capacity/liquidity data-authority gap (§11).** The historical OHLCV normalization's `volume` field has no authorized `volume_unit`/`volume_asset`/contracts-vs-notional discriminator at the tradable-bar evidence boundary. This blocks not only `evaluate_capacity`'s reuse (row 3) but also the §10 reduced-liquidity stress sub-category (row 8) — both must render `BLOCKED`/`MISSING_AUTHORIZED_VOLUME_UNIT_SEMANTICS` rather than an estimate. Unblocking this requires a separately reviewed authority establishing explicit venue/tradable-size unit semantics (and/or 3I.4); no implementation or schema decision is proposed here, since resolving it is out of this module's scope.

## Scope exclusions

No code. No migration. No parameter optimization. No strategy change. No new
feature. No open-interest/funding input. No real provider. No paper trading.
No shadow/live. No 3I.4 implementation. No performance/alpha claim.
