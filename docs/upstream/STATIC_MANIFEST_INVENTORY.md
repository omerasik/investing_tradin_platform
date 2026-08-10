# Static Manifest and Automation Inventory

Static-only inventory captured on 2026-07-30 from the pinned clones in `C:\Users\omerf\upstream-research\repositories`. It used file-name discovery and text pattern matching only; no dependency installer, build, container, notebook, package script, test suite or application code was executed.

| Repository | Recognized dependency/container manifests | Automation/script files | Disposition |
|---|---:|---:|---|
| ai-berkshire | 0 | 7 | Reference only; inspect scripts before any future use. |
| backtesting.py | 2 | 1 | Reference only; AGPL. |
| claude-trading-skills | 3 | 3 | Reference only. |
| FinceptTerminal | 3 | 6 | Design reference only; AGPL. |
| FinRL-Trading | 3 | 1 | Reference only. |
| FinRobot | 4 | 2 | Reference only. |
| freqtrade | 20 | 5 | Reference only; GPL. |
| investing-algorithm-framework | 19 | 1 | Clean-room concepts only. |
| Lean | 27 | 4 | Deferred separate-service evaluation. |
| machine-learning-for-trading | 0 | 0 | Partial Windows checkout; Git-object license audit completed. |
| nautilus_trader | 53 | 58 | Deferred isolated evaluation; large surface. |
| OpenBB | 115 | 3 | Reference only; AGPL; largest observed manifest surface. |
| qlib | 39 | 7 | Reference only pending legal/security review. |
| TradingAgents | 4 | 0 | Reference only. |
| vectorbt | 7 | 0 | Rejected as runtime dependency; Commons Clause. |
| Vibe-Trading | 7 | 1 | Reference only. |

The static search found 202 source files matching one or more execution-sensitive indicators (`eval`, `exec`, subprocess/process launch, shell execution, or equivalent), and 1,705 files containing secret-related identifier text. These are triage indicators, not vulnerability findings: names may appear in tests, documentation, configuration or safe wrappers. They confirm that every clone requires a focused SAST/SCA/secret review in a restricted environment before any runtime use.

No dependency is adopted by this product. Therefore no upstream SBOM is attributed to the product; a complete tool-generated SBOM, vulnerability scan, secret scan and container scan remain mandatory gates for any future proposed runtime adoption.
