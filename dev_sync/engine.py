"""Rclone subprocess orchestration engine for dev_sync."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from dev_sync.config import SyncTarget
from dev_sync.filter import generate_cleanup_filter_file


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

    def diagnose(self, target: SyncTarget) -> None:
        """Inspect remote connectivity and calculate exact ignored bloat size."""
        print(f"\n[1/3] Checking remote connectivity for '{target.remote_path}'...")
        try:
            self._run_rclone(["lsd", target.remote_path, "--max-depth", "2"])
        except subprocess.CalledProcessError:
            print(f"Warning: Could not list directory structure at '{target.remote_path}'. Remote may be empty or path does not exist yet.")

        # Dynamically compile cleanup filter to inspect bloat
        with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as tf:
            cleanup_filter_path = Path(tf.name)

        try:
            generate_cleanup_filter_file(self.filter_file, cleanup_filter_path)

            print(f"\n[2/3] Calculating ignored build artifacts & bloat on remote...")
            size_res = self._run_rclone(
                ["size", target.remote_path, "--filter-from", str(cleanup_filter_path)],
                stream=False,
            )
            print(size_res.stdout.strip())

            print(f"\n[3/3] Scanning top-level build folders on remote matching ignore rules...")
            dirs_res = self._run_rclone(
                ["lsf", target.remote_path, "--dirs-only", "--recursive", "--max-depth", "3", "--filter-from", str(cleanup_filter_path)],
                stream=False,
            )
            found_dirs = [d.strip() for d in dirs_res.stdout.splitlines() if d.strip()]
            if found_dirs:
                print("Detected remote bloat directories:")
                for d in found_dirs[:15]:
                    print(f"  - {d}")
                if len(found_dirs) > 15:
                    print(f"  ... and {len(found_dirs) - 15} more directories.")
            else:
                print("No ignored bloat directories detected on remote. Remote is clean!")
        finally:
            if cleanup_filter_path.exists():
                cleanup_filter_path.unlink()

    def cleanup(self, target: SyncTarget, dry_run: bool = False) -> None:
        """Purge remote build artifacts and transient caches using dynamic inverted filter."""
        with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as tf:
            cleanup_filter_path = Path(tf.name)

        try:
            generate_cleanup_filter_file(self.filter_file, cleanup_filter_path)
            mode_label = "[DRY-RUN] " if dry_run else ""
            print(f"\n--> {mode_label}Cleaning up ignored transient build folders and caches on remote...")

            cmd_args = [
                "delete",
                target.remote_path,
                "--filter-from",
                str(cleanup_filter_path),
                "--rmdirs",
                "-P",
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
        ]
        try:
            self._run_rclone(cmd_args)
        except subprocess.CalledProcessError as e:
            # rclone check returns 1 when differences are found
            if e.returncode == 1:
                print("\nDifferences detected between local and remote.")
            else:
                raise
