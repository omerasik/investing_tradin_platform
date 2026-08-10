# Data Architecture

Raw, normalized, point-in-time research, training, validation, test, paper and eventual production datasets are logically separate. Every record needs provider, source identifier, ingest/event/effective timestamps, original and normalized time zone, revision, data version, processing state, and quality score.

Initial canonical contracts cover instruments, OHLCV bars, corporate actions, macro releases, signals and order intents. The data-quality gate rejects missing/duplicate/time-regressing bars, invalid prices/volumes, stale data, currency inconsistency and below-threshold quality. Backtests may query only data available at the decision timestamp.

News/event metadata is stored separately from raw provider content. Each event has a provider-local item ID, canonical entity, URL, publication and ingestion times, topics, optional sentiment, extraction confidence, source reliability and data version. An event can be eligible for research only when its source license is explicitly approved and its sentiment is available; its uncertainty is preserved rather than inferred away. No external source is connected until licensing and credentials are approved.
