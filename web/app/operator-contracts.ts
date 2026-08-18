export type EvidenceStatus = "AVAILABLE" | "EMPTY" | "STALE" | "BLOCKED" | "ERROR" | "UNAVAILABLE" | "EXTERNAL_BLOCKED" | "DISABLED" | "POSTGRES_CONFIGURED" | "SQLITE_NON_PRODUCTION";

export type EvidenceReference = { id: string; kind: string };

export type EvidenceState = {
  id: string;
  status: EvidenceStatus;
  version: string;
  source: string;
  as_of: string;
  freshness: string;
  limitations: string[];
  evidence_references: EvidenceReference[];
  details: Record<string, unknown>;
};

export type CommandCenterEvidence = EvidenceState & {
  platform_mode: string;
  live_trading_enabled: false;
  states: EvidenceState[];
};

export type EvidenceResult<T> =
  | { state: "AVAILABLE"; value: T }
  | { state: "EMPTY"; detail: string }
  | { state: "ERROR"; detail: string }
  | { state: "EXTERNAL_BLOCKED"; detail: string };

export async function readEvidence<T>(url: string, configured: boolean, missingDetail: string): Promise<EvidenceResult<T>> {
  if (!configured) return { state: "EXTERNAL_BLOCKED", detail: missingDetail };
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (response.ok) return { state: "AVAILABLE", value: await response.json() as T };
    if (response.status === 404) return { state: "EMPTY", detail: "No durable evidence matched this configured reference." };
    return { state: "ERROR", detail: `Evidence source responded ${response.status}.` };
  } catch {
    return { state: "ERROR", detail: "Evidence source could not be reached." };
  }
}
