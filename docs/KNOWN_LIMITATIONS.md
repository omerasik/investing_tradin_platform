# Known Limitations

The full requirement-level matrix is in
[`MASTER_ROADMAP.md`](MASTER_ROADMAP.md). These are the material current limits.

- A 2026-08-16 local regression run completed 297 Python tests but skipped 23
  PostgreSQL-dependent tests because no disposable PostgreSQL DSN is configured.
  The current full no-skip evidence remains the Cycle 15 CI run, not this local
  fixture-only execution.

- This is a local, paper-only system. Live trading is intentionally unavailable.
- SQLite stores provide durable local evidence but are not the normalized
  PostgreSQL/analytics/object-storage/queue deployment architecture required by
  the specification.
- The normalized PostgreSQL schema and representative mapped legacy migration
  are CI-verified, including APPLY/replay/conflict/restart behavior. It is not a
  blanket converter for every research-only SQLite table: unknown or unsupported
  records fail closed and require an explicit operator resolution. A safe
  disposable local PostgreSQL DSN is not configured; CI remains the authoritative
  PostgreSQL integration environment.
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
- Authentication remains development-grade bearer-token auth. Production
  sessions/RBAC/MFA, CSRF/session hardening, managed secrets, encrypted off-site
  backup and incident-operated RPO/RTO remain open. Full-package static/security
  scans, dependency audits, secret detection, SBOM/license evidence and a fresh
  PostgreSQL restore/reconciliation drill are now CI gates.
- The repository is PUBLIC. It contains no approved credential or private
  dataset; mandatory tracked-file secret scanning reduces but does not remove
  accidental-disclosure risk. Visibility was not changed.
- Complete-package mypy has 120 known errors across 18 legacy modules. CI uses
  a file-level non-increasing ratchet and requires the critical PostgreSQL slice
  to remain at zero errors.
- Upstream repositories are reference-only pending complete isolated security,
  license and benchmark evidence. No third-party runtime dependency is approved.
- Cycle 10's universe/calendars are deterministic provider-neutral convention
  fixtures, not an authorized exchange/calendar feed. Coverage is limited to
  ARCX US sessions, one FX convention and UTC crypto; BIST, broader exchanges,
  futures/options/fixed income and Europe/Asia remain inactive. GLD is an ETF
  proxy, not spot gold or a future.
- Cycle 11's PostgreSQL US equity/ETF ingestion, normalization, dataset and PIT
  query core is implemented with synthetic fixtures only. No licensed or
  legally approved real provider/data terms were supplied, so actual authorized
  ingestion is `EXTERNAL_BLOCKED`; this cycle cannot be called a real-data proof.
- Cycle 12 Data Health is deterministic and PostgreSQL-gated, but its expected
  windows, provider-comparison inputs and calendar/session verdicts must still
  come from an authorized ingestion job. It is not yet calibrated on a licensed
  production feed and does not make the fixture dataset real.
- Cycle 13 is `EXTERNAL_BLOCKED`: without an authorized real market dataset the
  required real-data quant-validation proof cannot honestly run or be labelled
  alpha. Existing synthetic validation evidence remains synthetic.
- Cycle 14's SEC-style PostgreSQL filing/fact/metric core uses attributable
  synthetic filings. No SEC terms acceptance/operator identity was authorized
  for this task, so primary-source network ingestion remains `EXTERNAL_BLOCKED`.
- Cycle 15's macro catalogue is fixture-backed. No authoritative macro source
  terms were approved, and consensus expectations remain nullable/licensed;
  network ingestion is `EXTERNAL_BLOCKED`.
