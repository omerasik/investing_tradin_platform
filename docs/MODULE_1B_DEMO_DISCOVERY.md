# Module 1B: Demo Evidence and Workspace Discovery

## Current discovery boundary

The dashboard resolves PostgreSQL-backed authority defaults through the typed,
read-only `GET /operator-dashboard/workspace-references` projection.  It is
not a generic database search endpoint.  Each selected value uses an explicit
timestamp descending and identity descending tie-breaker.

Resolution is deterministic:

1. An explicit `dashboard.config.json` or environment override wins.
2. The authoritative PostgreSQL discovery projection supplies a latest record.
3. Missing evidence remains explicitly unavailable.

The projection covers Feature Authority (definition, instrument, dataset and
PIT decision time), Scorecard, Regime, Portfolio Construction, News instrument
and SRE service version.  It returns no connection details, credentials,
provider secrets, or raw database rows.

## Empty database behavior

No record is invented when an authority table is empty.  The authenticated
dashboard renders its existing unavailable state and does not choose an
undefined `LIMIT 1` row.

## Explicit overrides

`dashboard.config.json` remains deployment-owned optional override
configuration.  It can pin an authority ID for a reviewed deployment, but a
normal PostgreSQL workspace no longer needs the six discovered UUID fields
above copied into the file.

## Safety

Discovery only reads immutable authority projections.  It does not activate a
provider, contact a provider, submit an order, alter risk, or enable live
trading.  `LIVE TRADING: DISABLED` remains the product boundary.

## Deterministic local demo command

Use the Module 1A canonical local stack command:

```bash
python scripts/dev.py --reset-db --demo
```

It starts local PostgreSQL, migrates it, invokes
`scripts/seed_demo_evidence.py`, then starts the API and dashboard. The seed
uses `module1b-demo-evidence-v1`, UUID5 identities, a fixed scenario timestamp,
and no network/provider/broker call. It is restricted to local PostgreSQL DSNs.

All resulting records are explicitly synthetic, demo, and engineering evidence;
they are not market data, performance proof, recommendations, broker evidence,
or authority to trade.

### Seed-specific bridge note

The existing professional-instrument and feature repositories are used directly.
Several older immutable link tables have no public construction repository
(legacy research experiment/package bridge, legacy paper lifecycle bridge, and
cross-engine joins). The seeder performs only narrow deterministic inserts into
those tables, preserving their established foreign keys, content hashes,
lifecycle values, and immutable triggers. It adds no application mutation API.
