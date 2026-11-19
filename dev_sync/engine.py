"""Rclone subprocess orchestration engine for dev_sync."""

from __future__ import annotations

import contextlib
import datetime
import fnmatch
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from dev_sync.config import SyncTarget
from dev_sync.errors import build_rclone_error
from dev_sync.filter import extract_bloat_roots, parse_filter_rules, resolve_target_filters
from dev_sync.report import format_diagnostic_report, parse_lsd_duplicates
from dev_sync.summary import ExecutionSummary, build_summary, format_terminal_summary

__all__ = [
    "DEFAULT_PACING_FLAGS",
    "SyncEngine",
    "format_diagnostic_report",
    "parse_lsd_duplicates",
]

# Pacing and network shield flags for safe cloud remote operations
# Prevents socket exhaustion, NIC hardware driver freezes, and cloud API rate-limiting
DEFAULT_PACING_FLAGS: List[str] = [
    "--tpslimit", "10",
    "--tpslimit-burst", "10",
    "--checkers", "4",
    "--transfers", "4",
    "--drive-pacer-min-sleep", "100ms",
    "--fast-list",
]


def prepare_log_path(target: SyncTarget, action: str, custom_log: Optional[Path] = None) -> Path:
    """Create a dedicated JSONL log file under .logs/ or custom path."""
    if custom_log:
        custom_log.parent.mkdir(parents=True, exist_ok=True)
        return custom_log

    logs_dir = Path(".logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return logs_dir / f"dev-sync-{action}-{target.name}-{ts}.jsonl"


def format_exclude_args(extra_excludes: Optional[List[str]] = None) -> List[str]:
    """Format extra exclusion patterns as rclone command arguments."""
    args: List[str] = []
    if extra_excludes:
        for pat in extra_excludes:
            clean = pat.strip()
            if clean:
                args.extend(["--exclude", clean])
    return args


def build_copy_args(
    src: str,
    dst: str,
    sync_filter: Path,
    dry_run: bool = False,
    extra_excludes: Optional[List[str]] = None,
    update: bool = False,
    backup_dir: Optional[str] = None,
) -> List[str]:
    """Assemble parameterized rclone copy arguments with optional smart-sync hooks."""
    exclude_args = format_exclude_args(extra_excludes)
    cmd_args = ["copy", src, dst, *exclude_args, "--filter-from", str(sync_filter), "-P"]
    if update:
        cmd_args.append("--update")
    if backup_dir:
        cmd_args.extend(["--backup-dir", backup_dir])
    cmd_args.extend(DEFAULT_PACING_FLAGS)
    if dry_run:
        cmd_args.append("--dry-run")
    return cmd_args


def finish_summary(
    target_name: str, action: str, dry_run: bool, log_path: Path, show_summary: bool
) -> Optional[ExecutionSummary]:
    """Optionally build and print terminal execution summary."""
    if not show_summary:
        return None
    summary = build_summary(target_name, action, dry_run, log_path)
    print("\n" + format_terminal_summary(summary))
    return summary


class SyncEngine:
    """Executes validated sync operations using parameterized rclone commands."""

    def __init__(self, filter_file: Path, rclone_binary: str = "rclone"):
        self.filter_file = filter_file
        self.rclone_binary = rclone_binary
        if not self.filter_file.exists():
            raise FileNotFoundError(f"Canonical filter file not found: {self.filter_file}")

    def _verify_rclone(self) -> None:
        """Verify that the rclone executable is available."""
        if shutil.which(self.rclone_binary) is None:
            raise RuntimeError(
                f"Executable '{self.rclone_binary}' was not found in system PATH. "
                "Please install rclone (https://rclone.org) or add it to your PATH."
            )

    def _run_rclone(
        self,
        args: List[str],
        stream: bool = True,
        log_file: Optional[Path] = None,
        allow_diff_exit: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Execute rclone with parameterized arguments (shell=False)."""
        self._verify_rclone()
        cmd = [self.rclone_binary] + args
        if log_file:
            cmd.extend(["--log-file", str(log_file), "--use-json-log", "--log-level", "INFO"])

        res = subprocess.run(cmd, shell=False, text=True, capture_output=not stream)
        if res.returncode != 0 and not (allow_diff_exit and res.returncode == 1):
            raise build_rclone_error(
                res.returncode,
                cmd,
                log_file=log_file,
                output=None if stream else res.stdout,
                stderr=None if stream else res.stderr,
            )
        return res

    @contextlib.contextmanager
    def _target_filters(self, target: SyncTarget):
        """Resolve and manage temporary target-specific filter files."""
        sync_filter, cleanup_filter, is_temp = resolve_target_filters(self.filter_file, target)
        try:
            yield sync_filter, cleanup_filter
        finally:
            if cleanup_filter.exists():
                cleanup_filter.unlink()
            if is_temp and sync_filter.exists():
                sync_filter.unlink()

    def _scan_bloat_roots(
        self, target: SyncTarget, cleanup_filter: Path, exclude_args: List[str]
    ) -> List[str]:
        """Scan remote directories up to depth 3 and collapse into unique bloat roots."""
        dirs_res = self._run_rclone(
            [
                "lsf", target.remote_path, "--dirs-only", "--recursive", "--max-depth", "3",
                *exclude_args, "--filter-from", str(cleanup_filter), *DEFAULT_PACING_FLAGS,
            ],
            stream=False,
        )
        raw_out = dirs_res.stdout if isinstance(dirs_res.stdout, str) else ""
        raw_dirs = [d.strip() for d in raw_out.splitlines() if d.strip()]
        rules = parse_filter_rules(self.filter_file.read_text(encoding="utf-8"))
        return extract_bloat_roots(raw_dirs, rules)

    def _check_remote_lsd(self, target: SyncTarget) -> Tuple[str, dict[str, int]]:
        """Inspect remote top-level folders and detect duplicate directory names."""
        print(f"\n[1/3] Checking remote connectivity for '{target.remote_path}'...")
        try:
            lsd_res = self._run_rclone(["lsd", target.remote_path, "--max-depth", "2"], stream=False)
            lsd_output = lsd_res.stdout
            if lsd_output.strip():
                print(lsd_output.strip())
            duplicates = parse_lsd_duplicates(lsd_output)
            if duplicates:
                print("\n[!] Advisory: Detected duplicate folder entries on remote storage:")
                for name, count in duplicates.items():
                    print(f"    - '{name}' ({count} occurrences)")
                print(f"    Tip: Run 'rclone dedupe {target.remote_path}' to inspect and merge duplicate folders.")
            return lsd_output, duplicates
        except subprocess.CalledProcessError:
            print(f"Warning: Could not list directory structure at '{target.remote_path}'.")
            return "", {}

    def diagnose(
        self,
        target: SyncTarget,
        report_path: Optional[Path] = None,
        extra_excludes: Optional[List[str]] = None,
    ) -> Path:
        """Inspect remote connectivity, detect duplicates, and calculate exact ignored bloat size."""
        lsd_output, duplicates = self._check_remote_lsd(target)
        exclude_args = format_exclude_args(extra_excludes)

        with self._target_filters(target) as (_, cleanup_filter):
            print("\n[2/3] Calculating ignored build artifacts & bloat on remote...")
            size_res = self._run_rclone(
                ["size", target.remote_path, *exclude_args, "--filter-from", str(cleanup_filter), *DEFAULT_PACING_FLAGS],
                stream=False,
            )
            size_output = size_res.stdout.strip()
            print(size_output)

            print("\n[3/3] Scanning top-level build folders on remote matching ignore rules...")
            bloat_roots = self._scan_bloat_roots(target, cleanup_filter, exclude_args)
            if bloat_roots:
                print(f"Detected remote bloat directories ({len(bloat_roots)} unique root locations):")
                for d in bloat_roots[:15]:
                    print(f"  - {d}")
                if len(bloat_roots) > 15:
                    print(f"  ... and {len(bloat_roots) - 15} more directories.")
            else:
                print("No ignored bloat directories detected on remote. Remote is clean!")

        report_file = report_path if report_path else Path(f"dev-sync-report-{target.name}.txt")
        report_content = format_diagnostic_report(target, lsd_output, duplicates, size_output, bloat_roots)
        report_file.write_text(report_content, encoding="utf-8")
        print(f"\n[REPORT] Full untruncated diagnostic report written to:\n         {report_file.resolve()}")
        return report_file

    def _purge_detected_bloat_roots(
        self,
        target: SyncTarget,
        cleanup_filter: Path,
        exclude_args: List[str],
        extra_excludes: Optional[List[str]],
        dry_run: bool,
        active_log: Path,
    ) -> None:
        """Purge top-level bloat directories before running paced file deletion."""
        mode_label = "[DRY-RUN] " if dry_run else ""
        try:
            bloat_roots = self._scan_bloat_roots(target, cleanup_filter, exclude_args)
            if extra_excludes and bloat_roots:
                bloat_roots = [
                    br for br in bloat_roots
                    if not any(fnmatch.fnmatch(br.rstrip("/"), pat.strip().rstrip("/*")) for pat in extra_excludes)
                ]
            if bloat_roots:
                print(f"Purging {len(bloat_roots)} detected bloat root directory entities...")
                for br in bloat_roots:
                    purge_target = f"{target.remote_path}/{br.rstrip('/')}"
                    purge_cmd = ["purge", purge_target, *DEFAULT_PACING_FLAGS]
                    if dry_run:
                        purge_cmd.append("--dry-run")
                    print(f"  - {mode_label}Purging directory: {purge_target}")
                    self._run_rclone(purge_cmd, log_file=active_log)
        except Exception as ex:
            print(f"Warning: Root directory purge scan bypassed ({ex}). Proceeding with paced delete.")

    def cleanup(
        self,
        target: SyncTarget,
        dry_run: bool = False,
        purge_roots: bool = True,
        log_path: Optional[Path] = None,
        show_summary: bool = True,
        extra_excludes: Optional[List[str]] = None,
    ) -> Optional[ExecutionSummary]:
        """Purge remote build artifacts and transient caches safely."""
        active_log = prepare_log_path(target, "cleanup", log_path)
        exclude_args = format_exclude_args(extra_excludes)

        with self._target_filters(target) as (_, cleanup_filter):
            mode_label = "[DRY-RUN] " if dry_run else ""
            print(f"\n--> {mode_label}Cleaning up ignored transient build folders and caches on remote...")
            if purge_roots:
                self._purge_detected_bloat_roots(target, cleanup_filter, exclude_args, extra_excludes, dry_run, active_log)

            cmd_args = [
                "delete", target.remote_path, *exclude_args,
                "--filter-from", str(cleanup_filter), "--rmdirs", "-P", *DEFAULT_PACING_FLAGS,
            ]
            if dry_run:
                cmd_args.append("--dry-run")
            self._run_rclone(cmd_args, log_file=active_log)
            return finish_summary(target.name, "cleanup", dry_run, active_log, show_summary)

    def pull(
        self,
        target: SyncTarget,
        dry_run: bool = False,
        log_path: Optional[Path] = None,
        show_summary: bool = True,
        extra_excludes: Optional[List[str]] = None,
        update: bool = False,
        backup_dir: Optional[str] = None,
    ) -> Optional[ExecutionSummary]:
        """Download remote files to local, filtering out ignored artifacts."""
        target.local_path.mkdir(parents=True, exist_ok=True)
        mode_label = "[DRY-RUN] " if dry_run else ""
        print(f"\n--> {mode_label}Pulling from '{target.remote_path}' into '{target.local_path}'...")
        active_log = prepare_log_path(target, "pull", log_path)

        with self._target_filters(target) as (sync_filter, _):
            cmd_args = build_copy_args(
                target.remote_path, str(target.local_path), sync_filter,
                dry_run=dry_run, extra_excludes=extra_excludes, update=update, backup_dir=backup_dir,
            )
            self._run_rclone(cmd_args, log_file=active_log)
        return finish_summary(target.name, "pull", dry_run, active_log, show_summary)

    def push(
        self,
        target: SyncTarget,
        dry_run: bool = False,
        log_path: Optional[Path] = None,
        show_summary: bool = True,
        extra_excludes: Optional[List[str]] = None,
        update: bool = False,
        backup_dir: Optional[str] = None,
    ) -> Optional[ExecutionSummary]:
        """Upload local files to remote, filtering out ignored artifacts."""
        if not target.local_path.exists():
            raise FileNotFoundError(f"Cannot push: local directory does not exist at '{target.local_path}'")

        mode_label = "[DRY-RUN] " if dry_run else ""
        print(f"\n--> {mode_label}Pushing from '{target.local_path}' to '{target.remote_path}'...")
        active_log = prepare_log_path(target, "push", log_path)

        with self._target_filters(target) as (sync_filter, _):
            cmd_args = build_copy_args(
                str(target.local_path), target.remote_path, sync_filter,
                dry_run=dry_run, extra_excludes=extra_excludes, update=update, backup_dir=backup_dir,
            )
            self._run_rclone(cmd_args, log_file=active_log)
        return finish_summary(target.name, "push", dry_run, active_log, show_summary)

    def check(
        self,
        target: SyncTarget,
        log_path: Optional[Path] = None,
        show_summary: bool = True,
        extra_excludes: Optional[List[str]] = None,
    ) -> Optional[ExecutionSummary]:
        """Compare local and remote files to identify differences."""
        if not target.local_path.exists():
            print(f"Warning: Local directory does not exist yet: '{target.local_path}'")
            return None

        print(f"\n--> Checking synchronization differences between '{target.local_path}' and '{target.remote_path}'...")
        active_log = prepare_log_path(target, "check", log_path)
        exclude_args = format_exclude_args(extra_excludes)

        with self._target_filters(target) as (sync_filter, _):
            cmd_args = [
                "check", str(target.local_path), target.remote_path,
                *exclude_args, "--filter-from", str(sync_filter), *DEFAULT_PACING_FLAGS,
            ]
            self._run_rclone(cmd_args, log_file=active_log, allow_diff_exit=True)
        return finish_summary(target.name, "check", False, active_log, show_summary)
