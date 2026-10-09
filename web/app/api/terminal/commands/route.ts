import { NextResponse } from "next/server";

import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../../dashboard-config";

/** Closed set, mirrored from research_terminal_commands_v1. Owner kinds use the owner endpoint. */
const RESEARCH_KINDS = new Set([
  "STRATEGY_SEARCH", "CANDIDATE_FREEZE", "DECIMAL_RERUN", "HOLDOUT_VALIDATE",
  "RESEARCH_WATCH_START", "RESEARCH_WATCH_STOP", "PAPER_INCUBATION_START", "PAPER_INCUBATION_STOP",
]);
const OWNER_KINDS = new Set([
  "WATCHLIST_RECORD", "ACCOUNT_REGISTER", "ACCOUNT_POLICY_RECORD", "PREREGISTRATION_RECORD", "HOLDOUT_OPEN",
]);

export async function POST(request: Request) {
  try {
    const config = loadDashboardConfig(); const token = resolveDashboardOperatorToken(config);
    if (!config.apiBaseUrl || !token) return NextResponse.json({ detail: "Server-side command configuration is unavailable." }, { status: 503 });
    const body: unknown = await request.json();
    if (!body || typeof body !== "object" || Array.isArray(body)) return NextResponse.json({ detail: "Invalid command." }, { status: 400 });
    const { kind, inputs, idempotency_key: key } = body as Record<string, unknown>;
    if (typeof kind !== "string" || (!RESEARCH_KINDS.has(kind) && !OWNER_KINDS.has(kind))) return NextResponse.json({ detail: "Unknown command kind." }, { status: 400 });
    if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) return NextResponse.json({ detail: "Command inputs must be an object." }, { status: 400 });
    if (typeof key !== "string" || !/^[A-Za-z0-9:_.-]{1,200}$/.test(key)) return NextResponse.json({ detail: "Invalid idempotency key." }, { status: 400 });
    const route = OWNER_KINDS.has(kind) ? "/operator-dashboard/research-terminal/owner-commands" : "/operator-dashboard/research-terminal/commands";
    const response = await fetch(`${config.apiBaseUrl}${route}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ kind, inputs, idempotency_key: key }),
      cache: "no-store",
    });
    return new NextResponse(await response.text(), { status: response.status, headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" } });
  } catch { return NextResponse.json({ detail: "Command backend is unavailable." }, { status: 502 }); }
}
