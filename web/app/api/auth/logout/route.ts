import { NextRequest, NextResponse } from "next/server";
import {
  getClearSessionCookieOptions,
  SESSION_COOKIE_NAME,
} from "../../../session";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const isProduction = process.env.NODE_ENV === "production";
  const clearOptions = getClearSessionCookieOptions(isProduction);

  // If request came from a form submit that expects a redirect:
  const acceptHeader = request.headers.get("accept") ?? "";
  const wantsHtml = acceptHeader.includes("text/html");

  const response = wantsHtml
    ? NextResponse.redirect(new URL("/login", request.url), { status: 303 })
    : NextResponse.json({ ok: true, message: "Logged out." }, { status: 200 });

  response.cookies.set(SESSION_COOKIE_NAME, "", clearOptions);
  // Also clean up any legacy cookie if present
  response.cookies.set("dashboard_view_token", "", clearOptions);

  return response;
}

export async function GET(request: NextRequest) {
  // Support standard GET redirect to login while clearing session
  const isProduction = process.env.NODE_ENV === "production";
  const clearOptions = getClearSessionCookieOptions(isProduction);

  const response = NextResponse.redirect(new URL("/login", request.url), { status: 303 });
  response.cookies.set(SESSION_COOKIE_NAME, "", clearOptions);
  response.cookies.set("dashboard_view_token", "", clearOptions);

  return response;
}
