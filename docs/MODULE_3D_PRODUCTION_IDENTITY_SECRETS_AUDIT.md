# Module 3D — Production Identity, Secret Management & Durable Audit

Companion to [MODULE_3C_POSTGRES_RUNTIME_WIRING.md](MODULE_3C_POSTGRES_RUNTIME_WIRING.md) and
[PRODUCTION_READINESS_MATRIX.md](PRODUCTION_READINESS_MATRIX.md). Module 3C wired the
PostgreSQL authority graph into the protected runtime but left `production` mode
unconditionally fail-closed (`trade_platform.runtime_app` raised
`production_mode_not_yet_supported` for every input) because three authorities the
matrix marked **BLOCKED**/**PARTIAL** did not exist: a real production identity
architecture, a secret-management boundary beyond raw environment variables, and a
durable PostgreSQL audit authority. Module 3D implements all three and, only once all
three are actually configured, lets `production` start.

**Live trading remains disabled.** Nothing in this module touches
`PlatformConfig.live_trading_enabled`, activates a market-data or news provider,
integrates a broker, or adds network-connected execution. The only new outbound network
call this module can make is an HTTPS fetch of a configured identity provider's JWKS
key set — identity infrastructure, not a trading data or execution path.

## 1. What was blocking `production`

Before this module, `create_runtime_app_from_environment` raised unconditionally for
`TRADE_PLATFORM_ENVIRONMENT=production`:

```
raise RuntimeCompositionError(
    "production_mode_not_yet_supported: production identity, secret-manager and "
    "production-grade audit-durability authorities are not implemented; refusing "
    "to start rather than serve an unready production app"
)
```

That refusal was itself the correct fail-closed behavior for the state of the codebase
at the time — see `docs/MODULE_3C_POSTGRES_RUNTIME_WIRING.md` §"Remaining blockers".
This module replaces the unconditional refusal with real authorities, each gated by its
own fail-closed check, so `production` still refuses to start if any one of them is
missing or misconfigured.

## 2. New authorities

### 2.1 Real external identity (`trade_platform.oidc_identity`)

`trade_platform.external_identity.ExternalTokenVerifier` was deliberately left as an
injected `Protocol` with no implementation — the module's own docstring says a
deployment "must provide signature/key/issuer validation ... before composition".
`JwksExternalTokenVerifier` is that implementation: it verifies a bearer token's
signature against keys published by a configured OpenID Connect / OAuth2 issuer's JWKS
endpoint (RS/ES/PS-family asymmetric algorithms only — `none` and any symmetric `HS*`
algorithm are rejected at construction), checks `iss`/`aud`/`exp`/`iat`, and maps the
verified claims onto the existing `VerifiedExternalSession` shape that
`ExternalSessionAuthenticator` already knew how to validate against an approved
`ExternalIdentityMappingPolicy` (issuer/audience trust, session age, required
authentication methods, group→role mapping — unchanged from Module 2/3A). The
`PyJWKClient` backing it caches keys by `kid` and only refreshes on an unseen one, so a
compromised or misbehaving issuer cannot force unbounded per-request network calls.

### 2.2 Durable session revocation (`trade_platform.external_identity.PostgresSessionRevocationStore`)

A JWT's own `exp` claim cannot be shortened after issuance. Production-safe session
behavior requires that an operator's access can be revoked immediately — compromised
device, offboarding, incident response — without waiting for token expiry.
`PostgresSessionRevocationStore` is a durable, insert-only revocation ledger
(`operator_session_events`, immutable at the schema level via the same
`prevent_immutable_mutation()` trigger every other durable-evidence table in this
codebase uses) keyed by the session id's SHA-256 hash — the store never needs to see,
or persist, the raw external session token. `ExternalSessionAuthenticator` now accepts
an optional `revocation_store` and checks it on every authentication; a revoked session
is rejected with `external_session_revoked` even while its JWT remains unexpired.

### 2.3 Secret-management boundary (`trade_platform.secrets_manager`)

The readiness matrix's honest assessment was "Environment variables only... no secret
manager." `SecretProvider` is a real boundary with two implementations:

- `EnvironmentSecretProvider` — the pre-existing behavior, explicitly
  `is_production_capable = False`. Local/dev/paper flows are completely unchanged.
- `FileSecretProvider` — production-capable. One file per secret under a
  deployment-provisioned directory: the on-disk contract every mainstream secret
  manager's sync tooling already speaks (Kubernetes `Secret`/CSI-driver volumes, Vault
  Agent, AWS/GCP secret-store CSI drivers, Docker/Swarm/Nomad secrets under
  `/run/secrets`). This module never talks to a specific vendor API; it defines the
  boundary a real secret manager is expected to populate onto disk, and it fails closed
  if the directory is missing, a secret file is missing/empty, or (on POSIX) the file's
  permissions are group/world readable or writable.

Production composition uses `FileSecretProvider` exclusively; it is never given the
option to fall back to `EnvironmentSecretProvider`.

### 2.4 Durable PostgreSQL audit authority (`trade_platform.postgres_audit.PostgresAuditStore`)

The matrix classified audit as **BLOCKED**: "No Postgres audit store exists at all;
current store is explicitly scoped 'for development and paper simulation' by its own
docstring." `PostgresAuditStore` implements the same `append`/`recent`/`query`/`get`
shape as `SQLiteAuditStore` (formalized as the new `trade_platform.audit.AuditStore`
protocol so `build_app` depends on neither concrete type), backed by a new `audit_events`
table that is append-only, content-hashed, and immutable at the schema level (no
`UPDATE`/`DELETE` trigger path exists — enforced by PostgreSQL itself, not just by which
methods this class happens to expose). The dashboard-facing routes in `api.py` were
already read-only against whatever store is injected; nothing there changed.

### 2.5 CSRF protection for cookie-authenticated mutations (`trade_platform.csrf`)

Every route and test in this codebase authenticates via a bearer token in the
`Authorization` header, which a browser never attaches automatically — CSRF is
structurally not a risk for that shape of request. It becomes a real risk only once a
request carries an *ambient* credential a browser attaches automatically: a cookie.
`CsrfProtectionMiddleware` implements a signed double-submit-cookie check: it activates
only on a mutating request that carries the first-party operator-session cookie
(`trade_platform_session`), and requires a matching `X-CSRF-Token` header (an HMAC of
the session id under a deployment CSRF secret — never stored server-side, so no
session-keyed CSRF table is needed to validate one). A pure bearer-token request — the
shape every existing route/test exercises — never carries that cookie and is left
completely untouched. Production composition adds this middleware unconditionally,
sourcing the CSRF secret from the new secret-management boundary (`CSRF_SIGNING_KEY`).

### 2.6 Actor identity wired into durable authorization/audit evidence

Unchanged in mechanism, now load-bearing in production: `ExternalSessionAuthenticator`
already resolved the authenticated subject/role/session-hash from the verified token
and passed it to `RequireOperatorPermission`, which builds a hash-chained
`AuthorizationDecision` for every allow/deny and appends it through
`authorization_decision_sink` (§2.7). Module 3D's only change here is *which*
authorities production wires in for that flow — the same `PostgresIdentitySecurityStore`
already used for the mapping-policy authority also durably records every authorization
decision, and the new `PostgresAuditStore` durably records every explicit audit event —
both keyed to the real authenticated subject, not a placeholder.

### 2.7 Identity mapping policy lookup at startup

`PostgresIdentitySecurityStore` gained `latest_enabled_policy(policy_name)`, so
production composition loads the currently-approved mapping policy for a configured
policy name directly from its durable authority rather than needing policy internals
threaded through environment variables. If no enabled policy exists for that name,
composition fails closed.

## 3. Production runtime composition

`trade_platform.runtime_app.compose_protected_postgres_app` now accepts optional
production-only keyword arguments (`oidc_issuer`, `oidc_audience`, `oidc_jwks_url`,
`identity_policy_name`, `secrets_directory`). `PAPER` composition is byte-for-byte
unchanged from Module 3C — those arguments are simply unused for `PAPER`.
`create_runtime_app_from_environment` reads five new required environment variables for
`production`:

| Variable | Purpose |
|---|---|
| `TRADE_PLATFORM_OIDC_ISSUER` | Trusted issuer URL (`https://`, no trailing slash) |
| `TRADE_PLATFORM_OIDC_AUDIENCE` | Expected `aud` claim |
| `TRADE_PLATFORM_OIDC_JWKS_URL` | JWKS endpoint (`https://`) |
| `TRADE_PLATFORM_IDENTITY_POLICY_NAME` | Name of the approved mapping policy to load |
| `TRADE_PLATFORM_SECRETS_DIR` | Directory `FileSecretProvider` reads from |

`_compose_production_identity_authorities` (in `trade_platform.runtime_app`) composes
all of §2 in one place and fails closed — raising `RuntimeCompositionError` — for any
missing setting, an unreachable secrets directory, a missing `CSRF_SIGNING_KEY` secret
file, or no approved identity mapping policy for the configured name. Production never
starts partially wired: every one of these is checked before `build_app` is ever
called, and any failure closes the already-opened PostgreSQL connection before
re-raising.

## 4. Hard constraints verified unchanged

- `PlatformConfig.__post_init__` still unconditionally rejects `live_trading_enabled=True`
  (`config.py`) — untouched by this module.
- No market-data, news, or broker adapter was activated, modified, or given new
  credentials.
- No SQLite fallback exists anywhere in the production or paper composition path; this
  module only adds authorities on top of the existing no-fallback rule from Module 3C.
- Solo-maintainer branch protection (PR required, `verify` required and strict,
  conversation resolution required, force-push/branch-deletion blocked, 0 required
  approvals, CODEOWNERS present) is unchanged by this module.

## 5. What this module does not claim

- It does not deploy or configure a real OIDC identity provider, secret manager, or
  Kubernetes/Vault/cloud secret store — those are deployment-time integrations. This
  module defines and tests the boundary those integrations are expected to satisfy.
- It does not add a frontend (Next.js) session-cookie login flow; the CSRF middleware
  is a real, tested backend mechanism ready to protect a first-party cookie-based
  operator console once one exists, but no such flow is wired up by this module.
- `docs/PRODUCTION_READINESS_MATRIX.md` is updated to reflect what is now real, not to
  claim production is fully deployed — deployment, observability export, and scheduler
  automation remain open gaps tracked there.
