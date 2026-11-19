"""Hierarchical diff analyzer combining rclone, Git guard, and ledger provenance."""

from __future__ import annotations

import datetime
import json
import posixpath
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from dev_sync.config import SyncTarget
from dev_sync.git_guard import (
    RepoState,
    classify_file,
    detect_git_conflict,
    inspect_workspace_repos,
)
from dev_sync.ledger import (
    is_path_ignored_locally,
    load_local_prefs,
    load_merged_ledger,
)
from dev_sync.models import DiffReport, FileDiffItem, FolderDiffNode, LedgerEntry, LocalPrefs

if TYPE_CHECKING:
    from dev_sync.engine import SyncEngine


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_ts(raw: Optional[str]) -> Optional[float]:
    """Parse an ISO-8601 timestamp string to UTC epoch seconds."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _local_file_stat(local_root: Path, rel_path: str) -> Tuple[Optional[int], Optional[str]]:
    """Return (size_bytes, utc_iso_mtime) for a local relative file path."""
    full_path = local_root / Path(*rel_path.split("/"))
    try:
        st = full_path.stat()
    except OSError:
        return None, None
    mtime_iso = datetime.datetime.fromtimestamp(st.st_mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return int(st.st_size), mtime_iso


def parse_combined_check(stdout: str) -> List[Tuple[str, str]]:
    """Parse `rclone check --combined -` output into (symbol, rel_path) pairs."""
    entries: List[Tuple[str, str]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip("\r\n")
        if len(line) < 3 or line[1] != " ":
            continue
        symbol = line[0]
        rel_path = line[2:].strip().replace("\\", "/")
        if rel_path and symbol in ("=", "*", "+", "-", "!"):
            entries.append((symbol, rel_path))
    return entries


def parse_lsjson_map(stdout: str) -> Dict[str, Tuple[Optional[int], Optional[str]]]:
    """Parse `rclone lsjson` output into {rel_path: (size, mod_time)}."""
    if not stdout.strip():
        return {}
    try:
        items = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    result: Dict[str, Tuple[Optional[int], Optional[str]]] = {}
    if not isinstance(items, list):
        return result
    for entry in items:
        if not isinstance(entry, dict) or entry.get("IsDir"):
            continue
        path = str(entry.get("Path", "")).replace("\\", "/").lstrip("/")
        if not path:
            continue
        size = entry.get("Size")
        mod_time = entry.get("ModTime")
        result[path] = (int(size) if size is not None else None, str(mod_time) if mod_time else None)
    return result


def _classify_diff_status(
    symbol: str,
    local_mtime: Optional[str],
    remote_mtime: Optional[str],
) -> str:
    """Resolve semantic diff status from rclone check symbol and timestamps."""
    if symbol == "+":
        return "local_only"
    if symbol == "-":
        return "remote_only"
    if symbol == "!":
        return "error"
    local_ts = _parse_iso_ts(local_mtime)
    remote_ts = _parse_iso_ts(remote_mtime)
    if local_ts is not None and remote_ts is not None:
        if local_ts > remote_ts + 1.0:
            return "local_newer"
        if remote_ts > local_ts + 1.0:
            return "remote_newer"
    return "conflict"


def _build_diff_item(
    symbol: str,
    rel_path: str,
    local_root: Path,
    remote_map: Dict[str, Tuple[Optional[int], Optional[str]]],
    repos: List[RepoState],
    merged_ledger: Dict[str, LedgerEntry],
    prefs: LocalPrefs,
) -> Tuple[FileDiffItem, Optional[str]]:
    """Construct a FileDiffItem and optional Git conflict warning."""
    local_size, local_mtime = _local_file_stat(local_root, rel_path) if symbol in ("*", "+") else (None, None)
    remote_size, remote_mtime = remote_map.get(rel_path, (None, None))
    status = _classify_diff_status(symbol, local_mtime, remote_mtime)
    git_info = classify_file(rel_path, repos)
    ledger_entry = merged_ledger.get(rel_path)
    warning = detect_git_conflict(git_info, ledger_entry)
    folder = posixpath.dirname(rel_path) or "."

    item = FileDiffItem(
        rel_path=rel_path,
        folder=folder,
        status=status,
        local_size=local_size,
        remote_size=remote_size,
        local_mtime=local_mtime,
        remote_mtime=remote_mtime,
        git_status=git_info.git_status,
        git_branch=git_info.git_branch,
        origin_machine=ledger_entry.origin_machine if ledger_entry else None,
        last_modified_by=ledger_entry.last_modified_by if ledger_entry else None,
        ignored_locally=is_path_ignored_locally(rel_path, prefs),
    )
    return item, warning


def _group_into_folders(items: List[FileDiffItem], item_warnings: Dict[str, str]) -> List[FolderDiffNode]:
    """Group FileDiffItems into sorted FolderDiffNode trees."""
    grouped: Dict[str, List[FileDiffItem]] = {}
    for item in items:
        grouped.setdefault(item.folder, []).append(item)

    nodes: List[FolderDiffNode] = []
    for folder in sorted(grouped.keys()):
        folder_items = sorted(grouped[folder], key=lambda x: x.rel_path)
        total_bytes = sum(max(it.local_size or 0, it.remote_size or 0) for it in folder_items)
        conflict_count = sum(1 for it in folder_items if it.rel_path in item_warnings)
        nodes.append(
            FolderDiffNode(
                folder=folder,
                file_count=len(folder_items),
                total_bytes=total_bytes,
                git_conflict_count=conflict_count,
                items=folder_items,
            )
        )
    return nodes


class DiffAnalyzer:
    """Produces hierarchical DiffReport trees combining rclone, Git, and ledger."""

    def __init__(self, engine: SyncEngine):
        self.engine = engine

    def _fetch_remote_lsjson(
        self, target: SyncTarget, sync_filter: Path, exclude_args: List[str]
    ) -> Dict[str, Tuple[Optional[int], Optional[str]]]:
        """Fetch remote file metadata via `rclone lsjson`."""
        from dev_sync.engine import DEFAULT_PACING_FLAGS

        cmd = [
            "lsjson", target.remote_path, "-R", "--files-only",
            *exclude_args, "--filter-from", str(sync_filter), *DEFAULT_PACING_FLAGS,
        ]
        res = self.engine._run_rclone(cmd, stream=False)
        return parse_lsjson_map(res.stdout or "")

    def _fetch_check_entries(
        self, target: SyncTarget, sync_filter: Path, exclude_args: List[str]
    ) -> List[Tuple[str, str]]:
        """Run `rclone check --combined -` between local and remote."""
        from dev_sync.engine import DEFAULT_PACING_FLAGS

        cmd = [
            "check", str(target.local_path), target.remote_path,
            "--combined", "-", *exclude_args, "--filter-from", str(sync_filter), *DEFAULT_PACING_FLAGS,
        ]
        res = self.engine._run_rclone(cmd, stream=False, allow_diff_exit=True)
        return parse_combined_check(res.stdout or "")

    def _assemble_report(
        self,
        target: SyncTarget,
        raw_entries: List[Tuple[str, str]],
        remote_map: Dict[str, Tuple[Optional[int], Optional[str]]],
        repos: List[RepoState],
        merged_ledger: Dict[str, LedgerEntry],
        prefs: LocalPrefs,
    ) -> DiffReport:
        diff_items: List[FileDiffItem] = []
        warnings: List[str] = []
        item_warnings: Dict[str, str] = {}
        for symbol, rel_path in raw_entries:
            if symbol == "=":
                continue
            item, warning = _build_diff_item(
                symbol, rel_path, target.local_path, remote_map, repos, merged_ledger, prefs
            )
            diff_items.append(item)
            if warning:
                warnings.append(warning)
                item_warnings[rel_path] = warning
        folders = _group_into_folders(diff_items, item_warnings)
        return DiffReport(
            target_name=target.name,
            local_path=str(target.local_path),
            remote_path=target.remote_path,
            generated_at=_utc_now_iso(),
            total_files=len(diff_items),
            total_bytes=sum(node.total_bytes for node in folders),
            git_conflict_count=len(item_warnings),
            folders=folders,
            warnings=warnings,
        )

    def analyze(self, target: SyncTarget, extra_excludes: Optional[List[str]] = None) -> DiffReport:
        """Analyze local vs remote differences and return a hierarchical DiffReport."""
        from dev_sync.engine import format_exclude_args

        prefs = load_local_prefs(target.local_path)
        merged_ledger = load_merged_ledger(target.local_path)
        repos = inspect_workspace_repos(target.local_path) if target.local_path.exists() else []
        exclude_args = format_exclude_args(extra_excludes)

        with self.engine._target_filters(target) as (sync_filter, _):
            remote_map = self._fetch_remote_lsjson(target, sync_filter, exclude_args)
            if target.local_path.exists():
                raw_entries = self._fetch_check_entries(target, sync_filter, exclude_args)
            else:
                raw_entries = [("-", path) for path in sorted(remote_map.keys())]

        return self._assemble_report(target, raw_entries, remote_map, repos, merged_ledger, prefs)
