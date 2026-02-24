#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"

if [ ! -d "$OPENCLAW_HOME" ]; then
    echo "Error: OpenClaw home directory not found at $OPENCLAW_HOME"
    echo "Make sure OpenClaw is installed: npm install -g openclaw@latest && openclaw onboard --install-daemon"
    exit 1
fi

# Source .env if it exists
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
    echo "Loaded environment from .env"
else
    echo "Warning: No .env file found. Copy .env.example to .env and fill in your values."
    exit 1
fi

# Validate required variables
missing=()
[ -z "${CHAINSTACK_NODE:-}" ] && missing+=("CHAINSTACK_NODE")
[ -z "${POLYCLAW_PRIVATE_KEY:-}" ] && missing+=("POLYCLAW_PRIVATE_KEY")

if [ ${#missing[@]} -gt 0 ]; then
    echo "Error: Missing required environment variables: ${missing[*]}"
    exit 1
fi

if [ -z "${OPENROUTER_API_KEY:-}" ] && [ "${USE_LOCAL_MODEL:-false}" != "true" ]; then
    echo "Error: OPENROUTER_API_KEY is required unless USE_LOCAL_MODEL=true"
    exit 1
fi

# Choose config based on local model setting
if [ "${USE_LOCAL_MODEL:-false}" = "true" ]; then
    CONFIG_SOURCE="$PROJECT_DIR/config/local-model.json"
    echo "Using local model configuration"

    if [ -z "${LOCAL_MODEL_URL:-}" ]; then
        echo "Warning: LOCAL_MODEL_URL not set, defaulting to http://127.0.0.1:1234/v1"
    fi
else
    CONFIG_SOURCE="$PROJECT_DIR/config/openclaw-trading.json"
    echo "Using hosted API configuration"
fi

# Perform environment variable substitution and write config
envsubst < "$CONFIG_SOURCE" > "$OPENCLAW_HOME/openclaw.json"
echo "Config written to $OPENCLAW_HOME/openclaw.json"

# Verify polyclaw skill is installed
if [ ! -d "$OPENCLAW_HOME/skills/polyclaw" ]; then
    echo ""
    echo "PolyClaw skill not found. Installing..."
    clawhub install polyclaw
    cd "$OPENCLAW_HOME/skills/polyclaw" && uv sync
    echo "PolyClaw installed."
fi

echo ""
echo "Configuration applied. Next steps:"
echo "  1. Run wallet approval (one-time): cd $OPENCLAW_HOME/skills/polyclaw && uv run python scripts/polyclaw.py wallet approve"
echo "  2. Start trading: ./scripts/start-trading.sh"
