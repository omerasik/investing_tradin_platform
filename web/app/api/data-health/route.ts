import { NextResponse } from "next/server";
import { loadDashboardConfig, resolveDashboardOperatorToken } from "../../dashboard-config";

export async function GET(request: Request) {
  let backendUrl: string | undefined;
  let operatorToken: string | undefined;
  try {
    const config = loadDashboardConfig();
    backendUrl = config.apiBaseUrl;
    operatorToken = resolveDashboardOperatorToken(config);
  } catch {
    return NextResponse.json({ detail: "Server-side data-health configuration is invalid." }, { status: 503 });
  }
  if (!backendUrl || !operatorToken) {
    return NextResponse.json({ detail: "Server-side data-health configuration is unavailable." }, { status: 503 });
  }
  const target = new URL(request.url).searchParams.get("target");
  if (!target || !target.startsWith("/data-health/")) {
    return NextResponse.json({ detail: "Invalid data-health target." }, { status: 400 });
  }
  try {
    const response = await fetch(`${backendUrl}${target}`, {
      headers: { Authorization: `Bearer ${operatorToken}` }, cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "Data-health backend is unavailable." }, { status: 502 });
  }
}
