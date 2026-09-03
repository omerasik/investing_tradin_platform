# Module 2B-5 — Paper OMS, Operations/SRE & Audit Workspaces

Professionalizes the three remaining transitional system/execution dashboard pages:
`/paper`, `/operations`, `/audit`. This is the final Module 2B workspace-professionalization
pass. No broker, no real market-data provider, and no live trading are activated by this
module — `LIVE TRADING: DISABLED` is preserved globally.

## Paper OMS (`/paper`)

**Lifecycle model:** `INTENT` (`paper_order_intents` row) → zero or more `oms_events`
(`ORDER_STATUS_CHANGED`, etc.) → zero or more `fills`. Lifecycle status is the `to` value of
the latest `ORDER_STATUS_CHANGED` event, falling back to the intent's own `status` column when
no event exists — never fabricated.

**Discovery** (`GET /operator-dashboard/paper-orders`) is bounded (`limit`/`offset`,
deterministic `created_at DESC, intent_id DESC` order) and now supports narrow filters:
`account_id`, `instrument` (matches canonical symbol or instrument UUID), `side`,
`lifecycle_status`, `fill_state`, `reconciliation_state`. Rows carry `quantity` alongside the
existing identity/lifecycle/fill/reconciliation columns.

**Detail** (`GET /operator-dashboard/paper-orders/{intent_id}` and
`GET /operator-dashboard/paper-accounts/{account_id}/reconciliation`) are new PostgreSQL-backed
endpoints added in this module. They replace the legacy dev-only SQLite `paper_oms` store
(`/paper-oms/orders/{id}`, `/paper-oms/accounts/{id}/reconciliation`) as the dashboard's detail
source: the SQLite store is never wired into any real deployment or the Module 1B demo
pipeline, so the old detail panels always resolved to `503 UNAVAILABLE` there. The new
endpoints read the same PostgreSQL tables as discovery (`paper_order_intents`, `oms_events`,
`fills`, `reconciliations`, `reconciled_account_evidence`), so a selected discovery row always
has working detail evidence.

**Reconciliation semantics:** `reconciliation_state` on a discovery row, and the standalone
detail endpoint, resolve to `HEALTHY` only when a `reconciliations` row with `complete = TRUE`
exists for the account; absence of any reconciliation row is `UNAVAILABLE`
(`DashboardObjectNotFound`, surfaced to the dashboard as `EMPTY`) — never `HEALTHY`. The
workspace always labels a complete reconciliation `PAPER ACCOUNT RECONCILED`, never `BROKER
RECONCILED`, because there is no broker transport.

**Fill summary:** ordered/filled/remaining quantity and average fill price are computed only
from persisted, exact `fills` rows (weighted average price; remaining = ordered − filled). No
slippage or execution-quality claim is made.

**Safety:** the page header and every inspector view carry `PAPER ONLY`, `NO BROKER
CONNECTIVITY`, `NO LIVE ORDER SUBMISSION`. No control on the page can submit, cancel, or route
an order.

## Operations & SRE (`/operations`)

No backend changes were required: `PostgresOperatorDashboardQueries.sre_overview()` already
persisted dependency probes, SLO target/measured pairs, incidents, and failure drills — the
Module 2B-2/2B-3-era page just rendered them as a single long evidence dump. This module
restructures the same evidence into: System Overview status cards, Service Identity, a
Dependency Health table, an SLO Target-vs-Measured table (`TARGET ≠ MEASURED` is always shown
as a distinct textual claim, never collapsed), an Incident ledger (open incidents visually
distinct from resolved ones via both a status badge and a border-color/opacity treatment —
never color alone), a Failure/Recovery Drill history labeled `ENGINEERING / DRILL EVIDENCE`
(never `PRODUCTION OUTAGE PROOF`), and a Backup/Restore summary. Missing dependency latency,
missing SLO measurement, and missing incident/drill evidence are all rendered as `UNAVAILABLE`
— never zero or an inferred pass.

Operations ↔ Audit cross-links: an incident or drill's `evidence_reference` links to
`/audit?selected=<id>` only when that reference is actually UUID-shaped; no reference is ever
invented.

*Known limitation:* the optional SLO progress bar visualization mentioned in the module brief
was intentionally not built — the textual TARGET/MEASURED table already carries the full,
unambiguous evidence, and a bar adds visual surface without new information.

## Audit (`/audit`)

**Architecture finding:** this platform has exactly one audit-event authority: an append-only
`SQLiteAuditStore` (`src/trade_platform/audit.py`), already exposed by an authenticated
`POST/GET /audit/events` pair in the same FastAPI app that serves `/operator-dashboard/*`.
Nothing in any seed/demo pipeline calls `audit_store.append(...)` — the store is real but
typically empty outside of manual operator use, so the workspace must (and does) render
`AVAILABLE`-with-empty-items or `UNAVAILABLE`, never fabricate an event.

This is a **different domain** from `operational_alerts` (a separate PostgreSQL/SQLite-backed
monitoring-alert store the page also renders, in an explicitly separate `OPERATIONAL ALERTS`
section). The previous transitional page mislabeled the whole page "Audit Log & Alerts" and
showed only alerts — that conflation is fixed here.

**Bounded projection added this module:** `GET /operator-dashboard/audit-events` (filters:
`event_type`, `actor`, `start`, `end`; bounded `limit`/`offset`, deterministic
`occurred_at DESC, event_id DESC` order) and `GET /operator-dashboard/audit-events/{event_id}`.
Both live on the existing app and reuse the existing `SQLiteAuditStore`; `SQLiteAuditStore`
gained `query(...)` and `get(...)` methods for this purpose. Every returned payload passes
through `_redact_payload`, which replaces any key whose name looks secret-shaped (`token`,
`password`, `secret`, `dsn`, `credential`, `authorization`, `api_key`, `access_key`,
`private_key`, `session`) with `"REDACTED"`, recursively.

**Immutability truthfulness:** the workspace states its authority as
`SQLITE_APPEND_ONLY_STORE` and its guarantee as a development-grade one — no update/delete
route is exposed by the store or the API — explicitly distinguishing it from the PostgreSQL
`BEFORE UPDATE/DELETE` immutability triggers used elsewhere on this platform (e.g.
`paper_order_intents`, `kill_switch_events`). It never claims the PostgreSQL-grade guarantee it
does not have.

## Dashboard cleanup

`/dashboard`'s Paper OMS, Operations, and Audit cards were trimmed to concise summaries per the
module brief (latest paper lifecycle + reconciliation status; PostgreSQL + service health +
active incident count + kill switch; latest audit event + active alert count), each linking out
to the corresponding professionalized workspace. `getAllDashboardEvidence()` gained one bounded
`audit-events` fetch (`limit: 1`) to source the "latest audit event" summary.

## Security

`_redact_payload` in `api.py` strips secret-shaped keys from every audit payload before it
leaves the backend (`tests/test_api.py::test_dashboard_audit_events_are_bounded_filtered_and_redact_secret_shaped_payload_keys`
covers nested payloads too). No endpoint added in this module returns a bearer token, DSN, or
`Authorization` header value.

## Tests

- `tests/test_audit.py` — `SQLiteAuditStore.query()`/`get()` bounds, filters, and
  deterministic ordering.
- `tests/test_api.py` — the new `/operator-dashboard/audit-events[/…]` endpoints (auth,
  bounds, filtering, redaction, 404) and an explicit alerts-vs-audit-events distinctness test.
- `tests/test_module2b5_paper_orders_postgres.py` — disposable-PostgreSQL coverage for
  `paper_orders()` filters/quantity and the reconciliation truthfulness invariant (no
  reconciliation row ⇒ `UNAVAILABLE`, never `HEALTHY`), plus `paper_order()` /
  `paper_reconciliation()` detail evidence.
- `web/e2e/module2b5-paper-operations-audit.spec.ts` — the three workspaces end to end
  (discovery, inspector, safety banners, TARGET≠MEASURED, Audit Events vs Operational Alerts,
  no mutation controls, zero axe violations), plus a human-flow login→paper→operations→audit
  walkthrough.
- `web/e2e/module1b-demo.spec.ts` was updated to match the trimmed Operations dashboard card
  and to follow its link to `/operations` for the detailed SLO evidence that moved there.

## Known limitations

- The audit-event store is SQLite and empty in every current seed/demo pipeline; the workspace
  is built to represent that truthfully rather than to demonstrate populated audit evidence.
- No broker is activated; Paper OMS remains a simulation.
- The optional SLO progress-bar visualization was not built (see Operations section above).
