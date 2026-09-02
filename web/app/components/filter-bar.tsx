import React from "react";
import Link from "next/link";

export type FilterOption = {
  label: string;
  value: string;
};

export type FilterGroup = {
  id: string;
  name: string;
  label: string;
  options: FilterOption[];
  defaultValue?: string;
};

export function FilterBar({
  groups = [],
  children,
  resetHref,
  ariaLabel = "Filter Options",
}: {
  groups?: FilterGroup[];
  children?: React.ReactNode;
  resetHref?: string;
  ariaLabel?: string;
}) {
  return (
    <form method="GET" className="filter-bar" aria-label={ariaLabel}>
      <div className="filter-groups">
        {children}
        {groups.map((group) => (
          <div key={group.id} className="filter-item">
            <label htmlFor={group.id} className="filter-label">
              {group.label}
            </label>
            <select
              id={group.id}
              name={group.name}
              defaultValue={group.defaultValue ?? "ALL"}
              className="filter-select"
            >
              {group.options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>

      <div className="filter-actions">
        <button type="submit" className="filter-apply-btn">
          Apply Filters
        </button>
        {resetHref && (
          <Link href={resetHref} className="filter-reset-btn">
            Reset
          </Link>
        )}
      </div>
    </form>
  );
}
