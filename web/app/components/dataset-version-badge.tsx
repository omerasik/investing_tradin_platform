import React from "react";

export function DatasetVersionBadge({
  version,
  sealed = true,
  synthetic = false,
}: {
  version: string | null | undefined;
  sealed?: boolean;
  synthetic?: boolean;
}) {
  if (!version) {
    return <span className="dataset-badge dataset-badge-unavailable">UNAVAILABLE</span>;
  }

  return (
    <span className="dataset-badge-container">
      <code className="dataset-badge-version">{version}</code>
      {sealed && <span className="dataset-tag dataset-tag-sealed">SEALED</span>}
      {synthetic && <span className="dataset-tag dataset-tag-demo">DEMO</span>}
    </span>
  );
}
