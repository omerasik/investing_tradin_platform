import { NextResponse } from "next/server";

import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../dashboard-config";

export async function GET(request: Request) {
  try {
    const config = loadDashboardConfig(); const token = resolveDashboardOperatorToken(config);
    const target = new URL(request.url).searchParams.get("target");
    if (!config.apiBaseUrl || !token) return NextResponse.json({ detail: "Server-side risk API configuration is unavailable." }, { status: 503 });
    if (!target || !/^\/risk\/decisions\/[0-9a-f-]{36}$/i.test(target)) return NextResponse.json({ detail: "Invalid risk target." }, { status: 400 });
    const response = await fetch(`${config.apiBaseUrl}${target}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
    return new NextResponse(await response.text(), { status: response.status, headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" } });
  } catch { return NextResponse.json({ detail: "Risk backend is unavailable." }, { status: 502 }); }
}

export async function POST(request: Request) {
  try {
    const config = loadDashboardConfig(); const token = resolveDashboardOperatorToken(config);
    if (!config.apiBaseUrl || !token) return NextResponse.json({ detail: "Server-side risk API configuration is unavailable." }, { status: 503 });
    const response = await fetch(`${config.apiBaseUrl}/risk/portfolio`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: await request.text(), cache: "no-store" });
    return new NextResponse(await response.text(), { status: response.status, headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" } });
  } catch { return NextResponse.json({ detail: "Risk backend is unavailable." }, { status: 502 }); }
}
