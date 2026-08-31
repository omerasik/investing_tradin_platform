# Qlib Restricted Static Review — 2026-08-31

This review covers only the immutable local `microsoft/qlib` `main` pin recorded
as ordered fragments in the machine-readable record. It is a read-only review: no
clone was fetched or altered, and no candidate dependency, import, script,
notebook, test, build, container, package or network request was run.

The machine-readable record is
[`qlib_static_review_2026-08-31.json`](qlib_static_review_2026-08-31.json).
The declared-dependency inventory is the generated
[CycloneDX SBOM](qlib_declared_dependencies_2026-08-31.cdx.json). Both bind
their observations to hashes of the pinned root license, `pyproject.toml`,
`setup.py`, and Dockerfile.

## Static results

| Check | Result | Interpretation |
|---|---:|---|
| Root license | MIT | A discovery result only; it is not a legal approval. |
| Bandit 1.9.4 SAST | 477 findings: 18 high, 50 medium, 409 low | High signals are present in six paths, including shell/process invocations and a weak MD5 use. They require source-level triage before any use. |
| detect-secrets 1.5.0 | 0 findings | A no-baseline, all-files scanner result; it is not proof that all historical credentials are absent. |
| CycloneDX 5.5.0 SBOM | 23 direct declared components | None is exactly pinned; no resolved transitive graph is claimed. |
| SCA vulnerability audit | Not run | No lockfile or exact declarations exist. Resolving them would require a dependency installer, outside this static-only scope. |

The 18 Bandit high-severity signals comprise B602 (5), B605 (12) and B324
(1). Eleven B605 signals are in `examples/orderbook_data/create_dataset.py`;
the other five affected paths are listed in the JSON evidence. These are tool
findings, not exploit claims, but are sufficient to keep the candidate outside
any direct runtime, dependency or benchmark path.

## Decision

`qlib` remains `DEFER_REFERENCE_ONLY`. This cycle grants no adoption, license
acceptance, dependency approval, source import, benchmark authority or live
trading authority. A future review would need an explicitly approved isolated
environment, exact dependency resolution, SCA/SBOM reconciliation, triage of
each high/medium finding, container review, malicious-package review and legal
approval before a separate fixture-only POC could be considered.
