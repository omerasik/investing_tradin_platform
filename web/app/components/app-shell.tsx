import { Sidebar } from "./sidebar";
import { TopBar } from "./top-bar";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-layout">
      <Sidebar />
      <div className="app-main">
        <TopBar />
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
