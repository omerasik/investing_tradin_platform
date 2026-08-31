"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!password.trim()) {
      setError("Please enter your operator access credential.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (res.ok) {
        router.push("/");
        router.refresh();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(
          typeof data.detail === "string"
            ? data.detail
            : "Invalid credentials. Please verify your configured token.",
        );
      }
    } catch {
      setError("Unable to connect to authentication service.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main id="main-content">
      <div className="login-card">
        <div className="login-header">
          <span className="badge" style={{ marginBottom: "12px", display: "inline-block" }}>
            LIVE TRADING: DISABLED
          </span>
          <div className="eyebrow" style={{ marginTop: "4px" }}>
            RESEARCH / PAPER ONLY
          </div>
          <h1>Trade Investing Panel</h1>
          <p>Operator Authentication</p>
        </div>

        {error ? (
          <div className="error-banner" role="alert" aria-live="polite">
            {error}
          </div>
        ) : null}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="password-input">Operator Access Credential</label>
            <input
              id="password-input"
              name="password"
              type="password"
              className="form-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter view credential"
              autoComplete="current-password"
              required
              disabled={loading}
            />
          </div>

          <button
            type="submit"
            className="btn-submit"
            disabled={loading || !password.trim()}
          >
            {loading ? "Authenticating..." : "Sign In"}
          </button>
        </form>

        <footer style={{ marginTop: "24px", textAlign: "center", fontSize: "12px", color: "#6b7d9c" }}>
          Safe local development session • Server-side authority separation
        </footer>
      </div>
    </main>
  );
}
