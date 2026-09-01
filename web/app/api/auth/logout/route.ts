import { NextRequest, NextResponse } from "next/server";
import {
  getClearSessionCookieOptions,
  SESSION_COOKIE_NAME,
} from "../../../session";

export const dynamic = "force-dynamic";

function getBaseUrl(request: Request): string {
  const host = request.headers.get("x-forwarded-host") || request.headers.get("host");
  const proto =
    request.headers.get("x-forwarded-proto") ||
    (request.url.startsWith("https") ? "https" : "http");
  if (host) {
    return `${proto}://${host}`;
  }
  return request.url;
}

export async function POST(request: NextRequest) {
  const isHttps =
    request.url.startsWith("https://") ||
    request.headers.get("x-forwarded-proto") === "https";
  const clearOptions = getClearSessionCookieOptions(isHttps);

  // If request came from a form submit that expects a redirect:
  const acceptHeader = request.headers.get("accept") ?? "";
  const wantsHtml = acceptHeader.includes("text/html");

  const baseUrl = getBaseUrl(request);
  const response = wantsHtml
    ? NextResponse.redirect(new URL("/login", baseUrl), { status: 303 })
    : NextResponse.json({ ok: true, message: "Logged out." }, { status: 200 });

  response.cookies.set(SESSION_COOKIE_NAME, "", clearOptions);
  // Also clean up any legacy cookie if present
  response.cookies.set("dashboard_view_token", "", clearOptions);

  return response;
}

export async function GET(request: NextRequest) {
  // Support standard GET redirect to login while clearing session
  const isHttps =
    request.url.startsWith("https://") ||
    request.headers.get("x-forwarded-proto") === "https";
  const clearOptions = getClearSessionCookieOptions(isHttps);

  const baseUrl = getBaseUrl(request);
  const response = NextResponse.redirect(new URL("/login", baseUrl), { status: 303 });
  response.cookies.set(SESSION_COOKIE_NAME, "", clearOptions);
  response.cookies.set("dashboard_view_token", "", clearOptions);

  return response;
}
