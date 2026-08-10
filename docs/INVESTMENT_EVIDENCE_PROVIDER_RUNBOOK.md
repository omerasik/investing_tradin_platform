# Investment evidence provider activation runbook

This runbook activates long-horizon research evidence only. It does not enable paper or live orders, broker access, allocation changes, or investment recommendation approval.

## Preconditions

1. Obtain and accept the provider's applicable terms and rate-limit policy.
2. Create a deployment-owned mapping from canonical platform instrument IDs to provider identifiers. For SEC Company Facts, map each U.S. issuer to its numeric CIK; do not derive CIKs from ticker symbols at runtime.
3. Choose only reviewed local metrics and their provider concepts. For SEC, use explicit `(taxonomy, concept)` mappings such as `("us-gaap", "NetCashProvidedByUsedInOperatingActivities")`.
4. Supply an identifying, monitored SEC User-Agent with a real organization/contact address as required by SEC policy. This is configuration, not a secret.
5. Set and retain a deployment change record that terms acceptance is true. `SecCompanyFactsProvider` refuses requests otherwise.

## Configure the adapter

Construct `SecCompanyFactsProvider` with the CIK map, the metric-concept map, identifying User-Agent, `terms_accepted=True`, and a conservative request interval. The adapter uses only the fixed HTTPS SEC Company Facts host and records `sec_companyfacts` in provenance.

For a paid HTTPS JSON source, construct `HttpsJsonInvestmentEvidenceProvider` with a `ProviderConfiguration` containing HTTPS base URL, terms acceptance, and only `secret_reference`. Provide a deployment-owned transport that resolves that reference into an authorization header. Never pass a secret value into provider configuration, source records, dashboard configuration, logs, or tests.

## First activation checks

1. Request one mapped issuer over a known historical date range.
2. Validate returned metric/unit mappings and source identifiers before ingestion.
3. Confirm each source URL is HTTPS, timestamps are timezone-aware, and provider health records success.
4. Ingest with `ingest_investment_provider_facts`; inspect the resulting source reference and point-in-time query before using facts in valuations or reviews.
5. Run the investment review scheduler with a non-executing operator and inspect the protected thesis history and alert evidence.

SEC filing records often provide only a filing date. The adapter makes a fact available at the following UTC midnight, rather than assuming an intraday publication time. Do not change this to make an unverified same-day backtest or decision look better.

## Failure and rollback

- On HTTP throttling or service errors, the provider retries only its bounded retry policy. It does not fabricate data or fall back to a different provider unless an explicit `FallbackInvestmentEvidenceProvider` is configured.
- On malformed data, non-HTTPS source provenance, CIK/metric mismatch, or timestamp failure, retain the failure evidence and stop ingestion for that batch.
- Disable the provider by removing it from the selected provider set or setting terms acceptance false; existing immutable investment facts and reviews remain auditable.
- A drift alert is research-only. Acknowledge it through the protected alert API after review; acknowledgement does not alter a thesis, holding, rebalance, order, broker account, or risk limit.

## External activation boundary

No SEC request was made during local tests. Before production use, an authorized operator must confirm current SEC terms/rate limits and provide the real monitored User-Agent/contact. Paid-source activation additionally requires the organization’s licensed account and secret manager integration. Live trading remains deliberately unavailable.
