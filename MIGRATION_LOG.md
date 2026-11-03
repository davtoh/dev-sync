# Migration & Architecture Evolution Log (`dev-sync`)

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

- [x] **Milestone 3: Unified Engine CLI & Shell Wrappers**
  - Implement `dev_sync/engine.py` (safe parameterized `rclone` subprocess calls for `diagnose`, `cleanup`, `pull`, `push`, `check`).
  - Implement `dev_sync/cli.py` and `dev_sync/__main__.py`.
  - Refactor `sync-projects.sh` and `sync-projects.ps1` as thin, transparent forwarding wrappers.
  - Full end-to-end dry-run verification against mock repository structures (`tests/test_engine.py`).
  - Verification: 14/14 automated tests passed in 0.223s. Shell wrapper execution verified.

- [x] **Milestone 4: Executable Documentation & Review Gate**
  - Update `README.md` with Pre-Run Verification commands, Scenario Combinations Matrix, and phone/Termux instructions.
  - Update `sync-targets.conf.example` with portable placeholders ($LOCAL_BASE, $HOME, environment variables).
  - Verify all automated test suites pass (14/14 unit tests passing).
  - Developer review checkpoint and commit message suggestion.

- [x] **Milestone 5: Remote Diagnostics Hardening, Bloat Root Extraction & Physical Report**
  - Resolve Google Drive duplicate directory detection in Step [1/3] (`rclone lsd` parsing with deduplication advisory for `rclone dedupe`).
  - Fix misclassification in Step [3/3] where `rclone lsf --dirs-only` printed all traversed source parent directories (`products/`, `core_libraries/`, `examples/`).
  - Implement `extract_bloat_roots` in `dev_sync/filter.py` to collapse nested directories to their top-level root bloat folder (`products/rrtools/build/`, `node_modules/`).
  - Implement untruncated physical report generation (`--report <path>` / `dev-sync-report-<target>.txt`).
  - Added unit tests for duplicate detection, bloat root extraction, and diagnostic report generation (18/18 tests passing).

- [x] **Milestone 6: Cloud Pacing, Network Shielding & Atomic Bloat Purge Engine**
  - Diagnose BSOD `0x00000133: DPC_WATCHDOG_VIOLATION` triggered by network socket floods during unthrottled `cleanup` operations on large remote targets.
  - Implement `DEFAULT_PACING_FLAGS` (`--tpslimit 10`, `--tpslimit-burst 10`, `--checkers 4`, `--transfers 4`, `--drive-pacer-min-sleep 100ms`, `--fast-list`) across all rclone subprocess calls (`cleanup`, `pull`, `push`, `check`, `diagnose`).
  - Implement two-stage safe cleanup: Stage 1 scans and purges detected bloat root folders atomically via `rclone purge` (reducing tens of thousands of API calls down to 1 call per folder), followed by Stage 2 paced `rclone delete` for remaining loose ignored files.
  - Decompose `dev_sync/engine.py` by extracting report formatting to `dev_sync/report.py` to maintain all files under the 300-line modularity budget ceiling.
  - Added unit tests for pacing flags and root directory pre-purging (19/19 tests passing).

- [x] **Milestone 7: Dual-Stream Execution Summary Engine, Root-Directory Rollup & .git Bloat Protection**
  - Diagnose terminal scrolling floods during large dry-runs where hundreds of file entries push initial output out of view.
  - Implement `dev_sync/summary.py` providing structured JSONL log parsing, root directory extraction (`extract_root_folder`), action classification (copy, overwrite, delete, error, difference), and risk assessment.
  - Add end-of-run formatted execution summary table guaranteed to stay in view at the end of every `push`, `pull`, `cleanup`, and `check` run.
  - Implement structured persistent logging to `.logs/dev-sync-<action>-<target>-<timestamp>.jsonl` with CLI overrides (`--log <path>`, `--no-summary`).
  - Add `- .git/**` to `rclone-ignore.txt` to eliminate loose git object rate-limiting and eradicate `file not in Local file system at //` errors.
  - Add comprehensive unit tests in `tests/test_summary.py` and `tests/test_engine.py` (28/28 tests passing).

- [x] **Milestone 8: Hierarchical Exclusions, Universal Copy Ignores & Cloud-Safe Semantic Policies (Tiers 1–3)**
  - Diagnose inclusion of OS duplicate folders (`products/rrtools - copia/`, `products/rrtools - copia (2)/`) consuming over 600 MiB during dry-run push.
  - Add universal OS copy wildcard patterns to `rclone-ignore.txt` (`- * - copia*/**`, `- * - copia*`, `- * - Copy*/**`, `- * - Copy*`, `- * copy*/**`, `- * copy*`).
  - Implement **Tier 1 (CLI)**: Add repeatable `--exclude <pattern>` flag across `cli.py` and `engine.py`, injected safely ahead of `--filter-from`.
  - Implement **Tier 2 (Target Configuration)**: Add auto-discovery for target-level `.syncignore` / `dev-sync-ignore.txt`, and per-target `sync-targets.conf` options (`|exclude=...;...`, `|filter=...`).
  - Implement **Tier 3 (Semantic Protection)**: Introduce `FilterRule` with intent tagging (`# @local_only`, `-! pattern`), guaranteeing that local-only archives/datasets are excluded from sync without ever being deleted from cloud storage during `cleanup`.
  - Added unit tests for copy globbing, CLI `--exclude` propagation, `.syncignore` discovery, and `@local_only` cleanup protection (35/35 tests passing).

---

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

### ADR-003: Cloud Pacing, Network Shielding & Two-Stage Bloat Purging
- **Context**: Running `rclone delete` with unconstrained concurrency across large workspaces (like 3 GB of mobile dependencies) triggers storms of thousands of rapid HTTP DELETE requests. On cloud backends with strict quotas (Google Drive 10 TPS), this causes rate-limit bursts, high socket churn, and hardware queue lockups on network adapters (such as Intel I211).
- **Decision**:
  1. Enforce universal API pacing flags (`--tpslimit 10`, `--fast-list`, `--checkers 4`, `--transfers 4`) across all operations.
  2. Implement two-stage cleanup: Stage 1 purges identified top-level bloat root folders (`.gradle/`, `build/`, `.venv/`) as directory objects in a single API call, and Stage 2 performs a paced delete pass for loose files.
- **Impact**: Eliminates socket exhaustion and driver lockups, accelerates `--dry-run` and listing operations by up to 10x–50x with `--fast-list`, and converts hours of file-by-file deletion into near-instant atomic directory purges.

### ADR-004: Dual-Stream Structured Logging, Root-Directory Rollup & Git Metadata Exclusion
- **Context**: During large sync or dry-run operations, rclone prints hundreds of file entries to stderr, pushing initial entries out of terminal buffer view. Users could not tell which project root folders were being touched or if unwanted folders were modified. Concurrently, syncing `.git/` repositories across cloud storage triggered hundreds of loose hash object errors (`ERROR : .../.git/objects/...: file not in Local file system`) and threatened cloud API TPS limits.
- **Decision**:
  1. Add `- .git/**` to `rclone-ignore.txt` so version control loose hashes are never synced over cloud drives.
  2. Implement dual-stream execution: rclone runs with `-P` to stdout (interactive progress without line floods) while logging structured JSON events to `--log-file <path> --use-json-log --log-level INFO`.
  3. Aggregate events in `dev_sync/summary.py` into a high-density, readable terminal box table ("Execution Summary") grouping files by root folder with overwrite and deletion risk alerts.
  4. Automatically preserve detailed JSONL event logs under `.logs/` for offline inspection or scripted post-processing.
- **Impact**: Terminal view is uncluttered; users instantly see which project root folders are affected; overwrite and deletion risks are highlighted before execution; and `.git` loose object errors are permanently eliminated.

### ADR-005: 3-Tier Hierarchical Exclusions & Semantic `@local_only` Protection
- **Context**: Inverted filtering previously treated all exclusions (`- pattern`) as transient build bloat to be deleted on remote storage during `cleanup`. This created an architectural hazard: developers who excluded private local archives, offline datasets, or machine-specific artifacts from being synced risked having `cleanup` purge those files from the cloud if an archive copy was previously pushed. Furthermore, users lacked ad-hoc command-line exclusion overrides and target-level `.syncignore` capabilities.
- **Decision**:
  1. Add universal OS duplicate wildcards (`* - copia*/**`, `* - Copy*/**`, `* copy*/**`) to ignore numbered copies like `folder - copia (2)`.
  2. Implement CLI repeatable `--exclude <pattern>` arguments injected ahead of `--filter-from`.
  3. Implement automatic discovery of target-level `.syncignore` / `dev-sync-ignore.txt` and per-target `sync-targets.conf` options (`|exclude=...;...`, `|filter=...`).
  4. Implement semantic intent tagging: `# @local_only` or `-! pattern`. Rules with this tag are excluded from sync, but compiled as protected (`- pattern`) in the cleanup filter, and omitted from directory purge scans.
- **Impact**: Completely eliminates accidental remote deletion of offline data; enables granular project-level ignores without polluting global rules; and empowers users to exclude ad-hoc paths directly from the CLI.
