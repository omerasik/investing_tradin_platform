# trade-investing-panel — Claude Code operating guide

Private, safety-first investment **research** platform: a Python modular monolith
(`src/trade_platform/`) with Alembic-managed PostgreSQL authorities, a protected
FastAPI control plane, and a Next.js operator dashboard (`web/`).

Claude Code is the only coding agent used here. Keep this file small; put detail in
module docstrings and in the on-demand docs listed below.

## Non-negotiable product boundary

Research and paper-trading only. Never weaken and never route around:

- `live_trading_enabled=True` is unconditionally rejected in
  `src/trade_platform/config.py`. No live order routing, no broker execution
  credentials, no real funds — not via a new env var, config alias or default.
- Point-in-time correctness and look-ahead protection, including the two-clock
  (knowledge time vs effective time) split where the code already models it.
- Immutable evidence, content/dataset identity hashes, idempotency keys, audit
  provenance, risk controls, kill switches.
- Deterministic financial maths: `Decimal`, quantized to its `NUMERIC(p,s)` column
  scale **before** hashing or persisting.
- Fail closed when evidence is missing or ambiguous. Never default to
  "ready"/"acceptable", never invent an economic assumption, never add a default
  risk parameter that carries economic meaning without explicit authorization.
- Provider-*published* semantics stay separate from *inferred/calculated* ones.
  External and provider data is untrusted input.
- Research evidence is never executable authority.

**Public market data is not live trading.** The authorized public Bybit V5
historical acquisition path is in scope. Provider credentials, paid data,
broker/account APIs and order placement stay separately gated and fail-closed.

Solve the invariant, never the fixture. If a check is hard to pass, fix the cause —
do not special-case a test, relax a gate, or weaken CI.

On-demand policy docs, read only when the current task touches them:
`SECURITY.md`, `CONTRIBUTING.md`, `docs/RISK_MANAGEMENT_POLICY.md`,
`docs/EXECUTION_POLICY.md`, `docs/BACKTESTING_STANDARD.md`,
`docs/QUANT_RESEARCH_STANDARD.md`, `docs/LIVE_TRADING_READINESS.md`.

## Start here (context policy)

1. This file.
2. `docs/current-phase.yaml` — the cheap current-task pointer.
3. `git status` / `git diff`.
4. The scoped source file and its tests.

Expand beyond that only for a concrete dependency, failing test, interface or
policy. Do not routinely read the whole docs set, recurse through `tests/`, scan
`migrations/`, or open unrelated providers and engines.

Two large documents are **not** default reading:

- `docs/MASTER_ROADMAP.md` (352 KB) — authoritative for requirements, but ~80 % is
  historical per-cycle evidence. Read a specific section, never the file.
- `docs/CURRENT_STATUS.md` (59 KB) — a dated snapshot frozen at 2026-08-31 and
  superseded. Treat as history; do not quote its counts as current.

Navigation here is mechanical, so prefer Glob/Grep over any index:

- `src/trade_platform/<name>.py` ↔ `tests/test_<name>.py` (+ `tests/test_<name>_postgres.py`).
  This holds for 111 of 126 modules; the rest are near-prefix, e.g.
  `security.py` → `tests/test_security_roles.py`.
- The authoritative design notes are the **module docstrings**, not `docs/`.
- `graphify-out/` is gitignored, stale (built 2026-09-16, missing the newest
  modules) and not authoritative in this repository. A parent-directory
  `CLAUDE.md` and a user-level hook ask for it; it is not required here.

## Phase naming — three schemes coexist

Disambiguate before assuming. `Phase 3B`/`3C` in recent commits (historical
acquisition, scheduled acquisition) is **not** `docs/MODULE_3B_GOVERNANCE_SECURITY.md`
or `docs/MODULE_3C_POSTGRES_RUNTIME_WIRING.md`, and **not** the roadmap's own
`3G`/`3H`/`3I`/`3J` series. `docs/current-phase.yaml` states which is current.

## Validation

Measured on this repository with warm caches. The whole local gate is ~90 s, so
there is no reason to skip it before claiming a change is done.

| Check | Command | Time |
| :--- | :--- | :--- |
| One test module | `python -m unittest tests.test_<name>` | ~0.7 s |
| Lint | `ruff check src tests migrations scripts` | ~0.7 s |
| Byte-compile | `python -m compileall -q src tests migrations scripts` | ~0.3 s |
| Full Python suite | `python -m unittest discover -s tests` | ~73 s (1708 tests) |
| mypy ratchet | `python scripts/check_mypy_baseline.py` | ~4 s |
| SAST | `bandit -q -r src/trade_platform` | ~6 s |
| Frontend types | `cd web && pnpm exec tsc --noEmit` | ~2 s |
| Frontend units | `cd web && pnpm test:session && pnpm test:workspace` | <1 s |

On this machine the interpreter is `.venv\Scripts\python`.

**While iterating:** the single relevant test module, plus `tsc --noEmit` for web
changes.

**Before claiming a change is done:** `ruff` + the full Python suite (and the
frontend units if `web/` changed). Add `check_mypy_baseline.py` and `bandit` when
the change touches typing-critical, security, secrets, execution or provider
boundary code.

**PostgreSQL tests** need `POSTGRES_TEST_DSN`; without it 185 tests skip and the
suite still reports OK. Set it for any persistence, migration or repository
change. A shared database makes some tests assert global invariants — new fixtures
must use past timestamps, persist no `REJECTED` rows, and not duplicate
feature-definition names.

**Leave to CI** (`.github/workflows/verify.yml`) — do not run these in the
implementation loop: Docker build and hardened-container checks, Trivy scan and
SBOM, image attestations, `pip-audit`, `pg_dump`/`pg_restore` reconciliation
drill, staging Compose bring-up, `next build`, `pnpm audit`, and Playwright E2E.

If final validation fails: diagnose, make one focused fix, rerun only the failing
check. One further focused attempt if justified, then stop and report the blocker.
Do not rerun expensive checks the fix could not have invalidated.

## Workflow

Branch → PR → CI → merge, per `CONTRIBUTING.md`. No direct commits to `main`. After
merging a module-level change, re-verify CI green on the exact resulting `main`
commit before calling the module complete.

Migrations in `migrations/versions/` are additive where possible and are never
edited after merge — add a new migration instead. Use explicit column lists in raw
SQL; positional `INSERT`s break silently after an `ADD COLUMN`.

## Scope and stop rules

- Stay inside the scope paths in `docs/current-phase.yaml`.
- Do not broaden scope after a failure, and do not clean up unrelated files.
- Do not start the next module after the current one completes — stop and report.
- Do not make external provider, broker or paid-data calls.
- Do not create per-phase completion reports; git history and the roadmap already
  carry that evidence. Avoid adding Markdown files.

## Subagents

Default is **one Claude instance** per scoped module. The flat module tree and
mechanical test mapping make exploration cheap, so fan-out rarely pays here.

Use a subagent only for genuinely independent work: security review, a large
migration review, independent PIT/financial-correctness review of a major
algorithmic change, or a frontend/backend split with no overlapping files. Not for
exploration, small bugs, ordinary provider changes, tests, docs or refactors.

Use stronger reasoning for PIT/financial-correctness and architectural debugging;
keep routine coding and docs on the default model and effort.
