export function SafetyBanner({
  message = "LIVE TRADING IS DELIBERATELY UNAVAILABLE. THIS CONSOLE HAS NO LIVE-EXECUTION, RISK-OVERRIDE, OR BROKER CONTROL.",
}: {
  message?: string;
}) {
  return (
    <aside className="safety-banner" role="status" aria-label="Safety Boundary">
      <span>{message}</span>
      <span className="badge">LIVE TRADING: DISABLED</span>
    </aside>
  );
}
