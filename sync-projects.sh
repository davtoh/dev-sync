#!/usr/bin/env bash
# ==============================================================================
# dev-sync: Cross-platform development workspace sync utility via rclone
# Shell runner for macOS, Linux, and Android (Termux).
# Forwards execution to the unified Python core engine (dev_sync).
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Discover available Python 3 executable
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Error: Python 3 is required to run dev-sync but was not found in PATH." >&2
    echo "Please install Python (e.g. 'brew install python', 'apt install python3', or 'pkg install python' in Termux)." >&2
    exit 1
fi

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m dev_sync "$@"
