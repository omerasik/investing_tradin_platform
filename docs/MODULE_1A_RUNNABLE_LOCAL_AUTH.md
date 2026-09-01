# MODULE 1A — RUNNABLE LOCAL STACK & SAFE BROWSER AUTHENTICATION

## Executive Summary

Module 1A establishes a canonical, fail-fast local development stack and replaces insecure browser authentication patterns (query string tokens, URL reflection) with a tamper-resistant, cryptographic session cookie layer.

This implementation satisfies all security mandates:
- **Live Trading Hard-Disabled**: No real brokers, live execution credentials, or order routing pathways exist.
- **Safe Browser Authentication**: HttpOnly, SameSite=Lax, HMAC-SHA256 signed session cookies (`dashboard_session`) with 8-hour expiry and constant-time signature verification.
- **Zero Secrets in URLs**: Query string token parsing (`?token=...`) has been entirely removed from the proxy middleware and route handlers.
- **Zero Secrets in DOM**: Backend operator bearer tokens remain exclusively within the server runtime; no secrets are emitted in HTML markup, DOM data attributes, or client storage (`localStorage` / `sessionStorage`).
- **Fail-Closed Architecture**: Production environments (`NODE_ENV=production`) reject default dev passwords and require explicit cryptographic configuration.

---

## Key Deliverables & Architecture

### 1. Docker Compose Dev Manifest (`compose.dev.yml`)
- Provides a containerized PostgreSQL 16 Alpine database (`trade-platform-postgres-dev`).
- Uses port `5439` by default (`${POSTGRES_PORT:-5439}:5432`) to avoid port collisions with host PostgreSQL services (5432).
- Mounts a named persistent volume (`trade_platform_dev_pgdata`).
- Includes a container healthcheck using `pg_isready -U postgres -d trade_platform`.

### 2. Cross-Platform Orchestrator (`scripts/dev.py`, `scripts/dev.ps1`, `scripts/dev.sh`)
- Single-command orchestration for PostgreSQL container, Alembic schema migrations, FastAPI backend (`src/trade_platform/dev_app.py`), and Next.js frontend (`web/`).
- **Fail-Fast Prerequisite Checks**:
  - Python 3.12+ and required module imports (`fastapi`, `uvicorn`, `psycopg`, `alembic`).
  - Docker daemon availability and Docker Compose v2.
  - Node.js 20+ and pnpm / npm presence.
  - Port conflict detection on PostgreSQL, FastAPI, and Next.js ports before starting services.
- **Process Management & Teardown**:
  - Clean `Ctrl+C` signal handling terminating child processes while leaving the database volume intact.
  - `--reset-db` CLI flag for destructive volume cleanup and fresh migration replay.

### 3. Cryptographic Session Layer (`web/app/session.ts`)
- Standard Web Crypto HMAC-SHA256 signing and verification (`crypto.subtle.sign` / `crypto.subtle.verify`).
- Timestamped payload with 8-hour expiry validation.
- Constant-time password comparison using `node:crypto.timingSafeEqual` to prevent timing attacks.
- Secure cookie builder enforcing `HttpOnly`, `SameSite: "lax"`, `Path: "/"`, `Max-Age: 28800`.
- Dedicated standalone unit test suite `web/scripts/test-session.mjs` verifying generation, verification, expiration, tampering, and constant-time behavior.

### 4. Safe Browser Authentication Flow
- **`web/app/login/page.tsx`**: Accessible, semantic Operator Sign In interface displaying explicit "LIVE TRADING: DISABLED" and "RESEARCH / PAPER ONLY" badges.
- **`web/app/api/auth/login/route.ts`**: Validates operator password in constant time and sets the signed `dashboard_session` cookie.
- **`web/app/api/auth/logout/route.ts`**: Clears the session cookie and supports both API JSON responses and HTML form redirect responses.
- **`web/app/page.tsx`**: Includes a top-level header "Sign Out" button.
- **`web/proxy.ts` (Next.js Middleware)**:
  - Removed query string token parsing (`?token=...`).
  - Enforces session cookie or Bearer token authorization.
  - Unauthenticated page requests redirect to `/login`.
  - Unauthenticated `/api/*` requests fail with `401 Unauthorized`.
  - Enforces per-request dynamic CSP nonces with `strict-dynamic`.

---

## Quality Gates & Verification Matrix

All repository verification commands execute with zero errors:

| Tool / Check | Scope / Target | Result | Notes |
| :--- | :--- | :--- | :--- |
| **Python Compileall** | `src`, `tests`, `migrations`, `scripts` | **PASS** | Bytecode compilation clean |
| **Ruff** | `src`, `tests`, `migrations`, `scripts` | **PASS** | Zero lint or formatting warnings |
| **Complete-Package Mypy** | `src/trade_platform` | **PASS** | 117/117 baseline maintained (0 regressions) |
| **Critical Mypy Slice** | 48 source files | **PASS** | Strict typing clean |
| **Bandit** | `src/trade_platform` | **PASS** | AST security scanner clean |
| **Detect-Secrets** | All tracked git files | **PASS** | Zero high-entropy secret patterns |
| **Frontend TypeScript** | `web/` (`tsc --noEmit`) | **PASS** | 0 TypeScript compilation errors |
| **Frontend ESLint** | `web/` (`pnpm lint`) | **PASS** | 0 ESLint warnings/errors |
| **Session Unit Tests** | `web/scripts/test-session.mjs` | **PASS** | 100% assertions pass |
| **Frontend Build** | `web/` (`next build`) | **PASS** | Static and dynamic route compilation clean |
| **Python Unit Tests** | `tests/` (`unittest discover`) | **PASS** | 467 tests pass |
| **Playwright E2E** | `web/e2e/auth-flow.spec.ts` | **PASS** | Human browser login, logout, tamper rejection |

---

## Safety Boundary Declaration

1. **Live Trading**: Remains strictly disabled across all components (`live_trading_enabled: False`).
2. **Secrets Storage**: Server secrets (`TRADE_PLATFORM_OPERATOR_TOKEN`, `TRADE_PLATFORM_DASHBOARD_SECRET`) are never persisted in the browser or exposed across HTTP headers to untrusted clients.
3. **No External Outbound Calls**: The local stack runs entirely within the developer environment without connecting to third-party brokerage APIs.
