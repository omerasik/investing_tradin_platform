# Feature Comparison

| Candidate | Reusable pattern (not code) | Incompatible assumption / constraint |
|---|---|---|
| investing-algorithm-framework | experiment interface, strategy reports, two-stage validation | not an OMS/risk/reconciliation authority |
| backtesting.py | minimal strategy API, indicator test oracle | AGPL; not sole event simulator |
| Claude skills | manifests, journals, postmortems | LLM skills cannot do deterministic calculations |
| TradingAgents / Vibe-Trading | role separation, structured evidence, research artifacts | no direct order control or generated-code trust |
| FinceptTerminal | screen inventory, capability matrix | AGPL and protected UX assets; reference only |
| ML for Trading / Qlib | point-in-time research, experiments | notebook/research objects never feed execution directly |
| ai-berkshire | thesis/invalidation/bear-case checks | no named-investor authority claims |
| Lean / Nautilus | instrument/order/state/reconciliation models | separate POC and legal/operational review required |
| FinRL | target-weight interface | ML/RL still require deterministic validation |
| OpenBB | provider normalization/fallback | AGPL blocks core adoption |
| Freqtrade | dry-run lifecycle and leakage analyses | GPL and crypto-specific scope |
| VectorBT | parameter screening concepts | Commons Clause blocks adoption |
| FinRobot | research-report artifacts | generated reports are never order triggers |
