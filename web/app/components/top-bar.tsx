"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

function formatWorkspaceName(pathname: string): string {
  if (!pathname || pathname === "/" || pathname === "/dashboard") return "Command Center";
  const segment = pathname.split("/").filter(Boolean)[0] || "";
  return segment
    .split("-")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function TopBar({
  environment = "LOCAL",
}: {
  environment?: "LOCAL" | "CI" | "PAPER" | "STAGING";
}) {
  const pathname = usePathname();
  const workspaceName = formatWorkspaceName(pathname);

  return (
    <header className="app-topbar" aria-label="Application Top Bar">
      <div className="topbar-left">
        <nav className="topbar-breadcrumbs" aria-label="Breadcrumb">
          <Link href="/dashboard">Panel</Link>
          <span>/</span>
          <span className="topbar-current-title">{workspaceName}</span>
        </nav>
      </div>

      <div className="topbar-right">
        <span className="env-tag">ENVIRONMENT: {environment}</span>
        <span className="badge">LIVE TRADING: DISABLED</span>
        <form action="/api/auth/logout" method="POST" className="logout-form">
          <button type="submit" className="btn-logout">
            Sign Out
          </button>
        </form>
      </div>
    </header>
  );
}
