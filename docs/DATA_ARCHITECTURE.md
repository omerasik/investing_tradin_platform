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

Cycle 206 extends this prototype with PostgreSQL-native versioned source-rights
policies, publication/source-update/ingestion clocks, hashed document revisions,
deterministic duplicate clusters, explicit professional-instrument links,
versioned event taxonomy, Data Health bindings and correction/retraction lineage.
Point-in-time reads select the latest then-known revision and retractions produce
confidence withdrawal. These are research evidence contracts only: provider
activation and automatic confidence/risk increases are structurally absent.

## Professional instrument and calendar master

Migration 0008 is the PostgreSQL authority for canonical metadata, temporal
symbols and provider/broker/standard IDs, lifecycle events and calendars.
Resolution applies `[valid_from, valid_until)` and `ingested_at <= as_of`;
database exclusion constraints reject overlap. Delisting and calendar
exceptions are append-only. Futures metadata is explicitly reserved, but no
future or continuous contract is activated.

## Authorized historical market-data vertical slice

Migration 0009 implements the US equity/ETF provider-neutral path as five
immutable PostgreSQL layers: explicitly authorized source, raw observation,
normalized observation, sealed dataset version and dataset membership. Raw
OHLCV/dividend/split/symbol-change/delisting records preserve provider identity,
exchange, event/effective/ingestion times, adjustment status, revision,
canonical raw hash and provenance URI. Normalization resolves the provider ID
through the professional instrument master at separate effective/knowledge
times and records rejected quality evidence without repairing it.

Research reads are fixed to a sealed dataset, expose the exact data version and
select only revisions ingested by the knowledge timestamp. `LATEST_ADJUSTED`
observations are excluded unless a caller makes an explicit leakage-sensitive
override. This infrastructure does not itself grant source rights: a real
provider remains disabled until an operator supplies an approved authorization
reference and terms version.

## Persistent data health

Migration 0010 stores immutable scoped assessments and ordered findings. The
deterministic policy detects missing/duplicate/regressing bars, impossible
OHLC, invalid volume, staleness, gaps, corporate-action mismatch, provider
disagreement, timezone/session mismatch and incomplete coverage. Every result
uses one of `INFO`, `WARN`, `DEGRADE_CONFIDENCE`, `BLOCK_INSTRUMENT`,
`BLOCK_STRATEGY`, `BLOCK_ASSET_CLASS` or `GLOBAL_BLOCK`.

The latest assessment per applicable global/asset/strategy/instrument scope is
the gate state as of a signal assessment timestamp. A blocking state rejects a
PostgreSQL `VALIDATED` signal both in the application repository and in a
database trigger; a later clean immutable assessment reopens only that scope.

## Point-in-time fundamental filings

Migration 0011 adds an explicitly authorized filing source, immutable SEC-style
filing records and immutable as-reported/standardized facts. Filing ID, filing
and acceptance timestamps, reporting/fiscal periods, revision, ingestion time,
raw hash and provenance URI remain first-class. Historical reads require both
`accepted_at` and `ingested_at` to be no later than the query and select only
the latest then-known revision.

Transparent formula v1 derives revenue, operating margin, FCF, debt, shares,
dilution, NOPAT/invested-capital/ROIC and capital allocation from named
standardized inputs. It fails on missing inputs or invalid denominators. The
existing SEC-compatible network adapter remains disabled until terms acceptance
and an operator identity are explicitly configured.

## Point-in-time macro catalogue

Migration 0012 stores authorized macro sources and immutable releases for policy
rates, CPI, employment, GDP, yield curves and liquidity/credit. Observation
period, initial release, optional licensed expectation, prior, revision,
release and ingestion timestamps remain distinct; PIT reads select only the
latest revision actually released and ingested by the query time.
