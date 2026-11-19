"""Headless purge, native trash, restore, and empty-trash executor."""

from __future__ import annotations

import datetime
import fnmatch
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from dev_sync.config import SyncTarget
from dev_sync.ledger import ignore_local_paths
from dev_sync.models import PurgeItem, PurgePlan

if TYPE_CHECKING:
    from dev_sync.engine import SyncEngine

REMOTE_TRASH_DIR = ".dev-sync-trash"
LOCAL_TRASH_SUBPATH = Path(".dev-sync") / "trash"


def _trash_timestamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H%M%S")


def _parse_trash_ts(folder_name: str) -> Optional[datetime.datetime]:
    """Parse YYYY-MM-DD_HHMMSS trash folder name to UTC datetime."""
    clean = folder_name.strip().rstrip("/")
    try:
        return datetime.datetime.strptime(clean, "%Y-%m-%d_%H%M%S").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def matches_select_pattern(rel_path: str, pattern: str) -> bool:
    """Match a relative POSIX path against a glob or folder prefix."""
    norm_path = rel_path.replace("\\", "/").lstrip("/")
    norm_pat = pattern.strip().replace("\\", "/").lstrip("/")
    if not norm_pat:
        return False
    if fnmatch.fnmatch(norm_path, norm_pat):
        return True
    prefix = norm_pat.rstrip("/*")
    return bool(prefix) and (norm_path == prefix or norm_path.startswith(prefix + "/"))


def _write_files_from(paths: List[str]) -> Path:
    """Write a temporary UTF-8 --files-from list and return its Path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8", newline="\n")
    try:
        for p in paths:
            tmp.write(p.replace("\\", "/").lstrip("/") + "\n")
    finally:
        tmp.close()
    return Path(tmp.name)


def _move_local_to_trash(local_root: Path, rel_paths: List[str], ts: str, dry_run: bool) -> int:
    """Move local files into .dev-sync/trash/<ts>/."""
    count = 0
    for rel in rel_paths:
        src = local_root / Path(*rel.split("/"))
        if not src.is_file():
            continue
        dst = local_root / LOCAL_TRASH_SUBPATH / ts / Path(*rel.split("/"))
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        count += 1
    return count


def _delete_local_permanent(local_root: Path, rel_paths: List[str], dry_run: bool) -> int:
    """Permanently delete local files."""
    count = 0
    for rel in rel_paths:
        src = local_root / Path(*rel.split("/"))
        if not src.is_file():
            continue
        if not dry_run:
            src.unlink()
        count += 1
    return count


class PurgeExecutor:
    """Executes PurgePlan operations, trash restores, and trash expiration."""

    def __init__(self, engine: SyncEngine):
        self.engine = engine

    def _run_remote_files_cmd(
        self, subcmd: List[str], rel_paths: List[str], dry_run: bool
    ) -> int:
        """Execute an rclone command with --files-from for a batch of relative paths."""
        from dev_sync.engine import DEFAULT_PACING_FLAGS

        if not rel_paths:
            return 0
        files_from = _write_files_from(rel_paths)
        try:
            cmd = [*subcmd, "--files-from", str(files_from), *DEFAULT_PACING_FLAGS]
            if dry_run:
                cmd.append("--dry-run")
            self.engine._run_rclone(cmd, stream=False)
        finally:
            files_from.unlink(missing_ok=True)
        return len(rel_paths)

    def execute_plan(self, target: SyncTarget, plan: PurgePlan) -> Dict[str, Any]:
        """Execute a PurgePlan against remote and/or local targets."""
        ts = _trash_timestamp()
        summary = {"trashed": 0, "deleted": 0, "ignored_locally": 0, "dry_run": plan.dry_run}

        ignore_items = [it.rel_path for it in plan.items if it.action == "ignore_local"]
        if ignore_items:
            if not plan.dry_run:
                ignore_local_paths(target.local_path, ignore_items)
            summary["ignored_locally"] += len(ignore_items)

        remote_trash = [it.rel_path for it in plan.items if it.action == "trash" and it.scope in ("remote", "both")]
        local_trash = [it.rel_path for it in plan.items if it.action == "trash" and it.scope in ("local", "both")]
        if remote_trash:
            trash_dst = f"{target.remote_path}/{REMOTE_TRASH_DIR}/{ts}"
            summary["trashed"] += self._run_remote_files_cmd(["move", target.remote_path, trash_dst], remote_trash, plan.dry_run)
        if local_trash:
            summary["trashed"] += _move_local_to_trash(target.local_path, local_trash, ts, plan.dry_run)

        remote_perm = [it.rel_path for it in plan.items if it.action == "permanent" and it.scope in ("remote", "both")]
        local_perm = [it.rel_path for it in plan.items if it.action == "permanent" and it.scope in ("local", "both")]
        if remote_perm:
            summary["deleted"] += self._run_remote_files_cmd(["delete", target.remote_path], remote_perm, plan.dry_run)
        if local_perm:
            summary["deleted"] += _delete_local_permanent(target.local_path, local_perm, plan.dry_run)

        return summary

    def _restore_remote(self, target: SyncTarget, select_pattern: str, dry_run: bool) -> int:
        """Restore matching files from remote .dev-sync-trash/<ts>/."""
        from dev_sync.engine import DEFAULT_PACING_FLAGS

        trash_root = f"{target.remote_path}/{REMOTE_TRASH_DIR}"
        try:
            res = self.engine._run_rclone(["lsf", trash_root, "-R", "--files-only", *DEFAULT_PACING_FLAGS], stream=False)
        except Exception:
            return 0
        latest_by_rel: Dict[str, str] = {}
        for line in (res.stdout or "").splitlines():
            clean = line.strip().replace("\\", "/")
            if "/" not in clean:
                continue
            ts_part, rel_part = clean.split("/", 1)
            if matches_select_pattern(rel_part, select_pattern):
                if rel_part not in latest_by_rel or ts_part > latest_by_rel[rel_part]:
                    latest_by_rel[rel_part] = ts_part

        by_ts: Dict[str, List[str]] = {}
        for rel_part, ts_part in latest_by_rel.items():
            by_ts.setdefault(ts_part, []).append(rel_part)
        restored = 0
        for ts_part, rels in sorted(by_ts.items()):
            restored += self._run_remote_files_cmd(["move", f"{trash_root}/{ts_part}", target.remote_path], rels, dry_run)
        return restored

    def _restore_local(
        self,
        target: SyncTarget,
        select_pattern: str,
        dry_run: bool,
        overwrite: bool = False,
        skipped: Optional[List[str]] = None,
    ) -> int:
        """Restore matching files from local .dev-sync/trash/<ts>/."""
        trash_root = target.local_path / LOCAL_TRASH_SUBPATH
        if not trash_root.is_dir():
            return 0
        latest_by_rel: Dict[str, Tuple[str, Path]] = {}
        for ts_dir in sorted(trash_root.iterdir()):
            if not ts_dir.is_dir():
                continue
            for file_path in ts_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                rel_part = file_path.relative_to(ts_dir).as_posix()
                if matches_select_pattern(rel_part, select_pattern):
                    if rel_part not in latest_by_rel or ts_dir.name > latest_by_rel[rel_part][0]:
                        latest_by_rel[rel_part] = (ts_dir.name, file_path)

        restored = 0
        for rel_part, (_, src_file) in latest_by_rel.items():
            dst = target.local_path / Path(*rel_part.split("/"))
            if (dst.exists() or dst.is_symlink()) and not overwrite:
                if skipped is not None:
                    skipped.append(rel_part)
                continue
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src_file), str(dst))
            restored += 1
        return restored

    def restore(
        self,
        target: SyncTarget,
        select_pattern: str = "*",
        scope: str = "both",
        dry_run: bool = False,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Restore trashed files matching select_pattern."""
        remote_count = self._restore_remote(target, select_pattern, dry_run) if scope in ("remote", "both") else 0
        skipped_local: List[str] = []
        local_count = (
            self._restore_local(target, select_pattern, dry_run, overwrite=overwrite, skipped=skipped_local)
            if scope in ("local", "both")
            else 0
        )
        result: Dict[str, Any] = {"remote_restored": remote_count, "local_restored": local_count}
        if skipped_local:
            result["local_skipped"] = len(skipped_local)
            result["skipped_paths"] = skipped_local
        return result

    def _is_expired_trash(self, folder_name: str, older_than_days: Optional[int], now: datetime.datetime) -> bool:
        if older_than_days is None:
            return True
        parsed = _parse_trash_ts(folder_name)
        if parsed is None:
            return False
        return (now - parsed) >= datetime.timedelta(days=older_than_days)

    def _empty_remote_trash(
        self, target: SyncTarget, older_than_days: Optional[int], now: datetime.datetime, dry_run: bool
    ) -> int:
        from dev_sync.engine import DEFAULT_PACING_FLAGS

        trash_remote = f"{target.remote_path}/{REMOTE_TRASH_DIR}"
        purged = 0
        try:
            res = self.engine._run_rclone(["lsf", trash_remote, "--dirs-only", *DEFAULT_PACING_FLAGS], stream=False)
            for line in (res.stdout or "").splitlines():
                folder = line.strip().rstrip("/")
                if folder and self._is_expired_trash(folder, older_than_days, now):
                    cmd = ["purge", f"{trash_remote}/{folder}", *DEFAULT_PACING_FLAGS]
                    if dry_run:
                        cmd.append("--dry-run")
                    self.engine._run_rclone(cmd, stream=False)
                    purged += 1
        except Exception:
            pass
        return purged

    def empty_trash(
        self,
        target: SyncTarget,
        older_than_days: Optional[int] = None,
        scope: str = "both",
        dry_run: bool = False,
    ) -> Dict[str, int]:
        """Purge trash folders older than N days (or all if older_than_days is None)."""
        now = datetime.datetime.now(datetime.timezone.utc)
        remote_purged = self._empty_remote_trash(target, older_than_days, now, dry_run) if scope in ("remote", "both") else 0
        local_purged = 0
        if scope in ("local", "both"):
            trash_local = target.local_path / LOCAL_TRASH_SUBPATH
            if trash_local.is_dir():
                for child in sorted(trash_local.iterdir()):
                    if child.is_dir() and self._is_expired_trash(child.name, older_than_days, now):
                        if not dry_run:
                            shutil.rmtree(child)
                        local_purged += 1
        return {"remote_purged": remote_purged, "local_purged": local_purged}
