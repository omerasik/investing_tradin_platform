"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { RESEARCH_WORKFLOW, currentWorkflowStep } from "./research-workflow";

function subscribeNever(): () => void {
  return () => {};
}

type NavItem = {
  href: string;
  label: string;
};

type NavGroup = {
  title: string;
  items: NavItem[];
};

// The research workflow (terminal, chart, Strategy Lab, validation/paper) is its own group
// above these; its pages are not repeated here.
const NAVIGATION_GROUPS: NavGroup[] = [
  {
    title: "Overview",
    items: [{ href: "/dashboard", label: "Dashboard" }],
  },
  {
    // What evidence exists and what it can prove -- the first question of every study.
    title: "Data & Evidence",
    items: [
      { href: "/evidence", label: "Evidence & Data" },
      { href: "/markets", label: "Market Sources" },
      { href: "/instruments", label: "Instruments" },
      { href: "/data-health", label: "Data Health" },
      { href: "/features", label: "Features" },
    ],
  },
  {
    title: "Research",
    items: [
      { href: "/strategies", label: "Strategies" },
      { href: "/backtests", label: "Backtests" },
      { href: "/scorecards", label: "Scorecards" },
      { href: "/signals", label: "Signals" },
      { href: "/regimes", label: "Regimes" },
    ],
  },
  {
    title: "Portfolio & Risk",
    items: [
      { href: "/portfolio", label: "Portfolio" },
      { href: "/risk", label: "Risk" },
    ],
  },
  {
    title: "Investing",
    items: [
      { href: "/investments", label: "Investments" },
      { href: "/news", label: "News" },
    ],
  },
  {
    title: "Paper Trading",
    items: [{ href: "/paper", label: "Paper" }],
  },
  {
    title: "System",
    items: [
      { href: "/operations", label: "Operations" },
      { href: "/audit", label: "Audit" },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const currentStep = currentWorkflowStep(pathname);
  // On narrow screens the other workspaces fold away; opening them lasts until the next navigation.
  // They fold only once this component is interactive, so without script every link stays visible.
  const interactive = useSyncExternalStore(subscribeNever, () => true, () => false);
  const [openFor, setOpenFor] = useState<string | null>(null);
  const expanded = openFor === pathname;

  return (
    <aside className={`app-sidebar ${interactive ? "collapsible" : ""} ${expanded ? "expanded" : ""}`} aria-label="Sidebar Navigation">
      <div className="sidebar-brand">
        <h2 className="sidebar-brand-title">Trade Investing Panel</h2>
        <div className="sidebar-brand-subtitle">Operator Workstation</div>
      </div>

      <nav className="sidebar-nav" aria-label="Operator workspaces">
        <div className="nav-group nav-group-workflow">
          <div className="nav-group-title">Research Workflow</div>
          <ol className="nav-group-items">
            {RESEARCH_WORKFLOW.map((step, index) => {
              const isActive = step.id === currentStep;
              return (
                <li key={step.id}>
                  <Link
                    href={step.href}
                    className={`nav-link nav-link-workflow ${isActive ? "active" : ""}`}
                    aria-current={isActive ? "page" : undefined}
                  >
                    <span>{index + 1}. {step.label}</span>
                    <span className="nav-link-kind">{step.kind}</span>
                  </Link>
                </li>
              );
            })}
          </ol>
        </div>

        <button
          type="button"
          className="sidebar-more-toggle"
          aria-expanded={expanded}
          aria-controls="sidebar-more"
          onClick={() => setOpenFor(expanded ? null : pathname)}
        >
          {expanded ? "Hide other workspaces" : "Show all workspaces"}
        </button>

        <div id="sidebar-more" className="sidebar-more">
          {NAVIGATION_GROUPS.map((group) => (
            <div key={group.title} className="nav-group">
              <div className="nav-group-title">{group.title}</div>
              <ul className="nav-group-items">
                {group.items.map((item) => {
                  const isActive = pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(item.href));
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        className={`nav-link ${isActive ? "active" : ""}`}
                        aria-current={isActive ? "page" : undefined}
                      >
                        <span>{item.label}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      </nav>

      <div className="sidebar-footer">
        <div>RESEARCH / PAPER ONLY</div>
        <div className="sidebar-safety-tag">
          Server authority separation
        </div>
      </div>
    </aside>
  );
}
