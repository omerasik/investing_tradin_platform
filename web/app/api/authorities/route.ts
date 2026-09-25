import { NextRequest, NextResponse } from "next/server";

import { allowedAuthorityTarget } from "../../authority-targets";
import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../dashboard-config";

/** GET-only server boundary; the operator token and arbitrary backend paths never reach the browser. */
export async function GET(request: NextRequest) {
  try {
    const target = request.nextUrl.searchParams.get("target");
    if (!target || !allowedAuthorityTarget(target)) {
      return NextResponse.json({ detail: "Unsupported operator authority target." }, { status: 400 });
    }
    const config = loadDashboardConfig();
    const token = resolveDashboardOperatorToken(config);
    if (!config.apiBaseUrl || !token) {
      return NextResponse.json({ detail: "Server-side authority configuration is unavailable." }, { status: 503 });
    }
    const response = await fetch(`${config.apiBaseUrl}${target}`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "Operator authority backend is unavailable." }, { status: 502 });
  }
}
