# Changelog

## [2026-11-19] feat: Git Guard & Sharded Provenance Ledger Engine
- **Type**: feat
- **Summary**: Added git_guard.py for sub-repo/working-tree classification, ledger.py for sharded cross-machine provenance & local opt-out prefs, and built-in @local_only guards for trash/prefs.
- **Files** (7):
  - `dev_sync/filter.py`
  - `rclone-ignore.txt`
  - `tests/test_filter.py`
  - `dev_sync/git_guard.py`
  - `dev_sync/ledger.py`
  - `tests/test_git_guard.py`
  - `tests/test_ledger.py`
- **Verification**: python -m unittest discover -s tests -v

## [2026-11-19] feat: Milestone 1: Modularity Decomposition, Summary Fixes & Domain Contracts
- **Type**: feat
- **Summary**: Added headless JSON models in dev_sync/models.py, fixed directory modtime and category rollup in summary.py, and decomposed engine/cli/config/filter/report/tests below modularity budgets.
- **Files** (14):
  - `.TODO/smart_diff_ledger_purge_plan.md`
  - `MIGRATION_LOG.md`
  - `dev_sync/cli.py`
  - `dev_sync/config.py`
  - `dev_sync/engine.py`
  - `dev_sync/filter.py`
  - `dev_sync/report.py`
  - `dev_sync/summary.py`
  - ... (+6 more)
- **Verification**: python -m unittest discover -s tests -v

## Project Context
- **Objective**: Modernize `dev-sync` to support portable multi-target mappings (`target_name`, `raw_local`, `remote_path`), dynamic inverted filter compilation from `rclone-ignore.txt`, zero machine-specific pathing, and cross-platform execution (Windows, macOS, Linux, Android/Termux, future Flutter UI).
- **Core Standard**: Strict compliance with `modernization_rules.md`, `security_and_standards.md`, and `executable_documentation.md`.

---

## Active Milestones

- [x] **Milestone 1: Dynamic Inverted-Filter Engine & rclone-ignore Optimization**
  - Fix root-level globbing issues in `rclone-ignore.txt` (`dir/**` instead of `**/dir/**`).
  - Implement `dev_sync/filter.py` with dynamic inverted filter compilation (`+ -> -`, `- -> +`, final `- **`).
  - Unit tests verifying filter inversion and empirical rclone deletion safety (`tests/test_filter.py`).
  - Verification: 4/4 automated tests passed in 0.205s. Tested against mock project filesystem.

- [x] **Milestone 2: Portable Target Configuration & Path Resolver**
  - Implement `dev_sync/config.py` parsing `sync-targets.conf` (`target_name=raw_local|remote_path`).
  - Support dynamic resolution of `$LOCAL_BASE` (parent of repo or `$DEV_SYNC_BASE`), `$HOME` (`%USERPROFILE%`), and system environment variables.
  - Support ad-hoc CLI targets without requiring configuration file edits.
  - Unit tests verifying path normalization and environment expansion across operating systems (`tests/test_config.py`).
  - Verification: 5/5 automated tests passed in 0.030s.

## Architectural Decision Records (ADRs)

### ADR-001: Standard-Library Python 3 Engine with Shell Runners
- **Context**: Maintaining duplicate complex logic across Bash and PowerShell led to path discrepancies, quoting errors, and hardcoded cleanup rules.
- **Decision**: Centralize all filter compilation, configuration parsing, and subprocess orchestration into a zero-dependency Python 3 (3.10+) package (`dev_sync`), while keeping `sync-projects.sh` and `sync-projects.ps1` as thin wrappers.
- **Impact**: Guarantees 100% behavioral parity across Windows, macOS, Linux, and Android/Termux; eliminates cross-shell escaping divergence; prepares clean backend integration for future Flutter apps.

### ADR-002: Dynamic Inverted Filtering for Safe Remote Cleanup
- **Context**: Passing `rclone-ignore.txt` directly to `rclone delete` would delete wanted source code, which prompted a previous brittle workaround of hardcoded `--include` flags.
- **Decision**: Dynamically compile `rclone-ignore.txt` into an inverted cleanup filter at runtime:
  - `+ pattern` (preserved during sync) $\to$ `- pattern` (protected during cleanup).
  - `- pattern` (excluded during sync) $\to$ `+ pattern` (marked for deletion during cleanup).
  - Terminal rule: `- **` (protects all other source files).
- **Impact**: Completely eliminates manual synchronization of ignore rules; enables `diagnose` to calculate accurate remote bloat metrics via `rclone size`.
