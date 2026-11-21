"""Unit tests for dev_sync.ledger sharded provenance and local preferences."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dev_sync.git_guard import FileGitInfo
from dev_sync.ledger import (
    compile_merged_ledger,
    ignore_local_paths,
    is_path_ignored_locally,
    load_all_machine_snapshots,
    load_local_prefs,
    load_merged_ledger,
    record_local_snapshot,
    save_local_prefs,
    save_machine_snapshot,
    unignore_local_paths,
)
from dev_sync.models import LedgerEntry, LocalPrefs, MachineSnapshot


def _make_snapshot(
    machine_id: str,
    rel_path: str,
    size: int,
    ts: str,
    origin: str,
    modified_by: str,
    branch: str,
) -> MachineSnapshot:
    """Create a synthetic MachineSnapshot for multi-host merge tests."""
    return MachineSnapshot(
        machine_id=machine_id,
        updated_at=ts,
        entries={
            rel_path: LedgerEntry(
                rel_path=rel_path,
                size=size,
                mtime=ts,
                origin_machine=origin,
                last_modified_by=modified_by,
                updated_at=ts,
                git_status="untracked_companion",
                git_branch=branch,
            )
        },
    )


class TestLedgerEngine(unittest.TestCase):
    """Verify sharded snapshot merging, origin attribution, and local opt-out prefs."""

    def test_sharded_snapshot_merge_and_origin_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rel = "products/rrtools/NOTES.txt"
            save_machine_snapshot(
                root, _make_snapshot("desktop-win", rel, 120, "2026-11-18T10:00:00Z", "desktop-win", "desktop-win", "main")
            )
            save_machine_snapshot(
                root, _make_snapshot("laptop-mac", rel, 240, "2026-11-19T12:00:00Z", "desktop-win", "laptop-mac", "feature")
            )

            loaded = load_all_machine_snapshots(root)
            self.assertEqual([s.machine_id for s in loaded], ["desktop-win", "laptop-mac"])

            merged = compile_merged_ledger(loaded)
            entry = merged[rel]
            self.assertEqual(entry.origin_machine, "desktop-win")
            self.assertEqual(entry.last_modified_by, "laptop-mac")
            self.assertEqual(entry.size, 240)
            self.assertEqual(entry.git_branch, "feature")

    def test_local_prefs_ignore_and_unignore(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            save_local_prefs(root, LocalPrefs(machine_id="termux-phone", machine_alias="phone"))

            prefs = ignore_local_paths(root, ["heavy_project/**", "docs/*.pdf", "archive/"])
            self.assertEqual(len(prefs.ignored_paths), 3)
            self.assertTrue(is_path_ignored_locally("heavy_project/models/weights.bin", prefs))
            self.assertTrue(is_path_ignored_locally("docs/manual.pdf", prefs))
            self.assertTrue(is_path_ignored_locally("archive/old.txt", prefs))
            self.assertFalse(is_path_ignored_locally("products/app/main.py", prefs))

            updated = unignore_local_paths(root, ["docs/*.pdf"])
            self.assertFalse(is_path_ignored_locally("docs/manual.pdf", updated))
            self.assertTrue(is_path_ignored_locally("heavy_project/data.csv", updated))

            reloaded = load_local_prefs(root)
            self.assertEqual(reloaded.machine_alias, "phone")
            self.assertEqual(reloaded.ignored_paths, ["heavy_project/**", "archive/"])

    def test_record_local_snapshot_preserves_prior_origin(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            save_local_prefs(root, LocalPrefs(machine_id="workstation", machine_alias="workstation"))
            prior_snap = MachineSnapshot(
                machine_id="origin-laptop",
                updated_at="2026-11-01T08:00:00Z",
                entries={
                    "notes/todo.md": LedgerEntry(
                        rel_path="notes/todo.md",
                        origin_machine="origin-laptop",
                        last_modified_by="origin-laptop",
                        updated_at="2026-11-01T08:00:00Z",
                    )
                },
            )
            save_machine_snapshot(root, prior_snap)

            (root / "notes").mkdir()
            (root / "notes" / "todo.md").write_text("- item 1\n", encoding="utf-8")
            git_map = {
                "notes/todo.md": FileGitInfo(
                    rel_path="notes/todo.md",
                    git_status="untracked_companion",
                    git_branch="main",
                    git_head="1122334",
                )
            }
            snap = record_local_snapshot(root, ["notes/todo.md"], git_infos=git_map, compute_hash=True)
            self.assertEqual(snap.machine_id, "workstation")

            merged = load_merged_ledger(root)
            entry = merged["notes/todo.md"]
            self.assertEqual(entry.origin_machine, "origin-laptop")
            self.assertEqual(entry.last_modified_by, "workstation")
            self.assertIsNotNone(entry.sha256)
            self.assertEqual(entry.git_head, "1122334")


if __name__ == "__main__":
    unittest.main()
