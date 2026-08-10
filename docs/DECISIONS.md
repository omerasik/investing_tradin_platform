# Architecture Decisions

## ADR-001: Research and execution separation

Decision: use separate data states and deterministic, independent risk checks between research outputs and paper OMS. Rationale: prevents exploratory/LLM code from controlling execution. Status: accepted.

## ADR-002: Paper-only safety gate

Decision: do not add broker credentials, broker SDKs, or real-order routes. Rationale: authoritative requirement and lack of readiness evidence. Status: accepted.

## ADR-003: Clean-room upstream adoption

Decision: upstream repositories are isolated references; no code is imported before license/security/architecture approval. Rationale: license and supply-chain safety. Status: accepted.
