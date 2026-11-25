"""Unit tests for dev_sync.git_guard repository discovery and classification."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dev_sync.git_guard import (
    GIT_STATUS_IGNORED,
    GIT_STATUS_NON_GIT,
    GIT_STATUS_TRACKED_CLEAN,
    GIT_STATUS_TRACKED_DIRTY,
    GIT_STATUS_UNTRACKED,
    FileGitInfo,
    classify_file,
    detect_git_conflict,
    discover_git_repos,
    inspect_workspace_repos,
)
from dev_sync.models import LedgerEntry


def _run_git_setup(cwd: Path, *args: str) -> None:
    """Run a git setup command inside an isolated temporary repository."""
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _init_sample_repo(repo_dir: Path, branch: str = "main") -> None:
    """Initialize a Git repo with one committed file, one dirty file, one untracked, and one ignored."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    _run_git_setup(repo_dir, "init", "-b", branch)
    _run_git_setup(repo_dir, "config", "user.email", "test@example.com")
    _run_git_setup(repo_dir, "config", "user.name", "Test User")
    (repo_dir / ".gitignore").write_text("*.log\nignored_dir/\n", encoding="utf-8")
    (repo_dir / "clean.py").write_text("print('clean')\n", encoding="utf-8")
    (repo_dir / "dirty.py").write_text("print('v1')\n", encoding="utf-8")
    _run_git_setup(repo_dir, "add", ".gitignore", "clean.py", "dirty.py")
    _run_git_setup(repo_dir, "commit", "-m", "initial commit")

    (repo_dir / "dirty.py").write_text("print('v2')\n", encoding="utf-8")
    (repo_dir / "NOTES.txt").write_text("untracked companion\n", encoding="utf-8")
    (repo_dir / "debug.log").write_text("ignored log\n", encoding="utf-8")
    (repo_dir / "ignored_dir").mkdir()
    (repo_dir / "ignored_dir" / "cache.bin").write_text("bin", encoding="utf-8")


@unittest.skipIf(shutil.which("git") is None, "git executable is not available")
class TestGitGuard(unittest.TestCase):
    """Verify Git repo discovery, file classification, and conflict detection."""

    def test_discover_and_classify_subrepos_and_non_git(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_sample_repo(root / "products" / "app_a", branch="main")
            (root / "standalone").mkdir(parents=True)
            (root / "standalone" / "readme.txt").write_text("plain", encoding="utf-8")

            discovered = discover_git_repos(root)
            self.assertEqual(discovered, [root / "products" / "app_a"])

            repos = inspect_workspace_repos(root)
            self.assertEqual(len(repos), 1)
            self.assertEqual(repos[0].branch, "main")
            self.assertIsNotNone(repos[0].head)

            self.assertEqual(
                classify_file("products/app_a/clean.py", repos).git_status,
                GIT_STATUS_TRACKED_CLEAN,
            )
            self.assertEqual(
                classify_file("products/app_a/dirty.py", repos).git_status,
                GIT_STATUS_TRACKED_DIRTY,
            )
            self.assertEqual(
                classify_file("products/app_a/NOTES.txt", repos).git_status,
                GIT_STATUS_UNTRACKED,
            )
            self.assertEqual(
                classify_file("products/app_a/debug.log", repos).git_status,
                GIT_STATUS_IGNORED,
            )
            self.assertEqual(
                classify_file("products/app_a/ignored_dir/cache.bin", repos).git_status,
                GIT_STATUS_IGNORED,
            )
            self.assertEqual(
                classify_file("standalone/readme.txt", repos).git_status,
                GIT_STATUS_NON_GIT,
            )

    def test_detect_git_conflict_warnings(self) -> None:
        clean_info = FileGitInfo(
            rel_path="app/main.py",
            git_status=GIT_STATUS_TRACKED_CLEAN,
            git_branch="main",
            git_head="abc1234",
        )
        remote_diff_branch = LedgerEntry(
            rel_path="app/main.py",
            last_modified_by="laptop",
            git_branch="feature/ui",
            git_head="def5678",
        )
        warn = detect_git_conflict(clean_info, remote_diff_branch)
        self.assertIsNotNone(warn)
        self.assertIn("branch mismatch", warn.lower())

        dirty_info = FileGitInfo(
            rel_path="app/main.py",
            git_status=GIT_STATUS_TRACKED_DIRTY,
            git_branch="main",
            git_head="abc1234",
        )
        remote_same = LedgerEntry(
            rel_path="app/main.py",
            last_modified_by="laptop",
            git_branch="main",
            git_head="abc1234",
        )
        dirty_warn = detect_git_conflict(dirty_info, remote_same)
        self.assertIsNotNone(dirty_warn)
        self.assertIn("uncommitted", dirty_warn.lower())

        companion = FileGitInfo(rel_path="app/NOTES.txt", git_status=GIT_STATUS_UNTRACKED)
        self.assertIsNone(detect_git_conflict(companion, remote_diff_branch))

    def test_non_git_fallback_when_git_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "plain.txt").write_text("hello", encoding="utf-8")
            with mock.patch("dev_sync.git_guard.subprocess.run", side_effect=FileNotFoundError):
                repos = inspect_workspace_repos(root)
            self.assertEqual(repos, [])
            info = classify_file("plain.txt", repos)
            self.assertEqual(info.git_status, GIT_STATUS_NON_GIT)


if __name__ == "__main__":
    unittest.main()
