import { NextRequest, NextResponse } from "next/server";

/**
 * A configured deployment can require a separate dashboard-view credential.
 * This credential authorizes viewing the Next workspace only; it is never the
 * backend operator token and grants no mutation or execution authority.
 */
export function proxy(request: NextRequest) {
  const expected = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN;
  if (!expected) {
    return NextResponse.json({ detail: "Dashboard authentication is not configured." }, { status: 503 });
  }
  if (request.headers.get("authorization") !== `Bearer ${expected}`) {
    return NextResponse.json({ detail: "Dashboard authentication required." }, {
      status: 401,
      headers: { "WWW-Authenticate": "Bearer" },
    });
  }
  return NextResponse.next();
}

export const config = { matcher: ["/", "/api/:path*"] };
