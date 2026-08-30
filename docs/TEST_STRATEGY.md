# Test Strategy

Use unit, integration, contract, property, data-quality, backtest-regression, risk, security, failure-injection, restore and UI tests. Golden data and deterministic runs are required. Critical invariants: no order bypasses risk; expired signals and disabled strategies cannot create orders; duplicate intents cannot create exposure; stale data/reconciliation failures block risk increases; live trading remains impossible.

The CI suite provisions ephemeral PostgreSQL, applies Alembic migrations, and
runs migration/immutable-schema integration coverage when `POSTGRES_TEST_DSN`
is supplied. It also runs compile, deterministic unit tests, full-tree Ruff,
full-package Bandit, complete-package mypy debt ratchet, zero-error mypy for the
critical PostgreSQL slice, Python/frontend dependency audits, tracked source/
configuration secret detection, Python SBOM, frontend license inventory, frozen frontend
install, TypeScript, ESLint, production build and dashboard smoke. Local
machines without a running PostgreSQL service
skip that integration class explicitly; this is an external-environment gap,
not a passing production test.

The first executed CI evidence is GitHub Actions
[run 31721923194](https://github.com/omerasik/investing_tradin_platform/actions/runs/31721923194): PostgreSQL 16 was
provisioned, migrated and used by the integration/concurrency suite. Local
Docker remains unavailable, but that no longer leaves PostgreSQL test execution
unverified.

The PostgreSQL integration suite seeds normalized foreign keys and exercises
OMS/fill idempotency, daily-notional rejection, concurrent reservations and
validation-package rollback.
It is designed to be extended with concurrent reservation/restart/reconciliation
cases as each legacy repository moves to the adapter boundary.

The recovery job creates a custom-format backup, proves a truncated backup is
rejected, restores into a separately created database, compares revision plus
the critical table allow-list hashes/counts, reconstructs runtime state and releases a
durable recovery gate only after reconciliation.

Cycle 10 adds PostgreSQL tests for temporal symbol/provider resolution, overlap
constraints, delisting, ingestion-time visibility, US early-close/holiday/DST,
FX weekend boundaries, crypto 24x7 and restart. Fresh-restore comparison now
includes all seven professional instrument/calendar tables.

Cycle 11 adds real PostgreSQL coverage for immutable raw observations,
provider-ID resolution with separate event/knowledge times, OHLCV revisions,
quality rejection, dividend/split/symbol-change/delisting normalization, sealed
dataset hashing, default exclusion of latest-adjusted values, pre-seal PIT
invisibility and close/reopen reconstruction. Restore comparison includes all
five historical-data tables (**28 critical tables total**).

Cycle 12 locally covers every required detector and all seven action levels.
Real PostgreSQL coverage persists a blocking instrument assessment, proves both
a direct SQL validation and the repository validation fail closed, appends a
later clean assessment, validates successfully, rejects assessment mutation and
reconstructs the exact findings after restart. Restore hashing includes the two
data-health tables (**30 critical tables total**).

The Cycle 14 core adds real PostgreSQL tests for filing acceptance/ingestion
visibility, amended-revision selection, as-reported and standardized value
retention, immutable filing evidence and close/reopen reconstruction. Formula
unit tests cover all requested transparent fundamental metrics. Restore hashing
includes all three PIT fundamental tables (**33 critical tables total**).

The Cycle 15 core tests the controlled catalogue plus initial release,
ingestion delay, revision visibility, immutability and restart in PostgreSQL.
Restore hashing expands to **35 critical tables**.

Cycle 201 adds scorecard unit coverage for insufficient samples, no-trade and
zero-volatility availability, negative-return drawdown/Sortino, tail risk,
transparent complexity penalty, data-health blocking, deterministic identities
and labelled assumptions. The PostgreSQL fixture test creates a complete
immutable validation package, proves a scorecard is ineligible until linked,
rejects wrong dataset/feature/cost bindings and mutation, replays idempotently,
and reconstructs eligibility after restart. The restore allow-list includes
the scorecard, metric, component, Data Health link and package-link tables.

## Cycle 210 — Social/Narrative Intelligence Core

Cycle 210 adds deterministic coverage for rights and derived-use rejection,
raw-storage and geographic privacy limits, author hashing, untrusted-text
sanitization, all six required discussion classes, narrative clustering and
PIT windows. Metric tests cover mention/author acceleration, sentiment change,
disagreement, emotional intensity, influencer concentration, bot/spam/
coordination and pump risk, persistence, emergence and price divergence. Data
Health and high pump risk block evidence; database fields enforce research-only,
false standalone-trigger and false automatic authority.

Migration 0022 adds six immutable tables and expands the fresh-restore allow-list
to **97 critical tables**. The PostgreSQL test requires idempotent publication,
PIT visibility, restart reconstruction, immutable no-authority constraints and
unchanged OMS/strategy-activation counts. Local verification discovers **357
tests**, passes **323**, and skips the **34 PostgreSQL-only tests** explicitly
because no disposable local DSN exists. Corrected PR run `32280796788` applies
migration 0022, runs all **357 tests without skips**, matches all **97 restored
tables** and passes every configured quality, security, dependency, frontend,
build, smoke and browser gate. Initial run `32280620168` failed before tests on
an unsupported JSONB object-length function; the corrected exact-key constraint
uses supported containment/removal operators and is migration-verified.

## Cycle 211 — Signal Lifecycle Operations and Explorer

Cycle 211 adds unit coverage for mandatory actor/reason/evidence metadata,
monotonic lifecycle time, invalid/terminal transitions, deterministic expiry,
idempotent replay and rejection of expired signals by the risk adapter. The
PostgreSQL integration path exercises validation-to-lifecycle persistence,
restart reconstruction and expiry. Operator API tests keep the point-in-time
signal route authenticated, typed, GET-only and bounded; the configured browser
matrix adds a Signal Explorer scenario that verifies reasons while asserting no
execution or strategy-activation controls.

Migration 0023 adds one immutable lifecycle table. Runtime proposal, validation
and lifecycle authorities are now all included in the fresh-restore manifest,
raising it from 97 to **100 critical tables**. Local verification discovers
**358 tests**, passes **324**, and explicitly skips **34 PostgreSQL-only tests**.
PR-head run `32283931250` then verifies migration 0023, restart/immutability,
all **358 tests without skips**, all **100 restore-critical tables**, every
quality/security/supply-chain gate, the production build and all eleven
protected configured browser scenarios. Earlier runs `32283521769` and
`32283691569` exposed and closed, respectively, an over-specific expiry-batch
assertion and stale secret-baseline line metadata.

## Cycle 212 — Per-Trade Stop and Gap-Loss Enforcement

Cycle 212 adds deterministic tests for complete typed risk-policy decoding,
missing and wrong-side stops, intent/signal direction and entry-range mismatch,
maximum stop distance, loss at stop and conservative policy-buffered gap loss.
Coordinator and composed-runtime tests prove the calculations survive keyed
SQLite reopen, breached gap loss cannot reach paper submission and a runtime
cannot start with a legacy policy that omits the three-control set.

The configured PostgreSQL runtime test now asserts the same stop/gap figures in
the immutable assessment payload, and migration 0024 validates that payload's
shape. Local verification discovers **364 tests**, passes **330** and explicitly
skips **34 PostgreSQL-only tests**. Compileall, full Ruff, the tightened
**117/117** complete-package mypy ratchet and a zero-error **34-file** critical
slice pass. PR-head run `32286392247` then verifies migration 0024, all **364
tests without skips**, the unchanged **100-table** restore, PostgreSQL restart
and immutable calculations, every quality/security/supply-chain gate, the
production build and protected browser matrix.

## Cycle 213 — Atomic Risk-Violation Operations

Cycle 213 tests migration 0025, concurrency-safe active-alert deduplication,
immutable alert status events, restart recovery and authenticated actor
transitions. PostgreSQL integration proves a final portfolio rejection opens a
warning without consuming daily notional, a kill-switch rejection opens one
critical alert, simultaneous replay creates one decision/alert/open event, and
an injected alert-event failure rolls the entire risk-decision transaction back.

Local verification discovers **367 tests**, passes **330** and explicitly skips
**37 PostgreSQL-only tests**. Compileall, full Ruff, Bandit, the **117/117**
complete-package mypy ratchet and a zero-error changed-file slice pass. PR-head
run `32288130114` verifies all **367 tests without skips**, migration 0025,
**102 restore-critical tables**, the widened zero-error **35-file** critical
slice, every quality/security/supply-chain gate, production build and protected
browser matrix.

## Cycle 214 — Simulated Paper Operations Evidence

Cycle 214 adds deterministic unit coverage for terminal-state requirements,
exact OMS fill quantity/VWAP reconciliation, BUY/SELL directional slippage,
fill ratio, realized filled-quantity shortfall, intent-to-fill latency, policy
thresholds, numeric scale and mandatory simulated-only limitations. Shadow
rehearsals preserve divergence reasons and explicit non-activation status.

Migration 0026 adds two immutable tables and expands the fresh-restore manifest
to **104 critical tables**. Hosted PostgreSQL exercises policy/content hash
binding, idempotent replay, local threshold/divergence alerts, restart reads,
direct mutation rejection and injected alert-event failure rollback. Local
verification discovers **372 tests**, passes **334**, and explicitly skips **38
PostgreSQL-only tests** because no disposable local DSN exists. Corrected
PR-head run `32290318303` runs all **372 tests without skips**, applies migration
0026, matches all **104 restored tables**, and passes compileall, full Ruff,
Bandit, dependency/SBOM/secret gates, the **117/117** mypy ratchet, zero-error
**36-file** critical slice, frontend checks, production build, smoke and the
protected browser matrix. Initial run `32290146999` correctly retained a prior
Cycle 213 alert; only the over-exclusive new integration assertion failed and
was narrowed to coexist with valid earlier evidence.

## Cycle 215 — Durable Job Monitoring and Local Routing

Cycle 215 unit tests distinguish current, due and overdue schedules, prove a
successful run resets the deadline, reject future/naive/inverted run times and
enforce opaque local destination references. PostgreSQL coverage exercises
policy/run/route idempotency, point-in-time latest-policy selection, immutable
terminal evidence, overdue alert deduplication, local outbox enqueue, restart,
time-monotonic recovery and direct mutation rejection.

The failure-injection path rejects an outbox insert and proves the owning alert,
event and delivery all roll back. Migration 0027 adds four immutable tables and
expands restore verification to **108 critical tables**. Local verification
discovers **377 tests**, passes **338**, and explicitly skips **39 PostgreSQL-
only tests**. PR-head run `32292245989` runs all **377 tests without skips**,
applies migration 0027, matches all **108 restored tables**, and passes
compileall, full Ruff, Bandit, dependency/SBOM/secret gates, the **117/117**
mypy ratchet, zero-error **37-file** critical slice, frontend checks,
production build, smoke and the protected browser matrix.

## Cycle 216 — Non-destructive Retention and Object Evidence

Cycle 216 unit tests prove active windows, legal holds and disabled policies all
retain; an elapsed window produces only `ELIGIBLE_FOR_REVIEW`. Opaque catalog
references reject endpoints and path traversal, manifest evidence carries only
metadata/hash, policy identity and capture time, and mismatched policy/time
relationships fail closed.

PostgreSQL coverage proves policy and manifest idempotency, injected evaluation
rollback, conflicting replay rejection, restart reconstruction and immutable
delete rejection. Migration 0028 adds three immutable tables and schema-enforces
`REVIEW_ONLY_NO_DELETE`, `MANIFEST_ONLY`, and the two non-deletion disposition
states. Local verification discovers **382 tests**, passes **342**, and skips
**40 PostgreSQL-only tests**. PR-head run `32293960662` runs all **382 tests
without skips**, applies migration 0028, matches all **111 restored tables**, and
passes compileall, full Ruff, Bandit, dependency/SBOM/secret gates, the
**117/117** mypy ratchet, zero-error **38-file** critical slice, frontend checks,
production build, smoke and the protected browser matrix.

## Cycle 217 — HTTP Hardening and Automated Accessibility

Backend API tests require no-store, CSP, frame, MIME, referrer, permissions and
cross-origin headers on success and authentication failure, verify a bearer
challenge, prove local HSTS omission and production HSTS, and prove protected
deployments return 404 for interactive documentation and OpenAPI routes.

The Next production build applies corresponding dashboard headers. Playwright
asserts the header boundary and Axe scans the complete fail-closed and configured
PostgreSQL pages against WCAG 2.0/2.1 A and AA. Local verification discovers
**383 tests**, passes **343** and skips **40 PostgreSQL-only tests**; the built
fail-closed dashboard passes both browser checks with zero Axe violations.
PR-head run `32295559967` runs all **383 tests without skips**, matches all
**111 restored tables**, passes compileall, full Ruff, Bandit, full frontend and
Python dependency audits, SBOM/secret gates, the **117/117** mypy ratchet,
zero-error **39-file** critical slice, production build/smoke and all **12
configured browser scenarios**, including zero Axe WCAG A/AA violations.

## Cycle 218 — Least-Privilege Operator Permissions

Role tests require an explicit six-role matrix and prove that only `operator`
has the complete permission set. API checks distinguish authentication from
authorization: missing/invalid credentials return 401, authenticated identities
without the endpoint permission return 403, and absent/invalid deployment role
configuration returns 503. Viewer, researcher, data-steward, risk-reviewer and
auditor tests prove that read, research, data, risk/alert and audit command
boundaries cannot be crossed; actor attribution always comes from the verified
server-owned subject rather than request data.

Local verification discovers **388 tests**, passes **348** and skips **40
PostgreSQL-only tests**. PR-head run `32297293629` runs all **388 tests without
skips**, matches all **111 restored tables**, passes compileall, full Ruff,
Bandit, dependency/SBOM/secret gates, the **117/117** mypy ratchet, zero-error
**39-file** critical slice, production build/smoke and the complete configured
browser matrix.

## Cycle 219 — Hardened Non-Root Research API Container

Static contract tests require a deny-by-default Docker context, exact Python
base tag/digest, exact runtime dependency resolution, source-only copy,
credential-free layers, final UID/GID 10001, liveness probe and hardened CI
flags. Hosted runtime checks build the actual image, inspect its configured user,
start it read-only with all capabilities dropped, no-new-privileges and a
bounded no-exec tmpfs, then verify process UID, local-research/paper-only
readiness and anonymous/viewer read/viewer write HTTP results of 401/200/403.

Local verification discovers **393 tests**, passes **353** and skips **40
PostgreSQL-only tests**. PR-head run `32298849742` runs all **393 tests without
skips**, matches all **111 restored tables**, passes the **117/117** mypy
ratchet, zero-error **39-file** critical slice and every configured quality,
security, supply-chain, container, frontend and browser gate. This is not an
image-signing/provenance, container CVE, IaC, production deployment or load test.

## Cycle 220 — Container Vulnerability and Image SBOM Evidence

The scanner contract pins Trivy 0.73.0 by multi-architecture digest, forbids a
Docker-socket mount and requires non-root/read-only/capability-free/
no-new-privileges execution against a saved image archive. CI first writes the
complete unfiltered JSON report and CycloneDX SBOM, then rejects EOL images or
fixable HIGH/CRITICAL findings. The artifact step runs even after failure so the
diagnostic evidence is retained.

The first hosted scan correctly failed the stale Python 3.12.11/Debian 12.12
base. After refreshing to digest-pinned Python 3.12.14/Debian 12.15, PR-head run
`32300815517` passes all **397 tests without skips**, all **111 restore checks**
and every configured gate. The retained CycloneDX 1.7 SBOM contains **124
components**; the complete report has **212 findings**, **26 vendor-unfixed
HIGH/CRITICAL findings** and **zero fixable HIGH/CRITICAL findings**. Unfixed
findings remain explicit risk, not waived or relabelled as clean.

## Cycle 221 — Signed Image-Archive Attestations

Contract tests require OIDC/attestation permissions, two commit-pinned official
`actions/attest` invocations, a portable archive/checksum, separate SLSA and
CycloneDX signed bundles, independent GitHub CLI verification, no registry push
and no attestation authority for untrusted fork PRs.

PR-head run `32302388728` passes all **401 tests without skips**, all **111
restore checks** and every configured gate. The retained **56,877,312-byte**
archive matches its SHA-256 checksum after download; independent verification
returns one valid SLSA provenance result and one valid CycloneDX result. This is
file-artifact provenance, not registry-native OCI signing, release approval,
reproducible-build proof or deployment authority.

## Cycle 222 — Verified External Sessions and Authorization Evidence

Unit tests inject a verifier fixture and require exact HTTPS issuer/audience,
timezone-aware age/expiry, required authentication methods and exactly one
mapped server-owned role. Unmapped/ambiguous groups, stale/future sessions,
insecure issuers and tampered/future-approved policies fail closed. API tests
require a durable decision sink for external-session composition, distinguish
401/403 allow/deny evidence, prove an audit-write failure returns 503 and prove
raw tokens/session IDs never enter decision evidence.

PostgreSQL tests apply migration 0029, idempotently retain the immutable mapping
policy, append a policy-UUID-bound decision, reconstruct it after restart and
reject database mutation plus content-hash tampering. PR-head run `32547343608`
runs all **408 tests without skips**, restores/reconciles all **113 tables** at
revision `20260822_0029`, passes the **117/117** mypy ratchet, zero-error
**40-file** critical slice and every existing container, scan, attestation,
frontend and protected-browser gate. It does not test a real IdP or JWT library.

## Cycle 223 — Request-Nonce Dashboard CSP

Source contracts prevent the static Next.js configuration from restoring a
dashboard CSP or `unsafe-inline`, require proxy generation/propagation of one
request nonce, and require browser assertions for rendered-script binding and
nonce rotation. Production smoke verifies authenticated and unauthenticated
responses both receive strict nonce policies. The protected PostgreSQL
Playwright suite checks every rendered script through the reflected nonce
property, proves a second response rotates the nonce, retains the complete
Command Center matrix and reruns Axe WCAG A/AA. PR-head run `32548653129` runs
all **411 tests without skips**, restores/reconciles all **113 tables**, passes
the **117/117** mypy ratchet and zero-error **40-file** slice, and completes
every configured security, supply-chain, build, smoke and browser gate.

## Cycle 224 — Immutable Chronological Model Evaluation

Unit tests verify immediate-successor complexity progression, policy approval
before training, chronological non-overlapping holdouts, minimum sample and
two-class requirements, stable explanation feature sets, local-contribution
reconciliation and observation/policy content hashes. They independently check
the complete metric report and both fail-closed outcomes, including regression,
calibration and holdout-half stability failures.

PostgreSQL tests apply migration 0030, retain policy/report/observation rows,
prove idempotent replay and exact reconstruction after restart, append a
review-eligible validation to the separate model registry, and reject direct
database mutation. The restore drill reconciles all three new authorities.
PR-head run `32549927978` runs all **417 tests without skips**, restores and
reconciles all **116 tables**, passes the **117/117** mypy ratchet and zero-error
**41-file** critical slice, and completes every configured security,
supply-chain, container, production-build, smoke and protected-browser gate.

## Cycle 225 — Point-in-Time Internal Research Retrieval

Unit tests prove policy-time, source-kind, instrument, role, availability and
invalidation filtering; deterministic ranking; minimum result/source-diversity/
coverage insufficiency; hash tamper rejection; and one-way binding of complete
citations into the existing non-executing structured-agent request. PostgreSQL
tests prove idempotent policy/chunk/report writes, exact restart reconstruction
and database-level immutability. PR-head run `32551034230` runs all **423 tests
without skips**, restores/reconciles all **120 tables**, passes the **117/117**
mypy ratchet and zero-error **42-file** slice, and completes every configured
gate.

## Cycle 226 — Retrieval-Bound Agent Answer Evaluation

Unit tests require exact claim-to-retrieval bindings, context and approval-time
alignment, per-claim lexical support, citation utilization/diversity,
retrieval-bounded confidence and missing-data disclosure for partial query
coverage. Unsupported causal language and overconfidence produce `BLOCKED`;
supported fixture answers can reach only `REVIEW_ELIGIBLE`.

Migration 0032 adds immutable policy, report and claim-evaluation tables with a
composite policy-ID/content-hash foreign key and retrieval-report binding. The
PostgreSQL test proves idempotent writes, exact reconstruction after restart and
database-level mutation rejection. Two initial hosted runs exposed shared fixed
policy/request IDs across independently persisted fixtures; unique identities
closed those isolation defects. Corrected PR-head run `32552094284` runs all
**429 tests without skips**, restores/reconciles all **123 tables**, passes the
**117/117** mypy ratchet and zero-error **43-file** slice, and completes every
configured downstream gate.

## Cycle 227 — Model Sensitivity and Degradation Evidence

Unit tests require the exact untampered Cycle 224 report, complete feature
sensitivity coverage, all eight specified drift dimensions, policy approval
before evaluation, matching model identity and bounded observation time. They
verify deterministic evidence ordering, probability/confidence breaches,
dimension-specific degradation and the non-authoritative no-breach outcome.

Migration 0033 adds immutable policy, report, sensitivity-scenario and
degradation-observation tables. Composite foreign keys bind exact policy and
Cycle 224 evaluation content hashes. PostgreSQL coverage proves idempotent
replay, exact policy/report/child reconstruction after restart and direct
report/child mutation rejection. PR-head run `32553281343` runs all **435 tests
without skips**, restores/reconciles all **127 tables**, passes the **117/117**
mypy ratchet and zero-error **44-file** slice, and completes every configured
downstream gate.

## Cycle 228 — Scheduled Reporting and Cost Governance Evidence

Unit tests enumerate all 13 required report types, five cadences, five budget
modes and 12 cost categories. They reject cadence/job-interval mismatch,
tampered or failed job evidence, early report generation, missing fact/category
evidence and undisclosed gaps. They prove deterministic ordering, category and
total budget breaches, review-only live-named modes, value-to-cost thresholds
and deterministic-code preference over proposed AI inference.

Migration 0034 adds immutable schedule policy, cost policy, cost observation,
value assessment, report, section and report-cost link tables. Composite foreign
keys bind exact operational-job policy/run and budget-policy hashes. PostgreSQL
coverage proves idempotent replay, exact restart reconstruction, store-side
re-derivation and direct report/section/value mutation rejection. Initial run
`32554874033` exposed a connection-read defect and shared job-fixture ordering
collision; both were corrected. Run `32554992009` runs all **444 tests without
skips**, restores/reconciles all **134 tables**, passes the **117/117** mypy
ratchet and zero-error **45-file** slice, and completes every downstream gate.

## Cycle 229 — Scheduled Agent Workflow Governance Evidence

Unit tests require exact untampered governance, schedule, operational-job,
retrieval and answer-evaluation evidence. They cover disabled policies, failed
jobs, empty evidence, cross-report binding mismatches, over-frequent schedules,
per-workflow and aggregate token limits, estimated-cost limits, deterministic
ordering and the explicit human-review-only outcome.

Migration 0035 adds four immutable governance/schedule/assessment/candidate
tables and exact composite job, retrieval and answer-report hash foreign keys.
PostgreSQL coverage proves idempotent writes, store-side re-derivation from
registered evidence, restart reconstruction and direct mutation/deletion
rejection. PR-head run `32556281237` runs all **451 tests without skips**,
restores/reconciles all **138 tables**, passes the **117/117** mypy ratchet and
zero-error **46-file** slice, and completes every configured downstream gate.

## Cycle 230 — Historical Analogue Explanation Evidence

Six unit tests cover exact evaluation/version/feature binding, point-in-time
candidate and outcome cutoffs, deterministic weighted distance and ranking,
below-threshold member retention, source/regime diversity, divergence,
disabled/insufficient outcomes, non-finite/tampered evidence and input-order
invariance. One PostgreSQL test covers migration 0036, idempotent replay,
store-side re-derivation, restart reconstruction and immutable report/candidate
enforcement. The five new evidence tables are part of the restore allow-list and
the module is part of the zero-error strict mypy slice. Hosted proof is pending.
