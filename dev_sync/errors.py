"""Structured rclone error extraction and actionable remediation suggestions."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_RECONNECT_RE = re.compile(r"rclone\s+config\s+reconnect\s+([^\s\"']+)", re.IGNORECASE)
_REMOTE_ARG_RE = re.compile(r"^([A-Za-z0-9_.-]+):")
_HASH_SUFFIX_RE = re.compile(r"\{[^}]+\}")
_TEXT_ERR_RE = re.compile(r"^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+(CRITICAL|ERROR)\s*:\s*(.*)$")


@dataclass
class RcloneErrorDetail:
    """Captured diagnostic details and remediation steps for a failed rclone call."""

    returncode: int
    cmd: List[str]
    log_file: Optional[Path] = None
    critical_errors: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


class RcloneExecutionError(subprocess.CalledProcessError):
    """Raised when rclone fails, carrying parsed critical errors and fix suggestions."""

    def __init__(
        self,
        detail: RcloneErrorDetail,
        output: Optional[str] = None,
        stderr: Optional[str] = None,
    ) -> None:
        super().__init__(detail.returncode, detail.cmd, output=output, stderr=stderr)
        self.detail = detail

    def format_message(self, retry_cmd: Optional[str] = None) -> str:
        """Format a concise, actionable error summary for terminal output."""
        lines = [f"rclone exited with status {self.detail.returncode}."]
        if self.detail.log_file:
            lines.append(f"  Log file   : {self.detail.log_file}")

        for err in self.detail.critical_errors[:3]:
            lines.append(f"  Critical   : {err}")

        if self.detail.suggestions:
            for idx, suggestion in enumerate(self.detail.suggestions):
                label = "Suggestion " if idx == 0 else "           "
                lines.append(f"  {label}: {suggestion}")
            retry_text = retry_cmd if retry_cmd else "Re-run the command after completing the steps above."
            lines.append(f"  Retry      : {retry_text}")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format_message()


def _clean_remote_name(raw_remote: str) -> str:
    """Normalize an rclone remote reference (e.g. 'gdrive{kxrVe}:proj' -> 'gdrive:')."""
    cleaned = _HASH_SUFFIX_RE.sub("", raw_remote.strip())
    if ":" in cleaned:
        return cleaned.split(":", 1)[0] + ":"
    return cleaned + ":"


def _find_remote_in_cmd(cmd: List[str]) -> Optional[str]:
    """Find the first rclone remote argument (e.g. 'gdrive:projects') in a command list."""
    for arg in cmd[1:]:
        if arg.startswith("-"):
            continue
        # Skip Windows drive letters like 'C:\dev\projects'
        if len(arg) >= 2 and arg[1] == ":" and (len(arg) == 2 or arg[2] in ("\\", "/")):
            continue
        match = _REMOTE_ARG_RE.match(arg)
        if match:
            return f"{match.group(1)}:"
    return None


def extract_log_errors(log_file: Optional[Path], stderr: str = "") -> List[str]:
    """Extract unique critical/error messages from an rclone JSONL/text log or stderr."""
    errors: List[str] = []
    seen: set[str] = set()

    def _add(msg: str) -> None:
        clean = msg.strip()
        if clean and clean not in seen:
            seen.add(clean)
            errors.append(clean)

    if log_file and log_file.exists():
        content = log_file.read_text(encoding="utf-8", errors="replace")
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                level = str(data.get("level", "")).lower()
                if level in ("critical", "error"):
                    _add(str(data.get("msg", "")))
            else:
                m = _TEXT_ERR_RE.match(line)
                if m:
                    _add(m.group(2))

    if not errors and stderr:
        for line in stderr.splitlines():
            stripped = line.strip()
            if stripped:
                _add(stripped)
            if len(errors) >= 5:
                break

    return errors


def suggest_remediation(error_messages: List[str], cmd: List[str]) -> List[str]:
    """Map known rclone failure patterns to copy-pasteable remediation commands."""
    joined = "\n".join(error_messages)
    lower = joined.lower()
    suggestions: List[str] = []
    remote = _find_remote_in_cmd(cmd)

    if any(k in lower for k in ("invalid_grant", "token expired", "couldn't fetch token", "config reconnect")):
        match = _RECONNECT_RE.search(joined)
        target_remote = _clean_remote_name(match.group(1)) if match else (remote or "<remote>:")
        suggestions.append(f"Run: rclone config reconnect {target_remote}")
    elif "didn't find section in config file" in lower:
        suggestions.append("Run: rclone config  (create or verify the remote configuration)")
    elif "directory not found" in lower or "object not found" in lower:
        target_remote = remote or "<remote>:"
        suggestions.append(f"Verify remote path with: rclone lsd {target_remote}")
    elif any(k in lower for k in ("rate limit", "userratelimitexceeded", "429", "too many requests")):
        suggestions.append("Wait briefly and retry, or reduce concurrency/TPS pacing flags.")

    return suggestions


def build_rclone_error(
    returncode: int,
    cmd: List[str],
    log_file: Optional[Path] = None,
    output: Optional[str] = None,
    stderr: Optional[str] = None,
) -> RcloneExecutionError:
    """Build a structured RcloneExecutionError from a failed rclone subprocess."""
    critical_errors = extract_log_errors(log_file, stderr or "")
    suggestions = suggest_remediation(critical_errors, cmd)
    detail = RcloneErrorDetail(
        returncode=returncode,
        cmd=cmd,
        log_file=log_file,
        critical_errors=critical_errors,
        suggestions=suggestions,
    )
    return RcloneExecutionError(detail, output=output, stderr=stderr)

