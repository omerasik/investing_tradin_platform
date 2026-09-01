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

    const contentType = request.headers.get("content-type") || "";
    let credential = ""; // pragma: allowlist secret
    const isFormSubmit =
      contentType.includes("application/x-www-form-urlencoded") ||
      contentType.includes("multipart/form-data");

    if (isFormSubmit) {
      const formData = await request.formData();
      const formCred = formData.get("credential") || formData.get("password"); // pragma: allowlist secret
      if (typeof formCred === "string") {
        credential = formCred.trim(); // pragma: allowlist secret
      }
    } else {
      const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
      credential = // pragma: allowlist secret
        typeof body.credential === "string"
          ? body.credential.trim()
          : typeof body.password === "string" // pragma: allowlist secret
            ? (body.password as string).trim() // pragma: allowlist secret
            : "";
    }

    if (!credential || !constantTimeCompare(credential, expected)) {
      if (isFormSubmit) {
        const loginUrl = new URL("/login?error=Invalid+dashboard+credentials.", request.url);
        return NextResponse.redirect(loginUrl, { status: 303 });
      }
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
    const isHttps =
      request.url.startsWith("https://") ||
      request.headers.get("x-forwarded-proto") === "https";
    const cookieOptions = getSessionCookieOptions(isHttps);

    if (isFormSubmit) {
      const redirectUrl = new URL("/", request.url);
      const response = NextResponse.redirect(redirectUrl, { status: 303 });
      response.cookies.set(SESSION_COOKIE_NAME, token, cookieOptions);
      return response;
    }

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
