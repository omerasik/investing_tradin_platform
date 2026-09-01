import { timingSafeEqual } from "node:crypto";

export const SESSION_COOKIE_NAME = "dashboard_session";
export const DEFAULT_SESSION_MAX_AGE_SECONDS = 28800; // 8 hours

export type SessionPayload = {
  sub: string;
  iat: number;
  exp: number;
};

export type SessionVerificationResult = {
  valid: boolean;
  expired?: boolean;
  payload?: SessionPayload;
};

function uint8ArrayToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function base64UrlToUint8Array(str: string): Uint8Array {
  let base64 = str.replace(/-/g, "+").replace(/_/g, "/");
  while (base64.length % 4 !== 0) {
    base64 += "=";
  }
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

export function constantTimeCompare(a: string, b: string): boolean {
  try {
    const bufA = Buffer.from(a, "utf8");
    const bufB = Buffer.from(b, "utf8");
    if (bufA.length !== bufB.length) {
      return false;
    }
    return timingSafeEqual(bufA, bufB);
  } catch {
    return false;
  }
}

export function getSessionSecret(): string {
  return (
    process.env.TRADE_PLATFORM_SESSION_SECRET?.trim() ||
    process.env.TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN?.trim() ||
    ""
  );
}

export async function createSessionToken(
  secret: string,
  maxAgeSeconds = DEFAULT_SESSION_MAX_AGE_SECONDS,
): Promise<string> {
  if (!secret) {
    throw new Error("Session secret is required to create a session token.");
  }
  const now = Math.floor(Date.now() / 1000);
  const payload: SessionPayload = {
    sub: "operator",
    iat: now,
    exp: now + maxAgeSeconds,
  };
  const encoder = new TextEncoder();
  const payloadBytes = encoder.encode(JSON.stringify(payload));
  const encodedPayload = uint8ArrayToBase64Url(payloadBytes);

  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );

  const signatureBuffer = await crypto.subtle.sign(
    "HMAC",
    key,
    encoder.encode(encodedPayload),
  );
  const encodedSignature = uint8ArrayToBase64Url(new Uint8Array(signatureBuffer));

  return `${encodedPayload}.${encodedSignature}`;
}

export async function verifySessionToken(
  token: string,
  secret: string,
): Promise<SessionVerificationResult> {
  if (!token || !secret) {
    return { valid: false };
  }
  const parts = token.split(".");
  if (parts.length !== 2) {
    return { valid: false };
  }
  const [encodedPayload, encodedSignature] = parts;

  try {
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      "raw",
      encoder.encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["verify"],
    );

    const signatureBytes = base64UrlToUint8Array(encodedSignature);
    const isValidSignature = await crypto.subtle.verify(
      "HMAC",
      key,
      signatureBytes as unknown as BufferSource,
      encoder.encode(encodedPayload),
    );

    if (!isValidSignature) {
      return { valid: false };
    }

    const payloadBytes = base64UrlToUint8Array(encodedPayload);
    const decoder = new TextDecoder();
    const payloadJson = decoder.decode(payloadBytes);
    const payload: SessionPayload = JSON.parse(payloadJson);

    if (!payload.sub || typeof payload.exp !== "number" || typeof payload.iat !== "number") {
      return { valid: false };
    }

    const now = Math.floor(Date.now() / 1000);
    if (payload.exp <= now) {
      return { valid: false, expired: true, payload };
    }

    return { valid: true, payload };
  } catch {
    return { valid: false };
  }
}

export function getSessionCookieOptions(secure = false, maxAge = DEFAULT_SESSION_MAX_AGE_SECONDS) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    path: "/",
    maxAge,
    secure,
  };
}

export function getClearSessionCookieOptions(secure = false) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    path: "/",
    maxAge: 0,
    expires: new Date(0),
    secure,
  };
}
