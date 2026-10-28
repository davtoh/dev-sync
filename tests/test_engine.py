"""Tests for the sync engine and CLI argument parser."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dev_sync.cli import main, parse_args
from dev_sync.config import SyncTarget
from dev_sync.engine import SyncEngine


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


if __name__ == "__main__":
    unittest.main()
