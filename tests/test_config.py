"""Tests for target configuration parsing and path resolution."""

import os
import tempfile
import unittest
from pathlib import Path

from dev_sync.config import (
    create_target,
    get_default_local_base,
    load_targets,
    resolve_local_path,
)


class TestConfigResolver(unittest.TestCase):
    def test_resolve_local_path_placeholders(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = Path.home().resolve()

            # Test $LOCAL_BASE
            p1 = resolve_local_path("$LOCAL_BASE/projects", local_base=base)
            self.assertEqual(p1, (base / "projects").resolve())

            # Test ${LOCAL_BASE}
            p2 = resolve_local_path("${LOCAL_BASE}/phone_projects", local_base=base)
            self.assertEqual(p2, (base / "phone_projects").resolve())

            # Test $HOME
            p3 = resolve_local_path("$HOME/code/my-app", local_base=base)
            self.assertEqual(p3, (home / "code" / "my-app").resolve())

            # Test relative path
            p4 = resolve_local_path("relative_dir", local_base=base)
            self.assertEqual(p4, (base / "relative_dir").resolve())

    def test_resolve_with_custom_env_var(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            os.environ["CUSTOM_TEST_WORKSPACE"] = str(base / "custom_work")
            try:
                p = resolve_local_path("$CUSTOM_TEST_WORKSPACE/repo", local_base=base)
                self.assertEqual(p, (base / "custom_work" / "repo").resolve())
            finally:
                del os.environ["CUSTOM_TEST_WORKSPACE"]

    def test_create_target(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "dev-sync"
            repo_root.mkdir()
            target = create_target(
                name="mobile_app",
                raw_local="$LOCAL_BASE/mobile",
                remote_path="gdrive:mobile",
                repo_root=repo_root,
            )
            self.assertEqual(target.name, "mobile_app")
            self.assertEqual(target.raw_local, "$LOCAL_BASE/mobile")
            self.assertEqual(target.remote_path, "gdrive:mobile")
            self.assertEqual(target.local_path, (Path(td) / "mobile").resolve())

    def test_load_targets_from_config(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            conf_file = base / "sync-targets.conf"
            conf_file.write_text(
                """
                # Sample configuration
                web_project = $LOCAL_BASE/web | gdrive:web
                s3_data = $LOCAL_BASE/data | s3:bucket/data
                home_doc = $HOME/docs | gdrive:docs
                """,
                encoding="utf-8",
            )
            targets = load_targets(conf_file, repo_root=base / "dev-sync")
            self.assertIn("web_project", targets)
            self.assertIn("s3_data", targets)
            self.assertIn("home_doc", targets)
            self.assertEqual(targets["s3_data"].remote_path, "s3:bucket/data")

    def test_auto_copy_from_example(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            conf_file = base / "sync-targets.conf"
            example_file = base / "sync-targets.conf.example"
            example_file.write_text("demo = $LOCAL_BASE/demo | gdrive:demo\n", encoding="utf-8")

            # Conf doesn't exist yet, should auto-copy from example
            targets = load_targets(conf_file, repo_root=base / "dev-sync")
            self.assertTrue(conf_file.exists())
            self.assertIn("demo", targets)


if __name__ == "__main__":
    unittest.main()
