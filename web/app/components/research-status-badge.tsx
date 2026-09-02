export function ResearchStatusBadge({ classification }: { classification: string }) {
  const upper = classification.toUpperCase();
  let badgeClass = "badge badge-unavailable";
  if (upper.includes("SYNTHETIC")) {
    badgeClass = "badge badge-warning";
  } else if (upper.includes("REAL_DATA")) {
    badgeClass = "badge badge-available";
  } else if (upper.includes("UNAVAILABLE")) {
    badgeClass = "badge badge-unavailable";
  }
  return <span className={badgeClass}>{classification}</span>;
}
