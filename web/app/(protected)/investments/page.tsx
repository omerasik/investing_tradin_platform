import Link from "next/link";
import {
  getWorkspaceContext,
  getInvestmentThesisDiscovery,
  getInvestmentThesis,
  getInvestmentPortfolioDiscovery,
  getInvestmentPortfolio,
  stateText,
  utc,
  type Thesis,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { FilterBar } from "../../components/filter-bar";
import { DataTable } from "../../components/data-table";
import { Pagination } from "../../components/pagination";
import { StatusBadge } from "../../components/status-badge";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";
import { DemoEvidenceBanner } from "../../components/demo-evidence-banner";

export const dynamic = "force-dynamic";

function latestCompanyResearch(data: Thesis) {
  return data.company_research.length > 0 ? data.company_research[data.company_research.length - 1] : undefined;
}

export default async function InvestmentsPage({
  searchParams,
}: {
  searchParams?: Promise<{
    instrument?: string; status?: string; review_state?: string; synthetic_demo?: string;
    thesis?: string; thesis_offset?: string;
    portfolio_status?: string; account_id?: string; portfolio?: string; portfolio_offset?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const instrument = resolvedParams?.instrument?.trim() || undefined;
  const status = resolvedParams?.status?.trim() || undefined;
  const reviewState = resolvedParams?.review_state?.trim() || undefined;
  const syntheticDemoParam = resolvedParams?.synthetic_demo?.trim();
  const syntheticDemo = syntheticDemoParam === "true" ? true : syntheticDemoParam === "false" ? false : undefined;
  const thesisOffset = Number(resolvedParams?.thesis_offset ?? 0) || 0;

  const portfolioStatus = resolvedParams?.portfolio_status?.trim() || undefined;
  const accountId = resolvedParams?.account_id?.trim() || undefined;
  const portfolioOffset = Number(resolvedParams?.portfolio_offset ?? 0) || 0;

  const ctx = await getWorkspaceContext();

  const thesisDiscoveryResult = await getInvestmentThesisDiscovery(ctx, {
    instrument, status, review_state: reviewState, synthetic_demo: syntheticDemo, limit: 20, offset: thesisOffset,
  });
  const thesisDiscoveryPage = thesisDiscoveryResult.state === "AVAILABLE" ? thesisDiscoveryResult.value : undefined;
  const thesisItems = thesisDiscoveryPage?.items ?? [];
  const selectedThesisId = resolvedParams?.thesis || (thesisItems.length > 0 ? thesisItems[0].thesis_id : undefined);
  const thesisResult = selectedThesisId ? await getInvestmentThesis(ctx, { thesisId: selectedThesisId }) : undefined;
  const thesisDetail = thesisResult?.state === "AVAILABLE" ? thesisResult.value : undefined;
  const selectedDiscoveryRow = thesisItems.find((item) => item.thesis_id === selectedThesisId);

  const portfolioDiscoveryResult = await getInvestmentPortfolioDiscovery(ctx, {
    status: portfolioStatus, account_id: accountId, limit: 20, offset: portfolioOffset,
  });
  const portfolioDiscoveryPage = portfolioDiscoveryResult.state === "AVAILABLE" ? portfolioDiscoveryResult.value : undefined;
  const portfolioItems = portfolioDiscoveryPage?.items ?? [];
  const selectedPortfolioId = resolvedParams?.portfolio || (portfolioItems.length > 0 ? portfolioItems[0].portfolio_id : undefined);
  const portfolioResult = selectedPortfolioId ? await getInvestmentPortfolio(ctx, { portfolioId: selectedPortfolioId }) : undefined;
  const portfolioDetail = portfolioResult?.state === "AVAILABLE" ? portfolioResult.value : undefined;
  const selectedPortfolioRow = portfolioItems.find((item) => item.portfolio_id === selectedPortfolioId);

  const thesisBaseFilters = {
    instrument: instrument ?? "", status: status ?? "", review_state: reviewState ?? "",
    synthetic_demo: syntheticDemoParam ?? "", thesis: selectedThesisId ?? "",
  };
  const portfolioBaseFilters = {
    portfolio_status: portfolioStatus ?? "", account_id: accountId ?? "", portfolio: selectedPortfolioId ?? "",
  };

  const research = thesisDetail ? latestCompanyResearch(thesisDetail) : undefined;
  const historicalResearch = thesisDetail ? thesisDetail.company_research.slice(0, -1) : [];

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Investment Research Workspace"
        subtitle="Long-horizon investment thesis, valuation, and portfolio review evidence, read directly from persisted authorities. No thesis or news event here can generate an order."
        status={thesisDiscoveryResult.state}
        asOf={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>NOT A REAL INVESTMENT RECOMMENDATION.</strong> REVIEW ONLY. NO BUY / SELL AUTHORITY. Investment
        research is explicitly separate from systematic Portfolio Construction (see <Link href="/portfolio" className="workspace-link">/portfolio</Link>).
      </div>

      <section aria-label="Investment Thesis Discovery">
        <h2>Thesis Discovery</h2>
        <FilterBar
          groups={[
            {
              id: "filter-thesis-synthetic-demo", name: "synthetic_demo", label: "Synthetic / Demo State",
              defaultValue: syntheticDemoParam ?? "ALL",
              options: [
                { label: "All", value: "ALL" },
                { label: "Synthetic / Demo Only", value: "true" },
                { label: "Non-Demo Only", value: "false" },
              ],
            },
          ]}
          resetHref="/investments"
          ariaLabel="Investment Thesis Discovery Filters"
        >
          <div className="filter-item">
            <label htmlFor="filter-thesis-instrument" className="filter-label">Instrument / Symbol</label>
            <input id="filter-thesis-instrument" name="instrument" type="text" defaultValue={instrument ?? ""} className="filter-select" />
          </div>
          <div className="filter-item">
            <label htmlFor="filter-thesis-status" className="filter-label">Thesis Status</label>
            <input id="filter-thesis-status" name="status" type="text" defaultValue={status ?? ""} className="filter-select" />
          </div>
          <div className="filter-item">
            <label htmlFor="filter-thesis-review-state" className="filter-label">Review Status</label>
            <input id="filter-thesis-review-state" name="review_state" type="text" defaultValue={reviewState ?? ""} className="filter-select" />
          </div>
        </FilterBar>

        <div className="workspace-split-layout">
          <div>
            <article className="panel">
              <h3>
                <span>Thesis Discovery Table</span>
                <span className="badge tabular-num">{thesisItems.length} returned</span>
              </h3>
              {thesisItems.length > 0 ? (
                <>
                  <DataTable caption="Investment Thesis Discovery" ariaLabel="Investment Thesis Discovery">
                    <thead>
                      <tr>
                        <th scope="col">Symbol / Instrument</th>
                        <th scope="col">Thesis Status</th>
                        <th scope="col">Version</th>
                        <th scope="col">As-Of</th>
                        <th scope="col">Review State</th>
                        <th scope="col">Evidence Classification</th>
                        <th scope="col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {thesisItems.map((item) => {
                        const isSelected = item.thesis_id === selectedThesisId;
                        const params = new URLSearchParams();
                        for (const [key, value] of Object.entries(thesisBaseFilters)) {
                          if (value && key !== "thesis") params.set(key, value);
                        }
                        for (const [key, value] of Object.entries(portfolioBaseFilters)) {
                          if (value) params.set(key, value);
                        }
                        if (thesisOffset > 0) params.set("thesis_offset", String(thesisOffset));
                        params.set("thesis", item.thesis_id);
                        return (
                          <tr key={item.thesis_id} className={isSelected ? "row-selected" : ""}>
                            <td>{item.canonical_symbol ?? item.instrument_id}</td>
                            <td><StatusBadge status={item.status} /></td>
                            <td>{item.thesis_version}</td>
                            <td><small>{utc(item.as_of)}</small></td>
                            <td>{item.review_state ?? "UNAVAILABLE"}</td>
                            <td><small>{item.evidence_classification}</small></td>
                            <td className="cell-action">
                              <Link href={`/investments?${params.toString()}`} className="table-link" aria-label={`Inspect thesis ${item.thesis_id}`}>
                                Inspect &rarr;
                              </Link>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </DataTable>
                  {thesisDiscoveryPage && (
                    <Pagination
                      limit={thesisDiscoveryPage.page.limit} offset={thesisDiscoveryPage.page.offset}
                      returned={thesisDiscoveryPage.page.returned} hasMore={thesisDiscoveryPage.page.has_more}
                      basePath="/investments" searchParams={{ ...thesisBaseFilters, ...portfolioBaseFilters }}
                      offsetParam="thesis_offset" limitParam="thesis_limit"
                    />
                  )}
                </>
              ) : (
                <p className="empty-state">{stateText(thesisDiscoveryResult)}</p>
              )}
            </article>
          </div>

          <aside aria-label="Investment Thesis Inspector">
            {selectedDiscoveryRow ? (
              <div className="inspector-card">
                <header className="inspector-header">
                  <div className="inspector-title-group">
                    <div className="inspector-title-row">
                      <h2>{selectedDiscoveryRow.canonical_symbol ?? selectedDiscoveryRow.instrument_id}</h2>
                      <StatusBadge status={selectedDiscoveryRow.status} />
                    </div>
                    <span className="badge">{selectedDiscoveryRow.evidence_classification}</span>
                  </div>
                </header>

                {selectedDiscoveryRow.synthetic_demo && (
                  <DemoEvidenceBanner message="This thesis was generated by an explicit local engineering scenario. It is not a real investment recommendation." />
                )}

                <div className="inspector-section inspector-section-first">
                  <span className="inspector-section-title">Investment Thesis</span>
                  <KeyValueGrid
                    columns={2}
                    items={[
                      { key: "instrument", label: "Instrument", value: selectedDiscoveryRow.canonical_symbol ?? selectedDiscoveryRow.instrument_id },
                      { key: "version", label: "Version", value: thesisDetail?.thesis.version ?? selectedDiscoveryRow.thesis_version },
                      { key: "status", label: "Status", value: <StatusBadge status={selectedDiscoveryRow.status} /> },
                      { key: "as-of", label: "As-Of", value: utc(selectedDiscoveryRow.as_of) },
                    ]}
                  />
                  <p>{thesisDetail?.thesis.statement ?? "UNAVAILABLE"}</p>
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Quality / Company Research</span>
                  {research ? (
                    <>
                      <KeyValueGrid
                        columns={2}
                        items={[
                          { key: "as-of", label: "As-Of", value: utc(research.as_of) },
                          { key: "position-sizing", label: "Position Sizing Rationale", value: research.position_sizing_rationale },
                          { key: "replacements", label: "Replacement Candidates", value: research.replacement_candidates.join(", ") || "UNAVAILABLE" },
                          { key: "evidence-ids", label: "Evidence References", value: research.evidence_ids.join(", ") || "UNAVAILABLE" },
                        ]}
                      />
                      {historicalResearch.length > 0 && (
                        <p><small>{historicalResearch.length} earlier company-research revision(s) on record (not shown; latest revision above is authoritative).</small></p>
                      )}
                    </>
                  ) : (
                    <p className="empty-state">UNAVAILABLE &mdash; no persisted company research for this thesis.</p>
                  )}
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Scenario Analysis (BEAR / BASE / BULL)</span>
                  <p><small>No scenario is implied to be expected or guaranteed; all three are persisted research narratives, not forecasts.</small></p>
                  {research ? (
                    <>
                      <div className="margin-bottom-16">
                        <h3>Bear Case</h3>
                        <p>{research.bear_case}</p>
                      </div>
                      <div className="margin-bottom-16">
                        <h3>Base Case</h3>
                        <p>{research.base_case}</p>
                      </div>
                      <div className="margin-bottom-16">
                        <h3>Bull Case</h3>
                        <p>{research.bull_case}</p>
                      </div>
                    </>
                  ) : (
                    <p className="empty-state">UNAVAILABLE</p>
                  )}
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Valuation</span>
                  {thesisDetail && thesisDetail.valuations.length > 0 ? (
                    <DataTable caption="Persisted Valuation Records" ariaLabel="Persisted Valuation Records">
                      <thead>
                        <tr>
                          <th scope="col">Model Version</th>
                          <th scope="col">Intrinsic Value / Share</th>
                          <th scope="col">Currency</th>
                          <th scope="col">As-Of</th>
                        </tr>
                      </thead>
                      <tbody>
                        {thesisDetail.valuations.map((valuation) => (
                          <tr key={valuation.valuation_id}>
                            <td>{valuation.model_version}</td>
                            <td className="tabular-num">{valuation.intrinsic_value_per_share}</td>
                            <td>{valuation.currency}</td>
                            <td><small>{utc(valuation.as_of)}</small></td>
                          </tr>
                        ))}
                      </tbody>
                    </DataTable>
                  ) : (
                    <p className="empty-state">UNAVAILABLE &mdash; no persisted valuation record for this thesis.</p>
                  )}
                  <p><small>No upside/downside is computed against a current market price on this platform.</small></p>
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Catalysts</span>
                  {research && research.catalysts.length > 0 ? (
                    <ul>{research.catalysts.map((item, index) => <li key={index}>{item}</li>)}</ul>
                  ) : thesisDetail && thesisDetail.thesis.catalysts.length > 0 ? (
                    <ul>{thesisDetail.thesis.catalysts.map((item, index) => <li key={index}>{item}</li>)}</ul>
                  ) : (
                    <p className="empty-state">UNAVAILABLE</p>
                  )}
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Risks</span>
                  {thesisDetail && thesisDetail.thesis.risks.length > 0 ? (
                    <ul>{thesisDetail.thesis.risks.map((item, index) => <li key={index}>{item}</li>)}</ul>
                  ) : (
                    <p className="empty-state">UNAVAILABLE</p>
                  )}
                </div>

                <div className="inspector-section inspector-section-warning">
                  <span className="inspector-section-title">What Would Invalidate This Thesis?</span>
                  {research && research.invalidation_conditions.length > 0 ? (
                    <ul>{research.invalidation_conditions.map((item, index) => <li key={index}>{item}</li>)}</ul>
                  ) : (
                    <p className="empty-state">UNAVAILABLE &mdash; no persisted invalidation condition.</p>
                  )}
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Review History</span>
                  {thesisDetail && thesisDetail.reviews.length > 0 ? (
                    <DataTable caption="Research Review Timeline" ariaLabel="Research Review Timeline">
                      <thead>
                        <tr>
                          <th scope="col">Reviewed At</th>
                          <th scope="col">Outcome</th>
                          <th scope="col">Rationale</th>
                          <th scope="col">Next Review</th>
                        </tr>
                      </thead>
                      <tbody>
                        {thesisDetail.reviews.map((review) => (
                          <tr key={review.review_id}>
                            <td><small>{utc(review.reviewed_at)}</small></td>
                            <td><StatusBadge status={review.outcome} /></td>
                            <td><small>{review.rationale}</small></td>
                            <td><small>{utc(review.next_review_at)}</small></td>
                          </tr>
                        ))}
                      </tbody>
                    </DataTable>
                  ) : (
                    <p className="empty-state">UNAVAILABLE &mdash; no review recorded. No automatic approval is implied.</p>
                  )}
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Thesis Drift / Review Awareness</span>
                  <KeyValueGrid
                    columns={2}
                    items={[
                      { key: "as-of", label: "Thesis As-Of", value: utc(selectedDiscoveryRow.as_of) },
                      { key: "latest-review", label: "Latest Review Date", value: thesisDetail?.reviews.at(-1) ? utc(thesisDetail.reviews.at(-1)!.reviewed_at) : "UNAVAILABLE" },
                      { key: "latest-valuation", label: "Latest Valuation Date", value: thesisDetail?.valuations.at(-1) ? utc(thesisDetail.valuations.at(-1)!.as_of) : "UNAVAILABLE" },
                      { key: "linked-news", label: "Related Persisted News", value: <Link href={`/news?instrument=${encodeURIComponent(selectedDiscoveryRow.instrument_id)}`} className="workspace-link">View linked events &rarr;</Link> },
                    ]}
                  />
                  <p><small>No staleness flag is shown: this platform has no documented deterministic review-age rule yet, so only timestamps are displayed.</small></p>
                </div>

                <ProvenancePanel
                  source="Investment Research Authority"
                  recordId={selectedDiscoveryRow.thesis_id}
                  version={selectedDiscoveryRow.thesis_version}
                  asOf={selectedDiscoveryRow.as_of}
                  limitations={["Synthetic/demo evidence unless a real authorized provider is on record.", "No investment-advice functionality exists on this platform."]}
                />
              </div>
            ) : (
              <div className="inspector-card">
                <p className="empty-state">{stateText(thesisDiscoveryResult)}</p>
              </div>
            )}
          </aside>
        </div>
      </section>

      <section aria-label="Investment Portfolio">
        <h2>Investment Portfolio</h2>
        <p><small>Explicitly separate from systematic Portfolio Construction V2 (see <Link href="/portfolio" className="workspace-link">/portfolio</Link>).</small></p>

        <FilterBar
          resetHref="/investments"
          ariaLabel="Investment Portfolio Discovery Filters"
        >
          <div className="filter-item">
            <label htmlFor="filter-portfolio-status" className="filter-label">Review Status</label>
            <input id="filter-portfolio-status" name="portfolio_status" type="text" defaultValue={portfolioStatus ?? ""} className="filter-select" />
          </div>
          <div className="filter-item">
            <label htmlFor="filter-portfolio-account" className="filter-label">Portfolio / Account ID</label>
            <input id="filter-portfolio-account" name="account_id" type="text" defaultValue={accountId ?? ""} className="filter-select" />
          </div>
        </FilterBar>

        <div className="workspace-split-layout">
          <div>
            <article className="panel">
              <h3>
                <span>Portfolio Discovery</span>
                <span className="badge tabular-num">{portfolioItems.length} returned</span>
              </h3>
              {portfolioItems.length > 0 ? (
                <>
                  <DataTable caption="Investment Portfolio Discovery" ariaLabel="Investment Portfolio Discovery">
                    <thead>
                      <tr>
                        <th scope="col">Portfolio</th>
                        <th scope="col">As-Of</th>
                        <th scope="col">Review Status</th>
                        <th scope="col">Holdings Count</th>
                        <th scope="col">Evidence Classification</th>
                        <th scope="col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {portfolioItems.map((item) => {
                        const isSelected = item.portfolio_id === selectedPortfolioId;
                        const params = new URLSearchParams();
                        for (const [key, value] of Object.entries(thesisBaseFilters)) {
                          if (value) params.set(key, value);
                        }
                        for (const [key, value] of Object.entries(portfolioBaseFilters)) {
                          if (value && key !== "portfolio") params.set(key, value);
                        }
                        if (portfolioOffset > 0) params.set("portfolio_offset", String(portfolioOffset));
                        params.set("portfolio", item.portfolio_id);
                        return (
                          <tr key={item.portfolio_id} className={isSelected ? "row-selected" : ""}>
                            <td>{item.portfolio_id}</td>
                            <td><small>{utc(item.as_of)}</small></td>
                            <td><StatusBadge status={item.review_status} /></td>
                            <td className="tabular-num">{item.holdings_count}</td>
                            <td><small>{item.evidence_classification}</small></td>
                            <td className="cell-action">
                              <Link href={`/investments?${params.toString()}`} className="table-link" aria-label={`Inspect portfolio ${item.portfolio_id}`}>
                                Inspect &rarr;
                              </Link>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </DataTable>
                  {portfolioDiscoveryPage && (
                    <Pagination
                      limit={portfolioDiscoveryPage.page.limit} offset={portfolioDiscoveryPage.page.offset}
                      returned={portfolioDiscoveryPage.page.returned} hasMore={portfolioDiscoveryPage.page.has_more}
                      basePath="/investments" searchParams={{ ...thesisBaseFilters, ...portfolioBaseFilters }}
                      offsetParam="portfolio_offset" limitParam="portfolio_limit"
                    />
                  )}
                </>
              ) : (
                <p className="empty-state">{stateText(portfolioDiscoveryResult)}</p>
              )}
            </article>
          </div>

          <aside aria-label="Investment Portfolio Inspector">
            {selectedPortfolioRow ? (
              <div className="inspector-card">
                <header className="inspector-header">
                  <div className="inspector-title-group">
                    <div className="inspector-title-row">
                      <h2>{selectedPortfolioRow.portfolio_id}</h2>
                      <StatusBadge status={selectedPortfolioRow.review_status} />
                    </div>
                    <span className="badge">{selectedPortfolioRow.evidence_classification}</span>
                  </div>
                </header>

                <div className="inspector-section inspector-section-first">
                  <span className="inspector-section-title">Portfolio Summary</span>
                  <KeyValueGrid
                    columns={2}
                    items={[
                      { key: "total-value", label: "Total Value", value: portfolioDetail?.assessment.total_value ?? "UNAVAILABLE" },
                      { key: "assessment", label: "Assessment State", value: portfolioDetail ? (portfolioDetail.assessment.approved ? "WITHIN LIMITS" : portfolioDetail.assessment.reasons.join(", ") || "REVIEW REQUIRED") : "UNAVAILABLE" },
                      { key: "as-of", label: "As-Of", value: utc(selectedPortfolioRow.as_of) },
                      { key: "nav", label: "Latest NAV / Cumulative Return", value: portfolioDetail?.performance.at(-1) ? `${portfolioDetail.performance.at(-1)!.net_asset_value} / ${portfolioDetail.performance.at(-1)!.cumulative_return}` : "UNAVAILABLE" },
                    ]}
                  />
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Holdings</span>
                  {portfolioDetail && portfolioDetail.holdings.length > 0 ? (
                    <DataTable caption="Investment Portfolio Holdings" ariaLabel="Investment Portfolio Holdings">
                      <thead>
                        <tr>
                          <th scope="col">Instrument</th>
                          <th scope="col">Market Value</th>
                          <th scope="col">Observed At</th>
                          <th scope="col">Provenance</th>
                        </tr>
                      </thead>
                      <tbody>
                        {portfolioDetail.holdings.map((holding, index) => (
                          <tr key={`${holding.instrument_id}-${index}`}>
                            <td>{holding.instrument_id}</td>
                            <td className="tabular-num">{holding.market_value}</td>
                            <td><small>{utc(holding.observed_at)}</small></td>
                            <td><small>{holding.source_reference}</small></td>
                          </tr>
                        ))}
                      </tbody>
                    </DataTable>
                  ) : (
                    <p className="empty-state">EMPTY &mdash; no persisted holding for this portfolio.</p>
                  )}
                  <p><small>Quantities, cost basis, and weights are shown only where actually persisted; none are inferred.</small></p>
                </div>

                <div className="inspector-section">
                  <span className="inspector-section-title">Rebalance Candidates</span>
                  {portfolioDetail && portfolioDetail.rebalance_decisions.length > 0 ? (
                    portfolioDetail.rebalance_decisions.map((decision) => (
                      <div key={decision.decision_id} className="margin-bottom-16">
                        <span className="badge badge-warning">REBALANCE CANDIDATE &mdash; REVIEW DECISION</span>
                        <KeyValueGrid
                          columns={2}
                          items={[
                            { key: "rationale", label: "Rationale", value: decision.rationale },
                            { key: "approved-by", label: "Approved By (Reviewer)", value: decision.approved_by },
                          ]}
                        />
                      </div>
                    ))
                  ) : (
                    <p className="empty-state">EMPTY &mdash; no persisted rebalance candidate for this portfolio.</p>
                  )}
                  <p><small>No apply/execute control exists here.</small></p>
                </div>

                <ProvenancePanel
                  source="Investment Portfolio Authority"
                  recordId={selectedPortfolioRow.portfolio_id}
                  asOf={selectedPortfolioRow.as_of}
                  limitations={["Review-only; no execution authority.", "Portfolio evidence classification is fail-closed: absence of a synthetic marker never implies real-provider data."]}
                />
              </div>
            ) : (
              <div className="inspector-card">
                <p className="empty-state">{stateText(portfolioDiscoveryResult)}</p>
              </div>
            )}
          </aside>
        </div>
      </section>

      <div className="panel-footer-row">
        <span className="status">NOT A REAL INVESTMENT RECOMMENDATION / REVIEW ONLY / NO BUY OR SELL AUTHORITY</span>
      </div>
    </div>
  );
}
