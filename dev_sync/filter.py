"""Filter rule parser and dynamic inverted cleanup compiler for rclone."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import List, Tuple


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


def parse_filter_rules(content: str) -> List[Tuple[str, str]]:
    """Parse raw filter file content into a list of (action, pattern) tuples.

    Action is '+' (include) or '-' (exclude).
    Comments and blank lines are ignored.
    """
    rules: List[Tuple[str, str]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("+"):
            pattern = normalize_pattern(line[1:].strip())
            if pattern:
                rules.append(("+", pattern))
        elif line.startswith("-"):
            pattern = normalize_pattern(line[1:].strip())
            if pattern:
                rules.append(("-", pattern))
    return rules


def compile_cleanup_filter(rules: List[Tuple[str, str]]) -> str:
    """Compile an inverted filter specifically for safe remote cleanup.

    Transformation logic:
    1. '+ pattern' (explicit preservation, e.g. .env.example) -> '- pattern' (protect from deletion).
    2. '- pattern' (ignored bloat, e.g. node_modules/**) -> '+ pattern' (target for deletion).
    3. Terminal guard: '- **' (protect all other source files from deletion).
    """
    lines: List[str] = [
        "# ==============================================================================",
        "# DYNAMICALLY GENERATED CLEANUP FILTER - DO NOT EDIT MANUALLY",
        "# Inverts ignore rules: excludes become targets for deletion (+),",
        "# while preserved files (-) and all other source code (- **) are strictly protected.",
        "# ==============================================================================",
    ]

    # First emit all protection rules (-) derived from '+' entries in source
    for action, pattern in rules:
        if action == "+":
            lines.append(f"- {pattern}")

    # Then emit deletion targets (+) derived from '-' entries in source
    for action, pattern in rules:
        if action == "-":
            lines.append(f"+ {pattern}")

    # Terminal guard: Never delete any file that was not explicitly targeted
    lines.append("# Terminal guard: Protect all remaining unmentioned files")
    lines.append("- **")

    return "\n".join(lines) + "\n"


def compile_normalized_sync_filter(rules: List[Tuple[str, str]]) -> str:
    """Compile a normalized filter for standard sync operations (push/pull/check).

    Ensures that patterns without leading '**/' match accurately at both root and nested levels.
    """
    lines: List[str] = [
        "# ==============================================================================",
        "# NORMALIZED SYNC FILTER - MATCHES AT ROOT AND NESTED DEPTHS",
        "# ==============================================================================",
    ]
    for action, pattern in rules:
        lines.append(f"{action} {pattern}")
    return "\n".join(lines) + "\n"


def extract_bloat_roots(raw_paths: List[str], rules: List[Tuple[str, str]]) -> List[str]:
    """Extract and deduplicate root bloat directory paths matching ignore rules.

    Distinguishes true bloat roots (e.g. 'node_modules', 'build', '.venv') from
    normal source parent directories traversed during directory scanning.
    Collapses nested bloat paths into their top-level root.
    """
    dir_tokens: List[str] = []
    file_tokens: List[str] = []

    for action, pat in rules:
        if action == "-":
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


def generate_cleanup_filter_file(source_file: Path, target_file: Path) -> Path:
    """Read source ignore file and write the compiled cleanup filter to target_file."""
    content = source_file.read_text(encoding="utf-8")
    rules = parse_filter_rules(content)
    cleanup_content = compile_cleanup_filter(rules)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(cleanup_content, encoding="utf-8")
    return target_file
