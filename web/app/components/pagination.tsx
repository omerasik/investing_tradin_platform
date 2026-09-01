import React from "react";
import Link from "next/link";

export function Pagination({
  limit,
  offset,
  returned,
  hasMore,
  basePath,
  searchParams = {},
}: {
  limit: number;
  offset: number;
  returned: number;
  hasMore: boolean;
  basePath: string;
  searchParams?: Record<string, string | undefined>;
}) {
  const currentStart = offset + 1;
  const currentEnd = offset + returned;

  const prevOffset = Math.max(0, offset - limit);
  const nextOffset = offset + limit;

  function buildUrl(targetOffset: number): string {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(searchParams)) {
      if (v !== undefined && v !== "") params.set(k, v);
    }
    params.set("offset", String(targetOffset));
    params.set("limit", String(limit));
    return `${basePath}?${params.toString()}`;
  }

  return (
    <nav className="pagination-container" aria-label="Table Pagination">
      <div className="pagination-info">
        {returned > 0 ? (
          <span>
            Showing <strong className="tabular-num">{currentStart}</strong> &ndash;{" "}
            <strong className="tabular-num">{currentEnd}</strong>
          </span>
        ) : (
          <span>No records</span>
        )}
      </div>

      <div className="pagination-actions">
        {offset > 0 ? (
          <Link href={buildUrl(prevOffset)} className="pagination-btn" aria-label="Previous Page">
            &larr; Previous
          </Link>
        ) : (
          <button type="button" className="pagination-btn" disabled aria-disabled="true">
            &larr; Previous
          </button>
        )}

        {hasMore ? (
          <Link href={buildUrl(nextOffset)} className="pagination-btn" aria-label="Next Page">
            Next &rarr;
          </Link>
        ) : (
          <button type="button" className="pagination-btn" disabled aria-disabled="true">
            Next &rarr;
          </button>
        )}
      </div>
    </nav>
  );
}
