import React from "react";
import Link from "next/link";
import {
  getWorkspaceContext,
  getFeatureDefinitions,
  getFeatureDefinition,
  getFeatureMaterializations,
  stateText,
  utc,
} from "../../lib/data-access";
import { WorkspaceToolbar } from "../../components/workspace-toolbar";
import { QualityStateBadge } from "../../components/quality-state-badge";
import { DatasetVersionBadge } from "../../components/dataset-version-badge";
import { DemoEvidenceBanner } from "../../components/demo-evidence-banner";
import { FilterBar } from "../../components/filter-bar";
import { DataTable } from "../../components/data-table";
import { PITTimestampGroup } from "../../components/pit-timestamp-group";
import { KeyValueGrid } from "../../components/key-value-grid";
import { ProvenancePanel } from "../../components/provenance-panel";

export const dynamic = "force-dynamic";

export default async function FeaturesPage({
  searchParams,
}: {
  searchParams?: Promise<{
    family?: string;
    feature_id?: string;
    instrument?: string;
    dataset_version?: string;
    decision_time?: string;
    offset?: string;
    limit?: string;
  }>;
}) {
  const resolvedParams = await searchParams;
  const family = resolvedParams?.family || undefined;
  const customFeatureId = resolvedParams?.feature_id || undefined;
  const customInstrument = resolvedParams?.instrument || undefined;
  const customDataset = resolvedParams?.dataset_version || undefined;
  const customDecisionTime = resolvedParams?.decision_time || undefined;

  const ctx = await getWorkspaceContext();

  const [definitionsResult, defaultDefResult] = await Promise.all([
    getFeatureDefinitions(ctx, { family, limit: 50, offset: 0 }),
    getFeatureDefinition(ctx, customFeatureId),
  ]);

  const definitionsPage = definitionsResult.state === "AVAILABLE" ? definitionsResult.value : undefined;
  const definitions = definitionsPage?.items ?? [];

  const selectedDef = defaultDefResult.state === "AVAILABLE" ? defaultDefResult.value : (definitions.length > 0 ? definitions[0] : undefined);
  const activeFeatureId = selectedDef?.feature_definition_id;

  let materializationsResult = undefined;
  if (activeFeatureId) {
    materializationsResult = await getFeatureMaterializations(ctx, {
      feature_id: activeFeatureId,
      instrument: customInstrument,
      dataset_version: customDataset,
      decision_time: customDecisionTime,
      limit: 20,
      offset: 0,
    });
  }

  const materializationsPage = materializationsResult?.state === "AVAILABLE" ? materializationsResult.value : undefined;
  const materializations = materializationsPage?.items ?? [];

  const hasDemo = (selectedDef?.feature_name.toLowerCase().includes("demo") || selectedDef?.family.toLowerCase().includes("demo")) ||
    materializations.some((m) => m.dataset_version.toLowerCase().includes("demo"));

  return (
    <div className="workspace-container">
      <WorkspaceToolbar
        title="Feature Engineering & Materialization"
        subtitle="Authoritative feature definitions, point-in-time calculation logic, governance policies, and immutable materializations."
        status={selectedDef ? "AVAILABLE" : definitionsResult.state}
        asOf={ctx.evidenceTime}
      />

      {hasDemo && (
        <DemoEvidenceBanner message="Feature materializations shown include synthetic sandbox data. The server materialization engine enforces strict point-in-time visibility boundaries." />
      )}

      {/* Feature Family Filter */}
      <FilterBar
        groups={[
          {
            id: "filter-family",
            name: "family",
            label: "Feature Family",
            defaultValue: family ?? "ALL",
            options: [
              { label: "All Families", value: "ALL" },
              { label: "Price Returns", value: "PRICE_RETURNS" },
              { label: "Volatility", value: "VOLATILITY" },
              { label: "Momentum", value: "MOMENTUM" },
              { label: "Liquidity", value: "LIQUIDITY" },
              { label: "Orderbook", value: "ORDERBOOK" },
              { label: "Macro", value: "MACRO" },
              { label: "Sentiment", value: "SENTIMENT" },
            ],
          },
        ]}
        resetHref="/features"
        ariaLabel="Feature Definitions Filter"
      />

      {/* Level 1: Feature Definitions Explorer */}
      <article className="panel margin-bottom-24">
        <h2>
          <span>Feature Definitions Catalog</span>
          <span className="badge tabular-num">{definitions.length} defined</span>
        </h2>
        <p>
          Governed mathematical definitions with deterministic calculation specs, missing value policies, and leakage boundaries.
        </p>

        {definitions.length > 0 ? (
          <DataTable caption="Feature Definitions Table" ariaLabel="Feature Definitions">
            <thead>
              <tr>
                <th scope="col">Feature Name</th>
                <th scope="col">Family</th>
                <th scope="col">Version</th>
                <th scope="col">Frequency / Lookback</th>
                <th scope="col">Required Inputs</th>
                <th scope="col">Governance Policies</th>
                <th scope="col">Status</th>
                <th scope="col">Action</th>
              </tr>
            </thead>
            <tbody>
              {definitions.map((def) => {
                const isSelected = def.feature_definition_id === activeFeatureId;
                const selectParams = new URLSearchParams();
                if (family && family !== "ALL") selectParams.set("family", family);
                selectParams.set("feature_id", def.feature_definition_id);

                return (
                  <tr key={def.feature_definition_id} className={isSelected ? "row-selected" : ""}>
                    <td>
                      <strong>{def.feature_name}</strong>
                      <br />
                      <code className="inspector-id-code">{def.feature_definition_id}</code>
                    </td>
                    <td><code>{def.family}</code></td>
                    <td><code className="dataset-badge-version">{def.semantic_version}</code></td>
                    <td>
                      {def.frequency} &bull; Lookback: {def.lookback} ({def.timestamp_semantics})
                    </td>
                    <td>
                      <small>
                        Types: {def.required_dataset_types.join(", ") || "None"}
                        <br />
                        Fields: {def.required_fields.join(", ") || "None"}
                      </small>
                    </td>
                    <td>
                      <small>
                        Missing: {def.missing_value_policy} &bull; Outlier: {def.outlier_policy} &bull; Leakage: {def.leakage_policy}
                      </small>
                    </td>
                    <td><QualityStateBadge status={def.status} /></td>
                    <td className="cell-action">
                      <Link
                        href={`/features?${selectParams.toString()}`}
                        className="table-link"
                        aria-label={`Select ${def.feature_name}`}
                      >
                        {isSelected ? "Active" : "Inspect →"}
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </DataTable>
        ) : (
          <p className="empty-state">{stateText(definitionsResult)}</p>
        )}
      </article>

      {/* Selected Feature Definition Deep Details */}
      {selectedDef && (
        <article className="panel margin-bottom-24">
          <h2>
            <span>Active Feature Specification: {selectedDef.feature_name}</span>
            <QualityStateBadge status={selectedDef.status} />
          </h2>

          <KeyValueGrid
            columns={3}
            items={[
              { key: "feat_name", label: "Feature Name", value: selectedDef.feature_name },
              { key: "feat_family", label: "Family", value: selectedDef.family },
              { key: "feat_version", label: "Semantic Version", value: selectedDef.semantic_version },
              { key: "calc_ver", label: "Calculation Engine Version", value: selectedDef.calculation_version },
              { key: "units", label: "Measurement Units", value: selectedDef.units },
              { key: "freq", label: "Sampling Frequency", value: selectedDef.frequency },
              { key: "lookback", label: "Lookback Window", value: `${selectedDef.lookback} periods` },
              { key: "semantics", label: "Timestamp Semantics", value: selectedDef.timestamp_semantics },
              { key: "missing_pol", label: "Missing Value Policy", value: selectedDef.missing_value_policy },
              { key: "outlier_pol", label: "Outlier Policy", value: selectedDef.outlier_policy },
              { key: "leakage_pol", label: "Leakage Policy", value: selectedDef.leakage_policy },
              { key: "created", label: "Created At (UTC)", value: <time dateTime={selectedDef.created_at}>{utc(selectedDef.created_at)}</time> },
            ]}
          />

          {Object.keys(selectedDef.parameters).length > 0 && (
            <div className="params-container">
              <span className="inspector-section-title">Calculation Parameters</span>
              <pre className="code-block-box">
                {JSON.stringify(selectedDef.parameters, null, 2)}
              </pre>
            </div>
          )}
        </article>
      )}

      {/* Level 2: Point-in-Time Materializations */}
      <article className="panel">
        <h2>
          <span>Point-in-Time Materializations</span>
          <QualityStateBadge status={materializations.length > 0 ? "AVAILABLE" : (materializationsResult?.state ?? "UNAVAILABLE")} />
        </h2>
        <p>
          Point-in-time materialized values evaluated with strict visibility cutoff. Timestamps guarantee zero future lookahead leakage.
        </p>

        {materializations.length > 0 ? (
          <DataTable
            caption={`Point-in-time materializations as of ${utc(materializationsPage?.decision_time ?? ctx.evidenceTime)}`}
            ariaLabel="Feature Materializations"
          >
            <thead>
              <tr>
                <th scope="col">Instrument / Dataset</th>
                <th scope="col">Point-in-Time Timestamps (UTC)</th>
                <th scope="col">Materialized Value</th>
                <th scope="col">Quality State</th>
                <th scope="col">Content Hash &amp; Provenance</th>
              </tr>
            </thead>
            <tbody>
              {materializations.map((item) => (
                <tr key={item.materialization_id}>
                  <td>
                    <strong>{item.instrument}</strong>
                    <br />
                    <DatasetVersionBadge version={item.dataset_version} />
                  </td>
                  <td>
                    <PITTimestampGroup
                      eventTime={item.event_time}
                      effectiveTime={item.effective_time}
                      knowledgeTime={item.knowledge_time}
                      computedTime={item.computed_time}
                    />
                  </td>
                  <td>
                    <strong className="metric-value">
                      {item.value ?? "UNAVAILABLE"}
                    </strong>
                  </td>
                  <td>
                    <QualityStateBadge status={item.quality_state} />
                  </td>
                  <td>
                    <code className="content-hash" title={item.content_hash}>
                      {item.content_hash.slice(0, 16)}&hellip;
                    </code>
                    {item.source_manifest.length > 0 && (
                      <div className="sources-list-footer">
                        Sources: {item.source_manifest.join(", ")}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <p className="empty-state">
            {materializationsResult?.state === "AVAILABLE"
              ? "No authorized materializations matched this bounded point-in-time scope; zero was not substituted."
              : stateText(materializationsResult ?? { state: "EMPTY", detail: "Feature materialization scope unavailable." })}
          </p>
        )}

        <ProvenancePanel
          source="PostgresOperatorDashboardQueries: feature_definitions & feature_materializations"
          recordId={activeFeatureId}
          version="features-v1"
          asOf={ctx.evidenceTime}
          limitations={[
            "Frontend never recomputes or approximates feature values.",
            "Zero leakage guarantee enforced by strict knowledge cutoff.",
          ]}
        />
      </article>
    </div>
  );
}
