import { NextResponse } from "next/server";
import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../dashboard-config";

export async function GET() {
  try {
    const config = loadDashboardConfig(); const token = resolveDashboardOperatorToken(config);
    if (!config.apiBaseUrl || !token) return NextResponse.json({ detail: "Server-side alert configuration is unavailable." }, { status: 503 });
    const response = await fetch(`${config.apiBaseUrl}/alerts`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
    return new NextResponse(await response.text(), { status: response.status, headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" } });
  } catch { return NextResponse.json({ detail: "Alert backend is unavailable." }, { status: 502 }); }
}
