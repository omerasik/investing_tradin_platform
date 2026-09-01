import type { AppEnvironment } from "../dashboard-config";
import { Sidebar } from "./sidebar";
import { TopBar } from "./top-bar";

export function AppShell({
  children,
  environment = "LOCAL",
}: {
  children: React.ReactNode;
  environment?: AppEnvironment;
}) {
  return (
    <div className="app-layout">
      <Sidebar />
      <div className="app-main">
        <TopBar environment={environment} />
        <main id="main-content" className="workspace-content">
          {children}
          <footer>
            Live trading is deliberately unavailable. This console has no live-execution,
            risk-override, kill-switch-bypass, or automatic strategy-activation control.
          </footer>
        </main>
      </div>
    </div>
  );
}
