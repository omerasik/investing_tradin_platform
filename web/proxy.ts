import { NextRequest, NextResponse } from "next/server";
import {
  getSessionSecret,
  SESSION_COOKIE_NAME,
  verifySessionToken,
} from "./app/session";

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

const PUBLIC_PATHS = new Set([
  "/login",
  "/api/auth/login",
  "/api/auth/logout",
  "/favicon.ico",
]);

function isPublicPath(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) {
    return true;
  }
  if (pathname.startsWith("/_next/")) {
    return true;
  }
  return false;
}

/**
 * A configured deployment requires a separate dashboard-view credential.
 * This authorizes viewing the Next.js workspace; it is never the backend operator
 * token and grants no mutation or execution authority.
 *
 * Query-string tokens (?token=...) are strictly rejected and never inspected.
 */
export async function proxy(request: NextRequest) {
  const nonce = crypto.randomUUID().replaceAll("-", "");
  const policy = contentSecurityPolicy(nonce);
  const expectedViewToken = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN?.trim();
  const pathname = request.nextUrl.pathname;

  if (!expectedViewToken) {
    return withCsp(
      NextResponse.json(
        { detail: "Dashboard authentication is not configured." },
        { status: 503 },
      ),
      policy,
    );
  }

  const authHeader = request.headers.get("authorization");
  const bearerToken = authHeader?.replace(/^Bearer\s+/i, "")?.trim();
  const sessionCookie = request.cookies.get(SESSION_COOKIE_NAME)?.value;

  let isAuthenticated = false;

  // 1. Check Bearer authorization header (used for machine/API testing)
  if (bearerToken && bearerToken === expectedViewToken) {
    isAuthenticated = true;
  }

  // 2. Check signed server session cookie
  if (!isAuthenticated && sessionCookie) {
    const sessionSecret = getSessionSecret();
    if (sessionSecret) {
      const verification = await verifySessionToken(sessionCookie, sessionSecret);
      if (verification.valid) {
        isAuthenticated = true;
      }
    }
  }

  // Allow public paths
  if (isPublicPath(pathname)) {
    // If user is already authenticated and visits /login, redirect to /
    if (pathname === "/login" && isAuthenticated) {
      const redirectRes = NextResponse.redirect(new URL("/", request.url));
      return withCsp(redirectRes, policy);
    }

    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("Content-Security-Policy", policy);
    requestHeaders.set("x-nonce", nonce);
    const nextRes = NextResponse.next({ request: { headers: requestHeaders } });
    return withCsp(nextRes, policy);
  }

  // Protected route handling
  if (!isAuthenticated) {
    // API routes return 401 JSON
    if (pathname.startsWith("/api/")) {
      return withCsp(
        NextResponse.json(
          { detail: "Dashboard authentication required." },
          { status: 401, headers: { "WWW-Authenticate": "Bearer" } },
        ),
        policy,
      );
    }

    // Page routes redirect to /login
    const loginUrl = new URL("/login", request.url);
    const redirectRes = NextResponse.redirect(loginUrl);
    return withCsp(redirectRes, policy);
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("Content-Security-Policy", policy);
  requestHeaders.set("x-nonce", nonce);
  const nextRes = NextResponse.next({ request: { headers: requestHeaders } });
  return withCsp(nextRes, policy);
}

export const config = { matcher: ["/", "/login", "/api/:path*"] };
