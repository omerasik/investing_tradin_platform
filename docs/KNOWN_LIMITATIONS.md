# Known Limitations

The full requirement-level matrix is in
[`MASTER_ROADMAP.md`](MASTER_ROADMAP.md). These are the material current limits.

- This is a local, paper-only system. Live trading is intentionally unavailable.
- SQLite stores provide durable local evidence but are not the normalized
  PostgreSQL/analytics/object-storage/queue deployment architecture required by
  the specification.
- The normalized PostgreSQL schema and migration boundary are present,
  but legacy SQLite repositories have not yet been migrated individually and
  the backfill intentionally stops before writes without an explicit identity
  mapping. A safe disposable local PostgreSQL DSN is not configured; CI is the
  authoritative PostgreSQL integration environment.
- PostgreSQL configuration can no longer enter the legacy SQLite paper-runtime
  constructor, and an explicit PostgreSQL-only core authority graph exists.
  That graph is deliberately not submission-ready. Policy and signed
  assessment plus point-in-time quotes, execution context and return history
  are PostgreSQL-CI verified. Instrument/risk-profile/session, validated-signal
  and model-approval authorities are implemented pending CI. Their full managed
  lifecycles and return-provider health/cadence remain open.
- Golden artifacts now retain declared spread, fee, latency, participation and
  impact assumptions, partial/unfilled-order observations, and raw versus
  explained divergence. They are deterministic synthetic regressions, not
  production execution validation: queue priority, funding, borrow, margin,
  tax remain incomplete. Capacity is separately persisted but is an explicit
  daily OHLCV/ADV estimate: it has no order-book, queue, auction or empirical
  broker-fill precision.
- Slippage, latency, stress and robustness artifacts are deterministic,
  versioned research evidence. Their costs and shocks are declared model/fixture
  assumptions; no full historical stress archive, real order-book replay or
  calibrated provider-backed capacity model is available.
- A complete package is required for a review decision. Canonical manifest v1,
  exact restart reconstruction and immutable membership are PostgreSQL-CI
  verified. Pre-manifest rows are intentionally LEGACY_UNVERIFIABLE. Package
  generation is not yet an automated backtest-launch workflow or dashboard
  view, and critical application composition still contains legacy SQLite
  authorities outside this verified package boundary.
- Strategy coverage is four transparent long-only baselines. Cross-sectional,
  factor, macro, relative-value, event, sentiment, crypto-basis and
  market-neutral families remain incomplete.
- Market, macro, fundamental and news adapters are configuration/fixture or
  narrow public-source boundaries. There is no activated licensed provider,
  streaming feed, full SEC parser, economic calendar or real-time source health
  deployment.
- Social/narrative intelligence is not started: no lawful connector, bot/spam
  controls, narrative clustering, crowding or price/sentiment divergence.
- Regime, ensemble, ML and agent layers are local governance/research contracts;
  they do not have validated production models, retrieval, orchestration,
  empirical evaluation or execution authority.
- The dashboard has selected browser E2E evidence, but lacks production
  authentication/RBAC/MFA, interactive charts, accessibility verification and
  the full required operator workflow suite.
- Security is development-grade: bearer authentication and fail-closed local
  controls exist; sessions/RBAC/MFA/CSRF/security headers/secret manager/SAST/
  SCA/SBOM/encrypted off-site backup and disaster-recovery drills remain open.
- Upstream repositories are reference-only pending complete isolated security,
  license and benchmark evidence. No third-party runtime dependency is approved.
