# MODULE 2B-2 — Professional Research, Backtest, Scorecard & Signal Workspaces Documentation

## 1. Overview & Objectives

Module 2B-2 transforms the four transitional research views (`/strategies`, `/backtests`, `/scorecards`, `/signals`) into coherent professional quant-research workspaces, completing the narrative `Strategy → Research Experiment → Validation → Scorecard → Signal`. Each workspace pairs a bounded, filterable discovery table with a deep inspector, sourced entirely from existing PostgreSQL authorities. No page in this module submits an order, activates live execution, contacts a broker, or converts a signal into an order.

### Core System Invariants Maintained
- **LIVE TRADING: DISABLED** — enforced globally via the top bar badge on every page in this module.
- **Strategy Signal != Order / Research != Execution / AI != Risk Authority** — the Signal Explorer carries an explicit `SIGNAL ≠ ORDER · NO EXECUTION AUTHORITY` banner; no page exposes an execute/trade/buy/sell/order control.
- **Synthetic Evidence != Live Market Evidence** — every strategy, experiment, scorecard, and signal row now carries a backend-computed `evidence_classification` (`SYNTHETIC_ENGINEERING_EVIDENCE_ONLY` / `REAL_DATA_RESEARCH_EVIDENCE` / `UNAVAILABLE`), never a client-side guess.
- **No opaque aggregate score** — the Scorecard workspace preserves the existing per-metric evidence-state model (`MEASURED` / `ASSUMED` / `UNAVAILABLE`) and adds only a presentation-level coverage count, not a new authoritative score.

---

## 2. Tech Debts Resolved from Module 2A / 2B-1

### Tech Debt 1: Dishonest evidence classification
`strategies()` and `experiments()` previously hardcoded an authority-disclaimer string (`"RESEARCH_ONLY; NO_LIVE_OR_EXECUTION_AUTHORITY"`) in the `evidence_classification` field — a field that is supposed to answer "is this evidence synthetic or real," not "does this grant execution authority." `strategy_scorecard()` computed classification via an inline, one-off heuristic with the wrong vocabulary (`RESEARCH_EVIDENCE_ONLY` instead of `REAL_DATA_RESEARCH_EVIDENCE`), and `signals()` had no classification field at all. Replaced all four with a single shared helper, `classify_research_evidence()` in `operator_dashboard.py`.

**Module 2B-2.1 correction (see §3.1 below):** the shared helper's first version was fail-open — absence of a demo/synthetic marker was (incorrectly) enough to return `REAL_DATA_RESEARCH_EVIDENCE`. That defect is fixed; `classify_research_evidence()` is now fail-closed and requires positive, persisted real-provider proof.

### Tech Debt 2: Duplicated signal lifecycle timeline markup
The signal lifecycle `<details><ol>` block was hand-duplicated between `signals/page.tsx` and `dashboard/page.tsx`. Extracted into `SignalLifecycleTimeline` (`web/app/components/signal-lifecycle-timeline.tsx`), now the single source for both.

### Tech Debt 3 (pre-existing, closed): `test:e2e:module2b1` never wired into CI
`verify.yml` already ran `test:e2e:module1b` and `test:e2e:module2a` but never invoked `test:e2e:module2b1`, even though the script existed in `package.json`. Both `module2b1` and `module2b2` are now invoked in the same CI step.

---

## 3. Backend Read-Only Endpoints & Queries

All changes are read-only, authenticated, bounded, deterministic, and PIT-safe.

1. **`GET /operator-dashboard/strategy-scorecards`** (new): the only genuinely missing discovery endpoint — scorecards previously had no list route, only single-record `GET /operator-dashboard/strategy-scorecards/{id}`. Filters: `strategy_id`, `status` (`BLOCKED` | `REVIEW_REQUIRED`), bounded `limit`/`offset`.
2. **`GET /operator-dashboard/strategies?family=`**: added an exact-match `family` filter (previously unfilterable).
3. **`GET /research/strategies/{id}`** and **`GET /research/experiments/{id}`**: now include `evidence_classification`, matching the field already present on the discovery views.
4. **`classify_research_evidence(...)`** (`operator_dashboard.py`): the single source of truth for synthetic-vs-real classification, applied consistently across `strategies()`, `experiments()`, `strategy_scorecard()`, `strategy_scorecards()`, and `signals()`.

---

### 3.1 Module 2B-2.1 — Evidence classification truthfulness patch

**The defect.** The original `classify_research_evidence()` was a fail-open string-marker heuristic: it scanned dataset version/limitations/instrument-ID text for `demo`/`synthetic`/`fixture`/`module1b`, and returned `REAL_DATA_RESEARCH_EVIDENCE` whenever *none* of those markers were present. Absence of a marker is not proof of real data — a realistic-looking identifier with no marker (e.g. the `cycle208` Postgres integration test's fixture instrument `US:XNYS:C208-*` and its `trend-cycle208-v1` signal) was incorrectly classified `REAL_DATA_RESEARCH_EVIDENCE`. This was a real correctness/truthfulness defect, not documented, expected behavior — it has been fixed.

**The rule now.** `classify_research_evidence()` takes three booleans — `synthetic_provenance`, `real_data_provenance_verified`, `lineage_complete` — and is fail-closed:

```text
synthetic_provenance == True                               -> SYNTHETIC_ENGINEERING_EVIDENCE_ONLY
real_data_provenance_verified == True AND lineage_complete  -> REAL_DATA_RESEARCH_EVIDENCE
otherwise                                                   -> UNAVAILABLE
```

Every call site resolves these booleans from **positive, persisted, foreign-key-joined provenance** — the `datasets.provider` / `historical_data_sources.provider` column reached by actually walking `strategy_versions` / `research_experiments` / `validation_packages` / `strategy_scorecards` → `dataset_versions` → `datasets` — never from a dataset name, instrument symbol, UUID, or any other free-text identifier. `real_data_provenance_verified` additionally requires the resolved provider to be on `_AUTHORIZED_REAL_MARKET_DATA_PROVIDERS`, an explicit allowlist in `operator_dashboard.py` that is **deliberately empty**: no real market-data provider is authorized or activated on this platform, so no research object can classify as `REAL_DATA_RESEARCH_EVIDENCE` today, by construction — not by convention. String markers (`demo`/`synthetic`/`fixture`/`module1b`) remain as a *defensive* synthetic detector layered on top of that persisted provenance (a validation package or strategy contract that explicitly declares itself synthetic still wins), but they are never sufficient on their own to prove real provenance.

A legacy, non-Postgres research surface (`/research/strategies/{id}`, `/research/experiments/{id}` in `api.py`, backed by an in-memory `StrategyRunCard`/`ResearchExperiment` registry with no dataset-provider table at all) has no lineage to positively verify, so it uses `classify_research_evidence_from_markers()` — the same fail-closed rule with `lineage_complete` always `False`, so it can only ever return `SYNTHETIC_ENGINEERING_EVIDENCE_ONLY` or `UNAVAILABLE`, never `REAL_DATA_RESEARCH_EVIDENCE`.

**Validation status is not data provenance.** A signal's lifecycle `VALIDATED` status and its `evidence_classification` are computed independently — a fully validated signal built entirely on synthetic data still classifies `SYNTHETIC_ENGINEERING_EVIDENCE_ONLY`.

**Current platform real-provider state: NO REAL PROVIDER ACTIVATED.** Every Module 1B seeded demo object (strategies, experiments, scorecards, signals) therefore classifies `SYNTHETIC_ENGINEERING_EVIDENCE_ONLY`, confirmed by `tests/test_operator_dashboard_postgres.py` and `web/e2e/module2b2-research.spec.ts`. Test coverage (`tests/test_classify_research_evidence.py`, `tests/test_operator_dashboard_postgres.py`) exercises all three outcomes: synthetic fixture lineage, unknown/incomplete provenance (a realistic, marker-free provider name that is simply not on the allowlist — `UNAVAILABLE`, not `REAL_DATA_RESEARCH_EVIDENCE`), and classifier semantics for positive real provenance (via a temporarily patched allowlist, proving the mechanism without claiming any provider is actually authorized).

---

## 4. Shared Research Component Library

**Reused unchanged**: `DataTable`, `Pagination`, `FilterBar`/`SearchField`, `WorkspaceToolbar`, `KeyValueGrid`, `ProvenancePanel`, `EvidenceStateBadge`, `QualityStateBadge`/`StatusBadge`, `SafetyBanner`, `PageHeader`.

**New in `web/app/components/`**:
- **`ResearchStatusBadge`**: renders the backend's `evidence_classification` string verbatim, styled by keyword (never rewrites or infers the text).
- **`StrategyIdentity`**: consistent strategy id/version/family chip, reused across strategies, backtests, and scorecards rows.
- **`ParameterTable`**: renders a strategy's `parameter_schema` or an experiment's `parameters` as a key/value table; shows "No parameters recorded" when empty.
- **`SignalLifecycleTimeline`**: extracted, de-duplicated lifecycle timeline (see Tech Debt 2).
- **`ContradictionPanel`**: dedicated panel for `contradicting_evidence`, always rendering "None recorded" rather than hiding an empty state.

---

## 5. Workspaces Implementation

### 5.1 Strategy Laboratory (`/strategies`)
- Discovery table (`getStrategyDiscovery`) with an exact-match family filter, `StrategyIdentity`, `ResearchStatusBadge`, and pagination.
- Strategy Inspector: separates **Economic Hypothesis** from **Implementation Rules** (entry/exit/sizing/risk/capacity/universe), a dedicated **`WHEN SHOULD THIS STRATEGY NOT WORK?`** callout for `failure_conditions`, and a `ParameterTable` for `parameter_schema`.
- **Graceful degradation**: the deep single-record detail route (`/research/strategies/{id}`) depends on a legacy `StrategyRunCard` registry that is not wired into the Postgres-backed demo/CI fixture server. When that detail fetch is unavailable, the inspector still renders everything available from the discovery row (hypothesis, family, version, datasets, features, cost model, evidence classification) and explicitly marks the deeper implementation-rule fields `UNAVAILABLE` — rather than collapsing to a single opaque error line. A visible notice explains when this fallback is active.
- `StrategyCreator` isolated in a `Create Research Strategy` panel labeled `RESEARCH ONLY · NO EXECUTION AUTHORITY · NO AUTOMATIC PROMOTION`. Removed the client-side `family.toLowerCase().includes("trend")` classification heuristic entirely.

### 5.2 Backtest & Validation Workspace (`/backtests`)
- Discovery table (`getExperimentDiscovery`) filterable by strategy via a bounded dropdown populated from `getStrategyDiscovery` (no fabricated distinct-values endpoint).
- Experiment Inspector: `ParameterTable` for `parameters`; Performance/Validation/Cost Model sections render only actual persisted `report` values, `UNAVAILABLE` for anything missing; Promotion Decision section shows `BLOCKED`/`REVIEW_REQUIRED` — never an `AUTO_APPROVED LIVE` state.
- Same graceful-degradation pattern as Strategies for when `/research/experiments/{id}` is unavailable — falls back to the discovery row for identity/dataset/cost-model fields, explicit `UNAVAILABLE` for report-only fields (parameters, performance, validation).
- `ResearchLauncher` isolated in a `Run Research Experiment` panel labeled `RESEARCH ONLY · NO LIVE ACTION`.

### 5.3 Strategy Scorecard V2 (`/scorecards`)
- The biggest structural change: previously single-record only. Now a full discovery table (new `getScorecardDiscovery`) filterable by status, backed by the new `/operator-dashboard/strategy-scorecards` endpoint.
- Scorecard Inspector preserves the existing grouped metric matrix (`PERFORMANCE`/`ROBUSTNESS`/`EXECUTION`/`RISK`/`DATA_QUALITY`/`SIGNAL_DECAY`) and complexity components unchanged, and adds an **Evidence Coverage Summary** — a presentation-level count of MEASURED/ASSUMED/UNAVAILABLE metrics computed from the loaded scorecard only, explicitly not a new authoritative score.
- **Known limitation**: multi-scorecard side-by-side comparison (spec section 3, "Scorecard Comparison") was not implemented in this module — the discovery table and inspector are complete, but comparing 2-3 scorecards side-by-side is deferred. No backend gap blocks it (the new discovery endpoint already bounds the candidate set); it is a frontend-only follow-on.

### 5.4 Signal Explorer (`/signals`)
- Discovery table (new `getSignalDiscovery`, finally wiring the `status`/`instrument`/`strategy_version` filters the backend already supported) with `ResearchStatusBadge` per row.
- Per-row `SignalLifecycleTimeline` and dedicated `ContradictionPanel`.
- Prominent `SIGNAL ≠ ORDER · NO EXECUTION AUTHORITY` safety banner. Strictly read-only — no lifecycle-mutation control exists anywhere on the page.

### Visualization — explicitly skipped
`ResearchExperiment.report` (`research.py`) contains only scalar aggregates (`total_return`, `sharpe`, `max_drawdown`, etc.) as strings; no equity curve or period-return series is ever persisted. Scorecard metrics are likewise point values. Per the module's explicit instruction not to fabricate a series, no chart was added in this module — this is a deliberate decision, not an oversight.

---

## 6. Dashboard Monolith Cleanup

`/dashboard`'s `#strategy` and `#backtest` sections were reduced from full inline `<dl>`/`<table>` detail to a one-line summary (bound strategy/experiment when available, else discovered-count-and-headline from the Postgres discovery data) plus an `Open {X} Workspace →` link — the same pattern already established for Markets/Instruments/Data Health/Features.

**`#scorecard` and `#signals` were deliberately left at full detail.** The pre-existing `cycle208.spec.ts` acceptance test asserts the full scorecard metric-group headings (`PERFORMANCE`, `ROBUSTNESS`, ...) and full signal lifecycle detail (including `all_validation_stages_passed`) directly inside those two dashboard sections. Simplifying them would have broken that old, must-not-break test. Given the explicit project rule that all Module 1A/1B/2A/2B-1 tests must remain green, these two sections keep their existing full-detail rendering; only their footer link text was upgraded to `Open Scorecard Workspace →` / `Open Signal Workspace →` for consistency. This is a documented, intentional exception to the "reduce all four domains equally" goal, not an oversight.

`web/e2e/module1b-demo.spec.ts`'s `#strategy` assertion was updated from checking for the now-removed hypothesis substring (`"Trend V2 synthetic"`) to checking for the still-present, arguably more informative `evidence_classification` substring (`"SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"`) — an intentional update to a legitimately-changed section, per the module's explicit allowance for this.

---

## 7. Test Coverage

- **`web/e2e/module2b2-research.spec.ts`**: one `test.describe` block, four tests (`/strategies`, `/backtests`, `/scorecards`, `/signals`), each following the `module2b1-market-data.spec.ts` pattern — login, navigate, assert heading, assert key evidence text, assert zero execute/trade/buy/sell/order controls, `AxeBuilder` WCAG 2.0/2.1 A/AA scan, zero console errors. **(Module 2B-2.1)** every test now asserts `SYNTHETIC_ENGINEERING_EVIDENCE_ONLY` specifically for Module 1B demo-derived objects and asserts zero occurrences of `REAL_DATA_RESEARCH_EVIDENCE` on the page — previously it accepted synthetic, real, or unavailable indiscriminately, which was too permissive to catch the fail-open defect described in §3.1.
- **`tests/test_operator_dashboard_postgres.py`**: extended with coverage for the `family` filter on `strategies()`, the new `strategy_scorecards()` discovery method (strategy_id filter, status filter, wrong-status-returns-empty), and confirmed `signals()` now returns `evidence_classification`. **(Module 2B-2.1)** the `cycle208` fixture signal assertion was corrected from `REAL_DATA_RESEARCH_EVIDENCE` to `SYNTHETIC_ENGINEERING_EVIDENCE_ONLY` (see §3.1), and a new `test_evidence_classification_fails_closed_without_authorized_real_provider` test proves, against real PostgreSQL lineage, that a realistic marker-free provider name resolves to `UNAVAILABLE` while unauthorized, and to `REAL_DATA_RESEARCH_EVIDENCE` only once explicitly added to the allowlist.
- **`tests/test_classify_research_evidence.py`** (new, Module 2B-2.1): pure-Python unit coverage of the classifier's full boolean contract, marker detection, the empty real-provider allowlist, and `classify_research_evidence_from_markers()`.
- **`tests/test_operator_dashboard_api.py`** and **`tests/test_module1b_demo_acceptance.py`**: updated call sites for the new required `family` keyword argument on `strategies()`.

---

## 8. Verification Results

### Automated Quality Gates
1. **Python Unit Tests**: 475 passed, 52 skipped (Postgres-only tests skip without `POSTGRES_TEST_DSN`) — `.\.venv\Scripts\python.exe -m unittest discover -s tests`. Also verified clean against a freshly migrated, disposable PostgreSQL database (all 475 pass, 0 skipped for Postgres-backed tests).
2. **Python Linting**: Ruff clean — `.\.venv\Scripts\python.exe -m ruff check src tests scripts`.
3. **Python Typing**: Mypy clean on the two touched files (`operator_dashboard.py`, `api.py`) and the complete-package regression ratchet holds at its existing 117/117 baseline (no new errors) — `python scripts/check_mypy_baseline.py`.
4. **Security**: Bandit clean (`bandit -q -r src/trade_platform`, exit 0).
5. **TypeScript & Linter**: Zero `tsc --noEmit` errors, zero ESLint warnings (`npx eslint .`).
6. **Production Build**: Clean Next.js 16 build (`next build`).
7. **Frontend Tests**: `test:session` and `test:workspace` passing.
8. **Playwright E2E**: `module1b-demo`, `module2a-app-shell`, `module2b1-market-data` (4 tests), and the new `module2b2-research` (4 tests) all pass against a live, seeded dashboard — 10/10 green, including WCAG 2.0/2.1 A/AA scans and zero-execution-control assertions.
9. **Seed data**: `scripts/seed_demo_evidence.py` extended with a second, independent research scenario (`MEAN_REVERSION` strategy, second experiment, second scorecard with `BLOCKED` status, second candidate signal) purely additively (`ON CONFLICT DO NOTHING`, new deterministic UUIDs) — the primary Module 1B demo scenario and its bound IDs are unchanged.

### Security allowlist fix
`web/app/api/authorities/route.ts` maintains an explicit per-path query-parameter allowlist as the sole GET-only server boundary between the browser and the operator token / arbitrary backend paths. This was updated to permit the new `family` parameter on `/operator-dashboard/strategies` and to add the new `/operator-dashboard/strategy-scorecards` list route (`strategy_id`, `status`, `limit`, `offset`) — omitting this update would have made the new discovery endpoint and filter silently return `400 Unsupported operator authority target` from the frontend.

---

## 9. Known Limitations

- Scorecard side-by-side comparison (2-3 scorecards) is not implemented; deferred as a frontend-only follow-on (see §5.3).
- The `/research/strategies/{id}` and `/research/experiments/{id}` single-record detail routes depend on a `StrategyRunCard`/`ResearchExperiment` registry that is not wired into the Postgres-backed demo/CI server fixture (`serve_module1b_demo.py`), so in that environment the Strategy and Experiment inspectors always render via the discovery-row fallback described in §5.1/§5.2, never the full single-record detail. This is a pre-existing gap in the demo server construction, not something this module introduced or was in scope to fix (wiring a Postgres-backed strategy/experiment registry is a larger backend change beyond "bounded read-only" additions).
- No lightweight visualization was added (see §5, "Visualization — explicitly skipped").
- (Resolved in Module 2B-2.1, see §3.1) Evidence classification is now fail-closed on positive, persisted provider lineage rather than a demo/synthetic string-marker heuristic; no real market-data provider is authorized on this platform, so no research object can classify `REAL_DATA_RESEARCH_EVIDENCE` today.
