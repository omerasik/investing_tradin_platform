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

## ADR-UP-004

- Source: all 16 isolated repositories at the local pins in
  `identity_activity_refresh_2026-08-31.json`
- Decision: RETAIN_EXISTING_PIN / DO_NOT_FETCH_OR_EXECUTE_REMOTE_ADVANCEMENTS
- Reason: 12 recorded remote branches have advanced since the last static
  inventory; advancement alone supplies no legal, security, reproducibility or
  benchmark evidence.
- Integration: none; no package, source, asset or runtime linkage is permitted.
- Review: after the complete RQ-029 scan/SBOM/license gate for an explicitly
  selected immutable candidate commit.

## ADR-UP-005

- Source: `microsoft/qlib` at the immutable pin recorded in
  `qlib_static_review_2026-08-31.json`
- Decision: DEFER_REFERENCE_ONLY
- Reason: the restricted static review found 18 high and 50 medium SAST signals
  and no exact direct dependency pins. The generated SBOM is declaration-only;
  it does not resolve the transitive supply chain or make an SCA claim.
- Integration: none; no package, source, asset, benchmark, container or runtime
  linkage is permitted.
- Review: only after an explicitly approved isolated resolution/triage and legal
  review; this decision does not authorize a qlib POC.
