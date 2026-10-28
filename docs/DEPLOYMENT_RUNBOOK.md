# Deployment Runbook

## Research Terminal Lite

### First offline BTC study (P0.4C.2A)

The browser-facing Lite API remains SELECT-only and rejects every mutation.
Research is a separate, explicitly invoked local process using the existing
`lite_owner` writer; no command worker or execution authority is enabled:

```powershell
.venv\Scripts\python scripts/terminal_lite_research.py run
# Repeat the same command only to resume this study or verify its completed no-op.
# With the existing Lite API/dashboard running:
node web/scripts/smoke-terminal-lite.mjs
```

This bounded launcher currently requires Windows. It exposes no alternate DSN,
family, dataset, retry or execution arguments. It strips inherited platform/DB
settings, admits only the independently capped Lite container on loopback:5441,
re-proves the active BTC Aug01..14 dataset and canonical study declaration, then
checks the actual PostgreSQL database/user/version/address/schema, reader grants,
cluster pin, existing study identity and trial definitions before writing. It
never opens the original research database. Another study or changed dataset,
canonical SDK identity, cluster, grants or resource limits causes refusal.

The unchanged `strategy_lab.py` declaration and canonical evaluator/ledger run
the complete **84-point breakout_channel** space: **64 EVALUATED and 20
INADMISSIBLE_PARAMETERS**, zero failed/pending trials in the measured run. All
84 results retain their original ordinals and multiple-testing denominator.
The existing worker runs in the supervised process rather than spawning a pool;
other CLI/API callers keep their previous behavior. No family/domain, evaluator,
numeric/financial convention, schema, cost model or lag policy is changed.

Study `020fe0da-b880-54fb-be1b-52cee81bbd7f` binds exactly BTC dataset
`e5942fd6-42f4-5ad2-8b34-09bae2996db6`. The persisted complete manifest hash is
`67e4c918caa21cebab952f94d8829de0b164e856616b6bd6763c9b18f201b795`.
The second complete invocation preserves all study/trial/result/event/manifest
counts and the manifest hash: it performs no new evaluations. Receipts and
negative/stop logs remain outside Git under `terminal-lite/research-pilot/`.
Results and exact metric text are read from PostgreSQL through the existing
Strategy Lab list, detail, pagination, outcome filters and trial readers.
`/strategy-lab?study=020fe0da-b880-54fb-be1b-52cee81bbd7f` opens the study.

**These are SEARCH_NON_AUTHORITATIVE, gross, development-only results on a
short 14-day sample, with the existing 2-second declared research lag. Trading
costs are unverified and profitability is not validated.** No candidates are
frozen, no Decimal/validation/walk-forward/holdout result is generated, and
those unsupported results remain unavailable. Near ties retain the canonical
search diagnostics; no authority is inferred. No fees/spread/slippage/funding
amounts or execution trades are invented. Research action endpoints, paper,
brokers and live execution stay disabled.

The existing 16 MiB evidence ceiling is unchanged. The offline process has one
worker, one logical core, one numeric/Arrow I/O thread, 512 MiB process commit
and working-set caps, and a 120-second external deadline. The local host guards
retain a 24 GiB disk floor, 512 MiB physical-RAM reserve (1 GiB at admission)
and 1 GiB commit headroom; Lite database size is refused above 64 MiB. A hard
stop retains already committed queue/results and negative receipts, without
automatic retry or fabricated completion. PostgreSQL's existing 256 MiB/0.5 CPU,
connection and chart/query limits remain unchanged.

Measured complete study: 26.01 s worker wall / 24.73 s CPU, 127.8 MiB peak RSS /
145.0 MiB peak commit. Database grew from 20,347,927 to 20,823,063 bytes (about
19.86 MiB total). No source frames, previous subset or original research DB
were changed. The initial pre-write check refused PostgreSQL's CIDR address
text versus Docker's host text; comparing `host(inet_server_addr())` resolved
the representation mismatch without changing the expected server identity.
That stopped attempt registered zero studies and remains in private receipts.

### Verified multi-day window (P0.4C.1)

The explicitly selected common window is **2026-08-01 through 2026-08-14 UTC**:
**20,160 minute bars per market / 60,480 total**. The later 2026-08-05..18
candidate was refused because SOL's retained 2026-08-15 frame has 1,439 rows
and no 21:56 UTC bar. No missing minute is filled or carried forward, and
there is no automatic date-window substitution. The selected dates are before
the untouched 2026-08-20 holdout boundary.

On the already prepared Lite host, this procedure performs no database migration:

```powershell
.venv\Scripts\python scripts/terminal_lite.py prepare-window --from 2026-08-01 --to 2026-08-14
.venv\Scripts\python scripts/terminal_lite.py verify --subset subsets/2026-08-01_2026-08-14
.venv\Scripts\python scripts/terminal_lite.py activate --subset subsets/2026-08-01_2026-08-14
.venv\Scripts\python scripts/terminal_lite.py serve
# From web/, in another shell:
node scripts/smoke-terminal-lite.mjs
```

`prepare-window` requires explicit dates and 7..14 days. It reads only retained
archive/day manifests and Parquet, reuses the canonical
`build_research_bar_dataset_v1(require_continuous=True)`, and verifies every day
and the complete window's exact Decimal row hash. A complete UTC minute grid
and NULL market knowledge time are required. Startup re-proves the retained
day frames and their exact concatenation before connecting to the Lite reader.
Missing/corrupt days or objects, extra catalogue records, changed identities or
bindings, incomplete minute grids, mismatched dates, excessive size and holdout
overlap are refused. The chart ceiling stays **NO_TIMING_AUTHORITY**.

No dataset format or evaluator changes: the existing `terminal-lite-v1`
sidecar accepts the legacy `utc_day`, or explicit `first_utc_day`, `last_utc_day`,
`day_count` and original day-frame bindings. Actual dataset UUID/content hashes
come from the canonical research-window builder, and differ from the one-day
and original long-window datasets. Source metadata/hashes remain intact; raw
archives are not downloaded or copied.

The new root is fully verified in staging, then atomically published under
`~/.trade_platform/terminal-lite/subsets/2026-08-01_2026-08-14`. Existing roots
are never overwritten. `activate` verifies both roots and atomically writes
`active-subset.local.json`, pinning content hashes and recording the previous
root. Stop the serving launcher before switching, then restart: a running API
keeps its startup root. The prior `evidence/` directory remains unchanged and
is still the default when no selector exists. To recover it:

```powershell
# Stop the Lite API/dashboard launcher first (Ctrl+C).
.venv\Scripts\python scripts/terminal_lite.py activate --subset evidence
.venv\Scripts\python scripts/terminal_lite.py serve
```

If the currently selected root/selector is corrupted, add `--recover` to the
explicit `activate --subset evidence` command. This bypasses only admission of
the broken current selection; the requested recovery root must still fully
verify. There is no automatic substitution of a different historical window.

Measured preparation on 2026-10-28: **10,354,390 bytes / about 9.87 MiB** total
evidence, including retained day frames and canonical window objects; the
**16 MiB ceiling is unchanged**. Preparation took 63.91 s wall / 23.08 s CPU,
with 93,425,664 bytes peak RSS (about 89.1 MiB), admitted under one logical CPU,
one numeric/Arrow I/O thread, a 512 MiB process limit and a 120 s external wall
deadline. Windows uses the existing Job Object helper; POSIX uses an address-space
limit and requires CPU-affinity support. Actual preparation was measured on
Windows; the POSIX path is not a hosted-resource validation. Preflight found
about 5.3 GiB available RAM and 24.2 GiB free disk.
The previous root remains separate at about 0.4 MiB. PostgreSQL and chart limits
below remain unchanged; native application RSS is still measured, not OS-capped.
The 11-visit multi-day browser smoke measured API peak RSS about 169 MiB and
Next.js development server peak RSS about 718 MiB, within the existing roughly
2.5 GiB local application allowance plus Docker/OS overhead. Authentication,
all four timeframes, minute paging, provenance identities and unavailable
study/comparison states passed; no study results were generated.
Full dataset identities and measured preparation resources are retained outside
Git in `terminal-lite/preparation-2026-08-01_2026-08-14.local.json`.

`verify` runs portable isolation/provenance/chart checks and a separate real-data
acceptance harness, each with a 120 s subprocess deadline. It covers legacy
compatibility, exact multi-day identities and aggregation, all timeframes,
minute paging, corruption/missing-day/mismatch/holdout refusal and rollback.
The bounded browser smoke checks three markets, all four timeframes, an earlier
minute page, provenance identities/navigation, authentication and refusal states.
Its range and screenshots stay outside Git under
`terminal-lite/browser-smoke/<first>_<last>/`. No retained data or settings ship
in Git. Window preparation itself generates no study/trial, trades, performance
or comparison results. Only the separately invoked BTC pilot above adds genuine
search results; unsupported evidence and execution restrictions remain intact.

### Original one-day bootstrap and baseline

P0.4A provides a local read-only demonstration through the existing FastAPI,
Next.js, chart, Strategy Lab, terminal and research data-plane readers. It does
not query, dump, copy, seed, reset or migrate `trade_platform_research` or its
`0062` volume. `compose.lite.yml` owns only project `trade-terminal-lite`, database
`trade_terminal_lite`, PostgreSQL 16 and volume `trade-terminal-lite_lite_pgdata`.
The local database port is 5441; API and dashboard use 8011 and 3011 on loopback.

### Reproduce locally

Install the repository's Python dev and analytics dependencies and the existing
web dependencies. Docker Desktop must be running. Existing retained archive
day records/manifests and Parquet objects must be present; preparation does not
download anything. The default sources are `~/.trade_platform/public-archive`
and `~/.trade_platform/research-data`; explicit `--archive-root` and
`--source-root` overrides are available. Neither source is modified.

```powershell
.venv\Scripts\python scripts/terminal_lite.py prepare
.venv\Scripts\python scripts/terminal_lite.py verify
.venv\Scripts\python scripts/terminal_lite.py serve
# In another shell, from web/:
node scripts/smoke-terminal-lite.mjs
```

Open `http://127.0.0.1:3011/terminal`. Obtain `dashboard_password` from
`~/.trade_platform/terminal-lite/settings.local.json`; do not paste this file
into logs or source control. Operator, database, dashboard and session secrets
are independently generated. Ctrl+C stops this launcher's API/dashboard child
processes. `python scripts/terminal_lite.py stop` stops only the Lite PostgreSQL
service and preserves its disposable volume. Restart with `prepare` then
`serve`. Preparation re-verifies the existing subset and applies canonical
migrations only to its constructed Lite target. It strips inherited research,
test and dashboard configuration so another DSN cannot take precedence.

The smallest useful existing source granularity is a verified archive day.
The latest common retained day before the untouched holdout is **2026-08-18 UTC**.
Each market has 1,440 one-minute bars: 4,320 total. These are real historical
Bybit public archive event-time values, with **NO_TIMING_AUTHORITY** as the chart
claim ceiling. This is not a verified publication-lag, performance or profit
claim. Each original day frame is byte- and logical-hash verified; its original
archive file identity, retrieval metadata and retention record are retained.
Raw archives previously evicted under the existing retention policy are not
downloaded or re-derived. The supported `build_research_bar_dataset_v1` path
creates the one-day windows. Their Decimal row hashes must equal the source day
hashes. New window identities describe the actual subset; parent identities
are never relabelled as smaller datasets. Provenance is in
`~/.trade_platform/terminal-lite/evidence/lite-provenance.json`.

Bars remain in the content-addressed filesystem data plane; they are not
duplicated into SQL price rows. The PostgreSQL control-plane catalog is fresh.
There is no supported standalone import of the available walk-forward exports
into the complete study/trial/frozen-candidate ledger. The original full-window
study identities cannot be attached to one-day windows. Accordingly there are
zero loaded studies, trials, trades or performance results. Strategy Lab and
comparison navigation retain explicit unavailable/empty states. No synthetic
fixture is loaded. Creating new results or copying the original SQL ledger is
outside this Lite delivery.

The dedicated `lite_reader` login has SELECT grants, no schema/database CREATE,
and default read-only transactions with a 5-second statement timeout. The Lite
factory refuses another database/login, PostgreSQL major version, mismatched
subset or schema revision (v1 pins current head `20261020_0067`). It has no
command ledger, paper OMS, worker, capture root, price-source policy or broker.
Every non-GET/HEAD/OPTIONS API request is refused. The existing legacy generic
`/health/ready` reports platform build capabilities, including paper capability;
use **`/health/lite-ready`** for actual Lite readiness and disabled execution
flags. Readiness uses the canonical query lock and a database read. Browser
authentication uses the existing signed HttpOnly/SameSite session and server-only
API bearer token. Local mode is not a claim of production OIDC/RBAC readiness.

### Resource envelope and validation

Measured on the Windows host during one bounded eight-visit browser smoke on
2026-10-27, without a full historical import or repository benchmark:

| Component | Observed | Limit / planning allowance |
| --- | --- | --- |
| Evidence directory | 396,688 bytes before provenance report; under 0.4 MiB total | 16 MiB preparation/startup ceiling; at most 1,440 rows/market |
| Fresh canonical SQL database | 20,347,927 bytes; PostgreSQL data directory about 75 MiB | Independent volume; plan 1 GiB hosted storage |
| PostgreSQL container | about 42–47 MiB working memory | Enforced 256 MiB, 0.5 CPU, 12 connections, 32 MiB shared buffers, 2 MiB work memory, no parallel workers |
| API process | about 126 MiB resident, 131 MiB observed peak on final smoke | One worker, concurrency 32; plan 512 MiB container limit |
| Next.js development server | about 490 MiB resident; 1,414 MiB observed peak, plus about 68 MiB launcher peak | 512 MiB JS heap setting is **not** an RSS ceiling; allow 2 GiB for local dev |
| Chart queries | one-day scans, 24 hourly candles per market | One DuckDB thread, 128 MB memory limit, no disk spill; existing chart point/page limits |

Allow roughly 2.5 GiB for local application processes and PostgreSQL, plus the
Docker/OS overhead. API and Next.js native processes have measured working sets,
not OS-enforced RSS caps. Hosted container limits must be configured separately.
Production Next.js runtime/build memory is **not yet measured**; keep the 2 GiB
dashboard allowance until an approved isolated deployment proves a smaller cap.
Do not infer hosted load capacity or production build requirements from this
single-user development smoke.

`verify` runs only the Lite isolation/integration tests and existing chart tests
against the dedicated database. Portable checks stay in normal CI discovery;
the explicit `tests.terminal_lite_acceptance` harness requires local evidence
and fails on missing inputs rather than introducing CI skips. It checks authentication/refusal, real subset
candle counts, exact Decimal aggregation, canonical schema, empty evidence and
missing-object responses. The browser smoke checks actual canvases for all three
markets, login failure/success/logout, secret absence in HTML, evidence-to-chart
links, Strategy Lab, comparison and mutation refusal. Screenshots and its bounded
result are written outside the repository under `terminal-lite/browser-smoke`.

### Independent Railway deployment path — pending owner approval

No Railway project, service, deployment, domain, volume or data transfer was
created. Read-only account discovery returned one existing project; it was not
selected as a target. SocialOS and all existing services remain untouched.
Before provisioning, the owner must approve a **new project**, its spend/resource
cap and transfer of the specific Lite evidence directory. The following is a
reproducible configuration procedure, not evidence of a deployed service.

After approval, record the new project/environment/service IDs and use them
explicitly for all operations. Do not use a previously linked project or bare
`railway up`. Use three independent services, one replica each:

| Service | Source / configuration | Initial limit |
| --- | --- | --- |
| PostgreSQL | `postgres:16-alpine`; fresh volume; `trade_terminal_lite` / `lite_owner`; private network | 256 MiB / 0.5 CPU / 1 GiB volume |
| API | Repository root; `Dockerfile.lite`; `PORT=8000`; `/health/lite-ready` | 512 MiB |
| Dashboard | `/web`; existing Dockerfile; `PORT=3000`; `/login` | 2 GiB until measured |

`Dockerfile.lite.dockerignore` independently permits only the Lite image recipe,
locked requirements and source tree. The canonical API build-context allowlist
is unchanged; settings, local evidence and archives stay outside both images.

Bootstrap only the new database through an authenticated SSH tunnel to
`127.0.0.1:5541`, using `scripts.terminal_lite.initialize_database(dsn,
reader_password)`. This helper checks the dedicated target and PostgreSQL major
version before applying canonical migrations and SELECT-only reader grants.
Never copy/restore the original database. Supply only the new reader URL as
`LITE_READ_DSN` to the API, plus a new operator token, `TRADE_PLATFORM_LITE=1`
and `LITE_RESEARCH_ROOT=/data/evidence`. Upload only the approved evidence subset
to an independent volume with read access for UID/GID 10001; preserve and
re-verify all manifests and objects. Do not upload settings, full archives,
fixtures or the original data plane.

Set `TRADE_PLATFORM_LITE=1` on the dashboard, independent dashboard/session
secrets, the API bearer token and the approved HTTPS dashboard origin. Use the
API's authenticated **HTTPS** service domain for `TRADE_PLATFORM_API_BASE_URL`:
the existing validator rejects HTTP internal hostnames. Keep credentials at
runtime, and workers/capture/cron/brokers absent. Build/scan both images, observe
terminal deployment SUCCESS, then run the bounded authenticated chart smoke and
measure hosted resources before accepting the environment. No image build,
transfer or hosted validation is part of this integration.

Current readiness: **local Lite working; hosted deployment conditional** on
owner approval, fresh-service bootstrap/evidence upload, image build/security
validation and a bounded production-mode hosted smoke. No paid or hosted
validation was attempted, and no further research phase is started.

References checked: [Railway monorepos](https://docs.railway.com/deployments/monorepo),
[healthcheck ports](https://docs.railway.com/deployments/healthchecks),
[SSH transfers and local forwarding](https://docs.railway.com/cli/ssh),
[volume ownership/replica constraints](https://docs.railway.com/volumes/reference),
and [legacy config-as-code deprecation](https://docs.railway.com/config-as-code/reference).

---

Development is local and research-only. Future environments are development, test, paper, then production, with signed/versioned artifacts, migration checks, least-privilege secret injection, staged rollout, rollback and backup/restore evidence. No deployment may include live execution without signed readiness approval.

The repository `Dockerfile` is a narrow research API artifact, not the paper or
production deployment recipe. It pins Python 3.12.14 by tag and digest, installs
the exact `requirements-runtime.txt` resolution, copies only `src/`, and runs as
UID/GID 10001. Build it from the repository root and inject the temporary bearer
token/role only at runtime. The verified hardened invocation uses a read-only
root filesystem, `--cap-drop=ALL`, `no-new-privileges` and a bounded no-exec
`/tmp` tmpfs; `/health/live` and `/health/ready` must pass before use. CI also
requires readiness to state `local_research`, paper enabled and live disabled.
Do not relabel this SQLite-backed artifact as a PostgreSQL environment.

CI scans the built archive with digest-pinned Trivy without mounting the Docker
socket. It retains the complete JSON inventory and CycloneDX image SBOM, rejects
an EOL distribution and fails on fixable HIGH/CRITICAL findings. Do not describe
a passing gate as vulnerability-free: vendor-unfixed findings remain in the
retained report and require review on every base/scanner/database update.

CI also compresses the exact scanned image, records a portable SHA-256 checksum,
and uses commit-pinned GitHub `actions/attest` with short-lived OIDC/Sigstore
credentials to create SLSA provenance and CycloneDX SBOM attestations. It
retains both signed bundles and verifies both predicates with `gh attestation
verify`. Untrusted fork PRs receive no attestation authority. A consumer must
download the archive and checksum together, verify the checksum, then verify
both attestations against `omerasik/investing_tradin_platform` before use.

Before any staging/production use, add registry-native OCI signing/publication,
release approval and registry retention/access controls, IaC, network/TLS and resource policy,
PostgreSQL migration/restore validation, rollback and soak evidence. A mutable
tag, unsigned image, missing evidence or failed scan is not deployable.

For a PostgreSQL environment, inject the DSN through the deployment secret
manager, set `persistence_target=postgres`, and run `alembic upgrade head`
before application startup. `paper` and `production` configuration reject
SQLite. Perform the SQLite backfill dry-run and resolve every identity conflict
before any mapped write. Downgrades are permitted only for disposable schema
tests; production migrations are forward-only. A failed migration, unavailable
database, stale reconciliation or incomplete restore must leave risk increase
and paper submission fail-closed.

Serve the API and dashboard only behind an HTTPS terminator. Paper/production
API deployments suppress `/docs`, `/redoc` and `/openapi.json`; do not re-enable
them on an Internet-facing process. Verify CSP, HSTS, frame denial, MIME,
referrer, permissions, opener/resource and no-store headers after every proxy or
CDN change, because an intermediary can remove or replace them. The dashboard
CSP must contain request-unique `script-src` and `style-src` nonces, must retain
`strict-dynamic`, and must not contain `unsafe-inline`. Confirm every rendered
script nonce matches that response's policy and that a second request receives
a different nonce. Never cache, normalize, combine or statically replace this
request-bound policy at a proxy/CDN. Response headers do not provide OIDC, MFA
or RBAC.

For the temporary bearer boundary, inject `TRADE_PLATFORM_OPERATOR_TOKEN` only
through the deployment secret manager, set a non-blank
`TRADE_PLATFORM_OPERATOR_SUBJECT`, and assign exactly one
`TRADE_PLATFORM_OPERATOR_ROLE`. Omission defaults to `viewer`; an unrecognized
role fails closed with service unavailable. Use the narrowest role:

- `viewer`: authenticated evidence reads only;
- `researcher`: reads plus strategy creation and research backtest launch;
- `data_steward`: reads plus fundamental materialization and ingestion cadence;
- `risk_reviewer`: reads plus portfolio-risk evaluation and alert acknowledgement;
- `auditor`: reads plus append-only audit-event creation;
- `operator`: all currently defined permissions.

Roles are deployment-owned and never supplied by a browser/API request. Do not
represent this static one-token mapping as OIDC, MFA, production sessions or a
managed identity directory; replace it at that boundary before production.

For an external identity deployment, supply a verifier that performs signature,
trusted-key, algorithm and issuer validation before constructing a
`VerifiedExternalSession`. Register an approved immutable mapping policy with
an exact HTTPS issuer, audience, required authentication methods, maximum age
and allowlisted group-to-role mapping. Compose `ExternalSessionAuthenticator`
only with the PostgreSQL `PostgresIdentitySecurityStore` as the authorization
decision sink; startup rejects a missing/non-durable sink, and an audit write
failure blocks the request. Validate IdP key rotation, token revocation/session
termination, clock skew, group lifecycle and break-glass procedures in staging.
Never pass an unverified decoded JWT payload, client-selected role, raw token or
raw provider session ID into these contracts. This runbook does not select or
activate an IdP.

After any restore, run `scripts/verify_postgres_restore.py --source-dsn ...
--restored-dsn ...` against a separately created database. Do not release the
append-only recovery gate unless revision, critical hashes/counts, validation
manifests, OMS reconstruction, broker cursor, risk reservation, kill switch,
promotion and reconciliation checks all pass. A corrupt or incomplete dump is
a failed deployment.

Operational job policies and terminal run evidence do not, by themselves, authorize
a scheduler to run arbitrary code. Module 3E's deployment-owned scheduler
(`trade_platform.scheduler`, served by `trade_platform.worker_app`) is exactly the
"deployment-owned scheduler" this paragraph anticipated: it invokes only the
separately approved job entry points named in `scheduler.default_job_registry()`
(currently `operational_job_monitor`, `postgres_dependency_probe`, and
`retention_evaluation_sweep` — see `docs/MODULE_3E_STAGING_DEPLOYMENT_AND_SCHEDULER.md`),
and every execution appends terminal evidence with a unique idempotency key via the
existing `PostgresOperationalJobStore.append_run`. A due job policy whose name has no
registered runner is still monitored for overdue alerts but is never executed.
Register job policies for a new environment with
`scripts/seed_operational_job_policies.py --postgres-dsn ... --approved-by ...`
(idempotent when re-run with the same `--version`/`--approved-at`) before starting
the worker.

For a Docker Compose staging deployment, see `docker-compose.staging.yml` and
`docs/MODULE_3E_STAGING_DEPLOYMENT_AND_SCHEDULER.md`: it runs `postgres`, a one-shot
`migrate` step (a separate, disposable image — `Dockerfile.migrate` — that does not
share the hardened research-API image's dependency set), then `api`, `worker`, and
the `web` dashboard as four independently built and deployed services. Copy
`.env.staging.example` to `.env`, fill in real secrets, and run
`docker compose -f docker-compose.staging.yml up -d --build`. A successful staging
deployment is not, and must never be represented as, live-trading readiness.

## First BTC/ETH/SOL paper-incubation operator procedure

This is the canonical ordered handoff for M-TERMINAL-V1. Stop at an unmet
prerequisite; a successful API read or a queued command is not acceptance.
The sprint authorizes neither holdout opening nor paper start. The future write
steps below require separate owner authorization. Reuse the protected activation,
candidate lifecycle, funding, economics and risk projections; no checklist here
replaces their gate logic.

### 1. Identify runtime, evidence roots and access

Record deployment host, reviewed commit, app identity/base URL, canonical database
name/server/revision, authenticated subject/role and the capture/data root writer
map. Obtain secret injection through the deployment operator; never paste account
credentials, tokens or DSNs into files, argv or tickets. A research database is
not the paper database. Verify migrations through the normal rollout, then use
[the read-only smoke](#read-only-terminal-runtime-smoke) with the reviewed code.
Preserve its protected readiness, unauthenticated rejection and authenticated
read results plus UTC time/exit code. Runtime access is **NOT RUN / ACCESS BLOCKED**
if that authorized pair cannot be identified.

For read-only gate evidence, with the existing token injected:

```text
python scripts/terminal_command.py --api <AUTHORIZED_PAPER_API> --readiness
```

This issues GET `/operator-dashboard/research-terminal/activation` without a
command body. HTTP failures exit nonzero. Exit zero means the projection was
read, even when its answers say BLOCKED. Save `cycle_id`, `holdout_state`,
`state_hash`, each answer's `status`, `subjects`, `reasons`, `identities`,
`evidence` and `next_action`. Check the intended candidate/account subjects,
not just an aggregate READY supported by a different account or candidate.

### 2. Verify actual-account fee evidence; obtain OR-6 decisions

The owner supplies a redacted actual Bybit USDT-perpetual account fee statement
or account fee-screen record: opaque account ID, venue/product, actual VIP/tier,
exact maker and taker **bps per side**, verifier, ISO verification date, source
reference and explicit effective start/exclusive end. Keep secrets and personal
identifiers outside repository/logs. A public fee table, hypothetical rate or
current statement without historical coverage cannot establish development fees.
If full coverage is unavailable, retain UNAVAILABLE rather than extending it.

The operator constructs the existing `FeeScheduleV1` and canonical
`actual-account-fee-evidence-v1` envelope with VERIFIED /
ACTUAL_ACCOUNT_FEE_STATEMENT, validity interval and `evidence_hash =
identity_hash_v1(evidence)`. Install a reviewed `DevelopmentFeeBindingV1` through
`PostgresOperatorDashboardQueries(..., terminal_fee_bindings={study_content_hash:
binding})` in the deployment composition. It binds independent intended account,
venue/product/tier, study/frozen-set/dataset hashes, actual full observation
interval and assessment instant. There is currently **no env-file installer or
browser upload**; do not assume that putting JSON on disk installs this binding.
See [the exact envelope and binding contract](DEVELOPMENT_COST_FRAGILITY.md#actual-account-fee-integration--2026-10-23).
Read Compare for those exact identities and retain VERIFIED/UNAVAILABLE, reasons,
source/verifier/date, coverage, evidence hash and exact fee-only/scenario
remainders. Terminal server `as_of` bounds verification time. Hash consistency
does not independently prove the statement's truth.

Separately the OR-6 owner approves or rejects the prepared
CONSERVATIVE_MEDIUM 1 / 2.5 / 5 bps per-side slippage grid, or supplies a replacement
named grid with exact bps and rationale. Record the approved cost policy as
`or6-cost-policy-v2` in preregistration with the verified schedule; do not mutate
old gross study identities. The retrospective comparison binding does not
approve this policy or prove execution costs.

Historical F3 published settled funding is implemented for T2 holdout validation.
Forward paper funding remains a **technical AND owner-evidence gap**:
`paper_funding_evidence_v1` never emits ECONOMICALLY_COSTABLE and calculates no
charge. OR-6 must separately decide the settlement-price convention (trade proxy
versus venue mark is not interchangeable), whether published settlement is paper
account settlement authority, acquisition permission and fill-at-settlement
holding treatment. No option is silently selected. Existing `paper_evidence.py`
accepts explicit `LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT` proxy declarations but
those are evidence declarations, not approved economic authority; the CLI has no
venue interval store and its funding window remains IRREGULAR. Owner approval
alone does not implement missing charge/settlement logic. Preserve all economic
blockers; do not label fee-adjusted figures as complete net returns.

### 3. Complete owner packets, without executing them in this sprint

Use exact full identities from Compare; short hashes below only locate prepared
packets in [current-phase.yaml](current-phase.yaml). Owner command actor fields
must equal the authenticated subject and owner commands need REVIEW_RISK.
The input schemas are the existing Pydantic models in
`research_terminal_commands_v1.py`; validate complete JSON against those models
before submission. Use stable explicit idempotency keys and retain returned
command IDs, events and final states.

| Gate | Exact outstanding owner decision/input | Existing submission |
| --- | --- | --- |
| OR-7 | Confirm exact ETH breakout study `566bdfb8-301b-544c-93ce-ea6c9cfb5d77`, dataset and rerun; holdout end A: 2026-09-24 or B: latest admissible published archive day at opening, represented as an explicit exclusive date; minimum trades 30/50/100 (prepared 50); acceptance net return > 0 alone, plus owner drawdown cap, or plus owner daily Sharpe floor (prepared return + cap, cap missing); incubation 14/28/56 days (prepared 28); authorized_by/on and approved v2 cost policy | PREREGISTRATION_RECORD; DRAFT until all requirements resolve |
| OR-9 | Approve exact study/rerun/trial/symbol entries, watchlist ID, approved_by/on and note. Prepared core: ETH trend `01e0595f`, SOL trend `db8f92df`, ETH breakout `8813e8d1`/`e275f278`; optional diagnostic-only ETH/SOL mean reversion `a204fdbd`/`2a11033e`. Research subset does not itself authorize any watch | WATCHLIST_RECORD; no runner until separately permitted |
| OR-11 | Account ID, PERSONAL_PAPER or PROP_PAPER, display name and base currency; every policy value below and approved_by/on | ACCOUNT_REGISTER then ACCOUNT_POLICY_RECORD; PROP rules are not enforced by paper v1 and remain blocked |

OR-11 money fields use the chosen base currency: `paper_starting_capital`,
`paper_order_notional`, `daily_loss_limit`, `maximum_drawdown_limit`,
`maximum_order_notional`, `maximum_position_notional`,
`maximum_daily_order_notional`, `maximum_per_trade_loss` are strictly positive.
`maximum_leverage` is a positive multiple; `allowed_symbols` is an explicit
nonempty selection from BTCUSDT/ETHUSDT/SOLUSDT; `max_market_age_seconds` is a
positive integer. `minimum_data_quality`, `maximum_spread_fraction`,
`maximum_event_risk`, `maximum_expected_slippage_fraction` are fractions/scores
in [0,1]; `maximum_stop_distance_fraction` is (0,1] and
`stop_gap_buffer_fraction` is [0,1). No value is proposed by this procedure.
PROP_PAPER additionally needs prop_firm, prop_daily_loss_limit,
prop_max_trailing_drawdown and prop_profit_target, and stays blocked by the
existing paper ledger limitation.

For PERSONAL_PAPER only, the owner may explicitly declare these seven unsupported
controls `NOT_APPLICABLE`: minimum_data_quality, maximum_spread_fraction,
maximum_event_risk, maximum_expected_slippage_fraction, maximum_per_trade_loss,
maximum_stop_distance_fraction, stop_gap_buffer_fraction. The last three must be
all numeric or all NOT_APPLICABLE. Numeric unsupported controls still block
opening because paper v1 has no corresponding evidence source. Missing controls
are never interpreted as NOT_APPLICABLE.

After a **separate approval** to record the prepared packets, use the existing
submission command, one kind/file/key at a time:

```text
python scripts/terminal_command.py --api <AUTHORIZED_PAPER_API> --kind <APPROVED_KIND> --inputs <REVIEWED_PACKET_JSON> --key <STABLE_DECISION_KEY>
python scripts/terminal_command.py --api <AUTHORIZED_PAPER_API> --list
```

The terminal worker must be separately deployed/authorized with matching code,
DB and roots for queued preregistration/validation/runner work. A HTTP 2xx receipt
is not worker success. Verify final command events, then repeat `--readiness`.

### 4. Establish OR-1 capture and clock evidence

Identify one authoritative writer per root before any measurement. Run existing
`capture_bybit_public.py --root <IDENTIFIED_R1B_ROOT> status`, `availability`, and
`universe-hours --hours 2`. OR-1 owner must supply formal max-unproven seconds S;
60 seconds is a paging threshold, not an approved S. Only then run:

```text
python scripts/capture_bybit_public.py --root <IDENTIFIED_R1B_ROOT> universe-acceptance --max-unproven-seconds <OWNER_APPROVED_S> --required-hours 24 --verbose
python scripts/capture_bybit_public.py --root <IDENTIFIED_R1B_ROOT> universe-acceptance --max-unproven-seconds <OWNER_APPROVED_S> --required-hours 24 --verify
```

Retain joint BTC/ETH/SOL COMPLETE hours, longest/current run, evidence end,
interruptions/staleness, replay outcome and the owner's acceptance reference.
A historical MET is not proof of current continuity. See
[OCI capture/backup/clock operations](../deploy/oci/README.md) for root/writer
handoff and independent restore receipt requirements. Do not start capture or
change a host clock as part of this check.

From the smoke retain application-before/DB/application-after UTC instants,
bracket width, DB-minus-app interval and observed relation. Also record that
host's time-service state and separate bounded market-source samples at their
actual observation times. A single bracket and historical different-host samples
prove no ongoing synchronization or owner-approved tolerance. Clock/service
changes require the deployment owner.

### 5. Separately authorized opening and first paper session

Repeat the activation read and inspect candidate lifecycle/exit criteria in
Validation, Risk Preview and Paper. The projection does not include every
external operational requirement: explicitly attach the OR-1/current-clock and
runtime evidence above. Require authorized preregistration, correct cost policy,
accepted operational prerequisites and owner permission before the one-shot
HOLDOUT_OPEN command (preregistration_hash, confirm_cycle_id, opened_by). This
irreversible holdout action is **not authorized by the sprint**. After that separate
authorization, execute HOLDOUT_VALIDATE for the same preregistration hash through
the existing worker; retain every candidate/scenario/lag outcome including failure.
Acceptance uses net criteria under every approved cost scenario at the OR-5
2-second baseline and the mandatory 5/30/60-second sensitivity sweep.
Do not open another cycle or repeat selection to rescue a failed result.

Only a resulting INCUBATING candidate, its authorized watch, a usable account
policy and separately approved runtime/session can support PAPER_INCUBATION_START
with family, dataset_version_id, symbol and watchlist_id. A watch entry alone
cannot make a candidate INCUBATING. Inspect readiness subjects and funding/risk
blockers again; do not bypass the terminal worker via the lower-level unit-fill
runner. Keep cost-incomplete research diagnostics explicitly separate from any
owner-authorized incubation acceptance. Forward funding remains technically
non-costable under v1 even after packets are recorded.

Retain the start command ID; stop via PAPER_INCUBATION_STOP with
`start_command_id` after the authorized session. Read `--list`, terminal
incubation, paper-account and the selected candidate's paper-evidence response.
For stored evidence inspection, `paper_evidence.py` is dry-run by default and
`paper_incubation.py report` reports unit results; both support injected
TRADE_PLATFORM_RESEARCH_DSN so no DSN need appear in argv. Do not add `--persist`
or run a runner without separate authority.

### 6. Interpret and retain results

Strategy Lab/Compare SEARCH and walk-forward are gross development evidence,
not independent validation. Decimal reconciliation establishes numeric/selection
agreement under the recorded convention, not net profitability. Fee-only and
fee-plus-proposed-slippage remainders are sensitivity arithmetic, not compounded
net returns. Gross unit fills, hypothetical account money P&L, proven unrealized
trade references and funding evidence are distinct. A trade proxy is not a
settlement mark; EVIDENCE_COMPLETE is not ECONOMICALLY_COSTABLE. Missing funding,
parity, clock, age, price or risk inputs stay unavailable/refused. Record non-fills,
chain breaks, rejected candidates and all unresolved economic blockers.
INCUBATING is not PROFESSIONALLY_VALIDATED or execution authority.

Sprint evidence at 2026-10-23 / base `dfd4bc5`: the research ledger at 0062 has
nine studies/frozen sets/reruns, zero preregistrations, holdout openings,
watchlist versions, accounts or policies. It cannot stand in for paper readiness.
This process has no POSTGRES_DSN, POSTGRES_TEST_DSN, operator token or explicit
paper environment, so no authorized paper smoke/clock bracket was attempted.
Windows Time was stopped/Manual. OR-1 current runtime/root acceptance was not
measured. Forward funding costability is technically unimplemented. Disposition:
**not ready for genuine paper incubation**; no owner gate, holdout or paper
promotion was activated.

## Read-only terminal runtime smoke

The existing `analytics` extra is the supported local/staging analytical Python
environment: install the repository with `python -m pip install -e '.[dev,analytics]'`
in an isolated environment. `dev` supplies the in-process HTTP smoke client;
`analytics` supplies NumPy, DuckDB and PyArrow. Use the same reviewed source revision
for the smoke and the process that will serve it. This is a local/staging preparation
path, not a separately scanned analytical container or a production release artifact.
The thin API image and `requirements-runtime.txt` deliberately exclude these packages.

| Capability | Thin API | Analytical environment |
| --- | --- | --- |
| Protected PostgreSQL terminal reads and stored research reports | Available with migrated DB and authentication | Same |
| Search/rerun/columnar research tooling | Not installed | Existing analytics extra and explicit research data required |
| Sealed T4 proven-price resolution | Explicit `UNAVAILABLE` when analytical imports are absent | Requires pinned selection policy and admissible stored/raw evidence |
| Net economics, forward validation, owner authority | Existing evidence/owner gates apply | Same; dependencies confer no authority |

Run the smoke from the repository root in the intended environment. Inject
`POSTGRES_DSN` and `TRADE_PLATFORM_OPERATOR_TOKEN` through the existing local/staging
secret mechanism; do not put their values in commands, logs or policy files. Set
`TRADE_PLATFORM_ENVIRONMENT=paper` explicitly and `PYTHONPATH=src`. Apply existing
Alembic migrations separately before running the smoke. It never migrates, seeds,
fetches, starts workers or calls write endpoints.

```text
python scripts/smoke_terminal_runtime.py --profile analytical
python scripts/smoke_terminal_runtime.py --profile thin
```

Both commands test the canonical ASGI app, protected PostgreSQL readiness,
unauthenticated rejection and an authenticated terminal overview read. `thin` checks
the core path without importing analytics and reports installed modules honestly;
running it in an analytical environment does not prove the thin container's package
boundary. CI's existing hardened-container checks own that boundary. `analytical`
additionally imports all three analytics packages and the real frame/price resolver
modules. `PASS` means `RUNTIME_SMOKE_ONLY`: an empty registry, unopened holdout and
missing price policy remain explicit facts, never successful research validation.
The paper composition uses the existing temporary bearer boundary; this check makes
only read requests and makes no least-privilege or production identity claim.
After that authenticated read, the smoke samples `clock_timestamp()` once through
the canonical app's PostgreSQL connection in a read-only transaction. It reports
`app_before_utc`, `postgres_sample_utc` and `app_after_utc`, plus the application
bracket width and the possible `DB - app` offset interval in microseconds. A strictly
positive interval is observed DB-ahead; a strictly negative one is observed
DB-behind; an interval containing zero overlaps the application bracket. The bracket
width is the measurement uncertainty from this query path, including local work and
database round trip; it is not a separately measured network latency. This single
observation is diagnostic only: it is not market knowledge time, an owner-approved
tolerance, or a continuous clock-health guarantee. No timestamp is corrected and no
readiness or evidence gate is relaxed by this measurement.

Validation on 2026-10-23: the focused pure smoke tests passed locally (13/13),
and the PostgreSQL integration test passed on the disposable PostgreSQL service
in [PR #263's core CI job](https://github.com/omerasik/investing_tradin_platform/actions/runs/39837356817/job/120029048350).
The workstation had no `POSTGRES_TEST_DSN`; a separate local database was not
started because available physical memory was about 278 MB and other running
containers were not identified as disposable for this task. Its Windows Time
service was stopped (`Manual`), and no authorized application/database runtime
was available for an actual clock-offset observation. The CI pass verifies the
query and output contract, not the current deployment's clock alignment. The
next operational check is to repeat this smoke in the authorized deployment
and assess its reported interval alongside that host's time-service status and
the separate market-source clock evidence; one sample cannot establish ongoing
clock health.

Access check on 2026-10-23 from the current workspace: no explicit paper
environment, `POSTGRES_DSN`, or operator token was present in this process. No
authorized paper application and canonical PostgreSQL database could be
identified and accessed together, so the authenticated smoke was not run and
there are no application-before, PostgreSQL, application-after, bracket-width,
offset-interval, or relation observations for this runtime. No fixture or other
database substitutes for that missing observation. The local Windows Time
service was stopped with Manual startup at the read-only check; this is a
service-state observation, not proof of current host offset or synchronization.
The separate capture evidence in `docs/current-phase.yaml` records a
2026-10-05 Bybit-minus-Windows-host estimate of +64 ms +/- 126 ms after a
restart, and a 2026-10-13 OCI validation-host range of +8.6 to +11.4 ms with
about +/- 97 ms bounds. Those are RTT-bounded, historical, different-host
market-source observations; neither measures the paper application's
PostgreSQL clock or proves current market knowledge time. The operational check
remains pending until the intended paper runtime and its deployment-owned
credentials are available through the authorized secret mechanism.

For an existing, separately authorized candidate, add `--study-id <UUID> --trial-id
<UUID>` to validate its protected paper-evidence response and report its actual
unrealized availability/reasons. Add `--require-proven-price` only when testing an
installed proven-price deployment; it exits nonzero unless the returned unrealized
mark is `AVAILABLE`. Missing candidate/evidence/configuration never becomes a price.

Minimal proven-price configuration consists of all four existing environment settings:
`TRADE_PLATFORM_TERMINAL_PRICE_SELECTION_PATH`,
`TRADE_PLATFORM_TERMINAL_PRICE_SELECTION_HASH`,
`TRADE_PLATFORM_RESEARCH_DATA_ROOT` and
`TRADE_PLATFORM_UNIVERSE_CAPTURE_ARCHIVE_ROOT`. The JSON policy's canonical identity
hash must match the pin. Its schema is `terminal-paper-price-selection-v1`, with a
nonempty `policy_reference`, aware `declared_at`, and a unique candidate selection
binding `cycle_id`, `study_id`, `trial_id`, stored `entry_fill_hash`, `symbol`,
`source_id`, `session_id`, `instrument`, observation `instant`, explicit integer
`max_age_micros` and `capture_max_unproven_nanos`. These declarations select evidence;
they never contain a supplied price. Consult `terminal_paper_prices_v1.py` for the
existing contract; do not invent age/clock policy values for deployment convenience.

Both roots must contain the exact catalogued frames and independently verifiable
capture partitions referenced by the stored seal. Filesystem access, hash identity,
capture continuity, holdout scope and clock/as-of checks must all succeed. Merely
creating directories is insufficient. Server installation time is an additional
knowledge bound; restarting/reinstalling a policy does not establish its historical
availability. Inspect the smoke's application/DB time observation and retain each
market source's separate clock evidence. Without these prerequisites, keep the
truthful refusal or `UNAVAILABLE` result and leave operator/owner activation gates
closed.

Alert routing currently ends at immutable `LOCAL_OUTBOX` rows whose status is
`PENDING_EXTERNAL_DELIVERY`. Destination references are opaque allowlisted names,
not endpoints. Do not configure network delivery, credentials or a claim of
successful notification until an independently approved adapter implements
retry, current-alert recheck, delivery attempts, escalation and secret handling.

Retention evaluation is evidence-only. Register an approved immutable policy,
then a manifest containing an opaque catalog reference and independently
computed SHA-256. This application does not upload/download object bytes and
must not be given object-store credentials. `ELIGIBLE_FOR_REVIEW` never permits
automatic deletion: verify current policy, legal hold, artifact hash, backup
topology and named human authorization in a separately approved lifecycle
system. Until that system exists, retain the object and append no deletion
claim.


## Forward funding research calculation (offline, hypothetical only)

`paper_funding_calculation_v1.calculate_paper_funding_v1` adds an opt-in Python
calculation beside the unchanged v1 evidence/economics projections. It does not
install a terminal/API route, write a charge, change v1 hashes or start a runner.
Use the existing source loaders to obtain hashed paper fills and issued
`CandidateFundingEvidenceV1` from the verified forward funding store/price bridge.
Load only records available by the explicit `as_of`; retain source/recording
provenance. No acquisition is performed or authorized by this calculation.

Declare every assumption explicitly, for example this **research hypothesis**:

```python
from trade_platform.paper_funding_calculation_v1 import (
    FundingCalculationAssumptionsV1, calculate_paper_funding_v1,
)

assumptions = FundingCalculationAssumptionsV1(
    price_reference_kind="TRADE_PRICE_PROXY",
    price_convention="LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT",
    settlement_authority="PUBLISHED_RATE_HYPOTHESIS_NOT_ACCOUNT_SETTLEMENT",
    holding_at_settlement="STRICT_INTERIOR_REFUSE_FILL_INSTANT",
    position_value_basis="ONE_UNIT_ENTRY_NOTIONAL_FIXED_QUANTITY",
)
result = calculate_paper_funding_v1(
    hashed_fills, recorded_cost_policy, funding=issued_candidate_evidence,
    assumptions=assumptions, as_of=aware_reading_instant,
    # resolution=registry_issued_incubation_resolution, if one exists
)
# Retain result.payload and result.content_hash together.
```

The first-trade convention `FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT` is also
supported when the bridge proves that exact reference; there is no fallback
between conventions. Venue marks, including EXACT ticker timestamp matches,
remain distinct unused evidence and cannot be selected as settlement prices.
Undeclared/unsupported assumptions, another candidate/symbol/validation, future
knowledge, invalid prices, missing coverage, open/broken chains, ambiguous or
unfilled signals, and any settlement exactly at an entry/exit/reversal refuse
the whole calculation. No partial total or favorable holding subset is returned.

Each signed funding contribution is `-direction * published_rate * proxy_price /
entry_price`. Quantity is fixed at one unit of entry notional divided by entry
price: the figure is a return, not account money. Positive is a receipt, negative
a payment. Decimal precision is 50; each settlement is half-even rounded to
1e-12 before summing, without compounding. Prices must be exact at 1e-18 within
NUMERIC(38,18). Zero with no settlements is possible only for a fully covered,
closed holding. Retain candidate/holding identities, entry/exit fill hashes,
raw rate text and payer rule, all settlement/knowledge instants, source window,
bridge, proxy/bar/dataset hashes and separate unused mark evidence. Verify
`identity_hash_v1(result.payload) == result.content_hash`; this proves content
consistency, not the truth of a venue/account settlement claim.

Every successful result is `HYPOTHETICAL_RESEARCH_ONLY`, `in_paper_net: false`;
net remains NOT_COSTABLE with no value. An approval boolean/object is not an
input and cannot grant authority. The existing registries issue no forward
settlement economic authorization, so there is deliberately no verified mode.
For verified costs the owner must separately decide the actual settlement-price
reference (and any nearby-mark rule/tolerance), published-settlement applicability
to the specified account, holding treatment at a simultaneous fill and acquisition
permission, with scope/effective time and registered approval evidence. Genuine
venue settlement-price authority, complete causally known published-rate/interval
and price coverage, proven fill/holding parity, applicable actual-account fees,
approved slippage and remaining account/risk gates are still required. Implemented
v1 approval contracts cannot represent all those decisions; a declaration alone
does not implement them. None of these requirements is fulfilled by this example.
