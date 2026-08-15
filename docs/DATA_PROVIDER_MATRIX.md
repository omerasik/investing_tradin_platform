# Data Provider Matrix

## Status and decision rule

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
| [Databento](https://databento.com/docs) | Historical and live market-data APIs; its US Equities Summary [example](https://databento.com/docs/examples/equities/closing-prices) documents daily US equity closing prices. Its [corporate-actions specification](https://databento.com/docs/venues-and-datasets/corporate-actions) documents listing-level, point-in-time records, delisted/relisted continuity, explicit identifiers, and updates four times daily; [symbology](https://databento.com/docs/standards-and-conventions/symbology) preserves historical symbols at their original time. | Strongest documented candidate for a PIT corporate-action/instrument-history evaluation and later trades/quotes, subject to entitlement. | Public pages do not establish this product's exact plan entitlement, storage/redistribution rights, Belgium availability, support/SLA, or total cost for this operator. Obtain a current written quote and rights schedule; do not infer them from marketing pages. | `CANDIDATE_NOT_SELECTED` |
| [Alpaca Market Data](https://docs.alpaca.markets/us/docs/about-market-data-api) | HTTP and WebSocket market data for equities/options/crypto; [historical bars](https://docs.alpaca.markets/us/reference/stockbarsingle-1) document stock feeds, pagination, rate-limit responses, and an `asof` symbol-resolution parameter. Historical API docs also list news. | Useful secondary candidate for historical bars and later real-time/crypto evaluation. Its documented `asof` symbol mapping is relevant but is not a substitute for a complete, independently evidenced PIT corporate-action history. | The public docs require API authentication. Exact feed/venue entitlement, historical depth under the operator's plan, corporate-action API rights, redisplay/redistribution, Belgian availability and production support remain unverified. | `CANDIDATE_NOT_SELECTED` |
| [Massive / legacy Polygon.io](https://massive.com/docs) | The former Polygon documentation now redirects to Massive. Official [dividend documentation](https://massive.com/docs/rest/stocks/corporate-actions/dividends) describes historical dividends, adjustment factors and `next_url` pagination; official [news documentation](https://massive.com/docs/rest/stocks/news) describes timestamped news and pagination; stock WebSocket documentation is published [here](https://massive.com/docs/websocket/stocks/overview). | Candidate for bars/corporate actions and later news/streaming; adapter must preserve provider identifiers, response version evidence and paging URLs without assuming cross-provider equivalence. | Product/brand transition, plan-level access, endpoint stability, current price, redistribution/storage rights, venue coverage, rate limits, Belgium availability and SLA require an operator-reviewed quote and agreement. No paid/free plan was activated. | `CANDIDATE_NOT_SELECTED` |
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
