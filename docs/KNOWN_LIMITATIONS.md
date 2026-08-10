# Known Limitations

- The project began from an empty repository; implemented components are local, fixture-driven and paper-only rather than operational production services.
- External market, fundamental, macro, news and social data require licensed sources and provider contracts.
- Broker paper integration requires sandbox credentials and Belgian/legal/tax review.
- Upstream audits are static and preliminary until dependency/SBOM scanners are configured in an isolated environment.
- No live-trading capability exists or is intended in the current implementation.
- The initial idempotency ledger is in-memory only; a durable audited OMS store is required before multi-process paper trading.
- The API has a single environment-backed operator token and in-memory rate limit. It must not be exposed beyond localhost until durable sessions, RBAC, TLS, distributed rate limits and deployment controls are implemented.
- The Next.js dashboard has a server-side risk proxy and read-only local workspace states. Broader authenticated data wiring, durable operator identity and browser-level UI tests remain pending.
- Browser-level local UI verification is currently blocked by browser-control navigation timeouts despite the local dashboard server responding with HTTP 200 and the production build passing. This is tooling evidence, not a dashboard pass.
