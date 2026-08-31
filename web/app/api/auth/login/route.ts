import { NextResponse } from "next/server";
import {
  constantTimeCompare,
  createSessionToken,
  getSessionCookieOptions,
  getSessionSecret,
  SESSION_COOKIE_NAME,
} from "../../../session";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const expected = process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN?.trim();
    if (!expected) {
      return NextResponse.json(
        { detail: "Dashboard authentication is not configured." },
        { status: 503 },
      );
    }

    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const password = typeof body.password === "string" ? body.password.trim() : ""; // pragma: allowlist secret

    if (!password || !constantTimeCompare(password, expected)) {
      return NextResponse.json(
        { detail: "Invalid dashboard credentials." },
        { status: 401 },
      );
    }

    const secret = getSessionSecret();
    if (!secret) {
      return NextResponse.json(
        { detail: "Dashboard session secret is not configured." },
        { status: 503 },
      );
    }

    const token = await createSessionToken(secret);
    const isProduction = process.env.NODE_ENV === "production";
    const cookieOptions = getSessionCookieOptions(isProduction);

    const response = NextResponse.json({ ok: true, message: "Authenticated." }, { status: 200 });
    response.cookies.set(SESSION_COOKIE_NAME, token, cookieOptions);

    return response;
  } catch {
    return NextResponse.json(
      { detail: "Authentication request failed." },
      { status: 500 },
    );
  }
}
