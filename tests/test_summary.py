"""Unit tests for summary aggregation, log parsing, and risk assessment."""

import tempfile
import unittest
from pathlib import Path

from dev_sync.summary import (
    CONTAINER_ROOT_PREFIXES,
    PathEvent,
    build_summary,
    extract_root_folder,
    format_bytes,
    format_terminal_summary,
    parse_log_events,
)


class TestSummaryEngine(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(format_bytes(100), "100 B")
        self.assertEqual(format_bytes(1024), "1.0 KiB")
        self.assertEqual(format_bytes(1048576), "1.0 MiB")
        self.assertEqual(format_bytes(1073741824), "1.0 GiB")

    def test_extract_root_folder_nested(self):
        self.assertEqual(extract_root_folder("products/rrtools/main.py"), "products/rrtools/")
        self.assertEqual(extract_root_folder("products/rrtools/.git/objects/ff/hash"), "products/rrtools/")
        self.assertEqual(extract_root_folder("packages/core_lib/src/index.ts"), "packages/core_lib/")
        self.assertEqual(extract_root_folder("apps/web/package.json"), "apps/web/")
        self.assertEqual(extract_root_folder("simple_app/src/index.js"), "simple_app/")
        self.assertEqual(extract_root_folder("README.md"), "(root)/")
        self.assertEqual(extract_root_folder(""), "(root)/")

    def test_parse_jsonl_log_events(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False, encoding="utf-8") as tf:
            log_path = Path(tf.name)
            tf.write(
                '{"time":"2026-11-02T21:00:00","level":"notice","msg":"Skipped copy as --dry-run is set","skipped":"copy","size":5000,"object":"products/rrtools/src/app.py"}\n'
                '{"time":"2026-11-02T21:00:01","level":"notice","msg":"Skipped delete as --dry-run is set","skipped":"delete","size":1200,"object":"products/rrtools/dist/bundle.js"}\n'
                '{"time":"2026-11-02T21:00:02","level":"notice","msg":"Skipped copy that would replace existing file","skipped":"copy","size":2000,"object":"web/index.html"}\n'
                '{"time":"2026-11-02T21:00:03","level":"error","msg":"sizes differ","object":"config.json"}\n'
                '{"time":"2026-11-02T21:00:04","level":"notice","msg":"1 differences found","object":"Local file system at //dst"}\n'
            )

        try:
            events = parse_log_events(log_path)
            self.assertEqual(len(events), 4)  # Skipped Fs-level entry

            self.assertEqual(events[0].path, "products/rrtools/src/app.py")
            self.assertEqual(events[0].root_folder, "products/rrtools/")
            self.assertEqual(events[0].action, "copy")
            self.assertEqual(events[0].size, 5000)

            self.assertEqual(events[1].path, "products/rrtools/dist/bundle.js")
            self.assertEqual(events[1].action, "delete")
            self.assertEqual(events[1].size, 1200)

            self.assertEqual(events[2].path, "web/index.html")
            self.assertEqual(events[2].action, "overwrite")

            self.assertEqual(events[3].path, "config.json")
            self.assertEqual(events[3].action, "difference")
        finally:
            if log_path.exists():
                log_path.unlink()

    def test_parse_text_log_fallback(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False, encoding="utf-8") as tf:
            log_path = Path(tf.name)
            tf.write(
                "2026/11/02 21:00:00 NOTICE: products/rrtools/foo.py: Skipped copy as --dry-run is set\n"
                "2026/11/02 21:00:01 ERROR : products/rrtools/bar.py: file not in Local file system at //dst\n"
            )

        try:
            events = parse_log_events(log_path)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0].path, "products/rrtools/foo.py")
            self.assertEqual(events[0].action, "copy")
            self.assertEqual(events[1].path, "products/rrtools/bar.py")
            self.assertEqual(events[1].action, "missing_remote")
        finally:
            if log_path.exists():
                log_path.unlink()

    def test_build_summary_risk_warnings(self):
        events = [
            PathEvent(path="products/rrtools/a.py", root_folder="products/rrtools/", action="copy", size=1000),
            PathEvent(path="products/rrtools/b.py", root_folder="products/rrtools/", action="overwrite", size=2000),
            PathEvent(path="web/dead.js", root_folder="web/", action="delete", size=500),
            PathEvent(path="web/err.js", root_folder="web/", action="error", size=0),
        ]
        summary = build_summary(
            target_name="projects",
            action="push",
            dry_run=True,
            events=events,
        )

        self.assertEqual(summary.total_files, 4)
        self.assertEqual(summary.total_bytes, 3500)
        self.assertEqual(summary.copied_count, 1)
        self.assertEqual(summary.overwritten_count, 1)
        self.assertEqual(summary.deleted_count, 1)
        self.assertEqual(summary.error_count, 1)
        self.assertIn("products/rrtools/", summary.root_stats)
        self.assertIn("web/", summary.root_stats)

        # Risk warnings check
        warning_text = " ".join(summary.warnings)
        self.assertIn("OVERWRITE", warning_text)
        self.assertIn("targeted for DELETION", warning_text)
        self.assertIn("error(s) occurred", warning_text)

    def test_format_terminal_summary_output(self):
        events = [
            PathEvent(path="products/rrtools/a.py", root_folder="products/rrtools/", action="copy", size=1024),
        ]
        summary = build_summary(
            target_name="projects",
            action="push",
            dry_run=True,
            events=events,
        )
        rendered = format_terminal_summary(summary)
        self.assertIn("dev-sync Execution Summary", rendered)
        self.assertIn("products/rrtools/", rendered)
        self.assertIn("[DRY-RUN PREVIEW]", rendered)
        self.assertIn("TOTAL", rendered)


if __name__ == "__main__":
    unittest.main()

