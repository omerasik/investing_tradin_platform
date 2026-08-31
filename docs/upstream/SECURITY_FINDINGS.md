# Security Findings

## Scope and method

Static, non-executing review only: root license files, manifest/Docker/Compose/automation inventory, source-tree mapping, and commit capture. No installation, scripts, notebooks, containers, downloaded artifacts, MCP/browser tools or credentials were run.

## Findings

| ID | Finding | Impact | Disposition |
|---|---|---|---|
| US-01 | Every candidate is untrusted third-party code with package manifests and/or automation. | Supply-chain/code-execution risk. | Isolate; pin SHA; scan SBOM/dependencies before any use. |
| US-02 | FinceptTerminal, Freqtrade, Lean, NautilusTrader, Qlib, TradingAgents, Vibe-Trading, FinRL and FinRobot include Docker/Compose and/or scripts. | Host/container and network risk if run. | Do not execute outside restricted disposable environment. |
| US-03 | 16 repositories expose 0-61 recognized manifests; NautilusTrader and OpenBB have especially large dependency surfaces. | Vulnerability and operational complexity. | No direct dependency approved. |
| US-04 | `machine-learning-for-trading` checkout is incomplete on Windows (1,579 deleted/index entries after clone), but its pinned Git object was inspected to recover the MIT root license. | Working-tree coverage remains incomplete. | Reference only; do not run it. |
| US-05 | AGPL/GPL/Commons-Clause candidates create license-contamination risk. | Legal/security governance risk. | Do not copy code or package them into this repository. |
| US-06 | Static manifest inventory found 202 execution-sensitive source-file indicators and 1,705 secret-named-file indicators across the isolated clones. | Broad third-party attack surface; hits are triage signals, not confirmed flaws. | `STATIC_MANIFEST_INVENTORY.md`; no direct adoption. |

Required before any candidate changes state: secret scan, SCA vulnerability scan, SBOM generation, SAST, container scan if applicable, typosquat review, credential/file/subprocess/dynamic-evaluation review, data-leakage review, and restricted sandbox benchmark.

## 2026-08-31 identity refresh

`IDENTITY_ACTIVITY_REFRESH_2026-08-31.md` shows that 12 recorded remote branches
now differ from their local audited pins. This is a supply-chain change signal,
not an approval or a vulnerability finding. The pins were not changed, fetched
or executed; each advanced commit requires a complete new RQ-029 review before
it can be considered.

## 2026-08-31 qlib restricted static review

| ID | Finding | Impact | Disposition |
|---|---|---|---|
| US-07 | The current pinned qlib clone has 18 high, 50 medium and 409 low Bandit findings across 333 Python files; high signals include shell/process invocation and weak MD5 usage. | Unresolved source-level process/input and security-design risk. | `DEFER_REFERENCE_ONLY`; no source, package, benchmark or execution approval. |
| US-08 | Qlib declares 23 direct production dependencies but none is exact-pinned; no lockfile/resolved transitive graph was supplied. | A deterministic vulnerability/SCA claim cannot be made from the static manifest. | Record the direct-declaration CycloneDX inventory only; do not resolve/install dependencies in this review. |
| US-09 | An all-files no-baseline detect-secrets scan found zero findings. | This narrows one automated signal but cannot establish historical credential absence. | Retain the evidence hash; do not treat it as an adoption clearance. |
| US-10 | The pinned FinRL-Trading clone's Bandit scan cannot decode `src/trading/alpaca_manager.py` as UTF-8; its partial result has 16 medium and 25 low findings. | Full-source SAST coverage is not established, and the repository also declares a live-trading command. | `DEFER_REFERENCE_ONLY`; do not transcode, execute, import or install the candidate to work around the static-review gap. |
| US-11 | The pinned NautilusTrader clone has 83 medium and 25,670 low Bandit findings across 370,358 scanned lines; its all-files secret scan is zero-finding. | Source-level SAST triage remains incomplete and a zero secret result is not a security clearance. | `DEFER_REFERENCE_ONLY`; require legal/SCA review before any use. |
| US-12 | The pinned VectorBT clone has 12 medium and 1,876 low Bandit findings across 91,606 scanned lines; its all-files secret scan is zero-finding. | SAST findings are untriaged and the Commons Clause licensing condition needs legal review. | `DEFER_REFERENCE_ONLY`; no source, package, benchmark or execution approval. |
| US-13 | The pinned backtesting.py clone has 113 low Bandit findings across 4,585 scanned lines, all B101 assertions; its all-files secret scan is zero-finding and its three production declarations are not exact-pinned. | SAST signals remain untriaged, dependency resolution was not performed, and AGPL-3.0-or-later requires legal review. | `DEFER_REFERENCE_ONLY`; no source, package, benchmark or execution approval. |

See [`QLIB_STATIC_REVIEW_2026-08-31.md`](QLIB_STATIC_REVIEW_2026-08-31.md) for
the immutable input hashes, exact tool versions and restriction boundary.
