# Execution Policy

Order intents have immutable identifiers and pass data, strategy, regime, liquidity, portfolio, risk, broker-capability, execution, and user-policy validation. The paper OMS models proposed through reconciliation-required states, duplicate checks, fills, cancellation and rejection. No broker SDK or live submission path is permitted.

Before the paper OMS boundary, the risk engine must bind the intent to the
validated signal's entry range, direction and protective stop and enforce both
loss-at-stop and policy-buffered gap loss. These calculations grant no order,
cancel, replace or broker authority.

Daily notional is reserved only after both individual and portfolio gates
approve. A portfolio or individual rejection creates durable local alert
evidence atomically with the PostgreSQL risk decision and never reaches the
paper OMS. External notification delivery remains unconfigured.
## Paper-broker boundary

The only broker-shaped implementation is a deterministic local fixture adapter. It accepts a `PaperOrder` only after the independent risk state is `APPROVED`, stores no credentials, makes no network requests, and exposes execution-quality evidence (fill rate and absolute benchmark slippage). Any vendor adapter must remain sandbox-only until it has credentials, a legal/compliance review, reconciliation proof, operational monitoring and explicit approval.
