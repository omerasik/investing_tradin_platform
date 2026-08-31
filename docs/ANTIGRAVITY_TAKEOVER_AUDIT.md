# Antigravity Takeover Audit & Red-Main Recovery Report

**Repository:** `omerasik/investing_tradin_platform`  
**Takeover Time:** 2026-09-01  
**Author:** Antigravity Engineering Takeover  
**Safety Boundary:** LIVE TRADING DISABLED (Strict Research / Paper / Review Mode Only)

---

## A. Repository State

* **Takeover Local Branch:** `codex/cycle237-risk-workspace` (commit `6373341`, merged upstream into remote `main` as `2569eb7`).
* **Takeover Local Main:** `fd820486fb7f66b9ef943748fd7332ecb19759aa` (behind remote `main` by 1 merge commit).
* **Takeover Remote Main:** `2569eb7eaaccedb9312c26e9b62ca8c409e01de1` (Merge of PR #45 / Cycle 237).
* **Post-Recovery Remote & Local Main:** `e7d96116bb87d8d212711dc756df602928507c57` (Merge of PR #46).
* **Local / Remote Divergence at Takeover:** Local `codex/cycle237-risk-workspace` was clean and aligned with `origin/codex/cycle237-risk-workspace`. No stashed or uncommitted changes existed.
* **Ignored & Generated Directories:**
  * `.venv/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`
  * `graphify-out/` (historical code visualization artifacts; non-source)
  * `web/.next/`, `web/node_modules/`, `web/test-results/`, `web/tsconfig.tsbuildinfo`
  * `web/dashboard.config.json`, `web/dashboard.e2e.token`
* **Repository Visibility:** The GitHub repository is configured as **`PUBLIC`** (`"visibility": "public"`, `"private": false`), despite the top-level README claiming `"Private, safety-first investment intelligence"`.
* **Branch Protection:** Branch protection on `main` is currently **disabled** (GitHub API returned HTTP 404), which allowed PR #45 to merge despite failing CI.

---

## B. Red Main Recovery

* **Failing GitHub Actions Run:** `33447348648` (Merge commit `2569eb7`)
* **Failing Test:** `test_authority_projections_are_bounded_pit_correct_and_read_only` in `tests/test_operator_dashboard_postgres.py` (line 252).
* **Failure Mismatch:**
  ```text
  AssertionError: Tuples differ: (True, '250.000000000000', True, False) != (True, '250', True, False)
  First differing element 1:
  '250.000000000000'
  '250'
  ```
* **Root Cause:**
  1. PostgreSQL stores currency and notional quantities in fixed-scale columns (such as `notional NUMERIC(30,12)` in `risk_reservations`).
  2. When psycopg executes queries against these columns, it instantiates Python `Decimal('250.000000000000')` preserving the full database column scale.
  3. `operator_dashboard._decimal` previously performed naive `str(value)`, leaking raw database scale zeros (`'250.000000000000'`) to API responses instead of canonical financial decimal representation.
* **Canonical Representation Contract:**
  * Exact financial decimals across API projection boundaries must be normalized (`value.normalize()`) and formatted without exponential notation (`format(normalized, "f")` with `'0'` for zero).
  * No float conversion is permitted.
  * Semantically identical NUMERIC values (e.g. `250.000000` vs `250`) produce identical canonical string representations across all API endpoints and UI consumers.
* **Fix & Code Changes:**
  1. Updated `operator_dashboard._decimal` in `src/trade_platform/operator_dashboard.py` to canonicalize `Decimal`, `int`, `float`, and numeric string inputs.
  2. Renamed endpoint handler in `src/trade_platform/api.py` to `operator_risk_decisions` to eliminate mypy parameter shadowing.
  3. Added comprehensive decimal normalization regression tests in `tests/test_operator_dashboard_api.py`.
  4. Updated test assertions in `tests/test_operator_dashboard_postgres.py` and `web/e2e/cycle208.spec.ts` to expect canonical decimal strings (`'0.01'`, `'0.02'`, `'0.5 / 0.6'`, `'0.98'`).
* **Recovery PR & Mainline CI:**
  * Recovery Branch: `recovery/cycle237-main-canonical-decimals`
  * Recovery PR: **PR #46** (`https://github.com/omerasik/investing_tradin_platform/pull/46`)
  * PR CI Run: `33448969966` (**GREEN / SUCCESS**)
  * Merged to Main: `e7d96116bb87d8d212711dc756df602928507c57`
  * Mainline CI Run: `33449286565` (**GREEN / SUCCESS**)

---

## C. Runnability Audit (Why the App Currently Feels Unrunnable)

If a new developer or operator clones this repository and attempts to run the platform locally, the workflow immediately breaks down due to hidden prerequisites:

1. **No Unified Dev Startup:**
   * There is no `docker-compose.yml`, `Makefile`, or dev script to start PostgreSQL, run migrations, launch the FastAPI backend, and run Next.js in a single step.
2. **Database Provisioning & Migration Gap:**
   * PostgreSQL must be installed/started separately.
   * `alembic upgrade head` must be executed manually.
3. **Hidden Configuration & UUID Dependencies:**
   * The Next.js frontend (`web/app/dashboard-config.ts` and `web/app/page.tsx`) requires a `web/dashboard.config.json` file populated with over 10 specific UUIDs and parameters:
     * `feature_definition_id`, `feature_instrument`, `feature_dataset_version`, `feature_decision_time`
     * `scorecard_id`, `regime_run_id`, `portfolio_construction_run_id`
     * `investment_thesis_id`, `investment_portfolio_id`, `paper_order_intent_id`, `paper_account_id`
   * In a fresh database without seed data, or without copying exact UUIDs into `dashboard.config.json`, almost all 17 workspaces render `"UNAVAILABLE"` empty states.
4. **Browser Authentication Barrier:**
   * Opening `http://localhost:3000/` directly in a browser results in a raw JSON error:
     ```json
     {"detail": "Dashboard authentication required."}
     ```
   * There is no login screen or authentication redirect. The browser user must manually provide an `Authorization: Bearer <token>` header or attach `?token=...` in the URL.

---

## D. Authentication Audit (Cycle 237 Changes & Security Risks)

* **Current Implementation (`web/proxy.ts`):**
  * Reads `TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN`.
  * Extracts token from `Authorization: Bearer <token>`, `cookie: dashboard_view_token`, or query string `?token=<token>`.
  * If `?token=` matches, it sets a cookie `dashboard_view_token` with `httpOnly: true, sameSite: "lax", path: "/"`.
* **Identified Security Risks:**
  1. **Query-String Credential Leakage:** Tokens in URL query parameters are logged in browser history, proxy access logs, monitoring services, referrer headers, and can be exposed via copy-pasted URLs or screenshots.
  2. **Incomplete Cookie Security:** The cookie lacks the `Secure` attribute for HTTPS deployments, has no explicit TTL/expiry, and has no rotation or logout mechanism.
  3. **No Interactive Login Flow:** An unauthenticated human visiting the site is greeted with a 401 JSON response instead of a login interface.
* **Target Design for Module 1:**
  1. Remove `?token=` query-string authentication.
  2. Implement a dedicated `/login` page with standard form submission.
  3. Issue a secure session cookie (`HttpOnly`, `SameSite=Lax`, `Secure` in production) upon successful verification.
  4. Provide a `/logout` action and clear session lifecycle management.

---

## E. Frontend Architecture Audit

* **Monolithic Single-Page Structure:**
  * `web/app/page.tsx` is a ~40 KB file rendering 17 distinct domain workspaces simultaneously in a single server component.
  * Navigation is purely internal anchor links (`#signals`, `#risk`, `#portfolio`, etc.).
* **Absence of Component Abstraction:**
  * Low-level HTML (`<article>`, `<dl>`, `<dt>`, `<dd>`, `<table>`, `<tr>`, `<td>`) is copy-pasted across every workspace section.
  * No shared component library exists for common patterns: `EvidenceCard`, `DataTable`, `StatusPill`, `ProvenanceDetails`, `EmptyState`, `MetricGrid`.
* **Visual & UX Deficiencies:**
  * Rudimentary styling with basic monospace table formatting.
  * Lack of interactive filtering, searching, sorting, or pagination.
  * No visual charting for time-series, return distributions, risk attribution, or regime probabilities.
* **Recommended Target Route Structure (Module 2+):**
  ```text
  /dashboard
  /markets
  /instruments
  /data-health
  /features
  /signals
  /risk
  /strategies
  /backtests
  /scorecards
  /regimes
  /portfolio
  /investments
  /news
  /paper
  /operations
  /audit
  ```

---

## F. Backend Health & System Inventory

| System | Status | Classification | Notes |
| :--- | :--- | :--- | :--- |
| **PostgreSQL Persistence & Schema** | PASS | Preserve / Core | Point-in-time tables, immutable triggers, audit events. |
| **Feature Authority & Platform** | PASS | Preserve / Core | PIT materializations, version bounds, feature families. |
| **Trend Strategy & Research V2** | PASS | Preserve / Core | Deterministic signals, research vs paper execution boundary. |
| **Strategy Scorecard V2** | PASS | Preserve / Core | Multi-dimensional evidence, zero opaque aggregate metrics. |
| **Investment Engine V2** | PASS | Preserve / Core | Fundamental theses, catalysts, invalidation rules. |
| **Regime Engine V2** | PASS | Preserve / Core | Dimension models, fail-closed risk reduction candidates. |
| **Portfolio Construction V2** | PASS | Preserve / Core | Target allocations, covariance, constraints, risk gates. |
| **News & Narrative Intelligence** | PASS | Preserve / Core | Correction-aware lineage, entity linking, source policies. |
| **Paper OMS & Reconciliation** | PASS | Preserve / Core | Intent lifecycle, execution quality, shadow reconciliation. |
| **Security, SBOM & Attestations** | PASS | Preserve / Core | Non-root container, Bandit, pip-audit, provenance sigstore. |
| **Operator Dashboard Query Layer** | PASS | Recovered / Clean | Canonical decimal serialization, read-only PIT projections. |
| **Data Discovery APIs** | INCOMPLETE | Needs Integration | Requires list/search/latest endpoints to avoid UUID config. |
| **Live Brokers & External Feeds** | DISABLED | Strictly Blocked | No live brokers, real money, or unapproved feeds. |

---

## G. Documentation Drift

1. **Repository Visibility:** `README.md` describes the project as `"Private"`, but the GitHub repository is configured as **`PUBLIC`**.
2. **Cycle Numbering:** Documentation frequently references incremental Codex cycle labels (e.g. Cycle 208, 217, 237) instead of modular product capabilities.
3. **Hardcoded Evidence Assumptions:** Documentation assumes disposable test containers and fixture scripts rather than documenting standard human operator onboarding.

---

## H. Module 1 Plan: Runnable Local Product & Safe Dev Auth

### Objective
Enable any developer or operator to clone the repository, run a single setup/dev command, authenticate safely via a browser UI without tokens in URLs, and immediately interact with a fully populated dashboard.

### Acceptance Criteria
1. **Local Compose / Bootstrap:**
   * `docker-compose.yml` (or `make dev` / `scripts/dev.ps1`) starting PostgreSQL, applying migrations, running the FastAPI backend, and running the Next.js frontend.
2. **Deterministic Demo Seed:**
   * Idempotent seed script (`scripts/seed_demo_evidence.py`) generating a complete, coherent dataset (instruments, features, signals, scorecards, regime runs, portfolio runs, theses, paper orders, SRE metrics).
3. **Safe Browser Authentication:**
   * Dedicated `/login` page and form.
   * Removal of `?token=` query parameter credential handling.
   * `HttpOnly` session cookie with secure attributes and a `/logout` flow.
4. **Auto-Discovery of Latest Workspaces:**
   * The dashboard automatically displays the latest available evidence without requiring hardcoded UUIDs in `dashboard.config.json`.
5. **Quality Gates:**
   * 100% green CI on GitHub Actions with all security, lint, test, container, and E2E gates passing.
