#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CRON_CONFIG="$PROJECT_DIR/config/cron-jobs.json"

if ! command -v openclaw &> /dev/null; then
    echo "Error: openclaw CLI not found."
    exit 1
fi

echo "Removing trading cron jobs..."

job_count=$(jq '.jobs | length' "$CRON_CONFIG")
for i in $(seq 0 $((job_count - 1))); do
    name=$(jq -r ".jobs[$i].name" "$CRON_CONFIG")
    openclaw cron remove --name "$name" 2>/dev/null && echo "  Removed: $name" || echo "  Not found: $name"
done

echo ""
echo "All trading jobs removed."
