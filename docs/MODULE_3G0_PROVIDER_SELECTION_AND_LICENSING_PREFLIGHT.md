# Module 3G.0: Real Market-Data Provider Selection and Licensing Preflight

Status: **research / design / documentation only**. No provider account was
created, no terms were accepted, no credential was supplied, no provider
endpoint was called, and no real market-data provider is activated by this
module. The platform remains paper-only; live trading remains disabled.
Companion to [MODULE_3F_POSTGRES_HISTORICAL_BARS_AND_DATA_HEALTH_WORKER.md](MODULE_3F_POSTGRES_HISTORICAL_BARS_AND_DATA_HEALTH_WORKER.md),
[DATA_PROVIDER_MATRIX.md](DATA_PROVIDER_MATRIX.md) and
[DATA_SOURCE_ACTIVATION_PACKAGES.md](DATA_SOURCE_ACTIVATION_PACKAGES.md), which
this module extends rather than replaces.

This document records the outcome of evaluating three real market-data
candidates — **Twelve Data**, **Massive** (formerly Polygon.io) and
**Databento** — against the platform's Professional Multi-Asset, Multi-Account
Investment & Trading Intelligence Terminal north star, and proposes (but does
not execute) the exact Module 3G implementation. **The provider recommendation
below requires explicit owner approval before any subscription, credential, or
real-data activation.**

## 1. North star restated for this evaluation

The platform is evaluated from now on as a terminal that must eventually
support: active long-term portfolio management; medium-term/tactical
investing; swing trading; systematic short-term trading; personal broker
accounts; prop-firm accounts; separate capital/risk policies per account;
robust historical backtesting; strategy research and comparison; global
opportunity ranking across markets and horizons; macro/regime intelligence;
fundamental/valuation intelligence; deterministic independent risk; and a
paper → broker sandbox → shadow → separately-authorized live progression.
Investment and trading systems may share data/intelligence, but their
capital, policies, risk and execution authority remain separated (see
[RISK_MANAGEMENT_POLICY.md](RISK_MANAGEMENT_POLICY.md),
[EXECUTION_POLICY.md](EXECUTION_POLICY.md)). This has two direct consequences
for provider selection:

1. The platform is not a hobbyist/personal tool. Any candidate whose
   affordable tiers are contractually restricted to "personal,
   non-commercial, non-business" use is a **licensing risk**, not just a cost
   line, once prop-firm and multi-account professional use are in scope —
   even though only paper/research use is authorized today.
2. Corporate-action correctness, point-in-time (PIT) revision semantics,
   stable cross-event instrument identity and immutable raw provenance matter
   more than API ergonomics, because the eventual Strategy Lab's backtests are
   only as trustworthy as the historical dataset underneath them.

## 2. Provider comparison matrix

All findings below are from each provider's current official documentation,
pricing pages and terms of service (fetched 2026-09-06). None of it is a
substitute for a signed order form and rights schedule; API documentation is
not a licence grant, and none of the below has been operator-approved.

| Dimension | Twelve Data | Massive (Polygon.io lineage) | Databento |
|---|---|---|---|
| US equities | Yes, all tiers | Yes, all tiers | Yes; direct Nasdaq/NLS+ and other venue licensing |
| ETFs | Grow tier ($79/mo) and up | Yes, same stock plans | Yes, same equities pipeline |
| Forex | All tiers (pairs) | Separate "Currencies" subscription (forex + crypto) | Documented, entitlement-dependent |
| Crypto | All tiers | Same "Currencies" subscription as forex | Documented, entitlement-dependent |
| Indices | Market indices, tier-gated | Separate "Indices" subscription | Documented via index/reference datasets |
| Commodities | Grow tier and up (metals/energy/ag/livestock) | Not a dedicated product line found | Not the primary focus; venue-dependent |
| Futures | Not offered | Separate "Futures" subscription: CME, CBOT, NYMEX, COMEX, $999/mo Business "CME" plan for equity-index futures | Strong (Databento's origin is CME/futures market data) |
| European equities | Pro tier ($229/mo): "EU real-time equities" | Not confirmed in reviewed pages | Venue/entitlement-dependent, not confirmed for this review |
| Historical depth (equities) | "All history" back to 2000-01-15 on paid tiers per corporate-action docs; EOD on all tiers | Basic/Starter 2yr, Developer 5yr, Advanced 20+yr | Usage-based & Standard: 1yr L1 / 1mo L2-L3; Plus & Unlimited: 16+ years |
| Daily/minute/second/tick coverage | `time_series` intervals from 1min to 1month; no documented sub-minute tick feed | Minute aggregates on Basic; trades (tick) from Developer up; quotes from Advanced up | Full L1/L2/L3 (top-of-book to full order book), schema-dependent on plan |
| Corporate actions | Dividends, splits, earnings endpoints; `adjust` parameter (`all`/`splits`/`dividends`/`none`) | Dividends confirmed with full history on paid tiers; splits endpoint exists; merger/spinoff/delisting completeness not confirmed | `pit=True` corporate-actions mode retaining prior records by `ts_record`; documents dividends, splits, listings, delistings and more event types — the most complete PIT corporate-action documentation of the three |
| Symbol/reference data | Symbol catalogs, search, cross-listings, earliest-timestamp discovery | Reference/ticker endpoints on Basic tier | `symbology` standard explicitly preserves historical symbols "at their original time" |
| Delistings / survivorship-bias support | Not explicitly documented | Not explicitly confirmed for non-Business tiers | `listing_id`/`security_id` "survive many symbol/name/merger changes, but not every spin-off or non-relisted delisting" (Databento's own documented caveat — the most honest of the three) |
| Market/session/timezone metadata | Symbol metadata includes exchange/timezone fields | Reference data includes exchange metadata | Exchange/venue-native session metadata via reference datasets |
| WebSocket / live support | All paid tiers | Starter tier and up | Yes, subscription-tier dependent; not needed for this module's scope |
| Rate limits | Credit-based: Basic 8/min (800/day), Grow 377/min, Pro 1,597/min, Ultra 10,946/min | Basic 5/min; Starter/Developer/Advanced "unlimited" calls | Historical is usage-based (billed by data volume, not request rate); live tiers carry their own entitlement limits |
| Backfill performance | REST pagination, credit-metered | REST pagination (`next_url`), unlimited calls above Basic | Bulk historical download API designed for large backfills; billed per GB, not per request |
| PIT / revision semantics | `adjust` parameter exists but no documented "as of ingestion time" revision history; **contract limits how long raw data may be cached** (see §3) | Not established by reviewed docs; no documented PIT revision ledger | Strongest documented PIT model of the three (`pit=True`, `ts_record`-keyed corporate-action history) |
| Raw-data provenance | Standard REST responses; no documented raw-payload preservation guarantee | Standard REST responses; `next_url` provides page provenance | Documents source/venue-native provenance; direct exchange licensing gives clearer chain of title |
| Internal data storage rights | **Restricted**: ToS §2.3(g) prohibits storing/caching data "beyond permitted timeframes specified in the Documentation"; §16.1/§16.2 require deletion within 30 days of termination | Individual ToS grants access "solely for personal, non-commercial, non-business purposes"; no explicit unlimited local-storage grant found | Standard/Plus/Unlimited tiers are framed as data *licenses*, not rentals; historical purchases are usage-based (pay once per dataset pull) rather than time-boxed access |
| Non-display / algo use rights | ToS §2.2(d): "Non-Display Use only as permitted by your subscription tier" — gated by plan, ambiguous at Basic/Grow | Not explicitly addressed in Individual ToS; likely requires Business tier for anything beyond personal display | Explicitly supports non-display/algorithmic use; Databento markets "internal analytics" and "algorithmic trading" as intended non-display use cases |
| Derived-data rights | Customer "retains rights to Derived Data" that "cannot be reverse-engineered to recreate the original Data" (§6.2, §2.2(c)) | Not clearly defined for Individual tier | Not fully documented in this review; venue-specific "no reverse-engineering" pattern is the general industry norm and applies here too |
| Redistribution restrictions | Prohibited outside an explicit "Redistribution Rights Add-On" or separate agreement (§2.3(b)) | Individual ToS: no redistribution; Business ToS required for anything beyond personal, non-commercial use | Company states "Databento doesn't apply any redistribution restrictions" beyond publisher-specific pass-through terms; Plus tier adds explicit "external distribution rights" |
| Personal vs professional licensing | Explicit split: Individual plans (Basic–Ultra) are personal/internal-use only; Business plans (Venture/Enterprise/Enterprise+) required for commercial display/professional use | Explicit "Non-pros only" label on every Individual tier; CME/OPRA-style "Non-Professional Subscriber" exchange classification applies; a Business subscription (`massive.com/business`) is required once professional-use criteria are met | Standard tier ($199/mo) is framed as personal use (2 devices); Plus/Unlimited are the professional/business tiers |
| Current pricing (as researched) | Free / $79 / $229 / $999 per month (Individual) | Free / $29 / $79 / $199 per month (Stocks Individual); Business Futures $999/mo; Business Options $1,999/mo | Usage-based ($/GB, $125 free trial credit) or $199 / $1,750 / $4,500 per month subscription tiers |
| Estimated initial cost for this project | ~$79–229/mo if Grow/Pro tier needed for ETFs/EU equities, **but see §3 — storage-duration restriction is disqualifying for a permanent research dataset regardless of price** | $199/mo (Advanced) for 20+ years of real-time-eligible history, **but "Non-pros only" licensing conflicts with the platform's professional multi-account terminal positioning** at any price the Individual tier offers | Likely near-$0–low-hundreds for the first bounded US equities/ETF vertical slice using the $125 free credit plus modest usage-based historical pulls; no monthly subscription is required to start |
| Suitability for serious research/backtesting | Weak: contractual storage-duration limits directly conflict with the platform's immutable, permanent raw-capture architecture | Moderate: reasonable depth and cost, but PIT/corporate-action completeness and delisting/survivorship coverage are unconfirmed, and licensing tier is a poor fit for a professional terminal | Strong: purpose-built PIT/corporate-action model, explicit non-display/algo rights, no storage-duration restriction found, direct-exchange licensing gives the clearest chain of title |
| Suitability for future production use | Requires a Business-tier renegotiation regardless of current spend | Requires a Business-tier renegotiation for any professional/multi-account use | Plus/Unlimited tiers already framed for exactly this progression path (added distribution/redistribution rights, deeper history, no license fees on several real-time products) |

## 3. Licensing findings that materially affected the decision

These are the specific findings — not marketing claims — that separate the
three candidates for this platform's purpose. All quotes are from the pages
cited; none of this has been confirmed by an operator-executed written
agreement, and nothing here authorizes a signature or a purchase.

- **Twelve Data's storage-duration restriction is disqualifying for this
  architecture as documented.** [Twelve Data's Terms of Use](https://twelvedata.com/terms)
  §2.3(g) prohibits storing or caching data "beyond permitted timeframes
  specified in the Documentation," and §16.1/§16.2 require deletion of all
  data within 30 days of subscription termination. This platform's data
  architecture is built on **immutable, permanent raw capture**
  (`historical_raw_observations`, append-only, no delete/update path — see
  [DATA_ARCHITECTURE.md](DATA_ARCHITECTURE.md)) specifically so that sealed
  research datasets remain reproducible indefinitely and survivorship-bias
  evidence is never silently lost. A provider whose contract requires
  deleting historical data after a bounded window, or on termination, is
  structurally incompatible with that guarantee unless the operator
  separately negotiates a perpetual-storage add-on. [Twelve Data Terms of Use](https://twelvedata.com/terms),
  [Twelve Data commercial/personal usage guidance](https://support.twelvedata.com/en/articles/5332349-commercial-and-personal-usage).
- **Massive's Individual tier is contractually "Non-pros only" and
  personal/non-commercial.** The [Massive for Individuals Terms of Service](https://massive.com/legal/individuals-terms-of-service)
  grant a license "solely for your own personal, non-commercial, and
  non-business purposes," and every Individual stocks/options/futures plan
  is labeled "Non-pros only" with a link to CME/OPRA-style
  Non-Professional Subscriber classification criteria. This platform is
  explicitly being built toward prop-firm accounts and a professional
  multi-account terminal; continuing to run production or even serious paper
  research on an "Individual/Non-pro" licence once that framing is public
  (or once the operator's use crosses the professional-subscriber criteria,
  which is a use-based test, not a self-declaration) is a compliance risk
  independent of price. A Business subscription
  ([massive.com/business](https://massive.com/business)) is the compliant
  path, at materially higher cost. This does not block using Massive later
  as a specialized or cross-check source under an appropriate licence tier.
- **Databento's licensing model is the closest fit to "research now, produce
  later" without a licence-tier cliff.** Databento frames historical data
  purchases as usage-based, pay-once access rather than a
  time-boxed rental, offers a **$125 free trial credit** usable directly
  against historical downloads (6-month expiry), and its own materials state
  it "doesn't apply any redistribution restrictions" beyond pass-through
  publisher terms — with the Plus tier explicitly adding "external
  distribution rights" as a paid upgrade rather than a hard licence-class
  change. [Databento pricing](https://databento.com/pricing). Non-display and
  algorithmic use are explicitly named, intended use cases, not a
  higher-tier carve-out. [Databento subscriber-status guidance](https://databento.com/blog/subscriber-status).
- **Databento's own documentation is the most honest about PIT/identity
  limits**, stating that `listing_id`/`security_id` "survive many
  symbol/name/merger changes, but not every spin-off or non-relisted
  delisting" — i.e., it does not overclaim survivorship-bias completeness.
  [Databento corporate-actions specification](https://databento.com/docs/venues-and-datasets/corporate-actions).
  This module's implementation plan (§6) treats that caveat as a real gap to
  test against, not a marketing footnote.
- None of the three providers' public documentation establishes exact
  Belgian/EU availability, a complete redistribution schedule, an
  SLA, or a final invoiced price for this operator's specific usage pattern.
  Per the existing selection gate in
  [DATA_PROVIDER_MATRIX.md](DATA_PROVIDER_MATRIX.md#selection-and-pilot-gates),
  a written quote and rights schedule remain required before any purchase.

## 4. Selected first provider (subject to owner approval)

**Recommendation: Databento**, for the bounded first vertical slice defined
in §5, using its usage-based historical-data purchase path rather than a
monthly subscription. This is **not an activation** — see §9. This
recommendation is consistent with, and formalizes, the "Recommended" ranking
Databento already held in the pre-existing
[DATA_PROVIDER_MATRIX.md](DATA_PROVIDER_MATRIX.md) and
[DATA_SOURCE_ACTIVATION_PACKAGES.md](DATA_SOURCE_ACTIVATION_PACKAGES.md)
technical evaluations from 2026-08-16; this module adds Twelve Data to the
comparison and confirms the same conclusion holds.

Rationale, in priority order:

1. **PIT/corporate-action rigor.** Databento's `pit=True` corporate-actions
   mode and documented `ts_record` history are the only one of the three that
   directly targets the correction/revision semantics this platform's
   `historical_market_data.py` pipeline already models
   (`AdjustmentStatus`, `QualityStatus`, PIT `research_query`).
2. **Licensing fit for the platform's actual trajectory**, not just its
   current paper-only state. No storage-duration cliff (unlike Twelve Data)
   and no "Non-pros only" personal-use ceiling (unlike Massive) for the
   research tier being considered.
3. **Cost-to-start.** The usage-based historical path plus the $125 trial
   credit lets the first vertical slice (§5) be researched without a
   recurring subscription commitment, matching "recommend one controlled
   first real-data vertical slice" rather than an all-in commitment.
4. **Non-display/algorithmic use is a named, intended use case**, which
   matters because this platform's data is consumed by non-display research
   and (eventually) systematic strategies, never displayed as a market-data
   product to third parties.

### Why the other candidates were deferred, not rejected outright

- **Twelve Data** remains a candidate for **breadth-of-coverage cross-checks**
  (it is the only one of the three with documented mutual funds, fixed
  income, and a single unified API across equities/forex/crypto/commodities)
  once its storage-duration terms are either renegotiated in writing or
  confirmed inapplicable to a specific plan. It is not selected as the first
  provider because the default documented terms conflict with permanent raw
  retention.
- **Massive** remains a strong candidate for **futures** (documented CME/CBOT/
  NYMEX/COMEX coverage under a dedicated Futures subscription) and as a
  **verification/cross-check source** for US equities corporate actions, once
  under an appropriately licensed (Business) tier. It is not selected as the
  first provider because its cost-compliant tier for a professional terminal
  is the Business tier, not the affordable Individual tier evaluated here.
- Both remain explicitly available as **specialized asset-class sources,
  cross-check data, or redundancy/fallback** per the single-provider-first
  policy in §9 — this module does not rule either out of the platform's
  eventual multi-provider future.

## 5. Initial market/data scope (first controlled vertical slice)

Per the "do not activate every asset class at once" instruction, the proposed
first real-data scope is deliberately narrow:

- **Asset classes:** US equities and ETFs only.
- **Granularity:** daily OHLCV and minute OHLCV.
- **Reference data:** symbol/instrument reference data sufficient to resolve
  provider identifiers into the existing
  `PostgresProfessionalInstrumentMaster` (`professional_instruments.py`).
- **Corporate actions:** dividends and splits where authorized under the
  selected plan (delistings/symbol changes are captured if the plan's data
  supports them, but are not a blocking requirement for the first slice).
- **Historical depth:** enough for serious strategy research — the exact
  window (e.g. 10, 15, or 20 years) is an operator budget/scope decision to
  make at approval time, not fixed by this document; Databento's
  usage-based pricing means depth is a cost dial, not a plan-tier cliff.

**Explicitly deferred to later, separate provider adapters:** forex, crypto,
and futures. These follow only after the first ingestion architecture (§6)
is proven end-to-end against real US equity/ETF data — reusing the same
`RawHistoricalAdapter` protocol with a different adapter implementation, not
a parallel pipeline.

## 6. Exact architecture mapping

This reuses every existing authority named in the task; **no new ingestion
architecture is introduced**. The mapping resolves an open question left
implicit by Module 3F: this platform actually has *two* historical-data
authorities — the narrow `HistoricalBarStore` boundary
(`market_data.py`/`postgres_market_data.py`, used by the Data Health worker
job and internal signal-validation gating) and the more complete
authorized-source raw→normalize→seal→research pipeline
(`historical_market_data.py`, used for sealed, versioned research datasets
and eventually Feature Authority). Module 3F deliberately did not merge them.
Module 3G must feed both from **one** real-provider capture, not two:

```
Databento adapter (RawHistoricalAdapter, new in Module 3G)
  -> provider_ingestion.ingest_raw_historical_pages()          [checkpointed, resumable, no cross-provider fallback]
       -> PostgresHistoricalIngestionCheckpointStore.record()  [append-only ProviderIngestionCheckpoint evidence]
       -> historical_market_data.PostgresHistoricalMarketDataPipeline.capture_raw()
            [immutable RawHistoricalObservation: provider identity, provenance_uri,
             event/effective/ingested_at, adjustment_status, revision, raw_payload_sha256]
            -> .normalize()  [resolves provider_identifier -> instrument_id via
                               PostgresProfessionalInstrumentMaster.resolve_identifier_point_in_time();
                               canonical instrument mapping happens exactly here]
                 -> for OHLCV kind, validated normalized rows are also translated to
                    OHLCVBar and passed through the EXISTING market_data.ingest_from_provider()
                    boundary into PostgresHistoricalBarStore.ingest()
                    [same HistoricalBarStore protocol Module 3F already wired into
                     JobContext -- no new store, no new protocol]
                      -> scheduler.run_data_health_evaluation() [Module 3F job, unchanged,
                         now evaluating real bars instead of an empty series]
                 -> once a scope's raw+normalized+health-checked batch is complete,
                    .seal_dataset() [existing HistoricalDatasetVersion: content-hash-bound,
                     immutable, exposes an exact data_version]
                      -> .research_query() [leakage-safe PIT reads, LATEST_ADJUSTED excluded
                         by default -- unchanged from historical_market_data.py today]
                           -> feature_authority.py [FeatureDefinitionVersion /
                              FeatureMaterialization consume the sealed dataset exactly as
                              every other feature family does today]
                                -> strategy/backtest research (research.py, trend_research_v2.py,
                                   strategy_scorecard_v2.py -- unchanged)
```

Key points this mapping makes explicit:

- **Raw capture happens exactly once**, in `capture_raw()`. `ingest_from_provider()` /
  `PostgresHistoricalBarStore` never re-fetches from the provider; it is fed the
  same normalized, already-validated OHLCV rows. This is what "no parallel
  duplicate ingestion architecture" means concretely here.
- **Data Health gates the fast path** (bar-level signal validation via
  `PostgresHistoricalBarStore` + `run_data_health_evaluation`), exactly as
  Module 3F already wired it — this module changes only what feeds it (real
  bars instead of nothing).
- **The sealed-dataset path gates the research path.** Feature Authority and
  strategy/backtest research are only ever pointed at a `SEALED`
  `HistoricalDatasetVersion`, never at in-flight raw or normalized rows —
  this is unchanged from `historical_market_data.py`'s existing contract and
  is exactly what makes backtests reproducible.
- **Canonical instrument mapping is not new work.** It already exists in
  `PostgresProfessionalInstrumentMaster.resolve_identifier_point_in_time()`;
  Module 3G's adapter only needs to supply `provider_identifier_namespace`
  correctly at `AuthorizedHistoricalSource` registration time.
- **The `RawHistoricalAdapter` Protocol (`provider_ingestion.py`) is the only
  new code surface for the provider itself** — a Databento-specific class
  implementing `fetch_raw_page(source_id, scope, cursor) -> RawHistoricalPage`.
  Everything downstream of that one method is existing, reused authority.

## 7. Credentials / secrets requirements

- No credential is created, stored, or referenced by this module.
- When Module 3G is separately approved: the Databento API key is held only
  in the operator's external secret store (matching the existing pattern for
  every other external credential in this codebase — see
  [SECURITY_MODEL.md](SECURITY_MODEL.md)). The repository and CI configuration
  record only a **secret reference name**, never a value.
- `AuthorizedHistoricalSource.authorization_reference` (already a required,
  validated field in `historical_market_data.py`) is the auditable link
  between the persisted data and the operator's actual signed
  agreement/order form; it is populated with a reference the operator
  controls, not a copy of contract text or a credential.
- No credential of any kind is placed in frontend code, logs, or committed
  configuration, consistent with the existing rule in
  [MASTER_ROADMAP.md](MASTER_ROADMAP.md) (RQ-024) and
  [SECURITY_MODEL.md](SECURITY_MODEL.md).

## 8. Rate-limit strategy, retry/backoff, and raw-data retention

- **Rate-limit strategy.** Databento's historical path is usage-based
  (billed by data volume pulled, not requests/minute), so the primary
  constraint is bounded, sequential backfill rather than a fixed RPM budget.
  The existing `ProviderConfiguration`/`RetryPolicy` types in
  `data_providers.py` already model bounded retry/rate control; the Module 3G
  adapter configures those with conservative concurrency (sequential or
  low-parallelism page fetches) so a single backfill run cannot generate an
  unexpectedly large bill or trip any live-tier rate limit if one is added
  later.
- **Retry/backoff behavior.** Fully reused, not reinvented:
  `ingest_raw_historical_pages()` (`provider_ingestion.py`) already
  classifies every attempt as `HEALTHY`, `STALE`, or `ERROR` via
  `ProviderHealthRegistry`, already refuses to loop on a repeated pagination
  cursor, already refuses to fall back to a different provider on error, and
  already persists an append-only `ProviderIngestionCheckpoint` for every
  attempt (successful or not) so a failed run resumes from `resume_cursor`
  rather than re-pulling (and re-billing) already-captured pages. Module 3G
  adds no new retry mechanism — only a Databento-specific `RawHistoricalAdapter`
  that raises the existing `ProviderError`/`HistoricalMarketDataError` types
  on failure so this machinery classifies it correctly.
- **Raw-data retention.** `historical_raw_observations` is already immutable
  and append-only (Module 3F's `prevent_immutable_mutation` trigger pattern
  applies to this table too, per `postgres_schema.py`); nothing this module
  proposes changes that. The open item for approval is confirming, in
  Databento's written agreement, that a purchased historical dataset may be
  retained indefinitely by the operator (matching the platform's permanent
  raw-capture architecture) rather than under any time-boxed access
  condition — this must be confirmed in writing before the first real
  purchase, not assumed from public pricing pages.

## 9. Data Health gates

No change to `data_health.py`'s deterministic policy or to
`run_data_health_evaluation()`'s behavior (Module 3F, unchanged). What
changes is only the *input*: once Module 3G ingests real bars into
`PostgresHistoricalBarStore`, `known_series()` will return real
`(instrument_id, interval)` pairs instead of an empty set, so the existing
job evaluates real missing/duplicate/regressing/impossible-OHLC/staleness/gap
findings against real data for the first time. The job's existing
"never fabricate health" guarantee (documented in Module 3F) is exactly the
gate that must stay intact: a blocking `INSTRUMENT`/`BLOCK_ASSET_CLASS`
assessment on newly-ingested real data must reject signal validation for that
scope exactly as it already does for fixture data — Module 3G adds no
override, bypass, or confidence-boost path.

## 10. Solo-maintainer governance — current state confirmed and adjusted

Verified directly against the live GitHub branch protection API for `main`
during this module's preparation:

| Requirement | State before this module | State after this module |
|---|---|---|
| PR required to merge to `main` | Yes (protection active) | Unchanged |
| Required status check `verify`, strict/up-to-date | Yes | Unchanged |
| `codeql` workflow enabled and running on every PR | Yes (advisory, not a blocking required context) | Unchanged — left advisory; adding matrix-named CodeQL jobs as *required* contexts was judged too risky to change blind in a non-interactive session (a wrong context name would silently block all future merges), so this is flagged for the operator to enable manually in GitHub's branch-protection UI if a hard CodeQL gate is wanted |
| Required conversation resolution | Yes | Unchanged |
| Required approving reviews | 0 (no second reviewer required) | Unchanged |
| CODEOWNERS retained | Yes (`.github/CODEOWNERS`) | Unchanged |
| Force pushes blocked | Yes | Unchanged |
| Branch deletion blocked | Yes | Unchanged |
| No admin bypass (`enforce_admins`) | **No** — admins could bypass required checks | **Fixed: `enforce_admins` set to `true`** via the GitHub API so even an owner/admin merge cannot skip the required `verify` check or conversation-resolution gate |

Dependabot policy is unchanged by this module: it remains enabled, and
routine dependency PRs are merged after an inspection of dependency/release/
security impact once green, without waiting for a second human reviewer —
consistent with the zero-required-approvals setting above.

## 11. Proposed Module 3G implementation plan (not started by this module)

This is a plan, not an authorization. Each step remains gated on the prior
step's evidence, and the whole module remains gated on owner approval of the
recommendation in §4.

1. **Owner approval gate.** Operator reviews §2–§4, approves (or redirects)
   the provider choice, approves the exact initial universe (symbol list or
   selection rule) and historical window/budget, and separately provisions a
   Databento account/API key in the external secret store. No code in this
   step.
2. **`AuthorizedHistoricalSource` registration.** Record the approved
   provider, dataset name, `provider_identifier_namespace`, accepted terms
   version, and the operator's `authorization_reference` — through
   `PostgresHistoricalMarketDataPipeline.register_source()`, unchanged.
3. **`DatabentoRawHistoricalAdapter`** implementing `RawHistoricalAdapter`
   (`provider_ingestion.py`): translates Databento's historical bulk-download
   response pages into `RawHistoricalPage`/`RawHistoricalObservation` records,
   preserving Databento's own identifiers, `ts_record`/revision evidence, and
   response version as `provider_version`. Disabled by default
   (feature-flagged off) until step 1's credential exists in the environment.
4. **Fixture-backed adapter contract tests** mirroring the existing
   `data_providers.py` fixture-transport tests: pagination, cursor-loop
   rejection, rate/error classification, and provenance-mismatch rejection —
   all against recorded/fixture Databento response shapes, not live calls.
5. **Bounded, read-only, authorized pilot** against a small real universe
   (e.g. 10–25 US equities/ETFs spanning at least one rename, one split, one
   cash dividend, and one delisted security if the plan's data supports it) —
   per the existing acceptance checklist in
   [DATA_SOURCE_ACTIVATION_PACKAGES.md](DATA_SOURCE_ACTIVATION_PACKAGES.md#non-negotiable-acceptance-checks-after-approval).
6. **Wire the OHLCV bridge** from `normalize()`'s validated output into
   `ingest_from_provider()`/`PostgresHistoricalBarStore`, as mapped in §6, so
   the Module 3F Data Health job evaluates real data for the first time.
7. **Seal the first real dataset version** and run a real
   `research_query()` PIT read, confirming a decision timestamp cannot see a
   later Databento correction — the same non-negotiable check already listed
   in the activation-packages document.
8. **Extend `MASTER_ROADMAP.md`/`CURRENT_STATUS.md`/`DATA_PROVIDER_MATRIX.md`**
   with the real evidence (exact run IDs, universe, error rate, cost from the
   actual invoice) once the pilot completes — never before.
9. **PR → `verify` + CodeQL → protected merge → exact-main verification**,
   following the same workflow as every prior module.

Module 3G does **not** begin automatically after this document merges. It
requires the owner's explicit go-ahead referenced in step 1.

## 12. Hard boundaries (unchanged)

- Live trading remains disabled.
- No broker integration; no prop-firm integration.
- No market-data provider credentials exist anywhere in this repository or
  its CI configuration.
- No real provider API call was made to produce this document — all
  provider facts came from each vendor's public documentation and pricing
  pages.
- No order submission capability exists or is added.
- No change to risk, identity, RBAC, session, CSRF, secrets or audit
  controls beyond the branch-protection `enforce_admins` fix in §10, which
  strengthens rather than weakens those controls.

## 13. Sources

- [Twelve Data pricing](https://twelvedata.com/pricing)
- [Twelve Data documentation](https://twelvedata.com/docs)
- [Twelve Data Terms of Use](https://twelvedata.com/terms)
- [Twelve Data commercial/personal usage guidance](https://support.twelvedata.com/en/articles/5332349-commercial-and-personal-usage)
- [Massive pricing](https://massive.com/pricing)
- [Massive futures product page](https://massive.com/futures)
- [Massive dividends documentation](https://massive.com/docs/rest/stocks/corporate-actions/dividends)
- [Massive for Individuals Terms of Service](https://massive.com/legal/individuals-terms-of-service)
- [Massive business pricing](https://massive.com/business)
- [Databento pricing](https://databento.com/pricing)
- [Databento US Equities Summary example](https://databento.com/docs/examples/equities/closing-prices)
- [Databento corporate-actions specification](https://databento.com/docs/venues-and-datasets/corporate-actions)
- [Databento symbology standard](https://databento.com/docs/standards-and-conventions/symbology)
- [Databento subscriber-status guidance](https://databento.com/blog/subscriber-status)

No candidate is selected, activated, or contracted by this document. It
records a research-based recommendation only, pending owner approval.
