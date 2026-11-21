"""Git repository discovery and working-tree classification for dev_sync."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple

from dev_sync.models import LedgerEntry

GIT_STATUS_TRACKED_CLEAN = "tracked_clean"
GIT_STATUS_TRACKED_DIRTY = "tracked_dirty"
GIT_STATUS_UNTRACKED = "untracked_companion"
GIT_STATUS_IGNORED = "git_ignored"
GIT_STATUS_NON_GIT = "non_git"

_PRUNE_DIRS = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".dev-sync",
    ".dev-sync-trash",
})


@dataclass
class FileGitInfo:
    """Git classification and branch metadata for a single file."""

    rel_path: str
    git_status: str = GIT_STATUS_NON_GIT
    git_branch: Optional[str] = None
    git_head: Optional[str] = None
    repo_rel_root: Optional[str] = None


@dataclass
class RepoState:
    """Snapshot of a discovered Git repository's branch and file states."""

    repo_root: Path
    repo_rel_root: str
    branch: Optional[str] = None
    head: Optional[str] = None
    tracked_files: Set[str] = field(default_factory=set)
    dirty_files: Set[str] = field(default_factory=set)
    untracked_files: Set[str] = field(default_factory=set)
    ignored_files: Set[str] = field(default_factory=set)
    ignored_dirs: Tuple[str, ...] = ()


def _run_git(repo_root: Path, args: List[str], timeout: int = 10) -> Optional[bytes]:
    """Execute a read-only git command and return stdout bytes, or None on error."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def discover_git_repos(root: Path) -> List[Path]:
    """Discover all Git repositories under root without descending into .git/."""
    if not root.is_dir():
        return []
    repos: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames or ".git" in filenames:
            repos.append(Path(dirpath))
        dirnames[:] = sorted(d for d in dirnames if d not in _PRUNE_DIRS)
    return repos


def _parse_porcelain_entry(
    tokens: List[str],
    idx: int,
    dirty: Set[str],
    untracked: Set[str],
    ignored_files: Set[str],
    ignored_dirs: List[str],
) -> int:
    """Parse one NUL-delimited porcelain-v2 record and return the next index."""
    entry = tokens[idx]
    if entry.startswith("1 "):
        dirty.add(entry.split(" ", 8)[8])
    elif entry.startswith("u "):
        dirty.add(entry.split(" ", 10)[10])
    elif entry.startswith("2 "):
        dirty.add(entry.split(" ", 9)[9])
        return idx + 2
    elif entry.startswith("? "):
        untracked.add(entry[2:].rstrip("/"))
    elif entry.startswith("! "):
        path = entry[2:]
        if path.endswith("/"):
            ignored_dirs.append(path.rstrip("/"))
        else:
            ignored_files.add(path)
    return idx + 1


def _parse_porcelain_v2_z(
    raw: Optional[bytes],
) -> Tuple[Set[str], Set[str], Set[str], Tuple[str, ...]]:
    """Parse `git status --porcelain=v2 --ignored -uall -z` output."""
    if not raw:
        return set(), set(), set(), ()
    tokens = [t.decode("utf-8", errors="replace") for t in raw.split(b"\x00") if t]
    dirty: Set[str] = set()
    untracked: Set[str] = set()
    ignored_files: Set[str] = set()
    ignored_dirs: List[str] = []
    idx = 0
    while idx < len(tokens):
        idx = _parse_porcelain_entry(
            tokens, idx, dirty, untracked, ignored_files, ignored_dirs
        )
    return dirty, untracked, ignored_files, tuple(ignored_dirs)


def _parse_ls_files_z(raw: Optional[bytes]) -> Set[str]:
    """Parse `git ls-files -z` output into a set of POSIX relative paths."""
    if not raw:
        return set()
    return {t.decode("utf-8", errors="replace") for t in raw.split(b"\x00") if t}


def _resolve_branch_and_head(repo_root: Path) -> Tuple[Optional[str], Optional[str]]:
    """Resolve current branch name and short HEAD SHA without mutating .git/."""
    head_raw = _run_git(repo_root, ["rev-parse", "--short", "HEAD"])
    head = head_raw.decode("utf-8", errors="replace").strip() if head_raw else None
    branch_raw = _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch_raw:
        branch = branch_raw.decode("utf-8", errors="replace").strip()
        return branch or None, head
    sym_raw = _run_git(repo_root, ["symbolic-ref", "--short", "HEAD"])
    branch = sym_raw.decode("utf-8", errors="replace").strip() if sym_raw else None
    return branch or None, head


def inspect_repo(repo_root: Path, base_root: Path) -> RepoState:
    """Inspect a single Git repository's branch, tracked, dirty, and ignored files."""
    rel = repo_root.relative_to(base_root).as_posix()
    repo_rel_root = "" if rel == "." else rel
    branch, head = _resolve_branch_and_head(repo_root)
    tracked = _parse_ls_files_z(_run_git(repo_root, ["ls-files", "-z"]))
    status_raw = _run_git(
        repo_root, ["status", "--porcelain=v2", "--ignored", "-uall", "-z"]
    )
    dirty, untracked, ignored_files, ignored_dirs = _parse_porcelain_v2_z(status_raw)
    return RepoState(
        repo_root=repo_root,
        repo_rel_root=repo_rel_root,
        branch=branch,
        head=head,
        tracked_files=tracked,
        dirty_files=dirty,
        untracked_files=untracked,
        ignored_files=ignored_files,
        ignored_dirs=ignored_dirs,
    )


def inspect_workspace_repos(base_root: Path) -> List[RepoState]:
    """Discover and inspect all Git repositories under base_root (deepest first)."""
    repos = [inspect_repo(repo, base_root) for repo in discover_git_repos(base_root)]
    repos.sort(key=lambda r: (len(r.repo_rel_root.split("/")) if r.repo_rel_root else 0), reverse=True)
    return repos


def _find_matching_repo(norm_path: str, repos: List[RepoState]) -> Optional[Tuple[RepoState, str]]:
    """Find the most specific RepoState containing norm_path and return (repo, in_repo_path)."""
    for repo in repos:
        if not repo.repo_rel_root:
            return repo, norm_path
        prefix = f"{repo.repo_rel_root}/"
        if norm_path.startswith(prefix):
            return repo, norm_path[len(prefix):]
    return None


def _is_under_ignored_dir(in_repo_path: str, ignored_dirs: Tuple[str, ...]) -> bool:
    """Return True if in_repo_path resides inside any ignored directory prefix."""
    return any(
        in_repo_path == d or in_repo_path.startswith(f"{d}/")
        for d in ignored_dirs
    )


def classify_file(rel_path: str, repos: List[RepoState]) -> FileGitInfo:
    """Classify a target-relative path against discovered Git repositories."""
    norm = rel_path.replace("\\", "/").lstrip("./")
    match = _find_matching_repo(norm, repos)
    if match is None:
        return FileGitInfo(rel_path=norm, git_status=GIT_STATUS_NON_GIT)

    repo, in_repo = match
    if in_repo in repo.dirty_files:
        status = GIT_STATUS_TRACKED_DIRTY
    elif in_repo in repo.tracked_files:
        status = GIT_STATUS_TRACKED_CLEAN
    elif in_repo in repo.ignored_files or _is_under_ignored_dir(in_repo, repo.ignored_dirs):
        status = GIT_STATUS_IGNORED
    else:
        status = GIT_STATUS_UNTRACKED

    return FileGitInfo(
        rel_path=norm,
        git_status=status,
        git_branch=repo.branch,
        git_head=repo.head,
        repo_rel_root=repo.repo_rel_root,
    )


def detect_git_conflict(
    local_info: FileGitInfo,
    remote_entry: Optional[LedgerEntry],
) -> Optional[str]:
    """Return a human-readable warning if pulling remote_entry risks clobbering Git state."""
    if local_info.git_status not in (GIT_STATUS_TRACKED_CLEAN, GIT_STATUS_TRACKED_DIRTY):
        return None
    if remote_entry is None:
        return f"Local file is Git-tracked ({local_info.git_status}) with no remote provenance"
    if remote_entry.git_branch and local_info.git_branch and remote_entry.git_branch != local_info.git_branch:
        who = remote_entry.last_modified_by or remote_entry.origin_machine or "remote"
        return (
            f"Git branch mismatch: local '{local_info.git_branch}' vs "
            f"remote '{remote_entry.git_branch}' ({who})"
        )
    if remote_entry.git_head and local_info.git_head and remote_entry.git_head != local_info.git_head:
        return (
            f"Git commit divergence ({local_info.git_status}): "
            f"local {local_info.git_head} vs remote {remote_entry.git_head}"
        )
    if local_info.git_status == GIT_STATUS_TRACKED_DIRTY:
        return "Local file has uncommitted Git modifications (tracked_dirty)"
    return None
