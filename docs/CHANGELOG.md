# Changelog

## 2026-08-31

- Verified Cycle 230 unchanged on exact merged main commit
  `a4d835fd3832b7a9a6f2ae37970091d4bee49902` in CI run `33337948319`.
- Added Cycle 231's metadata-only upstream identity/activity refresh. It records
  all 16 local pins, checked-out branches, remote branch heads, activity times,
  discovery-only GitHub license metadata and working-tree state without fetching
  or executing a candidate. Twelve remote branches advanced; all remain pinned,
  unadopted and subject to the complete RQ-029 gate.
- Verified Cycle 231 on exact merged main commit
  `a2efd002a7cc7d4c4c40808bfd6def57fce84eda` in CI run `33397619313`.
- Added Cycle 232's restricted static review of the current immutable `qlib`
  pin: a no-baseline secret scan, Bandit SAST and a generated declared-
  dependency CycloneDX SBOM. Eighteen high-severity SAST signals and 23
  non-exact dependency declarations keep it `DEFER_REFERENCE_ONLY`; no
  dependency resolution, installer, candidate execution or adoption occurred.
- Verified Cycle 232 on exact merged main commit
  `0a22bf2110f5dcd0f542ebac1cb132333ecaa9b4` in CI run `33400243819`.
- Added Cycle 233's restricted static FinRL-Trading review. An undecodable
  tracked source file leaves SAST coverage incomplete, while all 25 production
  declarations remain unpinned; the candidate is `DEFER_REFERENCE_ONLY`.
- Added Cycle 234's restricted static NautilusTrader review. LGPL-3.0-or-later,
  untriaged medium SAST findings and no SCA/legal review keep it
  `DEFER_REFERENCE_ONLY`; no candidate code was executed.

## 2026-08-22

- Verified Cycle 221 on exact merged main commit
  `d91e7950d446093462e92dd172b039ff4fe14d5d` in CI run `32546680765`.
- Added Cycle 222's provider-independent verified external-session contract,
  fail-closed issuer/audience/time/MFA/group-to-role mapping, immutable mapping
  policies and durable allow/deny authorization-decision evidence. No token
  decoder, identity-provider connection, credential or production activation
  is included.
- Verified Cycle 222 on PR head in CI run `32547343608`: migration 0029, all
  408 tests without skips, 113-table restore, zero-error 40-file typed slice,
  hardened image/scan/attestations and every existing configured gate.
- Verified Cycle 222 on exact merged main commit
  `76eb731f5abdd183b116d3d808b78e290511fd0d` in CI run `32547868925`.
- Added Cycle 223's per-request dashboard CSP nonces. The proxy now binds one
  cryptographically random nonce to the request, framework rendering context and
  response policy, removes `unsafe-inline`, enables `strict-dynamic`, rotates
  the nonce on every response and protects authentication failures too.
- Verified Cycle 223 on PR head in CI run `32548653129`: all 411 tests without
  skips, 113-table restore, zero-error 40-file typed slice, production smoke,
  protected PostgreSQL Playwright/Axe and every configured security and
  supply-chain gate.
- Verified Cycle 223 on exact merged main commit
  `d728db7143934dabfb58488b9d46738deda57111` in CI run `32549205078`.
- Added Cycle 224's immutable chronological binary-model evaluation authority.
  A pre-approved policy permits only the immediate next complexity tier and
  gates leakage-safe holdout metrics, calibration, stability, economic value
  after cost and local-contribution reconciliation against a predecessor.
  Reports can reach only `BLOCKED` or `REVIEW_ELIGIBLE`; model execution,
  serving and automatic approval are absent.
- Verified Cycle 224 on PR head in CI run `32549927978`: migration 0030, all
  417 tests without skips, 116-table restore, zero-error 41-file typed slice
  and every configured security, supply-chain, container, frontend and browser
  gate.
- Verified Cycle 224 on exact merged main commit
  `7186d01a88d20b006c6903ee4f354feaef536423` in CI run `32550384273`.
- Added Cycle 225's immutable point-in-time internal research retrieval. Approved
  policies scope source kinds, roles, timestamps, minimum source diversity and
  query coverage; complete reports bind ranked citations into the existing
  structured agent request, while insufficient evidence cannot be bound.
- Verified Cycle 225 on PR head in CI run `32551034230`: migration 0031, all
  423 tests without skips, 120-table restore and zero-error 42-file typed slice.
- Verified Cycle 225 on exact merged main commit
  `f831791ccd6de18311aa5dc8bd6a494296d5acab` in CI run `32551428312`.
- Added Cycle 226's immutable retrieval-bound answer evaluation. Pre-approved
  policy thresholds require exact per-claim retrieved citations, lexical
  support, citation utilization, distinct sources, evidence-bounded confidence
  and missing-data disclosure; unsupported causal language fails closed.
- Verified corrected Cycle 226 PR head in CI run `32552094284`: migration 0032,
  all 429 tests without skips, 123-table restore, zero-error 43-file typed slice
  and every configured security, supply-chain, container, frontend and browser
  gate. Runs `32551944909` and `32552022891` exposed fixed-identity collisions
  between independently persisted retrieval fixtures; unique policy and request
  identities closed both test-isolation defects without changing production
  evaluation logic.
- Verified Cycle 226 on exact merged main commit
  `92dfb04e685e5cb5c5e8e2e0a0f543e9ca193792` in CI run `32552654160`.
- Added Cycle 227's immutable model explanation-sensitivity and degradation
  evidence. Exact Cycle 224 evaluation reports are evaluated under pre-approved
  probability-shift, confidence-degradation and eight-dimension drift thresholds;
  outcomes grant no execution, prediction, approval or risk authority.
- Verified Cycle 227 on PR head in CI run `32553281343`: migration 0033, all
  435 tests without skips, 127-table restore, zero-error 44-file typed slice and
  every configured security, supply-chain, container, frontend and browser gate.
- Verified Cycle 227 unchanged on exact merged main commit
  `8585e9c915274b79eeada1edd3879e982e30b729` in CI run `32553765217`.
- Added Cycle 228's immutable scheduled governance-report and operational-cost
  evidence. All 13 required report types are cadence-bound to exact successful
  operational-job evidence; cost governance covers all five budget modes, all
  12 required categories and review-only dataset/model value-for-cost evidence.
- Corrected hosted restart-read and shared-fixture ordering defects exposed by
  initial Cycle 228 run `32554874033`. Run `32554992009` then applied migration
  0034, passed all 444 no-skip tests, reconciled 134 restored tables, passed the
  zero-error 45-file typed slice and completed every downstream gate.
- Verified Cycle 228 unchanged on exact merged main commit
  `72617cb16bf9a371fea5b49ac5c1dfaf112c2e0e` in CI run `32555630655`.
- Added Cycle 229's immutable scheduled-agent-workflow governance. Exact job,
  retrieval and answer-evaluation hashes are assessed under purpose/role,
  cadence, workflow-count, token and estimated-cost limits. Scheduler, tool,
  model-invocation and action authority are all `NONE`; the strongest outcome
  is `READY_FOR_HUMAN_REVIEW`.
- Verified Cycle 229 on PR head in CI run `32556281237`: migration 0035, all
  451 tests without skips, 138-table restore, zero-error 46-file typed slice and
  every configured security, supply-chain, container, frontend and browser gate.
- Verified Cycle 229 unchanged on exact merged main commit
  `712720916d47fdede8d369ab9be4a5b53d06e34f` in CI run `32556866846`.
- Added Cycle 230's immutable point-in-time historical-analogue explanation
  evidence. Exact review-eligible model-evaluation, target and candidate hashes
  feed deterministic importance-weighted ranking, diversity and divergence
  gates. Model invocation, prediction and action authority remain `NONE`.
- Verified Cycle 230 on PR head in CI run `33337482205`: migration 0036, all
  458 tests without skips, 143-table restore, 117/117 mypy ratchet, zero-error
  47-file typed slice and every configured downstream gate.

## 2026-08-19

- Verified Cycle 211 on exact merged main commit
  `c19e32de5bb791cfb48e79c942d9221d6c64b9d9` in CI run `32284687492`.
- Added Cycle 212 policy-bound per-trade stop and conservative gap-loss
  calculations, fail-closed composed-runtime policy requirements and immutable
  SQLite/PostgreSQL assessment evidence. All operation remains simulated paper-only.
- Verified Cycle 212 on PR head in CI run `32286392247`: migration 0024, all
  364 tests without skips, 100-table restore and every configured workflow gate.
- Verified Cycle 212 on exact merged main commit
  `aa357de89985a62e22eec72df9def7d85f3bd211` in CI run `32287192826`.
- Added Cycle 213 atomic PostgreSQL risk-violation alerts, immutable alert
  transitions, portfolio-rejection reservation safety and concurrency/failure
  evidence. Live trading and external notification delivery remain disabled.
- Verified Cycle 213 on PR head in CI run `32288130114`: migration 0025, all
  367 tests without skips, 102-table restore and every configured workflow gate.
- Verified Cycle 213 on exact merged main commit
  `315da7dfc809dc6a3732f3ff5b0f24fda059c06e` in CI run `32288909541`.
- Added Cycle 214 immutable PostgreSQL simulated-paper execution-quality and
  shadow-rehearsal evidence, policy/content binding, atomic local divergence/
  threshold alerts and restart/rollback coverage. No network broker, live data
  or activation credit is introduced.
- Verified Cycle 214 on PR head in CI run `32290318303`: migration 0026, all
  372 tests without skips, 104-table restore and every configured workflow gate.
- Verified Cycle 214 on exact merged main commit
  `ba50671af94ab4c106f0666b8752a076eb2d0621` in CI run `32291280787`.
- Added Cycle 215 immutable PostgreSQL job policies/terminal runs, deterministic
  due/overdue monitoring, recovery transitions and a versioned local-only alert
  outbox. No scheduler, provider call or external delivery adapter is present.
- Verified Cycle 215 on PR head in CI run `32292245989`: migration 0027, all
  377 tests without skips, 108-table restore and every configured workflow gate.
- Verified Cycle 215 on exact merged main commit
  `067cfd44755f919d33ea5c6f5c5e6537f562f0f2` in CI run `32293009334`.
- Added Cycle 216 immutable retention-policy, manifest-only object-hash and
  point-in-time disposition evidence. The only terminal states are `RETAIN`
  and `ELIGIBLE_FOR_REVIEW`; no delete, object-store, network or credential
  authority exists.
- Verified Cycle 216 on PR head in CI run `32293960662`: migration 0028, all
  382 tests without skips, 111-table restore and every configured workflow gate.
- Verified Cycle 216 on exact merged main commit
  `c89a5de261a7dea00bb8372ca66b4658298f32cd` in CI run `32294785184`.
- Added Cycle 217 centralized API/dashboard security headers, production API
  documentation suppression, bearer challenges, full frontend dependency audit
  and automated Axe WCAG A/AA browser gates. Production identity remains open.
- Verified Cycle 217 on PR head in CI run `32295559967`: all 383 tests without
  skips, 111-table restore, 39-file typed slice and the twelve-scenario
  configured PostgreSQL browser matrix including zero Axe A/AA violations.
- Verified Cycle 217 on exact merged main commit
  `1f4352c7cea7cd36a33f29e142aff184ac305f47` in CI run `32296711621`.
- Added Cycle 218 deployment-assigned viewer, researcher, data-steward,
  risk-reviewer, auditor and operator roles with explicit endpoint permissions,
  viewer-by-default environment composition and fail-closed invalid roles.
- Verified Cycle 218 on PR head in CI run `32297293629`: all 388 tests without
  skips, unchanged 111-table restore, 39-file typed slice and every configured
  security, supply-chain, build and browser gate.
- Verified Cycle 218 on exact merged main commit
  `6db68e7cb23d5051ca1fe75617a62fdc73e0abc3` in CI run `32298149573`.
- Added Cycle 219's digest-pinned, dependency-locked research API container,
  explicit build-context allow-list and non-root UID/GID 10001 runtime. Hosted
  CI runs it read-only with all capabilities dropped, no-new-privileges and a
  bounded tmpfs, then verifies liveness/readiness and 401/200/403 role behavior.
- Verified Cycle 219 on PR head in CI run `32298849742`: all 393 tests without
  skips, unchanged 111-table restore, zero-error 39-file typed slice, the new
  hardened container runtime gate and every existing configured gate.
- Verified Cycle 219 on exact merged main commit
  `7710fbd4a9fad38f6c2a700f5695119c51434274` in CI run `32299780980`.
- Added Cycle 220's digest-pinned Trivy image scan without Docker-socket access,
  retained full JSON vulnerability inventory and CycloneDX 1.7 image SBOM, and
  fail-closed fixable HIGH/CRITICAL and EOL gates. Refreshed the Python base from
  3.12.11/Debian 12.12 to 3.12.14/Debian 12.15.
- Verified Cycle 220 on PR head in CI run `32300815517`: all 397 tests without
  skips, unchanged 111-table restore and every existing gate. Retained evidence
  has 124 SBOM components, 212 total findings, 26 vendor-unfixed HIGH/CRITICAL
  findings and zero fixable HIGH/CRITICAL findings.
- Verified Cycle 220 on exact merged main commit
  `a9ca933245512b7f207f84201691719025b79ff6` in CI run `32301746841`.
- Added Cycle 221's retained compressed research-image archive/checksum,
  Sigstore-signed SLSA provenance and CycloneDX SBOM attestations, signed bundle
  retention and independent GitHub CLI verification. Untrusted fork PRs receive
  no attestation authority and no registry publication occurs.
- Verified Cycle 221 on PR head in CI run `32302388728`: all 401 tests without
  skips, unchanged 111-table restore and every existing gate. The downloaded
  56,877,312-byte archive matches its checksum and both attestations re-verify.
- Verified Cycle 210 social/narrative intelligence on exact merged main commit
  `4606848e8fd1aae19c7dde85be4a067ecd5ea9b9` in CI run `32281500037`.
- Added Cycle 211 immutable reasoned signal lifecycle events, deterministic
  expiry processing, point-in-time protected signal reads and a read-only
  accessible Signal Explorer. Live trading and automatic authority remain off.
- Verified Cycle 211 on PR head in CI run `32283931250`: migration 0023, all
  358 tests without skips, 100-table restore and the complete protected browser,
  quality, security, supply-chain and production-build workflow.

## 2026-08-16

- Verified Cycle 205 multi-strategy portfolio construction on exact merged main
  commit `9bf966a7` in CI run `31922415005`.
- Added Cycle 206 PostgreSQL-native news/event research evidence with versioned
  source rights, immutable correction/retraction lineage, deterministic dedupe
  clusters, professional-instrument linking, event taxonomy, credibility and
  Data Health gates, point-in-time replacement, and confidence withdrawal.
- Kept providers unactivated and all news/event output research-only with no
  signal, order, OMS, broker, execution, or automatic risk-increase authority.
- Verified Cycle 206 on exact merged main commit `8b2b3a15` in CI run
  `31923127659`.
- Added Cycle 207 immutable correlated telemetry, dependency/business probes,
  candidate SLO/SLI evidence, actionable alert/incident lifecycles and measured
  failure drills without asserting production deployment or SLO attainment.

## 2026-07-30

- Created Phase 0 documentation from the authoritative specifications.
- Extracted and rendered both specifications for review.
- Created isolated sibling upstream-research directory and shallow-cloned the specified candidates without executing repository code.
- Added paper-only configuration, canonical domain contracts, deterministic risk checks and kill-switch controls with passing unit tests.
- Added duplicate-intent protection and verified paper-only source scanning.
- Added local FastAPI health/audit API, SQLite audit store, OpenAPI/API tests and CI verification workflow.
- Added fail-closed environment-backed local API authentication and rate limiting.
- Added canonical, venue-qualified instrument master persistence and tests.
- Added structured local observability metrics and event logging.
- Added point-in-time OHLCV provenance contracts, data-quality checks and deterministic batch-versioning.
- Replaced opaque bar persistence with typed columns and added point-in-time historical query gating.
- Added a provider-neutral historical-bar adapter contract and deterministic fixture adapter.
- Added versioned feature registry and leakage-safe cost-aware vectorized moving-average baseline.
- Added standardized performance reports and append-only versioned experiment tracking.
- Added deterministic paper-order lifecycle and partial-fill simulation.
- Added paper corporate-action treatment and reconciliation-to-risk blocking.
- Added deterministic paper funding, borrow-cost and margin-limit calculations.
- Added paper venue tick, lot, session and notional constraint validation.
- Added cross-engine equity, fill and position reconciliation reporting.
- Added transparent baseline breakout, momentum and mean-reversion strategies.
- Added chronological out-of-sample split generation and bounded inverse-risk allocation.
- Added portfolio exposure, concentration, and generalized stress-scenario calculations.
- Added explainable deterministic portfolio risk-policy decisions.
- Added protected paper-only portfolio risk-report API contract.
- Added responsive Next.js paper-only operator command-center scaffold.
- Added server-side dashboard risk proxy and HTTP smoke-test coverage.
- Expanded the dashboard information architecture and strengthened HTTP smoke assertions.
- Added lawful provider-neutral news/event metadata, source-confidence and license-gating contracts with point-in-time tests.
- Added transparent probabilistic regime estimates and bounded ensemble allocation with walk-forward invariance tests.
- Added append-only long-horizon investment theses, evidence-bound recommendations, non-executable rebalance plans and scenarios.
- Completed static reference-only upstream manifest and supply-chain triage evidence; no upstream runtime dependency was adopted.
- Added fixture-only paper-broker adapter contracts and deterministic execution-quality metrics; no vendor connection exists.
- Added local shadow divergence and failure-drill evidence contracts; no live-data comparison exists.
- Completed initial command, market, instrument, research, backtest, risk, investment and audit dashboard workspace coverage.
- Performed consolidated final verification, source safety scan and readiness-document refresh.
