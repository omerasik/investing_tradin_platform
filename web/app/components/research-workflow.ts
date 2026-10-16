/**
 * Stage B.1.3a: the primary research workflow, in order, shared by the sidebar and the
 * research screens. Each step is a read-only view of recorded evidence; none of them acts.
 *
 * ``kind`` names the data a step shows, so a screen never passes historical bars off as
 * live prices or a search-tier result as a validated one. Validation and paper status are
 * sections of the Research Terminal (there is no separate page), so that step links to the
 * terminal anchor and is never marked as the current page.
 */
export type ResearchWorkflowStep = {
  id: "terminal" | "chart" | "strategy-lab" | "validation";
  href: string;
  label: string;
  kind: string;
};

export const RESEARCH_WORKFLOW: readonly ResearchWorkflowStep[] = [
  { id: "terminal", href: "/terminal", label: "Research Terminal", kind: "Recorded evidence & readiness" },
  { id: "chart", href: "/chart", label: "Market Chart", kind: "Historical bars · not live prices" },
  { id: "strategy-lab", href: "/strategy-lab", label: "Strategy Lab", kind: "Studies & reruns · search-tier, gross" },
  { id: "validation", href: "/terminal#validation", label: "Validation & Paper", kind: "Recorded status · owner-gated" },
];

/** The workflow step a pathname belongs to; anchors inside a page never count as a page. */
export function currentWorkflowStep(pathname: string): ResearchWorkflowStep["id"] | undefined {
  const step = RESEARCH_WORKFLOW.find((item) => !item.href.includes("#") && (pathname === item.href || pathname.startsWith(`${item.href}/`)));
  return step?.id;
}
