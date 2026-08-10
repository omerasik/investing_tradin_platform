import { NextResponse } from "next/server";
import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../dashboard-config";

export async function GET(request: Request) {
  try {
    const config = loadDashboardConfig(); const token = resolveDashboardOperatorToken(config);
    const target = new URL(request.url).searchParams.get("target");
    if (!config.apiBaseUrl || !token) return NextResponse.json({ detail: "Server-side investment configuration is unavailable." }, { status: 503 });
    if (!target || !/^(\/investments\/theses\/[0-9a-f-]{36}|\/investments\/portfolios\/[A-Za-z0-9:_-]+)(\?.*)?$/i.test(target)) return NextResponse.json({ detail: "Invalid investment target." }, { status: 400 });
    const response = await fetch(`${config.apiBaseUrl}${target}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
    return new NextResponse(await response.text(), { status: response.status, headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" } });
  } catch { return NextResponse.json({ detail: "Investment backend is unavailable." }, { status: 502 }); }
}
