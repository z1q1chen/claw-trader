#!/usr/bin/env bash
set -euo pipefail

if ! command -v openclaw &> /dev/null; then
    echo "Error: openclaw CLI not found."
    exit 1
fi

echo "=== Cron Jobs ==="
openclaw cron list 2>/dev/null || echo "No cron jobs found"

echo ""
echo "=== Recent Cron Logs ==="
openclaw cron logs --tail 20 2>/dev/null || echo "No logs available"

echo ""
echo "=== Wallet & Positions ==="
echo "To check wallet balance and positions, open a chat with OpenClaw and ask:"
echo '  "Show my wallet balance and all open Polymarket positions with P&L"'
