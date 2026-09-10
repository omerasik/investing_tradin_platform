# Module 3J.2b (Proposal): First Deterministic Crypto Perpetual Strategy Research Architecture

Status: **proposal only — not implemented, not authorized, not started.** No code,
migration, schema, strategy, signal, or test in this repository changes as a
result of this document. This is documentation/architecture analysis only:
Python, migrations, tables, execution, and risk are explicitly out of scope
for this PR. The goal is to determine whether the crypto-perpetual research
path is actually research-complete end to end, and if not, to say precisely
what is missing, before any implementation PR is opened.

**Revision note.** This revision incorporates owner review of the original
proposal (PR #116). All architecture items previously logged as REQUIRES
REVIEW in §19 have been resolved by explicit owner decision; the resolutions
are summarized in the revised §19 decision log and threaded through §2, §4,
§4.1, §5, §6, §6.1 (new), §7, §8, §9, §10, §11, §12.1 (new), §15, §16, and
§17 below. In summary: the v1 signal set is narrowed to
`crypto_mark_index_basis` alone (`open_interest_change`,
`crypto_realized_funding_annualized`, and `crypto_funding_forecast_error`
move to later preregistered extensions/ablation studies); Architecture 1 is
confirmed with the funding sign-convention question explicitly deferred, not
blocking; the dataset relationship is Option A for v1, decided; a new
dataset-bound tradable-bar research reader is added; fixture-only crypto
perpetual OHLCV activation is authorized; a new independent signed
research-exposure abstraction is approved by name; an overstated PIT claim
about basis availability is corrected; strategy decisions are made
feature-event-driven rather than bar-close-driven; the entry/exit bar-open
timestamp contract is tightened; and deterministic non-overlapping
holding/re-entry semantics are pinned. No implementation is authorized by
this revision; 3J.2b.1 does not begin automatically.

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
| `crypto_mark_index_basis` | `INSTRUMENT`; requires `ReferencePriceRequirement.MARK_AND_INDEX` | `max(mark.normalized_at, index.normalized_at, dataset.created_at)`, fails closed if `> decision_at` | **Sole v1 signal feature (OWNER DECIDED, §19)** |
| `open_interest_change` | `INSTRUMENT`; no crypto-kind gate (shared futures/crypto authority) | `max(current.normalized_at, prior.normalized_at, dataset.created_at)` | **Deferred — later preregistered extension/ablation, not in v1 (OWNER DECIDED, §19)** |
| `crypto_realized_funding_annualized` | `INSTRUMENT`; requires `CryptoInstrumentKind.PERPETUAL` | `max(realized.normalized_at, dataset.created_at)` — bounded below by the realized observation's own knowledge time, because the row must already exist for the calculator to produce a value at all | **Deferred — later preregistered extension/ablation, not in v1 (OWNER DECIDED, §19)** |
| `crypto_funding_forecast_error` | `INSTRUMENT`; requires `CryptoInstrumentKind.PERPETUAL` | `max(realized.normalized_at, indicative.normalized_at, dataset.created_at)` — realized value is looked up and required *before* the indicative side is even queried | **Deferred — later preregistered extension/ablation, not in v1 (OWNER DECIDED, §19)** |

**OWNER DECIDED: `crypto_mark_index_basis` is the sole v1 signal feature.**
3J.2b v1 requires exactly one signal feature. The first strategy must prove
the smallest possible causal/PIT chain before adding asynchronous features,
filters, or additional degrees of freedom. `open_interest_change` is **not**
an optional v1 filter (the original proposal's "confirmation/filter" framing
is superseded) — it, `crypto_realized_funding_annualized`, and
`crypto_funding_forecast_error` all move to later preregistered
extensions/ablation studies (§16, §19) and play no role, direct or as a
filter/regime input, in v1 decision-making.

**PIT statement, corrected.** The original proposal stated that
`crypto_mark_index_basis` is "knowable essentially as of the moment it
happens" — that overstates the case and is retracted. Mark and index may
share an `event_at`, but `event_at` describes only the economic observation
time. Tradability/research eligibility begins only when the feature's
`event_at`, `effective_at`, `knowledge_at`, and `computed_at` clocks are all
admissible (§8). This is still the cleanest of the four features' PIT
profiles — its knowledge time is bounded by an event-time pair rather than a
separate realization event — but "cleanest" is a relative, not an absolute,
claim.

**`open_interest_change`, `crypto_realized_funding_annualized`, and
`crypto_funding_forecast_error` are deferred from v1 in full**, not merely
demoted to optional filters. They remain available, unmodified, for later
research iterations (Hypothesis B/C, §7) and for a future preregistered
ablation study once a v1 basis-only baseline exists; nothing here removes or
alters their existing feature definitions.

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

## 4. Accounting architecture decision — Architecture 1 (OWNER DECIDED, §19)

**OWNER DECIDED: Architecture 1 — price-return-first, and Architecture 1
only.**

3J.2b v1 is explicitly price-return-only basis/dislocation research. Funding
cashflows are excluded, in full, from v1 — not deprioritized, excluded. Its
research P&L is price return only (via the existing
`run_vectorized_backtest`/`EventDrivenBacktester` engines and `CostModel`,
subject to §12.1's accounting-compatibility finding). `crypto_realized_funding_annualized`
and `crypto_funding_forecast_error` do not enter v1 at all (§2) — not even as
historical state/filter variables; they are deferred to later preregistered
extensions/ablation studies (§16, §19).

**Implementation must not call or reuse `paper_execution.apply_funding()`.**
That primitive, and any funding cashflow accounting, is out of scope for
3J.2b in its entirety.

**Naming constraint.** The strategy must not be labelled, documented, or
described as a funding strategy, carry strategy, funding capture, or funding
arbitrage, in code, tests, docs, or scorecard metadata. Its only claimed
edge is basis convergence (price return), and any documentation produced by
3J.2b.2 must say so explicitly.

**The funding sign-convention question (§4.1) is therefore DEFERRED, not an
unresolved blocker for 3J.2b.** Funding features do not enter v1 at all, so
the sign-convention gap below cannot affect this proposal's recommendation or
3J.2b.1/3J.2b.2's implementation contract. It remains a precondition only for
a *future* Architecture 2 proposal. Do not adopt the current
`paper_execution.py` docstring as authoritative by implication — a future
Architecture 2 / funding-aware strategy must receive its own owner-reviewed
accounting proposal defining payment direction, settlement asset, notional,
position-at-funding-time, and P&L attribution, independent of anything
stated here.

Why not Architecture 2 now: the recommended first hypothesis (§7,
Hypothesis A) does not require funding cashflows to be a legitimate,
honestly-labelled strategy — its edge claim is basis convergence, not
funding capture. Building a new, separately-reviewed funding-aware
accounting primitive before validating that the simpler price-only path is
even research-complete would front-load risk and scope onto a strategy that
doesn't need it. Architecture 2 remains available for a *future*,
explicitly funding-carry-labelled hypothesis (Hypothesis B/C's natural
domain), gated on its own owner-reviewed accounting proposal.

### 4.1 If Architecture 2 is pursued later — semantics deferred, not a blocker

This is recorded now, while the investigation is fresh, as a **deferred
precondition for a future Architecture 2 proposal**, not a blocker for
3J.2b and not a spec for this PR:

- **Sign convention — DEFERRED, not a blocker for 3J.2b.** Grepping the
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
  own explicit rule (see negative-test plan, §17).
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

**Data-availability gap — OWNER DECIDED: fixture-only activation authorized
(§19).** Inspection of `historical_market_data.py`, the crypto fixtures in
`tests/test_crypto_market_observations_postgres.py`, and
`crypto_derivatives_features.py` shows that **no crypto OHLCV/tradable-bar
ingestion path, fixture, or test currently exists for any crypto perpetual
instrument.** The dedicated crypto source fixture is authorized only for
`{FUNDING_RATE_REALIZED, FUNDING_RATE_INDICATIVE, MARK_PRICE, INDEX_PRICE,
OPEN_INTEREST}` — `OHLCV` is absent. Crypto historical data today is event
data only.

3J.2b.1 is authorized to close this gap as **fixture engineering evidence
only**, using the existing historical pipeline: extend only a fixture/test
source capability to `OHLCV` as needed (new source authorization +
ingested/sealed OHLCV evidence for at least one `PERPETUAL` test fixture).
Explicitly out of scope for this activation:

- no real venue/provider call;
- no paid data activation;
- no claim that crypto OHLCV has been externally verified.

This closes the engineering precondition (fixture-only) while leaving real
crypto-OHLCV-source activation for a later, separate, explicit
authorization. If a future real provider cannot supply `MARK_PRICE`,
`INDEX_PRICE`, and `OHLCV` for the same instrument under one coherent source
registration, that is a real-provider-activation-time problem (§6) — it must
not be described as a blocker to fixture-only 3J.2b.1 engineering now.

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

**Composite-evidence architecture requirement — OWNER DECIDED: Option A for
v1 (§19).** OHLCV's real downstream consumer path today is
`historical_bar_bridge.py` → `PostgresHistoricalBarStore` (Module 3F) — a
structurally separate subsystem from `historical_dataset_members` /
`historical_dataset_versions`, which is the path `crypto_derivatives_features.py`
and `SubjectAwareResearchFeatureBundle` read. Even where OHLCV exists
(equities, futures), it does not flow through the same
`dataset_version_id`-scoped `FeatureMaterializationV2` mechanism the
derivatives features use. So "one dataset_version_id can hold both" is true
at the schema layer but does not by itself give a strategy a single,
provably-consistent evidence source.

**OWNER DECIDED: use exactly one sealed historical dataset UUID** containing
the evidence required for the research run. For the bounded fixture
implementation (§5), this dataset must contain, for the same crypto
`PERPETUAL` instrument, `MARK_PRICE`, `INDEX_PRICE`, and `OHLCV` under one
authorized source identity and one normalization version. The resulting
`crypto_mark_index_basis` materializations and the tradable OHLCV series must
therefore trace back to the same exact sealed `dataset_version_id`.

- **Option A (decided for v1):** extend one crypto
  `AuthorizedHistoricalSource`'s `authorized_observation_kinds` to include
  `OHLCV` alongside the existing derivative kinds, ingest and normalize both
  under that one `source_id`, and call `seal_dataset()` once with
  `normalized_ids` spanning both kinds. This reuses the existing
  single-sealed-dataset rule with **zero new authority, zero schema change**
  — it is purely an ingestion/source-registration decision. Its own open
  question — whether a bar-oriented reader needs to exist alongside
  `SubjectAwareResearchFeatureBundle` so a strategy can consume price
  evidence and feature evidence under one asserted `dataset_version_id` — is
  resolved in §6.1 below: yes, a dedicated read-only reader is added.
- **Option B (explicitly deferred, not authorized for 3J.2b):** a
  separately reviewed composite-evidence/dataset-linkage authority for
  cases where real venue plumbing makes single-source ingestion of OHLCV and
  mark/index/funding infeasible — mirrors 3J.1 §9's own deferral of "any
  feature combining multiple sealed dataset versions." **No cross-dataset
  linkage authority is authorized in 3J.2b.** If a future real provider
  cannot supply the required evidence under one coherent source
  registration, implementation must stop at real-provider activation time
  and propose a composite-evidence authority separately — that future
  provider limitation must not block fixture engineering now (§5).

## 6.1 Dataset-bound tradable-bar research reader (new, OWNER DECIDED, §19)

3J.2b.1 must not treat Module 3F's `PostgresHistoricalBarStore` as the
canonical provenance authority for this strategy merely because it already
stores bars — reading an unrelated bar store and asserting that its dataset
tag matches the sealed dataset is not acceptable; provenance must be derived
from actual sealed dataset membership, not asserted.

Instead, 3J.2b.1 introduces a **read-only research boundary** over the
sealed historical authority, conceptually named `TradableBarEvidenceReaderV2`
/ `AuthoritativeTradableBarSeriesV2`. It must resolve OHLCV by:

- exact `dataset_version_id`;
- exact `instrument_id`;
- `ObservationKind.OHLCV`;
- exact interval;
- dataset membership (the observation must actually belong to
  `historical_dataset_members` for the asserted `dataset_version_id`, not
  merely carry a matching label);
- PIT visibility (same `knowledge_at`-gated admissibility discipline as the
  existing `FeatureAuthorityReaderV2` path);
- canonical normalized/raw observation identity.

It may **project** data for research use (e.g. into the per-trade
accounting shape needed by §12.1), but it must not create another durable
market-data authority. **No new table is expected.** This reader is the
mechanism by which the `crypto_mark_index_basis` materializations (§2) and
the tradable OHLCV series (§5) are proven, not merely claimed, to trace back
to the same sealed `dataset_version_id`.

## 7. Hypothesis comparison

| # | Hypothesis | Primary driver | Assumptions | PIT cleanliness |
|---|---|---|---|---|
| A | Basis mean reversion | `crypto_mark_index_basis` (sole v1 signal, OWNER DECIDED) | Basis dislocations revert; no OI/funding feature in v1 | Cleanest — primary signal has event-time knowledge, no realization lag |
| B | Funding persistence/crowding | `crypto_realized_funding_annualized` + OI/basis state | Realized funding history predicts subsequent direction/convergence; crowding is a real, exploitable regime | Primary driver is inherently lagged (funding realizes only every N hours); more causal assumptions |
| C | Combined basis + funding regime | `crypto_mark_index_basis` (primary) + funding/OI (regime) | Superset of A, adds regime conditioning | Same PIT cleanliness as A at the primary-signal level, but more free parameters and moving parts |

**OWNER DECIDED: Hypothesis A, pure basis mean reversion**, with exactly one
signal feature, `crypto_mark_index_basis` — no `open_interest_change` filter
and no `crypto_realized_funding_annualized` regime input in v1; both move to
later preregistered extensions/ablation studies (§2, §16, §19). This is the
hypothesis with the fewest assumptions and the cleanest causal/PIT
interpretation, per instruction — not the one that sounds most profitable.
Hypothesis C is the natural second iteration once A has a validated baseline;
Hypothesis B is the natural domain for `crypto_funding_forecast_error`
(deferred from v1, §2) and for Architecture 2 (§4.1) if that is ever pursued.
Neither B nor C is recommended for v1.

## 8. Signal-to-entry timing (OWNER DECIDED: feature-event-driven, §19)

Strict rule: `entry_time > max(feature knowledge_at values, decision_at)`.
A feature observation at timestamp `t` never permits an entry at price `t`.

**Decision timing — feature-event-driven, not bar-close-driven.** The
original proposal's `decision_at = arbitrary OHLCV bar close` rule is
superseded. A v1 decision candidate is triggered by an eligible `VALIDATED`
`crypto_mark_index_basis` materialization — not by bar cadence. Evaluating
decisions on bar close would implicitly forward-fill an irregular,
event-driven basis feature onto arbitrary OHLCV bars; that is not adopted.

Canonical decision availability must satisfy, for that exact materialization:

```
decision_at >= max(event_at, effective_at, knowledge_at, computed_at)
```

The implementation may choose the canonical minimum admissible decision
timestamp deterministically (i.e. `decision_at` is pinned to the earliest
timestamp satisfying the inequality above, not left to discretion at
implementation time). **No later database revision may rewrite an earlier
decision** — once a decision candidate's `decision_at` is fixed against a
given materialization, a subsequent revision to that materialization cannot
retroactively move it.

**Earliest entry rule — bar-open timestamp contract required.** Preserve
`execution_time > decision_at`, but tighten the earlier "next bar open"
claim. 3J.2b.1 must establish canonical OHLCV interval/timestamp semantics
(a proven `bar_open_at`, via §6.1's `TradableBarEvidenceReaderV2`) before an
`OPEN` price is considered executable. The v1 contract is: **the first
eligible bar whose canonical bar-open timestamp is strictly greater than
`decision_at`**, using that bar's `OPEN` price as the research execution
proxy. The bar-open timestamp must not be inferred from a bar-close
timestamp unless the existing interval/source contract deterministically
proves the bar start (e.g. a fixed, known interval width subtracted from a
proven close, with no ambiguity about which convention the source uses). If
canonical `bar_open_at`/interval-start semantics cannot be established from
existing historical evidence, 3J.2b.1 must fail closed and report the gap
before 3J.2b.2 begins.

Never use, as the execution price:

- same-event price;
- `MARK` as execution price;
- `INDEX` as execution price;
- basis as execution price.

No look-ahead through same-timestamp mark/index, realized funding, OI
revision, bar close, or normalization time: every feature clock consumed by
a decision candidate must independently satisfy the §8 admissibility
inequality before the decision is made, and the price evidence used for
entry must have its own bar-open timestamp strictly after `decision_at`,
never merely after the feature's `event_at`.

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
silently changing meaning for unrelated long-only trend strategies. **Do not
retrofit Trend V2.**

**OWNER DECIDED: independent signed research exposure abstraction (§19).** A
new, generic research-only signed-exposure abstraction is approved.
Semantics:

```
-cap <= exposure <= +cap
```

with negative = short, zero = flat, positive = long. Require `0 < cap <= 1`
and finite `Decimal` values throughout.

This abstraction must remain **research-only**. It grants no:

- `OrderSide` semantics;
- leverage authority;
- portfolio authority;
- risk authority;
- execution authority.

Prefer generic names such as `SignedResearchSignalObservationV2` and
`SignedResearchSignalSeriesV2` rather than crypto-specific exposure types, so
futures/other strategy research can reuse the primitive later without
changing Trend V2. This is a real, reviewable code change — **not
implemented in this PR** and explicitly assigned to 3J.2b.1 (§16). **No
persistence/new table is expected.**

## 10. Holding and exit semantics

| Candidate | Parameters needed | Notes |
|---|---|---|
| Fixed N-bar horizon | 1 (`N`) | Simplest, fully auditable, no path-dependent detection logic |
| Next funding event | 0 (derived from convention) | Economically motivated for a funding-adjacent hypothesis, not this one |
| Basis convergence threshold | 1 (`threshold`) | Economically motivated for Hypothesis A specifically; needs threshold-crossing detection |
| Time stop | 1 | Usually paired with another exit as a safety bound, not standalone |
| Signal reversal | 0 (derived) | Couples exit tightly to entry-signal noise; less auditable |

**OWNER DECIDED: fixed N-bar holding horizon (§19)**, the single
deterministic exit condition for v1. This keeps the parameter surface
minimal (one parameter, no threshold-crossing state machine) and the model
maximally auditable for a first strategy. Basis-convergence-threshold is the
natural v1.1 enhancement once a fixed-horizon baseline exists and provides a
convergence-timing prior; it is **not** adopted for v1, to avoid combining
multiple optional exits before there is a reason to.

**Deterministic, non-overlapping trade lifecycle (OWNER DECIDED, §19).** One
non-overlapping trade lifecycle applies:

- enter at the first eligible bar open strictly after the decision (§8);
- hold exactly `N` complete bar intervals (`holding_horizon_bars`, §11);
- exit deterministically at the pinned convention below;
- while a trade is open, ignore subsequent basis-entry candidates;
- no pyramiding;
- no overlapping trades;
- no scale-in/out;
- the next trade may only originate from a new eligible feature event
  (§8) after the prior trade has exited.

**Pinned exit price/timestamp convention.** Now that §8/§6.1 establish the
canonical `bar_open_at` contract, the exit convention is pinned as: exit at
the `OPEN` price of the bar whose canonical bar-open timestamp is exactly
`N` complete bar intervals after the entry bar's bar-open timestamp. Because
bar interval spacing is fixed and known at entry time, this exit timestamp
is fully determined without relying on any future knowledge — only on the
existence of that bar. If no tradable bar exists at that exact
bar-open timestamp, the trade is excluded from the study per the existing
fail-closed rule (§15, Fail-closed conditions) rather than force-closed at a
synthetic or undefined price.

## 11. Parameter policy (OWNER DECIDED: exactly three v1 parameters, §19)

Initial parameters become exactly:

1. `basis_entry_threshold` — dimensionless; enter when `|basis| > threshold`.
2. `holding_horizon_bars` — integer `N`, the fixed exit horizon (§10).
3. `maximum_absolute_exposure` — the `cap` in `-cap <= exposure <= +cap`
   (§9), with `0 < cap <= 1`.

`oi_change_confirmation_threshold` is removed from the v1 parameter set —
`open_interest_change` is not a v1 feature at all (§2, §7). **No
optimization is authorized**: one small, preregistered, coarse set across
these three parameters — no Bayesian search, no grid, no "best Sharpe"
selection, no parameter sweep, no in-sample selection.

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

## 12.1 Price-return accounting compatibility (new, resolved for 3J.2b.1)

Two claims must be kept distinct: "the validation machinery (§12 table) is
reusable" and "the existing price-accounting function is automatically
correct for this new execution convention." The first is true unconditionally
— walk-forward, purge/embargo, bootstrap, Monte Carlo, parameter stability,
stress, multiple-testing, PBO, DSR, and Scorecard V2 all operate on a
resulting return/equity series regardless of how that series was produced.
The second is **not** automatically true and must not be assumed.

`run_vectorized_backtest()` (`research.py:348-369`) computes
`gross = position * (close_t / close_t-1 - 1)` — a per-bar, close-to-close
return over a continuously held position. §8/§10's execution convention is
different in kind: a fixed-`N`-bar, non-overlapping trade whose entry and
exit prices are both `OPEN` prices at the trade's bar-open boundaries, not a
continuously marked close-to-close series. Passing `OPEN` prices into an
API whose documented contract computes close-to-close returns, and treating
the result as though it were that documented contract, would be misleading
— that shortcut is not proposed.

3J.2b.1 may require a **minimal, deterministic price-return research
accounting adapter** for signed exposure: computing, per non-overlapping
trade, `signed_exposure * (exit_open_price / entry_open_price - 1)`, net of
the existing `CostModel`'s turnover-based cost applied at entry and exit,
and packaging the resulting per-trade (or resampled-equity) series into the
same shape the §12 validation stack already consumes. This adapter must
still exclude funding (§4) and must not build a duplicate validation stack —
it is accounting glue only, not a new engine. If required, it belongs in
3J.2b.1, and it must later reconcile against an independent engine before
any robustness claim is made from its output.

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

1. **First strategy hypothesis:** Hypothesis A, pure basis mean reversion (§7).
2. **Required features:** `crypto_mark_index_basis` — the sole v1 signal
   feature (OWNER DECIDED, §19). `open_interest_change`,
   `crypto_realized_funding_annualized`, and `crypto_funding_forecast_error`
   are not part of v1; all three move to later preregistered
   extensions/ablation studies (§2).
3. **Feature roles:** per the table in §2.
4. **Subject type:** `FeatureSubjectType.INSTRUMENT`, crypto `PERPETUAL`.
5. **Tradable return series:** OHLCV/tradable-bar observations for the same
   perpetual — **does not exist yet**; fixture-only ingestion and sealing
   authorized for 3J.2b.1 (§5).
6. **Dataset relationship:** Option A, decided for v1 (§6) — one sealed
   dataset spanning `MARK_PRICE`/`INDEX_PRICE`/`OHLCV` for the same
   instrument, via extended source authorization, read through the new
   dataset-bound tradable-bar reader (§6.1). Option B is explicitly
   deferred; no cross-dataset linkage authority is authorized in 3J.2b.
7. **Decision timestamp:** feature-event-driven — triggered by an eligible
   `VALIDATED` `crypto_mark_index_basis` materialization, with
   `decision_at >= max(event_at, effective_at, knowledge_at, computed_at)`
   for that materialization (§8).
8. **Earliest entry rule:** first eligible bar whose canonical bar-open
   timestamp is strictly after `decision_at`, using that bar's `OPEN`
   price; fails closed if the bar-open timestamp contract cannot be
   established (§8).
9. **Long/short/flat semantics:** `SignedResearchSignalObservationV2` /
   `SignedResearchSignalSeriesV2` — a new, independent, research-only
   signed-exposure abstraction, `-cap <= exposure <= +cap`, `0 < cap <= 1`,
   structurally independent of Trend V2 (§9, OWNER DECIDED).
10. **Exit rule:** fixed `N`-bar holding horizon, non-overlapping, pinned to
    the bar `N` intervals after the entry bar's bar-open timestamp (§10).
11. **Funding-cashflow treatment:** excluded from v1 entirely (Architecture
    1 only, §4); funding features do not enter v1 at all; `apply_funding()`
    must not be called/reused; "funding strategy"/"carry strategy"/"funding
    capture"/"funding arbitrage" labels are prohibited.
12. **Cost model:** existing `CostModel` (turnover + versioned
    spread/slippage assumption), stress-tested at base/1.5x/2x/3x (§12),
    applied through the §12.1 accounting adapter if required.
13. **Minimal parameter set:** `basis_entry_threshold`,
    `holding_horizon_bars`, `maximum_absolute_exposure` — exactly these
    three; no optimization authorized (§11).
14. **Validation protocol:** the existing walk-forward/purge/bootstrap/Monte
    Carlo/parameter-stability/stress/multiple-testing/PBO/DSR/Scorecard V2
    stack, unmodified (§12); price-return accounting compatibility per
    §12.1.
15. **Fail-closed conditions:** see below.
16. **Evidence/provenance manifest:** reuses 3J.1/3J.2a's existing manifest
    conventions — bundle content hash; per-feature
    `materialization_id`/`content_hash`/`quality_status`; dataset
    `dataset_version_id`/`content_hash`/`source_id`; plus the canonical
    observation ids for the OHLCV evidence consumed through
    `TradableBarEvidenceReaderV2` (§6.1), all traceable to the one sealed
    `dataset_version_id` decided in §6.
17. **Implementation decomposition:** §16 below.

### Fail-closed conditions

- Any required feature's `knowledge_at > decision_at` → excluded from the
  bundle (existing 3J.2a/3J.1 behavior).
- `DEGRADED`/`REJECTED` quality materializations → excluded under
  `VALIDATED_ONLY` (existing default policy).
- Cross-dataset feature or feature/price pairing → rejected (existing
  3J.2a rule; extended to any price evidence bound under the bundle via
  `TradableBarEvidenceReaderV2`, §6.1).
- Wrong subject, wrong instrument, non-`PERPETUAL` instrument kind →
  rejected (existing per-feature gates).
- Zero/negative price in either the basis computation or the tradable
  return series → rejected, no value/trade produced.
- No tradable bar available for the required exit horizon → the trade is
  **excluded from the study**, never force-closed at a synthetic/undefined
  price.
- Canonical `bar_open_at`/interval-start semantics cannot be established
  from existing historical evidence → fail closed, report the gap before
  3J.2b.2 begins (§8).
- A basis-entry candidate arrives while a trade is already open → ignored;
  no pyramiding, no overlapping trades (§10).
- Signed exposure requested outside `-cap <= exposure <= +cap` or with a
  non-finite `Decimal` or `cap` outside `(0, 1]` → hard fail-closed (§9).

## 16. Implementation decomposition

**Two bounded modules, not one (OWNER DECIDED decomposition, §19).** Unlike
the funding-P&L question (§4), which under Architecture 1 needs no new
accounting primitive, structurally independent prerequisites still exist —
signed exposure (§9), fixture-only OHLCV evidence (§5), the dataset-bound
tradable-bar reader (§6.1), and the canonical bar-timestamp/execution
semantics (§8) — and these are cross-cutting infrastructure changes that
deserve independent review before the strategy logic that depends on them,
consistent with how 3J.0 (identity) was reviewed separately from 3J.1
(features) and 3J.2a (binding).

- **3J.2b.1 — Signed Research Exposure + Dataset-Bound Tradable Price
  Evidence.** Establishes:
  - signed research exposure — `SignedResearchSignalObservationV2` /
    `SignedResearchSignalSeriesV2` (§9);
  - fixture-only crypto `PERPETUAL` OHLCV in the existing historical
    authority (§5);
  - one sealed dataset containing `MARK_PRICE`/`INDEX_PRICE`/`OHLCV` for the
    same instrument (§6);
  - the dataset-bound tradable-bar reader, `TradableBarEvidenceReaderV2` /
    `AuthoritativeTradableBarSeriesV2` (§6.1);
  - canonical bar timestamp/execution semantics — a proven `bar_open_at`
    contract (§8);
  - price-return accounting compatibility, or the minimal adapter if
    objectively required (§12.1);
  - **no strategy hypothesis implementation.**

  Then exact-main verify and stop.
- **3J.2b.2 — Pure Basis Mean-Reversion Research.** Implements:
  - `crypto_mark_index_basis` only (§2, §7);
  - threshold-based signed direction: positive basis above threshold →
    short; negative basis below `-threshold` → long; otherwise flat;
  - fixed-`N`-bar non-overlapping trades (§10);
  - strictly-later entry (§8);
  - price-return-only P&L (§4, §12.1);
  - conservative versioned transaction costs (§12);
  - the existing walk-forward/purge/embargo/bootstrap/Monte
    Carlo/stress/stability/multiple-testing/PBO/DSR/Scorecard stack (§12);
  - synthetic/no-edge control;
  - `RESEARCH_ONLY` (§13).

  No OI/funding feature is part of the first baseline.

One bounded module is not recommended: 3J.2b.1's prerequisites are genuinely
separable from strategy logic, are each independently testable, and (per the
codebase's established pattern) each deserves its own exact-main
verification before the strategy that depends on all of them is reviewed.

## 17. Required negative-test plan

Implementation (not this PR) must include tests for at least:

- feature known after decision;
- same-event execution leakage;
- wrong instrument;
- wrong dataset;
- cross-dataset hidden pairing (feature/feature and feature/price);
- wrong subject;
- mark/index used as executable price;
- basis used as executable price;
- missing tradable return bar;
- canonical `bar_open_at` cannot be established → fail closed before
  3J.2b.2 (§8);
- a basis-entry candidate arriving while a trade is open → ignored, no
  pyramiding, no overlapping trades (§10);
- next trade only originates from a new eligible feature event after the
  prior trade has exited (§10);
- zero/negative prices;
- no future bar for exit (trade excluded from study, not force-closed);
- signed exposure outside `-cap <= exposure <= +cap`, non-finite `Decimal`,
  or `cap` outside `(0, 1]` (§9);
- parameter-set mutation (content-hash must change);
- deterministic replay/hash mismatch;
- in-sample-only promotion attempt (must be blocked by the existing
  validation package/promotion gate);
- *(Architecture 2 only, not applicable to 3J.2b.1/3J.2b.2 — retained here
  for the future funding-aware proposal, §4.1):* realized funding used
  before availability; forecast error used before realization; unsupported
  funding sign convention; funding paid to wrong position direction;
  position opened after funding event incorrectly receiving payment;
  missing funding observation (filter unavailable, not approximated);
  ambiguous funding revision.

## 18. Scope exclusions

No implementation. No strategy execution. No paper orders. No broker. No
Opportunity Radar. No portfolio allocation. No risk-policy change. No ML. No
futures strategy. No real provider calls. No alpha/performance claims. No
funding-cashflow accounting (Architecture 1 only, §4).

## 19. Decision log

All architecture items previously logged as REQUIRES REVIEW are resolved by
owner decision. **No remaining architecture item REQUIRES REVIEW to start
3J.2b.1.**

1. **Dataset relationship (§6)** — **OWNER DECIDED: Option A for v1.** One
   sealed dataset spanning `MARK_PRICE`/`INDEX_PRICE`/`OHLCV` under one
   source identity and normalization version; Option B explicitly deferred;
   no cross-dataset linkage authority authorized in 3J.2b. A future
   real-provider composite-evidence problem remains deferred and must not be
   described as a blocker to fixture-only 3J.2b.1.
2. **Funding cashflow/sign (§4, §4.1)** — **OWNER DECIDED: excluded/
   deferred.** Architecture 1 only; funding cashflows excluded from v1 in
   full; the sign-convention question is deferred to a future,
   separately-owner-reviewed Architecture 2 proposal — not a blocker here.
3. **Signed exposure (§9)** — **OWNER DECIDED: independent research-only
   abstraction.** `SignedResearchSignalObservationV2` /
   `SignedResearchSignalSeriesV2`, `-cap <= exposure <= +cap`,
   `0 < cap <= 1`, finite `Decimal`; no Trend V2 retrofit.
4. **Crypto OHLCV (§5)** — **OWNER DECIDED: fixture-only historical
   authority onboarding permitted;** real provider activation remains
   separately unauthorized.
5. **Signal timing (§8)** — **OWNER DECIDED: feature-event-driven.**
   Decision candidates are triggered by eligible `VALIDATED`
   `crypto_mark_index_basis` materializations, not bar close.
6. **First feature set (§2, §7)** — **OWNER DECIDED: basis only.**
   `crypto_mark_index_basis` is the sole v1 signal feature;
   `open_interest_change`, `crypto_realized_funding_annualized`, and
   `crypto_funding_forecast_error` move to later preregistered
   extensions/ablation studies.

## 20. No authority granted by this document

This is analysis and a recommendation only. No feature, strategy, migration,
accounting primitive, or API described here exists in the codebase. No
strategy, signal, opportunity, order, or risk authority is implied. 3J.2b
does not begin until a separate implementation PR (or PRs, per §16) is
opened, reviewed, and merged following the same branch → PR → CI → merge →
exact-main verification discipline as every prior module.
