# dev-sync: Multi-Platform Workspace Sync via Rclone

A lightweight, portable synchronization and maintenance tool designed to keep active development workspaces in sync across multiple operating systems (Windows, macOS, Linux) via Google Drive, while rigorously excluding build caches, virtual environments, dependencies, and OS metadata.

---

## 1. Directory Structure

```text
dev/
├── dev-sync/                     # This Git repository
│   ├── .gitignore
│   ├── README.md
│   ├── rclone-ignore.txt         # Global exclusion & filter rules
│   ├── sync-targets.conf         # Active target mapping (ignored by Git)
│   ├── sync-targets.conf.example # Version-controlled template
│   ├── sync-projects.ps1         # Windows PowerShell runner
│   └── sync-projects.sh          # macOS / Linux Bash runner
├── projects/                     # Local workspaces
└── phone_projects/
```

---

## 2. Configuration (`sync-targets.conf`)

Target paths are defined with the `$LOCAL_BASE` variable (which resolves dynamically to the parent folder `dev/` on any OS):

```ini
projects=$LOCAL_BASE/projects|gdrive:projects
phone_projects=$LOCAL_BASE/phone_projects|gdrive:phone_projects
```

---

## 3. Pre-Run Verification & Diagnostics

Before executing sync or cleanup actions, run diagnostics to check connection and view remote build artifacts:

### Windows (PowerShell):
```powershell
.\sync-projects.ps1 -Action diagnose -Target projects
```

### macOS / Linux (Bash):
```bash
chmod +x ./sync-projects.sh
./sync-projects.sh diagnose projects
```

---

## 4. Scenario Combinations Matrix

| Scenario / Objective | Target | Options | Windows (PowerShell) | macOS / Linux (Bash) |
| :--- | :--- | :--- | :--- | :--- |
| **Inspect Remote Bloat** | `projects` | Read-only scan | `.\sync-projects.ps1 diagnose projects` | `./sync-projects.sh diagnose projects` |
| **Simulate Cleanup** | `phone_projects` | `--dry-run` | `.\sync-projects.ps1 cleanup phone_projects -DryRun` | `./sync-projects.sh cleanup phone_projects --dry-run` |
| **Purge Remote Bloat** | `all` | Live remote delete | `.\sync-projects.ps1 cleanup all` | `./sync-projects.sh cleanup all` |
| **Pull New Changes** | `projects` | Non-destructive copy | `.\sync-projects.ps1 pull projects` | `./sync-projects.sh pull projects` |
| **Pull with Dry Run** | `all` | Preview download | `.\sync-projects.ps1 pull all -DryRun` | `./sync-projects.sh pull all --dry-run` |
| **Push Local Changes** | `projects` | Non-destructive copy | `.\sync-projects.ps1 push projects` | `./sync-projects.sh push projects` |
| **Verify Sync Diff** | `projects` | Integrity comparison | `.\sync-projects.ps1 check projects` | `./sync-projects.sh check projects` |

---

## 5. Freeing Local Google Drive Disk Space

If Google Drive for Desktop (`G:\`) is consuming local storage, it is likely set to **Mirror files** or has cached offline data:

1. Click the **Google Drive icon** in your taskbar / system tray.
2. Click the **Gear icon (Settings)** → **Preferences**.
3. Under **Google Drive folders**:
   - Change from **Mirror files** to **Stream files**.
   - With "Stream files", files stay in the cloud and are downloaded only on demand.
4. If a folder was manually pinned:
   - In File Explorer, right-click `G:\Mi unidad\projects` (or any heavy folder) → **Offline access** → choose **Online only**.
5. Clear cache:
   - In Drive Preferences → **Settings (Gear)**, check the **Local cached files directory** size and clear or limit the cache size.
