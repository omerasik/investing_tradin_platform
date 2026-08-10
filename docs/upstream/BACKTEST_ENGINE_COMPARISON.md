# Backtest Engine Comparison

The benchmark is not yet run because dependency execution is prohibited until isolated sandbox/SBOM/security gates are in place. The eventual same-data moving-average and portfolio benchmarks must compare order timestamps, fills, fees, slippage, accounting and corporate actions among an internal vectorized baseline, independent baseline, and internal event-driven simulator.

| Engine / pattern | Intended role | Current decision |
|---|---|---|
| internal vectorized | rapid, deterministic screening | build |
| internal event-driven | promotion and paper parity | build |
| Backtesting.py | independent educational comparison | reference only (AGPL) |
| Lean | future specialist POC | separate-service evaluation |
| NautilusTrader | future fidelity POC | separate-service evaluation |
| VectorBT | concept-only throughput reference | rejected dependency |
