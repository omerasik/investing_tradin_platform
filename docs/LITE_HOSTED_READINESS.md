# P0.4D.1 — Lite hosted readiness

Disposition on 2026-10-29: **BLOCKED**. This is a deployment preparation record,
not approval to provision, publish images with data, upload evidence or deploy.
Implementation baseline: merged P0.4C.2B, `f557f613990093a34f140edbc5bdd7907145dd39`.
No Railway account/project discovery or mutation is needed for this phase;
never use the locally linked project or interact with SocialOS.

### Opt-in code-only image qualification (P0.4D.1A)

After its focused PR and normal required CI merge, run the manual
`lite-image-qualification.yml` workflow with `--ref main -f source_sha=<exact
merged-main-SHA>`. The workflow refuses a different dispatch SHA or branch.
It builds **both actual Dockerfiles**, sequentially in a 4 GiB / 2 CPU BuildKit
container (stopped before each scan), using `--load` without a registry push.
The existing digest-pinned Trivy 0.73.0 policy checks EOL and fixable
HIGH/CRITICAL findings; secret findings also block qualification. Standard
GitHub-hosted Linux admission requires 5 GiB available RAM/commit, 12 GiB free
disk and two CPUs; monitored floors are 512 MiB RAM, 1 GiB commit and 3 GiB disk.
These are isolated ephemeral-runner limits, not changes to Windows guards.

Production smoke uses generated credentials, a fresh capped PG16 database,
three **synthetic** one-day filesystem fixtures and **zero studies/results**.
It exercises the actual non-root API/dashboard image commands, chart readers,
health, session/login/logout/HTTPS-cookie contract, SELECT-only reader and
mutation/execution refusal. Services bind only runner loopback ports; the
dashboard shares the API container network namespace to use an accepted
loopback API URL, without changing the hosted HTTPS validator.

Only `admission.json` and `qualification.json` are uploaded for seven days:
exact source SHA, image config IDs (local SHA-256 digests, not registry
RepoDigests), layer IDs/archive hashes, sanitized CVE/package/severity counts,
scanner DB metadata, startup outcomes, step timings, cgroup peak memory/CPU
usage and host minima. No image tar, raw secret report, environment/inspect
output, credentials, cookies, logs, SQL or fixture/data file is uploaded.
The entrypoint refuses workstations and does not read private Lite settings.

A workflow SUCCESS with `IMAGE_QUALIFICATION_PASS` qualifies code-only images;
it does **not** qualify private source/export/restore, real three-study recovery,
public TLS/ingress, Railway volume permissions or deployment. Failed admission
or build/scan/smoke reports BLOCKED with the sanitized failing step; do not use
a paid runner as an automatic fallback. Record actual run conclusions below
only after execution; the older pending gates below are not silently passed by
adding this workflow.

## Evidence and pending gates

| Gate | Result / precise prerequisite |
| --- | --- |
| Source baseline | P0.4C.2B receipts verified three canonical completed studies, 252 trials/results, 504 trial events, three manifests; every study has 64 evaluated + 20 inadmissible, zero failed/pending. These are prior-phase observations, not a new export audit. |
| API image | PENDING: actual `Dockerfile.lite` build, package/CVE scan and production image smoke on an admitted machine. Canonical API CI builds the root `Dockerfile`, which does not establish Lite image readiness. |
| Dashboard image | PENDING: actual `web/Dockerfile` build/scan/smoke. Existing CI production `next build` and browser tests are useful regression evidence, not a Docker image qualification. |
| Local admission | Refused: initial sample physical RAM 412,786,688 bytes, commit headroom 1,429,397,504 bytes, disk 26,630,541,312 bytes. Later sample RAM 777,060,352, commit 1,885,687,808, disk 26,597,781,504 bytes also fails the unchanged 1 GiB initial RAM guard. Private measurement: `terminal-lite/p04d1-admission.local.json`. |
| New source/dataset audit | PENDING admitted bounded verifier; do not replace source bytes or rely on the earlier receipt as fresh verification. |
| Private export/package | PENDING fresh audit and admitted environment. No dump or market-data package created or uploaded in this phase. |
| Independent PG16 restore | PENDING export plus a separate disposable PG16 container/volume, enough RAM/disk and target identity checks below. No local Lite reset/migration. |
| Hosted HTTPS/volume/resource smoke | PENDING owner-approved isolated deployment; static contracts checked below. |

Reproduce resource admission without database access:

```powershell
.venv\Scripts\python -c "from scripts import terminal_lite_research as p; from scripts.research_loading_performance import host_memory; h=host_memory(); print(h); print(p.admitted(h,initial=True))"
```

Keep the existing 1 GiB initial / 512 MiB running physical reserve, 1 GiB commit
headroom and 24 GiB free-disk floor. Do not force an operation after refusal or
trim other applications' memory to manufacture admission. Build sequentially on
an isolated runner with at least 4 GiB available RAM and commit headroom, plus
the disk floor; this is a conservative build admission requirement, not a
measured image footprint. An existing runner may build/scan code with generated
test credentials and synthetic fixtures only. Never give CI real dump/evidence,
local settings, secrets or authenticated screenshots. CI artifact upload is
allowed only for sanitized code-image scan evidence.

## Image qualification recipe (not executed here)

Run from a clean exact-commit checkout, with no local secrets or data. Record
commit, base digests, resolved image IDs, build logs, scanner/database versions,
SBOM and scan conclusions. The Dockerfile-specific allowlist includes only
source/requirements for Lite; `web/.dockerignore` excludes local settings,
dependencies, `.next` and all private data from the dashboard build context.

```sh
docker build --file Dockerfile.lite --tag lite-api:p04d1 .
docker build --file web/Dockerfile --tag lite-dashboard:p04d1 web
docker image inspect lite-api:p04d1 lite-dashboard:p04d1
```

Use the repository's pinned Trivy gate/scanner database process on **these two
image IDs**, rejecting EOL distributions and fixable HIGH/CRITICAL findings;
review retained unfixed findings separately. Do not reuse the canonical API's
scan as the Lite scan or weaken package pins after a build failure. Confirm
UID/GID 10001, no embedded credentials, no database/dataset/settings/dump files,
and runtime-only secrets. No registry publication in this phase.

Run sequential containers with explicit memory/CPU/PID limits, no privileged
mode, dropped capabilities, no-new-privileges, bounded `/tmp`, one replica and
loopback ports only. Test production `next start`, not `next dev`. Read-only
rootfs is appropriate for the API with a writable bounded `/tmp`; verify the
dashboard's Next.js runtime cache requirements before enforcing a read-only
rootfs there. Record actual peak RSS/CPU/disk, OOM/restarts and health latency.
Use generated fixtures on CI; real restore/evidence tests stay on an admitted
private machine. Do not introduce synthetic performance into the real ledger.

## Private snapshot, export and evidence transfer

This procedure is intentionally **pending execution**. A byte hash of a dump
does not prove recovery: both byte integrity and canonical logical
reconciliation are required. Custom `pg_dump` archives contain metadata and
need not be byte-identical on repeated exports; deterministic acceptance means
identical logical inventories for the same source snapshot, not a fabricated
stable dump hash.

1. Use a new timestamped private directory outside Git under
   `~/.trade_platform/terminal-lite/transfer/`; fail if it already exists. Limit
   access to the current operator (Windows ACL or POSIX mode 0700; files 0600).
   Do not print DSNs, credentials, raw SQL, rows or file contents into logs.
   No cloud or CI destination is allowed. Do not copy `settings.local.json`.
2. Perform read-only source admission using
   `scripts.terminal_lite_research.checked_writer_dsn`, `container_identity`,
   `checked_studies(active_root())` and `database_proof` on the **constructed**
   loopback `127.0.0.1:5441/trade_terminal_lite`, user `lite_owner`, with secrets
   read locally from settings. Require the existing system-identifier pin,
   schema `20261020_0067`, capped container and SELECT-only reader checks. No
   inherited DSN, connection URI argument or remote alias is accepted. Call
   `verify_results(..., complete=True)` for each of exactly the three studies;
   recheck the completed receipt digests and original pinned BTC manifest.
   Run this verifier in the existing single-core, single-process 512 MiB
   Windows Job Object envelope with a 120-second deadline and host guards;
   no worker search/evaluation command is needed or permitted for transfer.
3. Retain one `REPEATABLE READ, READ ONLY` source transaction. Export its
   snapshot with `SELECT pg_export_snapshot()` and keep it open while taking
   the inventory and dump. Collect sorted `(table_name, count, SHA256)` entries
   for **every public table**, including empty tables and `alembic_version`.
   For each table stream `SELECT to_jsonb(t)::text FROM <quoted table> t ORDER
   BY to_jsonb(t)::text`. On both connections set TimeZone=UTC, DateStyle=ISO,
   IntervalStyle=postgres and extra_float_digits=3. For each returned text row,
   encode UTF-8 and feed its byte length as an unsigned eight-byte big-endian
   integer, then its bytes, to SHA-256. Start a new digest per table; an empty
   table has the SHA-256 of empty bytes. Use `psycopg.sql.Identifier`, never
   concatenate identifiers supplied by a caller. Include sequence
   `last_value/is_called` in a separately ordered inventory and require no
   writer during sequence capture (sequence changes are not MVCC snapshots).
   Store this inventory privately, containing hashes/counts only.
4. Use the existing source container's PostgreSQL **16** client, with the
   exported snapshot and no role/global export:
   `docker exec trade-terminal-lite-postgres-1 pg_dump -U lite_owner -d
   trade_terminal_lite --format=custom --no-owner --no-acl --snapshot=<snapshot>`.
   Stream stdout directly into the new private binary file with Python
   `subprocess.run(..., stdout=binary_file, check=True, timeout=120)`; never
   stream it to the terminal or through PowerShell text redirection. The source
   transaction is committed/closed only after dump success. Failure leaves a
   private rejected partial artifact; never advertise it as a valid backup.
5. Record dump size and SHA-256; enforce a 64 MiB **database** ceiling and a
   separately bounded transfer-directory disk budget. Verify the dump with
   PG16 `pg_restore --list` without restoring it to the source. A truncated
   copy must be rejected. A listable archive alone is not restore acceptance.
6. Independently reverify the selected `subsets/2026-08-01_2026-08-14` root with
   `verify_lite_subset`, `report_days` and the three-dataset allowlist. Require
   20,160 rows per market / 60,480 total, exact August 1–14 dates, original
   retained day manifests/objects and source archive file identities. The
   evidence ceiling stays 16 MiB; missing/corrupt objects or symlinks fail.
   Hash every regular file by relative path and bytes before packaging. Use a
   deterministic tar (sorted paths, normalized archive UID/GID/name/mtime,
   gzip mtime=0); do not normalize or modify the source files themselves.
   Include only this selected root, never its parent, full archive, settings,
   previous subset, cluster pin or receipts. Repeat packaging to verify its
   hash, then rehash the source files to prove they were not changed.
7. The private transfer manifest binds dump hash, evidence archive hash, full
   logical SQL inventory, per-file hashes, source commit, schema, source
   cluster identity, exact dataset/study/trial/result/manifest identities and
   expected canonical manifests. Keep it separate from code/images. Encrypt
   at rest/in transit using owner-approved storage/recipient keys. No upload
   until the owner approves these exact artifact hashes, recipient and region.

Expected study set (UUIDs, never labels inferred from the archive name):

| Market | Dataset | Study |
| --- | --- | --- |
| BTC | `e5942fd6-42f4-5ad2-8b34-09bae2996db6` | `020fe0da-b880-54fb-be1b-52cee81bbd7f` |
| ETH | `d3436b40-3d8b-58e1-987f-82dc0d08c95f` | `761c1b42-fb0e-53ec-aaec-6bb5931895cc` |
| SOL | `04b8964d-5053-5783-b390-93d916e0a06e` | `c187ee85-932a-535a-b147-13821a9ac24b` |

## Independent restore and reconciliation

On admission only, create a uniquely named **disposable local** PG16 container,
fresh volume and private network with labels identifying P0.4D.1. Bind only
`127.0.0.1:5542:5432`; cap 256 MiB / 0.5 CPU with the same PostgreSQL settings
as `compose.lite.yml`. Use a new password supplied through a private env file,
not argv/logs; database `trade_terminal_lite`, owner `lite_owner`. Inspect actual
container labels, port, mount, limits, `current_database/current_user`, server
version/address and `pg_control_system()` before any restore. The new cluster
identifier must differ from the source; record a **new target proof**, never
rewrite the source cluster pin. Require zero public tables and no active API.
Never restore to port 5441 or an existing/nonempty target. No `--clean`, schema
reset, source migration, `prepare` or bootstrap call is used.

Verify the private archive hash, then stream the dump via binary stdin to:

```text
docker exec -i <verified-disposable-container> pg_restore -U lite_owner
  -d trade_terminal_lite --no-owner --no-acl --exit-on-error --single-transaction
```

Recompute the complete ordered SQL/sequence inventories and compare to the
source inventory; require exact equality, not just counts. Run the canonical
study/trial/result/manifest checks against the restored connection and verified
filesystem root. Compare `study_digest` for all six ledger tables per study to
the source receipts; require exactly 3/252/252/504/3 studies/trials/results/
events/manifests, 252 SUCCEEDED queue rows, and zero unexpected studies,
candidates, holdout openings or command/execution records. Any mismatch blocks
acceptance. Do not use source `database_proof` against a new cluster: its source
pin is deliberately different. Its identity/grants checks must be performed
against the separately recorded target proof.

Create a fresh `lite_reader` with an independently generated password on the
**target only**: LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT, no role
memberships, database CONNECT and schema USAGE, SELECT on all public tables;
revoke PUBLIC database/schema CREATE. Set default_transaction_read_only=on and
statement_timeout=5s; retain the existing query bounds. Source role passwords
and global roles are never exported. Verify reader grants using the existing
Lite admission SQL; test INSERT/UPDATE/DELETE/DDL refusal in rollback-only
transactions as the reader. Reconcile logical tables again after grants.

Extract evidence into a new target staging directory only, refusing absolute
paths, `..`, links, devices and duplicate paths. Match every extracted file's
hash and re-run canonical evidence verification before atomic publication.
Give runtime UID/GID 10001 read/traverse access, no evidence write permission.
Mount read-only where supported; otherwise enforce OS ownership/modes and test
write refusal as the runtime UID. Do not make the application root to bypass
volume ownership. Any temporary target-only ownership initialization must be
separate from the serving process and logged without data contents.

Run bounded production API/dashboard smoke against this clone privately.
Authenticate, discover all three studies through catalogue bindings, reconcile
all 252 result/hash/metric texts, test pagination/filters/charts and comparison
unavailable states, and assert disabled execution flags and all non-read API
methods refuse. Restart both containers and repeat identity discovery; do not
rerun research. Destroy only the explicitly recorded disposable container,
network and volume after checking they differ from every source identity.

## Exact proposed Railway settings (apply only after later approval)

Use a **new isolated project**, one environment, one owner-approved region,
three services, one replica each. Record explicit new project/environment/
service/volume IDs; no bare `railway up`, linked defaults or SocialOS discovery.
Prepare settings in the service UI/CLI after approval; do not add deprecated
`railway.json`/`railway.toml` or run an IaC apply in this phase.

| Service | Code/source, port, health, limits and storage |
| --- | --- |
| `postgres` | PG16 image pinned to its tested digest; no public domain/TCP proxy; port 5432; `trade_terminal_lite`, `lite_owner`; 256 MiB, 0.5 vCPU; volume `/var/lib/postgresql/data`, initial 1 GiB requested (confirm plan minimum before creation); shared_buffers=32MB, work_mem=2MB, max_connections=12, max_parallel_workers=0. Restore reviewed dump into this fresh empty DB; no migrations. |
| `lite-api` | Repository root, builder Dockerfile, path `Dockerfile.lite`, exact reviewed commit; PORT=8000 and target port 8000; default image command (one uvicorn worker, concurrency 32); health `/health/lite-ready`, timeout 120s; 512 MiB, 1 vCPU; independent evidence volume at `/data`, requested 1 GiB subject to plan minimum; root `/data/evidence`; UID/GID 10001. No research worker/start command. |
| `dashboard` | Repo root `/web`, Dockerfile `Dockerfile`, exact commit; PORT=3000 and target port 3000; image `next start` command; health `/login`, timeout 120s; 2 GiB, 1 vCPU until production RSS measurement justifies less; no persistent volume. |

Set API runtime-only `LITE_READ_DSN` to the new private PostgreSQL host and
**lite_reader**, `LITE_RESEARCH_ROOT=/data/evidence`, `TRADE_PLATFORM_LITE=1`
and a new `TRADE_PLATFORM_OPERATOR_TOKEN`. Never inject owner credentials,
POSTGRES_DSN, DATABASE_URL, worker, capture or execution variables into the API.
Keep numeric/Arrow/DuckDB thread and memory/query limits as in the image/code.

Set dashboard runtime-only `NODE_ENV=production`, `TRADE_PLATFORM_LITE=1`,
`TRADE_PLATFORM_ENVIRONMENT=STAGING`, `NODE_OPTIONS=--max-old-space-size=512`,
`NEXT_TELEMETRY_DISABLED=1`, independent high-entropy
`TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN` and `TRADE_PLATFORM_SESSION_SECRET`,
the matching API operator token, and `TRADE_PLATFORM_DASHBOARD_ORIGIN` equal
to its exact approved HTTPS origin. No NEXT_PUBLIC secret, build argument,
database credential or copied local dashboard config.

Current dashboard URL validation accepts HTTPS or loopback HTTP only. Therefore
`http://lite-api.railway.internal:8000` is **incompatible** as currently written.
Preserve the validator: use the API's Railway HTTPS domain after its bearer
authentication and refusal tests pass, then set
`TRADE_PLATFORM_API_BASE_URL=https://<approved-api-domain>`. No unauthenticated
data endpoint may become public. If private-only API ingress is required, this
is an explicit unresolved design prerequisite (reviewed TLS proxy or a separate
scoped URL-policy change); do not silently allow internal HTTP or add a fourth
service. Database remains private in either case.

Both Dockerfiles have fixed start ports, so explicitly configure Railway PORT
and domain target ports as above; do not rely on arbitrary injected ports.
Keep public ingress disabled until authentication is installed, local/private
production tests pass, and owner approves a controlled authenticated HTTPS
verification. Verify TLS certificate/redirect, HTTPS origin and proxy trust,
Secure/HttpOnly/SameSite cookies, invalid/expired/forged session refusal,
logout, no secrets in DOM/network/logs, CSP/HSTS/no-store headers, authenticated
API GETs and unauthenticated 401s, all mutating methods refused, and no timing/
economic/execution claim inflation. Health endpoints and `/login` may be public
but must expose no private evidence. Railway healthchecks gate deployment,
not continuous availability; configure separate owner-approved monitoring.

## Owner approval, cost controls and teardown

Minimum later approvals: (1) workspace/billing payer, new project name/region
and operators; (2) exact tested image digests/commit and passed image scans,
restore reconciliation and private production smoke; (3) exact encrypted dump
and evidence archive hashes, recipient, region, retention and transfer purpose;
(4) numeric monthly budget/hard cap and alert thresholds, volume sizes and
service resource caps; (5) controlled HTTPS ingress/security verification and
who can access the shared operator login; (6) teardown date and backup retention.
Shared operator authentication is not enterprise multi-user OIDC/RBAC.

Current published rates: RAM $10/GB-month, CPU $20/vCPU-month, egress $0.05/GB,
volume use $0.15/GB-month, plus applicable subscription/minimum/credits/tax.
At the proposed 2.75 GiB RAM and 2.5 vCPU limits, a deliberately conservative
fully-utilized planning estimate is about $77.50/month compute plus storage,
egress and applicable plan terms; limits are not measured usage or a guaranteed
bill. Confirm Railway billing units and current rates before approval. Builds
are currently listed as free; this does not authorize project creation.

Set workspace usage alerts and the owner-chosen hard spending limit **before**
any paid creation, with awareness of all other workspace workloads: do not
silently change a shared workspace's cap. Verify shutdown behavior and alert
delivery with the owner. Resource caps and healthchecks are not spending caps;
hard spending limits can cause downtime and do not replace a retention plan.
Disable automatic deployments, cron, replicas and auto-volume growth beyond
approved storage. No automatic restart loop beyond a bounded on-failure policy
(three retries proposed) without an alert. Review disk/DB size below the
existing evidence/database ceilings and record daily spend during initial soak.

Teardown: remove public ingress first, stop application services, retain an
owner-approved encrypted backup with expiry, revoke new reader/operator/session
credentials, then delete only the explicitly recorded new services/volumes/
environment/project. Stopping compute does not delete billable persistent
storage. Confirm the billing page and remaining volumes/backups afterward;
do not touch local Lite or any existing Railway project. Do not perform this
plan automatically in this phase.

References checked 2026-10-29: [pricing](https://docs.railway.com/pricing/plans),
[cost controls](https://docs.railway.com/pricing/cost-control),
[volumes and UID caveats](https://docs.railway.com/volumes/reference),
[private networking](https://docs.railway.com/networking/private-networking),
[healthchecks and ports](https://docs.railway.com/deployments/healthchecks),
[config-as-code deprecation](https://docs.railway.com/config-as-code/reference).

Stop disposition remains BLOCKED until the specifically pending image,
fresh source/export/restore and private production-mode tests above pass.
Owner approval for actual deployment is a separate subsequent decision.
