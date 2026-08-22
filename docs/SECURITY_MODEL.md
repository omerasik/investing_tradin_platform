# Security Model

Threat model priorities are secret exposure, unauthorized execution, data poisoning/leakage, supply-chain compromise, privilege escalation and audit tampering. Enforce least privilege, validated schemas, secure configuration, rate limits, structured non-secret logs, dependency/secret/static scans, immutable audit events and backup encryption. Credentials are absent by design; upstream clones are isolated and unexecuted.

## Authentication and repository posture

Current authentication is a fail-closed configured bearer token suitable only
for local/paper operation. Cycle 218 adds an internal least-privilege
authorization boundary: viewer, researcher, data-steward, risk-reviewer,
auditor and operator roles receive explicit read/research/data/risk/alert/audit
permissions. Environment composition defaults to viewer, rejects an unknown
role as unavailable, and never accepts a role from the client request. This is
not production identity or RBAC: one deployment token still maps to one static
subject and role.

Cycle 222 adds the provider-independent external-session composition boundary.
It accepts claims only from an injected verifier responsible for cryptographic
token/key validation, rechecks exact HTTPS issuer, audience, timezone-aware
session age/expiry and required authentication methods, then maps allowlisted
groups to exactly one server-owned role. Missing, conflicting, stale, future or
tampered evidence fails closed. External-session composition requires a durable
decision sink; immutable PostgreSQL policies and allow/deny decisions bind the
policy UUID/version and retain only a session-ID hash, never the raw token or
session ID. This is an integration contract, not an OIDC implementation:
production still requires a selected IdP/verifier, key rotation, short-lived
server-side sessions, MFA enforcement at the provider, CSRF protection,
identity lifecycle governance, managed secrets and independent acceptance.

Cycle 217 applies deterministic API/dashboard response headers: no-store,
content policy, frame denial, MIME sniffing prevention, no-referrer, permissions
isolation and cross-origin isolation; production also receives HSTS. Protected
API deployments omit schema/documentation routes and invalid credentials return
a bearer challenge. These are defense-in-depth only. Dashboard framework
hydration currently requires inline script/style CSP allowances, and no header
replaces HTTPS termination, OIDC, MFA, external identity governance or
penetration tests.

The repository is PUBLIC as verified on 2026-08-15; visibility was not changed.
Credentials and private datasets are prohibited. `detect-secrets` scans tracked
source/configuration against a hash-only reviewed baseline; pnpm integrity
hashes are excluded, and four reviewed synthetic CI/test DSNs remain baselined.
Dependency audits cover production and development/test packages and block high
findings; SBOM/license inventories are retained. Upstream repositories stay
reference-only under `docs/upstream`; their licenses do not authorize copying
runtime code.

Cycle 219 reduces research-image privilege and build-context exposure. Its base
tag is digest-pinned, dependencies are exact-version locked, the context is an
explicit allow-list, no token is embedded, and the final process is UID/GID
10001. CI runs it read-only with all capabilities dropped,
`no-new-privileges`, a bounded no-exec tmpfs and explicit health/authorization
probes. This is defense-in-depth for the local-research API only: there is no
image signature/provenance, registry policy, IaC/network policy, production
secret manager, PostgreSQL deployment or penetration test.

Cycle 220 scans a saved copy of the built image with digest-pinned Trivy. The
scanner has no Docker socket and runs non-root/read-only with capabilities
dropped and no-new-privileges. CI retains the full vulnerability JSON and
CycloneDX 1.7 SBOM even when the later fixable HIGH/CRITICAL or EOL gate fails.
The verified report still contains 26 vendor-unfixed HIGH/CRITICAL findings;
zero are currently fixable. This explicit risk inventory is not a waiver or a
claim of vulnerability absence and must be revisited as fixes become available.

Cycle 221 grants the workflow OIDC and attestation write permissions solely to
produce Sigstore-signed SLSA provenance and CycloneDX SBOM attestations for the
retained image archive. The official action is commit-pinned, signed bundles
are retained and GitHub CLI verifies both predicates. Untrusted fork PRs skip
the authority. This does not grant cloud/provider/broker access and does not
publish or sign an OCI manifest; registry-native signing, release approvals and
deployment identity remain separate future controls.
