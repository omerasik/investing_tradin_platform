import { AppShell } from "../components/app-shell";
import { loadDashboardConfig } from "../dashboard-config";

export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const config = loadDashboardConfig();
  return <AppShell environment={config.environment}>
    {process.env.TRADE_PLATFORM_LITE === "1" ? (
      <aside className="panel margin-bottom-24" role="status" aria-label="Research Terminal Lite limits">
        <strong>RESEARCH TERMINAL LITE · REAL HISTORICAL EVIDENCE · READ ONLY</strong>
        <p>BTC, ETH and SOL charts use a bounded, verified public archive subset. These are historical event-time bars; timing eligibility and profitability are not established.</p>
        <p>Study results, trials, trades and performance are unavailable in this environment. Comparison requires imported study evidence. Research commands, holdout access, paper execution, brokers and live trading are disabled.</p>
      </aside>
    ) : null}
    {children}
  </AppShell>;
}
