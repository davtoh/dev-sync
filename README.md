# dev-sync: Universal Multi-Target Workspace Sync via Rclone

A lightweight, portable synchronization and workspace maintenance engine designed to keep active development projects synchronized across multiple machines (Windows, macOS, Linux, and Android/Termux) and cloud backends (Google Drive, AWS S3, Cloudflare R2, SFTP, etc.) via `rclone`.

Build artifacts, virtual environments, node dependencies, IDE caches, and secret keys are rigorously excluded during sync. A dynamic inverted-filter engine ensures that remote bloat can be safely cleaned up without maintaining duplicate rules or risking source code deletion.

---

## 1. Directory Structure

```text
dev-sync/
├── .gitignore
├── AGENTS.md                  # Development invariants & engineering standards
├── MIGRATION_LOG.md           # Persistent migration and task log
├── README.md                  # Executable manual & scenario matrix
├── rclone-ignore.txt          # Canonical ignore & exclusion patterns
├── sync-targets.conf          # Active local target mappings (untracked)
├── sync-targets.conf.example  # Version-controlled portable template
├── sync-projects.ps1          # Windows PowerShell runner
├── sync-projects.sh           # macOS / Linux / Android (Termux) Bash runner
├── dev_sync/                  # Python 3 standard library core engine
│   ├── __init__.py
│   ├── __main__.py            # CLI entrypoint ('python -m dev_sync')
│   ├── cli.py                 # Argument parser & feedback formatter
│   ├── config.py              # Portable path resolver ($LOCAL_BASE, $HOME)
│   ├── engine.py              # Parameterized rclone subprocess runner
│   └── filter.py              # Inverted cleanup filter compiler
└── tests/                     # Automated unit test suite
    ├── test_config.py
    ├── test_engine.py
    └── test_filter.py
```

---

## 2. Pre-Run Verification One-Liners

Before executing sync or cleanup actions, run these quick diagnostic commands to verify environment readiness:

```bash
# 1. Run the automated test suite (must pass 100%)
python -m unittest discover -s tests -v

# 2. Check rclone installation and version
rclone version

# 3. Verify target configuration and resolved local paths
python -m dev_sync --list-targets

# 4. Preview the compiled inverted cleanup filter derived from rclone-ignore.txt
python -m dev_sync --test-filter
```

> [!NOTE]
> If Google Drive returns `invalid_grant: maybe token expired?`, refresh the OAuth token anytime by running:
> ```bash
> rclone config reconnect gdrive:
> ```

---

## 3. Configuration (`sync-targets.conf`)

Target paths are defined with portable variables in `sync-targets.conf`:

```ini
# Format: <target_name>=<raw_local>|<remote_path>
# Variables available:
#   $LOCAL_BASE : Parent directory of dev-sync (or $DEV_SYNC_BASE environment variable)
#   $HOME       : User home directory (~/ on Unix, %USERPROFILE% on Windows)
#   $ANY_VAR    : Any active environment variable

projects=$LOCAL_BASE/projects|gdrive:projects
phone_projects=$LOCAL_BASE/phone_projects|gdrive:phone_projects
```

---

## 4. Scenario Combinations Matrix

| Scenario / Objective | Target | Mode / Flags | Windows (PowerShell) | macOS / Linux / Termux (Bash) | Direct Python |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **List Configured Targets** | - | Read-only | `.\sync-projects.ps1 --list-targets` | `./sync-projects.sh --list-targets` | `python -m dev_sync --list-targets` |
| **Inspect Remote Bloat** | `projects` | Read-only scan | `.\sync-projects.ps1 diagnose projects` | `./sync-projects.sh diagnose projects` | `python -m dev_sync diagnose projects` |
| **Save Full Diagnostic Report** | `projects` | `--report <file>` | `.\sync-projects.ps1 diagnose projects --report report.txt` | `./sync-projects.sh diagnose projects --report report.txt` | `python -m dev_sync diagnose projects --report report.txt` |
| **Consolidate Remote Duplicates** | `projects` | Google Drive dedupe | `rclone dedupe gdrive:projects` | `rclone dedupe gdrive:projects` | `rclone dedupe gdrive:projects` |
| **Simulate Cleanup** | `phone_projects` | `--dry-run` | `.\sync-projects.ps1 cleanup phone_projects -DryRun` | `./sync-projects.sh cleanup phone_projects --dry-run` | `python -m dev_sync cleanup phone_projects --dry-run` |
| **Purge Remote Bloat** | `all` | Live remote delete | `.\sync-projects.ps1 cleanup all` | `./sync-projects.sh cleanup all` | `python -m dev_sync cleanup all` |
| **Pull Remote Changes** | `projects` | Copy remote to local | `.\sync-projects.ps1 pull projects` | `./sync-projects.sh pull projects` | `python -m dev_sync pull projects` |
| **Pull Preview** | `all` | `--dry-run` | `.\sync-projects.ps1 pull all -DryRun` | `./sync-projects.sh pull all --dry-run` | `python -m dev_sync pull all --dry-run` |
| **Push Local Changes** | `projects` | Copy local to remote | `.\sync-projects.ps1 push projects` | `./sync-projects.sh push projects` | `python -m dev_sync push projects` |
| **Compare Differences** | `projects` | Check hashes/size | `.\sync-projects.ps1 check projects` | `./sync-projects.sh check projects` | `python -m dev_sync check projects` |
| **Ad-Hoc Custom Sync** | `custom` | Direct parameters | `.\sync-projects.ps1 push app $HOME/app gdrive:app -DryRun` | `./sync-projects.sh push app "$HOME/app" "gdrive:app" --dry-run` | `python -m dev_sync push app "$HOME/app" "gdrive:app" --dry-run` |

---

## 5. Mobile & Android (Termux) Setup

Because the core engine is pure standard-library Python 3, `dev-sync` runs natively on mobile devices via Termux:

1. Install prerequisites in Termux:
   ```bash
   pkg update
   pkg install python rclone git
   ```
2. Clone your repository:
   ```bash
   git clone <your-repo-url> dev-sync
   cd dev-sync
   ```
3. Set your target mappings in `sync-targets.conf` (e.g., pointing to internal storage via `termux-setup-storage`).
4. Run standard commands:
   ```bash
   chmod +x ./sync-projects.sh
   ./sync-projects.sh pull phone_projects
   ```

---

## 6. How Dynamic Inverted Filtering Protects Your Code

Standard ignore files (`rclone-ignore.txt`) tell `rclone` which files **not to transfer** during `push` and `pull`:
- `- node_modules/**` (exclude from transfer)
- `+ .env.example` (preserve)

If you ran `rclone delete` with this file directly, rclone would delete your source code and spare only the excluded files.

`dev-sync` solves this by dynamically compiling an inverted filter at runtime:
1. Every `+ pattern` (e.g. `.env.example`) becomes `- pattern` (**protected from deletion**).
2. Every `- pattern` (e.g. `node_modules/**`, `.venv/**`) becomes `+ pattern` (**targeted for deletion**).
3. The engine automatically appends a terminal guard: `- **` (**protects all remaining source files**).

This makes remote cleanup 100% automated, synchronized with `rclone-ignore.txt`, and mathematically immune to accidental source code deletion.

---

## 7. Freeing Local Google Drive Disk Space

If Google Drive for Desktop is consuming excessive local disk storage:

1. **Switch from Mirror to Stream:**
   - Click the **Google Drive icon** in your taskbar / system tray.
   - Click **Settings (Gear)** → **Preferences**.
   - Under **Google Drive folders**, select **Stream files** instead of **Mirror files**.
2. **Mark Heavy Folders Online-Only:**
   - In File Explorer / Finder, right-click your projects folder inside Google Drive → **Offline access** → choose **Online only**.
3. **Purge Cache:**
   - In Google Drive Preferences → **Settings (Gear)**, locate **Local cached files directory** and purge or limit the cache size.
