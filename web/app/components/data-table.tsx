import React from "react";

export function DataTable({
  children,
  caption,
  className = "",
  ariaLabel,
}: {
  children: React.ReactNode;
  caption?: string;
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <div className={`data-table-container ${className}`} tabIndex={0} role="region" aria-label={ariaLabel ?? caption ?? "Data Table"}>
      <table className="data-table">
        {caption && <caption className="data-table-caption">{caption}</caption>}
        {children}
      </table>
    </div>
  );
}
