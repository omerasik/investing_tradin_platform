# Product Requirements

The product has separate active-trading and long-term-investment domains, accounts, risk budgets, limits, reports, and decision histories. MVP scope is liquid US ETFs/equities, major FX pairs, gold, BTC and ETH using historical data and internal paper simulation only.

All decisions must carry provenance, timestamp semantics, uncertainty, versions, and an audit trail. Signals are evidence objects, never orders; a deterministic risk engine alone may approve a paper order. No capability in this repository may enable live trading.

Long-horizon investment work is separately represented by versioned theses, bounded recommendations, evidence references, explicit risks and non-executable rebalance plans. It may share research data but never bypasses paper-risk controls or become an execution instruction.
