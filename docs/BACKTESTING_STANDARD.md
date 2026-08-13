# Backtesting Standard

Use independent vectorized and event-driven engines. Include fees, spread, slippage, market impact, latency, corporate actions, funding, borrow, margin and currency effects as applicable. Run optimistic, base, and pessimistic scenarios; promotion relies on base and pessimistic results.

Cross-engine reports reconcile timestamps, orders, fills, position accounting, costs and corporate-action treatment. Material unexplained divergence, leakage, omitted costs, or unrealistic fills blocks promotion.

Execution degradation must be reported, not hidden: base, 1.5x, 2x and 3x
slippage plus wider-spread and higher-impact scenarios all retain return,
annualized return where valid, Sharpe/Sortino, drawdown, turnover, cost and
baseline degradation. Latency is expressed in meaningful whole bars at the
dataset frequency and retains changed/missed fills and decay. Capacity reports
multiple capital levels, participation, fill/unfilled estimates and post-impact
metrics. All are immutable evidence; OHLCV alone must be labelled an estimate.
