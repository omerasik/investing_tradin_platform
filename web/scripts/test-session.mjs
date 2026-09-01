import assert from "node:assert/strict";
import {
  constantTimeCompare,
  createSessionToken,
  getClearSessionCookieOptions,
  getSessionCookieOptions,
  SESSION_COOKIE_NAME,
  verifySessionToken,
} from "../app/session.ts";

console.log("Running session & cookie unit tests...");

const secret = "test-secret-12345-secure-key"; // pragma: allowlist secret

// 1. Session token creation and successful verification
const token = await createSessionToken(secret, 3600);
assert.ok(token.includes("."), "Token should contain payload.signature separator");
const result = await verifySessionToken(token, secret);
assert.equal(result.valid, true, "Valid token should verify successfully");
assert.equal(result.payload?.sub, "operator", "Payload subject should be operator");
assert.ok(typeof result.payload?.exp === "number", "Payload expiry should be a number");

// 2. Tampered signature should be rejected
const [payloadPart, sigPart] = token.split(".");
const tamperedSig = sigPart.slice(0, -2) + "xx";
const tamperedResult = await verifySessionToken(`${payloadPart}.${tamperedSig}`, secret);
assert.equal(tamperedResult.valid, false, "Tampered signature must be rejected");

// 3. Tampered payload should be rejected
const tamperedPayload = Buffer.from(JSON.stringify({ sub: "admin", exp: 9999999999, iat: 1 }))
  .toString("base64")
  .replace(/\+/g, "-")
  .replace(/\//g, "_")
  .replace(/=+$/, "");
const tamperedPayloadResult = await verifySessionToken(`${tamperedPayload}.${sigPart}`, secret);
assert.equal(tamperedPayloadResult.valid, false, "Tampered payload must be rejected");

// 4. Token signed with wrong secret should be rejected
const wrongSecretResult = await verifySessionToken(token, "wrong-secret-999");
assert.equal(wrongSecretResult.valid, false, "Token with wrong secret must be rejected");

// 5. Expired token should be identified as expired
const expiredToken = await createSessionToken(secret, -10); // expired 10 seconds ago
const expiredResult = await verifySessionToken(expiredToken, secret);
assert.equal(expiredResult.valid, false, "Expired token must not be valid");
assert.equal(expiredResult.expired, true, "Expired token must flag expired: true");

// 6. Malformed tokens should be rejected
assert.equal((await verifySessionToken("", secret)).valid, false);
assert.equal((await verifySessionToken("invalid", secret)).valid, false);
assert.equal((await verifySessionToken("a.b.c", secret)).valid, false);

// 7. Constant-time comparison
assert.equal(constantTimeCompare("secret123", "secret123"), true);
assert.equal(constantTimeCompare("secret123", "secret124"), false);
assert.equal(constantTimeCompare("short", "muchlongerstring"), false);
assert.equal(constantTimeCompare("", ""), true);

// 8. Cookie options verification
const devCookie = getSessionCookieOptions(false, 28800);
assert.equal(devCookie.httpOnly, true, "Cookie must be HttpOnly");
assert.equal(devCookie.sameSite, "lax", "Cookie SameSite must be lax");
assert.equal(devCookie.path, "/", "Cookie path must be /");
assert.equal(devCookie.secure, false, "Dev cookie secure should be false for HTTP");
assert.equal(devCookie.maxAge, 28800, "Cookie maxAge must be 28800");

const prodCookie = getSessionCookieOptions(true, 28800);
assert.equal(prodCookie.secure, true, "Prod cookie secure must be true for HTTPS");

const clearCookie = getClearSessionCookieOptions(false);
assert.equal(clearCookie.maxAge, 0, "Clear cookie maxAge must be 0");
assert.equal(clearCookie.httpOnly, true, "Clear cookie must be HttpOnly");
assert.equal(SESSION_COOKIE_NAME, "dashboard_session");

console.log("PASS: all session and cookie security tests passed successfully.");
