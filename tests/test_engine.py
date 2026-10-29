"""Tests for the sync engine and CLI argument parser."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dev_sync.cli import main, parse_args
from dev_sync.config import SyncTarget
from dev_sync.engine import SyncEngine, format_diagnostic_report, parse_lsd_duplicates


class TestSyncEngine(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.filter_file = self.base / "rclone-ignore.txt"
        self.filter_file.write_text(
            """
            + .env.example
            - node_modules/**
            - .env
            """,
            encoding="utf-8",
        )
        self.target = SyncTarget(
            name="test_proj",
            raw_local=str(self.base / "local"),
            local_path=self.base / "local",
            remote_path="gdrive:test_proj",
        )

    def tearDown(self):
        self.td.cleanup()

    def test_cli_parse_positional_args(self):
        args = parse_args(["pull", "projects", "--dry-run"])
        self.assertEqual(args.action, "pull")
        self.assertEqual(args.target, "projects")
        self.assertTrue(args.dry_run)

    def test_cli_parse_adhoc_args(self):
        args = parse_args(["push", "my_app", "$HOME/code", "s3:bucket/app"])
        self.assertEqual(args.action, "push")
        self.assertEqual(args.target, "my_app")
        self.assertEqual(args.adhoc_local, "$HOME/code")
        self.assertEqual(args.adhoc_remote, "s3:bucket/app")

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_cleanup_command_construction(self, mock_run, mock_verify):
        mock_run.return_value = MagicMock(returncode=0)
        engine = SyncEngine(filter_file=self.filter_file)
        engine.cleanup(self.target, dry_run=True)

        self.assertTrue(mock_run.called)
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "rclone")
        self.assertEqual(call_args[1], "delete")
        self.assertEqual(call_args[2], "gdrive:test_proj")
        self.assertIn("--rmdirs", call_args)
        self.assertIn("--dry-run", call_args)
        self.assertIn("-P", call_args)
        self.assertIn("--filter-from", call_args)
        self.assertIn("--tpslimit", call_args)
        self.assertIn("--fast-list", call_args)

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_cleanup_purges_detected_bloat_roots_first(self, mock_run, mock_verify):
        def fake_run(cmd, *args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            if "lsf" in cmd:
                m.stdout = "web/node_modules/\nweb/node_modules/subpkg/\n"
            else:
                m.stdout = ""
            return m

        mock_run.side_effect = fake_run
        engine = SyncEngine(filter_file=self.filter_file)
        engine.cleanup(self.target, dry_run=True)

        calls = [c[0][0] for c in mock_run.call_args_list]
        self.assertTrue(any(c[1] == "lsf" for c in calls))
        self.assertTrue(any(c[1] == "purge" and "web/node_modules" in c[2] for c in calls))
        self.assertTrue(any(c[1] == "delete" for c in calls))

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_pull_command_construction(self, mock_run, mock_verify):
        mock_run.return_value = MagicMock(returncode=0)
        engine = SyncEngine(filter_file=self.filter_file)
        engine.pull(self.target, dry_run=False)

        self.assertTrue(mock_run.called)
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "rclone")
        self.assertEqual(call_args[1], "copy")
        self.assertEqual(call_args[2], "gdrive:test_proj")
        self.assertEqual(call_args[3], str(self.target.local_path))
        self.assertIn("--filter-from", call_args)
        self.assertIn(str(self.filter_file), call_args)

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_push_missing_local_raises(self, mock_run, mock_verify):
        engine = SyncEngine(filter_file=self.filter_file)
        # target.local_path does not exist
        with self.assertRaises(FileNotFoundError):
            engine.push(self.target)

    def test_parse_lsd_duplicates(self):
        sample_lsd = """
           0 2026-10-19 22:40:02        -1 web_and_services
           0 2026-10-19 22:40:36        -1 rpa_automation
           0 2026-10-19 22:40:02        -1 web_and_services
           0 2026-10-19 22:40:02        -1 web_and_services
           0 2026-10-19 22:36:14        -1 products
        """
        dups = parse_lsd_duplicates(sample_lsd)
        self.assertEqual(dups, {"web_and_services": 3})

    def test_format_diagnostic_report(self):
        report = format_diagnostic_report(
            target=self.target,
            lsd_output="           0 2026-10-19 22:40:02 -1 web_and_services",
            duplicates={"web_and_services": 2},
            size_output="Total objects: 10\nTotal size: 5 MiB",
            bloat_roots=["web_and_services/app/node_modules/"],
        )
        self.assertIn("dev-sync Diagnostic & Bloat Audit Report", report)
        self.assertIn("web_and_services/app/node_modules/", report)
        self.assertIn("ADVISORY: Duplicate Folder Entities Detected", report)
        self.assertIn("rclone dedupe gdrive:test_proj", report)

    @patch("dev_sync.engine.SyncEngine._verify_rclone")
    @patch("subprocess.run")
    def test_diagnose_generates_report_file(self, mock_run, mock_verify):
        def fake_run(cmd, *args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            if "lsd" in cmd:
                m.stdout = "           0 2026-10-19 22:40:02 -1 web_and_services\n           0 2026-10-19 22:40:02 -1 web_and_services"
            elif "size" in cmd:
                m.stdout = "Total objects: 5\nTotal size: 10 MiB"
            elif "lsf" in cmd:
                m.stdout = "web_and_services/\nweb_and_services/node_modules/\nweb_and_services/node_modules/pkg/"
            return m

        mock_run.side_effect = fake_run
        engine = SyncEngine(filter_file=self.filter_file)
        custom_report = self.base / "custom_report.txt"
        saved = engine.diagnose(self.target, report_path=custom_report)

        self.assertTrue(saved.exists())
        content = saved.read_text(encoding="utf-8")
        self.assertIn("web_and_services/node_modules/", content)
        self.assertNotIn("web_and_services/node_modules/pkg/", content)  # collapsed to root
        self.assertIn("rclone dedupe", content)


if __name__ == "__main__":
    unittest.main()
