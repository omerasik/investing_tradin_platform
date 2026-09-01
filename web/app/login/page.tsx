export const dynamic = "force-dynamic";

interface LoginPageProps {
  searchParams?: Promise<{ error?: string }> | { error?: string };
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  const error = params?.error;

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

        <form action="/api/auth/login" method="POST">
          <div className="form-group">
            <label htmlFor="credential-input">Operator Access Credential</label>
            <input
              id="credential-input"
              name="credential"
              type="password" // pragma: allowlist secret
              className="form-input"
              placeholder="Enter view credential"
              autoComplete="current-password" // pragma: allowlist secret
              required
            />
          </div>

          <button
            type="submit"
            className="btn-submit"
          >
            Sign In
          </button>
        </form>

        <footer style={{ marginTop: "24px", textAlign: "center", fontSize: "12px", color: "#6b7d9c" }}>
          Safe local development session • Server-side authority separation
        </footer>
      </div>
    </main>
  );
}
