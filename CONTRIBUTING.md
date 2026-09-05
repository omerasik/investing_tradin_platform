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
   owner. (Branch protection enforcing this is currently pending — see
   `docs/MODULE_3B_GOVERNANCE_SECURITY.md`.)

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
