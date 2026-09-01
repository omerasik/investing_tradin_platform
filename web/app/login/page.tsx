"use client";

import { useEffect, useState } from "react";

export default function LoginPage() {
  const [hydrated, setHydrated] = useState(false);
  const [credential, setCredential] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHydrated(true);
  }, []);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const formData = new FormData(form);
    const formCredential = ((formData.get("credential") as string) || credential || "").trim(); // pragma: allowlist secret

    if (!formCredential) {
      setError("Please enter your operator access credential.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential: formCredential }),
      });

      if (res.ok) {
        window.location.replace("/");
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
            <label htmlFor="credential-input">Operator Access Credential</label>
            <input
              id="credential-input"
              name="credential"
              type="password" // pragma: allowlist secret
              className="form-input"
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
              placeholder="Enter view credential"
              autoComplete="current-password" // pragma: allowlist secret
              required
              disabled={loading}
            />
          </div>

          <button
            type="submit"
            className="btn-submit"
            disabled={!hydrated || loading}
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
