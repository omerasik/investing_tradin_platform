# Trade Investing Panel

Private, safety-first investment intelligence and research platform.

> **SAFETY MANDATE:** This system is strictly research and paper-only. Live trading is hard-disabled. No broker execution credentials, real funds, or live order routing paths exist in this stack.

---

## Quick Start (Local Development)

### 1. Prerequisites
- **Docker Desktop** (or Docker Engine with Compose v2)
- **Python 3.12**
- **Node.js 20+** & **pnpm 10+** (or npm)

### 2. Environment Configuration
Copy the template configuration:
```bash
cp .env.example .env
```
*(Windows PowerShell: `Copy-Item .env.example .env`)*

Default local credentials:
- **Operator Dashboard Password / Secret**: `local-dev-operator-secret-token`
- **Dashboard URL**: `http://localhost:3000`
- **FastAPI Backend URL**: `http://localhost:8000`
- **PostgreSQL Port**: `5439` (default chosen to avoid host 5432 port collisions)

### 3. One-Command Launch
Launch the complete stack (PostgreSQL, schema migrations, FastAPI backend, Next.js dashboard):

- **Linux / macOS**:
  ```bash
  ./scripts/dev.sh
  ```
- **Windows (PowerShell)**:
  ```powershell
  .\scripts\dev.ps1
  ```
- **Cross-Platform (Python)**:
  ```bash
  python scripts/dev.py
  ```

Once healthy, open `http://localhost:3000` in your browser. You will be redirected to the secure login page (`/login`). Sign in with your configured password (`local-dev-operator-secret-token`).

---

## Safe Browser Authentication

The dashboard uses a secure server-side session cookie architecture:
- **HttpOnly & SameSite=Lax**: The session token (`dashboard_session`) is signed using HMAC-SHA256 and stored in an HttpOnly cookie with strict 8-hour expiry.
- **Zero Secrets in URLs**: Query string authentication tokens (such as `?token=...`) are rejected.
- **Zero Secrets in DOM**: Backend operator bearer tokens are held exclusively by the Next.js server runtime and are never reflected to browser JavaScript, DOM attributes, or `localStorage`.
- **Sign Out**: A header "Sign Out" button securely invalidates the session cookie and redirects to `/login`.
- **Fail-Closed**: In production mode (`NODE_ENV=production`), default fallback secrets are rejected at startup.

---

## Stopping and Resetting

- **Graceful Shutdown**:
  Press `Ctrl+C` in the terminal running `scripts/dev.py`. Child processes (FastAPI and Next.js) will terminate cleanly while preserving PostgreSQL data on disk.
- **Destructive Database Reset**:
  To wipe local development state and rebuild the database from scratch:
  ```bash
  python scripts/dev.py --reset-db
  ```

---

## Troubleshooting

| Issue | Resolution |
| :--- | :--- |
| **Docker daemon not running** | Start Docker Desktop or verify `docker info` succeeds before running `scripts/dev.py`. |
| **Port conflict on 5439, 8000, or 3000** | Pass custom ports: `python scripts/dev.py --postgres-port 5440 --api-port 8001 --port 3001`. |
| **Missing Python dependencies** | Run `.venv\Scripts\python -m pip install -e ".[dev]"` (Windows) or `source .venv/bin/activate && pip install -e ".[dev]"` (Unix). |
| **Missing Frontend dependencies** | Run `cd web && pnpm install`. |

---

## Architecture & Verification

The authoritative requirements are recorded in `docs/MASTER_ROADMAP.md`. Technical details for Module 1A are in `docs/MODULE_1A_RUNNABLE_LOCAL_AUTH.md`.

To run quality gates manually:
```bash
# Python quality gates
python -m compileall -q src tests migrations scripts
ruff check src tests migrations scripts
python scripts/check_mypy_baseline.py
bandit -q -r src/trade_platform
detect-secrets-hook --baseline .secrets.baseline $(git ls-files -- . ':!.secrets.baseline')
python -m unittest discover -s tests -v

# Frontend quality gates (in web/)
pnpm exec tsc --noEmit
pnpm lint
pnpm test:session
pnpm audit --audit-level high
pnpm exec next build
```
