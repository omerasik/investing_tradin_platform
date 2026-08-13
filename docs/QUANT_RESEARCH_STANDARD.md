# Quant Research Standard

Research begins with transparent baselines. Experiments, datasets, features, strategy parameters, costs and outputs are versioned. Validation uses chronological splits, walk-forward testing, embargo/purging where needed, cost stress, regime slices, and unmodified holdouts. Track researcher degrees of freedom and never promote on a single best historical result.

Complex ML/LLM methods require measured incremental out-of-sample and economic value over a simpler baseline. LLM outputs are untrusted evidence only.

Every candidate must persist a versioned, immutable and content-addressed
validation package bound to its exact strategy, dataset, feature and cost-model
versions. It includes multi-capital capacity (marked an estimate for OHLCV-only
data), base/1.5x/2x/3x execution cost, wider-spread and impact scenarios,
frequency-appropriate latency, seeded bootstrap, trade-order Monte Carlo,
stress fixtures, parameter-neighborhood stability and multiple-testing counts.
BH false-discovery control, probabilistic/deflated Sharpe diagnostics and an
explicit overfitting diagnostic are recorded with their limitations. A missing,
stale, mismatched, failed or unexplained artifact blocks the result. Passing
research can only be `REVIEW_REQUIRED`; it never creates an order or enables
paper/live trading.
