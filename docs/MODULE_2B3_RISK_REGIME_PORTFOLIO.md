# Module 2B-3 — Risk, Regime & Portfolio Workspaces

Continues from the verified Module 2B-2.1 baseline. This module professionalizes the
`/risk`, `/regimes`, and `/portfolio` frontend pages into institutional risk-control
workspaces, adds two bounded discovery endpoints, and trims the `/dashboard` summary
cards for these domains. It does not touch Investments, News, Paper OMS, Operations, or
Audit, does not activate any real market-data provider, and does not add any execution,
override, or approval authority.

## Safety boundary (unchanged, re-verified)

- `LIVE TRADING: DISABLED` and `NO AUTOMATIC AUTHORITY` remain true everywhere.
- No page or component in this module can override risk, increase a risk limit, approve
  a rejected decision, release or alter a reservation, apply/rebalance a portfolio, or
  place an order. Every backend route added or touched is `GET`-only.
- Regime evidence may only `REDUCE` or `BLOCK` risk; it can never automatically increase
  a limit. `automatic_authority` is `Literal[False]` at the pydantic layer for every
  regime/portfolio evidence type, so a violation fails at construction time, not just by
  convention.
- Portfolio output is always `REVIEW ELIGIBLE`, never "approved for execution."
  `review_only` is `Literal[True]`.

## Backend

### New discovery endpoints

- `GET /operator-dashboard/regime-runs` — bounded, paginated, filterable
  (`instrument`, `status`, `model_version_id`, `dataset_version`). Deterministic
  ordering: `evaluated_at DESC, run_id DESC`. Each item includes a per-dimension summary
  (hard label, top probability, uncertainty) resolved from persisted
  `regime_observations` in a single batched query, not one query per run.
- `GET /operator-dashboard/portfolio-construction-runs` — bounded, paginated,
  filterable (`status`, `policy_version_id`, `regime_run_id`). Deterministic ordering:
  `constructed_at DESC, run_id DESC`. Every returned run carries `risk_gate_approved`,
  `review_only=True`, `automatic_authority=False`.

Both are additive; the existing by-ID detail routes
(`/operator-dashboard/regime-runs/{run_id}`,
`/operator-dashboard/portfolio-construction-runs/{run_id}`) are unchanged.

### Extended risk-decision filters

`GET /operator-dashboard/risk-decisions` gained `approved`, `account_id`,
`policy_version_id`, `business_date`, and `has_reservation` query filters, applied
server-side with the same `CAST(%s AS ...) IS NULL OR ...` pattern used elsewhere in
this file. `limit`/`offset` pagination is unchanged.

### Server-side authority allowlist (`web/app/api/authorities/route.ts`)

The Next.js proxy that forwards browser requests to the operator-dashboard API
maintains an explicit allowlist of paths and query-parameter names. The two new list
endpoints and the risk-decision filters were added to this allowlist; nothing else was
loosened.

### Truthfulness fix: covariance `provider_backed`

While building the Covariance / Correlation Evidence panel, `provider_backed` was found
to be computed from the wrong SQL column (`provider_identifier_namespace` instead of
`provider`) and from a heuristic (`authorization_reference` truthiness) that any
synthetic fixture can satisfy. The Module 1B demo seed's covariance source is not named
literally `FIXTURE` and does set an `authorization_reference`, so it was rendering as
`PROVIDER_BACKED_COVARIANCE` — a false real-provider claim. Fixed to use the same
fail-closed allowlist (`_AUTHORIZED_REAL_MARKET_DATA_PROVIDERS`, currently empty because
no real provider is activated) that every other evidence-classification path in this
file already uses. Covered by a dedicated Postgres integration test that reproduces the
exact demo-seed shape and asserts `provider_backed is False`.

## Frontend

### `/risk` — Risk Control Workspace

List + inspector split (`DataTable` + `Pagination` on the left, an inspector `<aside>`
on the right), following the same pattern as `/scorecards` from Module 2B-2.

- **Risk Summary**: presentation-level aggregates over the *current bounded page only*
  (decisions returned, approved/rejected counts, reservations present, a page-scoped
  reserved-notional sum, latest decision timestamp) — explicitly labeled as such, not a
  new authoritative metric.
- **Filters**: outcome (approved/rejected), reservation presence, account, policy
  version ID, business date — all server-side via the extended query params.
- **Ledger**: outcome, policy, account, reserved notional, business date, reasons,
  decided-at, with an inspect link that preserves the active filters.
- **Inspector**: decision identity, policy identity + content hash, policy limits
  rendered as a `KeyValueGrid`, outcome and every persisted reason, reservation evidence
  (or an explicit `NO RESERVATION`, never a bare `0`), and the boundary banner
  (`RESEARCH / PAPER ONLY` / `NO AUTOMATIC AUTHORITY` / `NO RISK OVERRIDE`), plus a
  `ProvenancePanel` with the decision ID, policy version, content hash, and timestamp.

### `/regimes` — Regime Engine Workspace

- **Discovery**: as-of time, instrument, model/rule version, dataset version, status,
  a dominant-regime summary, and an uncertainty summary — filterable by instrument,
  status, and model version ID.
- **Inspector**: identity, PIT semantics (as-of vs. knowledge timestamp, explicit
  `UNAVAILABLE` when knowledge time can't be resolved), the persisted `risk_boundary`
  string, and per-dimension detail.
- **Probability visualization**: an inline SVG horizontal bar per state
  (`viewBox="0 0 100 10"`, a numeric `width` attribute driven by the persisted
  probability — no inline `style`, so it satisfies the app's strict
  `style-src 'self' 'nonce-...'` CSP with no `unsafe-inline`). The table/list values
  remain the authoritative, always-visible representation; the bar is a supplementary
  visual only.
- **Uncertainty**: rendered prominently next to each dimension, and a persisted `null`
  uncertainty always renders as the literal string `UNAVAILABLE`, never `0`.
- **Regime Risk Effects**: strategy version, current/proposed multiplier, pre-approved
  maximum, action, status, reasons, and an explicit invariant check —
  `PROPOSED ≤ CURRENT` and `PROPOSED ≤ PREAPPROVED MAXIMUM`, computed directly from the
  three persisted numbers. A violation renders a prominent `ERROR / BLOCKED` badge
  instead of being silently clamped.
- Safety banner (`REGIME MAY REDUCE OR BLOCK RISK` / `REGIME CANNOT INCREASE GLOBAL RISK
  LIMITS`) is always visible, both at the top of the page and again in the inspector.

### `/portfolio` — Portfolio Construction Workspace

- **Discovery**: constructed-at, policy version, regime run (linked back to
  `/regimes?selected=...`), status, equity/target vol, portfolio/stressed vol, and the
  risk-gate result — filterable by status, policy version ID, and regime run ID.
- **Allocation Flow**: for every sleeve, requested vs. review allocation is shown as
  text (`0.4 requested → 0.24 review`, or `REJECTED` when `review_allocation` is
  `null`) and as a pair of SVG bars (same CSP-safe technique as the regime page), plus
  effective notional, risk budget, capacity weight, liquidity score, drawdown, regime
  multipliers, marginal/component risk, and every persisted adjustment reason rendered
  as a chip — the literal persisted strings, not a re-derived category.
- **Constraint Ledger**: every persisted constraint with its actual `state`
  (`SATISFIED | REDUCED | BLOCKED` — the real enum in this schema; nothing is renamed to
  a `PASS`/`UNAVAILABLE` vocabulary that isn't what's persisted), observed value, limit,
  and reasons. Nothing is filtered out.
- **Independent Portfolio Risk Gate**: approved/blocked, every reason, and the wording
  `REVIEW ELIGIBLE` — never `APPROVED FOR EXECUTION` — even when approved.
- **Covariance / Correlation Evidence**: dataset version, content hash, estimation
  version, observation count, as-of, uncertainty, correlation stress, source provider,
  source terms version, the fail-closed `provider_backed` boolean (see the backend fix
  above), and the resulting classification string.
- Safety banner: `REVIEW ONLY`, `NO APPLY/REBALANCE/EXECUTE CONTROL EXISTS HERE`.

### Cross-workspace links

- A regime run's inspector links to `/portfolio?regime_run_id=<id>` to find the
  constructions that used it.
- A portfolio run's inspector and discovery table link its regime run to
  `/regimes?selected=<id>`.
- All links go through validated query-state (`?selected=`, `?approved=`,
  `?status=`, etc.); an invalid or unmatched ID renders the existing
  `EvidenceResult` `EMPTY`/`ERROR` states, never a crash.

### `/dashboard` cleanup

The Risk, Regime, and Portfolio Command Center cards were trimmed to concise summaries
(latest decision + approved/rejected count; dominant regime + uncertainty summary;
latest construction status + risk gate) with an "Open ... Workspace →" link to the new
dedicated page. The detailed evidence they used to inline (full sleeve/constraint
tables, per-state probabilities, covariance classification) now lives on the dedicated
workspace instead. `web/e2e/cycle208.spec.ts` and `web/e2e/module1b-demo.spec.ts` were
updated to follow that link and assert the same evidence there, rather than weakened.

## Discovery API contract notes

- Both list endpoints return heavy nested detail (full dimension list, full sleeve
  list, full constraint list) *only* from the by-ID detail routes; the list responses
  carry only the summarized fields needed for a table row, per the "avoid returning full
  heavy nested detail in list endpoints" constraint.
- `limit` is bounded to `[1, 100]`, `offset` to `[0, 10000]`, matching every other
  discovery endpoint in this API.

## Tests

- **Backend unit/API** (`tests/test_operator_dashboard_api.py`): the two new list
  routes were added to the existing table-driven "every authority read is
  protected, typed, and GET-only" test (401 unauthenticated, 401 bad token, 200
  authenticated with no secret/DSN/token leakage, 405 on POST). New query-parameter
  validation cases (invalid UUID, invalid enum, oversized limit, negative offset) were
  added to the existing fail-closed validation test, asserting the mocked query methods
  are never called. A dedicated invariant test asserts `automatic_authority`/
  `review_only` in the JSON response for both domains.
- **Backend Postgres integration**
  (`tests/test_operator_dashboard_discovery_postgres.py`, new file, isolated disposable
  database per the `test_module1b_demo_acceptance.py` pattern): empty-state,
  multiple-runs with deterministic ordering, bounded pagination, every filter, no-match
  fail-closed `UNAVAILABLE`, and the covariance `provider_backed` truthfulness fix,
  reproducing the exact non-`FIXTURE`-namespaced source shape that exposed the bug.
- **Backend Postgres integration** (`tests/test_operator_dashboard_postgres.py`,
  extended): the existing shared-DB fixture now also exercises `approved`,
  `has_reservation`, and `account_id` risk-decision filters against its existing
  approved/rejected/reservation rows.
- **Frontend E2E** (`web/e2e/module2b3-risk-regime-portfolio.spec.ts`, new): human
  login → `/risk` → `/regimes` → `/portfolio`, asserting the ledger/discovery tables,
  filters, inspector sections, reservation evidence, probability bars, uncertainty,
  regime risk effects and their invariant badge, the allocation flow, constraint ledger,
  independent risk gate wording, zero mutation-shaped buttons (a legitimate
  "Apply Filters" GET-form submit is explicitly distinguished from an execution
  control), `LIVE TRADING: DISABLED`, an axe WCAG 2.0/2.1 A/AA scan with zero
  violations, and zero browser console errors on each page.
- **Frontend E2E regressions preserved**: `cycle208.spec.ts` and
  `module1b-demo.spec.ts` were updated (not weakened) where the dashboard
  simplification moved evidence off the summary card and onto the dedicated
  workspace; `module2a-app-shell.spec.ts`'s sidebar-navigation assertion continues to
  pass unchanged because the `/risk` page kept its existing H1 title.

## Known limitations

- No real market-data or portfolio-analytics provider is activated on this platform.
  Every classification, including the corrected `provider_backed` covariance flag,
  resolves to synthetic/unavailable until a provider is added to
  `_AUTHORIZED_REAL_MARKET_DATA_PROVIDERS` in `operator_dashboard.py` — which must only
  happen when a provider is actually integrated and authorized, never to make a fixture
  pass.
- Risk-decision reservation notional summation on `/risk` is a page-scoped, presentation
  -level convenience for the operator; it is not a ledger balance and does not attempt
  to reconcile across pages.
- The regime/portfolio discovery list endpoints do not support full-text search or
  arbitrary field filters by design (bounded, safe filters only, per the module's API
  gap requirements).
- Dashboard `/dashboard` cards intentionally no longer show full sleeve/constraint/
  probability detail; that evidence is one click away on the dedicated workspace.
