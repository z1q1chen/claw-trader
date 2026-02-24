#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Local LLM Setup for claw-trader ==="
echo ""
echo "Supported local model servers:"
echo "  1) Ollama       - ollama serve                        → http://localhost:11434/v1"
echo "  2) LM Studio    - Start from GUI                      → http://localhost:1234/v1"
echo "  3) vLLM         - python -m vllm.entrypoints.openai.api_server → http://localhost:8000/v1"
echo "  4) llama.cpp    - ./server -m model.gguf --port 8080  → http://localhost:8080/v1"
echo "  5) LiteLLM      - litellm --model ollama/<model>      → http://localhost:4000/v1"
echo ""

read -rp "Which server are you using? (1-5): " choice

case $choice in
    1) default_url="http://localhost:11434/v1"; server="Ollama" ;;
    2) default_url="http://localhost:1234/v1"; server="LM Studio" ;;
    3) default_url="http://localhost:8000/v1"; server="vLLM" ;;
    4) default_url="http://localhost:8080/v1"; server="llama.cpp" ;;
    5) default_url="http://localhost:4000/v1"; server="LiteLLM" ;;
    *) echo "Invalid choice"; exit 1 ;;
esac

read -rp "Server URL [$default_url]: " url
url="${url:-$default_url}"

echo ""
echo "Checking server at $url ..."
if curl -s --max-time 5 "${url}/models" > /dev/null 2>&1; then
    echo "Server is reachable."
    echo ""
    echo "Available models:"
    curl -s "${url}/models" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    models = data.get('data', [])
    for m in models:
        print(f\"  - {m.get('id', 'unknown')}\")
    if not models:
        print('  (no models loaded)')
except:
    print('  (could not parse model list)')
" 2>/dev/null || echo "  (could not list models)"
else
    echo "Warning: Server not reachable at $url. Make sure it's running before trading."
fi

read -rp "Model name to use: " model_name

if [ -z "$model_name" ]; then
    echo "Error: Model name is required."
    exit 1
fi

# Update .env file
ENV_FILE="$PROJECT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
fi

# Set local model variables in .env
set_env_var() {
    local key=$1 val=$2 file=$3
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        echo "${key}=${val}" >> "$file"
    fi
}

set_env_var "USE_LOCAL_MODEL" "true" "$ENV_FILE"
set_env_var "LOCAL_MODEL_URL" "$url" "$ENV_FILE"
set_env_var "LOCAL_MODEL_NAME" "$model_name" "$ENV_FILE"

echo ""
echo "Updated .env with:"
echo "  USE_LOCAL_MODEL=true"
echo "  LOCAL_MODEL_URL=$url"
echo "  LOCAL_MODEL_NAME=$model_name"
echo ""
echo "Using $server with model '$model_name'"
echo ""
echo "Next: run ./scripts/apply-config.sh to apply the configuration."

# Hybrid mode recommendation
echo ""
echo "=== Recommendation ==="
echo "For trading, hybrid mode works best:"
echo "  - Local model handles: market browsing, trade execution, position checks"
echo "  - API fallback handles: hedge discovery (requires strong reasoning)"
echo ""
echo "To enable hybrid mode, also set OPENROUTER_API_KEY in your .env file."
echo "The local model will be tried first; API is used only when needed."
