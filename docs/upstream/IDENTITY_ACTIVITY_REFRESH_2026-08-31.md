# Upstream Identity and Activity Refresh — 2026-08-31

This is a metadata-only refresh of the 16 isolated clones in
`C:\Users\omerf\upstream-research\repositories`. The accompanying
machine-readable snapshot is
[`identity_activity_refresh_2026-08-31.json`](identity_activity_refresh_2026-08-31.json).
Each immutable Git identifier is stored there as five ordered eight-character
fragments; concatenating a field's fragments yields its exact 40-character SHA.
This keeps the data machine-verifiable while allowing the repository's secret
scanner to distinguish identifiers from credential-like opaque strings.

The refresh read local Git identity and working-tree status, queried the
checked-out branch with `git ls-remote`, and read GitHub repository metadata.
It did not fetch, change branches, install dependencies, execute a script,
notebook or test, build an artifact, start a container, or run candidate code.
No clone was modified and no repository is a product dependency.

## Findings

| State | Count | Meaning |
|---|---:|---|
| `CURRENT` | 4 | The recorded pin equals the remote head of its recorded branch. |
| `ADVANCED` | 12 | The remote recorded-branch head differs from the local immutable pin. |
| `CLEAN` worktrees | 15 | No local Git changes. |
| `PARTIAL_WINDOWS_CHECKOUT` | 1 | `machine-learning-for-trading` retains its already-recorded incomplete Windows checkout; it was not repaired or used. |

The twelve advanced repositories are `ai-berkshire`, `backtesting.py`,
`claude-trading-skills`, `FinceptTerminal`, `FinRobot`, `freqtrade`,
`investing-algorithm-framework`, `Lean`, `machine-learning-for-trading`,
`nautilus_trader`, `vectorbt`, and `Vibe-Trading`. Their old pinned commits
remain the identity of the prior static review. An advanced remote head is not
reviewed, selected, adopted, or eligible for execution.

GitHub's license metadata is recorded as a discovery signal only. `NOASSERTION`
for FinceptTerminal, OpenBB and vectorbt does not supersede the root-license
inspection in [`LICENSE_MATRIX.md`](LICENSE_MATRIX.md); any discrepancy requires
the same legal and static-review gates before an immutable pin could be changed.

## Required next state transition

A repository may move beyond `REFERENCE_ONLY`, `DESIGN_REFERENCE`,
`CLEAN_ROOM_REIMPLEMENTATION`, `REJECTED` or `DEFER` only after a deliberate
new immutable pin and the full RQ-029 gate: root-license and attribution review,
secret/SCA/SAST/license/malicious-package scans, an SBOM, container assessment
where relevant, and a restricted non-secret execution environment. Updating a
remote branch is explicitly not approval to fetch or execute it.
