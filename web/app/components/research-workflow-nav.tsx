import Link from "next/link";
import { RESEARCH_WORKFLOW, type ResearchWorkflowStep } from "./research-workflow";

/** The workflow strip at the top of each research screen: where you are and where to go next. */
export function ResearchWorkflowNav({ current }: { current: ResearchWorkflowStep["id"] }) {
  return (
    <nav aria-label="Research workflow" className="research-workflow margin-bottom-24">
      <ol className="research-workflow-steps">
        {RESEARCH_WORKFLOW.map((step, index) => {
          const active = step.id === current;
          return (
            <li key={step.id} className={active ? "active" : undefined}>
              <Link href={step.href} aria-current={active ? "page" : undefined}>
                <span className="research-workflow-label">{index + 1}. {step.label}</span>
                <span className="research-workflow-kind">{step.kind}</span>
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
