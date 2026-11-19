"""Command-line interface for dev_sync."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from dev_sync.config import SyncTarget, create_target, load_targets
from dev_sync.diff import DiffAnalyzer
from dev_sync.engine import SyncEngine
from dev_sync.errors import RcloneExecutionError
from dev_sync.filter import compile_cleanup_filter, parse_filter_rules
from dev_sync.interactive import prompt_purge_plan, render_diff_tree
from dev_sync.models import PurgeItem, PurgePlan
from dev_sync.purge import PurgeExecutor, matches_select_pattern


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    """Register positional and ad-hoc target selection arguments."""
    parser.add_argument(
        "action",
        nargs="?",
        choices=["pull", "push", "check", "diagnose", "cleanup", "diff", "purge", "restore", "empty-trash"],
        help="Action to execute.",
    )
    parser.add_argument("target", nargs="?", default="all", help="Target name from sync-targets.conf, 'all', or an ad-hoc target name (default: all).")
    parser.add_argument("adhoc_local", nargs="?", default=None, help="Optional ad-hoc local path (e.g. $LOCAL_BASE/my-app).")
    parser.add_argument("adhoc_remote", nargs="?", default=None, help="Optional ad-hoc remote path (e.g. gdrive:my-app).")
    parser.add_argument("--local", dest="flag_local", help="Explicit ad-hoc local path.")
    parser.add_argument("--remote", dest="flag_remote", help="Explicit ad-hoc remote path.")


def _add_execution_args(parser: argparse.ArgumentParser) -> None:
    """Register execution mode, logging, and info flags."""
    parser.add_argument("--dry-run", action="store_true", help="Perform a trial run with no permanent changes.")
    parser.add_argument("--report", type=Path, help="Optional path to save physical diagnostic report (default: dev-sync-report-<target>.txt).")
    parser.add_argument("--log", type=Path, help="Optional path to save structured JSONL execution event log (default: .logs/dev-sync-*.jsonl).")
    parser.add_argument("--no-summary", action="store_true", help="Suppress the formatted execution summary table at the end of the run.")
    parser.add_argument("--config", type=Path, help="Path to sync-targets.conf configuration file.")
    parser.add_argument("--filter", type=Path, help="Path to rclone-ignore.txt filter file.")
    parser.add_argument("--exclude", action="append", dest="exclude", default=[], help="Additional exclusion pattern to filter out (can be specified multiple times).")
    parser.add_argument("--list-targets", action="store_true", help="List all configured sync targets and exit.")
    parser.add_argument("--test-filter", action="store_true", help="Display the compiled inverted cleanup filter derived from rclone-ignore.txt and exit.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit machine-readable JSON output (for diff).")
    parser.add_argument("--apply-plan", type=Path, help="Execute a serialized PurgePlan JSON file non-interactively.")
    parser.add_argument("--select", help="Glob/path pattern to select items for purge or restore.")
    parser.add_argument("--scope", choices=["remote", "local", "both"], help="Target scope for purge, restore, or empty-trash.")
    parser.add_argument("--trash", action="store_true", help="Move purged items to trash instead of permanent deletion (default).")
    parser.add_argument("--permanent", action="store_true", help="Permanently delete selected items during purge (requires --yes).")
    parser.add_argument("--ignore-local", action="store_true", help="Register selected paths in .dev-sync/local-prefs.json.")
    parser.add_argument("--yes", action="store_true", help="Confirm non-interactive permanent deletion.")
    parser.add_argument("--older-than", type=int, default=None, help="Only empty trash folders older than N days.")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="dev-sync",
        description="Cross-platform development workspace sync and cleanup utility via rclone.",
    )
    _add_target_args(parser)
    _add_execution_args(parser)
    return parser.parse_args(argv)


def get_repo_root() -> Path:
    """Get the root directory of the dev-sync repository."""
    return Path(__file__).resolve().parent.parent


def _handle_info_flags(args: argparse.Namespace, config_path: Path, filter_path: Path, repo_root: Path) -> Optional[int]:
    """Handle --test-filter and --list-targets exit-early flags."""
    if args.test_filter:
        if not filter_path.exists():
            print(f"Error: Filter file not found at {filter_path}", file=sys.stderr)
            return 1
        rules = parse_filter_rules(filter_path.read_text(encoding="utf-8"))
        print(compile_cleanup_filter(rules))
        return 0

    if args.list_targets:
        try:
            targets = load_targets(config_path, repo_root)
        except Exception as e:
            print(f"Error loading targets: {e}", file=sys.stderr)
            return 1
        print(f"\nConfigured targets in '{config_path.name}':")
        print(f"{'Target':<20} {'Remote':<30} {'Resolved Local Path'}")
        print("-" * 80)
        for t in targets.values():
            print(f"{t.name:<20} {t.remote_path:<30} {t.local_path}")
        return 0

    return None


def _resolve_targets(args: argparse.Namespace, config_path: Path, repo_root: Path) -> Tuple[Optional[List[SyncTarget]], int]:
    """Resolve CLI arguments or sync-targets.conf into a list of SyncTarget instances."""
    adhoc_local = args.flag_local or args.adhoc_local
    adhoc_remote = args.flag_remote or args.adhoc_remote
    if adhoc_local and adhoc_remote:
        return [create_target(name=args.target or "adhoc", raw_local=adhoc_local, remote_path=adhoc_remote, repo_root=repo_root)], 0

    try:
        targets_map = load_targets(config_path, repo_root)
    except Exception as e:
        print(f"Error loading configuration: {e}", file=sys.stderr)
        return None, 1

    if args.target == "all":
        return list(targets_map.values()), 0
    if args.target not in targets_map:
        available = ", ".join(targets_map.keys())
        print(f"Error: Target '{args.target}' not found in {config_path.name}.\nAvailable targets: {available}", file=sys.stderr)
        return None, 1
    return [targets_map[args.target]], 0


def _run_diff_action(engine: SyncEngine, target: SyncTarget, args: argparse.Namespace) -> None:
    """Execute diff analysis and print JSON or terminal diff tree."""
    report = DiffAnalyzer(engine).analyze(target, extra_excludes=args.exclude)
    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_diff_tree(report))


def _resolve_purge_action_name(args: argparse.Namespace) -> str:
    if args.permanent:
        return "permanent"
    if args.ignore_local:
        return "ignore_local"
    return "trash"


def _build_select_purge_plan(
    engine: SyncEngine, target: SyncTarget, args: argparse.Namespace
) -> Optional[PurgePlan]:
    """Build a PurgePlan from --select and mode flags."""
    action_name = _resolve_purge_action_name(args)
    if action_name == "permanent" and not args.yes and not args.dry_run:
        raise ValueError("Permanent purge requires --yes in non-interactive --select mode (or use --dry-run / --trash).")
    report = DiffAnalyzer(engine).analyze(target, extra_excludes=args.exclude)
    all_items = [it for node in report.folders for it in node.items]
    matched = [it for it in all_items if matches_select_pattern(it.rel_path, args.select)]
    if matched:
        plan_items = [
            PurgeItem(
                rel_path=it.rel_path,
                scope=args.scope or ("local" if it.status == "local_only" else "remote"),
                action=action_name,
                size=max(it.local_size or 0, it.remote_size or 0),
            )
            for it in matched
        ]
    elif action_name == "ignore_local":
        plan_items = [PurgeItem(rel_path=args.select, scope="local", action="ignore_local", size=0)]
    else:
        return None
    return PurgePlan(target_name=target.name, dry_run=args.dry_run, items=plan_items)


def _run_purge_action(engine: SyncEngine, target: SyncTarget, args: argparse.Namespace) -> None:
    """Execute purge via --apply-plan, --select, or interactive tree prompt."""
    executor = PurgeExecutor(engine)
    if args.apply_plan:
        plan = PurgePlan.from_dict(json.loads(args.apply_plan.read_text(encoding="utf-8")))
        if args.dry_run:
            plan.dry_run = True
        print(f"Purge plan applied: {executor.execute_plan(target, plan)}")
        return

    if args.select:
        plan = _build_select_purge_plan(engine, target, args)
        if plan is None:
            print(f"No diff items matched '{args.select}'.")
            return
        print(f"Purge completed: {executor.execute_plan(target, plan)}")
        return

    report = DiffAnalyzer(engine).analyze(target, extra_excludes=args.exclude)
    plan = prompt_purge_plan(report, dry_run=args.dry_run)
    if plan:
        print(f"Purge completed: {executor.execute_plan(target, plan)}")
    else:
        print("Purge skipped; no changes made.")


def _execute_target_action(engine: SyncEngine, target: SyncTarget, args: argparse.Namespace) -> None:
    """Dispatch the requested action for a single SyncTarget."""
    show_sum = not args.no_summary
    if args.action == "diagnose":
        engine.diagnose(target, report_path=args.report, extra_excludes=args.exclude)
    elif args.action == "cleanup":
        engine.cleanup(target, dry_run=args.dry_run, log_path=args.log, show_summary=show_sum, extra_excludes=args.exclude)
    elif args.action == "pull":
        engine.pull(target, dry_run=args.dry_run, log_path=args.log, show_summary=show_sum, extra_excludes=args.exclude)
    elif args.action == "push":
        engine.push(target, dry_run=args.dry_run, log_path=args.log, show_summary=show_sum, extra_excludes=args.exclude)
    elif args.action == "check":
        engine.check(target, log_path=args.log, show_summary=show_sum, extra_excludes=args.exclude)
    elif args.action == "diff":
        _run_diff_action(engine, target, args)
    elif args.action == "purge":
        _run_purge_action(engine, target, args)
    elif args.action == "restore":
        res = PurgeExecutor(engine).restore(target, select_pattern=args.select or "*", scope=args.scope or "both", dry_run=args.dry_run)
        print(f"Restore completed: {res}")
    elif args.action == "empty-trash":
        res = PurgeExecutor(engine).empty_trash(target, older_than_days=args.older_than, scope=args.scope or "both", dry_run=args.dry_run)
        print(f"Empty trash completed: {res}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI execution entrypoint."""
    args = parse_args(argv)
    repo_root = get_repo_root()
    config_path = args.config or (repo_root / "sync-targets.conf")
    filter_path = args.filter or (repo_root / "rclone-ignore.txt")
    early_exit = _handle_info_flags(args, config_path, filter_path, repo_root)
    if early_exit is not None:
        return early_exit
    if not args.action:
        print("Error: Action is required (pull, push, check, diagnose, cleanup, diff, purge, restore, empty-trash).", file=sys.stderr)
        return 1
    targets_to_run, exit_code = _resolve_targets(args, config_path, repo_root)
    if targets_to_run is None:
        return exit_code

    engine = SyncEngine(filter_file=filter_path)
    quiet_json = args.action == "diff" and args.json_output
    if not quiet_json:
        print(f"\n{'=' * 60}\ndev-sync: Action='{args.action}', Targets={len(targets_to_run)}, DryRun={args.dry_run}\n{'=' * 60}")
    for t in targets_to_run:
        if not quiet_json:
            print(f"\nTarget: {t.name}\nLocal : {t.local_path}\nRemote: {t.remote_path}")
        try:
            _execute_target_action(engine, t, args)
        except RcloneExecutionError as e:
            raw_args = argv if argv is not None else sys.argv[1:]
            retry_cmd = shlex.join(["python", "-m", "dev_sync", *raw_args]) if raw_args else None
            print(f"\n[ERROR] Action '{args.action}' failed for target '{t.name}': {e.format_message(retry_cmd=retry_cmd)}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"\n[ERROR] Action '{args.action}' failed for target '{t.name}': {e}", file=sys.stderr)
            return 1
    if not quiet_json:
        print(f"\n[SUCCESS] Completed '{args.action}' for {len(targets_to_run)} target(s).")
    return 0
