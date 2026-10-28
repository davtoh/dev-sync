"""Command-line interface for dev_sync."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from dev_sync.config import SyncTarget, create_target, get_default_local_base, load_targets
from dev_sync.engine import SyncEngine
from dev_sync.filter import compile_cleanup_filter, parse_filter_rules


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="dev-sync",
        description="Cross-platform development workspace sync and cleanup utility via rclone.",
    )

    parser.add_argument(
        "action",
        nargs="?",
        choices=["pull", "push", "check", "diagnose", "cleanup"],
        help="Action to execute.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="all",
        help="Target name from sync-targets.conf, 'all', or an ad-hoc target name (default: all).",
    )
    parser.add_argument(
        "adhoc_local",
        nargs="?",
        default=None,
        help="Optional ad-hoc local path (e.g. $LOCAL_BASE/my-app).",
    )
    parser.add_argument(
        "adhoc_remote",
        nargs="?",
        default=None,
        help="Optional ad-hoc remote path (e.g. gdrive:my-app).",
    )
    parser.add_argument(
        "--local",
        dest="flag_local",
        help="Explicit ad-hoc local path.",
    )
    parser.add_argument(
        "--remote",
        dest="flag_remote",
        help="Explicit ad-hoc remote path.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform a trial run with no permanent changes.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to sync-targets.conf configuration file.",
    )
    parser.add_argument(
        "--filter",
        type=Path,
        help="Path to rclone-ignore.txt filter file.",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="List all configured sync targets and exit.",
    )
    parser.add_argument(
        "--test-filter",
        action="store_true",
        help="Display the compiled inverted cleanup filter derived from rclone-ignore.txt and exit.",
    )

    return parser.parse_args(argv)


def get_repo_root() -> Path:
    """Get the root directory of the dev-sync repository."""
    return Path(__file__).resolve().parent.parent


def main(argv: Optional[List[str]] = None) -> int:
    """CLI execution entrypoint."""
    args = parse_args(argv)
    repo_root = get_repo_root()

    config_path = args.config if args.config else repo_root / "sync-targets.conf"
    filter_path = args.filter if args.filter else repo_root / "rclone-ignore.txt"

    if args.test_filter:
        if not filter_path.exists():
            print(f"Error: Filter file not found at {filter_path}", file=sys.stderr)
            return 1
        content = filter_path.read_text(encoding="utf-8")
        rules = parse_filter_rules(content)
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

    if not args.action:
        print("Error: Action is required (pull, push, check, diagnose, cleanup).", file=sys.stderr)
        print("Run with --help for detailed usage information.", file=sys.stderr)
        return 1

    engine = SyncEngine(filter_file=filter_path)

    # Determine targets to process
    adhoc_local = args.flag_local or args.adhoc_local
    adhoc_remote = args.flag_remote or args.adhoc_remote

    targets_to_run: List[SyncTarget] = []

    if adhoc_local and adhoc_remote:
        # User passed ad-hoc target parameters
        target = create_target(
            name=args.target or "adhoc",
            raw_local=adhoc_local,
            remote_path=adhoc_remote,
            repo_root=repo_root,
        )
        targets_to_run.append(target)
    else:
        # Load targets from config
        try:
            targets_map = load_targets(config_path, repo_root)
        except Exception as e:
            print(f"Error loading configuration: {e}", file=sys.stderr)
            return 1

        if args.target == "all":
            targets_to_run = list(targets_map.values())
        else:
            if args.target not in targets_map:
                available = ", ".join(targets_map.keys())
                print(
                    f"Error: Target '{args.target}' not found in {config_path.name}.\nAvailable targets: {available}",
                    file=sys.stderr,
                )
                return 1
            targets_to_run.append(targets_map[args.target])

    print(f"\n{'=' * 60}")
    print(f"dev-sync: Action='{args.action}', Targets={len(targets_to_run)}, DryRun={args.dry_run}")
    print(f"{'=' * 60}")

    for t in targets_to_run:
        print(f"\nTarget: {t.name}")
        print(f"Local : {t.local_path}")
        print(f"Remote: {t.remote_path}")

        try:
            if args.action == "diagnose":
                engine.diagnose(t)
            elif args.action == "cleanup":
                engine.cleanup(t, dry_run=args.dry_run)
            elif args.action == "pull":
                engine.pull(t, dry_run=args.dry_run)
            elif args.action == "push":
                engine.push(t, dry_run=args.dry_run)
            elif args.action == "check":
                engine.check(t)
        except Exception as e:
            print(f"\n[ERROR] Action '{args.action}' failed for target '{t.name}': {e}", file=sys.stderr)
            return 1

    print(f"\n[SUCCESS] Completed '{args.action}' for {len(targets_to_run)} target(s).")
    return 0
