# Adoption Decision Records

## ADR-UP-001

- Source: all audited repositories at commits in `REPOSITORY_CATALOG.md`
- Component/pattern: repository structure and domain concepts
- Decision: REFERENCE_ONLY / REIMPLEMENT
- License/security/performance/testing: only preliminary static review; no runtime approval
- Integration: no source imports, package dependencies or copied assets
- Rollback: delete internally authored pattern implementation without upstream runtime coupling
- Review: before each implementation touching an upstream-inspired boundary

## ADR-UP-002

- Source: Backtesting.py, OpenBB, FinceptTerminal, Freqtrade, VectorBT
- Decision: REJECT as direct runtime dependencies
- Reason: AGPL, GPL, or Commons-Clause restriction; maintain clean proprietary core

## ADR-UP-003

- Source: Lean and NautilusTrader
- Decision: DEFER isolated proof of concept
- Reason: potentially useful fidelity, but operational complexity and license/compliance require an identical-data benchmark, SBOM, security review and legal review.
