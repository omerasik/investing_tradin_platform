# Data Architecture

Raw, normalized, point-in-time research, training, validation, test, paper and eventual production datasets are logically separate. Every record needs provider, source identifier, ingest/event/effective timestamps, original and normalized time zone, revision, data version, processing state, and quality score.

## Transactional persistence

Production persistence is PostgreSQL and is managed by forward-only Alembic
migrations. The initial normalized schema has explicit relational identity,
version, timestamp, provenance and `NUMERIC` financial fields for instruments,
datasets, market data, research, validation packages, promotion, risk, paper
OMS, investments, agents and audit events. JSONB is restricted to variable
payloads/contract details; it is not used in place of durable identifiers or
financial values. Immutable evidence/event tables have database triggers that
reject update/delete operations, and point-in-time/version queries have indexes.

SQLite remains a bounded local/unit-test adapter. It is not the production
target and paper/production configuration fails closed unless PostgreSQL is
selected. The current legacy SQLite-to-PostgreSQL tool supports deterministic
row-count/checksum inspection in dry-run mode and applies only with an explicit
legacy identity mapping. CI proves mapped APPLY, exact financial values,
idempotent replay, conflict/unsupported rejection, destination reconciliation
and restart reconstruction. It cannot silently invent normalized foreign keys;
old validation packages remain `LEGACY_UNVERIFIABLE`.

Initial canonical contracts cover instruments, OHLCV bars, corporate actions, macro releases, signals and order intents. The data-quality gate rejects missing/duplicate/time-regressing bars, invalid prices/volumes, stale data, currency inconsistency and below-threshold quality. Backtests may query only data available at the decision timestamp.

News/event metadata is stored separately from raw provider content. Each event has a provider-local item ID, canonical entity, URL, publication and ingestion times, topics, optional sentiment, extraction confidence, source reliability and data version. An event can be eligible for research only when its source license is explicitly approved and its sentiment is available; its uncertainty is preserved rather than inferred away. No external source is connected until licensing and credentials are approved.
