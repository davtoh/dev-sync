"""Unit tests for dev_sync.errors rclone error extraction and remediation."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dev_sync.errors import (
    RcloneExecutionError,
    build_rclone_error,
    extract_log_errors,
    suggest_remediation,
)


class TestRcloneErrorDiagnostics(unittest.TestCase):
    """Verify JSONL/stderr error extraction and actionable remediation output."""

    def test_extract_critical_error_and_suggest_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "push.jsonl"
            lines = [
                json.dumps({"level": "info", "msg": "Starting transaction limiter"}),
                json.dumps({
                    "level": "critical",
                    "msg": (
                        'Failed to create file system for "gdrive:projects": '
                        'couldn\'t fetch token: invalid_grant: maybe token expired? '
                        '- try refreshing with "rclone config reconnect gdrive{kxrVe}:"'
                    ),
                }),
            ]
            log_path.write_bytes("\n".join(lines).encode("utf-8"))

            cmd = ["rclone", "copy", "C:\\dev\\projects", "gdrive:projects", "--dry-run"]
            err = build_rclone_error(1, cmd, log_file=log_path)

            self.assertIsInstance(err, subprocess.CalledProcessError)
            self.assertIsInstance(err, RcloneExecutionError)
            self.assertEqual(len(err.detail.critical_errors), 1)
            self.assertIn("invalid_grant", err.detail.critical_errors[0])
            self.assertEqual(err.detail.suggestions, ["Run: rclone config reconnect gdrive:"])

            formatted = err.format_message(retry_cmd="python -m dev_sync push projects --dry-run")
            self.assertIn("Critical   :", formatted)
            self.assertIn("Suggestion : Run: rclone config reconnect gdrive:", formatted)
            self.assertIn("Retry      : python -m dev_sync push projects --dry-run", formatted)

    def test_fallback_to_stderr_when_no_log_file(self) -> None:
        errors = extract_log_errors(None, stderr="2026/11/12 ERROR : directory not found\n")
        self.assertEqual(errors, ["2026/11/12 ERROR : directory not found"])

        suggestions = suggest_remediation(errors, ["rclone", "lsd", "gdrive:missing"])
        self.assertEqual(suggestions, ["Verify remote path with: rclone lsd gdrive:"])

    def test_missing_config_section_suggestion(self) -> None:
        suggestions = suggest_remediation(
            ["Failed to create file system: didn't find section in config file"],
            ["rclone", "copy", "./src", "myremote:dst"],
        )
        self.assertEqual(
            suggestions,
            ["Run: rclone config  (create or verify the remote configuration)"],
        )


if __name__ == "__main__":
    unittest.main()

