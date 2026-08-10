# Architecture Comparison

| Domain recommendation | Candidates assessed | Recommendation | Rationale |
|---|---|---|---|
| Fast research baseline | Backtesting.py, VectorBT, investing-algorithm-framework | clean-room internal vectorized engine | direct licenses rule out first two; preserve simple independent checks |
| Event-driven simulation | Lean, NautilusTrader, Freqtrade | internal event model first; future isolated POC of Lean/Nautilus | Python control plane needs low operational complexity; licenses/operational cost require POC |
| Data platform | OpenBB, Qlib, Vibe-Trading | internal provider adapter registry | OpenBB AGPL; data contracts must be owned and point-in-time safe |
| Agent orchestration | TradingAgents, Vibe-Trading, Claude skills, FinRobot | structured evidence workflow only | agents cannot bypass deterministic risk or execution |
| Investment research | ai-berkshire, ML for Trading | clean-room thesis and validation contracts | convert perspectives into testable evaluation dimensions |
| Operator UX | FinceptTerminal, Vibe-Trading | original dashboard | avoid source/brand/layout copying and stack mismatch |

No aggregate winner is used. The initial platform is a Python modular monolith with independent risk and paper OMS; higher-fidelity engines remain research comparisons.
