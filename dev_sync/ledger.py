"""Conflict-free sharded provenance ledger and local preference management."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dev_sync.git_guard import FileGitInfo
from dev_sync.models import LedgerEntry, LocalPrefs, MachineSnapshot

DEV_SYNC_DIR_NAME = ".dev-sync"
MACHINES_DIR_NAME = "machines"
LEDGER_FILE_NAME = "ledger.json"
LOCAL_PREFS_FILE_NAME = "local-prefs.json"


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_rel_path(path: str) -> str:
    """Normalize a relative path or pattern to POSIX forward slashes."""
    return path.replace("\\", "/").strip().lstrip("./")


def sanitize_machine_id(raw_name: str) -> str:
    """Normalize a hostname or alias into a safe filename-friendly machine ID."""
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", raw_name.strip().lower()).strip("-")
    return cleaned or "unknown-host"


def resolve_machine_id(prefs: Optional[LocalPrefs] = None) -> str:
    """Resolve the current host's machine ID from local prefs or system hostname."""
    if prefs and prefs.machine_alias:
        return sanitize_machine_id(prefs.machine_alias)
    if prefs and prefs.machine_id:
        return sanitize_machine_id(prefs.machine_id)
    try:
        return sanitize_machine_id(socket.gethostname())
    except OSError:
        return "unknown-host"


def get_dev_sync_dir(root: Path) -> Path:
    """Return the .dev-sync metadata directory for a target root."""
    return root / DEV_SYNC_DIR_NAME


def _write_json(path: Path, payload: object) -> Path:
    """Write a JSON payload with UTF-8 encoding and LF newlines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    path.write_bytes(text.encode("utf-8"))
    return path


def _read_json(path: Path) -> Optional[dict]:
    """Read and parse a JSON object file, returning None if missing or invalid."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_local_prefs(root: Path) -> LocalPrefs:
    """Load .dev-sync/local-prefs.json or return default preferences."""
    prefs_path = get_dev_sync_dir(root) / LOCAL_PREFS_FILE_NAME
    data = _read_json(prefs_path)
    if data and "machine_id" in data:
        return LocalPrefs.from_dict(data)
    return LocalPrefs(machine_id=resolve_machine_id())


def save_local_prefs(root: Path, prefs: LocalPrefs) -> Path:
    """Persist .dev-sync/local-prefs.json."""
    prefs_path = get_dev_sync_dir(root) / LOCAL_PREFS_FILE_NAME
    return _write_json(prefs_path, prefs.to_dict())


def ignore_local_paths(root: Path, patterns: List[str]) -> LocalPrefs:
    """Add folder or file patterns to this machine's local opt-out preferences."""
    prefs = load_local_prefs(root)
    existing = set(prefs.ignored_paths)
    for raw in patterns:
        norm = _normalize_rel_path(raw)
        if norm and norm not in existing:
            prefs.ignored_paths.append(norm)
            existing.add(norm)
    save_local_prefs(root, prefs)
    return prefs


def unignore_local_paths(root: Path, patterns: List[str]) -> LocalPrefs:
    """Remove folder or file patterns from this machine's local opt-out preferences."""
    prefs = load_local_prefs(root)
    to_remove = {_normalize_rel_path(p) for p in patterns if _normalize_rel_path(p)}
    prefs.ignored_paths = [p for p in prefs.ignored_paths if _normalize_rel_path(p) not in to_remove]
    save_local_prefs(root, prefs)
    return prefs


def is_path_ignored_locally(rel_path: str, prefs: LocalPrefs) -> bool:
    """Return True if rel_path matches any pattern in LocalPrefs.ignored_paths."""
    norm = _normalize_rel_path(rel_path)
    if not norm or not prefs.ignored_paths:
        return False
    for raw_pat in prefs.ignored_paths:
        pat = _normalize_rel_path(raw_pat)
        if not pat:
            continue
        if pat.endswith("/**") or pat.endswith("/"):
            prefix = pat[:-3] if pat.endswith("/**") else pat[:-1]
            if norm == prefix or norm.startswith(f"{prefix}/"):
                return True
        elif norm == pat or norm.startswith(f"{pat}/"):
            return True
        elif fnmatch.fnmatch(norm, pat):
            return True
    return False


def load_machine_snapshot(root: Path, machine_id: str) -> Optional[MachineSnapshot]:
    """Load a single host's sharded snapshot from .dev-sync/machines/<machine_id>.json."""
    path = get_dev_sync_dir(root) / MACHINES_DIR_NAME / f"{sanitize_machine_id(machine_id)}.json"
    data = _read_json(path)
    if data and "machine_id" in data:
        return MachineSnapshot.from_dict(data)
    return None


def save_machine_snapshot(root: Path, snapshot: MachineSnapshot) -> Path:
    """Save a host's sharded snapshot to .dev-sync/machines/<machine_id>.json."""
    safe_id = sanitize_machine_id(snapshot.machine_id)
    path = get_dev_sync_dir(root) / MACHINES_DIR_NAME / f"{safe_id}.json"
    return _write_json(path, snapshot.to_dict())


def load_all_machine_snapshots(root: Path) -> List[MachineSnapshot]:
    """Load all sharded machine snapshots sorted by machine_id."""
    machines_dir = get_dev_sync_dir(root) / MACHINES_DIR_NAME
    if not machines_dir.is_dir():
        return []
    snapshots: List[MachineSnapshot] = []
    for json_file in sorted(machines_dir.glob("*.json")):
        data = _read_json(json_file)
        if data and "machine_id" in data:
            snapshots.append(MachineSnapshot.from_dict(data))
    return snapshots


def _entry_sort_key(entry: LedgerEntry, snapshot_updated_at: str) -> Tuple[str, str]:
    """Return comparable timestamp tuple for conflict-free entry ordering."""
    return (entry.updated_at or entry.mtime or snapshot_updated_at, snapshot_updated_at)


def compile_merged_ledger(snapshots: List[MachineSnapshot]) -> Dict[str, LedgerEntry]:
    """Merge sharded MachineSnapshots into a unified rel_path -> LedgerEntry map."""
    grouped: Dict[str, List[Tuple[Tuple[str, str], LedgerEntry]]] = {}
    for snap in snapshots:
        for rel_path, entry in snap.entries.items():
            norm = _normalize_rel_path(rel_path)
            grouped.setdefault(norm, []).append(
                (_entry_sort_key(entry, snap.updated_at), entry)
            )

    merged: Dict[str, LedgerEntry] = {}
    for norm, records in sorted(grouped.items()):
        records.sort(key=lambda item: item[0])
        earliest = records[0][1]
        latest = records[-1][1]
        origin = earliest.origin_machine or earliest.last_modified_by or latest.origin_machine
        merged[norm] = LedgerEntry(
            rel_path=norm,
            size=latest.size,
            mtime=latest.mtime,
            sha256=latest.sha256,
            origin_machine=origin,
            last_modified_by=latest.last_modified_by or latest.origin_machine,
            updated_at=latest.updated_at,
            git_status=latest.git_status,
            git_branch=latest.git_branch,
            git_head=latest.git_head,
        )
    return merged


def save_merged_ledger(root: Path, entries: Dict[str, LedgerEntry]) -> Path:
    """Write the compiled merged ledger to .dev-sync/ledger.json."""
    ledger_path = get_dev_sync_dir(root) / LEDGER_FILE_NAME
    payload = {
        "updated_at": _utc_now_iso(),
        "entry_count": len(entries),
        "entries": {k: v.to_dict() for k, v in sorted(entries.items())},
    }
    return _write_json(ledger_path, payload)


def load_merged_ledger(root: Path) -> Dict[str, LedgerEntry]:
    """Load .dev-sync/ledger.json or compile from sharded snapshots if absent."""
    ledger_path = get_dev_sync_dir(root) / LEDGER_FILE_NAME
    data = _read_json(ledger_path)
    if data and isinstance(data.get("entries"), dict):
        return {
            _normalize_rel_path(k): LedgerEntry.from_dict(v)
            for k, v in data["entries"].items()
            if isinstance(v, dict)
        }
    snapshots = load_all_machine_snapshots(root)
    return compile_merged_ledger(snapshots) if snapshots else {}


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file in fixed-size chunks."""
    digest = hashlib.sha256()
    with file_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_ledger_entry(
    root: Path,
    rel_path: str,
    machine_id: str,
    existing_origin: Optional[str] = None,
    git_info: Optional[FileGitInfo] = None,
    compute_hash: bool = False,
) -> Optional[LedgerEntry]:
    """Build a LedgerEntry for a local file under root, or None if missing."""
    norm = _normalize_rel_path(rel_path)
    full_path = root / norm
    if not full_path.is_file():
        return None
    stat = full_path.stat()
    mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return LedgerEntry(
        rel_path=norm,
        size=stat.st_size,
        mtime=mtime_iso,
        sha256=_compute_sha256(full_path) if compute_hash else None,
        origin_machine=existing_origin or machine_id,
        last_modified_by=machine_id,
        updated_at=_utc_now_iso(),
        git_status=git_info.git_status if git_info else "non_git",
        git_branch=git_info.git_branch if git_info else None,
        git_head=git_info.git_head if git_info else None,
    )


def record_local_snapshot(
    root: Path,
    rel_paths: List[str],
    git_infos: Optional[Dict[str, FileGitInfo]] = None,
    compute_hash: bool = False,
) -> MachineSnapshot:
    """Update this machine's sharded snapshot and recompile .dev-sync/ledger.json."""
    prefs = load_local_prefs(root)
    machine_id = resolve_machine_id(prefs)
    existing_snap = load_machine_snapshot(root, machine_id)
    existing_merged = load_merged_ledger(root)
    entries: Dict[str, LedgerEntry] = dict(existing_snap.entries) if existing_snap else {}

    for raw in rel_paths:
        norm = _normalize_rel_path(raw)
        prior = existing_merged.get(norm) or entries.get(norm)
        origin = prior.origin_machine if prior and prior.origin_machine else None
        git_info = git_infos.get(norm) if git_infos else None
        entry = build_ledger_entry(
            root, norm, machine_id, existing_origin=origin, git_info=git_info, compute_hash=compute_hash
        )
        if entry is not None:
            entries[norm] = entry

    snapshot = MachineSnapshot(
        machine_id=machine_id,
        machine_alias=prefs.machine_alias,
        updated_at=_utc_now_iso(),
        entries=entries,
    )
    save_machine_snapshot(root, snapshot)
    merged = compile_merged_ledger(load_all_machine_snapshots(root))
    save_merged_ledger(root, merged)
    return snapshot
