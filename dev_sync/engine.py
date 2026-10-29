"""Rclone subprocess orchestration engine for dev_sync."""

from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import List, Optional

from dev_sync.config import SyncTarget
from dev_sync.filter import extract_bloat_roots, generate_cleanup_filter_file, parse_filter_rules
from dev_sync.report import format_diagnostic_report, parse_lsd_duplicates

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

    def _run_rclone(self, args: List[str], stream: bool = True) -> subprocess.CompletedProcess[str]:
        """Execute rclone with parameterized arguments (shell=False)."""
        self._verify_rclone()
        cmd = [self.rclone_binary] + args

        if stream:
            res = subprocess.run(cmd, shell=False, text=True)
            if res.returncode != 0:
                raise subprocess.CalledProcessError(res.returncode, cmd)
            return res
        else:
            res = subprocess.run(cmd, shell=False, text=True, capture_output=True)
            if res.returncode != 0:
                raise subprocess.CalledProcessError(res.returncode, cmd, output=res.stdout, stderr=res.stderr)
            return res

    def diagnose(self, target: SyncTarget, report_path: Optional[Path] = None) -> Path:
        """Inspect remote connectivity, detect duplicates, and calculate exact ignored bloat size."""
        print(f"\n[1/3] Checking remote connectivity for '{target.remote_path}'...")
        lsd_output = ""
        duplicates: dict[str, int] = {}
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
                print("    Note: Providers like Google Drive allow identical folder names under the same directory.")
                print(f"    Tip: Run 'rclone dedupe {target.remote_path}' to inspect and merge duplicate folders.")
        except subprocess.CalledProcessError:
            print(f"Warning: Could not list directory structure at '{target.remote_path}'. Remote may be empty or path does not exist yet.")

        # Dynamically compile cleanup filter to inspect bloat
        with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as tf:
            cleanup_filter_path = Path(tf.name)

        size_output = ""
        bloat_roots: List[str] = []

        try:
            generate_cleanup_filter_file(self.filter_file, cleanup_filter_path)

            print(f"\n[2/3] Calculating ignored build artifacts & bloat on remote...")
            size_res = self._run_rclone(
                ["size", target.remote_path, "--filter-from", str(cleanup_filter_path), *DEFAULT_PACING_FLAGS],
                stream=False,
            )
            size_output = size_res.stdout.strip()
            print(size_output)

            print(f"\n[3/3] Scanning top-level build folders on remote matching ignore rules...")
            dirs_res = self._run_rclone(
                [
                    "lsf",
                    target.remote_path,
                    "--dirs-only",
                    "--recursive",
                    "--max-depth",
                    "3",
                    "--filter-from",
                    str(cleanup_filter_path),
                    *DEFAULT_PACING_FLAGS,
                ],
                stream=False,
            )
            raw_dirs = [d.strip() for d in dirs_res.stdout.splitlines() if d.strip()]
            rules = parse_filter_rules(self.filter_file.read_text(encoding="utf-8"))
            bloat_roots = extract_bloat_roots(raw_dirs, rules)

            if bloat_roots:
                print(f"Detected remote bloat directories ({len(bloat_roots)} unique root locations):")
                for d in bloat_roots[:15]:
                    print(f"  - {d}")
                if len(bloat_roots) > 15:
                    print(f"  ... and {len(bloat_roots) - 15} more directories.")
            else:
                print("No ignored bloat directories detected on remote. Remote is clean!")
        finally:
            if cleanup_filter_path.exists():
                cleanup_filter_path.unlink()

        # Generate and save physical diagnostic report
        report_file = report_path if report_path else Path(f"dev-sync-report-{target.name}.txt")
        report_content = format_diagnostic_report(
            target=target,
            lsd_output=lsd_output,
            duplicates=duplicates,
            size_output=size_output,
            bloat_roots=bloat_roots,
        )
        report_file.write_text(report_content, encoding="utf-8")
        print(f"\n[REPORT] Full untruncated diagnostic report written to:")
        print(f"         {report_file.resolve()}")
        return report_file

    def cleanup(self, target: SyncTarget, dry_run: bool = False, purge_roots: bool = True) -> None:
        """Purge remote build artifacts and transient caches safely.

        Uses dynamic inverted filter with safe API pacing (--tpslimit, --fast-list)
        to prevent network adapter socket exhaustion and rate-limit panics.
        When purge_roots is enabled, first scans and purges identified top-level
        bloat root directories in atomic directory operations, then runs a paced
        delete pass for any remaining loose ignored files.
        """
        with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as tf:
            cleanup_filter_path = Path(tf.name)

        try:
            generate_cleanup_filter_file(self.filter_file, cleanup_filter_path)
            mode_label = "[DRY-RUN] " if dry_run else ""
            print(f"\n--> {mode_label}Cleaning up ignored transient build folders and caches on remote...")

            # Stage 1: Atomic purge of detected bloat root folders (prevents file-by-file storms)
            if purge_roots:
                try:
                    dirs_res = self._run_rclone(
                        [
                            "lsf",
                            target.remote_path,
                            "--dirs-only",
                            "--recursive",
                            "--max-depth",
                            "3",
                            "--filter-from",
                            str(cleanup_filter_path),
                            *DEFAULT_PACING_FLAGS,
                        ],
                        stream=False,
                    )
                    raw_out = dirs_res.stdout if isinstance(dirs_res.stdout, str) else ""
                    raw_dirs = [d.strip() for d in raw_out.splitlines() if d.strip()]
                    rules = parse_filter_rules(self.filter_file.read_text(encoding="utf-8"))
                    bloat_roots = extract_bloat_roots(raw_dirs, rules)

                    if bloat_roots:
                        print(f"Purging {len(bloat_roots)} detected bloat root directory entities...")
                        for br in bloat_roots:
                            purge_target = f"{target.remote_path}/{br.rstrip('/')}"
                            purge_cmd = ["purge", purge_target, *DEFAULT_PACING_FLAGS]
                            if dry_run:
                                purge_cmd.append("--dry-run")
                            print(f"  - {mode_label}Purging directory: {purge_target}")
                            self._run_rclone(purge_cmd)
                except Exception as ex:
                    print(f"Warning: Root directory purge scan bypassed ({ex}). Proceeding with paced delete.")

            # Stage 2: Paced cleanup of loose ignored files / empty directories
            cmd_args = [
                "delete",
                target.remote_path,
                "--filter-from",
                str(cleanup_filter_path),
                "--rmdirs",
                "-P",
                *DEFAULT_PACING_FLAGS,
            ]
            if dry_run:
                cmd_args.append("--dry-run")

            self._run_rclone(cmd_args)
        finally:
            if cleanup_filter_path.exists():
                cleanup_filter_path.unlink()

    def pull(self, target: SyncTarget, dry_run: bool = False) -> None:
        """Download remote files to local, filtering out ignored artifacts."""
        target.local_path.mkdir(parents=True, exist_ok=True)
        mode_label = "[DRY-RUN] " if dry_run else ""
        print(f"\n--> {mode_label}Pulling from '{target.remote_path}' into '{target.local_path}'...")

        cmd_args = [
            "copy",
            target.remote_path,
            str(target.local_path),
            "--filter-from",
            str(self.filter_file),
            "-P",
            *DEFAULT_PACING_FLAGS,
        ]
        if dry_run:
            cmd_args.append("--dry-run")

        self._run_rclone(cmd_args)

    def push(self, target: SyncTarget, dry_run: bool = False) -> None:
        """Upload local files to remote, filtering out ignored artifacts."""
        if not target.local_path.exists():
            raise FileNotFoundError(f"Cannot push: local directory does not exist at '{target.local_path}'")

        mode_label = "[DRY-RUN] " if dry_run else ""
        print(f"\n--> {mode_label}Pushing from '{target.local_path}' to '{target.remote_path}'...")

        cmd_args = [
            "copy",
            str(target.local_path),
            target.remote_path,
            "--filter-from",
            str(self.filter_file),
            "-P",
            *DEFAULT_PACING_FLAGS,
        ]
        if dry_run:
            cmd_args.append("--dry-run")

        self._run_rclone(cmd_args)

    def check(self, target: SyncTarget) -> None:
        """Compare local and remote files to identify differences."""
        if not target.local_path.exists():
            print(f"Warning: Local directory does not exist yet: '{target.local_path}'")
            return

        print(f"\n--> Checking synchronization differences between '{target.local_path}' and '{target.remote_path}'...")
        cmd_args = [
            "check",
            str(target.local_path),
            target.remote_path,
            "--filter-from",
            str(self.filter_file),
            *DEFAULT_PACING_FLAGS,
        ]
        try:
            self._run_rclone(cmd_args)
        except subprocess.CalledProcessError as e:
            # rclone check returns 1 when differences are found
            if e.returncode == 1:
                print("\nDifferences detected between local and remote.")
            else:
                raise
