"""Execution event tracking, summary aggregation, and risk warning engine."""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Known common monorepo category prefixes where project roots are 2 levels deep
CONTAINER_ROOT_PREFIXES = {
    "products",
    "packages",
    "apps",
    "services",
    "projects",
    "modules",
    "core",
    "tools",
    "libraries",
}


@dataclass
class PathEvent:
    """Represents a single file or directory event captured from rclone logs."""

    path: str
    root_folder: str
    action: str  # copy, overwrite, delete, difference, missing_local, missing_remote, error
    size: int = 0
    level: str = "notice"  # notice, info, warning, error
    message: str = ""


@dataclass
class RootFolderStats:
    """Aggregated statistics for a specific root directory."""

    root_folder: str
    total_files: int = 0
    total_bytes: int = 0
    copied_count: int = 0
    overwritten_count: int = 0
    deleted_count: int = 0
    error_count: int = 0
    diff_count: int = 0


@dataclass
class ExecutionSummary:
    """Complete summary of an executed dev_sync operation."""

    target_name: str
    action: str
    dry_run: bool
    total_files: int = 0
    total_bytes: int = 0
    copied_count: int = 0
    overwritten_count: int = 0
    deleted_count: int = 0
    error_count: int = 0
    diff_count: int = 0
    root_stats: Dict[str, RootFolderStats] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    log_file_path: Optional[Path] = None


def format_bytes(num_bytes: int) -> str:
    """Format byte counts into human-readable strings."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    for unit in ["KiB", "MiB", "GiB", "TiB"]:
        num_bytes_f = num_bytes / 1024.0
        if num_bytes_f < 1024.0 or unit == "TiB":
            return f"{num_bytes_f:.1f} {unit}"
        num_bytes = int(num_bytes_f)
    return f"{num_bytes} B"


def extract_root_folder(object_path: str) -> str:
    """Extract the logical project root directory from a relative file path.

    Collapses deeply nested files to their logical top-level project component:
    - 'products/rrtools/src/index.js' -> 'products/rrtools/'
    - 'backend/routes/api.py'         -> 'backend/'
    - 'README.md'                     -> '(root)/'
    """
    clean = object_path.strip().replace("\\", "/").lstrip("/")
    if not clean:
        return "(root)/"

    parts = [p for p in clean.split("/") if p]
    if len(parts) <= 1:
        return "(root)/"

    if len(parts) >= 3 and parts[0].lower() in CONTAINER_ROOT_PREFIXES:
        return f"{parts[0]}/{parts[1]}/"

    return f"{parts[0]}/"


def _classify_event(level: str, msg: str, skipped: Optional[str] = None) -> str:
    """Classify the action category from rclone log attributes."""
    msg_lower = msg.lower()

    if level == "error":
        if "file not in" in msg_lower and "/dst" in msg_lower:
            return "missing_remote"
        if "file not in" in msg_lower and "/src" in msg_lower:
            return "missing_local"
        if "sizes differ" in msg_lower or "hash differ" in msg_lower:
            return "difference"
        return "error"

    if skipped == "delete" or "deleted" in msg_lower:
        return "delete"

    if "replace" in msg_lower or "overwrite" in msg_lower:
        return "overwrite"

    if skipped == "copy" or "copied" in msg_lower:
        return "copy"

    return "copy"


def parse_log_events(log_path: Path) -> List[PathEvent]:
    """Parse structured JSON lines or fallback text log output into PathEvents."""
    if not log_path.exists():
        return []

    events: List[PathEvent] = []
    text_regex = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} (NOTICE|ERROR|WARNING|INFO)\s*:\s*(?:([^:]+):\s*)?(.*)$")

    for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                obj = data.get("object")
                if not obj or not isinstance(obj, str):
                    continue
                # Skip rclone Fs-level summary lines like 'Local file system at ...'
                if "file system at" in obj or "root '" in obj:
                    continue

                level = str(data.get("level", "notice")).lower()
                msg = str(data.get("msg", ""))
                skipped = data.get("skipped")
                size = int(data.get("size", 0)) if str(data.get("size", "0")).isdigit() else 0
                action = _classify_event(level, msg, skipped)

                events.append(
                    PathEvent(
                        path=obj,
                        root_folder=extract_root_folder(obj),
                        action=action,
                        size=size,
                        level=level,
                        message=msg,
                    )
                )
                continue
            except json.JSONDecodeError:
                pass

        # Text line fallback
        match = text_regex.match(line)
        if match:
            level = match.group(1).lower()
            obj = match.group(2)
            msg = match.group(3)
            if obj and not ("file system at" in obj or "root '" in obj):
                action = _classify_event(level, msg)
                events.append(
                    PathEvent(
                        path=obj.strip(),
                        root_folder=extract_root_folder(obj.strip()),
                        action=action,
                        size=0,
                        level=level,
                        message=msg,
                    )
                )

    return events


def build_summary(
    target_name: str,
    action: str,
    dry_run: bool,
    log_path: Optional[Path] = None,
    events: Optional[List[PathEvent]] = None,
) -> ExecutionSummary:
    """Aggregate parsed events into an ExecutionSummary with safety warnings."""
    if events is None:
        events = parse_log_events(log_path) if log_path else []

    summary = ExecutionSummary(
        target_name=target_name,
        action=action,
        dry_run=dry_run,
        log_file_path=log_path,
    )

    for ev in events:
        summary.total_files += 1
        summary.total_bytes += ev.size

        if ev.root_folder not in summary.root_stats:
            summary.root_stats[ev.root_folder] = RootFolderStats(root_folder=ev.root_folder)

        rf = summary.root_stats[ev.root_folder]
        rf.total_files += 1
        rf.total_bytes += ev.size

        if ev.action == "copy":
            summary.copied_count += 1
            rf.copied_count += 1
        elif ev.action == "overwrite":
            summary.overwritten_count += 1
            rf.overwritten_count += 1
        elif ev.action == "delete":
            summary.deleted_count += 1
            rf.deleted_count += 1
        elif ev.action in ("difference", "missing_local", "missing_remote"):
            summary.diff_count += 1
            rf.diff_count += 1
        elif ev.action == "error":
            summary.error_count += 1
            rf.error_count += 1

    # Safety & Risk Assessment
    if summary.overwritten_count > 0:
        summary.warnings.append(
            f"ADVISORY: {summary.overwritten_count} file(s) will OVERWRITE existing files on destination!"
        )

    if summary.deleted_count > 0:
        label = "targeted for DELETION" if dry_run else "PERMANENTLY DELETED"
        summary.warnings.append(
            f"RISK: {summary.deleted_count} file(s) {label} from storage!"
        )

    if summary.error_count > 0:
        summary.warnings.append(
            f"ATTENTION: {summary.error_count} error(s) occurred during execution."
        )

    if summary.diff_count > 0 and action == "check":
        summary.warnings.append(
            f"CHECK: {summary.diff_count} synchronization difference(s) detected between local and remote."
        )

    return summary


def format_terminal_summary(summary: ExecutionSummary) -> str:
    """Format the ExecutionSummary into a compact, high-density terminal box table."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_tag = "[DRY-RUN PREVIEW] (No filesystem changes made)" if summary.dry_run else "[LIVE APPLIED] (Changes committed)"

    lines = [
        "=" * 86,
        "dev-sync Execution Summary",
        "=" * 86,
        f"Target       : {summary.target_name}",
        f"Action       : {summary.action.upper()}",
        f"Execution    : {mode_tag}",
        f"Timestamp    : {timestamp}",
    ]

    if summary.log_file_path:
        lines.append(f"Log File     : {summary.log_file_path.resolve()}")

    lines.extend([
        "-" * 86,
        f"Affected Root Directories ({len(summary.root_stats)} locations detected):",
        f"{'Root Directory':<32} {'Files':>7} {'Transferred':>12} {'Overwritten':>12} {'Deleted':>9} {'Errors':>8} {'Size':>10}",
        "-" * 86,
    ])

    if summary.root_stats:
        for rf_name in sorted(summary.root_stats.keys()):
            st = summary.root_stats[rf_name]
            lines.append(
                f"{rf_name:<32} {st.total_files:>7} {st.copied_count:>12} {st.overwritten_count:>12} {st.deleted_count:>9} {st.error_count:>8} {format_bytes(st.total_bytes):>10}"
            )
        lines.append("-" * 86)
        lines.append(
            f"{'TOTAL':<32} {summary.total_files:>7} {summary.copied_count:>12} {summary.overwritten_count:>12} {summary.deleted_count:>9} {summary.error_count:>8} {format_bytes(summary.total_bytes):>10}"
        )
    else:
        lines.append("No files or paths were affected. Target is clean and in sync.")

    lines.extend(["-" * 86, "Safety & Risk Assessment:"])
    if summary.warnings:
        for w in summary.warnings:
            lines.append(f"  [!] {w}")
    else:
        lines.append("  [OK] Zero overwrite or deletion risks detected.")

    if summary.dry_run:
        lines.append("  [TIP] Review affected paths above. Run without --dry-run to execute live changes.")

    lines.extend(["=" * 86, ""])
    return "\n".join(lines)

