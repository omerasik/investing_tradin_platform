# Module 2A — Professional Frontend Foundation & Application Shell

## 1. Overview & Objectives
Module 2A transitions the Trade Investing Panel from a monolithic single-page dashboard into a scalable, high-density, institutional-grade application shell and design foundation.

### Core Non-Negotiables Maintained
* **No live trading**: `LIVE TRADING: DISABLED` invariant preserved across all pages, banners, headers, and metadata.
* **No automatic authority / execution**: No trade execution, buy/sell buttons, broker activation, or risk limit bypass controls.
* **Fail-closed CSP & authentication**: Per-request cryptographic nonces with strict dynamic execution (`script-src 'self' 'nonce-...' 'strict-dynamic'`, `style-src 'self' 'nonce-...'`, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`).
* **Zero inline styles**: Fully compliant with strict CSP without requiring `'unsafe-inline'`.
* **Zero mock leaks**: Server-side separation of concerns, point-in-time materializations, and clear synthetic/research evidence markings.

---

## 2. Old Structure vs New Structure

| Dimension | Module 1B Monolith | Module 2A Professional Shell |
|---|---|---|
| **Root Route (`/`)** | 47.8 KB monolithic `page.tsx` rendering all 17 domains in one unstructured scroll | Lightweight redirect to `/dashboard` (or `/login` if unauthenticated) |
| **Routing Architecture** | Single page with internal `#anchor` tags | Next.js App Router with `(protected)` group layout and 17 dedicated routes |
| **Shell & Navigation** | No persistent shell, flat list of anchors | Full `AppShell` with fixed domain navigation sidebar, dynamic breadcrumb top bar, safety badges, active route indicator, and accessible skip link |
| **Design System & Tokens** | Inline styles and ad-hoc CSS properties | Unified CSS Custom Property design tokens (`--bg-canvas`, `--bg-surface`, `--text-primary`, `--status-*`, tabular numerals) |
| **Accessibility (a11y)** | Baseline checks on single page | Automated axe-core WCAG 2.1 AA validation across full shell, top bar, sidebar, and dashboard |
| **Data Layer** | Ad-hoc fetch calls inside single component | Typed Data Access Layer (`lib/data-access.ts`) with typed helper functions for all 17 domains |
| **Transitional Pages** | None | 16 dedicated transitional workspace pages ready for Module 2B deep domain UI development |

---

## 3. Route Map

```
/
├── /login (Public operator authentication)
└── (protected)/ (App Router shell with AppShell, Sidebar, TopBar)
    ├── /dashboard (Redesigned Command Center high-density overview)
    ├── /markets (Market Data & Provider Feeds)
    ├── /instruments (Instrument Universe & Identification)
    ├── /data-health (Data Health & Cadence Schedules)
    ├── /features (Point-in-Time Materialized Feature Authority)
    ├── /signals (Signal Explorer & Reasoning Lifecycle)
    ├── /risk (Pre-trade Risk Policies & Decisions)
    ├── /strategies (Strategy Laboratory & Hypotheses)
    ├── /backtests (Backtest Validation & Cost Models)
    ├── /scorecards (Strategy Scorecard V2 Metric Matrices)
    ├── /regimes (Regime Classification & Risk Reductions)
    ├── /portfolio (Portfolio Construction & Constrained Sleeves)
    ├── /investments (Investment Engine V2 & Company Theses)
    ├── /news (Event Intelligence & Correction Chains)
    ├── /paper (Paper Order Blotter & OMS Reconciliation)
    ├── /operations (Operations & SRE Slo / Incident Telemetry)
    └── /audit (Immutable Governance & Audit Ledger)
```

---

## 4. Design Tokens & Visual Language

Institutional dark financial terminal aesthetic defined in `web/app/styles.css`:

### Color Palette & Tokens
* **Surfaces & Canvases**:
  * Canvas: `--bg-canvas: #070b14`
  * Surface: `--bg-surface: #0c1424`
  * Surface Elevated: `--bg-surface-elevated: #111c33`
  * Surface Panel: `--bg-surface-panel: #15233e`
  * Inset: `--bg-surface-inset: #080e1a`
* **Typography & Text (Contrast >= 4.5:1 on dark surfaces)**:
  * Primary: `--text-primary: #e8edf6` (14.2:1 contrast)
  * Secondary: `--text-secondary: #cbd5e1` (11.0:1 contrast)
  * Muted: `--text-muted: #94a3b8` (7.2:1 contrast)
  * Dim: `--text-dim: #94a3b8` (7.2:1 contrast)
  * Accent: `--text-accent: #93c5fd` (9.5:1 contrast)
  * Numbers: `font-variant-numeric: tabular-nums lining-nums` across tables, time, code, and metrics.
* **Status Badges & Indicators**:
  * Available / Approved: `--status-available: #86efac` (bg: `rgba(34, 197, 94, 0.20)`)
  * Warning: `--status-warning: #fcd34d` (bg: `rgba(245, 158, 11, 0.20)`)
  * Danger / Error: `--status-danger: #fca5a5` (bg: `rgba(239, 68, 68, 0.20)`)
  * Blocked / External: `--status-blocked: #d8b4fe` (bg: `rgba(168, 85, 247, 0.20)`)
  * Unavailable: `--status-unavailable: #cbd5e1` (bg: `rgba(100, 116, 139, 0.22)`)
  * Safety Badge: `--safety-badge-color: #93c5fd` (bg: `rgba(59, 130, 246, 0.20)`)

---

## 5. Reusable Component Library

Located in `web/app/components/`:
1. **`AppShell` (`app-shell.tsx`)**: Responsive container combining skip link, sidebar, top bar, main workspace, and safety footer.
2. **`Sidebar` (`sidebar.tsx`)**: Navigation bar organized into 7 functional domain groups (`Overview`, `Market & Data`, `Research`, `Portfolio & Risk`, `Investing`, `Execution`, `System`) with active route tracking and accessible semantic elements.
3. **`TopBar` (`top-bar.tsx`)**: Dynamic breadcrumb trail, environment indicator (`LOCAL / DEV`), safety badge (`LIVE TRADING: DISABLED`), and server-authenticated Sign Out action.
4. **`PageHeader` (`page-header.tsx`)**: Standardized title, eyebrow category, and point-in-time timestamp badge.
5. **`MetricCard` (`metric-card.tsx`)**: High-density numerical display with label, value, unit, and optional evidence state.
6. **`StatusBadge` (`status-badge.tsx`)**: Standardized evidence and domain status indicator.
7. **`EvidenceStateBadge` (`evidence-state-badge.tsx`)**: Distinct badge for `MEASURED`, `ASSUMED`, and `UNAVAILABLE` evidence levels.
8. **`EvidenceMeta` (`evidence-meta.tsx`)**: Provenance footer displaying source authority, as-of time, contract version, and domain limitations.
9. **`SafetyBanner` (`safety-banner.tsx`)**: Non-bypassable safety boundary banner.

---

## 6. Server / Client Separation & Security Boundary

* **Server Components (Default)**: All workspace pages (`/dashboard`, `/markets`, `/instruments`, etc.) and data loaders in `lib/data-access.ts` execute exclusively on the server. No credentials, tokens, database URLs, or upstream authority secrets are ever sent to client JavaScript bundles.
* **Client Components (Isolated)**: Only interactive elements requiring browser hooks (`usePathname()` in `Sidebar` and `TopBar`) are designated as Client Components (`"use client"`).
* **Research & Paper Form Controls**: Isolated client components (`ResearchLauncher`, `StrategyCreator`) only operate on research endpoints and cannot trigger live execution.
* **Security Middleware (`proxy.ts`)**: Every request is intercepted, assigning a cryptographic nonce for CSP, verifying view tokens and HMAC-SHA256 session tokens, and redirecting unauthenticated requests to `/login`.

---

## 7. Verification Evidence & Quality Gates

All automated verification gates passed:

1. **TypeScript & Type Check**:
   `npx tsc --noEmit` &mdash; 0 errors.
2. **ESLint**:
   `npm run lint` &mdash; 0 errors, 0 warnings.
3. **Session & Workspace Security Unit Tests**:
   `npm run test:session` &mdash; PASS.
   `npm run test:workspace` &mdash; PASS.
4. **Next.js Production Build**:
   `npm run build` &mdash; Compiled successfully, all 17 routes dynamic/prerendered.
5. **Dashboard Evidence Verification**:
   `node scripts/verify-dashboard.mjs` &mdash; PASS (fail-closed proxies and CSP headers verified).
6. **Playwright E2E Suite**:
   - `auth-flow.spec.ts`: Full login, cookie verification, query token rejection, and logout lifecycle &mdash; PASS.
   - `unconfigured.spec.ts`: Fail-closed without config, security headers, and WCAG 2.1 AA scan &mdash; PASS.
   - `module2a-app-shell.spec.ts`: Full app shell navigation, sidebar groups, active route highlighting, top bar, safety badge, accessibility audit, console error verification, and sign out &mdash; PASS.

---

## 8. Deferred Module 2B Items
* Deep interactive charts (lightweight SVG/canvas without heavy third-party bundles).
* Specialized visual builders (Feature matrix explorer, Regime heatmap, Strategy visual builder, Backtest tearsheet, Paper order blotter).
* Real-time WebSocket/SSE live telemetry streaming (transitional pages currently use server-rendered point-in-time snapshots).
