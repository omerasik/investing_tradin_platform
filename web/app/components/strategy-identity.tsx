export function StrategyIdentity({
  strategyId,
  version,
  family,
}: {
  strategyId?: string;
  version: string;
  family?: string;
}) {
  return (
    <span className="strategy-identity">
      {family && <strong>{family}</strong>}
      <span className="strategy-identity-version">{version}</span>
      {strategyId && <code className="inspector-id-code">{strategyId}</code>}
    </span>
  );
}
