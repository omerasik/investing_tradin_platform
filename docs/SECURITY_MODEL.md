# Security Model

Threat model priorities are secret exposure, unauthorized execution, data poisoning/leakage, supply-chain compromise, privilege escalation and audit tampering. Enforce least privilege, validated schemas, secure configuration, rate limits, structured non-secret logs, dependency/secret/static scans, immutable audit events and backup encryption. Credentials are absent by design; upstream clones are isolated and unexecuted.
