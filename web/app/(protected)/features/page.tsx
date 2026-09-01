import { getWorkspaceContext, getFeatureDefinition, getFeatureMaterializations, stateText, utc } from "../../lib/data-access";
import { PageHeader } from "../../components/page-header";
import { StatusBadge } from "../../components/status-badge";
import { EvidenceMeta } from "../../components/evidence-meta";

export const dynamic = "force-dynamic";

export default async function FeaturesPage() {
  const ctx = await getWorkspaceContext();
  const [feature, featureMaterializations] = await Promise.all([
    getFeatureDefinition(ctx),
    getFeatureMaterializations(ctx),
  ]);

  const definition = feature.state === "AVAILABLE" ? feature.value : undefined;
  const materializations = featureMaterializations.state === "AVAILABLE" ? featureMaterializations.value : undefined;

  return (
    <>
      <PageHeader
        eyebrow="MARKET & DATA WORKSPACE"
        title="Feature Authority"
        asOfTime={ctx.evidenceTime}
      />

      <div className="transitional-banner">
        <strong>Module 2A Transitional Workspace</strong> &mdash; Point-in-time materialized feature values directly from PostgreSQL authority. Frontend never recomputes feature values. Deep feature matrix inspection arriving in Module 2B.
      </div>

      <div className="grid-2col margin-bottom-20">
        <article className="panel">
          <h2>
            <span>Feature Definition</span>
            <StatusBadge status={definition ? "AVAILABLE" : feature.state} />
          </h2>
          {definition ? (
            <>
              <dl>
                <dt>Feature Name / Version</dt>
                <dd>
                  <strong>{definition.feature_name}</strong> &bull; Version: {definition.semantic_version}
                </dd>
                <dt>Family / Status</dt>
                <dd>
                  {definition.family} &bull; <StatusBadge status={definition.status} />
                </dd>
                <dt>Required Datasets</dt>
                <dd>{definition.required_dataset_types.join(", ")}</dd>
                <dt>Required Fields</dt>
                <dd>{definition.required_fields.join(", ")}</dd>
                <dt>Frequency / Lookback</dt>
                <dd>
                  {definition.frequency} &bull; Lookback: {definition.lookback} ({definition.timestamp_semantics})
                </dd>
                <dt>Governance Policies</dt>
                <dd>
                  Missing: {definition.missing_value_policy} &bull; Outliers: {definition.outlier_policy} &bull; Leakage: {definition.leakage_policy}
                </dd>
                <dt>Calculation Logic</dt>
                <dd>
                  {definition.calculation_version} &bull; Units: {definition.units}
                </dd>
              </dl>
              <EvidenceMeta
                source="PostgreSQL Feature Authority"
                asOf={definition.created_at}
                version={definition.semantic_version}
              />
            </>
          ) : (
            <p className="empty-state">{stateText(feature)}</p>
          )}
          <span className="status margin-top-auto">READ ONLY</span>
        </article>

        <article className="panel">
          <h2>
            <span>Feature Metadata</span>
            <StatusBadge status="AVAILABLE" />
          </h2>
          <p>Features are calculated deterministically by server authority workers.</p>
          <dl>
            <dt>Authority Scope</dt>
            <dd>Strict Point-in-Time Materialization Engine</dd>
            <dt>Mutation Rights</dt>
            <dd>IMMUTABLE / READ-ONLY REPOSITORY</dd>
            <dt>Safety Boundary</dt>
            <dd>Zero-leakage guarantees verified by cryptographic content hashing</dd>
          </dl>
          <span className="status margin-top-auto">POINT IN TIME BOUNDED</span>
        </article>
      </div>

      <article className="panel">
        <h2>
          <span>Point-in-Time Materializations</span>
          <StatusBadge status={materializations?.items.length ? "AVAILABLE" : "UNAVAILABLE"} />
        </h2>

        {materializations?.items.length ? (
          <table>
            <caption>Point-in-time materializations as of {utc(materializations.decision_time)}</caption>
            <thead>
              <tr>
                <th>Instrument / Dataset</th>
                <th>Event Time</th>
                <th>Knowledge Time</th>
                <th>Computed Time</th>
                <th>Value</th>
                <th>Quality State</th>
                <th>Content Hash / Manifest</th>
              </tr>
            </thead>
            <tbody>
              {materializations.items.map((item) => (
                <tr key={item.materialization_id}>
                  <td>
                    <strong>{item.instrument}</strong>
                    <br />
                    <code>{item.dataset_version}</code>
                  </td>
                  <td>{utc(item.event_time)}</td>
                  <td>{utc(item.knowledge_time)}</td>
                  <td>{utc(item.computed_time)}</td>
                  <td>
                    <strong className="metric-value">{item.value ?? "UNAVAILABLE"}</strong>
                  </td>
                  <td>
                    <StatusBadge status={item.quality_state} />
                  </td>
                  <td>
                    <code>{item.content_hash}</code>
                    <br />
                    <small>{item.source_manifest.join(", ")}</small>
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
      </article>
    </>
  );
}
