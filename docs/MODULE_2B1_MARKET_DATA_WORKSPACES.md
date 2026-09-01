# MODULE 2B-1 — Professional Market & Data Workspaces Documentation

## 1. Overview & Objectives

Module 2B-1 transforms four core transitional views (`/markets`, `/instruments`, `/data-health`, `/features`) into full, high-density professional operator workstations. Powered by the Module 1B backend discovery layer and PostgreSQL query authorities, these workspaces provide institutional-grade observability, point-in-time provenance, deterministic quality gating, and canonical instrument resolution.

### Core System Invariants Maintained
- **LIVE TRADING: DISABLED**: Strictly enforced across all workspaces and components. No trade execution or live submission controls exist in DOM or server actions.
- **EXTERNAL_BLOCKED**: Truthful representation of zero external market feed authorization.
- **SYNTHETIC DEMO EVIDENCE**: Deterministic synthetic test evidence is prominently labeled using `DemoEvidenceBanner` and `DEMO` badges.
- **NON-BYPASSABLE QUALITY GATES**: The Data Health workspace provides zero override or bypass controls. Blocking assessments strictly halt downstream research and execution pipelines.

---

## 2. Tech Debts Resolved from Module 2A

### Tech Debt 1: Dynamic Environment Resolution
- Added typed `AppEnvironment` (`"LOCAL"` | `"CI"` | `"PAPER"` | `"STAGING"`) and `resolveEnvironment()` helper in `web/app/dashboard-config.ts`.
- Environment is dynamically loaded from config or environment variables, wired through `ProtectedLayout`, forwarded to `AppShell`, and displayed in the `TopBar` environment pill badge.

### Tech Debt 2: Documentation Navigation Clarification
- Updated `docs/MODULE_2A_FRONTEND_FOUNDATION.md` to accurately document the persistent, fixed domain navigation sidebar with institutional dark glass styling and WCAG AA compliance.

---

## 3. Backend Read-Only Endpoints & Queries

Extended `src/trade_platform/operator_dashboard.py` and `src/trade_platform/api.py` with read-only query capabilities:

1. **`GET /operator-dashboard/instruments`**: Filterable by search query (symbol, name, ID), `asset_class`, and `lifecycle_status` with offset pagination.
2. **`GET /operator-dashboard/instruments/{instrument_id}`**: Deep canonical specifications, identifier mappings (ISIN, CUSIP, SEDOL, FIGI), venue symbol mappings, lifecycle events history, and associated sealed dataset versions.
3. **`GET /operator-dashboard/historical-datasets`**: Sealed dataset versions with content hashes, storage locations, record counts, and byte sizes.
4. **`GET /operator-dashboard/data-health/assessments`**: Filterable by `scope_type`, `scope_value`, `blocking` status, and `max_action` with nested findings details.
5. **`GET /operator-dashboard/data-health/assessments/{assessment_id}`**: Single assessment detail query.

---

## 4. Shared Market & Data Component Library

Built reusable, accessible, CSP-compliant UI components in `web/app/components/`:

- **`WorkspaceToolbar`**: Standardized workspace headers with breadcrumbs, action buttons, dynamic timestamps, and safety badges.
- **`DataTable`**: High-density tabular layout with horizontal scrolling, accessibility captions, and ARIA attributes.
- **`Pagination`**: Institutional pagination control supporting previous/next page navigation, page limits, and item count summaries.
- **`SearchField` & `FilterBar`**: URL-synced search input and filter dropdown groups with query preservation.
- **`QualityStateBadge`**: Color-coded badges for system and data quality states (`HEALTHY`, `WARN`, `DEGRADE_CONFIDENCE`, `BLOCK_INSTRUMENT`, `GLOBAL_BLOCK`, `AVAILABLE`, `UNAVAILABLE`, etc.).
- **`DatasetVersionBadge`**: Semantic version badges for historical sealed datasets with optional synthetic indicators.
- **`InstrumentIdentity`**: Canonical ticker, name, exchange, and asset class pill badge with truncation protection.
- **`PITTimestampGroup`**: Point-in-time timestamp block displaying `EVENT`, `EFFECTIVE`, `KNOWLEDGE`, and `COMPUTED` timestamps in UTC.
- **`ProvenancePanel`**: Collapsible institutional audit box showing record IDs, query authorities, schema versions, and UTC timestamps.
- **`KeyValueGrid`**: Responsive 2-column or 3-column metadata grid for canonical specifications.
- **`DemoEvidenceBanner`**: Prominent callout banner for datasets containing synthetic demo fixtures.

---

## 5. Workspaces Implementation

### 5.1 Markets Workspace (`/markets`)
- **Provider Authorization & Isolation**: Displays `EXTERNAL_BLOCKED` truthful status and policy isolation details.
- **Ingestion Cadence & Checkpoints**: Live cadence schedule indicators and last successful run verification.
- **Sealed Historical Datasets**: Paginated ledger of versioned datasets with cryptographic SHA-256 content hashes, record counts, and sizes.
- **Safety**: Zero live execution controls; strict CSP adherence with zero inline styles.

### 5.2 Instruments Workstation (`/instruments`)
- **Interactive Split View**: Left-hand filterable table of discovered canonical instruments; right-hand deep inspector panel.
- **Search & Filtering**: Asset class filter (`EQUITY`, `FX`, `COMMODITY`, `CRYPTO`, `RATES`), lifecycle filter (`ACTIVE`, `DELISTED`, `HALTED`), and symbol text search.
- **Deep Inspector**:
  - Canonical Specifications (base currency, lot size, tick size, trading hours, MIC).
  - Cross-Venue Identifier Mappings (ISIN, CUSIP, SEDOL, FIGI).
  - Venue Symbol Mappings (Bloomberg, Reuters, Exchange Local, Data Provider).
  - Lifecycle Events Timeline.
  - Associated Sealed Dataset Versions.

### 5.3 Data Health & Quality Center (`/data-health`)
- **Non-Bypassable Quality Invariant Banner**: Explicit notice that quality gates cannot be overridden or bypassed from the UI.
- **Quality Metrics Strip**: Overall health state, active blocking count, total assessments, and ingestion cadence.
- **Multi-Dimension Filter Bar**: Filter assessments by scope type (`GLOBAL`, `DATASET`, `INSTRUMENT`, `FEATURE`), max action (`WARN`, `DEGRADE_CONFIDENCE`, `BLOCK_INSTRUMENT`, `GLOBAL_BLOCK`), and blocking impact.
- **Assessments & Findings Breakdown**: Collapsible accordions with granular check type, action, timestamp, and JSON parameter details.

### 5.4 Features Workspace (`/features`)
- **Level 1: Feature Definitions Catalog**: Catalog of governed mathematical definitions, lookback windows, sampling frequencies, missing value policies, and calculation engine versions.
- **Active Feature Specification**: Deep parameter inspector showing formula parameters and calculation rules.
- **Level 2: Point-in-Time Materializations**: High-density table featuring `PITTimestampGroup` (`EVENT`, `EFFECTIVE`, `KNOWLEDGE`, `COMPUTED` UTC timestamps) to verify zero future lookahead leakage.

---

## 6. Dashboard Monolith Cleanup

Refactored `/dashboard` to remove duplicated raw data tables for Markets, Instruments, Data Health, and Features. Replaced them with streamlined summary metrics cards containing direct navigation links to their respective dedicated workspaces:
- `Open Markets Workspace →`
- `Open Instrument Workstation →`
- `Open Data Health Center →`
- `Open Features Workspace →`

---

## 7. Verification Results

### Automated Quality Gates
1. **Python Unit Tests**: 475 passed (`.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`).
2. **Python Linting**: Ruff clean (`.\.venv\Scripts\ruff.exe check src tests`).
3. **Python Typing**: Mypy clean (`.\.venv\Scripts\mypy.exe --follow-imports=skip src/trade_platform/operator_dashboard.py src/trade_platform/api.py`).
4. **TypeScript & Linter**: Zero TypeScript errors (`npx tsc --noEmit`) and zero ESLint warnings (`npm run lint`).
5. **Production Build**: Clean Next.js 16 build (`npm run build`).
6. **Frontend Tests**:
   - `npm run test:session` (Session & cookie security tests passing).
   - `npm run test:workspace` (Dashboard discovery resolver tests passing).
   - `npm run test:e2e:module2a` (App Shell & Navigation E2E tests passing).
   - `npm run test:e2e:module2b1` (Module 2B-1 E2E tests + Axe WCAG AA scans 100% passing across `/markets`, `/instruments`, `/data-health`, `/features`).
