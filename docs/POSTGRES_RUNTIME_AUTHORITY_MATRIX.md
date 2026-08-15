# PostgreSQL Runtime Authority Matrix

This inventory is the enforced paper-runtime persistence boundary as of
2026-08-15. `persistence_target=postgres` is not allowed to pass through the
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
| Instrument risk profile/session calendar | normalized base schema is insufficient | MUST_MIGRATE | BLOCKS_SUBMISSION |
| Validated signal/lifecycle | normalized base schema is insufficient | MUST_MIGRATE | BLOCKS_SUBMISSION |
| Model validation/approval/drift/prediction | normalized base schema is insufficient | MUST_MIGRATE | BLOCKS_SUBMISSION |
| Halt/event-risk/slippage evidence | `PostgresExecutionEvidenceStore` | PostgreSQL authority | COMPOSED_PENDING_CI |
| Point-in-time quotes | `PostgresQuoteStore` | PostgreSQL authority | COMPOSED_PENDING_CI |
| Portfolio return history | `PostgresPortfolioReturnStore` | PostgreSQL authority | COMPOSED_PENDING_CI |
| Return provider ingestion health/cadence | none | MUST_MIGRATE | BLOCKS_MANAGED_INGESTION |
| SQLite paper runtime | `build_paper_runtime` | RESEARCH_ONLY | LOCAL_ONLY |
| Other SQLite research/evaluation stores | module-specific local stores | RESEARCH_ONLY or LEGACY_NONCRITICAL | OUTSIDE_PAPER_AUTHORITY |

`build_postgres_paper_core` composes the existing PostgreSQL rows above on one
explicitly selected database connection and never constructs a SQLite store.
It intentionally reports `submission_ready = False`: policy and keyed
assessment authorities are now wired, but the upstream signal/model/market and
portfolio evidence required to produce an approvable assessment is incomplete.
This is a fail-closed intermediate cutover state, not a claim that P0 or the
full paper runtime is complete.

P0 may close only after every `MUST_MIGRATE` row is replaced, the configured
full PostgreSQL composition is exercised end-to-end, and database uncertainty
is proven to reject risk approval.
