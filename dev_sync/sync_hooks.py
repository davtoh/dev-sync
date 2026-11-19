"""Pre-flight Git/orphan hooks, local-prefs exclusions, and post-sync ledger updates."""

from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Dict, List, Optional

from dev_sync.config import SyncTarget
from dev_sync.git_guard import (
    _PRUNE_DIRS,
    FileGitInfo,
    classify_file,
    detect_git_conflict,
    inspect_workspace_repos,
)
from dev_sync.ledger import (
    compile_merged_ledger,
    load_all_machine_snapshots,
    load_local_prefs,
    load_merged_ledger,
    record_local_snapshot,
    save_merged_ledger,
)


def merge_local_prefs_excludes(local_path: Path, extra_excludes: Optional[List[str]] = None) -> List[str]:
    """Combine CLI/caller excludes with machine-local ignored_paths from local-prefs.json."""
    merged: List[str] = []
    seen = set()
    for pat in extra_excludes or []:
        clean = pat.strip()
        if clean and clean not in seen:
            merged.append(clean)
            seen.add(clean)
    prefs = load_local_prefs(local_path)
    for pat in prefs.ignored_paths:
        clean = pat.strip()
        if clean and clean not in seen:
            merged.append(clean)
            seen.add(clean)
    return merged


def resolve_backup_dir(target: SyncTarget, action: str, explicit_backup_dir: Optional[str] = None) -> str:
    """Resolve native rclone --backup-dir for pull/push overwrite protection."""
    if explicit_backup_dir:
        return explicit_backup_dir
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    if action == "pull":
        return str(target.local_path / ".dev-sync" / "trash" / ts)
    return f"{target.remote_path}/.dev-sync-trash/{ts}"


def emit_preflight_git_banner(local_path: Path) -> List[str]:
    """Check local Git dirty state and ledger branch conflicts; print concise pre-flight banner."""
    if not local_path.exists():
        return []
    repos = inspect_workspace_repos(local_path)
    if not repos:
        return []
    merged_ledger = load_merged_ledger(local_path)
    warnings: List[str] = []
    for repo in repos:
        for rel_in_repo in sorted(repo.dirty_files):
            rel_path = rel_in_repo if repo.repo_rel_root == "." else f"{repo.repo_rel_root}/{rel_in_repo}"
            info = classify_file(rel_path, repos)
            conflict = detect_git_conflict(info, merged_ledger.get(rel_path))
            if conflict:
                warnings.append(conflict)
            else:
                warnings.append(f"Local uncommitted Git change in '{rel_path}' (branch '{repo.branch or 'unknown'}').")
    if warnings:
        print(f"\n[PRE-FLIGHT] Git Guard noticed {len(warnings)} active Git warning(s):")
        for w in warnings[:5]:
            print(f"  [!] {w}")
        if len(warnings) > 5:
            print(f"  ... and {len(warnings) - 5} more (run 'diff' for full tree).")
    return warnings


def _collect_workspace_rel_paths(local_path: Path) -> List[str]:
    """Collect relative POSIX file paths in local_path, pruning transient/cache dirs."""
    rel_paths: List[str] = []
    for dirpath, dirnames, filenames in os.walk(local_path):
        dirnames[:] = sorted(d for d in dirnames if d not in _PRUNE_DIRS)
        for fname in sorted(filenames):
            full = Path(dirpath) / fname
            rel_paths.append(full.relative_to(local_path).as_posix())
    return rel_paths


def update_post_sync_ledger(local_path: Path, dry_run: bool) -> None:
    """Update this machine's sharded snapshot and merged ledger after a live pull/push."""
    if dry_run or not local_path.exists():
        return
    rel_paths = _collect_workspace_rel_paths(local_path)
    repos = inspect_workspace_repos(local_path)
    git_infos: Dict[str, FileGitInfo] = {p: classify_file(p, repos) for p in rel_paths}
    record_local_snapshot(local_path, rel_paths, git_infos=git_infos)
    snapshots = load_all_machine_snapshots(local_path)
    save_merged_ledger(local_path, compile_merged_ledger(snapshots))


def run_copy_sync(
    engine: object, target: SyncTarget, action: str, src: str, dst: str,
    dry_run: bool, log_path: Optional[Path], show_summary: bool,
    extra_excludes: Optional[List[str]], update: bool, backup_dir: Optional[str],
) -> object:
    """Execute parameterized pull/push copy with local-prefs, backup-dir, and ledger sync."""
    from dev_sync.engine import build_copy_args, finish_summary, prepare_log_path

    active_log = prepare_log_path(target, action, log_path)
    merged_excludes = merge_local_prefs_excludes(target.local_path, extra_excludes)
    resolved_backup = resolve_backup_dir(target, action, backup_dir)
    with engine._target_filters(target) as (sync_filter, _):
        cmd_args = build_copy_args(
            src, dst, sync_filter, dry_run=dry_run,
            extra_excludes=merged_excludes, update=update, backup_dir=resolved_backup,
        )
        engine._run_rclone(cmd_args, log_file=active_log)
    update_post_sync_ledger(target.local_path, dry_run)
    return finish_summary(target.name, action, dry_run, active_log, show_summary)
