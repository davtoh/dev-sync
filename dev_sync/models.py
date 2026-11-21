"""Headless JSON-serializable domain contracts for dev_sync."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FileDiffItem:
    """Represents a single differing or orphaned file across local and remote."""

    rel_path: str
    folder: str
    status: str
    local_size: Optional[int] = None
    remote_size: Optional[int] = None
    local_mtime: Optional[str] = None
    remote_mtime: Optional[str] = None
    git_status: str = "non_git"
    git_branch: Optional[str] = None
    origin_machine: Optional[str] = None
    last_modified_by: Optional[str] = None
    ignored_locally: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FileDiffItem:
        """Deserialize from a dictionary."""
        return cls(
            rel_path=str(data["rel_path"]),
            folder=str(data["folder"]),
            status=str(data["status"]),
            local_size=data.get("local_size"),
            remote_size=data.get("remote_size"),
            local_mtime=data.get("local_mtime"),
            remote_mtime=data.get("remote_mtime"),
            git_status=str(data.get("git_status", "non_git")),
            git_branch=data.get("git_branch"),
            origin_machine=data.get("origin_machine"),
            last_modified_by=data.get("last_modified_by"),
            ignored_locally=bool(data.get("ignored_locally", False)),
        )


@dataclass
class FolderDiffNode:
    """Groups differing files under a logical project folder."""

    folder: str
    file_count: int = 0
    total_bytes: int = 0
    git_conflict_count: int = 0
    items: List[FileDiffItem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize folder node and nested items."""
        return {
            "folder": self.folder,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "git_conflict_count": self.git_conflict_count,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FolderDiffNode:
        """Deserialize folder node from a dictionary."""
        items = [FileDiffItem.from_dict(i) for i in data.get("items", [])]
        return cls(
            folder=str(data["folder"]),
            file_count=int(data.get("file_count", len(items))),
            total_bytes=int(data.get("total_bytes", 0)),
            git_conflict_count=int(data.get("git_conflict_count", 0)),
            items=items,
        )


@dataclass
class DiffReport:
    """Complete hierarchical diff report for CLI and GUI consumption."""

    target_name: str
    local_path: str
    remote_path: str
    generated_at: str
    total_files: int = 0
    total_bytes: int = 0
    git_conflict_count: int = 0
    folders: List[FolderDiffNode] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize diff report to a JSON-compatible dictionary."""
        return {
            "target_name": self.target_name,
            "local_path": self.local_path,
            "remote_path": self.remote_path,
            "generated_at": self.generated_at,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "git_conflict_count": self.git_conflict_count,
            "folders": [folder.to_dict() for folder in self.folders],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DiffReport:
        """Deserialize diff report from a dictionary."""
        folders = [FolderDiffNode.from_dict(f) for f in data.get("folders", [])]
        return cls(
            target_name=str(data["target_name"]),
            local_path=str(data["local_path"]),
            remote_path=str(data["remote_path"]),
            generated_at=str(data.get("generated_at", "")),
            total_files=int(data.get("total_files", 0)),
            total_bytes=int(data.get("total_bytes", 0)),
            git_conflict_count=int(data.get("git_conflict_count", 0)),
            folders=folders,
            warnings=[str(w) for w in data.get("warnings", [])],
        )


@dataclass
class LedgerEntry:
    """Provenance record for a synced file across machines."""

    rel_path: str
    size: int = 0
    mtime: str = ""
    sha256: Optional[str] = None
    origin_machine: str = ""
    last_modified_by: str = ""
    updated_at: str = ""
    git_status: str = "non_git"
    git_branch: Optional[str] = None
    git_head: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize ledger entry."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LedgerEntry:
        """Deserialize ledger entry from a dictionary."""
        return cls(
            rel_path=str(data["rel_path"]),
            size=int(data.get("size", 0)),
            mtime=str(data.get("mtime", "")),
            sha256=data.get("sha256"),
            origin_machine=str(data.get("origin_machine", "")),
            last_modified_by=str(data.get("last_modified_by", "")),
            updated_at=str(data.get("updated_at", "")),
            git_status=str(data.get("git_status", "non_git")),
            git_branch=data.get("git_branch"),
            git_head=data.get("git_head"),
        )


@dataclass
class MachineSnapshot:
    """Sharded provenance snapshot owned by a single host machine."""

    machine_id: str
    machine_alias: str = ""
    updated_at: str = ""
    entries: Dict[str, LedgerEntry] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize machine snapshot."""
        return {
            "machine_id": self.machine_id,
            "machine_alias": self.machine_alias,
            "updated_at": self.updated_at,
            "entries": {k: v.to_dict() for k, v in self.entries.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MachineSnapshot:
        """Deserialize machine snapshot from a dictionary."""
        raw_entries = data.get("entries", {})
        entries = {k: LedgerEntry.from_dict(v) for k, v in raw_entries.items()}
        return cls(
            machine_id=str(data["machine_id"]),
            machine_alias=str(data.get("machine_alias", "")),
            updated_at=str(data.get("updated_at", "")),
            entries=entries,
        )


@dataclass
class LocalPrefs:
    """Local-only machine preferences (never synced to remote)."""

    machine_id: str
    machine_alias: str = ""
    ignored_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize local preferences."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LocalPrefs:
        """Deserialize local preferences from a dictionary."""
        return cls(
            machine_id=str(data["machine_id"]),
            machine_alias=str(data.get("machine_alias", "")),
            ignored_paths=[str(p) for p in data.get("ignored_paths", [])],
        )


@dataclass
class PurgeItem:
    """Single file action inside a selective purge plan."""

    rel_path: str
    scope: str = "remote"
    action: str = "trash"
    size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize purge item."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PurgeItem:
        """Deserialize purge item from a dictionary."""
        return cls(
            rel_path=str(data["rel_path"]),
            scope=str(data.get("scope", "remote")),
            action=str(data.get("action", "trash")),
            size=int(data.get("size", 0)),
        )


@dataclass
class PurgePlan:
    """Executable selective purge plan for interactive or headless GUI execution."""

    target_name: str
    created_at: str = ""
    dry_run: bool = False
    items: List[PurgeItem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize purge plan."""
        return {
            "target_name": self.target_name,
            "created_at": self.created_at,
            "dry_run": self.dry_run,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PurgePlan:
        """Deserialize purge plan from a dictionary."""
        items = [PurgeItem.from_dict(i) for i in data.get("items", [])]
        return cls(
            target_name=str(data["target_name"]),
            created_at=str(data.get("created_at", "")),
            dry_run=bool(data.get("dry_run", False)),
            items=items,
        )
