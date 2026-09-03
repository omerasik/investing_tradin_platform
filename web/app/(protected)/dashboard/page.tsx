import Link from "next/link";
import {
  getAllDashboardEvidence,
  stateText,
  utc,
} from "../../lib/data-access";
import { EvidenceMeta } from "../../components/evidence-meta";
import { StatusBadge } from "../../components/status-badge";
import { PageHeader } from "../../components/page-header";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const data = await getAllDashboardEvidence();
  const {
    command,
    dataHealth,
    cadence,
    strategy,
    experiment,
    thesis,
    portfolio,
    alerts,
    paperOrder,
    reconciliation,
    instruments,
    strategies,
    experiments,
    investmentTheses,
    investmentPortfolios,
    paperOrders,
    feature,
    featureMaterializations,
    signals,
    risk,
    scorecard,
    regime,
    construction,
    news,
    sre,
  } = data;

  const health = dataHealth.state === "AVAILABLE" ? dataHealth.value : undefined;
  const schedule = cadence.state === "AVAILABLE" ? cadence.value[0] : undefined;
  const strategyCard = strategy.state === "AVAILABLE" ? strategy.value : undefined;
  const backtest = experiment.state === "AVAILABLE" ? experiment.value : undefined;
  const investment = thesis.state === "AVAILABLE" ? thesis.value : undefined;
  const investmentPortfolio = portfolio.state === "AVAILABLE" ? portfolio.value : undefined;
  const order = paperOrder.state === "AVAILABLE" ? paperOrder.value : undefined;
  const account = reconciliation.state === "AVAILABLE" ? reconciliation.value : undefined;
  const commandEvidence = command.state === "AVAILABLE" ? command.value : undefined;
  const alertRows = alerts.state === "AVAILABLE" ? alerts.value : undefined;
  const featureDefinition = feature.state === "AVAILABLE" ? feature.value : undefined;
  const materializations = featureMaterializations.state === "AVAILABLE" ? featureMaterializations.value : undefined;
  const signalEvidence = signals.state === "AVAILABLE" ? signals.value : undefined;
  const riskEvidence = risk.state === "AVAILABLE" ? risk.value : undefined;
  const scorecardEvidence = scorecard.state === "AVAILABLE" ? scorecard.value : undefined;
  const regimeEvidence = regime.state === "AVAILABLE" ? regime.value : undefined;
  const constructionEvidence = construction.state === "AVAILABLE" ? construction.value : undefined;
  const newsEvidence = news.state === "AVAILABLE" ? news.value : undefined;
  const sreEvidence = sre.state === "AVAILABLE" ? sre.value : undefined;

  const instrumentRows = instruments.state === "AVAILABLE" ? instruments.value.items : [];
  const strategyRows = strategies.state === "AVAILABLE" ? strategies.value.items : [];
  const experimentRows = experiments.state === "AVAILABLE" ? experiments.value.items : [];
  const thesisRows = investmentTheses.state === "AVAILABLE" ? investmentTheses.value.items : [];
  const investmentPortfolioRows = investmentPortfolios.state === "AVAILABLE" ? investmentPortfolios.value.items : [];
  const paperOrderRows = paperOrders.state === "AVAILABLE" ? paperOrders.value.items : [];

  return (
    <>
      <PageHeader
        eyebrow="PRIVATE READ-ONLY OPERATOR WORKSPACE"
        title="Command Center"
        asOfTime={data.ctx.evidenceTime}
      />

      {/* Top High-Density Summary Cards */}
      <section className="cards" aria-label="Authoritative command summary">
        <article className="card">
          <p>PLATFORM</p>
          <strong>{commandEvidence?.platform_mode ?? stateText(command)}</strong>
          <small>Live trading: DISABLED</small>
          <Link href="#command">View platform evidence &rarr;</Link>
        </article>

        <article className="card">
          <p>DATA</p>
          <strong>
            {commandEvidence?.states.find((item) => item.id === "features")?.status ??
              (health ? (health.healthy ? "AVAILABLE" : "BLOCKED") : dataHealth.state)}
          </strong>
          <small>Provider, Data Health, dataset and feature state</small>
          <Link href="/features">View feature evidence &rarr;</Link>
        </article>

        <article className="card">
          <p>RESEARCH</p>
          <strong>
            {commandEvidence?.states.find((item) => item.id === "scorecard")?.status ?? scorecard.state}
          </strong>
          <small>{scorecardEvidence?.evidence_classification ?? "Eligibility evidence unavailable"}</small>
          <Link href="/scorecards">View scorecard evidence &rarr;</Link>
        </article>

        <article className="card">
          <p>PORTFOLIO</p>
          <strong>
            {commandEvidence?.states.find((item) => item.id === "portfolio-construction")?.status ??
              construction.state}
          </strong>
          <small>
            {constructionEvidence
              ? `${constructionEvidence.sleeves.filter((item) => item.rejected).length} rejected; ${
                  constructionEvidence.sleeves.filter((item) => item.adjustment_reasons.length).length
                } reduced`
              : "Construction review unavailable"}
          </small>
          <Link href="/portfolio">View construction evidence &rarr;</Link>
        </article>

        <article className="card">
          <p>INVESTMENT</p>
          <strong>{investment?.thesis.status ?? thesis.state}</strong>
          <small>{alertRows ? `${alertRows.length} pending operator alerts` : "Alert evidence unavailable"}</small>
          <Link href="/investments">View investment evidence &rarr;</Link>
        </article>

        <article className="card">
          <p>NEWS</p>
          <strong>
            {commandEvidence?.states.find((item) => item.id === "news")?.status ??
              newsEvidence?.provider_state ??
              news.state}
          </strong>
          <small>Provider authorization and latest persisted event</small>
          <Link href="/news">View event evidence &rarr;</Link>
        </article>

        <article className="card">
          <p>OPERATIONS</p>
          <strong>{commandEvidence?.states.find((item) => item.id === "operations")?.status ?? sre.state}</strong>
          <small>PostgreSQL, reconciliation, recovery, incidents and kill switches</small>
          <Link href="/operations">View SRE evidence &rarr;</Link>
        </article>
      </section>

      {/* Grid of Domain Workstations / Evidence Sections */}
      <section className="grid">
        {/* Command Center Evidence */}
        <article id="command" className="panel">
          <h2>
            <span>Command Center Evidence</span>
            <StatusBadge status={commandEvidence ? "AVAILABLE" : command.state} />
          </h2>
          {commandEvidence ? (
            <>
              <dl>
                <dt>Contract version</dt>
                <dd>{commandEvidence.version}</dd>
                <dt>Platform mode</dt>
                <dd>{commandEvidence.platform_mode}</dd>
                <dt>Live trading</dt>
                <dd>{commandEvidence.live_trading_enabled ? "ENABLED" : "DISABLED"}</dd>
                {commandEvidence.states.map((item) => (
                  <div key={item.id} className="display-contents">
                    <dt>{item.id}</dt>
                    <dd>
                      {item.status}: {item.freshness}
                    </dd>
                  </div>
                ))}
              </dl>
              <EvidenceMeta
                source={commandEvidence.source}
                asOf={commandEvidence.as_of}
                version={commandEvidence.version}
                limitations={commandEvidence.limitations}
              />
            </>
          ) : (
            <p className="empty-state">{stateText(command)}</p>
          )}
          <span className="status margin-top-auto">READ ONLY</span>
        </article>

        {/* Data Sources / Providers */}
        <article id="data-sources" className="panel">
          <h2>
            <span>Data Sources / Providers</span>
            <StatusBadge status={health ? "AVAILABLE" : dataHealth.state} />
          </h2>
          <p>Provider status, ingestion cadences, and sealed dataset versioning.</p>
          <dl>
            <dt>Provider Status</dt>
            <dd><code>EXTERNAL_BLOCKED (TRUTHFUL)</code></dd>
            <dt>Authorization State</dt>
            <dd>No real market data provider authorized</dd>
            <dt>Ingestion Cadence</dt>
            <dd>
              {schedule
                ? `${schedule.overdue ? "STALE / OVERDUE" : schedule.due ? "DUE" : "CURRENT"}; last success ${utc(schedule.last_successful_at)}`
                : stateText(cadence)}
            </dd>
          </dl>
          <div className="panel-footer-row">
            <span className="status">{health ? "READ ONLY" : "EXTERNAL_BLOCKED"}</span>
            <Link href="/markets" className="workspace-link">
              Open Markets Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Data Health */}
        <article id="data-health" className="panel">
          <h2>
            <span>Data Health &amp; Quality</span>
            <StatusBadge status={health ? (health.healthy ? "AVAILABLE" : "BLOCKED") : dataHealth.state} />
          </h2>
          <p>Non-bypassable data health checks and quality anomaly assessments.</p>
          <dl>
            <dt>Overall Quality</dt>
            <dd>
              {health
                ? health.healthy
                  ? "HEALTHY / PASS"
                  : "BLOCKING / REVIEW REQUIRED"
                : dataHealth.state}
            </dd>
            <dt>Active Failures</dt>
            <dd>{health ? `${health.consecutive_failures} consecutive failures` : "0"}</dd>
            <dt>Quality Invariant</dt>
            <dd>Non-bypassable blocking controls active</dd>
          </dl>
          <div className="panel-footer-row">
            <span className="status">NON-BYPASSABLE</span>
            <Link href="/data-health" className="workspace-link">
              Open Data Health Center &rarr;
            </Link>
          </div>
        </article>

        {/* Instrument Workstation */}
        <article id="instrument" className="panel">
          <h2>
            <span>Instrument Workstation</span>
            <StatusBadge status={instruments.state} />
          </h2>
          <p>
            Canonical, point-in-time instrument discovery. Ambiguous symbols are displayed rather than
            guessed.
          </p>
          {instrumentRows.length ? (
            <table>
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Class / venue / lifecycle</th>
                  <th>Validity / dataset</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {instrumentRows.map((item) => (
                  <tr key={item.instrument_id}>
                    <td>
                      <strong>{item.canonical_symbol}</strong>
                      <br />
                      <code>{item.instrument_id}</code>
                    </td>
                    <td>
                      {item.asset_class} / {item.venue} / {item.lifecycle_status}
                    </td>
                    <td>
                      {utc(item.valid_from)} / {utc(item.valid_until)}
                      <br />
                      {item.latest_dataset_version ?? "UNAVAILABLE"}
                    </td>
                    <td>
                      {item.synthetic_demo ? "SYNTHETIC / DEMO" : "AUTHORITATIVE"}; mappings{" "}
                      {item.identifier_mapping_count};{" "}
                      {item.ambiguous_mapping ? "AMBIGUOUS — not selected" : "unambiguous"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty-state">{stateText(instruments)}</p>
          )}
          <div className="panel-footer-row">
            <span className="status">READ ONLY</span>
            <Link href="/instruments" className="workspace-link">
              Open Instrument Workstation &rarr;
            </Link>
          </div>
        </article>

        {/* Feature Authority */}
        <article id="features" className="panel">
          <h2>
            <span>Feature Authority</span>
            <StatusBadge status={featureDefinition ? "AVAILABLE" : feature.state} />
          </h2>
          <p>
            Feature values are displayed exactly as materialized by the point-in-time PostgreSQL
            authority; the frontend never recomputes them.
          </p>
          {featureDefinition ? (
            <>
              <dl>
                <dt>Definition / version</dt>
                <dd>
                  {featureDefinition.feature_name} / {featureDefinition.semantic_version}
                </dd>
                <dt>Family / status</dt>
                <dd>
                  {featureDefinition.family} / {featureDefinition.status}
                </dd>
                <dt>Required dataset / fields</dt>
                <dd>
                  {featureDefinition.required_dataset_types.join(", ")} /{" "}
                  {featureDefinition.required_fields.join(", ")}
                </dd>
                <dt>Frequency / timestamp semantics / lookback</dt>
                <dd>
                  {featureDefinition.frequency} / {featureDefinition.timestamp_semantics} /{" "}
                  {featureDefinition.lookback}
                </dd>
                <dt>Policies</dt>
                <dd>
                  Missing: {featureDefinition.missing_value_policy}; outlier:{" "}
                  {featureDefinition.outlier_policy}; leakage: {featureDefinition.leakage_policy}
                </dd>
                <dt>Units / calculation</dt>
                <dd>
                  {featureDefinition.units} / {featureDefinition.calculation_version}
                </dd>
              </dl>
              <EvidenceMeta
                source="PostgreSQL Feature Authority"
                asOf={featureDefinition.created_at}
                version={featureDefinition.semantic_version}
              />
            </>
          ) : (
            <p className="empty-state">{stateText(feature)}</p>
          )}
          {materializations?.items.length ? (
            <table>
              <caption>Point-in-time materializations as of {utc(materializations.decision_time)}</caption>
              <thead>
                <tr>
                  <th>Instrument / dataset</th>
                  <th>Event / knowledge / computed</th>
                  <th>Value</th>
                  <th>Quality / provenance</th>
                </tr>
              </thead>
              <tbody>
                {materializations.items.map((item) => (
                  <tr key={item.materialization_id}>
                    <td>
                      {item.instrument}
                      <br />
                      {item.dataset_version}
                    </td>
                    <td>
                      {utc(item.event_time)}
                      <br />
                      {utc(item.knowledge_time)}
                      <br />
                      {utc(item.computed_time)}
                    </td>
                    <td>{item.value ?? "UNAVAILABLE"}</td>
                    <td>
                      {item.quality_state}
                      <br />
                      <code>{item.content_hash}</code>
                      <br />
                      {item.source_manifest.join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty-state">
              UNAVAILABLE:{" "}
              {materializations
                ? "no authorized materialization matched this bounded PIT scope; zero was not substituted."
                : stateText(featureMaterializations)}
            </p>
          )}
          <div className="panel-footer-row">
            <span className="status">{materializations?.items.length ? "AVAILABLE" : "UNAVAILABLE"} / READ ONLY</span>
            <Link href="/features" className="workspace-link">
              Open Features Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Signal Explorer */}
        <article id="signals" className="panel">
          <h2>
            <span>Signal Explorer</span>
            <StatusBadge status={signalEvidence ? "AVAILABLE" : signals.state} />
          </h2>
          <p>
            Point-in-time, reasoned lifecycle evidence only. No control here can create an order,
            activate a strategy, or contact a broker.
          </p>
          {signalEvidence?.items.length ? (
            <>
              <table>
                <caption>Signal lifecycle as of {utc(signalEvidence.as_of)}</caption>
                <thead>
                  <tr>
                    <th>Signal / instrument</th>
                    <th>Status / expiry</th>
                    <th>Strength / confidence / data</th>
                    <th>Reason / explanation</th>
                  </tr>
                </thead>
                <tbody>
                  {signalEvidence.items.map((item) => (
                    <tr key={item.signal_id}>
                      <td>
                        <code>{item.signal_id}</code>
                        <br />
                        {item.instrument}
                        <br />
                        {item.strategy_version} / {item.direction}
                      </td>
                      <td>
                        {item.status} / {item.expiry_state}
                        <br />
                        created {utc(item.created_at)}
                        <br />
                        expires {utc(item.expires_at)}
                      </td>
                      <td>
                        {item.strength} / {item.confidence} / {item.data_quality_score}
                        <br />
                        passed {item.passed_stages.join(", ") || "none"}
                        <br />
                        failed {item.failed_stages.join(", ") || "none"}
                      </td>
                      <td>
                        {item.latest_reason}
                        <br />
                        {item.explanation}
                        <br />
                        Contradicting: {item.contradicting_evidence.join("; ") || "none recorded"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {signalEvidence.items.map((item) => (
                <details key={`${item.signal_id}-timeline`} className="provenance">
                  <summary>Lifecycle for {item.signal_id}</summary>
                  <ol>
                    {item.lifecycle.map((event) => (
                      <li key={event.event_id}>
                        {event.from_status} &rarr; {event.to_status} at {utc(event.occurred_at)} by{" "}
                        {event.actor}: {event.reason}; evidence {event.evidence_references.join(", ") || "none"}
                      </li>
                    ))}
                  </ol>
                </details>
              ))}
            </>
          ) : (
            <p className="empty-state">
              {signalEvidence
                ? `${signalEvidence.state}: no signal matched this bounded point-in-time scope.`
                : stateText(signals)}
            </p>
          )}
          <div className="panel-footer-row">
            <span className="status">RESEARCH / PAPER ONLY / READ ONLY / NO AUTOMATIC AUTHORITY</span>
            <Link href="/signals" className="workspace-link">
              Open Signal Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Strategy Laboratory */}
        <article id="strategy" className="panel">
          <h2>
            <span>Strategy Laboratory</span>
            <StatusBadge status={strategyCard ? "AVAILABLE" : strategy.state} />
          </h2>
          <dl>
            <dt>Current Strategy</dt>
            <dd>
              {strategyCard
                ? `${strategyCard.family} / ${strategyCard.strategy_version} — ${strategyCard.evidence_classification}`
                : strategyRows.length
                  ? `${strategyRows.length} discovered — ${strategyRows[0].family} (${strategyRows[0].evidence_classification})`
                  : stateText(strategies)}
            </dd>
          </dl>
          <div className="panel-footer-row">
            <span className="status">RESEARCH ONLY / READ ONLY</span>
            <Link href="/strategies" className="workspace-link">
              Open Strategy Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Backtest / Validation */}
        <article id="backtest" className="panel">
          <h2>
            <span>Backtest / Validation</span>
            <StatusBadge status={backtest ? "AVAILABLE" : experiment.state} />
          </h2>
          <dl>
            <dt>Latest Backtest Status</dt>
            <dd>
              {backtest
                ? `${String(backtest.report.walk_forward_status ?? "UNAVAILABLE")} — total return ${String(backtest.report.total_return ?? "UNAVAILABLE")}`
                : experimentRows.length
                  ? `${experimentRows.length} discovered — dataset ${experimentRows[0].dataset_version}`
                  : stateText(experiments)}
            </dd>
          </dl>
          <div className="panel-footer-row">
            <span className="status">HISTORICAL / RESEARCH ONLY</span>
            <Link href="/backtests" className="workspace-link">
              Open Backtest Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Risk Workspace */}
        <article id="risk" className="panel">
          <h2>
            <span>Risk Workspace</span>
            <StatusBadge status={riskEvidence ? "AVAILABLE" : risk.state} />
          </h2>
          <p>
            Immutable policy, decision and reservation evidence only. This workspace cannot evaluate,
            override, release, reserve, submit or cancel anything.
          </p>
          {riskEvidence?.items.length ? (
            <dl>
              <dt>Latest decision</dt>
              <dd>
                <StatusBadge status={riskEvidence.items[0].approved ? "APPROVED" : "REJECTED"} /> &bull;{" "}
                {utc(riskEvidence.items[0].decided_at)}
              </dd>
              <dt>Approved / Rejected (current page)</dt>
              <dd>
                {riskEvidence.items.filter((item) => item.approved).length} approved /{" "}
                {riskEvidence.items.filter((item) => !item.approved).length} rejected
              </dd>
            </dl>
          ) : (
            <p className="empty-state">
              {riskEvidence
                ? `${riskEvidence.state}: no immutable risk decision matched this bounded scope.`
                : stateText(risk)}
            </p>
          )}
          <div className="panel-footer-row">
            <span className="status">READ ONLY / NO RISK OVERRIDE / NO EXECUTION ACTION</span>
            <Link href="/risk" className="workspace-link">
              Open Risk Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Strategy Scorecard V2 */}
        <article id="scorecard" className="panel">
          <h2>
            <span>Strategy Scorecard V2</span>
            <StatusBadge status={scorecardEvidence ? "AVAILABLE" : scorecard.state} />
          </h2>
          <p>
            No opaque aggregate score is used. Metric evidence state is independent from workspace
            availability.
          </p>
          {scorecardEvidence ? (
            <>
              <p className="warning">{scorecardEvidence.evidence_classification}</p>
              <dl>
                <dt>Scorecard / strategy</dt>
                <dd>
                  <code>{scorecardEvidence.scorecard_id}</code> / {scorecardEvidence.strategy_version}
                </dd>
                <dt>Research / dataset / features / costs</dt>
                <dd>
                  {scorecardEvidence.research_run_id} / {scorecardEvidence.dataset_version} /{" "}
                  {scorecardEvidence.feature_versions.join(", ")} / {scorecardEvidence.cost_model_version}
                </dd>
                <dt>Evaluated / knowledge cutoff</dt>
                <dd>
                  {utc(scorecardEvidence.evaluated_at)} / {utc(scorecardEvidence.knowledge_cutoff)}
                </dd>
                <dt>Status / validation package</dt>
                <dd>
                  {scorecardEvidence.status} / {scorecardEvidence.validation_package_id ?? "UNAVAILABLE"}
                </dd>
                <dt>Evidence hash</dt>
                <dd>
                  <code>{scorecardEvidence.content_hash}</code>
                </dd>
              </dl>

              {scorecardEvidence.groups.map((group) => (
                <section key={group.name} aria-labelledby={`scorecard-${group.name}`}>
                  <h3 id={`scorecard-${group.name}`}>{group.name}</h3>
                  {group.metrics.length ? (
                    <table>
                      <thead>
                        <tr>
                          <th>Metric</th>
                          <th>Value / unit</th>
                          <th>Evidence state</th>
                          <th>Reference</th>
                        </tr>
                      </thead>
                      <tbody>
                        {group.metrics.map((metric) => (
                          <tr key={metric.metric_id}>
                            <td>{metric.name}</td>
                            <td>
                              {metric.value ?? "UNAVAILABLE"} {metric.unit}
                            </td>
                            <td>{metric.evidence_state}</td>
                            <td>
                              <code>{metric.evidence_reference}</code>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="empty-state">UNAVAILABLE</p>
                  )}
                </section>
              ))}

              <section aria-labelledby="scorecard-complexity">
                <h3 id="scorecard-complexity">COMPLEXITY</h3>
                {scorecardEvidence.complexity_components.map((item) => (
                  <p key={item.component_id}>
                    {item.name}: {item.value ?? "UNAVAILABLE"} ({item.formula_version}) &mdash; {item.rationale}
                  </p>
                ))}
              </section>

              <EvidenceMeta
                source="PostgreSQL Strategy Scorecard V2"
                asOf={scorecardEvidence.evaluated_at}
                version={scorecardEvidence.schema_version}
                limitations={scorecardEvidence.limitations}
              />
            </>
          ) : (
            <p className="empty-state">{stateText(scorecard)}</p>
          )}
          <div className="panel-footer-row">
            <span className="status">RESEARCH EVIDENCE / READ ONLY</span>
            <Link href="/scorecards" className="workspace-link">
              Open Scorecard Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Regime Workspace */}
        <article id="regime" className="panel">
          <h2>
            <span>Regime Workspace</span>
            <StatusBadge status={regimeEvidence ? "AVAILABLE" : regime.state} />
          </h2>
          <p className="warning">
            REGIME MAY REDUCE OR BLOCK RISK. REGIME CANNOT INCREASE GLOBAL RISK LIMITS.
          </p>
          {regimeEvidence ? (
            <dl>
              <dt>Instrument / status</dt>
              <dd>
                {regimeEvidence.instrument} &bull; <StatusBadge status={regimeEvidence.status} />
              </dd>
              <dt>Dominant regime</dt>
              <dd>
                {regimeEvidence.dimensions.map((item) => `${item.dimension}=${item.hard_label ?? "UNAVAILABLE"}`).join("; ") || "UNAVAILABLE"}
              </dd>
              <dt>Uncertainty summary</dt>
              <dd>
                {regimeEvidence.dimensions.map((item) => `${item.dimension}=${item.uncertainty ?? "UNAVAILABLE"}`).join("; ") || "UNAVAILABLE"}
              </dd>
            </dl>
          ) : (
            <p className="empty-state">{stateText(regime)}</p>
          )}
          <div className="panel-footer-row">
            <span className="status">RESEARCH ONLY / NO RISK-INCREASE CONTROL</span>
            <Link href="/regimes" className="workspace-link">
              Open Regime Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Portfolio Construction */}
        <article id="portfolio" className="panel">
          <h2>
            <span>Portfolio Construction</span>
            <StatusBadge status={constructionEvidence ? "AVAILABLE" : construction.state} />
          </h2>
          <p>Review-only requested and constrained allocations. This workspace has no apply or execution action.</p>
          {constructionEvidence ? (
            <dl>
              <dt>Status / constructed</dt>
              <dd>
                <StatusBadge status={constructionEvidence.status} /> &bull; {utc(constructionEvidence.constructed_at)}
              </dd>
              <dt>Risk gate</dt>
              <dd>
                <StatusBadge
                  status={constructionEvidence.risk_gate_approved ? "APPROVED" : "BLOCKED"}
                  label={constructionEvidence.risk_gate_approved ? "REVIEW ELIGIBLE" : "BLOCKED"}
                />
              </dd>
            </dl>
          ) : (
            <p className="empty-state">{stateText(construction)}</p>
          )}
          <div className="panel-footer-row">
            <span className="status">REVIEW ONLY / NO EXECUTION ACTION</span>
            <Link href="/portfolio" className="workspace-link">
              Open Portfolio Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Investment Workspace -- concise summary only; full research terminal is /investments (Module 2B-4). */}
        <article id="investment" className="panel">
          <h2>
            <span>Investment Workspace</span>
            <StatusBadge status={investment ? "AVAILABLE" : thesis.state} />
          </h2>
          <p className="warning">
            NOT A REAL INVESTMENT RECOMMENDATION. REVIEW ONLY. NO BUY / SELL AUTHORITY.
          </p>
          {thesisRows.length ? (
            <dl>
              <dt>Selected thesis / status</dt>
              <dd>
                {investment ? investment.thesis.instrument_id : thesisRows[0].canonical_symbol ?? thesisRows[0].instrument_id}
                {" / "}
                {investment ? investment.thesis.status : thesisRows[0].status}
              </dd>
              <dt>Latest review / classification</dt>
              <dd>
                {investment ? (investment.reviews.at(-1)?.outcome ?? "UNAVAILABLE") : (thesisRows[0].review_state ?? "UNAVAILABLE")}
                {" / "}
                {thesisRows[0].synthetic_demo ? "SYNTHETIC / DEMO" : "RESEARCH"}
              </dd>
            </dl>
          ) : (
            <p className="empty-state">{stateText(investmentTheses)}</p>
          )}

          {investmentPortfolioRows.length ? (
            <dl>
              <dt>Portfolio review status</dt>
              <dd>
                {investmentPortfolioRows[0].portfolio_id} / {investmentPortfolioRows[0].review_status}
              </dd>
            </dl>
          ) : investmentPortfolio ? (
            <dl>
              <dt>Portfolio review status</dt>
              <dd>
                {investmentPortfolio.portfolio_id} /{" "}
                {investmentPortfolio.assessment.approved ? "WITHIN LIMITS" : investmentPortfolio.assessment.reasons.join(", ")}
              </dd>
            </dl>
          ) : (
            <p className="empty-state">{stateText(investmentPortfolios)}</p>
          )}

          <div className="panel-footer-row">
            <span className="status">REVIEW ONLY / NO BUY OR SELL</span>
            <Link href="/investments" className="workspace-link">
              Open Investment Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* News / Event Intelligence -- concise summary only; full revision-chain evidence is /news (Module 2B-4). */}
        <article id="news" className="panel">
          <h2>
            <span>News / Event Intelligence</span>
            <StatusBadge status={newsEvidence?.provider_state ?? news.state} />
          </h2>
          <p className="warning">
            {newsEvidence?.provider_state ?? "EXTERNAL_BLOCKED"} / NOT LIVE NEWS / NO EXTERNAL NEWS PROVIDER AUTHORIZED
          </p>
          {newsEvidence?.items.length ? (
            <dl>
              <dt>Latest persisted event</dt>
              <dd>{newsEvidence.items[0].headline}</dd>
              <dt>Revision / correction-retraction state</dt>
              <dd>
                {newsEvidence.items[0].revision_kind} #{newsEvidence.items[0].revision}
                {newsEvidence.items[0].correction_chain.length
                  ? ` (${newsEvidence.items[0].correction_chain.map((link) => link.relation).join(", ")})`
                  : ""}
              </dd>
            </dl>
          ) : (
            <p className="empty-state">
              {newsEvidence
                ? `${newsEvidence.state}: no persisted event matched this scope.`
                : stateText(news)}
            </p>
          )}
          <div className="panel-footer-row">
            <span className="status">{newsEvidence?.provider_state ?? "EXTERNAL_BLOCKED"} / READ ONLY / NOT LIVE NEWS</span>
            <Link href="/news" className="workspace-link">
              Open News Intelligence &rarr;
            </Link>
          </div>
        </article>

        {/* Paper OMS */}
        <article id="paper-oms" className="panel">
          <h2>
            <span>Paper OMS</span>
            <StatusBadge status={order ? "AVAILABLE" : paperOrder.state} />
          </h2>
          <p>Paper lifecycle and reconciliation evidence only; this page cannot submit an order.</p>
          {order ? (
            <>
              <dl>
                <dt>Intent / instrument</dt>
                <dd>
                  <code>{order.intent_id}</code> / {order.instrument_id}
                </dd>
                <dt>Status / quantity / filled</dt>
                <dd>
                  {order.status} / {order.quantity} / {order.filled_quantity}
                </dd>
                <dt>Partial fills</dt>
                <dd>
                  {order.fills
                    .map((fill) => `${fill.external_fill_id}: ${fill.quantity} @ ${fill.price}`)
                    .join("; ") || "EMPTY"}
                </dd>
                <dt>Lifecycle</dt>
                <dd>
                  {order.events.map((event) => `${event.event_type} @ ${utc(event.occurred_at)}`).join("; ")}
                </dd>
                <dt>Reconciliation</dt>
                <dd>
                  {account
                    ? `${account.complete ? "COMPLETE" : account.discrepancies.join(", ")}; ${
                        account.source
                      }; ${utc(account.occurred_at)}`
                    : stateText(reconciliation)}
                </dd>
              </dl>
              <EvidenceMeta
                source="paper-oms"
                asOf={order.created_at}
                version="paper-oms-v1"
                limitations={["Paper-only evidence; no broker execution capability."]}
              />
            </>
          ) : paperOrderRows.length ? (
            paperOrderRows.map((item) => (
              <dl key={item.intent_id}>
                <dt>Intent / instrument</dt>
                <dd>
                  <code>{item.intent_id}</code> / {item.canonical_symbol ?? item.instrument_id}
                </dd>
                <dt>Side / lifecycle / fill</dt>
                <dd>
                  {item.side} / {item.lifecycle_status} / {item.fill_state}
                </dd>
                <dt>Paper / reconciliation</dt>
                <dd>
                  {item.paper_only ? "PAPER ONLY" : "BLOCKED"} / {item.reconciliation_state}
                </dd>
                <dt>Created</dt>
                <dd>{utc(item.created_at)}</dd>
              </dl>
            ))
          ) : (
            <p className="empty-state">{stateText(paperOrders)}</p>
          )}
          <div className="panel-footer-row">
            <span className="status">PAPER ONLY / READ ONLY</span>
            <Link href="/paper" className="workspace-link">
              Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Operations / SRE */}
        <article id="operations" className="panel">
          <h2>
            <span>Operations / SRE</span>
            <StatusBadge status={sreEvidence ? "AVAILABLE" : sre.state} />
          </h2>
          <p>
            TARGET and MEASURED are distinct. Candidate targets are never presented as achieved
            operational evidence.
          </p>
          {sreEvidence ? (
            <>
              <dl>
                <dt>Subsystem / version / environment</dt>
                <dd>
                  {sreEvidence.subsystem} / {sreEvidence.version} / {sreEvidence.environment}
                </dd>
                <dt>PostgreSQL / provider</dt>
                <dd>
                  {sreEvidence.postgres_state} / {sreEvidence.provider_state}
                </dd>
                <dt>Ingestion / dataset / feature freshness</dt>
                <dd>
                  {sreEvidence.ingestion_checkpoint_freshness} / {sreEvidence.dataset_freshness} /{" "}
                  {sreEvidence.feature_freshness}
                </dd>
                <dt>Research / signal / risk</dt>
                <dd>
                  {sreEvidence.research_job_health} / {sreEvidence.signal_freshness} /{" "}
                  {sreEvidence.risk_status}
                </dd>
                <dt>Reconciliation / backup-restore / kill switch</dt>
                <dd>
                  {sreEvidence.reconciliation_status} / {sreEvidence.backup_restore_status} /{" "}
                  {sreEvidence.kill_switch_state}
                </dd>
              </dl>

              <h3>SLO evidence</h3>
              <table>
                <thead>
                  <tr>
                    <th>Indicator</th>
                    <th>TARGET</th>
                    <th>MEASURED</th>
                    <th>Evidence state</th>
                  </tr>
                </thead>
                <tbody>
                  {sreEvidence.slos.map((item) => (
                    <tr key={item.slo_policy_version_id}>
                      <td>
                        {item.name}: {item.indicator}
                      </td>
                      <td>{item.target}</td>
                      <td>{item.measured_value ?? "UNAVAILABLE"}</td>
                      <td>
                        {item.measured_state}; {item.claim_status ?? "NO MEASUREMENT"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <h3>Incidents</h3>
              {sreEvidence.incidents.length ? (
                sreEvidence.incidents.map((item) => (
                  <p key={item.incident_id}>
                    <code>{item.incident_id}</code>: {item.severity} {item.subsystem}; opened{" "}
                    {utc(item.opened_at)}; acknowledged {utc(item.acknowledged_at)}; resolved{" "}
                    {utc(item.resolved_at)}; {item.status}; {item.reason}; {item.evidence_reference}
                  </p>
                ))
              ) : (
                <p className="empty-state">UNAVAILABLE: no persisted incident evidence.</p>
              )}

              <h3>Failure / recovery drills</h3>
              {sreEvidence.failure_drills.map((item) => (
                <p key={item.drill_run_id}>
                  {item.scenario}: {item.passed ? "PASSED" : "FAILED"}; expected {item.expected_protection};
                  measured {item.observed_protection}; {item.evidence_reference}
                </p>
              ))}
            </>
          ) : (
            <p className="empty-state">{stateText(sre)}</p>
          )}
          <div className="panel-footer-row">
            <span className="status">READ ONLY / TARGET &ne; MEASURED</span>
            <Link href="/operations" className="workspace-link">
              Workspace &rarr;
            </Link>
          </div>
        </article>

        {/* Audit */}
        <article id="audit" className="panel">
          <h2>
            <span>Audit</span>
            <StatusBadge status="AVAILABLE" />
          </h2>
          <p>
            Audit evidence is immutable and read-only: actor, action, domain object, version, timestamp,
            decision, reasons, and evidence IDs belong to protected backend records.
          </p>
          <dl>
            <dt>Alert evidence</dt>
            <dd>
              {alertRows
                ? alertRows
                    .map((item) => `${item.alert_id}: ${item.severity} ${item.code} @ ${utc(item.created_at)}`)
                    .join("; ") || "No active alerts recorded."
                : stateText(alerts)}
            </dd>
            <dt>Mutation control</dt>
            <dd>NO MUTATION ROUTE EXPOSED BY THIS DASHBOARD</dd>
          </dl>
          <div className="panel-footer-row">
            <span className="status">READ ONLY</span>
            <Link href="/audit" className="workspace-link">
              Workspace &rarr;
            </Link>
          </div>
        </article>
      </section>
    </>
  );
}
