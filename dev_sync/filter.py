"""Filter rule parser and dynamic inverted cleanup compiler for rclone."""

from __future__ import annotations

import fnmatch
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

from dev_sync.config import SyncTarget, find_target_ignore_file


class FilterRule(tuple):
    """Represents an individual filter rule with action, pattern, and semantic policy.

    Inherits from tuple to provide 100% transparent backwards compatibility
    with existing code expecting 2-tuples (action, pattern).
    """

    _is_local_only: bool

    def __new__(cls, action: str, pattern: str, is_local_only: bool = False):
        obj = super().__new__(cls, (action, pattern))
        obj._is_local_only = is_local_only
        return obj

    @property
    def action(self) -> str:
        return self[0]

    @property
    def pattern(self) -> str:
        return self[1]

    @property
    def is_local_only(self) -> bool:
        return self._is_local_only


def normalize_pattern(pattern: str) -> str:
    """Normalize glob patterns for rclone compatibility.

    In rclone filter syntax:
    - Patterns starting without '/' match against any path component (root or nested).
    - Patterns starting with '**/dir/**' only match when preceded by a parent directory
      (regex '(^|/).*/dir/.*'), which fails to match root-level 'dir/'.
    - Stripping a leading '**/' ensures rclone matches both at root and any nested depth.
    """
    pattern = pattern.strip()
    if pattern.startswith("**/"):
        return pattern[3:]
    return pattern


def parse_filter_rules(content: str) -> List[FilterRule]:
    """Parse raw filter file content into a list of FilterRule objects.

    Action is '+' (include) or '-' (exclude).
    Supports semantic directives:
    - Preceding tags: '# @local_only', '# @local-only', '# @archive'
    - Block scope tags: '# @scope: local_only', '# @scope: bloat'
    - Inline prefixes: '-! <pattern>', '- [local_only] <pattern>', '- @local_only <pattern>'
    """
    rules: List[FilterRule] = []
    next_is_local_only = False
    block_local_only = False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            next_is_local_only = False
            continue

        if line.startswith("#"):
            lower = line.lower()
            if any(t in lower for t in ["@local_only", "@local-only", "@archive"]):
                next_is_local_only = True
            elif "@scope: local_only" in lower or "@scope: local-only" in lower:
                block_local_only = True
            elif "@scope: bloat" in lower or "@scope: all" in lower or "@scope: default" in lower:
                block_local_only = False
            continue

        is_local = next_is_local_only or block_local_only
        next_is_local_only = False

        if line.startswith("-!"):
            pattern = normalize_pattern(line[2:].strip())
            if pattern:
                rules.append(FilterRule(action="-", pattern=pattern, is_local_only=True))
        elif line.startswith("+"):
            pattern = normalize_pattern(line[1:].strip())
            if pattern:
                rules.append(FilterRule(action="+", pattern=pattern, is_local_only=False))
        elif line.startswith("-"):
            clean_body = line[1:].strip()
            if clean_body.startswith("[local_only]"):
                clean_body = clean_body[len("[local_only]"):].strip()
                is_local = True
            elif clean_body.startswith("@local_only"):
                clean_body = clean_body[len("@local_only"):].strip()
                is_local = True

            pattern = normalize_pattern(clean_body)
            if pattern:
                rules.append(FilterRule(action="-", pattern=pattern, is_local_only=is_local))

    return rules


def compile_cleanup_filter(rules: List[Any]) -> str:
    """Compile an inverted filter specifically for safe remote cleanup.

    Transformation logic:
    1. '+ pattern' (explicit preservation, e.g. .env.example) -> '- pattern' (protect from deletion).
    2. '- pattern' with is_local_only=True -> '- pattern' (protect from deletion on remote!).
    3. '- pattern' standard bloat -> '+ pattern' (target for deletion).
    4. Terminal guard: '- **' (protect all other source files from deletion).
    """
    lines: List[str] = [
        "# ==============================================================================",
        "# DYNAMICALLY GENERATED CLEANUP FILTER - DO NOT EDIT MANUALLY",
        "# Inverts ignore rules: excludes become targets for deletion (+),",
        "# while preserved files (-), local-only archives (-),",
        "# and all other source code (- **) are strictly protected.",
        "# ==============================================================================",
    ]

    # First emit all protection rules (-) derived from '+' entries in source
    for rule in rules:
        action, pattern = rule
        if action == "+":
            lines.append(f"- {pattern}")

    # Emit protection rules (-) for local-only exclusions (# @local_only or -!)
    for rule in rules:
        action, pattern = rule
        if action == "-" and getattr(rule, "is_local_only", False):
            lines.append(f"- {pattern}")

    # Then emit deletion targets (+) derived from standard bloat exclusions
    for rule in rules:
        action, pattern = rule
        if action == "-" and not getattr(rule, "is_local_only", False):
            lines.append(f"+ {pattern}")

    # Terminal guard: Never delete any file that was not explicitly targeted
    lines.append("# Terminal guard: Protect all remaining unmentioned files")
    lines.append("- **")

    return "\n".join(lines) + "\n"


def compile_normalized_sync_filter(rules: List[Any]) -> str:
    """Compile a normalized filter for standard sync operations (push/pull/check).

    Ensures that patterns without leading '**/' match accurately at both root and nested levels.
    """
    lines: List[str] = [
        "# ==============================================================================",
        "# NORMALIZED SYNC FILTER - MATCHES AT ROOT AND NESTED DEPTHS",
        "# ==============================================================================",
    ]
    for rule in rules:
        action, pattern = rule
        lines.append(f"{action} {pattern}")
    return "\n".join(lines) + "\n"


def extract_bloat_roots(raw_paths: List[str], rules: List[Any]) -> List[str]:
    """Extract and deduplicate root bloat directory paths matching ignore rules.

    Distinguishes true bloat roots (e.g. 'node_modules', 'build', '.venv') from
    normal source parent directories traversed during directory scanning.
    Ignores rules flagged as local-only to protect them from directory-level purges.
    Collapses nested bloat paths into their top-level root.
    """
    dir_tokens: List[str] = []
    file_tokens: List[str] = []

    for rule in rules:
        action, pat = rule
        if action == "-" and not getattr(rule, "is_local_only", False):
            clean = pat.strip()
            if clean.endswith("/**"):
                dir_tokens.append(clean[:-3])
            elif clean.endswith("/*"):
                dir_tokens.append(clean[:-2])
            else:
                file_tokens.append(clean)

    bloat_roots: set[str] = set()

    for raw_p in raw_paths:
        p = raw_p.strip().replace("\\", "/").rstrip("/")
        if not p:
            continue

        parts = p.split("/")
        for i, part in enumerate(parts):
            matched = False
            for dt in dir_tokens:
                if fnmatch.fnmatch(part, dt):
                    bloat_roots.add("/".join(parts[: i + 1]) + "/")
                    matched = True
                    break
            if matched:
                break

            for ft in file_tokens:
                if fnmatch.fnmatch(part, ft):
                    is_dir = i < len(parts) - 1 or raw_p.strip().endswith(("/", "\\"))
                    bloat_roots.add("/".join(parts[: i + 1]) + ("/" if is_dir else ""))
                    matched = True
                    break
            if matched:
                break

    return sorted(bloat_roots)


def resolve_target_filters(
    global_filter_path: Path,
    target: SyncTarget,
) -> Tuple[Path, Path, bool]:
    """Resolve sync and cleanup filter paths for a specific target.

    Merges global filter, target custom_filter, target-level .syncignore,
    and target-configured excludes.
    Returns (sync_filter_path, cleanup_filter_path, is_temporary).
    """
    global_content = global_filter_path.read_text(encoding="utf-8")
    rules = parse_filter_rules(global_content)

    has_target_custom = False

    # 1. Target custom filter
    if target.custom_filter and target.custom_filter.is_file():
        custom_content = target.custom_filter.read_text(encoding="utf-8")
        rules.extend(parse_filter_rules(custom_content))
        has_target_custom = True

    # 2. Target-level .syncignore
    local_ignore = find_target_ignore_file(target.local_path)
    if local_ignore and local_ignore.is_file():
        local_content = local_ignore.read_text(encoding="utf-8")
        rules.extend(parse_filter_rules(local_content))
        has_target_custom = True

    # 3. Target-configured excludes
    if target.excludes:
        for pat in target.excludes:
            clean = normalize_pattern(pat)
            if clean:
                rules.append(FilterRule(action="-", pattern=clean, is_local_only=False))
        has_target_custom = True

    with tempfile.NamedTemporaryFile("w+", suffix=f"_{target.name}_cleanup.txt", delete=False) as tf_clean:
        cleanup_path = Path(tf_clean.name)
    cleanup_path.write_text(compile_cleanup_filter(rules), encoding="utf-8")

    if not has_target_custom:
        return global_filter_path, cleanup_path, False

    with tempfile.NamedTemporaryFile("w+", suffix=f"_{target.name}_sync.txt", delete=False) as tf_sync:
        sync_path = Path(tf_sync.name)
    sync_path.write_text(compile_normalized_sync_filter(rules), encoding="utf-8")
    return sync_path, cleanup_path, True


def generate_cleanup_filter_file(source_file: Path, target_file: Path) -> Path:
    """Read source ignore file and write the compiled cleanup filter to target_file."""
    content = source_file.read_text(encoding="utf-8")
    rules = parse_filter_rules(content)
    cleanup_content = compile_cleanup_filter(rules)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(cleanup_content, encoding="utf-8")
    return target_file
