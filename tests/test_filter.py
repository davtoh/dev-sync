"""Tests for filter compilation and safety invariants."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dev_sync.filter import (
    compile_cleanup_filter,
    compile_normalized_sync_filter,
    extract_bloat_roots,
    generate_cleanup_filter_file,
    normalize_pattern,
    parse_filter_rules,
)


class TestFilterCompiler(unittest.TestCase):
    def test_normalize_pattern(self):
        self.assertEqual(normalize_pattern("**/node_modules/**"), "node_modules/**")
        self.assertEqual(normalize_pattern("build/**"), "build/**")
        self.assertEqual(normalize_pattern("  **/.venv/**  "), ".venv/**")
        self.assertEqual(normalize_pattern("*.apk"), "*.apk")

    def test_parse_filter_rules(self):
        sample = """
        # Comment line
        - **/node_modules/**
        + **/.env.example
        - **/.env
        - **/.env.*
        """
        rules = parse_filter_rules(sample)
        self.assertEqual(
            rules,
            [
                ("-", "node_modules/**"),
                ("+", ".env.example"),
                ("-", ".env"),
                ("-", ".env.*"),
            ],
        )

    def test_compile_cleanup_filter_structure(self):
        rules = [
            ("-", "node_modules/**"),
            ("+", ".env.example"),
            ("-", ".env"),
            ("-", ".env.*"),
        ]
        cleanup_text = compile_cleanup_filter(rules)
        lines = [line.strip() for line in cleanup_text.splitlines() if line.strip() and not line.startswith("#")]

        # Protection rules must come first
        self.assertIn("- .env.example", lines)
        protection_idx = lines.index("- .env.example")

        # Deletion rules must follow
        self.assertIn("+ node_modules/**", lines)
        self.assertIn("+ .env", lines)
        self.assertIn("+ .env.*", lines)
        deletion_idx = lines.index("+ node_modules/**")

        self.assertLess(protection_idx, deletion_idx, "Protection rule must precede deletion targets")

        # Terminal guard must be the very last rule
        self.assertEqual(lines[-1], "- **", "Final rule must be terminal guard '- **'")

    def test_extract_bloat_roots(self):
        rules = [
            ("-", "node_modules/**"),
            ("-", "build/**"),
            ("-", ".venv/**"),
            ("-", ".env"),
            ("+", ".env.example"),
        ]
        raw_traversed_dirs = [
            "core_libraries/",
            "products/",
            "products/rrtools/",
            "products/rrtools/build/",
            "products/rrtools/build/nested/",
            "products/rrtools/src/",
            "web_and_services/",
            "web_and_services/enterprise_website/",
            "web_and_services/enterprise_website/node_modules/",
            "web_and_services/enterprise_website/node_modules/pkg/",
            "web_and_services/enterprise_website/node_modules/pkg/lib/",
            "research_and_experiments/servo_control/examples/",
        ]
        bloat = extract_bloat_roots(raw_traversed_dirs, rules)
        self.assertEqual(
            bloat,
            [
                "products/rrtools/build/",
                "web_and_services/enterprise_website/node_modules/",
            ],
        )
        # Verify clean source directories were not misclassified
        self.assertNotIn("core_libraries/", bloat)
        self.assertNotIn("products/", bloat)
        self.assertNotIn("products/rrtools/", bloat)
        self.assertNotIn("products/rrtools/src/", bloat)
        self.assertNotIn("research_and_experiments/servo_control/examples/", bloat)

    def test_rclone_live_cleanup_safety(self):
        """End-to-end empirical verification of rclone filtering using mock filesystem."""
        if shutil.which("rclone") is None:
            self.skipTest("rclone is not installed in PATH")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mock_tree = base / "mock_project"
            mock_tree.mkdir()

            # Create sensitive and wanted files (MUST SURVIVE)
            (mock_tree / "src").mkdir()
            (mock_tree / "src" / "index.py").write_text("print('hello')", encoding="utf-8")
            (mock_tree / ".env.example").write_text("API_KEY=sample", encoding="utf-8")
            (mock_tree / "README.md").write_text("# Readme", encoding="utf-8")

            # Create ignored bloat and secrets (MUST BE DELETED)
            (mock_tree / ".env").write_text("SECRET=12345", encoding="utf-8")
            (mock_tree / ".env.local").write_text("SECRET=local", encoding="utf-8")
            (mock_tree / ".DS_Store").write_text("metadata", encoding="utf-8")
            (mock_tree / "node_modules" / "express").mkdir(parents=True)
            (mock_tree / "node_modules" / "express" / "index.js").write_text("module.exports = {}", encoding="utf-8")
            (mock_tree / "sub" / ".venv" / "bin").mkdir(parents=True)
            (mock_tree / "sub" / ".venv" / "bin" / "python").write_text("binary", encoding="utf-8")

            # Source ignore file
            ignore_file = base / "rclone-ignore.txt"
            ignore_file.write_text(
                """
                # Secrets
                + **/.env.example
                - **/.env
                - **/.env.*
                - **/.DS_Store
                - **/node_modules/**
                - **/.venv/**
                """,
                encoding="utf-8",
            )

            cleanup_filter = base / "cleanup_filter.tmp"
            generate_cleanup_filter_file(ignore_file, cleanup_filter)

            # Run rclone delete with our compiled filter and --rmdirs
            cmd = [
                "rclone",
                "delete",
                str(mock_tree),
                "--filter-from",
                str(cleanup_filter),
                "--rmdirs",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"rclone delete failed: {res.stderr}")

            # Assert SURVIVED files
            self.assertTrue((mock_tree / "src" / "index.py").exists(), "Source file was deleted!")
            self.assertTrue((mock_tree / ".env.example").exists(), ".env.example was deleted!")
            self.assertTrue((mock_tree / "README.md").exists(), "README.md was deleted!")

            # Assert PURGED files
            self.assertFalse((mock_tree / ".env").exists(), ".env was not deleted!")
            self.assertFalse((mock_tree / ".env.local").exists(), ".env.local was not deleted!")
            self.assertFalse((mock_tree / ".DS_Store").exists(), ".DS_Store was not deleted!")
            self.assertFalse((mock_tree / "node_modules").exists(), "node_modules was not cleaned up!")
            self.assertFalse((mock_tree / "sub" / ".venv").exists(), ".venv was not cleaned up!")


if __name__ == "__main__":
    unittest.main()
