# Contributing

This is a safety-first research and paper-trading platform. Contributions
must preserve the platform's paper-only safety boundary. Read
`docs/LIVE_TRADING_READINESS.md` and `docs/PRODUCTION_READINESS_MATRIX.md`
before proposing a change to any security-, execution-, or persistence-
boundary file.

## Workflow

1. **Branch → PR → CI → merge.** All changes land through a pull request
   against `main`. Direct commits/pushes to `main` are not permitted, even
   for maintainers.
2. Branch names should be descriptive (e.g.
   `antigravity/module3b-governance-security`).
3. Open the PR using the repository's PR template
   (`.github/pull_request_template.md`) and answer every safety-boundary
   declaration truthfully.
4. CI (`.github/workflows/verify.yml`, `.github/workflows/codeql.yml`) must
   be green before merge. Do not weaken, skip, or delete a test/check to make
   CI pass — fix the underlying issue instead.
5. After merging a module-level change (anything that materially affects a
   domain in `docs/PRODUCTION_READINESS_MATRIX.md`), re-verify CI is green on
   the exact resulting `main` commit before considering the module complete.
   ("Exact-main CI" — the run must be against the merge commit itself, not a
   pre-merge PR head.)
6. Files matched in `.github/CODEOWNERS` require review from the listed
   owner. When the sole maintainer is also the PR author, the documented
   solo-maintainer procedure below may satisfy this repository review rule.
   It does not satisfy a GitHub requirement for an approving review.

## Solo-maintainer review procedure

This procedure applies while `omerasik` is the repository's sole maintainer
and listed code owner, for PRs authored under that account. Ordinary research
and development changes may use documented self-review, including explicitly
labelled agent-assisted review. This is neither independent review nor a formal
GitHub approval; never create or impersonate a reviewer to represent it as one.

Before merging, record a self-review in the PR description or a PR comment:

- The exact base and head commit SHAs, files and actual diff reviewed; reviewer
  identity and any agent assistance; and the UTC review date.
- Findings and their resolution, relevant tests and CI run links, safety-boundary
  declarations, residual limitations, and why the change is ordinary development
  or which separate owner authorization covers its protected scope.
- An explicit disposition: `SELF-REVIEW COMPLETE — NOT INDEPENDENT REVIEW OR
  FORMAL GITHUB APPROVAL`. An unresolved material finding blocks merge.

A new head invalidates the recorded self-review. After a base update, review
the resulting diff and any integration changes again; refresh the SHAs, record
and necessary targeted regression checks. Required CI, including the existing
security scans, must pass for the effective head and mergeability must be
checked immediately before merge. Preserve branch → PR → CI → merge, existing
branch protection, conversation resolution and traceable commits. Never use an
admin override, disable a check, or lower an enforced review requirement to
apply this procedure. If GitHub requires an approving review that cannot be
obtained, leave the PR blocked.

Self-review alone does **not** authorize changes to security/authentication/RBAC,
secrets or network-exposure boundaries; trading execution or its safety controls;
financial risk, costability/settlement or promotion authority; protected datasets,
holdouts or knowledge-time safeguards; operational activation, deployment,
database migration/recovery; or governance, CODEOWNERS, CI/security checks or
branch protection. These require a separate explicit decision from the human
owner before merge. Record the authorized scope and exact head SHA, the owner's
statement, UTC date and source/reference in the PR. A trusted owner instruction
in the working conversation may be quoted with its source identified; it must
not be invented, attributed to an agent, or presented as a GitHub approval.
A changed head needs renewed authorization for protected changes.

Code integration is never permission to activate capture, open a protected
holdout, approve an economic/risk policy, start paper incubation or place an
order. Those actions retain their separate owner gates. The hard rules below
remain in force.

After each merge under this procedure, verify CI on the exact resulting `main`
commit and retain its SHA/run links in the PR before calling integration complete.
An old PR-head or earlier-main CI pass is insufficient.

Adoption of this amendment itself requires the human owner's explicit approval
of its exact proposed head and a one-time authorization to integrate that policy
PR despite the existing author/code-owner review deadlock. Record that decision
as owner authorization, not as a formal GitHub approval. All required checks and
exact-main verification still apply. This procedure becomes available for other
PRs only after the owner-approved amendment is merged and its exact-main CI passes.

## Hard rules

- **No live-trading activation in ordinary PRs.** `PlatformConfig` forbids
  `live_trading_enabled=True` unconditionally
  (`src/trade_platform/config.py`). Do not attempt to route around this via a
  new environment variable, config alias, or default change. Any change that
  touches this boundary must be called out explicitly in the PR description
  and reviewed with extra scrutiny.
- **No real provider or broker credentials, ever.** Do not commit API keys,
  tokens, DSNs with embedded passwords, or any other credential — not even as
  a "placeholder" that happens to be a real value, and not even in a test
  fixture. Use `env:NAME` references (see
  `EnvironmentSecretResolver` in `config.py`) or clearly fake fixture values
  (e.g. `fixture-view-token`).
- **No new real market-data/news provider or broker integration** without an
  explicit, separate governance decision — this repository intentionally has
  none activated today.
- **Database migrations** (`migrations/versions/`) must be additive/reversible
  where possible, must not be edited after being merged to `main` (add a new
  migration instead), and must be exercised by `alembic upgrade head` in CI.
- **Fail-closed evidence classification.** Anything that reports readiness,
  risk, or compliance status (dashboards, audit evidence, the Production
  Readiness Matrix) must default to the most conservative/blocked state when
  data is missing or ambiguous — never default-open to "ready"/"acceptable".

## Tests

Run the fullest practical subset of the authoritative suite before opening a
PR: `python -m compileall src`, `alembic upgrade head`, `python -m unittest
discover -s tests`, `ruff check src tests migrations scripts`, `mypy`
(per `.github/workflows/verify.yml`), `bandit -r src/trade_platform`, and the
frontend suite under `web/` (`pnpm exec tsc --noEmit`, `pnpm lint`,
`pnpm test:session`, `pnpm test:workspace`, `pnpm exec next build`,
Playwright E2E). If part of the suite cannot run locally (e.g. no Docker,
no Postgres), say so explicitly in the PR rather than skipping silently.
