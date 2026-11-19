# AGENTS.md: Development, Architecture & Engineering Standards

## 1. Project Purpose & Scope
`dev-sync` is a lightweight, cross-platform synchronization and workspace maintenance engine built to synchronize active development projects between local environments (Windows, macOS, Linux, and Android/Termux) and cloud or remote storage backends (Google Drive, AWS S3, Cloudflare R2, SFTP, or local directories) via `rclone`.

The core principles are:
- **Portability First**: Zero hardcoded local machine paths, usernames, or operating system drive letters.
- **Dynamic Inverted Filtering**: Filter rules in `rclone-ignore.txt` are dynamically compiled to drive both sync exclusions and safe remote cleanup without duplicating rule lists or risking source code deletion.
- **Zero-Dependency Core**: The core engine runs strictly on standard library Python 3 (3.10+), accompanied by thin shell wrappers (`sync-projects.sh` and `sync-projects.ps1`).

---

## 2. Directory Structure & Ownership

```text
dev-sync/
├── AGENTS.md                  # Architectural boundaries & engineering rules
├── MIGRATION_LOG.md           # Persistent task & migration tracking
├── README.md                  # User manual & Scenario Combinations Matrix
├── rclone-ignore.txt          # Canonical ignore & exclusion patterns
├── sync-targets.conf          # Active local target mappings (untracked)
├── sync-targets.conf.example  # Version-controlled portable target template
├── sync-projects.sh           # Unix / macOS / Termux shell wrapper
├── sync-projects.ps1          # Windows PowerShell wrapper
├── dev_sync/                  # Python 3 standard library core engine
│   ├── __init__.py
│   ├── __main__.py            # Entrypoint (supports 'python -m dev_sync')
│   ├── cli.py                 # Argument parsing & subcommand dispatchers
│   ├── config.py              # Target mapping & path resolver
│   ├── diff.py                # Hierarchical diff analyzer (rclone + Git + ledger)
│   ├── engine.py              # Parameterized rclone subprocess executor
│   ├── filter.py              # rclone-ignore parser & inverted cleanup compiler
│   ├── git_guard.py           # Sub-repo discovery & Git working-tree classification
│   ├── interactive.py         # Numbered folder/file tree selector & prompts
│   ├── ledger.py              # Sharded cross-machine provenance & local-prefs
│   ├── models.py              # JSON-serializable Diff/Ledger/Purge contracts
│   ├── purge.py               # Native trash, permanent purge, restore, empty-trash
│   ├── report.py              # Terminal & diagnostic reporting helpers
│   ├── summary.py             # Transfer log parser & root rollup summary
│   └── sync_hooks.py          # Pre-flight Git banner, --backup-dir & post-sync ledger
└── tests/                     # Automated unit test suite
    ├── __init__.py
    ├── test_config.py         # Path resolution & environment variable expansion
    ├── test_diff_and_purge.py # Hierarchical diff, selector, trash & restore tests
    ├── test_engine.py         # Subprocess invocation & command assembly
    ├── test_filter.py         # Filter compilation & safety invariants
    ├── test_git_guard.py      # Sub-repo & working-tree classification tests
    ├── test_ledger.py         # Sharded machine snapshot & local-prefs tests
    ├── test_models.py         # JSON contract round-trip tests
    └── test_summary.py        # Log parsing & directory rollup tests
```

---

## 3. Engineering Invariants & Coding Rules

1. **Zero Machine-Specific Paths**:
   - Never commit or document absolute paths like `C:\...`, `/Users/...`, or `/home/user/...`.
   - Use `$LOCAL_BASE` (parent directory of `dev-sync` or `$DEV_SYNC_BASE` environment variable), `$HOME` (or `%USERPROFILE%`), or repository-relative paths (`./projects`).
2. **Dynamic Inverted Filter Invariant**:
   - `cleanup` must NEVER hardcode patterns.
   - It must compile an inverted filter from `rclone-ignore.txt`:
     - Lines starting with `+` (keep) become `-` (protect from delete).
     - Lines starting with `-` (ignore) become `+` (mark for delete).
     - Ending with `- **` to protect all other project files.
   - In rclone glob syntax, unanchored directory patterns should use `dir/**` or `{**/,}dir/**` so they match at both root and nested levels.
3. **Subprocess & Execution Security**:
   - Never use `shell=True` in Python or string interpolation in shell scripts. Pass arguments as sanitized argument arrays to prevent command injection.
4. **Standard Library Only**:
   - Do not add third-party Python dependencies (like `requests` or `click`) to the core sync engine. Keep it pure standard library so it executes cleanly on phones (Termux), CI runners, and minimal containers.
5. **Git Guard, Sharded Provenance & Filter Identity**:
   - Preserve `has_target_custom=False` in `resolve_target_filters` when injecting built-in `@local_only` guards (`.dev-sync-trash/**`, `.dev-sync/trash/**`, `.dev-sync/local-prefs.json`) so default global filter identity stays intact.
   - Keep `.dev-sync` and `.dev-sync-trash` in `git_guard._PRUNE_DIRS`, and guard `PurgeExecutor._restore_local` against silently overwriting an existing local file.

---

## 4. Verification Commands

Run these automated validation commands before committing changes:

```bash
# Run the complete test suite
python -m unittest discover -s tests -v

# Run Python syntax and style check
python -m compileall dev_sync tests

# Verify filter compilation directly
python -m dev_sync --test-filter

# Test dry-run diagnosis on all targets
python -m dev_sync diagnose all --dry-run
```
