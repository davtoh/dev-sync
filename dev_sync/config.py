"""Configuration parser and portable path resolver for dev_sync."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class SyncTarget:
    """Represents a validated synchronization target mapping."""

    name: str
    raw_local: str
    local_path: Path
    remote_path: str
    excludes: tuple[str, ...] = ()
    custom_filter: Optional[Path] = None


def find_target_ignore_file(local_path: Path) -> Optional[Path]:
    """Check for local target-level ignore file (.syncignore or dev-sync-ignore.txt)."""
    if not local_path.exists():
        return None
    for candidate in [".syncignore", "dev-sync-ignore.txt"]:
        target_file = local_path / candidate
        if target_file.is_file():
            return target_file
    return None


def get_default_local_base(repo_root: Optional[Path] = None) -> Path:
    """Determine the default local base directory.

    Priority:
    1. DEV_SYNC_BASE environment variable if set.
    2. Parent directory of the dev-sync repository (e.g. dev/ if repo is dev/dev-sync).
    """
    env_base = os.environ.get("DEV_SYNC_BASE")
    if env_base:
        return Path(env_base).expanduser().resolve()

    if repo_root is None:
        # Resolve assuming dev_sync is inside repo root
        repo_root = Path(__file__).resolve().parent.parent

    return repo_root.parent.resolve()


def resolve_local_path(raw_local: str, local_base: Optional[Path] = None) -> Path:
    """Resolve a raw local path string into an absolute, normalized Path.

    Expands:
    - $LOCAL_BASE / ${LOCAL_BASE} -> local_base
    - $HOME / ${HOME} -> user home directory
    - ~ -> user home directory
    - Any active system environment variable ($VAR or %VAR%)
    Relative paths are resolved relative to local_base.
    """
    if local_base is None:
        local_base = get_default_local_base()

    home = Path.home().resolve()

    expanded = raw_local.strip()
    expanded = expanded.replace("${LOCAL_BASE}", str(local_base)).replace("$LOCAL_BASE", str(local_base))
    expanded = expanded.replace("${HOME}", str(home)).replace("$HOME", str(home))

    # Expand any remaining system environment variables
    expanded = os.path.expandvars(expanded)

    # Expand tilde ~
    expanded = os.path.expanduser(expanded)

    path = Path(expanded)
    if not path.is_absolute():
        path = (local_base / path).resolve()
    else:
        path = path.resolve()

    return path


def create_target(
    name: str,
    raw_local: str,
    remote_path: str,
    repo_root: Optional[Path] = None,
    excludes: tuple[str, ...] = (),
    custom_filter: Optional[Path] = None,
) -> SyncTarget:
    """Create a validated SyncTarget instance from target_name, raw_local, and remote_path."""
    name_clean = name.strip()
    raw_local_clean = raw_local.strip()
    remote_clean = remote_path.strip()

    if not name_clean:
        raise ValueError("Target name cannot be empty")
    if not raw_local_clean:
        raise ValueError(f"Local path for target '{name_clean}' cannot be empty")
    if not remote_clean:
        raise ValueError(f"Remote path for target '{name_clean}' cannot be empty")

    local_base = get_default_local_base(repo_root)
    resolved_local = resolve_local_path(raw_local_clean, local_base)

    return SyncTarget(
        name=name_clean,
        raw_local=raw_local_clean,
        local_path=resolved_local,
        remote_path=remote_clean,
        excludes=excludes,
        custom_filter=custom_filter,
    )


def load_targets(config_path: Path, repo_root: Optional[Path] = None) -> Dict[str, SyncTarget]:
    """Parse sync-targets.conf and return a mapping of name -> SyncTarget.

    If config_path is missing but a .example sibling exists, copies the example.
    """
    if not config_path.exists():
        example_path = config_path.with_name(f"{config_path.name}.example")
        if example_path.exists():
            shutil.copy(example_path, config_path)
        else:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

    content = config_path.read_text(encoding="utf-8")
    targets: Dict[str, SyncTarget] = {}

    for line_num, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            raise ValueError(f"Invalid format at {config_path}:{line_num}: missing '=' separator")

        name, mapping = line.split("=", 1)
        name = name.strip()

        if "|" not in mapping:
            raise ValueError(f"Invalid format at {config_path}:{line_num}: mapping must contain '|' (<local>|<remote>)")

        parts = [p.strip() for p in mapping.split("|")]
        raw_local, raw_remote = parts[0], parts[1]

        target_excludes: list[str] = []
        custom_filter_path: Optional[Path] = None

        for opt in parts[2:]:
            if opt.startswith("exclude=") or opt.startswith("excludes="):
                _, val = opt.split("=", 1)
                for pat in val.split(";"):
                    clean_pat = pat.strip()
                    if clean_pat:
                        target_excludes.append(clean_pat)
            elif opt.startswith("filter="):
                _, val = opt.split("=", 1)
                clean_val = val.strip()
                if clean_val:
                    cf = Path(clean_val)
                    if not cf.is_absolute() and repo_root:
                        cf = repo_root / cf
                    custom_filter_path = cf

        target = create_target(
            name=name,
            raw_local=raw_local,
            remote_path=raw_remote,
            repo_root=repo_root,
            excludes=tuple(target_excludes),
            custom_filter=custom_filter_path,
        )
        targets[target.name] = target

    return targets
