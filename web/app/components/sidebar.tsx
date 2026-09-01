"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = {
  href: string;
  label: string;
};

type NavGroup = {
  title: string;
  items: NavItem[];
};

const NAVIGATION_GROUPS: NavGroup[] = [
  {
    title: "Overview",
    items: [{ href: "/dashboard", label: "Dashboard" }],
  },
  {
    title: "Market & Data",
    items: [
      { href: "/markets", label: "Markets" },
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
    ],
  },
  {
    title: "Portfolio & Risk",
    items: [
      { href: "/risk", label: "Risk" },
      { href: "/regimes", label: "Regimes" },
      { href: "/portfolio", label: "Portfolio" },
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
    title: "Execution",
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

  return (
    <aside className="app-sidebar" aria-label="Sidebar Navigation">
      <div className="sidebar-brand">
        <h2 className="sidebar-brand-title">Trade Investing Panel</h2>
        <div className="sidebar-brand-subtitle">Operator Workstation</div>
      </div>

      <nav className="sidebar-nav" aria-label="Operator workspaces">
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
