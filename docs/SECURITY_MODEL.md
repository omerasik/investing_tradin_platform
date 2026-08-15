# Security Model

Threat model priorities are secret exposure, unauthorized execution, data poisoning/leakage, supply-chain compromise, privilege escalation and audit tampering. Enforce least privilege, validated schemas, secure configuration, rate limits, structured non-secret logs, dependency/secret/static scans, immutable audit events and backup encryption. Credentials are absent by design; upstream clones are isolated and unexecuted.

## Authentication and repository posture

Current authentication is a fail-closed configured bearer token suitable only
for local/paper operation. Production architecture requires external OIDC,
short-lived server-side sessions, MFA, explicit operator/risk-reviewer/auditor
roles, CSRF protection, auditable authorization and managed secret injection.

The repository is PUBLIC as verified on 2026-08-15; visibility was not changed.
Credentials and private datasets are prohibited. `detect-secrets` scans tracked
source/configuration against a hash-only reviewed baseline; pnpm integrity
hashes are excluded, and four reviewed synthetic CI/test DSNs remain baselined.
Dependency audits block high findings, and SBOM/license inventories are retained. Upstream repositories stay
reference-only under `docs/upstream`; their licenses do not authorize copying
runtime code.
