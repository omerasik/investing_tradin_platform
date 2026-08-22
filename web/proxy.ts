import { NextRequest, NextResponse } from "next/server";

function contentSecurityPolicy(nonce: string): string {
  return [
    "default-src 'self'",
    "base-uri 'self'",
    "connect-src 'self'",
    "font-src 'self'",
    "frame-ancestors 'none'",
    "img-src 'self' data:",
    "object-src 'none'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    `style-src 'self' 'nonce-${nonce}'`,
    "form-action 'self'",
  ].join("; ");
}

function withCsp(response: NextResponse, policy: string): NextResponse {
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

/**
 * A configured deployment can require a separate dashboard-view credential.
 * This credential authorizes viewing the Next workspace only; it is never the
 * backend operator token and grants no mutation or execution authority.
 */
export function proxy(request: NextRequest) {
  const nonce = crypto.randomUUID().replaceAll("-", "");
  const policy = contentSecurityPolicy(nonce);
  const expected = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN;
  if (!expected) {
    return withCsp(
      NextResponse.json(
        { detail: "Dashboard authentication is not configured." },
        { status: 503 },
      ),
      policy,
    );
  }
  if (request.headers.get("authorization") !== `Bearer ${expected}`) {
    return withCsp(
      NextResponse.json(
        { detail: "Dashboard authentication required." },
        { status: 401, headers: { "WWW-Authenticate": "Bearer" } },
      ),
      policy,
    );
  }
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("Content-Security-Policy", policy);
  requestHeaders.set("x-nonce", nonce);
  return withCsp(
    NextResponse.next({ request: { headers: requestHeaders } }),
    policy,
  );
}

export const config = { matcher: ["/", "/api/:path*"] };
