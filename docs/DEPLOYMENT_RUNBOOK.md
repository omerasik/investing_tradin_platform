# Deployment Runbook

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
