# MODULE 2A — FRONTEND ARCHITECTURE MAP

## 1. Executive Summary & Context

This document maps the architectural transition of the Trade Investing Panel frontend from the Module 1B baseline (monolithic single-page dashboard) to a modular, scalable application shell and design foundation (Module 2A).

Module 2A establishes the professional layout, navigation, design tokens, component primitives, and server-side data loaders while preserving all verified safety boundaries (`LIVE TRADING: DISABLED`), session authentication, point-in-time PostgreSQL authority, and read-only evidence semantics.

---

## 2. Existing Monolithic Architecture Analysis

### 2.1 File Responsibilities & Size
- **`web/app/page.tsx` (~47.8 KB, 132 lines with dense inlined DOM)**:
  - Fetches 26 distinct evidence endpoints in a single server component (`Home`).
  - Renders 16 different operator domains in a single page with in-page anchor links (`#data-sources`, `#data-health`, `#instrument`, `#features`, `#signals`, `#risk`, `#strategy`, `#backtest`, `#scorecard`, `#regime`, `#portfolio`, `#investment`, `#news`, `#paper-oms`, `#operations`, `#audit`).
  - Intermixes overview cards, detail tables, metadata lists, and form tags in one monolithic render tree.
- **`web/app/layout.tsx` (383 B)**:
  - Root HTML skeleton importing `styles.css` and `evidence-tables.css`. Contains a basic `#main-content` skip link.
- **`web/app/styles.css` & `evidence-tables.css` (~3.6 KB total)**:
  - Basic CSS styling with hardcoded hex colors, generic Arial font, and minimal layout abstractions.
- **`web/app/operator-contracts.ts` (9.8 KB)**:
  - Core TypeScript types for evidence records (`CommandCenterEvidence`, `FeatureDefinition`, `SignalPage`, `RiskDecisionPage`, `StrategyScorecard`, `RegimeRun`, `PortfolioConstruction`, `NewsEventPage`, `SreOverview`, `EvidenceState`, `EvidenceStatus`, `MetricEvidenceState`).
  - Helper `readEvidence<T>()` with fail-closed semantics and Bearer token forwarding.
- **`web/app/dashboard-config.ts` & `dashboard-workspace.ts`**:
  - Configuration resolver supporting `dashboard.config.json` and environment fallback.
  - Discovery resolver combining explicit configuration with auto-discovered PostgreSQL UUID references.
- **`web/app/session.ts` & `web/proxy.ts`**:
  - Cryptographic HMAC-SHA256 session token generation and verification.
  - Strict Content Security Policy (per-request nonces, `frame-ancestors 'none'`, `strict-dynamic`).
  - Fail-closed route protection and query-string token rejection.
- **`web/app/research-launcher.tsx` & `web/app/strategy-creator.tsx`**:
  - Client components for isolated, research-only backtesting and strategy contract creation (no live execution authority).

---

## 3. Server Components vs. Client Components Boundary

### 3.1 Server Components (Evidence & Authority Display)
- **All Page Workspaces**: `/dashboard`, `/markets`, `/instruments`, `/data-health`, `/features`, `/signals`, `/risk`, `/strategies`, `/backtests`, `/scorecards`, `/regimes`, `/portfolio`, `/investments`, `/news`, `/paper`, `/operations`, `/audit`.
- **Protected Layout Shell**: Server-rendered application layout (`app/(protected)/layout.tsx`) ensuring zero client-side credential disclosure.
- **Data Presentation Primitives**: `Panel`, `MetricCard`, `DataTable`, `EvidenceMeta`, `StatusBadge`, `EvidenceStateBadge`, `EmptyState`, `ErrorState`, `BlockedState`, `DefinitionList`.

### 3.2 Client Components (Interactive & State Behaviors Only)
- **`Sidebar`**: Active route matching, collapsible mobile drawer, accessible keyboard navigation.
- **`ResearchLauncher` & `StrategyCreator`**: Strictly sandboxed to research-specific sub-routes; zero execution or order placement capability.
- **`TopBar` & Logout Form**: Client-friendly interactive controls.

---

## 4. Data Access Layer & Fetching Strategy

Instead of ad-hoc fetch calls inside individual pages, all data operations are encapsulated in typed server helpers (`web/app/lib/data-access.ts`):
- `getWorkspaceContext()`: Resolves active configuration and discovered workspace references.
- `getCommandCenterData()`: Platform mode, subsystem health summary, and operational alert counts.
- `getDataHealthData()`: Provider connectivity, ingestion cadences, consecutive failure counts.
- `getInstrumentData()`: Canonical point-in-time instrument discovery.
- `getFeatureData()`: Feature definitions and point-in-time materializations.
- `getSignalData()`: Signal lifecycle history, confidence metrics, and expiry states.
- `getRiskData()`: Immutable policy decisions and reservation records.
- `getStrategyData()` & `getScorecardData()`: Strategy definitions, backtest experiments, multidimensional scorecard groups.
- `getRegimeData()`: Model assessment probabilities and risk reduction multipliers.
- `getPortfolioData()`: Constrained sleeve allocations and covariance evidence.
- `getInvestmentData()`: Valuation models, thesis reviews, and investment portfolio holdings.
- `getNewsData()`: Ingested news events with revision/correction chains and entity bindings.
- `getPaperData()`: Paper order intents, fill events, and account reconciliation records.
- `getSreData()`: Service SLOs, subsystem health, active incidents, and recovery drill records.

---

## 5. Information Architecture & Target Route Map

```text
/ (Root) -> Redirect to /dashboard if authenticated, /login if unauthenticated
/login (Public login interface with dark terminal styling)

(protected)/
  layout.tsx (AppShell, Sidebar, TopBar, Safety Banner, Skip Link)
  
  /dashboard       -> Redesigned Command Center (Platform, Data, Research, Risk, Investing, SRE summaries)
  
  /markets         -> Market Overview & Provider Term Status
  /instruments     -> Canonical Point-in-Time Instrument Workstation
  /data-health     -> Return Provider Health & Ingestion Cadences
  /features        -> Feature Authority & PIT Materializations
  
  /strategies      -> Strategy Laboratory & Versioned Hypotheses
  /backtests       -> Backtest Experiments & Walk-Forward Validation
  /scorecards      -> Strategy Scorecard V2 (Multidimensional Groups)
  /signals         -> Signal Explorer & Reasoned Lifecycles
  
  /risk            -> Risk Decisions & Immutable Policies
  /regimes         -> Regime Engine V2 (Assessment & Multipliers)
  /portfolio       -> Portfolio Construction V2 (Sleeve Allocations)
  
  /investments     -> Long-Horizon Investment Theses & Portfolios
  /news            -> Ingested News & Correction Chains
  
  /paper           -> Paper OMS Orders & Reconciliation
  
  /operations      -> SRE & SLO Monitoring (Target vs. Measured)
  /audit           -> Immutable Operational & Alert Audit Logs
```

---

## 6. Design System & Token Target

The visual target is a **professional institutional trading/research workstation** (calm, dark, high information density, tabular numbers, restrained accents, zero consumer gamification).

### Token System (`styles.css` CSS Custom Properties):
- **Surfaces**:
  - `--bg-canvas`: `#070b14` (Deep background)
  - `--bg-surface`: `#0c1424` (Card/Sidebar background)
  - `--bg-surface-elevated`: `#111c33` (Modal/Dropdown/Hover background)
  - `--bg-surface-panel`: `#15233e` (Sub-panel background)
  - `--bg-surface-inset`: `#090e1a` (Table row alternate/Code blocks)
- **Borders**:
  - `--border-subtle`: `#1b2b48`
  - `--border-default`: `#243a60`
  - `--border-strong`: `#37578a`
- **Text & Typography**:
  - `--font-sans`: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif
  - `--font-mono`: "JetBrains Mono", "Cascadia Code", "SFMono-Regular", Consolas, monospace
  - `--text-primary`: `#e8edf6`
  - `--text-secondary`: `#aab8d0`
  - `--text-muted`: `#7f91b3`
  - `--text-dim`: `#4e6182`
- **Status & Evidence States**:
  - Available / Healthy: `--status-available` (`#22c55e`), `--status-available-bg` (`rgba(34, 197, 94, 0.12)`)
  - Warning / Assumed: `--status-warning` (`#f59e0b`), `--status-warning-bg` (`rgba(245, 158, 11, 0.12)`)
  - Danger / Error: `--status-danger` (`#ef4444`), `--status-danger-bg` (`rgba(239, 68, 68, 0.12)`)
  - Blocked / External Blocked: `--status-blocked` (`#a855f7`), `--status-blocked-bg` (`rgba(168, 85, 247, 0.12)`)
  - Unavailable: `--status-unavailable` (`#64748b`), `--status-unavailable-bg` (`rgba(100, 116, 139, 0.12)`)
  - Safety Accent: `--badge-safety-color` (`#60a5fa`), `--badge-safety-bg` (`rgba(96, 165, 250, 0.12)`)

---

## 7. Safety & Authorization Boundaries

1. `LIVE TRADING: DISABLED` indicator is permanently displayed in the global TopBar and SafetyBanner across every workspace.
2. No live broker integration, no trade execution buttons (`BUY`, `SELL`, `Execute`, `Submit Order`, `Activate Strategy`, `Override Risk`).
3. Research-only mutation controls (`ResearchLauncher`, `StrategyCreator`) are strictly isolated to research workspaces and require explicit server authorization.
4. Server session security with HttpOnly signed cookies and strict per-request CSP nonces is preserved without regression.
