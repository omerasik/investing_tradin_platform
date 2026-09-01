import React from "react";
import { QualityStateBadge } from "./quality-state-badge";
import { utc } from "../lib/data-access";

export function WorkspaceToolbar({
  title,
  subtitle,
  status,
  statusLabel,
  asOf,
  actions,
}: {
  title: string;
  subtitle?: string;
  status?: string;
  statusLabel?: string;
  asOf?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="workspace-toolbar" aria-label={`${title} Workspace Toolbar`}>
      <div className="workspace-toolbar-main">
        <div className="workspace-title-row">
          <h1 className="workspace-title">{title}</h1>
          {status && <QualityStateBadge status={status} label={statusLabel} />}
        </div>
        {subtitle && <p className="workspace-subtitle">{subtitle}</p>}
      </div>

      <div className="workspace-toolbar-side">
        {asOf && (
          <div className="workspace-asof">
            <span className="asof-label">As of:</span>
            <time className="asof-time" dateTime={asOf}>
              {utc(asOf)}
            </time>
          </div>
        )}
        {actions && <div className="workspace-toolbar-actions">{actions}</div>}
      </div>
    </header>
  );
}
