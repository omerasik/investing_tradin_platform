# Current Status

## Repository state

Audit at 2026-07-30: the application workspace was empty (no existing code, configuration, secrets or tests to preserve). Both authoritative PDFs were text-extracted and visually reviewed: 22 upstream-program pages and 65 platform-specification pages.

## Initial proposal

- MVP universe: liquid US ETFs/equities, major FX pairs, gold, BTC and ETH; historical data and internal paper simulation only.
- Data: generated golden fixtures until a licensed provider passes the provider matrix.
- Broker: none; later only a sandbox/paper adapter behind the paper OMS contract.
- Architecture: Python modular monolith with auditable domain events and separate research, risk and paper-execution boundaries.

## Critical risks

License compatibility, unreviewed third-party supply chains, data licensing/point-in-time correctness, backtest leakage, and any route that could bypass deterministic risk controls. Live trading is disabled by design.

## Implemented foundation

The project now contains dependency-free canonical contracts for instruments, market snapshots, signals, immutable order intents, portfolio state and risk decisions. `PlatformConfig` rejects any live-trading configuration. The independent risk engine rejects stale/low-quality data, invalid or expired signals, bad spreads, order/position limits, unresolved reconciliation and kill switches. Eight unit tests and bytecode compilation passed in Cycle 1.

Cycle 2 added duplicate-intent rejection via an idempotency ledger. Nine unit tests, compilation and a source-only live-trading safety scan now pass.

Cycle 3 added a local FastAPI application with liveness/readiness endpoints that report live trading as disabled, plus append-only SQLite audit storage and a CI verification workflow. Thirteen tests, compilation and an OpenAPI contract check pass. The API is local-only until authentication/authorization is implemented.

Cycle 4 closed that local API gap with environment-only bearer authentication, fail-closed endpoints, and a bounded in-memory rate limiter. Sixteen tests, compilation, readiness-contract and paper-only source scans pass. Durable sessions, RBAC and distributed rate limiting remain future deployment requirements.

Cycle 5 added a SQLite-backed instrument master whose primary key is a canonical instrument ID, while one symbol can refer to multiple venue-qualified instruments. Nineteen tests, compilation, an instrument smoke test and paper-only scan pass.

Cycle 6 added structured JSON event logging and local counters for health and audit paths; the metrics endpoint requires operator authentication. Twenty-two tests, compilation and the paper-only source scan pass.

Cycle 7 added provider-neutral OHLCV records with event/effective/ingest timestamps, source provenance, revision, data version, quality score and processing status. Invalid prices, volumes, duplicate timestamps and timestamp semantics are rejected. Twenty-six tests and compilation pass; licensed provider adapters remain unimplemented.

Cycle 8 replaced opaque local bar payloads with typed SQLite columns and added an explicit point-in-time `available_as_of` query. A leakage test proves that a future-ingested bar is unavailable at the earlier decision timestamp. Twenty-seven tests and compilation pass.

Cycle 9 completed the executable historical OHLCV adapter boundary with a deterministic fixture provider. The adapter is constrained by instrument, interval and time range, and rejects provenance mismatch. Twenty-nine tests and compilation pass. Live/licensed provider selection remains an external matrix and contract decision.

Cycle 10 started research infrastructure with versioned feature definitions, a transparent moving-average cross baseline, cost-aware vectorized simulation and deterministic performance outputs. Tests prove moving-average prefix invariance and that signals act only on the next period’s return. Thirty-four tests and compilation pass.

Cycle 11 completed the initial research foundation with standardized performance reports and append-only experiment records. Every experiment records strategy, dataset, feature, cost-model and parameter versions; result metrics round-trip through local storage. Thirty-five tests and compilation pass.

Cycle 12 started event-driven paper simulation with explicit state transitions, partial fills, final fills and weighted-average fill pricing. Invalid lifecycle transitions and overfills are rejected. Thirty-eight tests and compilation pass; this is an internal simulator only, with no broker or network execution code.

Cycle 13 added split/dividend treatment and reconciliation comparison. A reconciliation mismatch explicitly produces a portfolio state that blocks risk increases. Forty tests and compilation pass. Funding, borrow, margin and broker constraints remain the next Phase 4 scope.

Cycle 14 added deterministic funding, short-borrow, and margin calculations for the paper simulator. Funding and borrow change cash explicitly; invalid inputs are rejected, and margin checks enforce both gross-notional and leverage caps. Forty-one tests and compilation pass.

Cycle 15 added venue constraints for paper orders. Orders can be rejected for closed sessions, invalid tick/lot alignment or venue notional limits. Forty-two tests and compilation pass.

Cycle 16 completed the local event-driven simulation phase with a cross-engine reconciliation report. Material final-equity, fill-quantity, and position differences are explicit and block reconciliation. Forty-four tests and compilation pass.

Cycle 17 started the initial strategy library with versioned breakout, momentum, and mean-reversion baselines in addition to the prior trend baseline. Their deterministic signal behavior is tested. Forty-five tests and compilation pass; no strategy is promoted or used for broker execution.

Cycle 18 added chronological train-validation-test split generation and bounded inverse-risk strategy allocation. Tests confirm that splits preserve temporal order and that concentrated allocations are rejected rather than silently capped. Forty-seven tests and compilation pass.

Cycle 19 started portfolio-risk controls with gross/net exposure, concentration checks and generalized scenario shocks with per-instrument loss contributions. Forty-nine tests and compilation pass. These calculations remain independent from strategies and paper execution.

Cycle 20 added policy-level portfolio approvals/rejections. Breaches of gross exposure, single-position concentration and scenario-loss constraints now produce explicit deterministic reasons. Fifty tests and compilation pass.

Cycle 21 completed the initial risk-report contract through a protected API endpoint. It evaluates operator-supplied paper scenarios and returns risk decisions/reasons only; it cannot create an order. Fifty-one tests and compilation pass.

Cycle 22 started the operator dashboard as a separate responsive Next.js TypeScript application. It provides a paper-only command-center, risk, research and system-health information architecture. Its direct Next production build passes. The package manager blocked an optional `sharp` native build script, which was not approved; the app compiles without it.

Browser-level dashboard inspection was attempted through both available browser surfaces. Both timed out before local navigation completed, although the local server returned HTTP 200 and the direct production build passed. Browser UI verification remains unproven and is recorded in `KNOWN_LIMITATIONS.md`.

Cycle 23 added a server-side Next.js risk proxy, retaining the operator token outside browser code. Cycle 24 added an HTTP smoke test that confirms the rendered dashboard safety text and verifies that the risk proxy fails closed with HTTP 503 when server credentials are absent. The stale development server was replaced with a production server after a bounded request timed out. The dashboard smoke test and all 51 Python tests pass.

Cycle 25 expanded the responsive dashboard into linked market, instrument, strategy-laboratory, risk, audit and system-health workspaces. Python compilation, all 51 unit tests, a Next production build, and the expanded dashboard HTTP smoke test pass. The proxy continues to fail closed without configuration; a separate local, authenticated paper-risk API verification produced explicit gross-exposure, concentration, and scenario-loss blocks. Browser-level navigation remains unproven because the available browser surfaces timed out.

Cycle 26 implemented provider-neutral news-event metadata rather than fetching or storing external content. The append-only local store enforces provider-item uniqueness and point-in-time availability. Every assessment carries source reliability, extraction confidence, derived uncertainty, and a license gate; unlicensed sources cannot become research-eligible. Python compilation and all 55 tests pass. Actual provider integration remains blocked on source licensing approval and credentials.

Cycle 27 added transparent probabilistic regime estimates and regime-weighted ensemble allocation. Estimates use only the historical prefix supplied to them, preserve uncertainty, and must sum to one. Walk-forward checks prove future appends do not change earlier estimates; allocations with excessive concentration are rejected. Python compilation and all 59 tests pass.

Cycle 28 added the long-horizon investment workspace as append-only thesis and recommendation evidence objects. Recommendations require bounded target weights and supporting evidence; rebalance plans and scenarios report proposed allocation changes or losses only and cannot create orders. Python compilation and all 63 tests pass.

Cycle 29 completed the reference-only upstream audit. All 16 pinned clones have license, architecture, feature and adoption records; the partial `machine-learning-for-trading` checkout was license-checked from its pinned Git object. Static inventory found substantial manifest, automation and execution-sensitive surfaces, so no upstream code is approved for runtime use. No third-party code was executed.

Cycle 30 added a fixture-only paper-broker adapter contract. It accepts only risk-approved paper orders, has deterministic submit/fill/cancel behavior, protects against duplicate or unknown intents, and reports fill rate and absolute benchmark slippage. Python compilation and all 67 tests pass. It has no network or credential support; a real sandbox integration remains externally blocked.

Cycle 31 added local shadow-mode comparison and failure-drill evidence. It identifies quantity, fill-price, status and identity divergence, and records whether a protection drill actually produced its expected result. Python compilation and all 70 tests pass. No live data or sandbox broker is connected, so operational shadow mode remains blocked.

Cycle 32 completed the initial dashboard workspace coverage: command, market, instrument, strategy, backtest, risk, investment, audit and health. Python compilation, all 70 unit tests, a fresh Next production build and an HTTP smoke test pass. The dashboard risk proxy remains server-side and fails closed without credentials. Browser automation could not navigate the local application, which is recorded as a tooling limitation rather than a passing UI test.

Cycle 33 performed the consolidated verification: Python bytecode compilation and all 70 unit tests, frontend production build, a fresh dashboard HTTP smoke test, and a source safety scan. The scan found no network or named live-broker client use and no live-enable assignment in production source or tests. Documentation and the master roadmap now distinguish verified local work from blocked external integrations.
