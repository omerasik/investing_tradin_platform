# PostgreSQL Runtime Authority Matrix

This inventory is the enforced paper-runtime persistence boundary as of
2026-08-19. `persistence_target=postgres` is not allowed to pass through the
legacy `build_paper_runtime` SQLite constructor.

| Runtime authority | Current PostgreSQL implementation | Classification | Cutover state |
|---|---|---|---|
| Paper order intent/lifecycle/fills | `PostgresPaperOms` | PostgreSQL authority | COMPOSED |
| Broker event de-duplication/cursor | `PostgresPaperOms` + `PostgresBrokerEventStore` | PostgreSQL authority | COMPOSED |
| Reconciled account/positions | `PostgresPaperOms` read through `OmsReconciledAccountStore` | PostgreSQL authority | COMPOSED |
| Kill switches | `PostgresKillSwitchRegistry` | PostgreSQL authority | COMPOSED |
| Daily risk reservation/decision | `PostgresRiskStore` | PostgreSQL authority | COMPOSED |
| Validation package/evidence | `PostgresQuantValidationStore` | PostgreSQL authority | COMPOSED |
| Promotion/activation | `PostgresPromotionLedger` | PostgreSQL authority | COMPOSED |
| Policy documents and portfolio stress policy | `PostgresPolicyRegistry` | PostgreSQL authority | COMPOSED |
| Signed pre-trade assessment and input evidence | `PostgresPreTradeAssessmentStore` | PostgreSQL authority | COMPOSED |
| Instrument/risk profile/session calendar | `PostgresInstrumentStore` | PostgreSQL authority | COMPOSED_AND_CI_VERIFIED |
| Validated signal required by pre-trade | `PostgresSignalStore` | PostgreSQL authority | COMPOSED_AND_CI_VERIFIED |
| Model validation/approval required by pre-trade | `PostgresModelRegistry` | PostgreSQL authority | COMPOSED_AND_CI_VERIFIED |
| Full signal lifecycle and model drift/prediction | partial PostgreSQL surface | MUST_MIGRATE | DOES_NOT_BLOCK_BOUNDED_PAPER_SUBMISSION; BLOCKS_MANAGED_LIFECYCLE |
| Halt/event-risk/slippage evidence | `PostgresExecutionEvidenceStore` | PostgreSQL authority | COMPOSED_AND_CI_VERIFIED |
| Point-in-time quotes | `PostgresQuoteStore` | PostgreSQL authority | COMPOSED_AND_CI_VERIFIED |
| Portfolio return history | `PostgresPortfolioReturnStore` | PostgreSQL authority | COMPOSED_AND_CI_VERIFIED |
| Return provider ingestion health/cadence | none | MUST_MIGRATE | BLOCKS_MANAGED_INGESTION |
| SQLite paper runtime | `build_paper_runtime` | RESEARCH_ONLY | LOCAL_ONLY |
| Other SQLite research/evaluation stores | module-specific local stores | RESEARCH_ONLY or LEGACY_NONCRITICAL | OUTSIDE_PAPER_AUTHORITY |

`build_postgres_paper_core` composes the PostgreSQL rows above on one explicitly
selected database connection and never constructs a SQLite store. It
intentionally reports `submission_ready = False` because it is an unconfigured
authority bundle, not an order-submission facade.

`build_postgres_paper_runtime` resolves versioned risk and portfolio policies,
an approved model, and every point-in-time pre-trade authority before returning
`ConfiguredPostgresPaperRuntime`, whose submission path is limited to a
non-networked simulated-paper adapter. Hosted PostgreSQL CI has exercised that
configured path end to end, including approval, risk reservation, acknowledged
order, partial and final fill synchronization, reconciliation, kill-switch
rejection, restart reconstruction and durable cursor/idempotency evidence.
Database uncertainty and missing/stale authorities fail closed.

The remaining `MUST_MIGRATE` rows are managed-lifecycle and ingestion-service
gaps. They prevent a claim of complete platform or production readiness, but do
not negate the bounded, CI-verified simulated-paper runtime. Live trading and
network-connected broker adapters remain disabled.
