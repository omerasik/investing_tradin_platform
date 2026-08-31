# Upstream Repository Catalog

All clones are isolated in `C:\Users\omerf\upstream-research\repositories`, outside the product repository, and are never imported by this application. No upstream install script, notebook, container, Compose file or binary has been executed.

| Repository | Commit observed | Static role | Audit state |
|---|---|---|---|
| investing-algorithm-framework | `63483bdaa2a0defb644b6851407529d8ee6c63d5` | research workflow and reports | inspected |
| backtesting.py | `9c2b8e1c99f3a860883222ba8d6d5b5cac3a932d` | baseline backtest reference | inspected |
| claude-trading-skills | `62a16359964b80de118716b0b8cf27520d4e6dfe` | workflow/skill registry reference | inspected |
| TradingAgents | `a33fd4c0f134485a43553a2c23a63cb14adbd88f` | agent orchestration reference | inspected |
| Vibe-Trading | `261f007c410f7a6ff015a17f6830c8f809cd7413` | research workspace reference | inspected |
| FinceptTerminal | `823f63848084f3869e4c9a487663f41f44d55989` | terminal UX reference | inspected |
| machine-learning-for-trading | `bb53e24b45a95de6e9106124d0909144bb7fcc46` | research methodology reference | partial Windows checkout; MIT license recovered from pinned Git object; reference only |
| ai-berkshire | `66e556262d6486a9819286252e5c9f90a4cfa386` | investment thesis reference | inspected |
| Lean | `96030d331718af7b7eeb0e4f9ddd5e49bf87e7ce` | event model reference | inspected |
| nautilus_trader | `a930c8afe380025fc0a10c6b2cd6907d6b983e86` | high-fidelity simulator reference | inspected |
| qlib | `79633dd9506ea689e5400dea0197717b5b3d74b7` | ML research reference | restricted static review complete; `DEFER_REFERENCE_ONLY` |
| FinRL-Trading | `e65d6f0483ead7d2ef4a5fc940cdf960392a25c1` | weight-contract research reference | inspected |
| OpenBB | `3e071fcc2cd9f891cac6040ae60296dba76dab46` | data-adapter reference | inspected |
| freqtrade | `b084fb206cf4bbe2d6e4eae49f79f2fa09e658ae` | crypto paper-operation reference | inspected |
| vectorbt | `f9897528f675114e6b34790178dbb2ca137acb51` | vectorized research reference | inspected |
| FinRobot | `01ed408326f1d4ec2460596dee10858faf0f69af` | report-generation reference | inspected |

Exact commits are shallow-clone heads at audit time. Re-audit a pinned commit before every future use.

## 2026-08-31 metadata refresh

[`IDENTITY_ACTIVITY_REFRESH_2026-08-31.md`](IDENTITY_ACTIVITY_REFRESH_2026-08-31.md)
records a non-executing local-Git/remote-head/GitHub-metadata refresh for every
catalogue entry. It retains the original pins: 12 recorded branches have
advanced remotely, four remain current, 15 isolated worktrees are clean, and
the previously documented partial Windows checkout remains reference-only.
Neither remote advancement nor GitHub license metadata changes the preliminary
root-license classifications or any adoption decision.
