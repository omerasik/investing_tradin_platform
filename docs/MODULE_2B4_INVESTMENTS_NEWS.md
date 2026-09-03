# Module 2B-4 — Investment Research & News Intelligence Workspaces

Continues from the verified Module 2B-3 baseline. This module professionalizes the
`/investments` and `/news` frontend pages into an institutional long-horizon research
terminal and a correction-aware news/event intelligence workspace, adds bounded filters
and a truthfulness fix to the two existing investment discovery endpoints, and trims the
`/dashboard` Investment and News cards to concise summaries. It does not touch Paper OMS,
Operations, Audit, Risk, Regime, or systematic Portfolio Construction (Module 2B-3), does
not activate any real news or market-data provider, and does not add any order,
broker, or auto-promotion authority.

## Safety boundary (unchanged, re-verified)

- `LIVE TRADING: DISABLED` remains true everywhere.
- `/investments` always shows `NOT A REAL INVESTMENT RECOMMENDATION`, `REVIEW ONLY`,
  `NO BUY / SELL AUTHORITY`.
- `/news` always shows `NOT LIVE NEWS`, `RESEARCH EVIDENCE ONLY`, `NEWS EVENT ≠ ORDER`,
  and the provider state (`EXTERNAL_BLOCKED` unless a real provider is ever authorized).
- No BUY / SELL / Add-to-live-portfolio / Execute rebalance / Place order / Auto-trade
  event / Auto-promote thesis / AI auto-invest / broker action control exists anywhere in
  this module. Every backend route touched or added is `GET`-only.
- Rebalance candidates are always worded `REBALANCE CANDIDATE` / `REVIEW DECISION`, never
  `TRADE`. There is no apply button.
- News may inform research (a read-only cross-link both ways), but a persisted news event
  can never auto-generate an order, and an investment thesis can never auto-generate an
  order.

## Backend

### Investment-thesis discovery: bounded filters + truthful classification

`GET /operator-dashboard/investment-theses` gained `instrument`, `status`,
`review_state`, and `synthetic_demo` query filters (all optional, exact-match, applied
server-side — no full-history load-then-filter in the browser). The response now also
carries `evidence_classification`, resolved through the same fail-closed
`classify_research_evidence_from_markers()` helper used for strategies/experiments/
scorecards/signals since Module 2B-2.1: a `DEMO:`-prefixed instrument marker proves
`SYNTHETIC_ENGINEERING_EVIDENCE_ONLY`; absence of that marker never proves real data and
resolves to `UNAVAILABLE` (there is no authorized real-data provider on this platform, so
`REAL_DATA_RESEARCH_EVIDENCE` is unreachable today, by construction). The existing
`synthetic_demo` boolean (also `DEMO:`-prefix based) is unchanged and kept for backward
compatibility with the trimmed `/dashboard` card.

### Investment-portfolio discovery: bounded filters + truthfulness fix

`GET /operator-dashboard/investment-portfolios` gained `status` and `account_id` filters.

**Truthfulness fix (the module's most important backend finding):** `evidence_classification`
on this endpoint previously returned a *hardcoded constant string* —
`"REVIEW_ONLY; LONG_TERM_INVESTMENT; NO_EXECUTION_AUTHORITY"` — for every row, regardless
of whether the underlying rebalance candidate was synthetic or (hypothetically) real. This
is the exact "Tech Debt 1: dishonest evidence classification" defect pattern documented in
`docs/MODULE_2B2_RESEARCH_WORKSPACES.md` and already fixed there for
strategies/experiments/scorecards/signals, and in `docs/MODULE_2B3_RISK_REGIME_PORTFOLIO.md`
for covariance `provider_backed` — but it had never been applied to investment portfolios.
It now resolves through `classify_research_evidence_from_markers()`, scanning the account
id, persisted `limitations` text, and the candidate's instrument weight keys for a positive
synthetic marker; absence of one never proves real data. The authority-disclaimer text this
field used to carry now lives only in the always-visible static safety banner, not
overloaded into a provenance-classification field. Covered by a dedicated Postgres
integration test (`InvestmentDiscoveryPostgresTests` in
`tests/test_operator_dashboard_discovery_postgres.py`) reproducing both a DEMO-marked row
(must classify `SYNTHETIC_ENGINEERING_EVIDENCE_ONLY`) and a row with no synthetic marker at
all (must classify `UNAVAILABLE`, never `REAL_DATA_RESEARCH_EVIDENCE`).

### Server-side authority allowlist (`web/app/api/authorities/route.ts`)

`investment-theses` and `investment-portfolios` were split out of the generic
`limit`/`offset`-only allowlist entry into their own entries carrying the new filter keys
(`instrument`, `status`, `review_state`, `synthetic_demo` / `status`, `account_id`).
Nothing else was loosened.

### Investment thesis/portfolio *detail* routes: no backend gap, but a critical wiring gap

`GET /investments/theses/{thesis_id}` and `GET /investments/portfolios/{portfolio_id}`
(SQLite-backed, pre-existing, unchanged in this module) already return everything the
task specification asked for, including **`company_research[].invalidation_conditions`**
— there is **no missing invalidation field to add**: the `InvestmentThesis` dataclass has
none, but every `CompanyResearchRecord` already carries a required, non-empty
`invalidation_conditions` tuple, already serialized by the detail route. The Postgres
`investment_theses` table itself has no invalidation-adjacent column either. This was
confirmed by grepping every migration for `invalidat*` columns.

What *was* broken: `scripts/serve_module1b_demo.py` (the server this module's E2E suite,
and every module2b2/module2b3 E2E suite before it, runs against) never constructed or
attached a `SQLiteInvestmentStore`, so `app.state.investment_store` was always `None` and
both detail routes always returned `503` in the exact environment this module's tests run
in — the "Bound Investment Thesis"/"Bound Investment Portfolio" panels the old
`/investments` page inlined were therefore always blocked in CI, and this was never
caught because no prior E2E suite visited `/investments` or `/news`. Fixed by seeding a
`SQLiteInvestmentStore` in `serve_module1b_demo.py` with a thesis whose `thesis_id`
(`a53b4775-a2be-53d6-8c05-99a88534393f`) is the same UUID as
`scripts/seed_demo_evidence.py`'s `stable_id("investment-thesis")`, and a portfolio id
(`"demo-investment"`) matching the Postgres `investment_rebalance_candidates.account_id`
seeded there — so the Postgres discovery identity and the SQLite research content
describe one coherent scenario. The seeded content includes a full bull/base/bear
company-research record with two invalidation conditions, one DCF valuation, one
recommendation, one thesis review, portfolio holdings/performance/a rebalance decision,
one theme exposure, and one macro sensitivity.

### News fixture: ambiguous entity link

`scripts/seed_demo_evidence.py` seeded a retraction chain and one confident, reviewed
entity link, but no *ambiguous* one — so there was no fixture to exercise the "ambiguous
resolution must be visually obvious" requirement. Added a second entity link on the
`INITIAL` news document (a different instrument, `DETERMINISTIC_ALIAS` method, confidence
`0.4`, `ambiguous=TRUE`) so Module 2B-4's E2E coverage (and any future coverage) has a real
persisted fixture instead of asserting on a state nothing ever produces.

### `investment_theses`/`investment_portfolios` signature change

Both `PostgresOperatorDashboardQueries.investment_theses()` and `.investment_portfolios()`
gained required keyword-only filter parameters (no defaults, matching every other
filtered discovery method in this file — e.g. `regime_runs()`, `risk_decisions()`). Every
call site (`api.py`, `tests/test_module1b_demo_acceptance.py`) was updated to pass
explicit filter values.

## Frontend

### `/investments` — Investment Research Workspace

Replaces the old two fixed panels + two unpaginated raw `<table>`s with a real research
terminal, following the `DataTable`/`Pagination`/`FilterBar`/`WorkspaceToolbar`/
`ProvenancePanel`/`KeyValueGrid`/`DemoEvidenceBanner`/`StatusBadge` conventions
established in Module 2B-2/2B-3.

- **Thesis Discovery**: bounded, server-side-filtered table (instrument/symbol, thesis
  status, review status, synthetic/demo state) — symbol leads, not thesis UUID (the UUID
  is available only in the provenance panel).
- **Thesis Inspector**, split into clearly separated sections: Investment Thesis;
  Quality/Company Research; Scenario Analysis (Bear/Base/Bull, each its own `<h3>`, with
  an explicit note that no scenario is implied to be expected or guaranteed); Valuation
  (every persisted model version/intrinsic value/as-of, **no** upside/downside computed
  against a current market price); Catalysts; Risks (equal visual prominence to
  catalysts); **What Would Invalidate This Thesis?** (the persisted
  `invalidation_conditions`, never invented — see the backend section above for why no
  gap existed to fill); Review History (a real timeline, never implying automatic
  approval); Thesis Drift / Review Awareness (as-of, latest review date, latest valuation
  date, and a link to related persisted news — **no** invented staleness rule, since none
  exists in business logic yet, per the task's own instruction to prefer showing
  timestamps over inventing one); Evidence/Provenance (via `ProvenancePanel`, IDs kept out
  of the primary visual hierarchy).
- **Investment Portfolio**, a clearly separate section (its own `<h2>`, its own filter
  bar, and an explicit note distinguishing it from systematic Portfolio Construction V2 at
  `/portfolio`): Portfolio Discovery; Portfolio Summary; Holdings Table (quantities/cost
  basis/weights are never fabricated — only `instrument_id`/`market_value`/
  `observed_at`/`source_reference` are persisted, so only those are shown); Rebalance
  Candidates, worded `REBALANCE CANDIDATE` / `REVIEW DECISION` with rationale and
  approved-by, never `TRADE`, with no apply button.
- Safety banner (`NOT A REAL INVESTMENT RECOMMENDATION` / `REVIEW ONLY` /
  `NO BUY / SELL AUTHORITY`) is always visible, both at the top and again in the footer.

### `/news` — News / Event Intelligence Workspace

- **Provider State Header**: `EXTERNAL_BLOCKED` (or the true persisted provider state) and
  `NO EXTERNAL NEWS PROVIDER AUTHORIZED` prominently; a `DemoEvidenceBanner` when any
  visible event is not provider-activated.
- **Event Discovery Table** (bounded): headline leads, not event UUID; source, category,
  published time, revision + revision kind, urgency, uncertainty, credibility, rights
  state, and linked instrument(s) (with an inline `(AMBIGUOUS)` marker) — filterable by
  instrument, entity, category, and correction state via the existing backend filters
  (no new backend filter was needed here; `/operator-dashboard/news-events` already
  supported every filter this workspace needs).
- **Event Inspector**: headline; Source (source/version/terms/rights/authorization/
  provider-activated, shown separately from category/novelty); **Time Semantics shown
  separately** (published_at / source_updated_at / ingested_at / correction-retraction
  time — never merged into one field); Category/Novelty/Urgency/Horizon;
  **Credibility/Uncertainty as first-class evidence** (a missing value renders the literal
  string `UNAVAILABLE (never treated as 100%/zero)`, never a fabricated number);
  **Entity Links** (instrument, linking method, confidence, and an explicit
  `AMBIGUOUS — NOT EXACT` badge — never presented as an exact resolution — plus a
  read-only cross-link to `/investments?instrument=<id>`); **Correction/Retraction
  Chain** (the module's most important News UX feature): the selected event's own
  revision-kind/number/time, with an explicit `WITHDRAWN — DO NOT TREAT INITIAL CLAIM AS
  CURRENT` badge whenever it is a `RETRACTION`, followed by every persisted lineage
  relationship (`CORRECTS`/`RETRACTS`/`FOLLOWS_UP`); Research Rights/Provenance via
  `ProvenancePanel` (content fingerprint, provenance reference, source terms, rights
  state, authorization state, provider-activated, limitations — no raw credential
  reference).
- Safety banner (`NOT LIVE NEWS` / `RESEARCH EVIDENCE ONLY` / `NEWS EVENT ≠ ORDER`) is
  always visible. No order-generation action exists.

### Cross-links between Investments and News

- A thesis inspector's "Thesis Drift / Review Awareness" section links to
  `/news?instrument=<instrument_id>` to find related persisted events.
- A news event's Entity Links table links each linked instrument to
  `/investments?instrument=<instrument_id>` to find a thesis for the same instrument.
- Both directions are pure navigation through the existing bounded filters; neither ever
  auto-alters a thesis or auto-creates a catalyst/risk from a news event.

### URL state

Validated query params: `/investments?thesis=<id>`, `/investments?portfolio=<id>`,
`/investments?instrument=...&status=...&review_state=...&synthetic_demo=true|false`,
`/investments?portfolio_status=...&account_id=...`,
`/news?instrument=DEMO:XNAS:DEMO_EQ_A&category=EARNINGS`,
`/news?correction_state=RETRACTION`. An invalid or unmatched ID fails safely to the
existing `EvidenceResult` `EMPTY`/`UNAVAILABLE`/`ERROR` states — never a crash. No
credential ever appears in a URL.

### Pagination

Both discovery tables on `/investments` (thesis and portfolio — two independent bounded
lists on the same page) and the one on `/news` use the shared `Pagination` component,
which gained optional `offsetParam`/`limitParam` props (defaulting to `offset`/`limit` for
every existing caller) so `/investments` can page its two lists independently
(`thesis_offset` / `portfolio_offset`) without one list's pagination clobbering the
other's position. No full-history browser loading exists anywhere in this module.

### `/dashboard` cleanup

The Investment Workspace and News / Event Intelligence cards were trimmed to concise
summaries (selected/latest thesis status + truthful synthetic/demo classification, latest
review outcome, portfolio review status; provider state, latest persisted event headline +
revision/correction state) with `Open Investment Workspace →` / `Open News Intelligence →`
links to the dedicated pages, following the exact precedent set for Risk/Regime/Portfolio
in Module 2B-3. `web/e2e/cycle208.spec.ts`'s news test was updated (not weakened) to follow
the new link and assert the full revision-chain evidence (`RETRACTION #1`, `RETRACTS`) on
`/news` instead of on the dashboard card; `web/e2e/module1b-demo.spec.ts`'s `#investment`/
`#news` assertions (`SYNTHETIC / DEMO`, `NOT A REAL INVESTMENT RECOMMENDATION`, the
retraction headline, `NOT LIVE NEWS`) continue to pass unchanged because the trimmed cards
deliberately keep those same literal strings in their concise summaries.

## Tests

- **Backend unit/API** (`tests/test_operator_dashboard_api.py`): the two updated
  discovery routes (with their new filters) were added to the existing table-driven
  "every authority read is protected, typed, and GET-only" test; new oversized-limit/
  negative-offset validation cases were added to the existing fail-closed validation
  test; a new `test_investment_discovery_surfaces_fail_closed_evidence_classification`
  asserts the API passes the authority's classification through verbatim and never
  defaults to `REAL_DATA_RESEARCH_EVIDENCE`.
- **Backend Postgres integration**
  (`tests/test_operator_dashboard_discovery_postgres.py`, new
  `InvestmentDiscoveryPostgresTests` class, isolated disposable database): empty-state,
  every filter (instrument, status, review_state, synthetic_demo, portfolio status,
  account_id), deterministic ordering, bounded pagination, no-match fail-closed
  `UNAVAILABLE`, and — most importantly — the evidence-classification truthfulness fix,
  proving a DEMO-marked row classifies `SYNTHETIC_ENGINEERING_EVIDENCE_ONLY` and a row
  with no synthetic marker at all still classifies `UNAVAILABLE`, never
  `REAL_DATA_RESEARCH_EVIDENCE`.
- **Frontend E2E** (`web/e2e/module2b4-investments-news.spec.ts`, new): human login →
  `/investments` → `/news`, asserting thesis discovery/inspector (statement, Bear/Base/
  Bull, valuation, catalysts, risks, invalidation, review history), the Investment
  Portfolio section (holdings, a rebalance candidate worded `REBALANCE CANDIDATE`),
  `NOT A REAL INVESTMENT RECOMMENDATION`; `EXTERNAL_BLOCKED`, `NOT LIVE NEWS`, the
  correction/retraction chain (`RETRACTION`, `RETRACTS`, `WITHDRAWN`), credibility/
  uncertainty, entity links (including the ambiguous one), rights/provenance, and the
  News→Investments cross-link; zero trading/order-generation controls on either page;
  `LIVE TRADING: DISABLED`; zero console errors; zero axe WCAG 2.0/2.1 A/AA violations.
- **Frontend E2E regressions preserved**: `cycle208.spec.ts` and `module1b-demo.spec.ts`
  were updated (not silently broken) exactly where the dashboard trim moved detailed
  revision-chain evidence off the summary card onto the dedicated `/news` workspace, per
  the Module 2B-3 precedent.

## Known limitations

- **No historical (point-in-time / as-of) News query is supported by the backend.**
  `/operator-dashboard/news-events` only exposes the persisted *current* revision state
  for each event (its latest known `revision_kind`, `correction_or_retraction_at`, and
  full `correction_chain`) — it has no `as_of` parameter. This module's News UI therefore
  shows the full, real correction/retraction chain (which *does* let an operator see that
  an event was later retracted, and when), but it cannot answer "what would the News page
  have shown *before* the retraction was published" without a backend PIT query this
  authority does not have. No such support was invented; this limitation is stated
  directly in the News event's `ProvenancePanel` limitations.
- No real news or market-data provider is activated on this platform. Every
  classification, including the corrected investment-portfolio `evidence_classification`,
  resolves to synthetic/unavailable until a provider is added to
  `_AUTHORIZED_REAL_MARKET_DATA_PROVIDERS` in `operator_dashboard.py` — which must only
  happen when a provider is actually integrated and authorized, never to make a fixture
  pass.
- The investment thesis/portfolio *detail* routes (`/investments/theses/{id}`,
  `/investments/portfolios/{id}`) remain SQLite-backed and unpaginated by design (a single
  thesis's full research history is bounded by construction, not by a `limit`/`offset`
  query) — this module did not change that storage split, only wired a coherent demo
  dataset into it for the environment this module's tests run in.
- Investment thesis discovery does not support full-text search or arbitrary field
  filters, by design (bounded, safe filters only, per this module's own API-gap
  constraint).
- Dashboard `/dashboard` cards intentionally no longer show full research/revision-chain
  detail; that evidence is one click away on the dedicated workspace.
