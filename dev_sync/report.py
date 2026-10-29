"""Diagnostic report formatting and advisory utilities for dev_sync."""

from __future__ import annotations

import datetime
from collections import Counter
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from dev_sync.config import SyncTarget


def parse_lsd_duplicates(output: str) -> dict[str, int]:
    """Parse rclone lsd output and detect duplicate directory names.

    Google Drive and certain cloud backends allow multiple folder objects
    with the exact same name under the same parent directory.
    """
    folder_names: List[str] = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=4)
        if len(parts) >= 5:
            folder_names.append(parts[4])
    counts = Counter(folder_names)
    return {name: c for name, c in counts.items() if c > 1}


def format_diagnostic_report(
    target: SyncTarget,
    lsd_output: str,
    duplicates: dict[str, int],
    size_output: str,
    bloat_roots: List[str],
) -> str:
    """Construct an untruncated, structured diagnostic report."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "=" * 80,
        "dev-sync Diagnostic & Bloat Audit Report",
        "=" * 80,
        f"Target           : {target.name}",
        f"Local Path       : {target.local_path}",
        f"Remote Path      : {target.remote_path}",
        f"Timestamp        : {timestamp}",
        "",
        "-" * 80,
        "[1/3] Remote Connectivity & Folder Structure (max-depth 2)",
        "-" * 80,
        lsd_output.strip() if lsd_output.strip() else "(No directories listed or remote empty)",
    ]

    if duplicates:
        lines.extend([
            "",
            "ADVISORY: Duplicate Folder Entities Detected on Remote Storage!",
            "Providers such as Google Drive permit multiple folders with identical names",
            "under the same parent. The following duplicate names were found:",
        ])
        for name, count in duplicates.items():
            lines.append(f"  - '{name}' ({count} occurrences)")
        lines.extend([
            "",
            "Remediation:",
            f"  Run 'rclone dedupe {target.remote_path}' to inspect and merge duplicate folders.",
        ])

    lines.extend([
        "",
        "-" * 80,
        "[2/3] Remote Bloat Size & Object Metrics",
        "-" * 80,
        size_output.strip() if size_output.strip() else "(No size output)",
        "",
        "-" * 80,
        f"[3/3] Detected Remote Bloat Directories ({len(bloat_roots)} Unique Root Locations)",
        "-" * 80,
    ])

    if bloat_roots:
        lines.append("The following root directories match rclone-ignore rules and can be safely cleaned:")
        for b in bloat_roots:
            lines.append(f"  - {b}")
    else:
        lines.append("No ignored bloat directories detected on remote. Remote is clean!")

    lines.extend([
        "",
        "-" * 80,
        "Remediation & Execution Commands",
        "-" * 80,
        "1. Preview remote bloat deletion without deleting source code:",
        f"   python -m dev_sync cleanup {target.name} --dry-run",
        "",
        "2. Execute remote bloat deletion:",
        f"   python -m dev_sync cleanup {target.name}",
    ])

    if duplicates:
        lines.extend([
            "",
            "3. Consolidate duplicate remote folders (Google Drive dedupe):",
            f"   rclone dedupe {target.remote_path}",
        ])

    lines.extend(["=" * 80, ""])
    return "\n".join(lines)

