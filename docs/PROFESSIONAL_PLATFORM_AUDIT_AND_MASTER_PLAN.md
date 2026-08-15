# Professional Platform Audit and Master Plan

**Audit date:** 2026-08-15

**Repository:** omerasik/investing_tradin_platform

**Audited commit:** d7c1bc39dd429b2d3995ecb6a16fbc460154833b on main, equal to origin/main
**Decision:** the repository is a promising, safety-conscious local prototype and component test bed. It is not yet a professional research, paper-trading, investment-management, or production platform. P0 is **PARTIAL**, not verified.

This document is the fresh audit baseline and proposed execution plan. [MASTER_ROADMAP.md](MASTER_ROADMAP.md) remains the canonical requirement ledger; it must be reconciled with this evidence in the first documentation update after approved engineering work. No feature, provider, broker, live-trading path, credential, or order was activated during this audit.

---

## Status and evidence rules

The status of a subsystem is the status of its weakest material end-to-end link, not its best-tested class.

| Status | Meaning |
|---|---|
| NOT_STARTED | No meaningful implementation. |
| PROTOTYPE | Interface, contract, fixture, deterministic mock, or exploratory local implementation only. |
| PARTIAL | Material implementation exists, but the end-to-end path or an essential invariant is missing. |
| IMPLEMENTED | The bounded implementation exists, but execution evidence is absent or insufficient. |
| TESTED | Unit or synthetic integration tests execute the bounded behavior. |
| VERIFIED_LOCAL | The real local integration was executed successfully in the audited environment. |
| VERIFIED_CI | The relevant integration was executed successfully in the current commit's CI. |
| EXTERNAL_BLOCKED | Engineering and fixture verification are complete; only a named external dependency remains. |
| PRODUCTION_READY | Operational, security, recovery, compliance, and real-world acceptance gates have all passed. |

Evidence is separately labelled as interface, contract, fixture, deterministic mock, unit test, synthetic integration, CI integration, provider-backed, paper-operational, or production-operational. “TESTED” never implies market validity or alpha.

## A. Executive summary

### What exists

The repository is a Python modular monolith with explicit domain objects, a FastAPI surface, a Next.js dashboard, Alembic migrations, deterministic research fixtures, vectorized and event-driven backtest components, risk and pre-trade rules, a paper OMS, reconciliation concepts, investment-thesis prototypes, structured AI-research contracts, and recent PostgreSQL adapters. The strongest recent evidence is transactional PostgreSQL CI coverage for selected OMS/risk and quant-validation/promotion paths.

Major strengths are:

- a paper-only safety gate and no real-order submission path;
- clear attention to point-in-time timestamps, reproducibility, audit events, idempotency, kill switches, and promotion evidence;
- 265 passing tests in the current CI, including six PostgreSQL integration tests;
- successful TypeScript and Next.js production builds;
- a useful domain vocabulary spanning active trading and long-term investing;
- an appropriately conservative specification that treats LLM output as evidence, not authority.

### What does not exist

There is no provider-backed end-to-end research environment, authoritative PostgreSQL paper runtime, licensed real-time feed, real broker sandbox integration, professional point-in-time fundamental/macro/news store, social narrative system, mature feature platform, validated strategy ensemble, probabilistic regime engine, operational deployment, production authentication, full observability, or exercised PostgreSQL disaster recovery. The dashboard is mainly a scaffold with a mixture of static, fixture-backed, and read-only API-backed views.

No existing strategy has demonstrated alpha. Backtests and validation evidence are synthetic/local. The four simple long-only baselines are useful controls, not a professional research program.

### Top blockers

1. build_paper_runtime still constructs SQLite authorities even when paper configuration requires PostgreSQL.
2. Validation-package recovery now returns a stored hash without reconstructing and verifying the same canonical content.
3. validation_package_artifacts membership is mutable while the package is described as immutable.
4. No full PostgreSQL composition, mapped migration, interruption, backup/restore, and restart gate exists.
5. No authorized real historical data vertical slice exists.
6. Instrument/calendar/provider/broker mapping is incomplete for a controlled initial universe.
7. Repository-wide quality gates are not enforced: full Ruff reports 1,254 findings and mypy reports 120 errors.
8. The frontend production dependency audit reports 34 known vulnerabilities, including 16 high severity.
9. The public GitHub repository and unprotected main branch are inappropriate defaults for proprietary research.
10. Authentication, RBAC, secrets isolation, observability, DR, and deployment controls are not operational.

## Audit method and verified evidence

The audit inspected Git state, workflows, source, tests, migrations, scripts, frontend, configuration, all relevant documentation, and the authoritative platform and upstream specifications. It compared historical roadmap claims with code composition and executable evidence.

| Check | Audited result | Interpretation |
|---|---|---|
| Git | clean main at d7c1bc3, equal to origin/main | Current source baseline verified. |
| GitHub Actions | [run 31816569978](https://github.com/omerasik/investing_tradin_platform/actions/runs/31816569978) succeeded at d7c1bc3 | Current CI is green. |
| Previous CI | [run 31816473432](https://github.com/omerasik/investing_tradin_platform/actions/runs/31816473432) failed one validation restart test at f289faa | The latest commit addressed recovery, but not the deeper integrity design. |
| Python CI | 265 passed, 0 skipped with PostgreSQL 16 service | Current scoped CI evidence. |
| Python local | 265 passed, 6 PostgreSQL tests skipped because no safe test DSN was configured | Local suite is green but does not locally verify PostgreSQL. |
| PostgreSQL CI | six integration tests | Selected migrations, transactions, restart, reservation, OMS/risk, and quant package/promotion behavior only. |
| Python compile | passed | Syntax/import compilation check passed. |
| Ruff, repository-wide | 1,254 findings; 1,227 auto-fixable | CI's scoped Ruff pass is not repository-wide quality. |
| mypy, repository-wide package | 120 errors in 17 files | CI's scoped type pass is not repository-wide type safety. |
| Bandit, src | two medium B608 dynamic-SQL findings and three low B311 findings; no high | Bounded identifiers and simulation RNG may explain findings, but review/suppression evidence is required. |
| Python dependency audit | current CI frozen-requirements audit passed; local editable-project audit could not resolve the package from PyPI | Retain CI evidence; fix the local audit workflow. |
| Frontend TypeScript | passed locally and in CI | Compile-time frontend evidence. |
| Next production build | passed locally and in CI on Next 16.1.6 | Build evidence, not browser/product behavior. |
| Browser test | only an HTTP dashboard smoke check in CI | No Playwright/Cypress user-flow suite. |
| Warnings | Starlette TestClient/httpx deprecation; Node action/punycode deprecations; Bandit comment-parsing notices | Non-blocking today, but must be removed or explicitly governed before release. |
| pnpm production audit | 34 vulnerabilities: 16 high, 15 moderate, 3 low, 0 critical | Release/security blocker. |
| SBOM | generated transiently in CI | Not retained, signed, attested, or governed. |
| Secrets filename scan | no tracked secret-like filenames; no confirmed committed credential found | Necessary but insufficient secret assurance. |
| Repository governance | public repository; main branch not protected | Material IP, supply-chain, and change-control risk. |

Local PostgreSQL service existence was not treated as permission to mutate an unknown database. Current-commit CI is the authoritative PostgreSQL execution evidence.

## B. Verified current-state matrix

### Platform foundation

| Capability | Status | Actual evidence and gap |
|---|---|---|
| Configuration and environment separation | PARTIAL | Typed configuration and paper/live gates exist; composition does not consistently honor persistence_target. |
| Domain boundaries | TESTED | Strong contracts and domain modules, but shared-process coupling remains. |
| Persistence and PostgreSQL | PARTIAL | Schema and selected adapters are VERIFIED_CI; the paper composition root remains SQLite. |
| Migrations | VERIFIED_CI | Alembic runs in CI; upgrade, downgrade, large-data, and recovery rehearsals are incomplete. |
| Analytical storage | PROTOTYPE | SQLite/local structures only; no object/Parquet data plane. |
| Object storage | NOT_STARTED | Required for immutable raw data, artifacts, reports, and backups. |
| Cache | NOT_STARTED | Not yet needed; add only after measured shared-cache/rate-limit demand. |
| Queue/event bus | NOT_STARTED | In-process flows only; no durable multi-consumer delivery. |
| Workflow orchestration | PROTOTYPE | Scripts and synchronous jobs; no durable scheduled DAG with recovery. |
| API | TESTED | FastAPI endpoints and unit tests; no production auth, rate limits, version policy, or operational deployment. |
| Frontend | VERIFIED_CI | TypeScript/build/HTTP smoke pass; product coverage remains PARTIAL. |
| Authentication | PROTOTYPE | Single bearer-token pattern; no user/session lifecycle. |
| Authorization | NOT_STARTED | No RBAC, least privilege, or separation of research/risk/execution authority. |
| Observability and audit | PARTIAL | Metrics, health, alert, and audit concepts exist; no centralized logs/traces/SLOs/alert routing. |
| Backup and restore | PARTIAL | SQLite backup prototype/tests; PostgreSQL and object-store restore drills absent. |
| Deployment and CI/CD | PARTIAL | Verify workflow exists; no deployment pipeline, environment promotion, rollback, or protected approvals. |
| Infrastructure as code | NOT_STARTED | No reproducible cloud/network/database/secret infrastructure. |

### Instrument master

| Area | Status | Evidence and gap |
|---|---|---|
| Canonical instruments, asset class, currency, exchange, identifiers | TESTED | Local schemas and fixtures exist; no provider-backed authoritative registry. |
| Ticker changes, delistings, corporate-action relationships | PROTOTYPE | Contracts/fixtures, not an operational lifecycle. |
| Tick size, lot size, sessions | PARTIAL | Basic metadata only; temporal rule versions and broker enforcement incomplete. |
| Holidays, half days, overnight sessions, 24/7 markets | PROTOTYPE | Calendar concepts exist; no complete exchange-calendar service. |
| Futures contracts and continuous futures | NOT_STARTED | No contract chain, roll policy, adjustment, or expiry operation. |
| FX and crypto conventions | PROTOTYPE | Asset concepts only; pairs, pip/lot, funding, venue, wallet/custody conventions incomplete. |
| Provider and broker mappings | PARTIAL | Mapping structures exist; no live authoritative mapping workflow or conflict resolution. |

### Market data

| Area | Status | Evidence and gap |
|---|---|---|
| Historical OHLCV | TESTED | Fixture/local bar stores and a basic Stooq adapter; no contracted, authorized, reproducible production dataset. |
| Quotes, trades, bid/ask, spread | PROTOTYPE | Quote contracts and local stores; no real stream. |
| Order book and depth | NOT_STARTED | No L2/L3 schema, normalization, completeness, or replay. |
| Corporate actions, dividends, splits, symbol changes, delistings | PARTIAL | Models and tests exist; provider-backed PIT processing is absent. |
| Futures rolls | NOT_STARTED | No data or calculation pipeline. |
| Crypto funding, open interest, liquidations, basis | NOT_STARTED | No authorized source or normalized history. |
| Options data | NOT_STARTED | No chain, Greeks, surfaces, or corporate-action handling. |
| Breadth, ETF/sector, cross-asset | PROTOTYPE | Narrow derived fixtures; no broad universe pipeline. |
| Realtime and streaming | NOT_STARTED | No streaming provider, durable ingestion, replay, backpressure, or clock monitoring. |
| PIT correctness and revisions | PARTIAL | Timestamp-aware contracts exist; real source/revision lineage is not demonstrated. |
| Source health and fallback | PROTOTYPE | Health concepts exist; no dual-provider operational failover. |
| Licensing and storage rights | NOT_STARTED | No approved data-rights registry or contract controls. |
| Storage | PROTOTYPE | SQLite/local database; no immutable raw/object/Parquet layers. |

### Fundamentals and macro

| Capability | Status | Evidence and gap |
|---|---|---|
| SEC filing/company facts ingestion | PARTIAL | Adapter code and fixtures; no authorized scheduled ingestion, raw filing archive, taxonomy normalization, or operating evidence. |
| Income statement, balance sheet, cash flow | PROTOTYPE | Local normalized concepts; as-reported-to-standardized reconciliation is incomplete. |
| Filing timestamps, revisions, PIT history | PROTOTYPE | Temporal fields exist; provider-backed amendment/revision ledger absent. |
| Earnings, estimates, guidance | NOT_STARTED | Requires licensed source and PIT snapshots. |
| Insider transactions and institutional ownership | NOT_STARTED | No ingestion or ownership-event model. |
| Capital allocation, buybacks, dividends, debt, dilution | PROTOTYPE | Investment metrics/fixtures only. |
| Valuation and quality metrics | TESTED | Deterministic local calculations, not validated across real filings and sectors. |
| Rates, yield curves, CPI/PCE, employment, GDP | PROTOTYPE | Macro contracts/fixtures; FRED/ALFRED/ECB ingestion not operational. |
| PMI, liquidity, central banks, fiscal data, credit spreads | NOT_STARTED | Source and revision policies required. |
| Dollar/liquidity, commodity macro, surprise indices | NOT_STARTED | Requires licensed and release-calendar inputs. |
| Initial release versus revision and economic calendar | PROTOTYPE | Target semantics documented, not provider-backed. |

### News, events, social, and narrative

| Capability | Status | Evidence and gap |
|---|---|---|
| News ingestion, deduplication, ticker mapping | PROTOTYPE | Local structures and fixtures; no licensed feed. |
| Clustering, entity linking, classification | PROTOTYPE | Deterministic/LLM contracts only; no measured real-data accuracy. |
| Quality, novelty, urgency, uncertainty, follow-up | PROTOTYPE | Schema-level ideas; no calibration or source policy. |
| Rumor handling and correction/retraction | NOT_STARTED | Must preserve source state and withdraw downstream confidence. |
| Earnings, M&A, regulation, litigation, management, geopolitics | PROTOTYPE | Event taxonomy, not a complete operational feed. |
| Impact horizons | NOT_STARTED | No labeled outcome dataset or calibrated model. |
| Permitted social-source ingestion | NOT_STARTED | Legal, contractual, privacy, retention, and deletion review required first. |
| Mentions, acceleration, unique authors | NOT_STARTED | No lawful source or identity-safe aggregation. |
| Bot/spam/coordination/pump risk | NOT_STARTED | No model, labels, adversarial tests, or reviewer workflow. |
| Sentiment/change/disagreement/narrative clustering | NOT_STARTED | LLM opinion must not substitute for measured source data. |
| Influencer concentration, persistence, crowding, divergence | NOT_STARTED | No source-separated retail/professional/news pipeline. |

### Feature platform

| Family | Status | Evidence and gap |
|---|---|---|
| Momentum, trend, simple mean reversion | TESTED | Narrow deterministic features and strategies; incomplete cross-sectional and PIT validation. |
| Volatility | PARTIAL | Basic estimates; no surfaces, intraday realized measures, or forecast calibration. |
| Liquidity and microstructure | PROTOTYPE | Spread/volume notions; no tick/book inputs or execution-quality labels. |
| Breadth and factors | PROTOTYPE | No production universe, survivorship control, or neutralization pipeline. |
| Fundamentals and macro | PROTOTYPE | Fixture-derived; no provider-backed versioned features. |
| News and sentiment | PROTOTYPE | Contract-level only. |
| Crypto derivatives | NOT_STARTED | Funding/basis/OI/liquidation inputs absent. |
| Cross-asset and regime | PROTOTYPE | Simple signals/checklists, not a probabilistic validated engine. |
| Portfolio and execution-quality features | PROTOTYPE | Local risk/return history only; no operational fills/slippage history. |
| Feature registry, lineage, freshness | PARTIAL | Version concepts exist; no shared materialization, online/offline parity, or quality SLA. |

### Product subsystems

| Subsystem | Status | Evidence boundary |
|---|---|---|
| Active-trading research | TESTED | Synthetic/local engines and baselines only. |
| Backtesting and validation | TESTED | Useful deterministic components; realism and statistics remain incomplete. |
| Risk | PARTIAL | Unit-tested rules and selected PostgreSQL adapters; not an authoritative end-to-end paper gate. |
| OMS/reconciliation | PARTIAL | SQLite runtime plus PostgreSQL adapter CI tests; no broker. |
| Long-term investment | TESTED | Local thesis, valuation, cadence, and portfolio fixtures; no provider-backed operating workflow. |
| ML | PROTOTYPE | Registry/evidence concepts; no governed trained model pipeline. |
| AI research | PROTOTYPE | Structured agent contracts; deterministic/mock evidence, no measured factual pipeline. |
| Security | PARTIAL | Safety intent and scans exist; public/unprotected repo, dependency debt, and weak auth remain. |
| SRE/DR | PROTOTYPE | Health/alert/failure-drill concepts; no production service or exercised PostgreSQL DR. |
| Broker integration | NOT_STARTED | No adapter connected; correctly deferred. |
| Live execution | NOT_STARTED | Must remain disabled. |
| Production readiness | NOT_STARTED | No subsystem qualifies as production-ready. |

## C. Current architecture

~~~mermaid
flowchart LR
  U["User / CI"] --> W["Next.js dashboard"]
  W --> A["FastAPI modular monolith"]
  A --> D["Domain and application services"]
  D --> R["Research, backtest, risk, investment, AI prototypes"]
  D --> S["SQLite stores used by build_paper_runtime"]
  D --> P["Selected PostgreSQL adapters"]
  P --> PG["PostgreSQL 16 schema / Alembic"]
  R --> F["Synthetic fixtures and local files"]
  CI["GitHub Actions"] --> PG
  CI --> A
  CI --> W
  X["External data/broker"] -. "not operational" .-> D
~~~

The decisive split-brain is at the composition root: paper configuration demands PostgreSQL, while build_paper_runtime explicitly constructs SQLitePaperOms, SQLiteBrokerEventStore, SQLitePolicyRegistry, SQLitePreTradeAssessmentStore, SQLiteInstrumentStore, SQLiteSignalStore, SQLiteModelRegistry, SQLiteExecutionEvidenceStore, SQLiteQuoteStore, SQLitePortfolioReturnStore, and SQLitePromotionLedger. Selected PostgreSQL implementations therefore do not make PostgreSQL authoritative.

| P0 composition path | Verified current reality | P0 verdict |
|---|---|---|
| Persistence composition | persistence_target exists, but build_paper_runtime does not select create_database/PostgreSQL for its authorities | PARTIAL |
| Risk composition | PostgreSQL kill-switch/risk stores exist; the main runtime still reads multiple SQLite authorities | PARTIAL |
| Assessment composition | SQLite pre-trade assessment/policy/dependency stores are wired | PARTIAL |
| Paper OMS composition | PostgreSQL OMS adapter is VERIFIED_CI in bounded tests; main runtime wires SQLitePaperOms | PARTIAL |
| Broker-sync composition | Internal fixture broker and SQLite broker-event cursor/store; no external broker protocol | PROTOTYPE |
| Reconciliation composition | Reconciliation contracts and selected PostgreSQL restart test exist; no full composition/restore convergence | PARTIAL |
| Quant-validation composition | PostgreSQL validation package adapter has write/restart CI evidence; canonical recovery integrity is not verified | PARTIAL |
| Strategy-promotion composition | PostgreSQL promotion ledger adapter has bounded CI evidence; runtime still wires SQLitePromotionLedger | PARTIAL |

### SQLite classification

| Classification | Stores / use | Required disposition |
|---|---|---|
| LOCAL_TEST_ONLY | SQLiteDatabase; SQLiteBackupArtifactStore/SQLiteBackupService; isolated dashboard/test fixtures | Keep only under explicit local/test profiles. Never silently select in paper or live. |
| RESEARCH_ONLY | experiment, golden run, validation evidence, feature, bar, fundamental, macro, news, corporate action, agent research, investment provider health, investment, strategy registry | May remain for deterministic local research while real research moves to PostgreSQL/object/Parquet planes. Label outputs non-operational. |
| MUST_MIGRATE | paper OMS, broker events, policy registry, pre-trade assessments/decisions, instrument/signal/model registries, execution evidence, quotes, return history, promotion ledger, reconciled accounts/positions, kill switches, idempotency, daily notional/risk decisions, audit, operational alerts/failure drills | Implement PostgreSQL protocols/adapters and wire a single transactionally coherent paper composition before P0 exit. |
| LEGACY_ALLOWED | Read-only import of old local research databases and reproducible test snapshots | Provide explicit import/version support; no dual write and no operational authority. |
| DEPRECATED | Standalone SQLite safety ledgers and paper OMS after PostgreSQL cutover | Remove from paper composition after mapped migration and rollback window. |

### Validation-package integrity finding

The domain package hash is created from a deterministic logical payload containing strategy version, dataset version, feature versions, cost-model version, evidence IDs, limitations, and promotion status. PostgreSQL normalizes parts into foreign keys and membership rows. Its reconstruction does not reliably reproduce the original logical labels; the latest recovery fix removed read-time content-hash comparison. The returned package can therefore carry a stored hash that no longer proves its reconstructed content. In addition, validation_package_artifacts is not covered by the immutable-table trigger set, so evidence membership can be changed independently.

Correct design:

1. Store an immutable, schema-versioned canonical manifest containing exact semantic labels and stable IDs, timestamps, limitations, validation metadata, and ordered evidence membership.
2. Use a specified deterministic serialization, preferably RFC 8785 JSON canonicalization or a versioned equivalent, and hash the exact stored bytes.
3. Store normalized foreign keys alongside the manifest for relational queries; never recreate the signed/hashed manifest from mutable labels.
4. Make the manifest, package row, and membership rows immutable with database triggers and constraints.
5. Verify manifest hash, membership equality, and every referenced artifact hash on write, read, restart, promotion, export, and restore. Fail closed.
6. Add keyed signature or WORM/object-lock evidence only when the threat model requires protection against privileged database tampering; an ordinary hash detects accidental corruption but not an attacker who can rewrite both content and hash.
7. Backfill existing packages with a versioned migration and explicit unverifiable/legacy state; never silently bless them.
8. Test manifest tampering, membership insert/update/delete, label mismatch, artifact corruption, transaction interruption, restart, backup, and restore.

P0 remains **PARTIAL** until this and the authoritative runtime/DR gates pass.

## D. Target architecture

~~~mermaid
flowchart TB
  subgraph Sources["Authorized external sources"]
    MD["Market data"]
    FD["Filings / fundamentals / macro"]
    NE["Licensed news / permitted narrative"]
    BR["Broker sandbox, later"]
  end
  subgraph Ingestion["Ingestion and control"]
    AD["Vendor-neutral adapters"]
    CAL["Instrument master / calendars / mappings"]
    DQ["Data health, lineage, licensing policy"]
  end
  subgraph Data["Logical data planes"]
    RAW["Immutable raw objects"]
    PAR["Versioned Parquet datasets / features"]
    PG["PostgreSQL transactional state"]
    ART["Immutable model, validation, report artifacts"]
  end
  subgraph Intelligence["Research and investment intelligence"]
    Q["Feature / strategy / validation"]
    INV["Fundamental / macro / thesis / valuation"]
    AI["Untrusted structured AI evidence"]
    REG["Probabilistic regime"]
    PORT["Portfolio / ensemble allocation"]
  end
  subgraph Safety["Operational safety"]
    RISK["Pre-trade and portfolio risk"]
    OMS["Order intent / OMS / reconciliation"]
    OBS["Audit / logs / metrics / traces / SLOs"]
  end
  subgraph Product["Product"]
    API["Versioned API with RBAC"]
    UI["Role-aware workspaces"]
    REP["Governed reports"]
  end
  Sources --> AD --> DQ
  CAL --> AD
  DQ --> RAW --> PAR
  DQ --> PG
  PAR --> Q
  PAR --> INV
  Q --> REG --> PORT
  INV --> PORT
  AI --> Q
  AI --> INV
  PORT --> RISK --> OMS
  OMS <--> BR
  PG --> RISK
  PG --> OMS
  Q --> ART
  INV --> ART
  Safety --> OBS
  Intelligence --> API
  Safety --> API --> UI
  API --> REP
~~~

Logical authority must remain explicit:

- PostgreSQL: transactional control state, policies, approvals, OMS, reconciled accounts, risk decisions, audit indices.
- Immutable object storage: original provider payloads, filings, news documents where licensed, broker reports, reports, backups.
- Parquet: versioned historical market data, PIT fundamental/macro snapshots, features, backtest datasets.
- Artifact store: canonical validation manifests, models, prompts, reports, signatures, provenance.
- Redis: only when multiple processes require low-latency cache, rate limits, leases, or ephemeral coordination.
- Durable queue/event bus: only when external streaming or multiple independent consumers make in-process delivery unsafe.
- Columnar service such as ClickHouse: only after Parquet/PostgreSQL benchmarks show repeated tick/book queries cannot meet the agreed SLO.

## E. Gap analysis and architectural decisions

### Active-trading chain

| Link | Current | Required next state |
|---|---|---|
| Data | synthetic/local fixtures | licensed historical vertical slice with immutable raw capture and PIT lineage |
| Features | narrow local families | versioned materializations, freshness and leakage tests |
| Strategy | four simple long-only baselines | hypothesis-led family program with falsification and capacity assumptions |
| Validation | useful deterministic approximations | genuine nested/purged validation, trial registry, robust multiple-testing controls |
| Signal | local contract | expiring, quality-gated, versioned signal with decision lineage |
| Regime | checklist/prototype | calibrated probabilistic states with uncertainty and OOS transition tests |
| Portfolio | limited local construction | strategy-aware risk budgets and exposure/capacity constraints |
| Risk | tested components, split authority | atomic PostgreSQL gate across data, policy, portfolio, broker, and reconciliation state |
| Order intent / OMS | local SQLite runtime plus PG adapter | immutable intent and authoritative PG state machine |
| Broker | absent | vendor-neutral adapter, then sandbox after engineering gates |
| Fill / reconciliation | fixtures and selected tests | streaming/poll replay, unknown-state protocol, restart and divergence drills |
| Attribution | prototype | fill-, signal-, strategy-, factor-, cost-, currency-, and regime-level attribution |

### Backtest and research validity

The vector and event engines plus golden reconciliation are valuable baselines, but both are internal implementations driven mainly by synthetic data. Execution realism lacks robust queue position, market impact, capacity, venue calendars, borrow, margin, taxes, FX conversion, futures rolls, funding, delistings, and asset-specific corporate actions. A cross-engine equality test is not an independent market-validity test.

Existing walk-forward, purge/embargo, bootstrap, BH/FDR, Probabilistic Sharpe, “Deflated Sharpe,” and PBO-style calculations must be reviewed formula by formula. Several are deliberately simplified checklist approximations. They cannot be cited as institutional implementations until they have references, numerical oracle tests, trial-registry integration, researcher-degree-of-freedom accounting, and real OOS evidence. Required validation includes untouched holdout; expanding and rolling walk-forward; purged and embargoed nested CV where applicable; block/bootstrap and Monte Carlo path tests; parameter surfaces; regime/crisis/cross-market/cross-instrument tests; missing/delayed data; cost/capacity stress; and reality-check-style controls.

### Data health score

Each dataset, instrument, provider, and timestamp receives independently visible dimensions rather than a single opaque average:

| Dimension | Example checks |
|---|---|
| Completeness | missing bars, incomplete books, funding gaps, expected session coverage |
| Uniqueness | duplicate bars, trades, news, broker events |
| Validity | impossible price/volume, crossed quote, invalid sequence, negative fields |
| Timeliness | stale quotes/features/signals, provider delay, clock skew |
| Temporal correctness | timezone/session errors, filing/revision availability, corporate-action effective dates |
| Consistency | provider disagreement, OHLC constraints, book/trade reconciliation |
| Lineage/rights | source, license, raw object hash, dataset/feature version, permitted retention/use |

Policy outcomes:

- WARN: small non-critical discrepancy; preserve score and operator visibility.
- DEGRADE_CONFIDENCE: bounded fallback or disagreement; reduce signal confidence, never increase risk.
- DISABLE_STRATEGY: a required feature/source breaches its freshness or validity contract.
- BLOCK_INSTRUMENT: corrupt mapping, corporate action, halt, crossed market, or missing critical history.
- BLOCK_ASSET_CLASS: calendar/convention/provider-wide failure for that class.
- BLOCK_ALL_TRADING: authoritative clock, database, reconciliation, policy, kill switch, or multi-provider critical failure.

Low-quality or stale data can never produce an operationally valid signal. Fallback is a new provenance event and requires compatibility checks; it is not silent substitution.

## F. Active trading and professional strategy research plan

Strategy count is not a goal. Each candidate must have a written prior, falsification test, unique data dependency, explicit capacity model, and comparison with a naive benchmark.

| Family | Hypothesis and rationale | Universe/data/features/signal | Sizing, exit, horizon, costs | Failure regimes and validation/promotion |
|---|---|---|---|---|
| Trend | Persistent risk premia and slow information diffusion can create continuation. | Liquid futures/ETFs first; total-return prices, calendars, volatility; time-series momentum, moving averages, breakouts, multi-horizon trend. | Volatility-scaled, capped risk; reversal/trailing exits; days to months; spread, impact, roll/funding. | Choppy reversals/crowding; multi-market OOS, crisis and parameter-surface stability; promote only with net OOS breadth and capacity. |
| Mean reversion | Liquidity shocks and temporary overreaction can reverse. | Liquid ETFs/equities; quotes/VWAP/volume; z-score, transparent RSI baseline, residual/VWAP deviation. | Liquidity- and spread-aware, short horizon, time/mean/stop exits; conservative impact and borrow. | Trends, events, halts; delayed-signal and event exclusion tests; require net OOS stability and tail-loss controls. |
| Cross-sectional factors | Persistent compensated risks/behavioral effects may rank assets. | Survivorship-free PIT equity universe; momentum, value, quality, profitability, low-volatility, size/composite. | Sector/factor-neutral where justified, turnover and capacity constrained; weeks/months. | Factor crashes, crowding, stale fundamentals; PIT and delisting tests, neutralization audit, multiple-testing control. |
| Relative value | Economically related instruments may exhibit temporary residual dislocation. | Pairs/baskets/ETF components; cointegration/residuals, hedge-ratio stability, borrow/liquidity. | Market/sector-neutral risk budgets; convergence/time/structural-break exits. | Relationship breaks, borrow squeeze; true OOS pair selection, change-point and capacity stress. |
| Macro | Growth, inflation, liquidity, carry, and policy regimes alter cross-asset returns. | Rates, curves, FX, commodities, liquid proxies; release-vintage macro and carry. | Slow diversified allocations; regime/change exits; financing, roll, FX and rebalance costs. | Policy discontinuity/crowded carry; ALFRED-style vintage tests, cross-country/era robustness. |
| Event driven | Earnings, guidance, actions, index changes, M&A, and economic releases can reprice assets. | PIT event timestamps, expectations, news corrections, liquidity; surprise and abnormal-move features. | Event-specific exposure and horizon; gap/halt/borrow/impact costs. | Leakage, rumor, revision, cancellation; timestamp audits and event-family holdouts mandatory. |
| Sentiment/narrative | Measured surprise, acceleration, disagreement, or divergence may contain short-lived information. | Licensed news and legally permitted aggregated social data; source-separated features. | Confidence-capped and risk-reducing by default; rapid decay exits; high cost assumptions. | Bots, manipulation, selection bias; adversarial labels, source holdouts, correction handling; never LLM-only. |
| Crypto | Funding, basis, OI/liquidation and fragmented venues may create risk premia. | BTC/ETH only initially; venue-specific spot/derivatives history, outages and funding. | Venue/counterparty caps; carry/trend exits; fees, funding, liquidation, transfer and outage costs. | Exchange/stablecoin failure, regime shift; venue holdouts, 24/7 failure drills and severe tail stress. |

Promotion requires: registered hypothesis before testing; immutable dataset/feature/cost/model versions; all trial counts; untouched holdout; economic and statistical evidence; real cost/capacity stress; documented limitations/failure regimes; reproducible report; risk approval; no unresolved data-health breach; and forward paper evidence appropriate to the stage. Rejection evidence is retained.

## G. Long-term investment plan

Investing is a separate authority and workflow:

UNIVERSE → THEME/COMPANY DISCOVERY → FUNDAMENTAL QUALITY → VALUATION → THESIS → PORTFOLIO CONSTRUCTION → RISK → REVIEW → THESIS DRIFT → REBALANCING.

The current local thesis/valuation/cadence prototypes become useful only after PIT filings and price/fundamental data are provider-backed.

Required company-quality model:

- revenue quality, margins, ROIC and context-specific ROE;
- free cash flow, owner earnings, reinvestment runway, capital allocation;
- balance-sheet strength, debt maturity, dilution, buybacks and dividends;
- customer and geographic concentration, competitive position and management execution;
- source-level lineage, fiscal-period normalization, revision history, peer/sector comparability.

Required valuation:

- DCF and reverse DCF, multiples, historical and peer comparison;
- owner earnings, FCF yield, earnings yield;
- bull/base/bear scenarios and sensitivity surfaces;
- ranges with explicit assumptions and uncertainty, never point-estimate false precision.

Each theme—AI, semiconductors, robotics, automation, cloud, data centers, energy, grid, nuclear, cybersecurity, defense, space, biotech, longevity, digital payments, tokenization, critical minerals, water, demographics, and emerging markets—must be represented by evidence-backed exposures, revenue/KPI links, source dates, counter-evidence, and valuation context. An LLM label is not a theme score.

Every thesis contains: statement, evidence and counter-evidence, valuation, catalysts, risks, invalidation conditions, competitive threats, KPIs, review schedule, thesis-drift history, source IDs/timestamps/revisions, analyst/model/prompt versions, and approval state.

Portfolio construction uses strategic/tactical and core/satellite sleeves; sector/country/currency/factor/concentration constraints; valuation-aware sizing; conviction discounted by uncertainty; liquidity and drawdown limits; explicit rebalance bands; and justified currency hedges. Active-trading and investment capital, policies, P&L, and kill switches remain logically separate, with only consolidated risk reporting shared.

## H. Shared data and intelligence plan

### Provider-neutral acquisition

All adapters emit canonical envelopes with source, requested and received timestamps, availability timestamp, revision, instrument mapping version, license policy ID, raw-object hash, schema version, quality result, and idempotency key. Raw payloads are retained only when contractually allowed. Normalization never destroys the original lineage.

Initial provider strategy is a shortlist and proof process, not a purchase decision:

| Layer | Development | Low-cost paper candidate | Professional paper candidate | Scaled direction |
|---|---|---|---|---|
| US equities/ETFs | SEC plus evaluated delayed/EOD source | Massive or Twelve Data after rights/quality proof | Databento or equivalent licensed feed; separate fallback | Enterprise/direct or consolidated feed with contracted SLA |
| Borsa Istanbul | official reference material only | licensed BIST vendor RFP | authorized BIST data-vendor agreement | dual licensed vendor/direct relationship if justified |
| FX/gold | central-bank references and delayed evaluation feed | Twelve Data or equivalent | institutional venue/consolidated vendor RFP | primary plus independent backup |
| Crypto | exchange public metadata for development only | contracted aggregator/exchange pilots | institutional vendor RFP with venue/outage normalization | multi-venue primary plus independent archive |
| Macro | FRED/ALFRED and ECB under their terms | same, with monitored scheduled ingestion | same plus licensed releases/surprise/calendar if needed | redundant official/licensed sources |
| Fundamentals | SEC filings/company facts | SEC raw plus internal normalization | paid PIT estimates/ownership/corporate-actions RFP | dual-source reconciliation for critical fields |
| News | fixtures/public issuer releases | no operational news until rights approved | Benzinga or equivalent licensed structured feed | primary plus correction-aware backup |
| Social | none | permitted-source legal pilot only | contracted privacy/retention-compliant provider | only if demonstrated incremental value |
| Options | deferred | deferred | candidate only after underlying platform gates | specialist licensed provider |

Official-source findings as of the audit date:

- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) provide submissions and XBRL JSON without API keys and publish bulk archives; fair-access and source-specific terms still apply.
- [FRED/ALFRED](https://fred.stlouisfed.org/docs/api/fred/index.html) exposes economic series and real-time vintages; an API key and [terms review](https://fred.stlouisfed.org/docs/api/terms_of_use.html) are required, including third-party series rights.
- [ECB Data Portal API](https://data.ecb.europa.eu/help/api/overview) exposes SDMX data and history options.
- [Massive market-data documentation](https://massive.com/docs/rest/stocks) covers REST, WebSocket, and flat-file access across several asset classes; plan-specific entitlements and storage/commercial terms must be contracted.
- [Twelve Data documentation](https://twelvedata.com/docs/introduction/quickstart) describes multi-asset REST and WebSocket access; quality, entitlements, and retention require proof.
- [Databento documentation](https://databento.com/docs) covers historical/live schemas including tick and book data, with explicit licensing/venue-fee concerns in its [pricing and licensing material](https://databento.com/pricing).
- [Borsa İstanbul market-data products](https://www.borsaistanbul.com/en/data/data-dissemination/market-data-products) and its [licensed vendor directory](https://borsaistanbul.com/en/data/data-dissemination/data-vendors-directory) make BIST a licensed rollout, not a casual scraper. VERDA access is for authorized customers.
- [Benzinga News API](https://docs.benzinga.com/api-reference/news-api/overview) supports structured and correction-aware news; commercial and storage rights require agreement.

Provider proof criteria: universe and field coverage; historical depth; PIT/revision semantics; quote/trade/book completeness; corporate actions; rate limits and backfill; WebSocket recovery; latency distribution; outage history; storage/derived-data/commercial/redistribution rights; geographic access; credentials/security; cost mode; and a tested independent fallback. Architecture cannot depend on proprietary vendor identifiers.

## I. Quant, regime, portfolio, ML, and AI plan

### Regime engine

Start with transparent probabilistic baselines, then compare HMM, clustering, change-point, Bayesian state, tree, and ensemble methods. Outputs are calibrated probabilities and uncertainty for bull/bear/sideways, volatility and liquidity, risk-on/off, growth/inflation quadrants, monetary liquidity, credit/currency/commodity stress, correlation breakdown, and crisis. No hard label may silently raise risk.

Validation covers timestamp leakage, state stability, transition persistence, calibration/Brier/log-loss, economic interpretability, conditional strategy performance, cross-market/era OOS robustness, crisis behavior, and sensitivity to feature/source revisions. Regime eligibility can reduce/disable strategy risk; it cannot override portfolio or kill-switch limits.

### Ensemble and capital allocation

Progression is signal normalization → strategy sleeves → covariance/correlation and marginal-risk estimates → constrained allocation → independent portfolio risk gate. Use volatility targets, risk budgets, gross/net and leverage limits, concentration/factor/asset/sector/country/currency/broker caps, liquidity/capacity, drawdown/decay monitoring, and regime eligibility. Model correlation uncertainty and stress correlation convergence. Prevent hidden leverage by aggregating derivatives, FX, pending orders, and shared factor exposures before authorization.

### ML hierarchy

1. naive baseline;
2. deterministic rule;
3. linear/regularized model;
4. tree/gradient boosting;
5. justified time-series model;
6. ensemble;
7. neural method only with a demonstrated data/benchmark advantage;
8. reinforcement learning only after a defensible simulator and explicit governance—otherwise deferred.

Candidate tasks are expected return/direction, volatility, drawdown, regime, event impact, liquidity/slippage, sentiment, anomaly detection, strategy selection, and sizing support. Every model records dataset/feature/label versions, split method, calibration, Brier score/log-loss/IC where applicable, stability/drift, feature importance, economic value net of costs, and benchmark comparison. A model may reduce risk, disable risk, or lower confidence; it may never autonomously raise a risk limit.

### AI research organization

Potential roles are Technical, Fundamental, Macro, News, Sentiment/Narrative, Bull, Bear, Risk Challenger, Portfolio, Investment, and Final Synthesis. Roles are views over evidence, not autonomous authorities. Every output separates facts, inference, uncertainty, missing data, contradictions, source IDs/timestamps, model version, prompt version, and validation status.

LLMs may extract events, analyze documents, summarize, find contradictions, coordinate research, query evidence, and draft reports. They are untrusted structured inputs and can never be sole authority for orders, sizing, risk overrides, live activation, legal conclusions, or market-data truth. Prompt injection, poisoned sources, unsupported citations, schema violations, and model drift require adversarial tests and human-review paths.

## J. Risk, OMS, and broker target

### Risk

Pre-trade evaluation must atomically check data freshness/quality, quote/spread/slippage/liquidity, position/order/portfolio/buying-power/margin/leverage limits, daily/strategy/portfolio loss, event/halts/restrictions, model and signal approval/expiry, reconciliation and broker state, and currency exposure. Portfolio risk adds gross/net, concentration, beta/factors, sector/country/currency/duration, correlation clusters, VaR/CVaR with limitations, drawdown, stress, liquidity, gap, and capacity.

Kill switches require global, strategy, instrument, asset-class, exchange, broker, provider, model, and account scopes with reason, actor, timestamp, expiry/review, durable state, restart behavior, and independent release approval.

Crisis tests include 2008 credit/equity stress; 2020 rapid crash; rate, FX and commodity shocks; correlation convergence; crypto exchange and stablecoin failure; provider/broker/database/network outage; duplicate, missing and delayed fills; and stale policy/reconciliation states.

### OMS/EMS

Before broker paper, implement immutable order intent, deterministic idempotency, valid order-state transitions, partial fills, cancel/replace, rejects, unknown state, durable broker cursors, replay, restart, reconciliation, cash/positions/P&L/fees, and exact audit lineage. Bracket/OCO behavior follows only after basic state reliability. Market and simple passive/aggressive limits precede TWAP/VWAP/POV/iceberg/adaptive urgency. No advanced EMS work may outrun reconciliation and recovery.

### Broker requirements and shortlist

| Requirement | IBKR evaluation | Saxo evaluation | Gate |
|---|---|---|---|
| Belgium availability | [Belgium is listed](https://www.interactivebrokers.com/en/accounts/open-account-country-list.php) | [Belgian account site exists](https://www.home.saxo/nl-be/accounts/individual) | Legal entity, residency, product permission, and tax handling must be verified. |
| API | Web, TWS and FIX families documented on the [IBKR API page](https://www.interactivebrokers.com/campus/ibkr-api-page/) | OpenAPI REST/WebSocket | Contract test against an internal broker protocol first. |
| Paper/sandbox | Paper account has simulated limitations; Web API paper access is associated with an eligible funded live Pro account | [SIM and LIVE environments](https://www.developer.saxo/openapi/learn/environments); SIM omits some data/reporting behavior | Sandbox gaps must be recorded; never infer live behavior. |
| Assets/currencies/orders | Broad, subject to account and market permissions | Broad, subject to region and permissions | Create an entitlement matrix before selection. |
| Streaming/reconciliation | Candidate, requiring cursor/restart/rate-limit proof | Candidate, requiring WebSocket/token/reporting proof | Poll/stream convergence, unknown state, and restart tests mandatory. |
| Short/margin/options | Permission- and inventory-dependent | Permission- and product-dependent | Deferred beyond initial long-only paper; legal/risk approval required. |
| Decision | Primary technical candidate, not selected | Independent comparator, not selected | Select only after capability, operational, cost, support, tax, and compliance proof. |

Belgian considerations are external professional-review items, not guesses. The [Belgian FPS Finance TOB guidance](https://finance.belgium.be/en/enterprises/other-taxes/financial-institutions-and-insurance-companies/tax-stock-exchange) says Belgian residents using a foreign intermediary may bear declaration/payment responsibility unless the tax is paid; broker-specific handling must be confirmed by a Belgian tax professional. [MiFID II Article 17](https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mifid-ii/article-17-algorithmic-trading) requires relevant investment firms to maintain resilient systems, thresholds, erroneous-order prevention, continuity, testing, monitoring, and records. Whether and how it applies to the eventual operator requires qualified Belgian/EU legal analysis. No broker credential or account is needed to continue fixture-based engineering.

## K. Infrastructure, security, operations, and DR plan

### Infrastructure decision table

| Component | Problem solved | Trigger/scale threshold | Cost and migration path | Timing |
|---|---|---|---|---|
| PostgreSQL | Transactional authority, constraints, locks, audit indices | Required now for shared paper state | Moderate operational cost; finish adapters/composition, migrate, then remove operational SQLite | P0/MVP |
| Object storage | Immutable raw payloads, artifacts, reports, backups | First provider-backed dataset | Low-to-moderate; filesystem-compatible interface locally, versioned cloud bucket later | P1 |
| Parquet | Efficient versioned analytical datasets and portable lineage | First repeatable real historical dataset | Low; write partitioned manifests, query locally with DuckDB/Polars-class tools | P1 |
| ClickHouse or equivalent | Repeated high-volume tick/book analytics | Only after benchmarked Parquet/PostgreSQL workloads miss SLO, typically sustained hundreds of millions of events or multi-user sub-second scans | High operational burden; dual-load from immutable raw/event log, never become sole archive | P3/scale |
| Redis | Shared cache, rate limits, ephemeral leases | Multiple API/workers need low-latency shared ephemeral state | Moderate; add behind interfaces; never authoritative for risk/OMS | P2 only if measured |
| NATS/Redpanda/Kafka class | Durable fan-out, replay, decoupled consumers | External streams plus multiple consumers cannot safely use a DB outbox/worker | Moderate to high; begin with PostgreSQL outbox, promote after throughput/replay evidence | P2/P3 |
| Temporal/Prefect/Dagster class | Durable DAG scheduling, retries, backfill visibility | Many dependent recurring jobs make scripts/DB queue recovery unreliable | Moderate/high; first standardize idempotent jobs and manifests | P2 |
| Kubernetes | Large multi-service orchestration | Only after service count, isolation, autoscaling, and team operations justify it | High; containers and IaC on simpler managed runtime first | Deferred |

### Security findings and target

- Repository visibility is public and main is unprotected. Unless this is intentionally open source, the repository should become private before proprietary alpha, licensed data schemas, operating procedures, or broker configuration enter it. Visibility must not be changed without owner approval.
- A single bearer token is insufficient. Introduce an identity provider, secure session/token lifecycle, MFA for privileged roles, RBAC, separation of research/risk/execution/admin duties, service identities, and approval audit.
- Add CSRF protection where cookie authentication is used; strict CORS, CSP and security headers; input/schema limits; rate limits; SSRF egress allowlists and DNS/IP rebinding defenses; parameterized SQL and reviewed bounded identifier interpolation.
- Put provider and future broker secrets in a managed secret store with environment scoping, short-lived credentials where possible, rotation, access logging, and no exposure to research workers or browsers. Isolate broker credentials in the execution boundary.
- Encrypt PostgreSQL, objects, backups, and transport; classify data; enforce retention/deletion and data-license policy.
- Pin and update vulnerable frontend dependencies. Make full Ruff, mypy, Bandit review, Python and Node SCA, secret scanning, SAST, retained SBOM, artifact provenance, container scanning, and dependency-review gates repository-wide.
- Protect main with required current checks, review, signed/verified commits where appropriate, CODEOWNERS for risk/execution/security/migrations, no force push, and controlled release approvals.

### Observability and SRE

Use correlated structured logs, OpenTelemetry-class traces, RED/USE metrics, immutable business audit events, and environment/build/provider/model/dataset labels. Define SLOs only after baselining; candidate indicators include API availability/latency, provider freshness/completeness, job success/lag, feature and signal freshness, database health, queue/outbox lag, reconciliation convergence, strategy health, model drift, and report delivery.

Alerts must be actionable and routed by severity for provider outage, stale/corrupt data, reconciliation failure, drawdown, unusual slippage, duplicate event, broker divergence, database failure, model drift, clock skew, policy-load failure, and kill-switch activation. Every alert has an owner, runbook, deduplication, escalation, evidence link, and post-incident review. A green health endpoint cannot substitute for dependency and business-health probes.

### Disaster recovery

The existing SQLite backup prototype is not PostgreSQL DR evidence. The target is encrypted PostgreSQL logical backups plus an appropriate physical/WAL strategy, immutable object/artifact/config backups, retention and deletion policy, separate failure domain, checksum/catalog verification, secret recovery, and documented rebuild from IaC. A restore drill must start with a fresh environment, restore all authorities, verify manifest/artifact hashes, reconcile cash/positions/orders/cursors, and test kill switches before reopening paper operation. RPO and RTO remain **unachieved** until measured in repeated drills.

## L. Dashboard, product, and reporting plan

Current frontend status:

| Workspace | Current reality | Target |
|---|---|---|
| Command Center / Market Intelligence / Instrument Workstation | Navigation and mostly static fixture text; static LOCAL/HEALTHY health presentation | Live, timestamped, role-aware health, market, event, position, and incident evidence |
| Strategy Lab / Backtests | API-backed create/launch/read surfaces in a basic form | Experiment lineage, dataset/features/costs, diagnostics, comparisons, promotion evidence |
| Risk / Paper OMS / Audit | Read-only API evidence for selected decisions/orders/reconciliation/alerts | Operational drill-down, approvals, state transitions, divergence, kill-switch control with strong auth |
| Investments | API-backed thesis/portfolio/fundamental prototypes | Filing evidence, valuation ranges, KPI/thesis drift, portfolio constraints and review workflow |
| Data Health | Partial health concepts | Source/instrument/dataset scorecards, lineage, incidents, blocking state and repair history |
| Model Registry / AI Research / News / Social / Operations | Missing or prototype API fragments | Evidence-led workspaces only after underlying data and governance exist |

Required workspaces are Command Center, Market Intelligence, Instrument Workstation, Signal Explorer, Strategy Lab, Backtest Analysis, Risk Center, Portfolio Center, Investment Center, News/Event Center, Social/Narrative Center, Execution/Paper Console, Data Health, Model Registry, AI Research, Audit Center, and Operations. Correctness, accessibility, keyboard use, error/loading/empty states, provenance, timezone clarity, and dangerous-action confirmation precede visual polish. Add browser E2E tests for critical read, approval, kill-switch, and reconciliation flows.

Reporting:

- Daily trading: P&L, exposure, risk, fills, slippage, strategy/signal/cost attribution, reconciliation, incidents, and data-health exceptions.
- Weekly research: OOS evidence, drift, experiments accepted/rejected, regimes, capacity, and reproducibility status.
- Monthly investment: thesis/evidence/valuation changes, fundamentals, exposures, rebalance candidates, and theme changes.
- Quarterly governance: strategy promotion/demotion, model/provider/security reviews, DR drill, access review, and performance attribution.

Every report is reproducible from versioned data, signed/hashed, timestamped, access-controlled, and linked to corrections. Delivery is not evidence of correctness.

## M. Data-provider and broker requirements

The detailed shortlists are in Sections H and J. Procurement must produce a rights register for every endpoint and field: permitted environments, users, display, derived use, model training, retention, backup, redistribution, geography, termination/deletion, audit, and fallback. Technical proof must measure completeness, revisions, latency, reconnect/backfill, corporate actions, symbol mapping, and provider disagreement against a known sample.

Broker selection requires Belgium eligibility, assets/venues, paper fidelity, API lifecycle, streaming, supported order types, fills and corrections, short/margin/options permissions, FX and multi-currency handling, account/cash/position/P&L/fee reports, cursors, rate limits, support/escalation, maintenance windows, tax reports, operational history, security controls, and contractual automation permission. IBKR and Saxo are candidates, not recommendations to open or fund an account.

## N. Cost and scaling modes

Exact prices require quotes and current contracts. Cost categories below are relative and deliberately exclude invented provider prices.

| Mode | Data | Infrastructure/storage | Broker and monitoring | Complexity |
|---|---|---|---|---|
| LOCAL_RESEARCH | Free/authorized public, delayed/EOD, fixtures | Local PostgreSQL plus filesystem-compatible object/Parquet; low | No broker; local checks | Low |
| LOW_COST_PAPER | One contracted historical/delayed or limited real-time source plus official fundamentals/macro | Managed PostgreSQL, modest object storage, scheduled jobs; low/moderate | Internal paper only, basic on-call/alerts | Moderate |
| PROFESSIONAL_PAPER | Licensed real-time primary, news/fundamental specialists, independent fallback | HA database, object/Parquet, durable jobs, full telemetry; high | Broker sandbox, incident response and reconciliation | High |
| LIMITED_LIVE | Contracted operational data and broker, legal/tax/security reviews | Redundant control plane, tested DR and controlled deployment; high | Funded permissioned account, human coverage, strict limits | Very high |
| SCALED_LIVE | Multi-provider/venue, tick/book and enterprise rights | Multi-region/failure-domain design where justified, columnar/event systems; very high | Multiple brokers, 24/7 or market-hours operations, formal governance | Institutional |

### Asset-class rollout

1. Liquid US ETFs and a small survivorship-controlled US equity universe, daily then selected intraday: best path to instrument, corporate-action, PIT fundamental, backtest, and investment evidence.
2. Major FX and spot-gold data/paper signals: add 24/5 sessions, pair/currency conventions, financing and cross-currency risk.
3. BTC/ETH data and internal paper only: add 24/7 operation, venue/funding/counterparty and extreme-outage risk before broader crypto.
4. Borsa İstanbul: enter only with licensed data, complete local calendars/corporate actions/TRY/tick-lot rules, broker/API and Belgian/Turkish legal/tax review.
5. Futures/commodities: contract master, rolls, margin, settlement, expiry, delivery avoidance, and continuous-series validation first.
6. Options, fixed income, broader Europe/Asia: defer until specialist data, models, risk, corporate actions, calendars, and permissions are ready.

Parallel data research is allowed, but operational paper authority advances one bounded universe at a time.

## O. External blockers

| Blocker | Blocks | Engineering that proceeds without it |
|---|---|---|
| Licensed real-time and news contracts | Operational real-time paper | Adapter protocols, fixtures, replay, quality policy, procurement proof harness |
| Provider credentials | Provider-backed integration execution | Schema, raw envelope, mock server, contract tests |
| Broker sandbox/account permissions | Stage 2 | Broker protocol, state machine, simulator, reconciliation/restart tests |
| Belgian/EU legal and compliance advice | Stage 3 onward and possibly automated sandbox scope | All research and internal paper engineering |
| Belgian tax advice and broker TOB handling | Broker selection/live review | Tax-lot/report schemas and scenario fixtures |
| Cloud account and secret/KMS credentials | Hosted environment | IaC modules and local emulation |
| Security/privacy/data-license approval | Storage/use of contracted data and social sources | Rights registry and policy enforcement |
| Explicit owner authorization and capital | Any live order | All earlier stages |

An item becomes EXTERNAL_BLOCKED only after all feasible engineering and fixture gates pass. None of the current major subsystems qualifies merely because credentials are absent.

### Documentation drift

- CURRENT_STATUS.md says 255 tests and predates current PostgreSQL validation/promotion adapters; current CI ran 265.
- MASTER_ROADMAP.md's latest P0 narrative correctly says the runtime remains split, but its details do not include all f289faa/d7c1bc3 adapter evidence or the new integrity concern.
- KNOWN_LIMITATIONS.md and provider/broker matrices lag current code and remain placeholders.
- Existing “tested” labels often refer to deterministic fixtures; provider-backed and paper-operational boundaries are not consistently explicit.
- Recent CI success is scoped and can be mistaken for repository-wide lint/type/security success.

The canonical roadmap should be updated only from accepted evidence, retaining history and downgrading overclaims where necessary.

## P. Prioritized roadmap

Every item below uses the same mandatory fields. “Expected files” are planning targets, not authorization to implement.

### PF-01 — Restore validation-evidence integrity

- **Subsystem / objective / why:** Validation and promotion; implement canonical immutable manifests whose hashes remain verifiable after restart; strategy promotion is unsafe without durable evidence identity.
- **Priority / sequence / stage:** P0; 1; required in Stage 0 and all later stages. **Current / gap:** PARTIAL; PostgreSQL recovery is VERIFIED_CI but read-time verification was removed and membership is mutable.
- **Dependencies / design / files:** Existing schema/adapters; versioned canonical JSON manifest, stable IDs and semantic labels, manifest hash, immutable membership, relational projections, legacy backfill; migrations, quant_validation.py, postgres_quant_validation.py, repositories, tests and docs.
- **Data / cost / blockers:** Existing synthetic artifacts; low engineering/storage cost; none.
- **Tests / integration tests:** serialization oracle, hash/tamper/membership/legacy tests; PostgreSQL write-read-restart, interrupted transaction, backup/restore and promotion fail-closed tests.
- **Acceptance / failure conditions:** Every recovered package verifies exact canonical bytes and artifact membership; any mutation/corruption blocks promotion. Failure is a mismatch that passes, an unverifiable package shown as verified, or mutable membership.
- **Security / evidence:** Define privileged-tamper threat and optional signature/WORM path; current-commit CI logs, migration proof and recovered manifest/hash evidence. No waived integrity check.

### PF-02 — Make PostgreSQL the paper-runtime authority

- **Subsystem / objective / why:** Persistence/composition; replace safety-critical SQLite construction with protocols and PostgreSQL adapters; eliminate split-brain.
- **Priority / sequence / stage:** P0; 2; Stage 0 internal paper foundations. **Current / gap:** PARTIAL; adapters exist but build_paper_runtime always wires SQLite.
- **Dependencies / design / files:** PF-01; explicit storage protocols, one PostgreSQL unit-of-work/composition root, fail-fast environment validation, no implicit fallback; paper_runtime.py, persistence.py, risk/pretrade/OMS/reconciliation modules and tests.
- **Data / cost / blockers:** Synthetic paper fixtures; moderate engineering, low infra; none.
- **Tests / integration:** composition selection and forbidden-fallback unit tests; full PostgreSQL signal-to-risk-to-intent-to-fill-to-reconciliation restart E2E.
- **Acceptance / failure:** Paper profile contains zero MUST_MIGRATE SQLite authority and all safety state survives restart atomically. Any dual write, fallback, or cross-store non-atomic decision fails.
- **Security / evidence:** Least-privilege DB roles and protected DSN; dependency graph plus CI transaction/restart logs prove VERIFIED_CI.

### PF-03 — Complete mapped migration and cutover

- **Subsystem / objective / why:** Persistence migration; reproducibly map legacy SQLite state to PostgreSQL with checksums and rollback; avoid silent data loss.
- **Priority / sequence / stage:** P0; 3; before Stage 1. **Current / gap:** PARTIAL; schema/backfill helpers exist, no complete representative apply/cutover.
- **Dependencies / design / files:** PF-02; versioned mapping manifests, dry run, idempotent apply, counts/hashes, quarantine, cutover flag, read-only rollback window; postgres_backfill.py, scripts, migrations, runbooks, tests.
- **Data / cost / blockers:** Sanitized representative fixture; moderate; none.
- **Tests / integration:** duplicate/conflict/malformed/resume tests; fresh PG apply, rerun, checksum, cutover, rollback and reconciliation.
- **Acceptance / failure:** Exact mapped counts/hashes, documented exceptions and reversible cutover. Unmapped operational row, silent coercion, or non-idempotency fails.
- **Security / evidence:** No secret/PII leakage in fixtures/logs; signed migration report and CI artifact.

### PF-04 — PostgreSQL backup, restore, and recovery gate

- **Subsystem / objective / why:** DR; prove a fresh restore and post-restore reconciliation; runtime authority without recovery is unsafe.
- **Priority / sequence / stage:** P0; 4; before Stage 1. **Current / gap:** NOT_STARTED for PostgreSQL; only SQLite backup prototype.
- **Dependencies / design / files:** PF-02/03; encrypted logical plus WAL/physical strategy, retention, isolated copy, restore runbook, manifest verification and reopen gate; backup scripts, IaC later, DR docs/tests.
- **Data / cost / blockers:** Synthetic operational DB and artifacts; moderate; cloud isolation later but local proof unblocked.
- **Tests / integration:** corrupted/incomplete backup, key loss simulation, fresh restore, hashes, cursors, cash/positions/orders, kill-switch state and reconciliation.
- **Acceptance / failure:** Repeated measured restore meets approved targets; until then RPO/RTO are unachieved. Missing authority, hash failure, or trading before reconciliation fails.
- **Security / evidence:** Encryption, restore-role separation and access audit; timestamped drill report and recovery metrics.

### PF-05 — Repository security and quality baseline

- **Subsystem / objective / why:** CI/security/governance; make green CI representative and control changes; current scoped checks and vulnerable dependencies mask risk.
- **Priority / sequence / stage:** P0; 5, parallel after PF-01 design; before Stage 1. **Current / gap:** PARTIAL; full Ruff/mypy debt, 34 frontend vulnerabilities, public/unprotected repo, transient SBOM.
- **Dependencies / design / files:** Owner decision for visibility; staged lint/type remediation, dependency upgrades, branch rules, CODEOWNERS, SAST/SCA/secrets, retained SBOM/provenance; workflow/config/dependency manifests and broad source annotations.
- **Data / cost / blockers:** No market data; moderate engineering/tool cost; owner approval for visibility/protection.
- **Tests / integration:** full repo quality/security scans, dependency regression, frontend browser smoke; required checks exercised on a PR.
- **Acceptance / failure:** Approved baseline has no unreviewed high vulnerability, required checks cannot be bypassed, findings are fixed or time-bounded/risk-accepted. Scoped-only green or unretained evidence fails.
- **Security / evidence:** Core security item; branch settings, reports, SBOM and current CI artifacts.

### DA-01 — Authoritative instrument, calendar, and mapping service

- **Subsystem / objective / why:** Shared data; temporal canonical identity and trading conventions for the initial US universe; every data/risk/order decision depends on it.
- **Priority / sequence / stage:** P1; 6; Stage 0→1. **Current / gap:** PARTIAL fixtures, no provider-backed lifecycle.
- **Dependencies / design / files:** PF-02; versioned instruments/identifiers/mappings, exchanges/sessions/holidays/half-days/tick/lot/currency/corporate relationships and mapping approvals; instrument/calendar/provider adapter modules, schema and tests.
- **Data / cost / blockers:** Exchange/provider reference data; low/moderate; storage/usage rights review.
- **Tests / integration:** ticker reuse/change, delisting, split, DST, half-day, overnight/24-7 and mapping conflict; provider sample to canonical data/risk path.
- **Acceptance / failure:** Point-in-time lookup reproduces the correct identity/session/rules and rejects ambiguity. Silent remap or wrong session fails.
- **Security / evidence:** Mapping change approval/audit; versioned sample manifests and CI plus provider-backed proof.

### DA-02 — Immutable real historical data vertical slice

- **Subsystem / objective / why:** Market data; create the first authorized, reproducible US ETF/equity dataset; real research cannot proceed on fixtures.
- **Priority / sequence / stage:** P1; 7; Milestone A/Stage 0. **Current / gap:** PROTOTYPE local data, no contracted vertical slice.
- **Dependencies / design / files:** DA-01 and rights approval; adapter envelope, raw object, normalized Parquet, dataset manifest, PIT corporate actions/delistings, incremental backfill; market-data adapters, storage, dataset registry and jobs.
- **Data / cost / blockers:** Licensed/authorized historical prices/actions; low-to-moderate development then provider cost; contract/credentials.
- **Tests / integration:** schema/idempotency/rate/retry/revision/corporate-action and survivorship tests; provider download→raw→normalized→feature→backtest reproduction.
- **Acceptance / failure:** Same manifest reproduces identical dataset hash and adjusted/unadjusted semantics with no unlicensed retention. Missing raw lineage, survivorship, or timestamp ambiguity fails.
- **Security / evidence:** Credential isolation and rights enforcement; provider receipt, manifest, quality report and independent sampled reconciliation.

### DA-03 — Data Health Score and blocking control

- **Subsystem / objective / why:** Data quality/risk; turn data defects into deterministic operating policy; stale data must not authorize signals.
- **Priority / sequence / stage:** P1; 8; before Stage 1 exit. **Current / gap:** PROTOTYPE health concepts, not a universal gate.
- **Dependencies / design / files:** DA-01/02; dimensional scores, rule/version registry, incident lifecycle, fallback provenance, block scopes and risk integration; data health, signals, pretrade, API/UI modules.
- **Data / cost / blockers:** Provider samples and fault corpus; moderate; secondary provider for disagreement proof later.
- **Tests / integration:** every named defect and severity transition; corrupt/stale provider input blocks signal/strategy/instrument/asset/all scopes as configured.
- **Acceptance / failure:** No operational signal can bypass required health policy; every block has evidence and recovery. Averaged-away critical errors or silent fallback fails.
- **Security / evidence:** Provider spoofing/poisoning and operator override audit; fault-injection CI and paper drill.

### FM-01 — PIT fundamentals and investment facts

- **Subsystem / objective / why:** Fundamentals/investment; build filing-time-correct statements and company facts; long-term analysis needs auditable source truth.
- **Priority / sequence / stage:** P1; 9; Milestone B, Stage 0. **Current / gap:** PARTIAL SEC adapter/fixtures; no scheduled raw/revision pipeline.
- **Dependencies / design / files:** DA-01/02 storage pattern; SEC raw filings/company facts, taxonomy mapping, as-reported and standardized views, amendments, fiscal calendars, quality review; fundamentals, investment, jobs/schema/UI.
- **Data / cost / blockers:** SEC official data; low infrastructure, later paid estimates/ownership; fair-access and terms compliance.
- **Tests / integration:** amendments, restatements, units, dimensions, period selection, fiscal changes and reconciliation; filing→facts→metrics→thesis evidence.
- **Acceptance / failure:** Every metric resolves to source filing/fact and availability timestamp; no latest-value leakage. Untraceable normalization or revision overwrite fails.
- **Security / evidence:** Source integrity and parser hardening; provider-backed company sample with hand-reconciled statements.

### MA-01 — PIT macro and economic calendar

- **Subsystem / objective / why:** Macro; preserve releases and revisions for regime/macro research; final revised values create leakage.
- **Priority / sequence / stage:** P1; 10, parallel with FM-01; Stage 0. **Current / gap:** PROTOTYPE fixtures.
- **Dependencies / design / files:** DA-02 pattern; FRED/ALFRED/ECB adapters, release/vintage model, calendar, units/frequency transformations and rights metadata; macro modules, jobs, storage/tests.
- **Data / cost / blockers:** Official APIs plus licensed series/calendar later; low; API keys/third-party series terms.
- **Tests / integration:** initial/revised vintages, publication lag, missing release, frequency and timezone; as-of macro feature/backtest.
- **Acceptance / failure:** An as-of query returns only then-known values and source revisions. Final-value leakage or hidden interpolation fails.
- **Security / evidence:** Key isolation and rights flags; vintage manifests, release audit and provider-backed CI/controlled run.

### NE-01 — Licensed news and event intelligence

- **Subsystem / objective / why:** News/events; correction-aware evidence and event taxonomy; event strategies and thesis drift require trustworthy timing.
- **Priority / sequence / stage:** P2; 11; Stage 0 research, Stage 1 only after rights/quality. **Current / gap:** PROTOTYPE fixtures.
- **Dependencies / design / files:** DA-01/03, contract; raw/derived rights, dedupe, cluster, entity/ticker link, event type, novelty/urgency/uncertainty, correction/retraction and horizon labels; news/events/AI/API/UI.
- **Data / cost / blockers:** Licensed structured news and issuer releases; high recurring cost; contract/credentials.
- **Tests / integration:** duplicate, correction, rumor, ambiguous entity, late story, multilingual/adversarial text; feed→event→evidence→strategy/thesis confidence withdrawal.
- **Acceptance / failure:** Source and correction lineage is complete and measured mapping/classification meets approved thresholds. A corrected story remaining operationally valid fails.
- **Security / evidence:** Prompt injection/content sanitization and license enforcement; labeled evaluation, correction drill and rights audit.

### SN-01 — Lawful social/narrative pilot

- **Subsystem / objective / why:** Narrative; test whether permitted aggregate signals add robust value; prevent unlawful collection and manipulation.
- **Priority / sequence / stage:** P2; 12, after NE-01; research only. **Current / gap:** NOT_STARTED.
- **Dependencies / design / files:** Legal/privacy/license approval; source-separated aggregation, minimization, bot/spam/coordination/pump risk, sentiment change/disagreement/clusters/crowding/divergence; new narrative modules and governance/tests.
- **Data / cost / blockers:** Contracted permitted sources and labeled corpus; medium/high; legal, privacy, retention/deletion and source terms.
- **Tests / integration:** bots, raids, deletions, identifier privacy, manipulation, model bias/drift; source→aggregate→research feature with no personal operational profiling.
- **Acceptance / failure:** Rights and deletion are enforceable and incremental OOS value survives manipulation filters. Scraping without approval, raw personal-data sprawl, or LLM-only score fails.
- **Security / evidence:** Privacy threat model, access/retention controls; DPIA/legal sign-off, deletion drill and OOS report.

### FE-01 — Versioned feature platform

- **Subsystem / objective / why:** Quant data; broaden feature families with offline reproducibility and freshness; ad hoc calculations hide leakage.
- **Priority / sequence / stage:** P1; 13, starts after DA-02 and expands with later data; Stage 0/1. **Current / gap:** PARTIAL; a version concept and narrow fixture-backed features exist, but shared materialization, breadth, freshness and offline/online parity do not.
- **Dependencies / design / files:** DA-02/03, FM/MA as available; feature specs, registry, dependency DAG, as-of joins, materialization manifests, quality/freshness and offline/online parity; feature modules/storage/jobs/tests.
- **Data / cost / blockers:** Authorized inputs; moderate compute/storage; dependent provider rights.
- **Tests / integration:** numerical oracles, PIT/leakage, missingness, revision, determinism and incremental rebuild; raw→feature→strategy/research manifest.
- **Acceptance / failure:** Feature value is reproducible from exact sources/code/version and cannot use future availability. Hidden global state, unversioned change, or stale operational feature fails.
- **Security / evidence:** Code/data provenance and resource limits; manifests, oracle results and sampled independent calculations.

### QR-01 — Institutional validation kernel and trial registry

- **Subsystem / objective / why:** Quant validation; replace checklist approximations with referenced, oracle-tested methods and researcher-accountability.
- **Priority / sequence / stage:** P1; 14; before strategy promotion to Stage 1. **Current / gap:** TESTED synthetic approximations.
- **Dependencies / design / files:** PF-01, FE-01; preregistered trials, nested/purged/embargoed splits, holdout vault, block bootstrap/Monte Carlo, PSR/DSR/PBO/BH/reality checks with references, capacity/cost stress; quant/research validation, registries and reports.
- **Data / cost / blockers:** Versioned real datasets; moderate compute; none beyond data.
- **Tests / integration:** published numerical examples/oracles, adversarial leakage and repeated researcher trials; experiment→manifest→validation→promotion/rejection.
- **Acceptance / failure:** Trial count and degrees of freedom cannot be omitted; methods match references within tolerance and holdout access is controlled. Approximation presented as real, rerun cherry-picking, or mutable report fails.
- **Security / evidence:** Holdout access and promotion-role separation; oracle suite, immutable trial history and independent review.

### QR-02 — Hypothesis-led strategy program

- **Subsystem / objective / why:** Active research; evaluate the families in Section F without strategy-count incentives; seek robust falsifiable evidence.
- **Priority / sequence / stage:** P1/P2 by family; 15; Stage 0 then 1. **Current / gap:** TESTED four baselines only.
- **Dependencies / design / files:** FE-01/QR-01; common strategy contract, universe/cost/capacity/exit/failure-regime specifications and benchmark comparisons; strategy modules, experiment configs, reports/tests.
- **Data / cost / blockers:** Family-specific authorized real data; moderate/high; provider coverage.
- **Tests / integration:** signal invariants, no-lookahead, turnover, costs, capacity, parameter surfaces, regimes/crises/markets; provider dataset→validated package→internal paper signal.
- **Acceptance / failure:** Only preregistered, reproducible, net-OOS robust candidates advance; no alpha claim before evidence. Profitable in-sample result, hidden trials, or unexplained concentration fails.
- **Security / evidence:** Research/promotion separation and IP controls; full package, rejection archive and forward-paper record.

### RP-01 — Probabilistic regime and portfolio ensemble

- **Subsystem / objective / why:** Regime/portfolio; combine validated strategies without hidden correlated leverage.
- **Priority / sequence / stage:** P2; 16; Stage 1. **Current / gap:** PROTOTYPE rule/checklist and limited portfolio logic.
- **Dependencies / design / files:** QR-02 plus portfolio histories; calibrated transparent baseline, challenger models, covariance uncertainty, risk budgets, exposures/capacity/drawdown/decay and regime eligibility; regime, portfolio, risk, attribution modules.
- **Data / cost / blockers:** Cross-asset features and paper returns; moderate compute; data breadth.
- **Tests / integration:** calibration, transition, correlation convergence, missing strategy, leverage/exposure accounting and crisis tests; multi-strategy signals→allocation→risk gate.
- **Acceptance / failure:** Allocation respects all aggregate constraints and regime uncertainty can only reduce eligibility/risk. Hidden leverage, unstable state flip, or risk-limit increase by model fails.
- **Security / evidence:** Model/constraint version approvals; OOS calibration, stress and forward-paper attribution.

### IN-01 — Professional investment intelligence workflow

- **Subsystem / objective / why:** Long-term investment; turn local thesis/valuation prototypes into evidence-led research and portfolio governance.
- **Priority / sequence / stage:** P1/P2; 17, starts after FM-01; research/paper portfolio only. **Current / gap:** TESTED local synthetic prototypes; provider-backed PIT facts, governed review, drift and portfolio authority are missing.
- **Dependencies / design / files:** FM-01/MA-01/NE-01 as available; thesis evidence graph, quality metrics, DCF/reverse DCF/scenarios, KPI/catalyst/invalidation/drift, review cadence, constrained portfolio/rebalance; investment modules, schema, API/UI/reports.
- **Data / cost / blockers:** PIT filings/prices, macro/news and later estimates/ownership; moderate/high; licensed sources for non-SEC fields.
- **Tests / integration:** filing revision, valuation sensitivity, stale thesis, invalidation, review and rebalance constraints; source→facts→valuation→thesis→portfolio→monthly report.
- **Acceptance / failure:** All claims and values are reproducible with ranges/assumptions and independent active-trading capital. False precision, missing source, skipped invalidation, or authority mixing fails.
- **Security / evidence:** Analyst approvals and confidential-thesis access; reviewed company cases, portfolio stress and drift history.

### ML-01 — Governed statistical-learning lifecycle

- **Subsystem / objective / why:** ML; introduce models only when they beat simpler baselines economically and safely.
- **Priority / sequence / stage:** P2; 18; Stage 0 research, risk-reducing use in Stage 1. **Current / gap:** PROTOTYPE registry only.
- **Dependencies / design / files:** FE-01/QR-01; hierarchy, label registry, training/evaluation, calibration, registry, drift, champion/challenger and rollback; model/training/registry/monitor modules, artifacts/tests.
- **Data / cost / blockers:** Versioned labeled data; moderate/high compute later; none beyond data.
- **Tests / integration:** leakage, calibration, reproducibility, baseline comparison, drift, rollback, adversarial/missing features; training→artifact→approved inference→monitor.
- **Acceptance / failure:** Model has reproducible economic benefit, calibrated uncertainty, approved scope and cannot raise risk. Benchmark failure, unversioned label, or silent drift fails.
- **Security / evidence:** Model supply-chain, artifact signatures and restricted promotion; model card, evaluation, approval and live-paper monitor.

### AI-01 — Auditable untrusted AI intelligence

- **Subsystem / objective / why:** AI; use specialists for evidence workflows without granting market/risk truth authority.
- **Priority / sequence / stage:** P2; 19; research support only initially. **Current / gap:** PROTOTYPE structured/deterministic agents.
- **Dependencies / design / files:** Source evidence systems and auth; strict schemas, retrieval provenance, facts/inference separation, contradiction/challenge roles, human approval, prompt/model registry and evaluation; agent research, prompts, evidence/API/UI/tests.
- **Data / cost / blockers:** Licensed documents/news and model service; variable/high; provider/model contracts and privacy review.
- **Tests / integration:** citation entailment, unsupported claim, prompt injection, malicious document, schema failure, contradiction, outage and model-version drift; source→agent→challenger→human-reviewed report.
- **Acceptance / failure:** Outputs are source-traceable, calibrated and never directly authorize order/size/risk/legal facts. Fabricated citation, hidden uncertainty, or privilege crossing fails.
- **Security / evidence:** Treat content/output as untrusted, sandbox tools, least privilege and retention; red-team suite, evaluation dashboard and approvals.

### OP-01 — Deployable, observable, recoverable service

- **Subsystem / objective / why:** SRE/deployment; operate Stage 1 reliably and make failures visible/recoverable.
- **Priority / sequence / stage:** P1; 20, parallel after PF foundation; required before Stage 1 exit. **Current / gap:** PROTOTYPE health/alerts, no deployment/IaC/SLO.
- **Dependencies / design / files:** PF-04/05 and DA-03; containers, managed PG/object store, IaC, environment promotion, structured logs/metrics/traces, SLOs, on-call/runbooks, incident/DR automation; deployment, IaC, telemetry and ops docs/tests.
- **Data / cost / blockers:** Operational telemetry; moderate/high; cloud/KMS credentials for hosted proof.
- **Tests / integration:** deploy/rollback, dependency outage, alert routing, telemetry correlation, resource exhaustion and DR; staged environment soak.
- **Acceptance / failure:** Reproducible deploy/rollback, actionable SLOs and measured recovery with no secret leakage. Manual snowflake, silent failure or untested rollback fails.
- **Security / evidence:** Network/identity/secrets/container hardening; IaC plan, scan, soak, incident and DR reports.

### UX-01 — Evidence-first workspaces and governed reports

- **Subsystem / objective / why:** Product; make research, risk, investment, data, AI and operations evidence usable without hiding uncertainty.
- **Priority / sequence / stage:** P2; 21, incremental after each backend; Stage 0 onward. **Current / gap:** PARTIAL static/fixture/API scaffold.
- **Dependencies / design / files:** Auth/RBAC and relevant APIs; role-aware workspaces, provenance, timezones, uncertainty, accessible states, dangerous-action approvals and report archive; web pages/components/API, browser tests.
- **Data / cost / blockers:** Existing governed APIs; moderate; none.
- **Tests / integration:** accessibility, authorization, empty/error/stale, timezone, approval and kill-switch UX; browser E2E against seeded PostgreSQL.
- **Acceptance / failure:** Operator can trace every value/decision and safely act within role; static health, stale unlabeled data, inaccessible controls, or UI-only authority fails.
- **Security / evidence:** XSS/CSP/CSRF/session/access tests; CI browser videos/traces and user acceptance.

### BR-01 — Real broker sandbox adapter

- **Subsystem / objective / why:** Broker/OMS; validate real protocol behavior without capital; needed for Stage 2 only.
- **Priority / sequence / stage:** P2; 22; Stage 2. **Current / gap:** NOT_STARTED; internal fixtures only.
- **Dependencies / design / files:** PF/DA/RP/OP gates and broker selection; vendor-neutral adapter, token/stream lifecycle, idempotent submission, cursor/replay, unknown state, rate limit, reports and reconciliation; broker adapters, OMS/reconciliation, secret config/tests.
- **Data / cost / blockers:** Sandbox market/account data; broker/account credentials and permissions; moderate provider/account/ops cost.
- **Tests / integration:** broker contract simulator then sandbox partial fills/cancel/replace/reject/disconnect/restart/duplicate/delayed event; multi-day soak and zero-capital reconciliation.
- **Acceptance / failure:** Deterministic convergence and complete audit across reconnect/restart with no real endpoint/capital. Unknown unresolved order, credential leak, or sandbox/live confusion fails.
- **Security / evidence:** Isolated credentials, endpoint allowlist, live compile/runtime interlock and dual approval; sandbox logs, broker statements and reconciliation report.

### SH-01 — Shadow operation

- **Subsystem / objective / why:** Operations/trading; compare intended decisions with live market/broker state without submitting orders; validate decay and operational assumptions.
- **Priority / sequence / stage:** P3; 23; Stage 3. **Current / gap:** NOT_STARTED.
- **Dependencies / design / files:** BR-01 Stage 2 exit; immutable shadow intents, hypothetical fill policy, real-time data, daily reconciliation/attribution, incident and stop controls; runtime, reports, ops/UI/tests.
- **Data / cost / blockers:** Professional real-time data and read-only broker state; legal/compliance and contracts; high.
- **Tests / integration:** long-running clock/provider/broker/network/DB failures and drift; parallel paper/shadow comparison over approved duration.
- **Acceptance / failure:** Stable multi-week/month evidence, bounded unexplained divergence and zero submissions. Any order route, unresolved drift, or incident outside tolerance fails.
- **Security / evidence:** Network-enforced no-submit credential and change control; signed daily reports, incident log and governance review.

### LV-01 — Controlled live-readiness review

- **Subsystem / objective / why:** Governance; decide whether an extremely limited pilot is defensible; this item does not authorize live trading.
- **Priority / sequence / stage:** P4; 24; Stage 4 review then Stage 5 much later. **Current / gap:** NOT_STARTED.
- **Dependencies / design / files:** All prior stage exits, legal/tax/security/SRE approval and explicit owner authorization; independent readiness checklist, capital/loss/instrument/time caps, dual control, rollback and post-trade review; policies, runbooks, approval records.
- **Data / cost / blockers:** Complete paper/shadow/incident/performance evidence; very high; licenses, broker, legal, tax, capital, insurance/operations and explicit authorization.
- **Tests / integration:** live-endpoint isolation drill, kill switches, disaster game days, order/fill/reconciliation and human escalation simulations; independent audit.
- **Acceptance / failure:** Only a signed, time-bounded review may recommend a pilot; no automatic transition. Missing approval, recovery, unresolved risk or alpha/operational evidence fails.
- **Security / evidence:** Maximum separation, least privilege and dual authorization; independent signed review. This audit supplies no live authorization.

## Q. Critical path

~~~mermaid
flowchart LR
  P0["PF-01..05\nP0 safety exit"] --> IM["DA-01\nInstrument/calendar"]
  IM --> HD["DA-02 + DA-03\nReal historical data + health"]
  HD --> A["Milestone A\nProfessional real-data research"]
  HD --> FE["FE-01 + QR-01\nFeatures + validation"]
  FE --> QR["QR-02\nRobust strategy research"]
  QR --> C["Milestone C"]
  HD --> FM["FM-01 + MA-01\nFundamental/macro"]
  FM --> IN["IN-01\nInvestment workflow"]
  IN --> B["Milestone B"]
  QR --> RP["RP-01\nRegime/ensemble"]
  RP --> RT["OP-01 + real-time provider\nInternal real-time paper"]
  RT --> D["Milestone D"]
  D --> BR["BR-01\nBroker sandbox"]
  BR --> E["Milestone E"]
  E --> SH["SH-01\nShadow"]
  SH --> F["Milestone F"]
  F --> LV["LV-01\nIndependent live-readiness review"]
  LV --> G["Milestone G"]
~~~

The shortest credible sequence is P0 integrity/authority/recovery/security → canonical instruments → authorized historical data and health → reproducible feature/validation kernel → real-data strategy and investment vertical slices → portfolio/risk/operations → real-time internal paper → broker sandbox → shadow → independent live-readiness review.

Parallel work:

- PF-05 quality/security can proceed alongside PF-01/02 without weakening their tests.
- FM-01 and MA-01 can proceed in parallel after shared raw/manifest conventions.
- UX-01 follows proven backend slices incrementally; it is never on the correctness critical path.
- Procurement/legal discovery can run throughout, but cannot upgrade engineering status.
- NE-01, ML-01 and AI-01 may run as isolated research after their source/validation prerequisites.

Milestones:

- **A:** an authorized real historical dataset, PIT/corporate-action correctness, health gates, reproducible features/backtests, and immutable evidence.
- **B:** provider-backed filing/macro facts, reproducible valuations/theses, constraints, drift/review, and monthly report.
- **C:** at least one preregistered strategy family evaluated with real OOS/cost/capacity evidence; rejection is an acceptable scientific outcome.
- **D:** licensed real-time internal paper with authoritative PG state, SLOs, failures/recovery, attribution, and no broker submission.
- **E:** broker sandbox convergence across reconnect/restart and multi-day soak.
- **F:** sustained no-submit shadow operation with explained divergence and governance review.
- **G:** independent security/legal/tax/risk/SRE/research review may consider—not authorize—limited live.

## R. Things explicitly deferred

- Reinforcement learning, HFT, microsecond optimization, co-location, and custom FPGA/GPU execution.
- Options market making, complex volatility surfaces, and advanced multi-leg execution.
- Two-thousand-instrument real-time books, L3 everywhere, and broad simultaneous asset rollout.
- Kubernetes, service mesh, multi-region active/active, Kafka-scale infrastructure, or ClickHouse before measured need.
- TWAP/VWAP/POV/iceberg/adaptive EMS before basic OMS/reconciliation recovery.
- Dozens of weak strategies, neural forecasting without benchmark advantage, or alternative data without rights.
- Elaborate autonomous agent organizations before reliable source evidence and evaluation.
- Social scraping without explicit permission, privacy and retention controls.
- Frontend animation/polish ahead of data, risk, accessibility and operational correctness.
- BIST, broad crypto, futures, options, fixed income, Europe/Asia operational expansion before their dependency gates.
- Any real broker credential, funded account, live endpoint, real order, or risk-limit automation.

## S. Definition of done and professional success

### Stage gates

| Stage | Entrance | Exit |
|---|---|---|
| Stage 0 — Research only | P0 environment isolation; no live route; documented datasets/experiments | Milestone A and/or B evidence; reproducible PIT research; rejected trials retained; security baseline |
| Stage 1 — Internal paper on real data | P0 exit, authorized real data, health blocking, approved strategies/policies, observable runtime | Sustained real-time internal paper; exact PG restart/DR/reconciliation; stable attribution/incidents within approved limits |
| Stage 2 — Broker paper/sandbox | Stage 1 exit, selected broker contract, isolated sandbox creds, legal scope check | Multi-day/weeks reconnect/restart/order-state/reconciliation proof and resolved discrepancies |
| Stage 3 — Shadow | Stage 2 exit, professional live data and no-submit broker/read-only design | Sustained shadow evidence, explained paper/shadow divergence, game days and independent governance approval |
| Stage 4 — Extremely limited controlled live pilot | Stage 3 exit plus explicit owner, legal, tax, broker, security, risk and operations approval | Time/capital/instrument/loss-capped pilot meets predetermined operational and research criteria with no severe incident |
| Stage 5 — Controlled multi-strategy live | Stage 4 evidence plus a new independent approval | Bounded scale increases only; continuous reviews, rollback, capacity and correlation controls |

No stage may be skipped, and passing a stage never guarantees progression.

Research quality is done only when PIT correctness, raw lineage, deterministic reproduction, untouched OOS robustness, multiple-testing accounting, realistic costs, and capacity stress are demonstrated. Trading quality requires forward-paper stability, measured slippage, exact reconciliation, signal-decay and strategy-stability evidence. Investment quality requires complete sources, reproducible valuation ranges, thesis consistency/invalidation/drift, and disciplined portfolio constraints. Operations require approved SLOs, data freshness, reconciliation success, controlled incident rate, security gates, and exercised recovery. A green test suite, profitable backtest, plausible AI narrative, or polished dashboard is insufficient.

No profitability, alpha, or production-readiness claim is made by this plan.

## T. Next 10 engineering cycles

These cycles are exact and sequential. A stop condition pauses progression; it is not permission to weaken the gate.

### Cycle 1 — Canonical validation manifest

- **Objective:** Restore cryptographically verifiable package identity after restart.
- **Implementation scope:** PF-01 manifest schema/serialization/hash, immutable membership, relational projection, legacy-state migration and fail-closed reads.
- **Tests:** Canonicalization oracles; row/manifest/artifact/membership tamper; insert/update/delete; interruption; restart and promotion.
- **Acceptance evidence:** Current-commit PostgreSQL CI package whose stored bytes independently hash to the returned identity; tamper suite blocks every case.
- **Stop condition:** Any recovered package cannot reproduce its hash or any legacy package is silently marked verified.

### Cycle 2 — PostgreSQL paper composition root

- **Objective:** Remove safety-critical SQLite selection from paper mode.
- **Implementation scope:** Storage protocols, PostgreSQL factories/unit-of-work, explicit test/local SQLite profile, fail-fast configuration.
- **Tests:** Dependency-graph assertions, forbidden fallback, transaction boundaries and startup failure.
- **Acceptance evidence:** Runtime inventory proves zero MUST_MIGRATE SQLite authority in paper; CI starts and restarts PostgreSQL composition.
- **Stop condition:** Dual authority, implicit fallback, or a safety decision spans non-atomic stores.

### Cycle 3 — Complete PostgreSQL pre-trade authority

- **Objective:** Persist every input/output needed for an atomic risk decision.
- **Implementation scope:** Policies, instruments, signals, models, execution evidence, quotes, return history, assessments/decisions, reconciled account/position, audit and scoped kill switches.
- **Tests:** Stale/missing/mismatched versions, concurrent limits, HMAC/integrity, scopes, restart and deny-by-default.
- **Acceptance evidence:** One PostgreSQL transaction produces a fully replayable decision lineage.
- **Stop condition:** Any decision depends on unversioned memory/SQLite state or database failure can authorize.

### Cycle 4 — Full paper-runtime PostgreSQL E2E

- **Objective:** Verify signal→risk→intent→OMS→fill→reconciliation→attribution across failures.
- **Implementation scope:** Compose selected existing PG OMS/risk/quant adapters into one seeded paper runtime with broker simulator.
- **Tests:** Partial fill, duplicate/delayed/out-of-order event, reject, unknown state, process kill, DB/network interruption and restart.
- **Acceptance evidence:** CI proves exact state convergence, idempotency, cursor recovery and kill-switch persistence.
- **Stop condition:** Cash/position/order/risk/audit divergence or an unresolved unknown state.

### Cycle 5 — Mapped SQLite migration and cutover

- **Objective:** Prove representative legacy state can migrate safely.
- **Implementation scope:** Mapping manifest, dry-run/apply/resume, quarantine, counts/hashes, read-only rollback and cutover runbook.
- **Tests:** Complete fixture, rerun, conflicts, malformed rows, interruption, rollback and reconciliation.
- **Acceptance evidence:** Signed migration report with exact source/target counts, hashes, exceptions and successful rollback rehearsal.
- **Stop condition:** Silent coercion, missing operational row, non-idempotency or unbounded rollback risk.

### Cycle 6 — PostgreSQL backup/fresh restore P0 exit

- **Objective:** Exercise recoverability and formally decide P0.
- **Implementation scope:** Encrypted backup catalog, fresh restore, artifact/manifest verification, post-restore reconciliation, measured RPO/RTO and gate checklist.
- **Tests:** Missing/corrupt backup, wrong key/role, partial object set, cursor/order divergence and repeated restore.
- **Acceptance evidence:** Independent fresh-environment drill and reconciled runtime; P0 can be marked VERIFIED_CI only if all Cycles 1–6 pass.
- **Stop condition:** Any authority/evidence missing, hash mismatch, unreconciled state, or unmeasured recovery.

### Cycle 7 — Security, dependencies, and representative CI

- **Objective:** Make the repository safe to receive proprietary data and make green CI meaningful.
- **Implementation scope:** Resolve high frontend vulnerabilities, stage full Ruff/mypy debt, review Bandit findings, SAST/SCA/secrets, retained SBOM/provenance, CODEOWNERS/branch protection and visibility decision.
- **Tests:** Full scans/build/test/browser smoke and a PR enforcement rehearsal.
- **Acceptance evidence:** No unreviewed high dependency issue; retained reports/SBOM; protected required checks and documented visibility decision.
- **Stop condition:** A required check is bypassable, a high issue lacks time-bounded acceptance, or proprietary material would enter a public/uncontrolled repo.

### Cycle 8 — Initial instrument master and calendars

- **Objective:** Establish temporal identity/rules for a bounded liquid US ETF/equity universe.
- **Implementation scope:** DA-01 identifiers, mappings, ticker lifecycle, currency, tick/lot, sessions, holidays/half-days and corporate-action relationships.
- **Tests:** DST, holidays, half-days, ticker reuse/change, delisting, split and mapping conflict.
- **Acceptance evidence:** Versioned, reviewed sample reproduces correct as-of identity and market session across edge cases.
- **Stop condition:** Ambiguous instrument/provider mapping or incomplete calendar can flow into data/risk.

### Cycle 9 — Authorized immutable historical-data slice

- **Objective:** Create the first real reproducible research dataset without vendor lock-in.
- **Implementation scope:** One approved provider adapter, raw immutable capture, Parquet normalization, corporate actions/delistings, manifest, incremental backfill and rights policy.
- **Tests:** Rate/retry/idempotency, corrections, adjusted/unadjusted semantics, PIT availability, survivorship and independent sampled reconciliation.
- **Acceptance evidence:** Provider receipt/rights record, raw hashes, deterministic dataset manifest and quality report.
- **Stop condition:** Storage/derived-use rights are unclear, raw lineage is missing, or PIT/corporate-action sample disagrees unresolved.

### Cycle 10 — Data-health gate and first real-data research vertical slice

- **Objective:** Reach Milestone A with a bounded baseline, not an alpha claim.
- **Implementation scope:** Data Health Score/blocking, versioned features, existing naive/baseline strategy, realistic initial costs, reproducible backtest and immutable validation report.
- **Tests:** All quality fault classes, leakage, delayed/missing data, cost/capacity stress, cross-engine reconciliation and full reproduction from raw manifest.
- **Acceptance evidence:** A reviewer can rebuild the same features/results from authorized raw data; defects block correctly; limitations and rejection are acceptable outcomes.
- **Stop condition:** Stale/low-quality data yields a valid signal, reproduction differs, holdout leaks, or results are described as alpha without evidence.

After Cycle 10, re-audit Milestone A and only then schedule FM-01/MA-01, QR-01/02, IN-01, and operational real-time work in dependency order. Do not start broker integration because the calendar says ten cycles have elapsed; start it only when Stage 1 exit evidence exists.
