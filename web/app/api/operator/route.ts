import { NextResponse } from "next/server";

import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../dashboard-config";

/** Server-only allowlist: browser clients never receive an operator credential. */
export async function GET() {
  try {
    const config = loadDashboardConfig();
    const token = resolveDashboardOperatorToken(config);
    if (!config.apiBaseUrl || !token) {
      return NextResponse.json({ detail: "Server-side operator evidence configuration is unavailable." }, { status: 503 });
    }
    const response = await fetch(`${config.apiBaseUrl}/operator-dashboard/command-center`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "Operator evidence backend is unavailable." }, { status: 502 });
  }
}
