# Execution Policy

Order intents have immutable identifiers and pass data, strategy, regime, liquidity, portfolio, risk, broker-capability, execution, and user-policy validation. The paper OMS models proposed through reconciliation-required states, duplicate checks, fills, cancellation and rejection. No broker SDK or live submission path is permitted.
## Paper-broker boundary

The only broker-shaped implementation is a deterministic local fixture adapter. It accepts a `PaperOrder` only after the independent risk state is `APPROVED`, stores no credentials, makes no network requests, and exposes execution-quality evidence (fill rate and absolute benchmark slippage). Any vendor adapter must remain sandbox-only until it has credentials, a legal/compliance review, reconciliation proof, operational monitoring and explicit approval.
