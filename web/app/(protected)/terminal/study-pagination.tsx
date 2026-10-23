import React from "react";
import type { StrategyLabPageInfo } from "../../lib/data-access";

export const STUDY_PAGE_LIMIT = 50;
export const MAX_STUDY_OFFSET = 10_000;

export function studyOffset(value: string | undefined): number {
  return value && /^\d{1,5}$/.test(value) && Number(value) <= MAX_STUDY_OFFSET ? Number(value) : 0;
}

export function StudyPagination({ page }: { page: StrategyLabPageInfo }) {
  // Submit the comparison form even from the study table, so newly checked studies
  // and edited metric/sort values survive paging before a separate Compare click.
  const submit = { type: "submit" as const, form: "terminal-comparison-selection", formAction: "/terminal#strategy-lab", name: "study_offset" };
  return <nav aria-label="Terminal study pages" className="pagination-container">
    <span>{page.returned ? `Showing studies ${page.offset + 1}–${page.offset + page.returned}.` : page.offset
      ? "Study page out of range: no stored studies at this offset." : "No stored studies on this page."}</span>
    {page.offset > 0 ? <>
      <button {...submit} value={0}>First study page</button>{" "}
      <button {...submit} value={Math.max(0, page.offset - page.limit)}>Previous study page</button>
    </> : null}{" "}
    {page.has_more && page.offset + page.limit <= MAX_STUDY_OFFSET
      ? <button {...submit} value={page.offset + page.limit}>Next study page</button> : null}
    {page.has_more && page.offset + page.limit > MAX_STUDY_OFFSET
      ? <p>The study offset limit is reached. Narrow the market selection or return to the first page.</p> : null}
  </nav>;
}
