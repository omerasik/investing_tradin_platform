# Known Limitations

The full requirement-level matrix is in
[`MASTER_ROADMAP.md`](MASTER_ROADMAP.md). These are the material current limits.

- This is a local, paper-only system. Live trading is intentionally unavailable.
- SQLite stores provide durable local evidence but are not the normalized
  PostgreSQL/analytics/object-storage/queue deployment architecture required by
  the specification.
- Golden artifacts now retain declared spread, fee, latency, participation and
  impact assumptions, partial/unfilled-order observations, and raw versus
  explained divergence. They are deterministic synthetic regressions, not
  production execution validation: queue priority, funding, borrow, margin,
  tax and capacity remain incomplete.
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
