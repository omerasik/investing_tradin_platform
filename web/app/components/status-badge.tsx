import type { EvidenceStatus } from "../operator-contracts";

export function StatusBadge({ status, label }: { status: EvidenceStatus | string; label?: string }) {
  const displayLabel = label ?? status;
  let badgeClass = "badge";

  switch (status) {
    case "AVAILABLE":
    case "POSTGRES_CONFIGURED":
    case "HEALTHY":
    case "APPROVED":
    case "RECONCILED":
    case "PASSED":
      badgeClass = "badge badge-available";
      break;
    case "STALE":
    case "DUE":
    case "SQLITE_NON_PRODUCTION":
    case "ASSUMED":
      badgeClass = "badge badge-warning";
      break;
    case "ERROR":
    case "FAILED":
    case "BLOCKING":
      badgeClass = "badge badge-danger";
      break;
    case "BLOCKED":
    case "EXTERNAL_BLOCKED":
      badgeClass = "badge badge-blocked";
      break;
    case "UNAVAILABLE":
    case "DISABLED":
    default:
      badgeClass = "badge badge-unavailable";
      break;
  }

  return <span className={badgeClass}>{displayLabel}</span>;
}
