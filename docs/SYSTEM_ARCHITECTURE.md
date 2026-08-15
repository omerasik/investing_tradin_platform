# System Architecture

The initial architecture is a modular Python monolith with explicit domain boundaries and event contracts: instrument master, data/provenance, research/backtest, signal, portfolio, independent risk, paper OMS/simulation, investment intelligence, audit/observability, and API. It avoids premature microservices while preserving adapter boundaries.

```text
providers -> data quality/provenance -> feature/research -> signals -> independent risk -> paper OMS/simulator
                                      -> investment analysis -> recommendations       -> audit events
dashboard/API <------------------------------------------------------------ read models
```

PostgreSQL, analytical time-series storage, object storage, Redis, and a durable event bus are deployment targets, not local requirements. Initial development uses deterministic local fixtures and SQLite-compatible metadata abstractions. Upstream code is not imported. Third-party systems can only be wrapped behind version-pinned adapters after license/security review.

PostgreSQL now has an initial Alembic-managed normalized transactional schema.
The `persistence` boundary selects SQLite for local test use or PostgreSQL by
explicit configuration, so business rules do not choose a SQL dialect. This is
only the first persistence migration: existing legacy SQLite repositories are
not yet all moved behind that boundary, and schema presence alone is not a
production-readiness claim.

The configured paper composition now owns policy, instrument/session, signal,
model approval, quote/execution/return evidence, risk, OMS, broker event/cursor,
reconciliation, validation and promotion authorities in PostgreSQL with no
silent SQLite fallback. A durable recovery gate blocks risk increase after a
restore until reconciliation. This paper cutover is not live readiness.

The P1 data path begins with `PostgresProfessionalInstrumentMaster`, upstream of
future provider normalization. Provider/broker symbols must resolve through its
temporal mapping tables before observations become canonical. Calendar logic
evaluates venue-local weekly windows and append-only exceptions; it fetches no
schedule/data and has no execution authority.

`PostgresHistoricalMarketDataPipeline` now follows that authority with raw
capture, normalization, quality status, sealed dataset versioning and PIT
research queries for the US equity/ETF slice. The provider adapter remains
outside the trusted core and cannot register a source without recorded terms
and authorization evidence.

`PostgresDataHealthStore` is between historical normalization/research and
operational signal validation. It persists policy-versioned findings and its
fail-closed gate is duplicated at the PostgreSQL signal-validation boundary so
a caller cannot bypass it with a direct repository insert.
