import React from "react";

export function QualityStateBadge({
  status,
  label,
}: {
  status: string | null | undefined;
  label?: string;
}) {
  const normalized = (status ?? "UNAVAILABLE").toUpperCase();
  let className = "status-badge status-badge-unavailable";

  if (normalized === "HEALTHY" || normalized === "VALIDATED" || normalized === "AVAILABLE" || normalized === "PASS") {
    className = "status-badge status-badge-available";
  } else if (normalized === "WARN" || normalized === "DEGRADE_CONFIDENCE" || normalized === "SUSPECT" || normalized === "DUE") {
    className = "status-badge status-badge-warning";
  } else if (normalized === "BLOCKING" || normalized === "BLOCK_INSTRUMENT" || normalized === "BLOCK_STRATEGY" || normalized === "BLOCK_ASSET_CLASS" || normalized === "GLOBAL_BLOCK" || normalized === "REJECTED" || normalized === "ERROR" || normalized === "FAIL") {
    className = "status-badge status-badge-danger";
  } else if (normalized === "EXTERNAL_BLOCKED" || normalized === "BLOCKED") {
    className = "status-badge status-badge-blocked";
  } else if (normalized === "STALE" || normalized === "OVERDUE") {
    className = "status-badge status-badge-warning";
  }

  return (
    <span className={className} role="status">
      <span className="status-dot" aria-hidden="true" />
      <span>{label ?? normalized}</span>
    </span>
  );
}
