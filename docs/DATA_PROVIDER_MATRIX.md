# Data Provider Matrix

Providers remain unselected pending jurisdiction, license, cost and quality review. Evaluate asset/instrument coverage, historical and real-time depth, point-in-time/revision/delisting correctness, corporate actions, API/streaming limits, latency, storage/redistribution rights, Belgium availability, reliability, backup options and costs. Initial tests use generated golden fixtures only.

| Slice | Technical path | Authorization state | Operational state |
|---|---|---|---|
| US equities/ETFs historical | PostgreSQL raw → normalize → instrument resolve → quality → sealed dataset → PIT query | `EXTERNAL_BLOCKED`: no provider/terms/legal approval supplied | Provider-neutral core implemented; no network source activated |
| Stooq daily CSV candidate | Existing terms-gated read-only adapter | Terms acceptance not recorded; not selected as authoritative | Disabled |

Test-only `test-authorization://` references prove the fail-closed contract and
must never be interpreted as rights to store or redistribute real data.
