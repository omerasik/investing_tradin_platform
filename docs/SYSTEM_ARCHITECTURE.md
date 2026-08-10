# System Architecture

The initial architecture is a modular Python monolith with explicit domain boundaries and event contracts: instrument master, data/provenance, research/backtest, signal, portfolio, independent risk, paper OMS/simulation, investment intelligence, audit/observability, and API. It avoids premature microservices while preserving adapter boundaries.

```text
providers -> data quality/provenance -> feature/research -> signals -> independent risk -> paper OMS/simulator
                                      -> investment analysis -> recommendations       -> audit events
dashboard/API <------------------------------------------------------------ read models
```

PostgreSQL, analytical time-series storage, object storage, Redis, and a durable event bus are deployment targets, not local requirements. Initial development uses deterministic local fixtures and SQLite-compatible metadata abstractions. Upstream code is not imported. Third-party systems can only be wrapped behind version-pinned adapters after license/security review.
