"""Terminal hierarchical diff renderer and interactive selector."""

from __future__ import annotations

import datetime
from typing import Callable, Dict, List, Optional

from dev_sync.models import DiffReport, FileDiffItem, FolderDiffNode, PurgeItem, PurgePlan
from dev_sync.purge import matches_select_pattern
from dev_sync.summary import format_bytes


def _format_item_badges(item: FileDiffItem) -> str:
    """Format status, Git, provenance, and local-ignore badges for an item."""
    parts = [item.status.upper()]
    if item.git_status:
        parts.append(item.git_status.upper())
    if item.origin_machine:
        branch_part = f" ({item.git_branch})" if item.git_branch else ""
        parts.append(f"from: {item.origin_machine}{branch_part}")
    elif item.git_branch:
        parts.append(f"branch: {item.git_branch}")
    if item.ignored_locally:
        parts.append("IGNORED-LOCALLY")
    return " | ".join(parts)


def render_diff_tree(report: DiffReport) -> str:
    """Render a numbered hierarchical folder/file diff tree for terminal display."""
    lines = [
        "=" * 86,
        f"dev-sync Diff Tree: {report.target_name}",
        f"Local : {report.local_path}",
        f"Remote: {report.remote_path}",
        f"Total : {report.total_files} file(s), {format_bytes(report.total_bytes)}",
        "-" * 86,
    ]
    if report.warnings:
        lines.append("WARNINGS:")
        for w in report.warnings:
            lines.append(f"  [!] {w}")
        lines.append("-" * 86)

    if not report.folders:
        lines.append("No differences detected. Local and remote are in sync.")
        lines.append("=" * 86)
        return "\n".join(lines)

    for f_idx, node in enumerate(report.folders, start=1):
        folder_label = "./" if node.folder == "." else f"{node.folder.rstrip('/')}/"
        conflict_note = f", {node.git_conflict_count} git conflict(s)" if node.git_conflict_count else ""
        lines.append(f"[{f_idx}] {folder_label} ({node.file_count} files, {format_bytes(node.total_bytes)}{conflict_note})")
        for i_idx, item in enumerate(node.items, start=1):
            size_val = max(item.local_size or 0, item.remote_size or 0)
            lines.append(f"  [{f_idx}.{i_idx}] {item.rel_path} ({format_bytes(size_val)}) [{_format_item_badges(item)}]")

    lines.append("=" * 86)
    return "\n".join(lines)


def _expand_file_range(token: str, folders: List[FolderDiffNode]) -> List[FileDiffItem]:
    """Expand `F.I`, `F.I1-I2`, or `F1.I1-F2.I2` into matching FileDiffItems."""
    if "-" in token:
        left, right = token.split("-", 1)
        f_start_str, i_start_str = left.split(".", 1)
        f_start, i_start = int(f_start_str), int(i_start_str)
        if "." in right:
            f_end_str, i_end_str = right.split(".", 1)
            f_end, i_end = int(f_end_str), int(i_end_str)
        else:
            f_end, i_end = f_start, int(right)

        if f_start > f_end or f_end < 1 or f_start > len(folders):
            return []
        if f_start == f_end:
            if 1 <= f_start <= len(folders):
                items = folders[f_start - 1].items
                return [items[k - 1] for k in range(max(1, i_start), min(len(items), i_end) + 1)]
            return []

        selected: List[FileDiffItem] = []
        for f_idx in range(max(1, f_start), min(len(folders), f_end) + 1):
            items = folders[f_idx - 1].items
            lo = max(1, i_start) if f_idx == f_start else 1
            hi = min(len(items), i_end) if f_idx == f_end else len(items)
            selected.extend(items[k - 1] for k in range(lo, hi + 1))
        return selected

    f_idx_str, i_idx_str = token.split(".", 1)
    f_idx, i_idx = int(f_idx_str), int(i_idx_str)
    if 1 <= f_idx <= len(folders):
        items = folders[f_idx - 1].items
        if 1 <= i_idx <= len(items):
            return [items[i_idx - 1]]
    return []


def _expand_folder_token(token: str, folders: List[FolderDiffNode]) -> List[FileDiffItem]:
    """Expand folder index `F` or folder range `F1-F2` into FileDiffItems."""
    if "-" in token:
        start_s, end_s = token.split("-", 1)
        start_f, end_f = int(start_s), int(end_s)
        selected: List[FileDiffItem] = []
        for f_idx in range(max(1, start_f), min(len(folders), end_f) + 1):
            selected.extend(folders[f_idx - 1].items)
        return selected
    f_idx = int(token)
    if 1 <= f_idx <= len(folders):
        return list(folders[f_idx - 1].items)
    return []


def parse_selection(expr: str, report: DiffReport) -> List[FileDiffItem]:
    """Parse numbered folder/file selections and globs into unique FileDiffItems."""
    clean_expr = expr.strip()
    if not clean_expr:
        return []

    all_items = [item for node in report.folders for item in node.items]
    if clean_expr.lower() == "all":
        return all_items

    selected: Dict[str, FileDiffItem] = {}
    for raw_tok in clean_expr.split(","):
        tok = raw_tok.strip()
        if not tok:
            continue
        matches: List[FileDiffItem] = []
        try:
            if "." in tok and tok.replace(".", "").replace("-", "").isdigit():
                matches = _expand_file_range(tok, report.folders)
            elif tok.replace("-", "").isdigit():
                matches = _expand_folder_token(tok, report.folders)
            else:
                matches = [it for it in all_items if matches_select_pattern(it.rel_path, tok)]
        except ValueError:
            matches = [it for it in all_items if matches_select_pattern(it.rel_path, tok)]
        for it in matches:
            selected[it.rel_path] = it
    return list(selected.values())


def _default_scope_for_item(item: FileDiffItem) -> str:
    if item.status == "local_only":
        return "local"
    return "remote"


def _prompt_action(count: int, dry_run: bool, input_fn: Callable[[str], str]) -> Optional[str]:
    """Prompt user for purge action and confirm permanent deletion."""
    act_raw = input_fn("Action: [T]rash (default) / [P]ermanent / [I]gnore locally / [S]kip: ").strip().lower()
    if act_raw in ("s", "skip"):
        return None
    if act_raw in ("p", "permanent"):
        if not dry_run:
            confirm = input_fn(f"Confirm permanent delete of {count} item(s)? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                return None
        return "permanent"
    if act_raw in ("i", "ignore", "ignore_local"):
        return "ignore_local"
    return "trash"


def prompt_purge_plan(
    report: DiffReport,
    dry_run: bool = False,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> Optional[PurgePlan]:
    """Interactively build a PurgePlan from a DiffReport."""
    output_fn(render_diff_tree(report))
    if report.total_files == 0:
        return None
    sel_expr = input_fn("Select folders/files (e.g. '1, 2.1-2.4', '*.pdf', 'all', or Enter to quit): ").strip()
    items = parse_selection(sel_expr, report)
    if not items:
        return None
    action = _prompt_action(len(items), dry_run, input_fn)
    if action is None:
        return None

    scope_override: Optional[str] = None
    if action in ("trash", "permanent"):
        scope_raw = input_fn("Scope: [R]emote / [L]ocal / [B]oth (Enter = auto by status): ").strip().lower()
        scope_map = {"r": "remote", "remote": "remote", "l": "local", "local": "local", "b": "both", "both": "both"}
        scope_override = scope_map.get(scope_raw)

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan_items = [
        PurgeItem(
            rel_path=it.rel_path,
            scope=scope_override or _default_scope_for_item(it),
            action=action,
            size=max(it.local_size or 0, it.remote_size or 0),
        )
        for it in items
    ]
    return PurgePlan(target_name=report.target_name, created_at=now_iso, dry_run=dry_run, items=plan_items)
