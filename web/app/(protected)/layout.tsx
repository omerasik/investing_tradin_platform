import { AppShell } from "../components/app-shell";
import { loadDashboardConfig } from "../dashboard-config";

export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const config = loadDashboardConfig();
  return <AppShell environment={config.environment}>{children}</AppShell>;
}
