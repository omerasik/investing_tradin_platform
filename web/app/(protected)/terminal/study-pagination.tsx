import React from "react";
import type { StrategyLabPageInfo } from "../../lib/data-access";
import { terminalQuery, type CompareSelection } from "./compare-section";

export const STUDY_PAGE_LIMIT = 50;
export const MAX_STUDY_OFFSET = 10_000;

export function studyOffset(value: string | undefined): number {
  return value && /^\d{1,5}$/.test(value) && Number(value) <= MAX_STUDY_OFFSET ? Number(value) : 0;
}

export function studyPageHref(selection: CompareSelection, offset: number): string {
  const query = terminalQuery(selection);
  query.set("study_offset", String(offset));
  return `/terminal?${query}#strategy-lab`;
}

export function StudyPagination({ page, selection }: { page: StrategyLabPageInfo; selection: CompareSelection }) {
  return <nav aria-label="Terminal study pages" className="pagination-container">
    <span>{page.returned ? `Showing studies ${page.offset + 1}–${page.offset + page.returned}.` : page.offset
      ? "Study page out of range: no stored studies at this offset." : "No stored studies on this page."}</span>
    {page.offset > 0 ? <>
      <a href={studyPageHref(selection, 0)}>First study page</a>{" "}
      <a href={studyPageHref(selection, Math.max(0, page.offset - page.limit))}>Previous study page</a>
    </> : null}{" "}
    {page.has_more && page.offset + page.limit <= MAX_STUDY_OFFSET
      ? <a href={studyPageHref(selection, page.offset + page.limit)}>Next study page</a> : null}
    {page.has_more && page.offset + page.limit > MAX_STUDY_OFFSET
      ? <p>The study offset limit is reached. Narrow the market selection or return to the first page.</p> : null}
  </nav>;
}
