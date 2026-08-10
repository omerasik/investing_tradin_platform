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
