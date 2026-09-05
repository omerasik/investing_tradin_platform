# Security Policy

## Platform status

This repository is a **research and paper-trading platform**. As of this writing:

- Live trading is disabled and is forbidden by construction (see
  `src/trade_platform/config.py::PlatformConfig.__post_init__`, which raises
  `LiveTradingForbiddenError` if `live_trading_enabled` is ever `True`).
- No real market-data provider, no real news provider, and no broker is
  activated anywhere in this codebase.
- **This platform is NOT production-ready and is NOT ready for live trading.**
  See `docs/PRODUCTION_READINESS_MATRIX.md` and `docs/LIVE_TRADING_READINESS.md`
  for the current, evidence-based readiness state. Do not deploy this platform
  against real money, real brokerage accounts, or real market-data/news
  vendor credentials.

## Supported branch

Only `main` is supported for security fixes. Feature branches and forks are
not maintained.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for a suspected security
vulnerability, and never include credentials, tokens, DSNs, or other secrets
in a public issue, pull request, or discussion — even as an example.

This repository has GitHub's private vulnerability reporting flow available
for public repositories. To report privately:

1. Go to the repository's **Security** tab on GitHub.
2. Click **Report a vulnerability** ("Advisories" → "New draft security
   advisory").
3. Describe the issue, the affected file(s)/endpoint(s), and reproduction
   steps. Include enough detail for a maintainer to reproduce and fix it
   without further back-and-forth in a public thread.

If the private-reporting flow is ever unavailable to you, contact the
repository owner (GitHub user `@omerasik`) through GitHub directly (e.g. by
mentioning them on a draft security advisory or private channel) rather than
filing a public issue. No email address is published for vulnerability
reports; use GitHub-based reporting only.

At the time of this Module 3B audit, GitHub private vulnerability reporting
was **not yet enabled** on this repository (verified via
`gh api repos/<owner>/<repo>/private-vulnerability-reporting`). Enabling it is
a repository Settings change and is tracked as a remaining gap alongside
branch protection — see `docs/MODULE_3B_GOVERNANCE_SECURITY.md`. Until it is
enabled, use a draft security advisory (if visible under the Security tab) or
otherwise avoid public disclosure of exploit details.

## What is in scope

- The Python backend under `src/trade_platform/` and its API surface
  (`src/trade_platform/api.py`).
- The Next.js dashboard under `web/`.
- CI/CD workflow definitions under `.github/workflows/`.
- Database migrations under `migrations/`.

## What is explicitly out of scope

- The platform's intentional research/paper-only limitations that are already
  documented in `docs/KNOWN_LIMITATIONS.md`, `docs/PRODUCTION_READINESS_MATRIX.md`,
  and `docs/LIVE_TRADING_READINESS.md` — these are tracked as readiness gaps,
  not vulnerabilities, unless you find a way to bypass the stated safety
  boundary (e.g. actually enabling live trading, or activating a real
  provider/broker without authorization).
