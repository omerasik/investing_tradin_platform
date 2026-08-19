# Risk Management Policy

Capital preservation is first. Strategies propose; the independent deterministic Risk Engine approves, reduces, delays or rejects. Controls include data freshness/quality, order and position caps, portfolio concentration, leverage, liquidity/spread, loss/drawdown, event risk, reconciliation state, idempotency and available buying power.

Global, broker, venue, strategy, instrument, asset-class, data-provider and model-version kill switches prevent new orders, preserve logs, notify the operator and require explicit reactivation. They cannot reactivate automatically. This repository supports only internal paper simulation.

Every composed paper runtime requires an immutable reviewed per-trade control
set: maximum loss, maximum stop-distance fraction and a conservative stop-gap
buffer. The engine binds these limits to the validated signal's direction, entry
range and protective stop, then records both loss at stop and gap-adjusted loss.
Missing, wrong-side, out-of-range or breached evidence rejects the intent. A
protective stop is a risk-sizing assumption—not a guarantee of execution price.

In the PostgreSQL paper runtime, a rejected final pre-trade assessment opens a
deduplicated durable operator alert in the same transaction as the risk
decision. Portfolio rejection cannot reserve daily notional. Alert persistence
failure rolls the decision back and fails closed; concurrent replay cannot
duplicate the decision, alert or opening event. This is local evidence, not an
external paging service or execution authority.
