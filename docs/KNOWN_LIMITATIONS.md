# Known Limitations

The full requirement-level matrix is in
[`MASTER_ROADMAP.md`](MASTER_ROADMAP.md). These are the material current limits.

- Cycle 230's focused migration/replay/restart/immutability path passed against a
  disposable local PostgreSQL 16 container. Exact-main run `33337948319`
  verifies the full 458-test matrix, migration 0036, all 143 restored tables and
  browser scenarios on commit `a4d835fd3832b7a9a6f2ae37970091d4bee49902`.

- This is a local, paper-only system. Live trading is intentionally unavailable.
- Cycle 231 refreshed only upstream repository identity/activity metadata. Twelve
  of the 16 recorded remote branches have advanced, but their immutable local
  pins remain the only reviewed identities. No updated pin has received a full
  license, secret, SCA, SAST, SBOM, container or malicious-package review, and
  no candidate code was fetched or executed. Exact-main run `33397619313`
  verifies the platform's full 459-test/143-table hosted matrix on commit
  `a2efd002a7cc7d4c4c40808bfd6def57fce84eda`; it does not supply an upstream
  security or adoption approval.
- Cycle 232's qlib review has only completed static triage. Its 23 declared
  production dependencies have no exact pins or resolved transitive graph, so
  there is no vulnerability-clean claim. The 18 high Bandit signals require
  review in an explicitly approved isolated environment; qlib remains
  reference-only and no source or package is imported here. Exact-main run
  `33400243819` verifies the platform's full 460-test/143-table hosted matrix
  on commit `0a22bf2110f5dcd0f542ebac1cb132333ecaa9b4`; it does not change that
  upstream security or adoption limit.
- The FinRL-Trading review is likewise static-only and fails closed: Bandit
  could not decode one tracked Python file, and 25 direct production
  declarations are non-exact and unresolved. Its declared `finrl-trade` entry
  point is not authority to execute anything; the candidate remains
  reference-only.
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
  constructor. The unconfigured PostgreSQL-only core graph is deliberately not
  submission-ready, while the configured simulated-paper facade resolves all
  required policies and point-in-time authorities and is PostgreSQL-CI verified
  through approval, fill, kill-switch rejection, reconciliation and restart.
  Signal expiry and reasoned lifecycle operations now have a bounded immutable
  core, but no continuously deployed scheduler exists. Full model lifecycle and
  return-provider health/cadence remain open, and network-connected/live broker
  adapters remain prohibited.
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
- Cycle 201 scorecards preserve immutable, version-bound synthetic engineering
  evidence and can never authorize promotion beyond `REVIEW_REQUIRED`. They do
  not establish real-market alpha: factor/correlation/regime/cross-market,
  empirical capacity and live-consistency metrics remain `UNAVAILABLE` until
  attributable prerequisite data exists. PostgreSQL scorecard/recovery evidence
  is hosted-CI verified; this workstation still has no disposable DSN.
- Cycle 202 closes the engineering Trend vertical slice only. Its four
  feature-bound long/flat strategies, vector/independent/event reconciliation,
  walk-forward, quant evidence, scorecard and package use an attributed
  deterministic fixture and are labelled
  `SYNTHETIC_ENGINEERING_EVIDENCE_ONLY`. They do not establish alpha, real
  capacity, real slippage/impact, regime performance or live consistency, and
  cannot progress beyond `REVIEW_REQUIRED` or create a paper/live activation.
- Cycle 203 provides an immutable PostgreSQL investment evidence,
  policy, and non-executable rebalance-candidate foundation. Its deterministic
  company-quality and valuation fixture is attributed and point-in-time, but is
  not a real-company conclusion, price target, recommendation, or authorization
  to allocate capital. Hosted migration/restart/immutability and 54-table restore
  evidence passed in mainline CI run `31920718692` on merge commit `bd38d35`.
- Strategy coverage is four transparent long-only baselines. Cross-sectional,
  factor, macro, relative-value, event, sentiment, crypto-basis and
  market-neutral families remain incomplete.
- Cycle 204's multidimensional regime evidence currently implements only a
  transparent rule method and a deliberately softened probabilistic ensemble.
  HMM, change-point, clustering, Bayesian-state and tree models remain
  `UNAVAILABLE`; the deterministic attributed fixture is not empirical proof
  of real-time regime skill. Regime risk output is review-only, cannot activate
  a strategy, and blocks all automatic risk increases. PR CI run `31921339310`
  passed the PostgreSQL/restart/immutability checks; exact-merge mainline run
  `31921534291` matched the 62-table restore and closed Cycle 204.
- Cycle 205 portfolio construction currently uses a deterministic attributed
  covariance fixture, not a calibrated multi-market covariance forecast or
  empirical portfolio result. The review-only candidate binds exact strategy,
  scorecard, package, regime and Data Health identities and constrains hidden
  derivative/FX/pending exposure, but cannot create a signal or order. PR CI
  run `31922197235` passed the PostgreSQL/restart/immutability checks and matched
  the 71-table restore; exact-merge mainline run `31922415005` closed Cycle 205.
- Market, macro, fundamental and news adapters are configuration/fixture or
  narrow public-source boundaries. There is no activated licensed provider,
  streaming feed, full SEC parser, economic calendar or real-time source health
  deployment.
- Cycle 206 news/event intelligence is PR-verified only. It adds immutable
  rights, revision, dedupe/cluster, entity-link, taxonomy, credibility, Data
  Health, correction/retraction and research-confidence-withdrawal contracts,
  but uses deterministic attributed fixtures. No licensed provider, labeled
  real corpus, measured mapping/classification threshold, multilingual model,
  object-stored raw document, operational strategy feed or alpha evidence is
  present. PR CI run `31922911169` passed the PostgreSQL/restart/immutability
  checks and matched all 81 restored tables; exact-merge run `31923127659`
  closed Cycle 206.
- Cycle 207 Observability/SRE V2 remains local engineering evidence. It does not
  deploy infrastructure, route external pages, define an approved production
  SLO, prove a staged soak, provide managed telemetry, or exercise cloud/KMS
  rollback. Provider activation and live/paper execution authority are absent.
  PR CI run `31923500129` passed all PostgreSQL/restart/immutability and
  91-table restore gates; exact-merge run `31923709319` passed on main commit
  `2e3b701` and closes the Cycle 207 engineering exit gate.
- Cycle 210 adds a provider-neutral social/narrative evidence core with explicit
  rights/privacy, all six discussion classes, deterministic clusters, PIT
  metric windows, bot/spam/coordination/pump controls and price/sentiment
  divergence. Hosted run `32280796788` verifies migration 0022, restart,
  immutability and the 97-table restore. It remains synthetic engineering
  evidence: there is no lawful activated connector, labeled
  real corpus, measured classifier quality, production cadence, feature feed or
  operator workspace.
- Cycle 211 adds an immutable reasoned signal timeline, bounded idempotent expiry
  batch and read-only Signal Explorer. Exact-main run `32284687492` verifies all
  358 tests without skips, migration 0023, 100-table restore and all eleven
  configured browser scenarios. The scheduler is callable but not deployed
  continuously, and no signal can activate a strategy, create an order or
  contact a broker through this workspace.
- Cycle 212 enforces policy-bound per-trade loss, stop distance and conservative
  gap loss in composed paper runtimes and persists the calculations in keyed
  assessment evidence. The buffer is a reviewed deterministic policy input, not
  a calibrated empirical gap model; live source calibration remains outstanding.
  A stop is risk evidence, not proof that any fill or exit can occur at that price.
- Cycle 213 makes PostgreSQL pre-trade rejection alerts and their immutable
  transitions durable and atomic with risk decisions. This is local operator
  evidence only: there is no email/pager delivery, escalation schedule, broad
  producer coverage or production on-call integration. Snapshot/reconciliation
  reads are still not one serializable transaction, and empirical risk inputs
  remain unavailable.
- Cycle 214 persists policy/hash-bound execution-quality observations and
  primary/shadow comparisons, but both are deterministic simulated-paper
  engineering evidence. They use caller-supplied simulated reference prices,
  measure latency from intent creation rather than venue acknowledgement, omit
  unfilled opportunity cost and fees, and do not constitute real execution
  quality, broker-sandbox soak, operational shadow mode or activation evidence.
  Divergence/threshold alerts remain local; external routing is absent.
- Cycle 215 records durable job policies, terminal runs, overdue/recovery alert
  transitions and a local outbox handoff. It does not deploy a scheduler, run a
  job, invoke a provider, retain payload objects, call a webhook/email/pager or
  prove external delivery. Outbox entries deliberately remain
  `PENDING_EXTERNAL_DELIVERY`; a future consumer must re-check current alert
  status and requires separate authorization, secrets and failure evidence.
- Cycle 216 records retention policies, catalog metadata and hashes; it does not
  store or retrieve object bytes, prove off-site/encrypted storage, execute
  lifecycle rules or delete anything. `ELIGIBLE_FOR_REVIEW` is evidence for a
  future human/authorized workflow, not permission to delete. Legal holds are
  policy-wide rather than case-specific, and no policy owner has approved
  production durations.
- Regime, ensemble, ML and agent layers are local governance/research contracts;
  they do not have validated production models, semantic retrieval, orchestration,
  empirical evaluation or execution authority. Cycle 224 evaluates supplied
  chronological binary holdout predictions and local additive explanations
  against a predecessor under pre-approved thresholds. It does not train or
  invoke a model, compute permutation/SHAP explanations, establish causal
  attribution, perform historical-analogue/scenario sensitivity, monitor
  degradation in production or serve predictions. Its deterministic fixture
  can establish only engineering correctness, and `REVIEW_ELIGIBLE` still
  requires separate manual registry approval.
- Cycle 227 consumes supplied fixture scenario and normalized degradation scores;
  it does not perturb or invoke a model, calculate production drift, prove model
  stability, generate permutation/SHAP explanations, find historical analogues,
  establish causation or authorize automatic disablement/reapproval. Its eight
  required dimensions and threshold outcomes are immutable review evidence only.
  `NO_THRESHOLD_BREACH_OBSERVED` is not a production-quality or readiness claim.
- Cycle 228 records supplied report sections, cost observations and measurable-
  value estimates; it does not operate a scheduler, gather invoices, verify
  provider charges, call a model, deliver reports, procure a dataset/model or
  prove that estimated value will materialize. Budget modes named `LIMITED_LIVE`
  and `SCALED_LIVE` are catalogue values only. Database-enforced execution and
  procurement authority remains `NONE`, and live trading remains disabled.
- Cycle 229 governs supplied, already-produced retrieval and answer-evaluation
  records; it does not schedule a process, construct a prompt, invoke a model,
  expose a tool, ingest external data, deliver an answer or take an action.
  Token and estimated-cost values are attributed fixture inputs, not provider
  invoices or measured inference usage. `READY_FOR_HUMAN_REVIEW` is not agent,
  model, signal, order, risk, approval or live-trading authority.
- Cycle 230 compares supplied normalized fixture snapshots with deterministic
  feature-importance-weighted distance. This is not semantic or causal
  explanation, actual model invocation, permutation/SHAP evidence, a production
  score, proof of future quality/economic value, or an approval. Even
  `READY_FOR_REVIEW` has model-invocation, prediction and action authority
  `NONE`.
- Cycle 225 retrieval is deterministic lexical matching over attributed internal
  fixture chunks. Cycle 226 evaluates retrieval-bound structured answers using
  exact claim citations, token overlap, utilization/diversity, confidence and
  disclosure thresholds. Neither cycle provides semantic/vector retrieval,
  source truth, semantic factuality or causal verification, external document
  ingestion, production access control, a rich natural-language interface or
  external-model quality acceptance. No model, connector, credential, tool or
  sensitive-action approval is activated; `REVIEW_ELIGIBLE` still requires the
  existing independent safety and human-review workflow.
- Cycle 208 adds a separate deployment-owned dashboard-view bearer boundary,
  protected typed read models for Feature Authority, Strategy Scorecard V2,
  Regime V2, Portfolio Construction V2, News/Event V2 and SRE V2, plus a
  Playwright exit matrix. This remains development-grade authentication, not
  production sessions/RBAC/MFA. The locally executable unconfigured browser
  scenario passed, and all ten configured PostgreSQL browser scenarios passed
  in final PR run `32276487834` and exact-merge mainline run `32276800878`.
  Cycle 217 adds automated WCAG A/AA scanning across the full dashboard, but
  manual screen-reader, zoom/reflow, forced-colors and assistive-technology
  acceptance remain incomplete. Interactive charts are still absent.
- Default authentication remains development-grade bearer-token auth. Cycle
  222 adds a provider-independent verified-session/group mapping contract plus
  immutable PostgreSQL mapping policies and allow/deny decision evidence, but
  no concrete OIDC/JWT verifier, IdP, key rotation, revocation, managed groups,
  short-lived server session or provider-enforced MFA is configured. CSRF/session
  hardening, managed secrets, encrypted off-site backup and incident-operated
  RPO/RTO remain open. Full-package static/security
  scans, dependency audits, secret detection, SBOM/license evidence and a fresh
  PostgreSQL restore/reconciliation drill are now CI gates, but they are not a
  penetration test. Cycle 223 removes `unsafe-inline` from the dashboard policy
  and verifies per-request nonce binding/rotation in the protected browser
  suite. A deployment proxy can still break or weaken this dynamic policy;
  HSTS is effective only over HTTPS, and neither header policy substitutes for
  a TLS terminator, OIDC or identity governance.
- Cycle 219's container is limited to the default SQLite-backed
  `local_research` API. Hosted CI proves its digest-pinned build, UID/GID 10001,
  read-only/capability-free/no-new-privileges runtime, health state and viewer
  authorization checks. It does not prove a production/PostgreSQL deployment,
  registry-native OCI signature/publication, registry, IaC/orchestration, network/TLS policy,
  resource sizing, rollback or staging soak. The local
  Linux Docker daemon is unavailable, so no workstation runtime claim is made.
- Cycle 220 adds a complete retained container vulnerability report and
  CycloneDX 1.7 SBOM, not a clean-image claim. Run `32300815517` records 212
  total findings and 124 components; 26 HIGH/CRITICAL findings have no vendor
  fix and remain unresolved, while the fixable HIGH/CRITICAL count is zero.
  CI fails when a HIGH/CRITICAL fix becomes available but is not applied, or
  when the OS is EOL. Scanner/database coverage and severity classifications
  can change, and there is still no registry policy.
- Cycle 221 provides signed SLSA provenance and CycloneDX SBOM attestations for
  a retained gzip image archive, with checksum and online verification. It does
  not sign or publish an OCI manifest, create a registry/storage/deployment
  record, establish release approval, prove reproducible builds or define
  artifact retention. Sigstore/GitHub availability is an external dependency;
  untrusted fork PRs intentionally receive no attestation authority.
- The repository is PUBLIC. It contains no approved credential or private
  dataset; mandatory tracked-file secret scanning reduces but does not remove
  accidental-disclosure risk. Visibility was not changed.
- Complete-package mypy has 117 known errors across 18 legacy modules. CI uses
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
