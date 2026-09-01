import {
  getWorkspaceContext,
  getInvestmentThesisEvidence,
  getInvestmentThesisDiscovery,
  getInvestmentPortfolioEvidence,
  getInvestmentPortfolioDiscovery,
  stateText,
  utc,
} from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceMeta } from "../../components/evidence-meta";

export const dynamic = "force-dynamic";

export default async function InvestmentsPage() {
  const ctx = await getWorkspaceContext();
  const [thesis, theses, portfolio, portfolios] = await Promise.all([
    getInvestmentThesisEvidence(ctx),
    getInvestmentThesisDiscovery(ctx),
    getInvestmentPortfolioEvidence(ctx),
    getInvestmentPortfolioDiscovery(ctx),
  ]);

  const investment = thesis.state === "AVAILABLE" ? thesis.value : undefined;
  const investmentPortfolio = portfolio.state === "AVAILABLE" ? portfolio.value : undefined;
  const thesisRows = theses.state === "AVAILABLE" ? theses.value.items : [];
  const portfolioRows = portfolios.state === "AVAILABLE" ? portfolios.value.items : [];

  const latestValuation = investment?.valuations.at(-1);
  const company = investment?.company_research.at(-1);
  const latestPerformance = investmentPortfolio?.performance.at(-1);

  return (
    <>
      <PageHeader
        eyebrow="INVESTING WORKSPACE"
        title="Investment Engine V2"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Long-horizon investment research, company thesis valuations, and portfolio assessments. NOT A REAL INVESTMENT RECOMMENDATION. Deep valuation modeler arriving in Module 2B.
      </div>

      <div className="grid-2col margin-bottom-20">
        <article className="panel">
          <h2>
            <span>Bound Investment Thesis</span>
            <StatusBadge status={investment ? "AVAILABLE" : thesis.state} />
          </h2>
          <p className="warning">
            NOT A REAL INVESTMENT RECOMMENDATION. Long-horizon research is separate from trading.
          </p>

          {investment ? (
            <>
              <dl>
                <dt>Instrument / Statement</dt>
                <dd>
                  <strong>{investment.thesis.instrument_id}</strong>
                  <br />
                  {investment.thesis.statement}
                </dd>
                <dt>Status / Version</dt>
                <dd>
                  <StatusBadge status={investment.thesis.status} /> &bull; Version: {investment.thesis.version}
                </dd>
                <dt>Catalysts &amp; Risks</dt>
                <dd>
                  Catalysts: {investment.thesis.catalysts.join(", ")}
                  <br />
                  Risks: {investment.thesis.risks.join(", ")}
                </dd>
                <dt>Scenario Analysis</dt>
                <dd>
                  {company ? (
                    <div>
                      <div>Bear: {company.bear_case}</div>
                      <div>Base: {company.base_case}</div>
                      <div>Bull: {company.bull_case}</div>
                    </div>
                  ) : (
                    "UNAVAILABLE"
                  )}
                </dd>
                <dt>Intrinsic Valuation</dt>
                <dd>
                  {latestValuation ? (
                    <div>
                      <strong className="metric-value">{latestValuation.intrinsic_value_per_share}</strong> &bull; Model: {latestValuation.model_version}
                    </div>
                  ) : (
                    "UNAVAILABLE"
                  )}
                </dd>
                <dt>Review Outcome</dt>
                <dd>{investment.reviews.at(-1)?.outcome ?? "UNAVAILABLE"}</dd>
              </dl>
              <EvidenceMeta
                source="investment-store"
                asOf={investment.as_of}
                version={investment.thesis.version}
                limitations={["Fixture valuation is not investment advice."]}
              />
            </>
          ) : (
            <p className="empty-state">{stateText(thesis)}</p>
          )}
          <span className="status margin-top-auto">REVIEW ONLY / NO BUY OR SELL</span>
        </article>

        <article className="panel">
          <h2>
            <span>Bound Investment Portfolio</span>
            <StatusBadge status={investmentPortfolio ? "AVAILABLE" : portfolio.state} />
          </h2>
          {investmentPortfolio ? (
            <>
              <dl>
                <dt>Portfolio ID / Review</dt>
                <dd>
                  <code>{investmentPortfolio.portfolio_id}</code> &bull;{" "}
                  <StatusBadge
                    status={investmentPortfolio.assessment.approved ? "APPROVED" : "REVIEW"}
                    label={investmentPortfolio.assessment.approved ? "WITHIN LIMITS" : investmentPortfolio.assessment.reasons.join(", ")}
                  />
                </dd>
                <dt>Total Value / Performance</dt>
                <dd>
                  Value: <strong className="metric-value">{investmentPortfolio.assessment.total_value}</strong>
                  <br />
                  {latestPerformance ? (
                    <span>
                      NAV: {latestPerformance.net_asset_value} &bull; Return: {latestPerformance.cumulative_return}
                    </span>
                  ) : (
                    "UNAVAILABLE"
                  )}
                </dd>
                <dt>Holdings Provenance</dt>
                <dd>
                  {investmentPortfolio.holdings.map((h) => `${h.instrument_id}: ${h.source_reference}`).join("; ") || "EMPTY"}
                </dd>
                <dt>Rebalance Candidate</dt>
                <dd>{investmentPortfolio.rebalance_decisions.at(-1)?.rationale ?? "EMPTY"}</dd>
              </dl>
            </>
          ) : (
            <p className="empty-state">{stateText(portfolio)}</p>
          )}
          <span className="status margin-top-auto">REVIEW ONLY</span>
        </article>
      </div>

      <div className="grid-2col">
        <article className="panel">
          <h2>
            <span>Discovered Theses</span>
            <StatusBadge status={theses.state} />
          </h2>
          {thesisRows.length ? (
            <table>
              <thead>
                <tr>
                  <th>Symbol / Thesis ID</th>
                  <th>Status &amp; Version</th>
                  <th>Review State</th>
                  <th>Classification</th>
                </tr>
              </thead>
              <tbody>
                {thesisRows.map((item) => (
                  <tr key={item.thesis_id}>
                    <td>
                      <strong>{item.canonical_symbol ?? item.instrument_id}</strong>
                      <br />
                      <code>{item.thesis_id}</code>
                    </td>
                    <td>
                      <StatusBadge status={item.status} /> &bull; v{item.thesis_version}
                    </td>
                    <td>{item.review_state ?? "UNAVAILABLE"}</td>
                    <td>{item.synthetic_demo ? "SYNTHETIC / DEMO" : "RESEARCH"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty-state">{stateText(theses)}</p>
          )}
        </article>

        <article className="panel">
          <h2>
            <span>Discovered Portfolios</span>
            <StatusBadge status={portfolios.state} />
          </h2>
          {portfolioRows.length ? (
            <table>
              <thead>
                <tr>
                  <th>Portfolio ID</th>
                  <th>Review Status</th>
                  <th>Holdings Count</th>
                  <th>Classification</th>
                </tr>
              </thead>
              <tbody>
                {portfolioRows.map((item) => (
                  <tr key={item.portfolio_id}>
                    <td>
                      <code>{item.portfolio_id}</code>
                    </td>
                    <td>
                      <StatusBadge status={item.review_status} />
                    </td>
                    <td>{utc(item.as_of)} / {item.holdings_count} item(s)</td>
                    <td>{item.evidence_classification}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty-state">{stateText(portfolios)}</p>
          )}
        </article>
      </div>
    </>
  );
}
