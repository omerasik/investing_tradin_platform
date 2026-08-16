# Data-source activation packages

Status: **EXTERNAL_BLOCKED**. Prepared 2026-08-16 from current official
documentation. This document is a decision aid, not a provider selection,
purchase order, acceptance of terms, credential request, or authorization to
send a network request. The platform remains fixture-only until an operator
records an approval.

## 1. US equities / ETF historical-research data

### Ranked shortlist

| Rank | Provider and product | Coverage and corporate actions | PIT / identifiers | API and authentication | Licensing, cost and Belgium | Integration | Operator action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Recommended | [Databento US Equities Summary and Reference Corporate Actions](https://databento.com/docs/examples/equities/closing-prices) | Official Nasdaq NLS+ normalized US-equities EOD OHLCV; the corporate-actions service covers dividends, splits, listings, delistings and more event types. Raw/unadjusted bars and the separately versioned adjustment/corporate-action records must both be retained. | Its `pit=True` corporate-actions mode retains prior records by `ts_record`; `listing_id`/`security_id` survive many symbol/name/merger changes, but not every spin-off or non-relisted delisting. That is the strongest documented PIT and canonical-ID fit. | HTTPS historical/reference clients, API key required. Rate and entitlement limits must be verified in the signed plan rather than assumed. | Commercial data and storage/research/redistribution rights are entitlement and exchange-license dependent. Treat cost as enterprise/usage-priced. Belgian availability and any legal/tax restrictions require provider confirmation; no claim is made here. | Medium: adapter exists; map provider IDs, preserve raw payloads, seal raw and adjusted datasets separately. | Approve product, legal entity, Belgian availability, internal research/storage rights, historical retention, requested exchanges and budget; accept terms outside the repo; place the key only in the operator secret store. |
| Second choice | [Massive Stocks REST API](https://massive.com/docs/rest/stocks) | Historical stock aggregates plus documented split endpoints and adjusted/unadjusted aggregate views. It is practical for EOD OHLCV and common corporate actions. Delisting and full symbol-change continuity must be demonstrated under the selected plan before research use. | The docs exposed in this review do not establish a complete PIT revision history or a durable cross-event security master. Retain raw responses and use the platform instrument master rather than treating a ticker as canonical. | REST API, API key and plan limits apply. Confirm concrete request/concurrency limits in the selected contract. | Plan tiers are published, but research storage/redistribution and commercial rights must be approved from the actual agreement. Belgium availability is not inferred. | Low–medium for bars/splits; higher for PIT corporate-action and identifier evidence. | Approve exact plan, contractual research/storage rights, exchange scope, corporate-action and delisting requirements, Belgium availability and budget; provide a secret through the operator channel only. |
| Fallback | [Tiingo EOD and Corporate Actions](https://www.tiingo.com/documentation/end-of-day) | EOD OHLCV exposes raw and CRSP-method adjusted fields and checks splits, dividends and listing changes. Dividend/distribution endpoints are documented; the broader corporate-action endpoints are beta/enterprise-gated. | `permaTicker` is documented for delisted/recycled symbols, but full PIT/revision semantics and complete delisting history are not established by the public material reviewed. Preserve raw payloads and prohibit latest-adjusted backtests. | REST API using an account token; higher usage/commercial use requires the appropriate plan. | Cost category is subscription/commercial add-on. Corporate-action beta/enterprise entitlement and all internal research/storage/redistribution rights require written confirmation. Belgium availability must be confirmed. | Medium: straightforward bars, but provider-specific corporate-action and PIT qualification remains. | Obtain written confirmation of EOD, corporate-action, `permaTicker`, historical retention, internal research rights, Belgium availability and rate limits; then approve a plan and provision a secret outside Git. |

The shortlist deliberately excludes broker-first market-data options: they can
be useful for a future realtime upgrade, but a broker account is not evidence
of the historical/PIT/research rights required here. All three candidates have
a possible REST/streaming upgrade path, but none may be treated as a realtime
authorization until separately approved.

### Non-negotiable acceptance checks after approval

1. Ingest a small authorized universe into immutable raw storage; do not
   replace raw data with vendor-adjusted data.
2. Prove stable identifier and ticker-history mapping on a rename, a split,
   a cash dividend and a delisted security.
3. Seal separately reproducible raw, adjusted and corporate-action datasets.
4. Demonstrate that a decision timestamp cannot see a later vendor correction,
   revision or ingestion.
5. Record the accepted terms/version, entitlement scope, rate limit, price
   category, retention and internal-research rights in the authorized-source
   authority before any scheduled ingestion.

## 2. SEC fundamentals activation package

The existing SEC-style PIT core remains **EXTERNAL_BLOCKED**. `data.sec.gov`
does not require an API key, but that does not waive the SEC fair-access
requirements or this project's operator-identity gate.

The operator must provide and approve the following configuration outside Git:

| Requirement | Required implementation rule |
| --- | --- |
| Identifying User-Agent and contact | Configure a truthful, operator-approved User-Agent naming the organization/application and a monitored contact address. The SEC says requests are expected to identify the software/vendor/version and can reject invalid User-Agent values. Never invent an identity or contact. |
| Fair access | Apply a shared limiter below the SEC's published maximum of 10 requests/second across machines/IPs, with bounded concurrency, exponential backoff with jitter for 429/5xx, and a circuit-breaker/stale checkpoint state. Treat the stated threshold as a ceiling, not a target. |
| Caching and conditional retrieval | Retain raw response bytes, response headers, request/ingestion timestamps and source URL. Honour `ETag`, `Last-Modified` and `Cache-Control` where supplied; use conditional retrieval when supported. |
| PIT provenance | Persist accession number, CIK, form, filing/acceptance timestamp, source URL, retrieval time, parser/calculation version, raw-content hash and every parsed fact's units/period/context. Acceptance time—not fiscal period end—is the default knowledge-time boundary. |
| Raw filing retention | Keep the exact filing/XBRL artifact or an immutable, policy-approved archival reference plus hash. A parsed Company Facts value alone is insufficient for audit or restatement handling. |
| Company Facts limitations | Company Facts is extracted XBRL data, not a complete filing parser or a guarantee of comparable standardized facts. It may contain multiple contexts, amendments, taxonomy variation, extensions and later corrections. Keep the primary filing as provenance and select facts with explicit concept/unit/context/form rules. |
| Filing parser | Parse filing submissions and inline XBRL/XBRL contexts by accession; handle amendments, restatements, duplicates, dimensions, units/scales, period type and filing acceptance timestamps. No generic 'latest fact' shortcut may bypass the PIT authority. |

Official references: [SEC EDGAR data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[SEC API toolkit User-Agent and cache guidance](https://api.edgarfiling.sec.gov/),
and [SEC rate-control notice](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits).

## 3. Macro activation package

The macro core must retain observation period, release time, vintage/revision
time, ingestion time, source and raw payload. It must never overwrite an older
vintage with today's revised value.

| Source | Appropriate role | Vintage/revision handling | Operator requirement |
| --- | --- | --- | --- |
| [FRED / ALFRED](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html) | Primary catalogue for US and many cross-source series. | **Required** for any series used in a historical decision: default FRED requests represent today's knowledge; ALFRED real-time periods/vintage dates provide what was known then. Store output type, real-time window and source release metadata. | Register/approve an API key and terms outside Git; approve selected series and request budget. |
| [BLS Public Data API](https://www.bls.gov/developers/api_faqs.htm) | Direct source for labour, CPI/PPI and related US releases. | **Required** for release-driven research. Store release timestamp and revision/vintage artifact; do not assume one current API value is PIT. | Choose registered/unregistered access only after operator accepts current limits/terms; configure a non-secret registration key if required through the secret store. |
| [BEA API](https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf) | Direct source for GDP, national accounts and related releases. | **Required** for GDP/account revisions: persist release/vintage and revision lineage. | Obtain/approve key and throttling policy; keep within BEA's changeable protective limits. |
| [US Treasury FiscalData](https://fiscaldata.treasury.gov/api-documentation/) | Treasury fiscal and debt series; use Treasury's interest-rate publications only where their publication/revision semantics meet the feature definition. | Usually required for released fiscal/debt figures; model publication and correction time explicitly. | Approve source/product/series and lawful request budget; document the selected endpoint's update semantics. |
| [ECB SDMX](https://data.ecb.europa.eu/help/getting-data-web-services-sdmx-0) | Euro-area rates, inflation, money and FX-related official statistics. | **Required** for released/revised macro series: the API supports `updatedAfter` and `includeHistory=true`; retain the returned version history. | Approve series/dataflow, update policy and API use before activation. |
| [Eurostat dissemination API](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-getting-started) | EU/Belgian macro and structural series. | **Required**, but the public database serves the latest version and does not document/version past values; snapshot raw releases at ingestion or use another approved vintage archive for PIT research. | Approve exact datasets and retention/snapshot policy; do not treat latest-only history as PIT. |

Revision/vintage-aware sources are mandatory for GDP/national accounts, CPI/PPI,
employment/payrolls, retail/activity releases, policy-rate/curve releases when
revised, and any surprise or release-timing feature. A non-revised market level
can be catalogued differently only after its source publication policy is
recorded. No source activation, key creation, terms acceptance or network
retrieval is authorized by this document.
