"""Unit tests for headless JSON domain contracts in dev_sync.models."""

import json
import unittest

from dev_sync.models import (
    DiffReport,
    FileDiffItem,
    FolderDiffNode,
    LedgerEntry,
    LocalPrefs,
    MachineSnapshot,
    PurgeItem,
    PurgePlan,
)


class TestModelsSerialization(unittest.TestCase):
    def test_diff_report_json_round_trip(self):
        item = FileDiffItem(
            rel_path="products/rrtools/dissertation.pdf",
            folder="products/rrtools/",
            status="remote_only",
            remote_size=4096,
            remote_mtime="2026-11-18T12:00:00Z",
            git_status="untracked_companion",
            git_branch="main",
            origin_machine="laptop-a",
            last_modified_by="laptop-a",
            ignored_locally=False,
        )
        folder = FolderDiffNode(
            folder="products/rrtools/",
            file_count=1,
            total_bytes=4096,
            git_conflict_count=0,
            items=[item],
        )
        report = DiffReport(
            target_name="projects",
            local_path="/tmp/projects",
            remote_path="gdrive:projects",
            generated_at="2026-11-19T12:00:00Z",
            total_files=1,
            total_bytes=4096,
            git_conflict_count=0,
            folders=[folder],
            warnings=["Sample warning"],
        )

        payload = json.dumps(report.to_dict())
        restored = DiffReport.from_dict(json.loads(payload))
        self.assertEqual(restored, report)

    def test_ledger_and_machine_snapshot_round_trip(self):
        entry = LedgerEntry(
            rel_path="products/rrtools/NOTES.txt",
            size=128,
            mtime="2026-11-19T10:00:00Z",
            sha256="abc123",
            origin_machine="desktop-win",
            last_modified_by="desktop-win",
            updated_at="2026-11-19T10:01:00Z",
            git_status="untracked_companion",
            git_branch="main",
            git_head="deadbeef",
        )
        snapshot = MachineSnapshot(
            machine_id="desktop-win",
            machine_alias="Workstation",
            updated_at="2026-11-19T10:01:00Z",
            entries={entry.rel_path: entry},
        )
        restored = MachineSnapshot.from_dict(json.loads(json.dumps(snapshot.to_dict())))
        self.assertEqual(restored, snapshot)

    def test_local_prefs_and_purge_plan_round_trip(self):
        prefs = LocalPrefs(
            machine_id="termux-phone",
            machine_alias="Phone",
            ignored_paths=["heavy_project/**"],
        )
        restored_prefs = LocalPrefs.from_dict(json.loads(json.dumps(prefs.to_dict())))
        self.assertEqual(restored_prefs, prefs)

        plan = PurgePlan(
            target_name="projects",
            created_at="2026-11-19T12:05:00Z",
            dry_run=True,
            items=[PurgeItem(rel_path="old/cache.bin", scope="remote", action="trash", size=2048)],
        )
        restored_plan = PurgePlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        self.assertEqual(restored_plan, plan)


if __name__ == "__main__":
    unittest.main()
