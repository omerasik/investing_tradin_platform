# Test Strategy

Use unit, integration, contract, property, data-quality, backtest-regression, risk, security, failure-injection, restore and UI tests. Golden data and deterministic runs are required. Critical invariants: no order bypasses risk; expired signals and disabled strategies cannot create orders; duplicate intents cannot create exposure; stale data/reconciliation failures block risk increases; live trading remains impossible.
