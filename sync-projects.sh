#!/usr/bin/env bash
# ==============================================================================
# dev-sync: Cross-platform sync and cleanup utility between local workspaces
# and Google Drive via rclone (macOS / Linux).
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BASE="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$SCRIPT_DIR/sync-targets.conf"
CONFIG_EXAMPLE="$SCRIPT_DIR/sync-targets.conf.example"
FILTER_FILE="$SCRIPT_DIR/rclone-ignore.txt"

ACTION="${1:-}"
TARGET="${2:-all}"
DRY_RUN_ARG="${3:-}"

if [[ -z "$ACTION" ]]; then
    echo "Usage: ./sync-projects.sh <pull|push|check|diagnose|cleanup> [target_name|all] [--dry-run]"
    exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    if [[ -f "$CONFIG_EXAMPLE" ]]; then
        echo "sync-targets.conf not found. Creating from sync-targets.conf.example..."
        cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
    else
        echo "Error: Configuration file not found: $CONFIG_FILE" >&2
        exit 1
    fi
fi

if [[ ! -f "$FILTER_FILE" ]]; then
    echo "Error: Filter file not found: $FILTER_FILE" >&2
    exit 1
fi

BASE_FLAGS=(-P)
if [[ "$DRY_RUN_ARG" == "--dry-run" ]]; then
    BASE_FLAGS+=(--dry-run)
fi

execute_target() {
    local target_name="$1"
    local raw_local="$2"
    local remote_path="$3"

    # Replace $LOCAL_BASE placeholder
    local local_path="${raw_local//\$LOCAL_BASE/$LOCAL_BASE}"

    echo ""
    echo "========================================================"
    echo "Target: $target_name"
    echo "Local : $local_path"
    echo "Remote: $remote_path"
    echo "Action: $ACTION ${DRY_RUN_ARG:-}"
    echo "========================================================"

    case "$ACTION" in
        diagnose)
            echo "--> Scanning remote directory structure..."
            rclone lsd "$remote_path" --max-depth 2 || true
            echo "--> Scanning remote for build artifacts..."
            local patterns=("**/node_modules/" "**/build/" "**/target/" "**/.gradle/" "**/.dart_tool/" "**/Pods/" "**/.venv/")
            for p in "${patterns[@]}"; do
                rclone lsf "$remote_path" --dirs-only --recursive --include "$p" || true
            done
            ;;
        cleanup)
            echo "--> Cleaning up ignored transient build folders on remote..."
            rclone delete "$remote_path" \
                --include "**/node_modules/**" \
                --include "**/target/**" \
                --include "**/build/**" \
                --include "**/.gradle/**" \
                --include "**/.dart_tool/**" \
                --include "**/Pods/**" \
                --include "**/DerivedData/**" \
                --include "**/.venv/**" \
                --include "**/__pycache__/**" \
                --include "**/*.apk" \
                --include "**/*.aab" \
                --include "**/.DS_Store" \
                --include "**/Thumbs.db" \
                --rmdirs \
                "${BASE_FLAGS[@]}"
            ;;
        check)
            if [[ ! -d "$local_path" ]]; then
                echo "Warning: Local directory does not exist: $local_path"
                return 0
            fi
            rclone check "$local_path" "$remote_path" --filter-from "$FILTER_FILE"
            ;;
        pull)
            mkdir -p "$local_path"
            rclone copy "$remote_path" "$local_path" --filter-from "$FILTER_FILE" "${BASE_FLAGS[@]}"
            ;;
        push)
            if [[ ! -d "$local_path" ]]; then
                echo "Error: Local directory does not exist: $local_path" >&2
                exit 1
            fi
            rclone copy "$local_path" "$remote_path" --filter-from "$FILTER_FILE" "${BASE_FLAGS[@]}"
            ;;
        *)
            echo "Error: Unknown action '$ACTION'. Valid actions: pull, push, check, diagnose, cleanup" >&2
            exit 1
            ;;
    esac
}

found_any=0
while IFS='=' read -r name mapping || [[ -n "$name" ]]; do
    [[ -z "$name" || "$name" =~ ^[[:space:]]*# ]] && continue
    target_name="$(echo "$name" | xargs)"
    local_part="$(echo "$mapping" | cut -d'|' -f1 | xargs)"
    remote_part="$(echo "$mapping" | cut -d'|' -f2 | xargs)"

    if [[ "$TARGET" == "all" || "$TARGET" == "$target_name" ]]; then
        found_any=1
        execute_target "$target_name" "$local_part" "$remote_part"
    fi
done < "$CONFIG_FILE"

if [[ "$found_any" -eq 0 ]]; then
    echo "Error: Target '$TARGET' not found in $CONFIG_FILE" >&2
    exit 1
fi

echo -e "\nCompleted action '$ACTION' successfully."
