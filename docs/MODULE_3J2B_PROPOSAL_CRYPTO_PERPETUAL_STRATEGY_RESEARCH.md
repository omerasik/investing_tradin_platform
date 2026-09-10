# Module 3J.2b (Proposal): First Deterministic Crypto Perpetual Strategy Research Architecture

Status: **proposal only — not implemented, not authorized, not started.** No code,
migration, schema, strategy, signal, or test in this repository changes as a
result of this document. This is documentation/architecture analysis only:
Python, migrations, tables, execution, and risk are explicitly out of scope
for this PR. The goal is to determine whether the crypto-perpetual research
path is actually research-complete end to end, and if not, to say precisely
what is missing, before any implementation PR is opened.

## 0. Precondition

This proposal is written against exact current main
`f7bdfd6e44456648d483d9a3fb23a9aeb48786fe` (local and `origin/main` verified
identical, working tree clean at the time of writing). It assumes:

- **3J.1** (all seven `DERIVATIVES` feature definitions —
  `futures_front_back_normalized_spread`, `futures_annualized_calendar_spread_rate`,
  `futures_curve_curvature`, `open_interest_change`, `crypto_mark_index_basis`,
  `crypto_realized_funding_annualized`, `crypto_funding_forecast_error`) is
  merged and exact-main verified: 3J.1a on `321738df710876bfc0d87b5b94fdcf446dddb99e`,
  3J.1b on `227d429d419e0a31de86138c804fdc0556ec0df4`, 3J.1c on
  `f2ae9931a5373e4cccab0b0034aeac9627d2cb71` (see
  [MASTER_ROADMAP.md](MASTER_ROADMAP.md)).
- **3J.2a** (Subject-Aware Strategy Lab Feature Binding V2 —
  `src/trade_platform/strategy_feature_binding_v2.py`) is merged and
  exact-main verified on `31d38d18beb923ac1949120354a3dc17a83e5e06`.

3J.2b is the first module that would actually connect this evidence to a
concrete, tradable strategy hypothesis. Everything below is an evaluation of
whether that connection is currently sound, not a claim that it is built.

## 1. Architecture objective

Design the first genuinely PIT-safe, deterministic derivatives Strategy Lab
research path:

```
sealed data → FeatureMaterializationV2 → SubjectAwareResearchFeatureBundle
  → strategy decision → strictly later tradable entry → position/accounting
  → exit → returns → walk-forward / robustness / scorecard
```

Subject for v1: `FeatureSubjectType.INSTRUMENT`. Asset: crypto `PERPETUAL`.
Futures series are explicitly out of scope for this first strategy — per
owner direction, `FUTURES_SERIES → tradable contract → roll → realized P&L`
requires a separately reviewed roll/mapping authority that does not exist yet
(3I.1 §9 already deferred this), whereas a crypto perpetual's feature subject
(`INSTRUMENT`) and its tradable instrument are the same object, with no roll
step at all.

## 2. Candidate feature set — role classification

All four facts below are read directly from
`src/trade_platform/crypto_derivatives_features.py`,
`src/trade_platform/open_interest_features.py`, and `feature_authority.py`,
not inferred from the 3J.1 proposal text.

| Feature | Subject / instrument gate | `knowledge_at` | Role for v1 |
|---|---|---|---|
| `crypto_mark_index_basis` | `INSTRUMENT`; requires `ReferencePriceRequirement.MARK_AND_INDEX` | `max(mark.normalized_at, index.normalized_at, dataset.created_at)`, fails closed if `> decision_at` | **Primary signal** |
| `open_interest_change` | `INSTRUMENT`; no crypto-kind gate (shared futures/crypto authority) | `max(current.normalized_at, prior.normalized_at, dataset.created_at)` | **Confirmation/filter** |
| `crypto_realized_funding_annualized` | `INSTRUMENT`; requires `CryptoInstrumentKind.PERPETUAL` | `max(realized.normalized_at, dataset.created_at)` — bounded below by the realized observation's own knowledge time, because the row must already exist for the calculator to produce a value at all | **Regime/context** (subsequent decisions only) |
| `crypto_funding_forecast_error` | `INSTRUMENT`; requires `CryptoInstrumentKind.PERPETUAL` | `max(realized.normalized_at, indicative.normalized_at, dataset.created_at)` — realized value is looked up and required *before* the indicative side is even queried | **Defer from v1** |

**`crypto_mark_index_basis` is primary** because it is the only one of the
four whose knowledge time is bounded by an *event-time* pair (mark and index
observed at the same instant), not by a *realization* event. It is knowable
essentially as of the moment it happens, which gives it the cleanest
causal/PIT interpretation of the four and makes it the natural state variable
for a convergence hypothesis.

**`open_interest_change` is a confirmation/filter**, not primary, because an
OI delta describes positioning change, not price dislocation; it is used
here only to attenuate or confirm a basis-driven entry (e.g. large OI buildup
concurrent with a large basis reading suggests crowded, not yet unwinding,
positioning), never as the entry trigger itself.

**`crypto_realized_funding_annualized` is regime/context, restricted to
subsequent decisions.** Per the codebase's own PIT gate, this feature cannot
be visible before the realized funding observation it derives from is itself
knowable (`crypto_derivatives_features.py`, `_select_funding` returns `None`
if the realized row does not exist yet, and `knowledge_at` is bounded below
by that row's `normalized_at`). A strategy may therefore use *already-known*
realized-funding history as a state variable for a decision made strictly
after that history was published — e.g. "was the last known funding regime
positive or negative" — but must never treat a realized funding print as
something the *current* decision could still capture, and must never
substitute it for a forward funding estimate.

**`crypto_funding_forecast_error` is deferred from v1.** It requires *both*
the realized and a prior indicative observation, is doubly lagged relative to
`crypto_mark_index_basis`, and its natural role — a crowding/mispricing
diagnostic for a funding-persistence hypothesis — belongs to Hypothesis B/C
below, not to the minimal basis-reversion hypothesis this proposal
recommends. It remains available, unmodified, for a later research
iteration; nothing here removes or alters it.

## 3. Critical accounting question — resolved

**Inspection finding (not inferred from feature existence): the existing
research/backtest accounting stack does not model perpetual funding
cashflows in realized strategy P&L today.**

- The canonical vectorized engine, `run_vectorized_backtest()`
  (`src/trade_platform/research.py:348-369`), computes
  `gross = position * (close_t / close_t-1 - 1)`, `net = gross -
  CostModel.cost(turnover)`, `equity *= 1 + net`. No funding, carry, or
  cashflow term appears anywhere in this function. `trend_research_v2.py`
  calls this exact function and contains zero references to funding.
- The event-driven engine, `EventDrivenBacktester`
  (`src/trade_platform/event_backtest.py:136`), crosses a simulated bid/ask
  plus market impact and fees, but its `EventBacktestResult` tracks only
  `total_fees`, `net_cash_change`, `positions`, `cash` — no funding field.
- `CostModel` (`research.py:115-125`) is `fixed_per_turnover +
  turnover*(percentage_per_turnover + spread_fraction_per_turnover)` — a
  flat, versioned, researcher-supplied assumption, stress-tested via a
  `pessimistic_cost_multiplier` scenario (`research.py:255-256`). This is the
  "conservative versioned transaction-cost assumption" referenced in §14
  below, not realistic top-of-book spread.
- A cashflow-adjustment primitive *does* exist —
  `apply_funding(position, cash, mark_price, funding_rate) -> CashBalance`
  (`src/trade_platform/paper_execution.py:96-100`), sitting alongside
  `apply_dividend`/`apply_borrow_cost` in the same cash-ledger module — but it
  is dead code with respect to the research/backtest path: its only caller in
  the entire repository is its own unit test
  (`tests/test_paper_execution.py`). It is not invoked from
  `run_vectorized_backtest`, `EventDrivenBacktester`, `trend_research_v2.py`,
  or `cross_engine.py`.
- `docs/MODULE_3I2_CRYPTO_FUNDING_MARK_INDEX_OPEN_INTEREST.md` (lines
  16-19, 110-116) is explicit that funding rates are captured strictly as
  historical market-data/feature evidence — any forward-looking or
  accounting use "belongs to Feature Authority / Model Registry / strategy
  research," not to the market-data authority itself.
- `docs/BACKTESTING_STANDARD.md` (line 3) names funding as an in-scope cost
  category "as applicable" at the aspirational/normative level, but no
  implementation exists yet.

**Conclusion:** `crypto_realized_funding_annualized` and
`crypto_funding_forecast_error` are signal features today, not realized
return accounting. A strategy built on current main cannot honestly be
labelled a funding/carry strategy, because funding cashflows are absent from
its computed P&L.

## 4. Accounting architecture decision — Architecture 1 recommended

**Recommendation: Architecture 1 — price-return-first.**

The first strategy (§7 below) is explicitly a basis/dislocation strategy.
Its research P&L is price return only (via the existing
`run_vectorized_backtest`/`EventDrivenBacktester` engines and `CostModel`),
excluding funding income/expense entirely. `crypto_realized_funding_annualized`
and `crypto_funding_forecast_error` (when reintroduced later) are used only
as historical state/filter variables per §2 — never as a component of
realized return. **Any "funding carry" performance claim for this strategy
is prohibited**, and the strategy's own documentation/scorecard metadata
must say so explicitly once implemented.

Why not Architecture 2 now: the recommended first hypothesis (§7,
Hypothesis A) does not require funding cashflows to be a legitimate,
honestly-labelled strategy — its edge claim is basis convergence, not
funding capture. Building a new, separately-reviewed funding-aware
accounting primitive before validating that the simpler price-only path is
even research-complete would front-load risk and scope onto a strategy that
doesn't need it. Architecture 2 remains available for a *future*,
explicitly funding-carry-labelled hypothesis (Hypothesis B/C's natural
domain).

### 4.1 If Architecture 2 is pursued later — semantics that must first be resolved

This is recorded now, while the investigation is fresh, as a **blocking
architecture gap**, not a spec for this PR:

- **Sign convention — REQUIRES REVIEW, currently blocking.** Grepping the
  entire 3H.2/3I.2/Feature-Authority layer (`crypto_instruments.py`,
  `feature_authority.py`, `crypto_derivatives_features.py`,
  `docs/MODULE_3H2_CRYPTO_INSTRUMENT_AUTHORITY.md`,
  `docs/MODULE_3I2_CRYPTO_FUNDING_MARK_INDEX_OPEN_INTEREST.md`) finds **no**
  documented or code-enforced statement of which side of a perpetual pays
  funding when the rate is positive. The instrument-ingestion parser
  explicitly *declines* to assert one
  (`src/trade_platform/crypto_market_observations.py:203-205`: "A funding
  rate is legitimately negative -- that is shorts paying longs, not bad
  data -- so there is no sign check here.") The only place in the repository
  that states a convention is `apply_funding()`'s docstring in
  `paper_execution.py:97` ("Positive funding rate is paid by a long and
  received by a short") — an isolated, unwired paper-trading simulation
  primitive that is not part of the 3H.2 instrument authority, is not
  reviewed as a research-accounting contract, and is not referenced by
  `crypto_derivatives_features.py` or either 3H.2/3I.2 doc. Per instruction,
  this convention must **not** be assumed correct-by-reuse for a
  research-accounting primitive without an owner decision that either (a)
  the 3H.2 authority is amended to declare it explicitly, or (b) a
  separately reviewed research-accounting contract adopts
  `paper_execution.py`'s convention deliberately, with that adoption
  recorded as its own decision.
- **Position/exposure immediately before the funding event** — must read the
  position held at the canonical funding instant, not at bar close, not at
  decision time; requires a per-instant position query the current
  bar-oriented engines do not expose today.
- **Canonical realized funding observation** — `FUNDING_RATE_REALIZED` at
  the resolved 3H.2 convention's funding instant (already unambiguous;
  `crypto_realized_funding_annualized`'s own resolution logic is reusable
  evidence, not a new decision).
- **Target funding timestamp, settlement asset, notional basis, mark
  reference price** — must all be pinned to the exact fields already carried
  by `crypto_funding_observations` and the resolved convention; no new
  "notional as of" ambiguity should be introduced.
- **PIT visibility of the payment itself** — must equal the realized
  observation's own `knowledge_at`, never earlier.
- **Position opened after the funding cutoff** — must not receive the
  payment; this is a distinct edge case from PIT visibility and needs its
  own explicit rule (see negative-test plan, §11).
- **How funding enters period return/equity, and how it is separated from
  trading P&L and fees** — needs a new field on `BacktestResult`
  (`research.py:129`) and `EventBacktestResult` (`event_backtest.py:101`)
  analogous to how `apply_corporate_actions()` is already threaded through
  `EventDrivenBacktester` (`event_backtest.py:203-228`), reconciled between
  engines via `cross_engine.py`.

None of this is implemented, specified in full, or authorized here. It is
recorded so a future Architecture 2 proposal does not have to re-derive it.

## 5. Tradable return stream

**Feature evidence** (§2) is `MARK_PRICE`/`INDEX_PRICE`/`FUNDING_RATE_*`/
open-interest observations — never executable prices. **Tradable return
evidence** is OHLCV/tradable-bar observations for the same perpetual
instrument. The two must not be conflated: index price is not executable,
mark price is not necessarily executable, and the basis feature itself is a
derived ratio, not a return series.

**Data-availability gap (blocking, must be closed before any implementation
PR, independent of the accounting-architecture decision in §4):** inspection
of `historical_market_data.py`, the crypto fixtures in
`tests/test_crypto_market_observations_postgres.py`, and
`crypto_derivatives_features.py` shows that **no crypto OHLCV/tradable-bar
ingestion path, fixture, or test currently exists for any crypto perpetual
instrument.** The dedicated crypto source fixture is authorized only for
`{FUNDING_RATE_REALIZED, FUNDING_RATE_INDICATIVE, MARK_PRICE, INDEX_PRICE,
OPEN_INTEREST}` — `OHLCV` is absent. Crypto historical data today is event
data only. This must be closed (new source authorization + ingested/sealed
OHLCV evidence for at least one `PERPETUAL` test fixture) before a walk-forward
backtest of any crypto strategy can run against real evidence shapes, not
just before this specific hypothesis.

## 6. Dataset identity problem

3J.2a enforces exactly one sealed `historical_dataset_versions.dataset_version_id`
per `SubjectAwareResearchFeatureBundle`
(`tests/test_strategy_feature_binding_v2.py::test_cross_dataset_feature_rejected`
and `::test_bundle_mixing_two_dataset_uuids_rejected`). Both tests are pure
`dataset_version` string/UUID equality checks on caller-supplied tags — they
have no awareness of which `ObservationKind`s a given sealed dataset actually
contains.

**Inspection finding: `seal_dataset()` (`historical_market_data.py:939-1009`)
enforces only two identity constraints on its members — identical `source_id`
and identical `normalization_version`. It does not check `instrument_id` or
`observation_kind`.** `AssetScope.CRYPTO`'s `SCOPE_ELIGIBLE_KINDS`
(`historical_market_data.py:167-176`) already includes both `OHLCV` and the
four derivative kinds together, so one `AuthorizedHistoricalSource` is
schema-legally permitted to carry capability for both. **Yes, one sealed
dataset can legitimately contain both the 3J.1 crypto feature evidence and
OHLCV/tradable-price observations for the same perpetual** — but nothing in
the codebase does this today (§5's gap), and there is a second, structural
reason it is not simply a data-loading exercise:

**Composite-evidence architecture requirement (REQUIRES REVIEW, not
implemented here).** OHLCV's real downstream consumer path today is
`historical_bar_bridge.py` → `PostgresHistoricalBarStore` (Module 3F) — a
structurally separate subsystem from `historical_dataset_members` /
`historical_dataset_versions`, which is the path `crypto_derivatives_features.py`
and `SubjectAwareResearchFeatureBundle` read. Even where OHLCV exists
(equities, futures), it does not flow through the same
`dataset_version_id`-scoped `FeatureMaterializationV2` mechanism the
derivatives features use. So "one dataset_version_id can hold both" is true
at the schema layer but does not by itself give a strategy a single,
provably-consistent evidence source — a genuine composite-evidence decision
is required, not a hidden workaround.

Two candidate resolutions, per instruction, without hiding cross-dataset
mixing inside the strategy implementation:

- **Option A (recommended smallest safe solution):** extend one crypto
  `AuthorizedHistoricalSource`'s `authorized_observation_kinds` to include
  `OHLCV` alongside the existing derivative kinds, ingest and normalize both
  under that one `source_id`, and call `seal_dataset()` once with
  `normalized_ids` spanning both kinds. This reuses the existing
  single-sealed-dataset rule with **zero new authority, zero schema change**
  — it is purely an ingestion/source-registration decision. Its own open
  question: whether a bar-oriented reader analogous to
  `FeatureAuthorityReaderV2` needs to exist so `SubjectAwareResearchFeatureBundle`
  (or a sibling artifact) can absorb price evidence alongside feature
  evidence under the same bundle, or whether the strategy legitimately reads
  price bars through a separate, dataset-ID-tagged query and only asserts
  (not derives) the shared `dataset_version_id` at the boundary.
- **Option B (fallback, explicitly deferred):** if real venue plumbing makes
  single-source ingestion of OHLCV and mark/index/funding infeasible (e.g.
  they genuinely come from different provider endpoints), introduce a
  separately reviewed composite-evidence/dataset-linkage authority — mirrors
  3J.1 §9's own deferral of "any feature combining multiple sealed dataset
  versions." **Not built here; requires its own owner-reviewed proposal if
  Option A proves infeasible.**

This proposal does not choose between A and B unilaterally — Option A is
recommended as the default because it needs no new authority, but the final
call depends on whether real crypto OHLCV ingestion can in fact share a
source registration with the existing derivatives-event ingestion, which is
an operational/provider question outside this document's scope.

## 7. Hypothesis comparison

| # | Hypothesis | Primary driver | Assumptions | PIT cleanliness |
|---|---|---|---|---|
| A | Basis mean reversion | `crypto_mark_index_basis` | Basis dislocations revert; OI/funding as optional filters only | Cleanest — primary signal has event-time knowledge, no realization lag |
| B | Funding persistence/crowding | `crypto_realized_funding_annualized` + OI/basis state | Realized funding history predicts subsequent direction/convergence; crowding is a real, exploitable regime | Primary driver is inherently lagged (funding realizes only every N hours); more causal assumptions |
| C | Combined basis + funding regime | `crypto_mark_index_basis` (primary) + funding/OI (regime) | Superset of A, adds regime conditioning | Same PIT cleanliness as A at the primary-signal level, but more free parameters and moving parts |

**Recommendation: Hypothesis A, basis mean reversion**, optionally filtered
by `open_interest_change`, with `crypto_realized_funding_annualized` reserved
as regime/context for a later iteration rather than built into v1. This is
the hypothesis with the fewest assumptions and the cleanest causal/PIT
interpretation, per instruction — not the one that sounds most profitable.
Hypothesis C is the natural second iteration once A has a validated baseline;
Hypothesis B is the natural domain for `crypto_funding_forecast_error`
(deferred from v1, §2) and for Architecture 2 (§4.1) if that is ever pursued.
Neither B nor C is recommended for v1.

## 8. Signal-to-entry timing

Strict rule: `entry_time > max(feature knowledge_at values, decision_at)`.
A feature observation at timestamp `t` never permits an entry at price `t`.

**v1 rule:** decisions are evaluated once per OHLCV bar close (once that
evidence exists per §5/§6). `decision_at = bar_close_time` for the bar whose
close triggered re-evaluation, gated by requiring every consumed feature's
`knowledge_at <= decision_at` (already enforced per-feature by
`crypto_derivatives_features.py`/`open_interest_features.py`, and at the
bundle level by `SubjectAwareResearchFeatureBundle`). `entry_time` is the
**first eligible tradable OHLCV observation strictly after `decision_at`**
— i.e. the next bar's open, not same-close execution. No look-ahead through
same-timestamp mark/index, realized funding, OI revision, bar close, or
normalization time: every one of those must independently satisfy its own
`knowledge_at <= decision_at` before the decision is made, and price
evidence used for entry must have its own timestamp strictly after
`decision_at`, never merely after the feature's `event_at`.

## 9. Position direction

Basis mean reversion requires **long, short, and flat**: a positive basis
(mark above index) implies shorting the perpetual toward convergence; a
negative basis implies going long; magnitude below the entry threshold means
flat. This is a real requirement, not a stylistic preference — a long-only
proxy cannot express the short side of a symmetric convergence hypothesis.

**Inspection finding: the current research stack does not support signed
exposure safely.** `TrendSignalObservation.validate()`
(`trend_strategy_v2.py:194`) asserts `0 <= exposure <= maximum_exposure`,
and all four signal-generation functions in `trend_strategy_v2.py`
(`time_series_momentum_signals`, `breakout_signals`,
`multi_horizon_trend_signals`, `volatility_scaled_trend_exposure`) clamp or
floor output at `Decimal("0")`. `TrendStrategyDefinitionV2.maximum_research_exposure`
is itself constrained to `(0, 1]`. `Decimal` itself is not the blocker (it
supports negative values natively) — the blocker is these explicit
non-negative invariants and floor-at-zero transforms. No reusable signed
exposure/direction abstraction exists elsewhere (`OrderSide` in
`domain.py:31-33` is a `BUY`/`SELL` order-intent enum, not a strategy
exposure-sign concept).

**Do not fake short exposure through Trend V2's long-only abstraction.**
Trend V2's non-negative invariant is deliberate, and other existing
consumers may rely on it; retrofitting it to accept negative exposure risks
silently changing meaning for unrelated long-only trend strategies. The
minimum generalized change is a **new, minimal signed-exposure research
abstraction** (structurally parallel to, but independent from,
`TrendStrategyDefinitionV2`/`TrendSignalObservation`) whose exposure field
accepts `[-cap, +cap]` and whose transform functions are permitted to return
negative values. This is a real, reviewable code change — **not implemented
in this PR** and explicitly assigned to the implementation decomposition
(§16) rather than left implicit.

## 10. Holding and exit semantics

| Candidate | Parameters needed | Notes |
|---|---|---|
| Fixed N-bar horizon | 1 (`N`) | Simplest, fully auditable, no path-dependent detection logic |
| Next funding event | 0 (derived from convention) | Economically motivated for a funding-adjacent hypothesis, not this one |
| Basis convergence threshold | 1 (`threshold`) | Economically motivated for Hypothesis A specifically; needs threshold-crossing detection |
| Time stop | 1 | Usually paired with another exit as a safety bound, not standalone |
| Signal reversal | 0 (derived) | Couples exit tightly to entry-signal noise; less auditable |

**Recommendation: fixed N-bar holding horizon**, the single deterministic
exit condition for v1. This keeps the parameter surface minimal (one
parameter, no threshold-crossing state machine) and the model maximally
auditable for a first strategy. Basis-convergence-threshold is the natural
v1.1 enhancement once a fixed-horizon baseline exists and provides a
convergence-timing prior; it is **not** adopted for v1, to avoid combining
multiple optional exits before there is a reason to.

## 11. Parameter policy

Proposal only — a small, preregistered, coarse set, no optimization, no
Bayesian search, no grid, no "best Sharpe" selection:

1. `basis_entry_threshold` — dimensionless; enter when `|basis| > threshold`.
2. `holding_horizon_bars` — integer `N`, the fixed exit horizon (§10).
3. `exposure_cap` — maximum absolute signed exposure, reusing the existing
   cap concept generalized per §9.
4. `oi_change_confirmation_threshold` — optional; if set, requires
   `open_interest_change` to corroborate direction/magnitude before entry;
   `None`/off is a valid v1 configuration.

Implementation must start with one small coarse set across these four
parameters — no parameter sweep, no in-sample selection.

## 12. Validation design

Binds to the existing validation stack as-is — no new validation machinery
is proposed. Everything below already exists on current main:

| Component | Location |
|---|---|
| Chronological/walk-forward split | `WalkForwardProtocol` — `research.py:139` |
| Purge/embargo | `PurgedWalkForwardSplit`/`purged_walk_forward_splits()` — `strategy_validation.py:107,132` |
| Transaction costs (base/1.5x/2x/3x), spread/slippage sensitivity | `CostModel` — `research.py:115-125`; `pessimistic_cost_multiplier` — `research.py:255-256`; `evaluate_slippage_sensitivity` — `quant_validation.py:214` |
| Latency sensitivity | `evaluate_latency_sensitivity` — `quant_validation.py:268` |
| Bootstrap/block-bootstrap | `evaluate_bootstrap()`/`BootstrapEvidence` — `quant_validation.py:331,345` |
| Trade-order Monte Carlo | `evaluate_monte_carlo_trade_sequence()`/`MonteCarloEvidence` — `quant_validation.py:367,380` |
| Parameter stability | `evaluate_parameter_stability()` — `quant_validation.py:445,458` |
| Stress tests | `evaluate_stress()` — `quant_validation.py:412,420` |
| Multiple-testing controls (BH FDR) | `evaluate_multiple_testing()` — `quant_validation.py:478,496,504-507` |
| PBO | `backtest_overfitting_probability`, gate `<= 0.5` — `quant_validation.py:514,520` |
| DSR | `deflated_sharpe_probability`, gate `>= 0.5` — `quant_validation.py:513,520` |
| Strategy Scorecard V2 | `src/trade_platform/strategy_scorecard_v2.py:86` |
| Validation package / promotion gate | `build_validation_package()` — `quant_validation.py:706`; `PromotionStatus` (`BLOCKED`/`REVIEW_REQUIRED`) — `strategy_promotion.py:22-24` |

Synthetic/no-edge controls (a randomized-signal or shuffled-return control
run through the identical pipeline) must be included as part of the
Monte Carlo/multiple-testing evidence per `docs/QUANT_RESEARCH_STANDARD.md`
§7-17. No strategy may advance on in-sample Sharpe alone — this is already
the documented policy (`docs/QUANT_RESEARCH_STANDARD.md:15-17`) and this
proposal adds nothing to it beyond conformance.

## 13. Research lifecycle

The strategy begins, and remains, `RESEARCH_ONLY`. **Inspection finding: no
codified staged lifecycle (`RESEARCH_ONLY → signal authority → paper
eligible → shadow eligible → live eligible`) exists today.**
`TrendStrategyStatus.RESEARCH_ONLY` (`trend_strategy_v2.py:28-29,75`) is a
status marker on individual strategy definitions, and `PromotionStatus`
(`strategy_promotion.py:22-24`) has exactly two values, `BLOCKED` and
`REVIEW_REQUIRED` — there is no `PAPER_ELIGIBLE`/`SHADOW_ELIGIBLE`/
`LIVE_ELIGIBLE` state anywhere in code, and `docs/LIVE_TRADING_READINESS.md`
confirms live trading is deliberately disabled. A successful research result
under this proposal produces at most a `REVIEW_REQUIRED` validation package —
it does not, and structurally cannot today, automatically become signal,
opportunity, paper, shadow, or live eligibility. Promotion beyond
`RESEARCH_ONLY` remains separately earned and is out of scope for this
proposal and for the implementation it describes.

## 14. 3I.4 relationship

`docs/MASTER_ROADMAP.md` states 3I.4 (top-of-book quotes) "remains planned
but not authorized" — confirmed unchanged; no top-of-book/bid-ask execution
code exists anywhere in `src/trade_platform/` (the only match for
`bid_ask`/`top_of_book` is a label string inside `cross_engine.py`'s
divergence-explanation text, not an actual quote authority). Per instruction:
3I.4 is **not required** to begin historical research-only strategy
evaluation here, because the recommended cost model (`CostModel`, §3, §12)
is exactly the "conservative versioned transaction-cost assumption" that
makes 3I.4 non-blocking for `RESEARCH_ONLY` work. 3I.4 **becomes mandatory**
before promotion beyond research-only, wherever a claim depends on realistic
bid/ask spread, entry/exit execution, slippage, liquidity, or fill quality —
synthetic spread assumptions cannot earn paper/shadow/live eligibility.

## 15. Required proposal output

1. **First strategy hypothesis:** Hypothesis A, basis mean reversion (§7).
2. **Required features:** `crypto_mark_index_basis` (primary),
   `open_interest_change` (confirmation/filter, optional threshold);
   `crypto_realized_funding_annualized` and `crypto_funding_forecast_error`
   not required for v1 (§2).
3. **Feature roles:** per the table in §2.
4. **Subject type:** `FeatureSubjectType.INSTRUMENT`, crypto `PERPETUAL`.
5. **Tradable return series:** OHLCV/tradable-bar observations for the same
   perpetual — **does not exist yet**; must be ingested and sealed before
   implementation (§5).
6. **Dataset relationship:** Option A (§6) recommended — one sealed dataset
   spanning both feature evidence and OHLCV, via extended source
   authorization; Option B deferred fallback; final choice REQUIRES REVIEW
   against real provider plumbing.
7. **Decision timestamp:** `decision_at = bar_close_time`, gated by every
   consumed feature's `knowledge_at <= decision_at` (§8).
8. **Earliest entry rule:** first eligible tradable OHLCV observation
   strictly after `decision_at` — never same-close execution (§8).
9. **Long/short/flat semantics:** signed exposure required; not natively
   supported today; minimum generalized change is a new signed-exposure
   research abstraction, structurally independent of Trend V2 (§9).
10. **Exit rule:** fixed `N`-bar holding horizon (§10).
11. **Funding-cashflow treatment:** excluded from realized P&L (Architecture
    1, §4); funding features are historical state/filter only; "funding
    carry" performance claims prohibited.
12. **Cost model:** existing `CostModel` (turnover + versioned
    spread/slippage assumption), stress-tested at base/1.5x/2x/3x (§12).
13. **Minimal parameter set:** `basis_entry_threshold`,
    `holding_horizon_bars`, `exposure_cap`,
    `oi_change_confirmation_threshold` (optional) (§11).
14. **Validation protocol:** the existing walk-forward/purge/bootstrap/Monte
    Carlo/parameter-stability/stress/multiple-testing/PBO/DSR/Scorecard V2
    stack, unmodified (§12).
15. **Fail-closed conditions:** see §16.
16. **Evidence/provenance manifest:** reuses 3J.1/3J.2a's existing manifest
    conventions — bundle content hash; per-feature
    `materialization_id`/`content_hash`/`quality_status`; dataset
    `dataset_version_id`/`content_hash`/`source_id`; plus, once tradable
    price evidence exists, the analogous canonical observation ids for the
    OHLCV evidence actually consumed (exact shape depends on the §6 dataset
    decision).
17. **Implementation decomposition:** §16 below.

### Fail-closed conditions

- Any required feature's `knowledge_at > decision_at` → excluded from the
  bundle (existing 3J.2a/3J.1 behavior).
- `DEGRADED`/`REJECTED` quality materializations → excluded under
  `VALIDATED_ONLY` (existing default policy).
- Cross-dataset feature or feature/price pairing → rejected (existing
  3J.2a rule; extended, once §6 is resolved, to any price evidence bound
  under the bundle).
- Wrong subject, wrong instrument, non-`PERPETUAL` instrument kind →
  rejected (existing per-feature gates).
- Zero/negative price in either the basis computation or the tradable
  return series → rejected, no value/trade produced.
- No tradable bar available for the required exit horizon → the trade is
  **excluded from the study**, never force-closed at a synthetic/undefined
  price.
- Missing or ambiguous funding/OI observation used as a filter → filter
  treated as unavailable (existing silent-`None` behavior), never
  approximated or forward-filled.
- Signed exposure requested before the §9 generalized abstraction exists and
  is reviewed → hard fail-closed; the implementation must not silently clamp
  to long-only.

## 16. Implementation decomposition

**Recommendation: two bounded modules, not one.** Unlike the funding-P&L
question (§4), which under Architecture 1 needs no new accounting primitive,
two structurally independent prerequisites still exist — signed exposure
(§9) and composite tradable-price evidence (§5/§6) — and both are
cross-cutting infrastructure changes that deserve independent review before
the strategy logic that depends on them, consistent with how 3J.0 (identity)
was reviewed separately from 3J.1 (features) and 3J.2a (binding).

- **3J.2b.1 — Research exposure & evidence prerequisites.** (a) a new,
  minimal signed-exposure research abstraction independent of Trend V2
  (§9); (b) crypto perpetual OHLCV ingestion, source authorization, and a
  sealed dataset spanning both feature evidence and tradable bars per the
  Option A resolution in §6 (or the Option B fallback, if Option A proves
  infeasible); (c) no funding-cashflow accounting primitive — Architecture 1
  needs none.
- **3J.2b.2 — Deterministic basis-mean-reversion strategy + orchestration.**
  The Hypothesis A strategy definition itself: signal generation from
  `crypto_mark_index_basis` (+ optional `open_interest_change` filter),
  binding to 3J.2b.1's signed-exposure abstraction and composite dataset,
  the §8 decision/entry timing rule, the §10 fixed-horizon exit, and wiring
  into the unmodified validation stack (§12). Remains `RESEARCH_ONLY`
  throughout (§13).

One bounded module is not recommended: 3J.2b.1's two prerequisites are
genuinely separable from strategy logic, are each independently testable,
and (per the codebase's established pattern) each deserves its own
exact-main verification before the strategy that depends on both is
reviewed.

## 17. Required negative-test plan

Implementation (not this PR) must include tests for at least:

- feature known after decision;
- same-event execution leakage;
- realized funding used before availability;
- forecast error used before realization;
- wrong instrument;
- wrong dataset;
- cross-dataset hidden pairing (feature/feature and feature/price);
- wrong subject;
- mark/index used as executable price;
- missing tradable return bar;
- unsupported funding sign convention (blocked pending §4.1 resolution);
- funding paid to wrong position direction (only relevant if/when
  Architecture 2 is ever built);
- position opened after funding event incorrectly receiving payment (same);
- missing funding observation (filter unavailable, not approximated);
- ambiguous funding revision;
- unsupported short exposure (must fail closed until §9's abstraction
  exists);
- zero/negative prices;
- no future bar for exit (trade excluded from study, not force-closed);
- parameter-set mutation (content-hash must change);
- deterministic replay/hash mismatch;
- in-sample-only promotion attempt (must be blocked by the existing
  validation package/promotion gate).

## 18. Scope exclusions

No implementation. No strategy execution. No paper orders. No broker. No
Opportunity Radar. No portfolio allocation. No risk-policy change. No ML. No
futures strategy. No real provider calls. No alpha/performance claims.

## 19. Decision log — items requiring owner review

1. **Dataset relationship (§6)** — Option A (single extended source
   authorization) vs. Option B (composite-evidence authority), pending
   confirmation that real crypto OHLCV data can share a source registration
   with existing derivatives-event ingestion.
2. **Funding sign convention (§4.1)** — no authoritative 3H.2 convention
   exists; `paper_execution.py`'s convention is unreviewed for this purpose.
   Blocks Architecture 2 only; does not block this proposal's Architecture 1
   recommendation.
3. **Signed-exposure abstraction scope (§9)** — confirm a new, independent
   abstraction (not a Trend V2 retrofit) is the right shape before 3J.2b.1
   implementation begins.
4. **OHLCV ingestion/source authorization for crypto perpetuals (§5)** — an
   operational/provider decision, not an architecture decision, but a hard
   precondition for 3J.2b.1.

## 20. No authority granted by this document

This is analysis and a recommendation only. No feature, strategy, migration,
accounting primitive, or API described here exists in the codebase. No
strategy, signal, opportunity, order, or risk authority is implied. 3J.2b
does not begin until a separate implementation PR (or PRs, per §16) is
opened, reviewed, and merged following the same branch → PR → CI → merge →
exact-main verification discipline as every prior module.
