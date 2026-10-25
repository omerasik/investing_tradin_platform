# Data Provider Matrix

## Status and decision rule

### P3A zero-subscription preflight — 2026-10-25

The permanent `NO_PAID_MARKET_DATA_SUBSCRIPTIONS` decision supersedes the
historical paid-provider ranking below. **No source is eligible for activation
on the evidence reviewed.** Alpaca Basic / IEX is the preferred **technical
fixture contract**, not an approved data licence or usable operator entitlement.
All three candidates remain `EXTERNAL_BLOCKED`. Reading public documentation
does not accept terms, open an account or exercise a data endpoint.

| Candidate | Current official evidence | Rights / access decision |
|---|---|---|
| Alpaca Basic, explicitly `feed=iex` | [Plan documentation](https://docs.alpaca.markets/us/docs/about-market-data-api): free, US stocks/ETFs, history since 2016, 200 historical calls/minute; stock endpoints require key/secret headers. [Historical reference](https://docs.alpaca.markets/us/reference/stockbarsingle-1) specifies inclusive endpoints, opaque page tokens, raw/split/dividend/spin-off adjustments and `asof` remapping. [FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) distinguishes single-exchange IEX from consolidated SIP and describes delayed historical SIP access. This pilot deliberately models IEX only, never a default/best feed. | [Current linked terms](https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf), sections Personal and Non-Commercial Usage, U.S. Residents Only and Content, do not establish this Belgian operator's professional systematic-research, commercial storage or distribution rights. Written consent is required for the stated commercial copying/distribution cases; advance notice is not a licence grant. Permanent immutable retention, retention after termination, internal derived research, applicable underlying-exchange permissions and attribution remain unverified. Account/region entitlement, actual AAPL/SPY history/completeness and a dated terms version also remain unverified. **Technical fixture preference only; EXTERNAL_BLOCKED.** |
| Official IEX HIST (not IEX Cloud) | [Official HIST documentation](https://iextrading.com/trading/market-data/) describes T+1 binary feed captures in pcap/IEX-TP, requiring matching feed/specification versions. [HIST terms](https://www.iex.io/legal/hist-data-terms) identify free provision and permit distribution subject to attribution, retain IEX ownership and disclaim completeness. Their sections 8/10 describe single-venue reference information rather than a basis for trading decisions. | Potentially useful reference archive, but no conclusion here that the intended professional systematic-research use and indefinite evidence retention are licensed. Obtain clarification of that use against the reference-only language; reviewed terms contain no explicit retention period, which is not a perpetual rights grant. Current downloadable range, AAPL/SPY file availability, authentication/access mechanics, rate/concurrency limits and bounded file sizes were not established without acquisition. No current rate limit, guaranteed history or permission is invented. **EXTERNAL_BLOCKED for the intended use; no pcap decoder/backfill added.** |
| Existing disabled Stooq candidate | [Official terms URL](https://stooq.com/terms.html) returned a JavaScript browser-verification page during review. Existing CSV contract is engineering evidence only. | Current licence, permanent storage/retention, internal professional research, attribution, feed/adjustment semantics, access quotas and applicable symbol coverage could not be established from readable official evidence. No third-party assertion of free/commercial use substitutes for those rights. **EXTERNAL_BLOCKED; unchanged.** |

**P3A implementation and acceptance boundary.**
`alpaca_equities_pilot_v1.AlpacaIexFixtureAdapterV1` implements the existing
`RawHistoricalAdapter` contract from explicit offline response pages. Its default
has no pages and refuses access. It has **no HTTP transport, secret resolver,
account/broker API, startup wiring or activation switch**. It accepts only
test-authorized OHLCV sources, never an owner authorization masquerading as a
fixture. `terms_accepted=True` and credential references are rejected. No actual
source registration or calendar/instrument onboarding runs outside tests.

The pilot uses the existing professional `US:XNAS:AAPL` and `US:ARCX:SPY`
identities, with namespace `ALPACA:IEX:RAW:1MIN:SYMBOL:V1`. `IEXG` is retained as
execution venue; XNAS/ARCX remain listing venues in immutable provenance. The
normalizer admits this distinction only for the exact offline IEX contract and
rejects spoofed feed/venue/adjustment metadata; other providers are unchanged.
Fixtures are explicitly `T0_SYNTHETIC`, not historical-provider success.

Scope is explicit `iex`, `raw`, `1Min`, USD, `asof=-`, one of the two symbols,
UTC minute-aligned `[start,end)`, at most five calendar days, closed at least
15 minutes before retrieval. Pages contain at most 100 bars/256 KiB; use the
existing ingestion bound of 40 pages. No retries, plan upgrade or provider
fallback occur. Failed continuation/403/429 or the page bound yields ERROR;
already-captured evidence remains attributable but cannot justify completeness.
The required pre-seal coverage gate checks every expected open minute using
the existing PIT calendar, including wholly missing sessions; missing IEX trades
may legitimately produce no bar, but this conservative pilot refuses such
windows rather than inserting zero-volume/forward-filled bars. It rejects
cross-page duplicates/regression. DST, holidays and early closes remain owned by
the existing calendar, never a new weekdays-only production calendar.

Original JSON bodies and SHA-256, scope/cursor, provider row and version are
retained in immutable raw evidence. Economic values use Decimal and must fit the
existing `NUMERIC(30,12)` exactly; excess precision/range is rejected rather than
rounded. Bar effective time is minute close; retrieval, normalization and seal
times remain honest knowledge boundaries. There is no provider revision ID in
the modeled bars response: revision zero is explicit, changed evidence conflicts
in existing capture, and no correction is silently numbered or overwritten.
Adjusted histories are rejected, not relabelled as original/PIT prices. Page
regrouping/body changes can also conflict and need explicit reconciliation.

Fixture tests demonstrate response -> existing ingestion -> raw capture ->
normalization -> historical bar bridge -> `PostgresHistoricalBarStore` ->
persisted Data Health -> sealed dataset -> existing read-only PIT research query.
PostgreSQL tests roll back all fixture writes; broad and exact-main CI are the
integration gates. A non-blocking fixture health assessment proves only that
bounded engineering example: it establishes neither corporate-action completeness,
survivorship-bias-free universes, consolidated US coverage, historical market
knowability, professional-validation eligibility nor paper/live readiness.
No new source registry, schema, dataset authority or research engine is introduced.

**Exact owner actions before any bounded real pilot:**

1. Obtain and retain written source/underlying-exchange clarification covering
   this operator/entity and Belgium, the exact free plan/feed and symbols,
   internal systematic research/derived calculations, immutable raw/normalized
   storage and post-termination retention, attribution, permitted display and
   distribution (or their explicit exclusion), API access and rate limits.
   If rights require a recurring subscription, reject the source under the
   permanent decision; do not reinterpret this phase as permission to pay.
2. Owner reviews and explicitly accepts the applicable dated/versioned rights
   documents and exact scope. Only the owner may authorize account/credential
   provisioning; P3A creates neither. Retain only approved secret references.
3. After the external blocker is resolved, separately review/authorize the
   missing real transport and real-source onboarding against the existing
   `AuthorizedHistoricalSource`/professional-master contracts. A boolean flip
   cannot activate this fixture-only adapter, and P3A does not implement or
   authorize that later work.
4. Explicitly approve an exact UTC window, symbol/feed/adjustment set and limits
   (initially <=5 calendar days, <=40 pages, <=100 bars/page, concurrency 1,
   <=200 calls/minute with approved pacing, explicit timeout/byte/disk budgets).
   Validate actual entitlement, completeness/corrections and calendar evidence;
   preserve raw/checkpoint/health/seal receipts and stop on any failure. Real
   backfilled data is not T0 and must receive the existing evidence-tier review;
   it gains no historical knowledge-time or holdout/professional-validation credit
   from these fixtures. No provider activation is part of this PR.

The sections below are retained historical comparison evidence, not authority to
buy, license, select or activate their providers.

Update 2026-09-06 (Module 3G.0): Twelve Data was added to this comparison and
a first provider was **recommended** (Databento) for a bounded US
equities/ETF vertical slice, pending explicit owner approval. See
[MODULE_3G0_PROVIDER_SELECTION_AND_LICENSING_PREFLIGHT.md](MODULE_3G0_PROVIDER_SELECTION_AND_LICENSING_PREFLIGHT.md)
for the full comparison matrix, licensing findings, architecture mapping and
proposed (not started) Module 3G implementation plan. This update changes no
conclusion below; it formalizes the "Recommended" ranking Databento already
held here into an actual selection decision, still gated on owner approval
and still with no credential, purchase, or network call made.

This is a **documentation-only technical evaluation** synchronized on 2026-08-16.
No provider account was created, no terms were accepted, no credential was supplied,
and no provider endpoint was called. Consequently, nothing below is evidence of
contractual rights, usable availability in Belgium, measured latency, actual
completeness, or current price. The platform remains paper-only and every external
source remains `EXTERNAL_BLOCKED` until the operator separately approves the source,
rights, terms version, credentials, and a bounded pilot.

An eventual selection must record the exact plan/contract, venue/feed entitlement,
permitted display/storage/redistribution use, rate and concurrency limits, retention,
commercial/legal approval, incident contact, and a fixture-backed acceptance run.
An API's public documentation is not a licence grant.

## Current platform boundary

| Requirement | Existing technical evidence | Current limitation |
|---|---|---|
| Authoritative historical US equity/ETF path | PostgreSQL source authorization → immutable raw capture → normalization → quality gate → sealed versioned dataset → point-in-time research query in [`historical_market_data.py`](../src/trade_platform/historical_market_data.py) | No real source is authorized or activated. |
| Instrument identity and sessions | Exchange/MIC-aware professional master and US/FX/crypto calendars in [`professional_instruments.py`](../src/trade_platform/professional_instruments.py) | The historical source slice is deliberately scoped to US equities/ETFs. |
| Provider adapter controls | HTTPS-only configuration, declared capabilities, bounded retry/rate control, pagination and cache provenance in [`data_providers.py`](../src/trade_platform/data_providers.py) | The only concrete adapter is the disabled Stooq daily CSV candidate; it is not authoritative. |
| Quality and promotion gate | Missing/stale/gap/duplicate/OHLC/corporate-action/timezone/provider-disagreement checks and persisted blocking assessments in [`data_health.py`](../src/trade_platform/data_health.py) | Fixtures prove behaviour; no real-provider quality evidence exists. |
| Fundamentals and macro | Point-in-time PostgreSQL cores in [`pit_fundamentals.py`](../src/trade_platform/pit_fundamentals.py) and [`pit_macro.py`](../src/trade_platform/pit_macro.py) | No SEC or macro source authorization, retrieval, or coverage test exists. |

The test-only `test-authorization://` and `fixture://` references prove fail-closed
behaviour only. They never grant a right to store, redistribute, or represent market
data as real.

## Provider snapshot (official documentation only)

| Candidate | Documented capabilities relevant to the target | Technical fit | Rights, cost and availability conclusion | Decision |
|---|---|---|---|---|
| [Twelve Data](https://twelvedata.com/docs) | Single unified API spanning US/global equities, ETFs, forex, crypto, indices, commodities, fixed income and mutual funds; `time_series` intervals from 1min to 1month; dividends/splits/earnings endpoints with an `adjust` parameter. | Broadest single-API asset-class coverage of any candidate reviewed, useful for later cross-asset comparison. | [Terms of Use](https://twelvedata.com/terms) §2.3(g)/§16.1-16.2 restrict how long raw data may be cached and require deletion within 30 days of termination — structurally incompatible with this platform's permanent immutable raw-capture architecture unless separately renegotiated. Individual tiers are personal/internal-use only. | `CANDIDATE_NOT_SELECTED` (see Module 3G.0 for the full finding) |
| [Databento](https://databento.com/docs) | Historical and live market-data APIs; its US Equities Summary [example](https://databento.com/docs/examples/equities/closing-prices) documents daily US equity closing prices. Its [corporate-actions specification](https://databento.com/docs/venues-and-datasets/corporate-actions) documents listing-level, point-in-time records, delisted/relisted continuity, explicit identifiers, and updates four times daily; [symbology](https://databento.com/docs/standards-and-conventions/symbology) preserves historical symbols at their original time. | Strongest documented candidate for a PIT corporate-action/instrument-history evaluation and later trades/quotes, subject to entitlement. | Public pages do not establish this product's exact plan entitlement, storage/redistribution rights, Belgium availability, support/SLA, or total cost for this operator. Obtain a current written quote and rights schedule; do not infer them from marketing pages. | `RECOMMENDED_PENDING_OWNER_APPROVAL` (Module 3G.0, 2026-09-06) |
| [Alpaca Market Data](https://docs.alpaca.markets/us/docs/about-market-data-api) | HTTP and WebSocket market data for equities/options/crypto; [historical bars](https://docs.alpaca.markets/us/reference/stockbarsingle-1) document stock feeds, pagination, rate-limit responses, and an `asof` symbol-resolution parameter. Historical API docs also list news. | Useful secondary candidate for historical bars and later real-time/crypto evaluation. Its documented `asof` symbol mapping is relevant but is not a substitute for a complete, independently evidenced PIT corporate-action history. | The public docs require API authentication. Exact feed/venue entitlement, historical depth under the operator's plan, corporate-action API rights, redisplay/redistribution, Belgian availability and production support remain unverified. | `CANDIDATE_NOT_SELECTED` |
| [Massive / legacy Polygon.io](https://massive.com/docs) | The former Polygon documentation now redirects to Massive. Official [dividend documentation](https://massive.com/docs/rest/stocks/corporate-actions/dividends) describes historical dividends, adjustment factors and `next_url` pagination; official [news documentation](https://massive.com/docs/rest/stocks/news) describes timestamped news and pagination; stock WebSocket documentation is published [here](https://massive.com/docs/websocket/stocks/overview); a separate Futures subscription documents CME/CBOT/NYMEX/COMEX coverage. | Candidate for bars/corporate actions and later news/streaming/futures; adapter must preserve provider identifiers, response version evidence and paging URLs without assuming cross-provider equivalence. | Product/brand transition, plan-level access, endpoint stability, current price, redistribution/storage rights, venue coverage, rate limits, Belgium availability and SLA require an operator-reviewed quote and agreement. Its [Individual Terms of Service](https://massive.com/legal/individuals-terms-of-service) restrict every affordable tier to "personal, non-commercial, non-business" ("Non-pros only") use, which conflicts with this platform's professional multi-account terminal positioning at that tier; a Business subscription would be required. No paid/free plan was activated. | `CANDIDATE_NOT_SELECTED` — deferred as a future futures/cross-check source under an appropriate licence tier (Module 3G.0) |
| [Tiingo](https://www.tiingo.com/documentation/general) | Public documentation indexes EOD, news, crypto, FX, equity real-time/IEX, BOATS, fundamentals, dividends and splits. Its [stock product page](https://www.tiingo.com/products/stock-api) describes IEX historical/realtime and a fundamentals product including active and delisted filing issuers. | Breadth makes it a comparison candidate, particularly for later FX/crypto/news and fundamentals. It is not evidence that the required US consolidated history, PIT revision handling or corporate-action history matches the primary research requirement. | Exact source/venue, plan entitlement, historical depth, field semantics, storage/redistribution rights, rate limits, Belgian availability, reliability/SLA and price are not accepted or verified. | `CANDIDATE_NOT_SELECTED` |
| [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Primary issuer-filing source suitable for a future point-in-time filing adapter; the repository already models acceptance/filing/ingestion times and raw evidence. | Preferred primary-source investigation for US filings, not a replacement for a market-data vendor. | Fair-access requirements, acceptable user-agent/rate behaviour, completeness, amendment handling, storage and operational limits need an explicit preflight. No SEC request was made. | `EXTERNAL_BLOCKED` |
| [FRED API](https://fred.stlouisfed.org/docs/api/fred/) | Official macroeconomic API candidate; the repository already represents release/revision/ingestion timestamps. | Candidate for a bounded US macro pilot after authorization. | Series-specific licence, revisions/vintage semantics, availability, rate limits, redistribution, and fit for each required macro series must be checked before any call. | `EXTERNAL_BLOCKED` |

## Requirement coverage and open evidence

| Requirement | Candidate direction | What remains unproven / required before selection |
|---|---|---|
| US equities/ETF historical bars | Databento, Alpaca, Massive, Tiingo | Universe including delisted securities, adjusted/unadjusted semantics, exact intraday retention, pagination limits, source timestamps, correction policy, and total cost. |
| Corporate actions and delisting history | Databento first; Massive/Alpaca/Tiingo comparison only | Completeness for dividends/splits/mergers/spinoffs/symbol changes/delistings, PIT revisions, stable IDs, effective/announcement timestamps, adjustment factors, and rights. |
| Fundamentals | SEC EDGAR first; Tiingo comparison only | Filing acceptance timestamps, amendment/restatement lineage, taxonomy/units, issuer mapping, rate/fair-use policy and reconciliation sample. |
| Macro | FRED first; additional official source per series | Vintage/revision policy, release timestamp precision, series coverage and license/redistribution conditions. |
| Later real-time quotes/trades | Databento, Alpaca, Massive, Tiingo | Exact feed entitlement (SIP/IEX/proprietary/venue), delayed vs real-time status, reconnect/replay policy, latency measurement, rate/concurrency limits and display rights. |
| Later news | Massive, Alpaca, Tiingo | Article-content versus metadata rights, source attribution, retention, redistribution/display permission, timestamp/revision semantics and NLP licensing. |
| Later crypto/FX | Alpaca (crypto) and Tiingo (crypto/FX) are documented candidates; Massive also documents crypto/forex product sections | Venue/exchange list, trade/quote semantics, market hours, historical depth, stable instruments, geographic availability and rights. |
| Later BIST / Turkey | No source evaluated or selected | Identify an official/licensed BIST-compatible vendor; verify local market-data entitlements, corporate actions, holidays, instrument identifiers, Turkish-language news rights and operator jurisdiction. |

## Selection and pilot gates

1. The operator chooses at least one candidate and supplies written commercial/legal
   approval, exact terms version and secret reference. The code must record only the
   reference, never the secret.
2. Implement that candidate behind a disabled configuration and fixture contract.
   It must emit raw attributable records, request/page identity, source timestamps,
   provider/data version and field provenance; it may not silently replace data with
   another provider.
3. Run a bounded, read-only, authorized pilot. Compare a representative US equity,
   ETF, renamed issuer, delisted issuer, dividend, split and revised filing/macro
   observation against documented source evidence. Measure error rate, freshness,
   gaps, corrections, retry/rate-limit behaviour and cost from actual invoices/usage.
4. Persist the raw result, data-quality outcome and a sealed dataset version. Promote
   only if the data-health gate is non-blocking; otherwise retain explicit `ERROR` or
   `STALE` evidence and do not fall back silently.

No candidate is selected by this document, no adapter is activated by it, and it does
not change the paper-only/live-trading safety boundary.
