"""Unit tests for hierarchical diff, interactive selection, purge, restore, and CLI contracts."""

from __future__ import annotations

import datetime
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from dev_sync.cli import main as cli_main
from dev_sync.config import SyncTarget
from dev_sync.diff import DiffAnalyzer
from dev_sync.engine import SyncEngine
from dev_sync.interactive import parse_selection, prompt_purge_plan, render_diff_tree
from dev_sync.ledger import ignore_local_paths, load_local_prefs, record_local_snapshot, save_merged_ledger, compile_merged_ledger
from dev_sync.models import DiffReport, FileDiffItem, FolderDiffNode, LedgerEntry, PurgeItem, PurgePlan
from dev_sync.purge import PurgeExecutor


class TestDiffAndPurge(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.local = self.base / "local"
        self.local.mkdir(parents=True)
        self.filter_file = self.base / "rclone-ignore.txt"
        self.filter_file.write_text("- node_modules/**\n+ **\n", encoding="utf-8")
        self.target = SyncTarget(name="proj", raw_local="./local", local_path=self.local, remote_path="gdrive:proj")
        self.engine = SyncEngine(filter_file=self.filter_file)

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_diff_analyzer_hierarchical_tree_and_provenance(self, mock_run, mock_verify) -> None:
        local_file = self.local / "docs" / "notes.md"
        local_file.parent.mkdir(parents=True)
        local_file.write_text("hello", encoding="utf-8")
        ignore_local_paths(self.local, ["heavy/**"])
        save_merged_ledger(
            self.local,
            {
                "products/rrtools/guide.pdf": LedgerEntry(
                    rel_path="products/rrtools/guide.pdf",
                    origin_machine="macbook",
                    last_modified_by="macbook",
                )
            },
        )

        lsjson_payload = json.dumps([
            {"Path": "products/rrtools/guide.pdf", "Size": 2048, "ModTime": "2026-11-18T10:00:00Z", "IsDir": False},
            {"Path": "heavy/data.bin", "Size": 4096, "ModTime": "2026-11-18T10:00:00Z", "IsDir": False},
        ])
        check_payload = "+ docs/notes.md\n- products/rrtools/guide.pdf\n- heavy/data.bin\n"
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=lsjson_payload, stderr=""),
            MagicMock(returncode=1, stdout=check_payload, stderr=""),
        ]

        report = DiffAnalyzer(self.engine).analyze(self.target)
        self.assertEqual(report.total_files, 3)
        self.assertEqual([f.folder for f in report.folders], ["docs", "heavy", "products/rrtools"])
        rr_item = report.folders[2].items[0]
        self.assertEqual(rr_item.status, "remote_only")
        self.assertEqual(rr_item.origin_machine, "macbook")
        self.assertTrue(report.folders[1].items[0].ignored_locally)

        rendered = render_diff_tree(report)
        self.assertIn("[1] docs/", rendered)
        self.assertIn("from: macbook", rendered)

    def test_parse_selection_and_interactive_prompt(self) -> None:
        report = DiffReport(
            target_name="proj",
            local_path=str(self.local),
            remote_path="gdrive:proj",
            generated_at="2026-11-19T12:00:00Z",
            total_files=3,
            total_bytes=300,
            folders=[
                FolderDiffNode("docs", 1, 100, 0, [FileDiffItem("docs/a.md", "docs", "local_only", local_size=100)]),
                FolderDiffNode(
                    "rr",
                    2,
                    200,
                    0,
                    [
                        FileDiffItem("rr/x.pdf", "rr", "remote_only", remote_size=100),
                        FileDiffItem("rr/y.pdf", "rr", "remote_only", remote_size=100),
                    ],
                ),
            ],
        )
        sel = parse_selection("1, 2.1-2.2", report)
        self.assertEqual([i.rel_path for i in sel], ["docs/a.md", "rr/x.pdf", "rr/y.pdf"])
        sel_glob = parse_selection("*.pdf", report)
        self.assertEqual([i.rel_path for i in sel_glob], ["rr/x.pdf", "rr/y.pdf"])

        answers = iter(["2.1", "t", "r"])
        plan = prompt_purge_plan(report, input_fn=lambda _: next(answers), output_fn=lambda _: None)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.items[0].rel_path, "rr/x.pdf")
        self.assertEqual(plan.items[0].action, "trash")
        self.assertEqual(plan.items[0].scope, "remote")

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_purge_trash_permanent_and_ignore_local(self, mock_run, mock_verify) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        f_trash = self.local / "old" / "a.txt"
        f_perm = self.local / "old" / "b.txt"
        f_trash.parent.mkdir(parents=True)
        f_trash.write_text("trash", encoding="utf-8")
        f_perm.write_text("perm", encoding="utf-8")

        plan = PurgePlan(
            target_name="proj",
            items=[
                PurgeItem("old/a.txt", scope="both", action="trash", size=5),
                PurgeItem("old/b.txt", scope="both", action="permanent", size=4),
                PurgeItem("heavy/**", scope="local", action="ignore_local", size=0),
            ],
        )
        res = PurgeExecutor(self.engine).execute_plan(self.target, plan)
        self.assertEqual(res["trashed"], 2)
        self.assertEqual(res["deleted"], 2)
        self.assertEqual(res["ignored_locally"], 1)
        self.assertFalse(f_trash.exists())
        self.assertFalse(f_perm.exists())
        self.assertEqual(load_local_prefs(self.local).ignored_paths, ["heavy/**"])

        # Restore local trashed file
        restored = PurgeExecutor(self.engine).restore(self.target, select_pattern="old/*.txt", scope="local")
        self.assertEqual(restored["local_restored"], 1)
        self.assertTrue(f_trash.exists())

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_empty_trash_older_than_days(self, mock_run, mock_verify) -> None:
        old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).strftime("%Y-%m-%d_%H%M%S")
        new_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        (self.local / ".dev-sync" / "trash" / old_ts).mkdir(parents=True)
        (self.local / ".dev-sync" / "trash" / new_ts).mkdir(parents=True)

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=f"{old_ts}/\n{new_ts}/\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        res = PurgeExecutor(self.engine).empty_trash(self.target, older_than_days=30, scope="both")
        self.assertEqual(res["remote_purged"], 1)
        self.assertEqual(res["local_purged"], 1)
        self.assertFalse((self.local / ".dev-sync" / "trash" / old_ts).exists())
        self.assertTrue((self.local / ".dev-sync" / "trash" / new_ts).exists())

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_cli_diff_json_and_apply_plan(self, mock_run, mock_verify) -> None:
        conf = self.base / "sync-targets.conf"
        conf.write_text(f"proj={self.local}|gdrive:proj\n", encoding="utf-8")
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps([{"Path": "a.txt", "Size": 10, "ModTime": "2026-11-18T10:00:00Z", "IsDir": False}]), stderr=""),
            MagicMock(returncode=1, stdout="- a.txt\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["diff", "proj", "--json", "--config", str(conf), "--filter", str(self.filter_file)])
        self.assertEqual(rc, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["target_name"], "proj")
        self.assertEqual(parsed["total_files"], 1)

        plan_file = self.base / "plan.json"
        plan_file.write_text(
            json.dumps(PurgePlan("proj", items=[PurgeItem("a.txt", scope="remote", action="trash", size=10)]).to_dict()),
            encoding="utf-8",
        )
        rc2 = cli_main(["purge", "proj", "--apply-plan", str(plan_file), "--config", str(conf), "--filter", str(self.filter_file)])
        self.assertEqual(rc2, 0)

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_pull_uses_update_backup_dir_and_local_prefs(self, mock_run, mock_verify) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ignore_local_paths(self.local, ["heavy/**"])
        (self.local / "main.py").write_text("print('hi')\n", encoding="utf-8")

        self.engine.pull(self.target, dry_run=False, show_summary=False)
        call_args = mock_run.call_args[0][0]
        self.assertIn("--update", call_args)
        self.assertIn("--backup-dir", call_args)
        self.assertIn("heavy/**", call_args)
        self.assertTrue((self.local / ".dev-sync" / "ledger.json").exists())

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    def test_restore_local_skips_existing_unless_overwrite(self, mock_verify) -> None:
        trash_file = self.local / ".dev-sync" / "trash" / "2026-11-19_120000" / "old" / "a.txt"
        trash_file.parent.mkdir(parents=True)
        trash_file.write_text("trashed-v1", encoding="utf-8")

        live_file = self.local / "old" / "a.txt"
        live_file.parent.mkdir(parents=True)
        live_file.write_text("live-v2", encoding="utf-8")

        executor = PurgeExecutor(self.engine)
        skipped_res = executor.restore(self.target, select_pattern="old/*.txt", scope="local")
        self.assertEqual(skipped_res["local_restored"], 0)
        self.assertEqual(skipped_res["local_skipped"], 1)
        self.assertEqual(skipped_res["skipped_paths"], ["old/a.txt"])
        self.assertEqual(live_file.read_text(encoding="utf-8"), "live-v2")
        self.assertTrue(trash_file.exists())

        overwrite_res = executor.restore(self.target, select_pattern="old/*.txt", scope="local", overwrite=True)
        self.assertEqual(overwrite_res["local_restored"], 1)
        self.assertNotIn("local_skipped", overwrite_res)
        self.assertEqual(live_file.read_text(encoding="utf-8"), "trashed-v1")
        self.assertFalse(trash_file.exists())

    def test_parse_selection_cross_folder_and_reversed_ranges(self) -> None:
        report = DiffReport(
            target_name="proj",
            local_path=str(self.local),
            remote_path="gdrive:proj",
            generated_at="2026-11-19T12:00:00Z",
            total_files=5,
            total_bytes=500,
            folders=[
                FolderDiffNode("docs", 1, 100, 0, [FileDiffItem("docs/a.md", "docs", "local_only", local_size=100)]),
                FolderDiffNode(
                    "rr",
                    2,
                    200,
                    0,
                    [
                        FileDiffItem("rr/x.pdf", "rr", "remote_only", remote_size=100),
                        FileDiffItem("rr/y.pdf", "rr", "remote_only", remote_size=100),
                    ],
                ),
                FolderDiffNode(
                    "src",
                    2,
                    200,
                    0,
                    [
                        FileDiffItem("src/m.py", "src", "local_only", local_size=100),
                        FileDiffItem("src/n.py", "src", "local_only", local_size=100),
                    ],
                ),
            ],
        )
        self.assertEqual([i.rel_path for i in parse_selection("1.1-2.1", report)], ["docs/a.md", "rr/x.pdf"])
        self.assertEqual(
            [i.rel_path for i in parse_selection("1.1-3.1", report)],
            ["docs/a.md", "rr/x.pdf", "rr/y.pdf", "src/m.py"],
        )
        self.assertEqual(parse_selection("2.2-1.1", report), [])
        self.assertEqual([i.rel_path for i in parse_selection("2.1-2", report)], ["rr/x.pdf", "rr/y.pdf"])
