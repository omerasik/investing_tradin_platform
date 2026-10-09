import { NextResponse } from "next/server";

import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../../dashboard-config";

/**
 * Research commands only (mirrors research_terminal_commands_v1). Owner decisions --
 * watch list, account, account policy, preregistration authorization, holdout opening --
 * are never sent with the dashboard's shared server token: the owner submits them with
 * their own credential (scripts/terminal_command.py), and the backend binds every
 * approval field to that authenticated subject.
 */
const RESEARCH_KINDS = new Set([
  "STRATEGY_SEARCH", "CANDIDATE_FREEZE", "DECIMAL_RERUN", "HOLDOUT_VALIDATE",
  "RESEARCH_WATCH_START", "RESEARCH_WATCH_STOP", "PAPER_INCUBATION_START", "PAPER_INCUBATION_STOP",
]);

export async function POST(request: Request) {
  // Same-origin JSON only: another local app cannot ride the session cookie into a command.
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  let originHost: string | null = null;
  try { originHost = origin ? new URL(origin).host : null; } catch { originHost = null; }
  if (!originHost || !host || originHost !== host) return NextResponse.json({ detail: "Cross-origin command refused." }, { status: 403 });
  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite && fetchSite !== "same-origin") return NextResponse.json({ detail: "Cross-site command refused." }, { status: 403 });
  if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) return NextResponse.json({ detail: "Commands must be JSON." }, { status: 415 });
  try {
    const config = loadDashboardConfig(); const token = resolveDashboardOperatorToken(config);
    if (!config.apiBaseUrl || !token) return NextResponse.json({ detail: "Server-side command configuration is unavailable." }, { status: 503 });
    const body: unknown = await request.json();
    if (!body || typeof body !== "object" || Array.isArray(body)) return NextResponse.json({ detail: "Invalid command." }, { status: 400 });
    const { kind, inputs, idempotency_key: key } = body as Record<string, unknown>;
    if (typeof kind !== "string" || !RESEARCH_KINDS.has(kind)) return NextResponse.json({ detail: "Not a dashboard command kind (owner decisions use the owner's own credential)." }, { status: 400 });
    if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) return NextResponse.json({ detail: "Command inputs must be an object." }, { status: 400 });
    if (typeof key !== "string" || !/^[A-Za-z0-9:_.-]{1,200}$/.test(key)) return NextResponse.json({ detail: "Invalid idempotency key." }, { status: 400 });
    const response = await fetch(`${config.apiBaseUrl}/operator-dashboard/research-terminal/commands`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ kind, inputs, idempotency_key: key }),
      cache: "no-store",
    });
    return new NextResponse(await response.text(), { status: response.status, headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" } });
  } catch { return NextResponse.json({ detail: "Command backend is unavailable." }, { status: 502 }); }
}
