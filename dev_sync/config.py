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


def create_target(name: str, raw_local: str, remote_path: str, repo_root: Optional[Path] = None) -> SyncTarget:
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

        raw_local, raw_remote = mapping.split("|", 1)
        target = create_target(name, raw_local, raw_remote, repo_root)
        targets[target.name] = target

    return targets
