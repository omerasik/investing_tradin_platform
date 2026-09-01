import type { MetricEvidenceState } from "../operator-contracts";

export function EvidenceStateBadge({ state }: { state: MetricEvidenceState | string }) {
  let badgeClass = "badge";
  switch (state) {
    case "MEASURED":
      badgeClass = "badge badge-available";
      break;
    case "ASSUMED":
      badgeClass = "badge badge-warning";
      break;
    case "UNAVAILABLE":
    default:
      badgeClass = "badge badge-unavailable";
      break;
  }

  return <span className={badgeClass}>{state}</span>;
}
