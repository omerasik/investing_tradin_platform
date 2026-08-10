import { NextResponse } from "next/server";

import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../dashboard-config";

export async function GET(request: Request) {
  try {
    const config = loadDashboardConfig(); const token = resolveDashboardOperatorToken(config);
    const target = new URL(request.url).searchParams.get("target");
    if (!config.apiBaseUrl || !token) return NextResponse.json({ detail: "Server-side research configuration is unavailable." }, { status: 503 });
    if (!target || !/^\/research\/(strategies|experiments|promotions)\/[0-9a-f-]{36}$/i.test(target)) return NextResponse.json({ detail: "Invalid research target." }, { status: 400 });
    const response = await fetch(`${config.apiBaseUrl}${target}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
    return new NextResponse(await response.text(), { status: response.status, headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" } });
  } catch { return NextResponse.json({ detail: "Research backend is unavailable." }, { status: 502 }); }
}

export async function POST(request: Request) {
  try {
    const config = loadDashboardConfig(); const token = resolveDashboardOperatorToken(config);
    if (!config.apiBaseUrl || !token) return NextResponse.json({ detail: "Server-side research configuration is unavailable." }, { status: 503 });
    const body: unknown = await request.json();
    if (!body || typeof body !== "object" || Array.isArray(body)) return NextResponse.json({ detail: "Invalid research launch." }, { status: 400 });
    const payload = body as Record<string, unknown>;
    const action = payload.action;
    if (action !== "launch_backtest" && action !== "create_strategy") return NextResponse.json({ detail: "Invalid research action." }, { status: 400 });
    if (action === "launch_backtest" && (typeof payload.strategy_id !== "string" || !/^[0-9a-f-]{36}$/i.test(payload.strategy_id))) return NextResponse.json({ detail: "Invalid research launch." }, { status: 400 });
    delete payload.action;
    const route = action === "launch_backtest" ? "/research/backtests" : "/research/strategies";
    const response = await fetch(`${config.apiBaseUrl}${route}`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: JSON.stringify(payload), cache: "no-store" });
    return new NextResponse(await response.text(), { status: response.status, headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" } });
  } catch { return NextResponse.json({ detail: "Research backend is unavailable." }, { status: 502 }); }
}
