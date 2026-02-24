#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Source .env
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

CRON_CONFIG="$PROJECT_DIR/config/cron-jobs.json"
STRATEGIES_DIR="$PROJECT_DIR/strategies"

if ! command -v openclaw &> /dev/null; then
    echo "Error: openclaw CLI not found. Install it: npm install -g openclaw@latest"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo "Error: jq not found. Install it: apt install jq / brew install jq"
    exit 1
fi

echo "Registering trading cron jobs..."

job_count=$(jq '.jobs | length' "$CRON_CONFIG")
for i in $(seq 0 $((job_count - 1))); do
    name=$(jq -r ".jobs[$i].name" "$CRON_CONFIG")
    every=$(jq -r ".jobs[$i].every" "$CRON_CONFIG")
    session=$(jq -r ".jobs[$i].session" "$CRON_CONFIG")
    strategy_file=$(jq -r ".jobs[$i].strategy_file" "$CRON_CONFIG")
    announce=$(jq -r ".jobs[$i].announce" "$CRON_CONFIG")

    # Resolve interval env vars
    every=$(echo "$every" | envsubst)

    # Read strategy prompt from file
    strategy_path="$PROJECT_DIR/$strategy_file"
    if [ ! -f "$strategy_path" ]; then
        echo "Warning: Strategy file not found: $strategy_path, skipping $name"
        continue
    fi
    message=$(cat "$strategy_path")

    # Build cron add command
    cmd=(openclaw cron add --name "$name" --every "$every" --session "$session" --message "$message")

    if [ "$announce" = "true" ] && [ -n "${ANNOUNCE_CHANNEL:-}" ] && [ -n "${ANNOUNCE_TARGET:-}" ]; then
        cmd+=(--announce --channel "$ANNOUNCE_CHANNEL" --to "$ANNOUNCE_TARGET")
    fi

    # Remove existing job with same name (ignore errors if not found)
    openclaw cron remove --name "$name" 2>/dev/null || true

    "${cmd[@]}"
    echo "  Registered: $name (every $every)"
done

echo ""
echo "All trading jobs registered. View with: openclaw cron list"
echo "Monitor logs with: openclaw cron logs"
