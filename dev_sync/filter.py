"""Filter rule parser and dynamic inverted cleanup compiler for rclone."""

from __future__ import annotations

import fnmatch
import tempfile
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


def _update_scope_directive(line: str, next_local: bool, block_local: bool) -> Tuple[bool, bool]:
    """Update local-only directive flags from a comment line."""
    lower = line.lower()
    if any(t in lower for t in ["@local_only", "@local-only", "@archive"]):
        return True, block_local
    if "@scope: local_only" in lower or "@scope: local-only" in lower:
        return next_local, True
    if "@scope: bloat" in lower or "@scope: all" in lower or "@scope: default" in lower:
        return next_local, False
    return next_local, block_local


def _parse_rule_line(line: str, is_local: bool) -> Optional[FilterRule]:
    """Parse a single non-comment filter rule line into a FilterRule."""
    if line.startswith("-!"):
        pattern = normalize_pattern(line[2:].strip())
        return FilterRule(action="-", pattern=pattern, is_local_only=True) if pattern else None
    if line.startswith("+"):
        pattern = normalize_pattern(line[1:].strip())
        return FilterRule(action="+", pattern=pattern, is_local_only=False) if pattern else None
    if line.startswith("-"):
        clean_body = line[1:].strip()
        for tag in ("[local_only]", "@local_only"):
            if clean_body.startswith(tag):
                clean_body = clean_body[len(tag):].strip()
                is_local = True
        pattern = normalize_pattern(clean_body)
        return FilterRule(action="-", pattern=pattern, is_local_only=is_local) if pattern else None
    return None


def parse_filter_rules(content: str) -> List[FilterRule]:
    """Parse raw filter file content into a list of FilterRule objects."""
    rules: List[FilterRule] = []
    next_is_local_only = False
    block_local_only = False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            next_is_local_only = False
            continue
        if line.startswith("#"):
            next_is_local_only, block_local_only = _update_scope_directive(line, next_is_local_only, block_local_only)
            continue

        rule = _parse_rule_line(line, next_is_local_only or block_local_only)
        next_is_local_only = False
        if rule is not None:
            rules.append(rule)

    return rules


_CLEANUP_HEADER = [
    "# ==============================================================================",
    "# DYNAMICALLY GENERATED CLEANUP FILTER - DO NOT EDIT MANUALLY",
    "# Inverts ignore rules: excludes become targets for deletion (+),",
    "# while preserved files (-), local-only archives (-),",
    "# and all other source code (- **) are strictly protected.",
    "# ==============================================================================",
]


def compile_cleanup_filter(rules: List[Any]) -> str:
    """Compile an inverted filter specifically for safe remote cleanup."""
    lines = list(_CLEANUP_HEADER)
    for action, pattern in rules:
        if action == "+":
            lines.append(f"- {pattern}")
    for rule in rules:
        action, pattern = rule
        if action == "-" and getattr(rule, "is_local_only", False):
            lines.append(f"- {pattern}")
    for rule in rules:
        action, pattern = rule
        if action == "-" and not getattr(rule, "is_local_only", False):
            lines.append(f"+ {pattern}")
    lines.extend(["# Terminal guard: Protect all remaining unmentioned files", "- **"])
    return "\n".join(lines) + "\n"


def compile_normalized_sync_filter(rules: List[Any]) -> str:
    """Compile a normalized filter for standard sync operations (push/pull/check)."""
    lines: List[str] = [
        "# ==============================================================================",
        "# NORMALIZED SYNC FILTER - MATCHES AT ROOT AND NESTED DEPTHS",
        "# ==============================================================================",
    ]
    for action, pattern in rules:
        lines.append(f"{action} {pattern}")
    return "\n".join(lines) + "\n"


def _collect_bloat_tokens(rules: List[Any]) -> Tuple[List[str], List[str]]:
    """Split non-local-only exclude rules into directory and file glob tokens."""
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
    return dir_tokens, file_tokens


def _match_bloat_path(raw_p: str, dir_tokens: List[str], file_tokens: List[str]) -> Optional[str]:
    """Return the top-level bloat root for a single path if it matches any token."""
    p = raw_p.strip().replace("\\", "/").rstrip("/")
    if not p:
        return None
    parts = p.split("/")
    for i, part in enumerate(parts):
        if any(fnmatch.fnmatch(part, dt) for dt in dir_tokens):
            return "/".join(parts[: i + 1]) + "/"
        if any(fnmatch.fnmatch(part, ft) for ft in file_tokens):
            is_dir = i < len(parts) - 1 or raw_p.strip().endswith(("/", "\\"))
            return "/".join(parts[: i + 1]) + ("/" if is_dir else "")
    return None


def extract_bloat_roots(raw_paths: List[str], rules: List[Any]) -> List[str]:
    """Extract and deduplicate root bloat directory paths matching ignore rules."""
    dir_tokens, file_tokens = _collect_bloat_tokens(rules)
    bloat_roots: set[str] = set()
    for raw_p in raw_paths:
        matched = _match_bloat_path(raw_p, dir_tokens, file_tokens)
        if matched:
            bloat_roots.add(matched)
    return sorted(bloat_roots)


def _write_temp_filter(suffix: str, content: str) -> Path:
    """Create a temporary filter file and write UTF-8 content."""
    with tempfile.NamedTemporaryFile("w+", suffix=suffix, delete=False) as tf:
        path = Path(tf.name)
    path.write_text(content, encoding="utf-8")
    return path


def resolve_target_filters(
    global_filter_path: Path,
    target: SyncTarget,
) -> Tuple[Path, Path, bool]:
    """Resolve sync and cleanup filter paths for a specific target."""
    rules = parse_filter_rules(global_filter_path.read_text(encoding="utf-8"))
    has_target_custom = False

    if target.custom_filter and target.custom_filter.is_file():
        rules.extend(parse_filter_rules(target.custom_filter.read_text(encoding="utf-8")))
        has_target_custom = True

    local_ignore = find_target_ignore_file(target.local_path)
    if local_ignore and local_ignore.is_file():
        rules.extend(parse_filter_rules(local_ignore.read_text(encoding="utf-8")))
        has_target_custom = True

    if target.excludes:
        for pat in target.excludes:
            clean = normalize_pattern(pat)
            if clean:
                rules.append(FilterRule(action="-", pattern=clean, is_local_only=False))
        has_target_custom = True

    cleanup_path = _write_temp_filter(f"_{target.name}_cleanup.txt", compile_cleanup_filter(rules))
    if not has_target_custom:
        return global_filter_path, cleanup_path, False

    sync_path = _write_temp_filter(f"_{target.name}_sync.txt", compile_normalized_sync_filter(rules))
    return sync_path, cleanup_path, True


def generate_cleanup_filter_file(source_file: Path, target_file: Path) -> Path:
    """Read source ignore file and write the compiled cleanup filter to target_file."""
    content = source_file.read_text(encoding="utf-8")
    rules = parse_filter_rules(content)
    cleanup_content = compile_cleanup_filter(rules)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(cleanup_content, encoding="utf-8")
    return target_file
