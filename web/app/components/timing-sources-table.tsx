import React from "react";
import type { TimingSource } from "../lib/data-access";
import { DataTable } from "./data-table";

/** Sources and the tier *ceiling* each timing contract grants. A ceiling is not a verdict. */
export function TimingSourcesTable({ sources }: { sources: TimingSource[] }) {
  return (
    <DataTable caption="Market Data Sources and Timing Ceilings" ariaLabel="Market Data Sources and Timing Ceilings">
      <thead>
        <tr>
          <th scope="col">Source</th>
          <th scope="col">Timing Authority</th>
          <th scope="col">Tier Ceiling</th>
          <th scope="col">Publication Lag</th>
          <th scope="col">Timing Contract (SHA-256)</th>
        </tr>
      </thead>
      <tbody>
        {sources.map((source) => (
          <tr key={source.source_id}>
            <td>
              <strong>{source.label}</strong>
              <br />
              <code title={source.source_id}>{source.source_id.slice(0, 8)}&hellip;</code>
            </td>
            <td><code>{source.timing_authority}</code></td>
            <td title={source.tier_ceiling_reason ?? undefined}><code>{source.tier_ceiling}</code></td>
            <td><code>{source.publication_lag_state}</code></td>
            <td>
              {source.timing_contract_hash ? (
                <code className="content-hash" title={source.timing_contract_hash}>
                  {source.timing_contract_hash.slice(0, 16)}&hellip;
                </code>
              ) : (
                "NOT REGISTERED"
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </DataTable>
  );
}
