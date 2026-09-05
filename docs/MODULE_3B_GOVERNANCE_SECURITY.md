# Module 3B — Repository Governance & Security Hardening

Companion to [MODULE_3A_PRODUCTION_READINESS_AUDIT.md](MODULE_3A_PRODUCTION_READINESS_AUDIT.md)
and [PRODUCTION_READINESS_MATRIX.md](PRODUCTION_READINESS_MATRIX.md). This
module hardens GitHub-level repository governance and documents the current
security posture. It does **not** change any runtime behavior — no live
trading, no market-data/news provider, and no broker were activated.

Branched from `main@73e9a7977501fe7011c9f2568ee5ca0670b84757` (Module 3A,
PR #56, merged and exact-main-CI-verified).

## 1. Before / after GitHub governance state

| Control | Before (Module 3A audit) | After (this module) |
|---|---|---|
| Branch protection on `main` | Disabled (`404` from `branches/main/protection`) | **Still disabled.** Out of scope for this module — see §7. |
| Rulesets | None | None (unchanged; out of scope) |
| CODEOWNERS | None | `.github/CODEOWNERS` added, owner `@omerasik` |
| Dependabot | None | `.github/dependabot.yml` added (pip, npm/pnpm, github-actions; weekly) |
| Code scanning (CodeQL) | None | `.github/workflows/codeql.yml` added (Python, JavaScript/TypeScript) |
| Secret scanning | Enabled (GitHub default for public repos), push protection enabled | Unchanged (already enabled; verified via `gh api .../security_and_analysis`) |
| Private vulnerability reporting | Disabled (verified via `gh api .../private-vulnerability-reporting`) | Still disabled — documented in `SECURITY.md` as a remaining gap alongside branch protection |
| PR template | None | `.github/pull_request_template.md` added |
| `SECURITY.md` | None | Added |
| `CONTRIBUTING.md` | None | Added |
| Repository-level workflow `permissions:` | Already present and least-privilege in `verify.yml` (`contents: read`, `id-token: write`, `attestations: write`) | Unchanged; `codeql.yml` adds its own least-privilege `permissions:` (`contents: read`; job-level `security-events: write`, `actions: read`) |
| Visibility | Public | **Unchanged — public.** See §2 for the audit rationale. |

## 2. Repository visibility decision

**Decision: keep the repository public. No change made.**

Rationale, per the explicit hard constraint for this module and confirmed by
this audit:

- The Module 3A audit already established that no live execution path exists
  (`docs/PRODUCTION_READINESS_MATRIX.md` §5: "LIVE EXECUTION PATHS FOUND:
  NONE"), no real provider is activated, and no broker is integrated.
- This module's secret-hygiene audit (§5 below) found no committed
  credentials, and GitHub secret scanning + push protection are already
  enabled for the repository, providing an additional backstop against
  accidental credential disclosure.
- Nothing in the codebase depends on obscurity for safety — the paper-only
  boundary is enforced in code (`PlatformConfig.__post_init__`), not by
  hiding the source.
- Changing visibility is explicitly out of scope for this module regardless
  of findings; this section exists to record that the audit was performed
  and found no reason visibility would need to change even if it were in
  scope.

## 3. CODEOWNERS

Added `.github/CODEOWNERS` assigning `@omerasik` (the real repository owner —
no fictitious team was created) as owner of:

- `.github/workflows/**`, `.github/CODEOWNERS`, `.github/dependabot.yml`
- `src/trade_platform/security.py`, `external_identity.py`, `risk.py`,
  `policy_registry.py`, `paper_runtime.py`, `api.py`, `config.py`
- `migrations/**`, `alembic.ini`
- `Dockerfile`, `.dockerignore`, `compose.dev.yml` (the repository's only
  deployment/orchestration configuration — there is no separate
  `deploy/`/`infra/` directory)
- `.env.example`, `.secrets.baseline`, `.gitignore`
- `SECURITY.md`, `CONTRIBUTING.md`, and the security/live-trading/readiness
  docs under `docs/`
- A catch-all `*` default owner

All listed paths were verified to exist before being added; no spec path was
dropped (`security.py`, `external_identity.py`, `risk.py`,
`policy_registry.py`, `paper_runtime.py`, `api.py` all exist under
`src/trade_platform/`, and a `migrations/` directory exists).

CODEOWNERS only takes effect for required reviews once branch protection with
"Require review from Code Owners" is enabled — that step is pending (§7).

## 4. Dependabot

Added `.github/dependabot.yml` covering three ecosystems on a **weekly**
cadence (not daily, to avoid noise):

- `pip` at `/` (backend, `pyproject.toml`)
- `npm` at `/web` (frontend; Dependabot's `npm` ecosystem natively supports
  the `pnpm-lock.yaml` lockfile used here)
- `github-actions` at `/` (workflow action versions)

Development-dependency minor/patch updates are grouped per ecosystem to
reduce PR volume; major bumps and production dependencies still open
individual PRs so they get full review.

## 5. CodeQL

No CodeQL configuration existed previously (`.github/workflows/` contained
only `verify.yml`). Added `.github/workflows/codeql.yml` using
`github/codeql-action` with a language matrix for `python` and
`javascript-typescript` (both present in the repo — backend Python under
`src/`, frontend TypeScript/TSX under `web/`). Runs on push to `main`, on
every pull request, and on a weekly schedule. This is additive: it does not
duplicate or replace the existing Bandit, `pip-audit`, `detect-secrets`,
Trivy container scan, or SBOM/attestation steps already in `verify.yml`.

## 6. Workflow permission hardening

Audited every file under `.github/workflows/` (two, after this module):

- **`verify.yml`** (pre-existing, single job): already declares a top-level
  `permissions:` block — `contents: read`, `id-token: write`,
  `attestations: write`. `id-token`/`attestations` are genuinely needed for
  the existing `actions/attest` provenance/SBOM steps later in the same job.
  Because the workflow has exactly one job, a top-level block is equivalent
  to a job-level block here; no change was made to avoid touching working CI
  logic (the task's constraint against weakening or altering
  attestation/provenance behavior). No `write-all` usage found.
- **`codeql.yml`** (new): top-level `permissions: contents: read`, with the
  `analyze` job additionally granted `security-events: write` (required to
  upload SARIF results) and `actions: read` (required for private repos /
  the standard CodeQL pattern; harmless on a public repo). No broader scope
  requested.

**Action pinning consistency finding (reported, not changed):** the repo
mixes pinning styles — common actions (`actions/checkout@v5`,
`actions/setup-python@v6`, `actions/setup-node@v5`, `actions/cache@v6`,
`actions/upload-artifact@v7`) are pinned to major-version tags, while
`actions/attest` is pinned to a full commit SHA
(`actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d # v4.2.1`). This is
an intentional-looking inconsistency (SHA-pin only the action whose output is
attested/trusted) rather than an oversight, so this module did not change the
pinning style anywhere, including in the new `codeql.yml` (which uses tag
pinning, consistent with the majority convention for non-attestation
actions).

## 7. PR template

Added `.github/pull_request_template.md` with mandatory yes/no declarations
for: live trading changed, provider activated, broker activated, database
migration included, security boundary changed, evidence classification
changed — plus "Tests run" and "Known limitations" sections.

## 8. `SECURITY.md`

Added, covering: supported branch (`main` only), GitHub private
vulnerability reporting instructions (the repository's Security tab →
"Report a vulnerability" flow), an explicit instruction never to report
credentials in public issues, and an explicit "NOT production/live-trading
ready" disclaimer. No email address was invented; reporting is GitHub-based
only. The document also records that private vulnerability reporting is
currently **disabled** at the repository level (verified via
`gh api repos/<owner>/<repo>/private-vulnerability-reporting` →
`{"enabled": false}`) and treats enabling it as a remaining governance gap,
analogous to branch protection.

## 9. `CONTRIBUTING.md`

Added, documenting: branch → PR → CI → merge flow with no direct commits to
`main`; no live-trading activation in ordinary PRs and why (config-level
`LiveTradingForbiddenError`); no provider/broker credentials ever committed;
migration requirements (additive, exercised by `alembic upgrade head` in CI,
never edited post-merge); the requirement to re-verify CI on the exact
post-merge `main` commit after an important module; and the fail-closed
evidence-classification principle already practiced elsewhere in the repo's
docs.

## 10. Secret-hygiene audit

No `.pre-commit-config.yaml` exists, but `detect-secrets` 1.5.0 is already a
`dev` dependency and is run in CI (`verify.yml`) against
`.secrets.baseline`. This module re-ran it locally:

```
python -m detect_secrets.pre_commit_hook --baseline .secrets.baseline <all tracked files>
```

**Result: no new findings** (exit 0, no output) against the existing
baseline. A supplementary manual regex grep for common credential shapes
(`password/api_key/secret_key/access_token/private_key = "<long value>"`)
across the working tree also found **zero matches**.

`.gitignore` gaps found and fixed:

- No `*.sqlite3` / `*.db` pattern existed even though `sqlite3` is the
  local/dev persistence backend (`src/trade_platform/persistence.py`).
  Added `*.sqlite3`, `*.sqlite3-journal`, `*.db`, `*.db-journal`.
- Playwright artifact directories were only ignored under `web/` (`.gitignore`
  already had `web/test-results/`, `web/playwright-report/`); added the
  repo-root-relative forms (`test-results/`, `playwright-report/`) for
  robustness if Playwright is ever invoked from a different working
  directory.
- Added generic `*.token` and `*.credentials.json` patterns as defense in
  depth (the specific known token file, `web/dashboard.e2e.token`, was
  already ignored by name).
- `.env` / `.env.*` (with `.env.example` explicitly un-ignored) were already
  present and correct; no change needed there.

**Explicit finding list for this audit: none.** No secret, credential, or
committed provider/broker credential file was found in the repository.

## 11. Environment variable security inventory

Compiled by grepping `os.environ` / `os.getenv` in `src/` and
`process.env.` in `web/app/`, cross-referenced with `.env.example` and
`src/trade_platform/config.py`, `security.py`, `dev_app.py`.

| Variable | Purpose | Local / CI / staging behavior | Secret? | Default | Fails closed? |
|---|---|---|---|---|---|
| `TRADE_PLATFORM_SESSION_SECRET` | HMAC-SHA256 signing key for the dashboard's server-side session cookie (`web/app/session.ts`) | Local: falls back to a value derived from the view token if unset (per `.env.example`). CI (`verify.yml`): set to a fixture value for the Module 1B job. Staging/prod: must be an explicit, distinct, random secret. | **Yes** | Derived-from-view-token fallback locally; no safe default for staging/prod | Partially — a missing value degrades to a derived (weaker) secret rather than refusing to start; the platform is not staging/production configured today, so this is a documented residual gap, not a live risk |
| `TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN` | Shared-secret login password for the dashboard (`web/app/api/auth/login/route.ts`, `operator-contracts.ts`, `session.ts`) | Local default `local-dev-view-password` (`.env.example`); CI sets explicit fixture values per job | **Yes** | `local-dev-view-password` (local only) | Yes for auth checks (login fails without a matching token); the local default itself is a known, documented dev convenience, not a production credential |
| `TRADE_PLATFORM_OPERATOR_TOKEN` | Backend API bearer-token credential consumed by `OperatorAuthenticator` (`security.py`) | Local default `local-dev-operator-token`; CI sets explicit per-job tokens; container image test uses `container-role-token` | **Yes** | `local-dev-operator-token` (local only) | Yes — unauthenticated requests are rejected (verified by CI: `401` without a token) |
| `TRADE_PLATFORM_OPERATOR_ROLE` | Selects the single static RBAC role bound to the operator token (`security.py`) | Defaults to `viewer` (least privilege) if unset | No | `viewer` | Yes — unknown role values are rejected rather than granted; defaults to the least-privileged role |
| `TRADE_PLATFORM_OPERATOR_SUBJECT` | Static subject/identity label attached to the operator token for audit logging | Defaults to `local-operator` | No | `local-operator` | N/A (labeling only, not an authorization gate) |
| `POSTGRES_DSN` / `POSTGRES_TEST_DSN` | Postgres connection string (includes embedded credentials) for `dev_app.py` and CI | Local default points at `compose.dev.yml`'s local-only Postgres (`postgres:postgres@127.0.0.1:5439`); CI uses an ephemeral service-container DSN | **Yes** (DSN contains credentials) | Local-only default DSN (not usable outside the dev compose network) | Partially — a missing/invalid DSN fails the connection attempt (crashes rather than silently falling back), which is fail-closed in effect |
| `TRADE_PLATFORM_ENVIRONMENT` | Selects the logical environment name (`local_research`, `paper`, `production`, ...) used by `PlatformConfig` | Defaults to `local_research` | No | `local_research` | Yes — `PlatformConfig.__post_init__` requires Postgres persistence for `paper`/`production` and independently, unconditionally forbids `live_trading_enabled=True` regardless of this value (see next row) |
| **Live-trading control** — *no environment variable exists* | There is intentionally no `LIVE_TRADING`/similar env var anywhere in the codebase. `live_trading_enabled` is a `PlatformConfig` dataclass field that defaults to `False` and raises `LiveTradingForbiddenError` unconditionally if ever constructed `True` (`config.py`), including for `environment="production"`. | N/A — not environment-driven at all, by design | N/A | Hardcoded `False`, unconditionally enforced | **Yes, absolutely** — this is enforced at the type/constructor level, not by configuration, and is now additionally guarded by regression tests (§12) |
| Provider credential placeholders | No real market-data or news provider is wired; `.env.example` and `config.py`/`data_providers.py` contain no live provider credential fields at all today | N/A | N/A (none exist) | N/A | N/A — there is nothing to fail open, by construction |
| Broker credential placeholders | No broker integration exists (`broker_adapter.py`/`broker_sync.py` operate against the internal paper simulator only); no broker credential env var exists | N/A | N/A (none exist) | N/A | N/A — same as above |

## 12. Live-trading configuration invariant

A CI-enforced test already existed from Module 3A/earlier
(`tests/test_config.py::ConfigTests::test_live_mode_is_impossible`) proving
`PlatformConfig(live_trading_enabled=True)` raises. This module adds three
small, surgical regression tests to the same file (no Postgres-wiring work —
deferred to Module 3C as instructed):

1. `test_live_trading_defaults_disabled_for_every_environment_shape` —
   proves the default stays `False` across `local_research`, `paper`,
   `production`, and `staging`-named environments.
2. `test_no_environment_variable_alias_can_enable_live_trading` — greps
   every `src/trade_platform/**/*.py` file for `os.environ`/`os.getenv`
   reads whose variable name resembles a live-trading toggle (`LIVE_TRADING`,
   `LIVE_MODE`, `ENABLE_LIVE`, `ALLOW_LIVE`, `LIVE_EXECUTION`, case
   insensitive) and fails if any exist. Today none do — this is a
   regression guard for the future.
3. `test_platform_config_call_sites_never_pass_a_dynamic_live_trading_flag`
   — parses the AST of every backend source file and fails if any
   `PlatformConfig(...)` call site passes `live_trading_enabled` as anything
   other than the literal `False` (i.e. never a variable, env lookup, or
   other expression).

All three pass today (see test results below) and were added to the existing
file rather than duplicating the pre-existing constructor test.

## 13. Security-sensitive file change detection

**Decision: no custom detection script was added.** Per the task's own
guidance, CODEOWNERS (§3) plus a required-reviewer branch-protection rule is
the intended, standard GitHub mechanism for flagging changes to
security-sensitive files — it needs no bespoke script to maintain, cannot
drift out of sync with a separate path list, and is what GitHub's own review
UI and required-status-check model expect. A custom script (e.g. a workflow
step that diffs changed paths against a hardcoded list and comments/fails)
would duplicate CODEOWNERS' path list, need independent upkeep, and is
exactly the kind of brittle mechanism the task asked to avoid absent a
concrete gap. The only reason this isn't fully active today is that branch
protection (which makes CODEOWNERS review mandatory rather than advisory) is
not yet enabled — tracked in §14, not solved by a script.

## 14. Remaining security gaps (explicit)

- **Branch protection on `main` is PENDING.** This is intentionally out of
  scope for this module/agent — the orchestrating session applies it
  directly via the GitHub API with explicit confirmation. Until it is
  applied (at minimum: require pull request review, require the `verify`
  and `codeql` status checks, require CODEOWNERS review, restrict force
  pushes/deletion), CODEOWNERS is advisory only and this module **cannot be
  marked VERIFIED**. Re-verify branch protection is live and re-run/confirm
  the exact-`main` CI checks after it is applied.
- **Private vulnerability reporting is disabled.** Documented in
  `SECURITY.md`; enabling it is a repository Settings change not made by
  this module (kept in scope-parity with the branch-protection restriction
  above — both are GitHub repository-settings mutations, not code/doc
  changes).
- **No secret manager.** Secrets remain environment-variable-only (per §11).
  This was already known from Module 3A (`PRODUCTION_READINESS_MATRIX.md`
  §4: "Secrets | PARTIAL") and is unchanged; this module does not mark
  secrets management as READY.
- **No production identity/RBAC.** Unchanged from Module 3A — one static
  token still maps to one static role; this module does not mark
  authentication/authorization as READY.
- **Dependabot security updates** (the org-level auto-PR-for-vulnerable-deps
  feature, distinct from the `dependabot.yml` version-update config added
  here) is reported as `disabled` by `gh api .../security_and_analysis`.
  Enabling it is, again, a repository Settings/API mutation left to the
  orchestrating session alongside branch protection.

## 15. Production Readiness Matrix update

Only the row this module actually affects was changed in
`docs/PRODUCTION_READINESS_MATRIX.md` — the "Repository governance" row in
§4 (Production Readiness Scorecard). It moves from **BLOCKED** ("Public
repo, branch protection disabled, no CODEOWNERS, no Dependabot") to
**PARTIAL** ("CODEOWNERS, Dependabot, CodeQL, SECURITY.md, CONTRIBUTING.md,
and a PR template now exist; branch protection and private vulnerability
reporting remain disabled — verified via `gh api`"). No other row was
touched: this module does not activate a provider, broker, secret manager,
or production identity system, so those rows (Secrets, Authentication,
Authorization/RBAC, Deployment, Live readiness, etc.) are explicitly left
unchanged.

## 16. Test results

See the final agent report for the full pass/fail table executed as part of
this module. Summary: `compileall`, `alembic upgrade head`, the full
`python -m unittest discover -s tests` suite (510 tests, including the three
new live-trading invariant tests), `ruff check`, the mypy ratchet + explicit
file list, `bandit -r src/trade_platform`, `detect-secrets`, `pnpm exec tsc
--noEmit`, `pnpm lint`, `pnpm test:session`, `pnpm test:workspace`, `pnpm
audit --audit-level high`, and `pnpm exec next build` were all run directly
in this environment and passed. `pip-audit` was run but produced noise from
unrelated packages present in the shared local Python environment (not this
project's declared dependencies) and is not a reliable signal outside CI's
isolated virtualenv — the authoritative run is the one inside
`verify.yml`. Full Playwright/cycle208/Module 1B/Module 2 browser E2E
requires the exact multi-service bash orchestration in `verify.yml`
(sequential `next start` instances, a fixture API server, log-dump-on-
failure) and was not reproduced locally in this Windows/PowerShell
environment; it runs as part of this PR's actual CI, which is the
authoritative signal for those suites.
