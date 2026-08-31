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

## ADR-UP-006

- Source: `AI4Finance-Foundation/FinRL-Trading` at the immutable pin recorded
  in `finrl_static_review_2026-08-31.json`
- Decision: DEFER_REFERENCE_ONLY
- Reason: static SAST coverage is incomplete because one tracked Python file is
  not UTF-8 decodable by Bandit; 25 direct production declarations are
  unpinned, unresolved and include a declared live-trading entry point.
- Integration: none; no package, source, asset, benchmark, container or runtime
  linkage is permitted.
- Review: only after explicit isolated security/dependency/legal approval. This
  decision never authorizes live trading or a FinRL POC.

## ADR-UP-007

- Source: `nautechsystems/nautilus_trader` at the immutable pin recorded in
  `nautilus_static_review_2026-08-31.json`
- Decision: DEFER_REFERENCE_ONLY
- Reason: LGPL-3.0-or-later requires legal review, 83 medium SAST findings are
  untriaged, and static lockfile inventory is not a vulnerability result.
- Integration: none; no package, source, asset, benchmark, container or runtime
  linkage is permitted.
- Review: only after explicit isolated SCA/security/legal approval. This
  decision does not authorize an engine POC or trading execution.

## ADR-UP-008

- Source: `polakowo/vectorbt` at the immutable pin recorded in
  `vectorbt_static_review_2026-08-31.json`
- Decision: DEFER_REFERENCE_ONLY
- Reason: Apache 2.0 with Commons Clause requires legal review, 12 medium SAST
  findings are untriaged, and no resolved SCA was run.
- Integration: none; no package, source, asset, benchmark, container or runtime
  linkage is permitted.
- Review: only after explicit isolated SCA/security/legal approval. This does
  not authorize a VectorBT POC or trading execution.

## ADR-UP-009

- Source: `kernc/backtesting.py` at the immutable pin recorded in
  `backtesting_static_review_2026-08-31.json`
- Decision: DEFER_REFERENCE_ONLY
- Reason: AGPL-3.0-or-later requires legal review, 113 low-severity SAST
  findings remain untriaged, and three direct production declarations are not
  exact-pinned or resolved by SCA.
- Integration: none; no package, source, asset, benchmark or runtime linkage is
  permitted.
- Review: only after explicit isolated SCA/security/legal approval. This does
  not authorize a backtesting.py POC or trading execution.
