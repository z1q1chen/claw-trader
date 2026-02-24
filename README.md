# claw-trader

Automated Polymarket trading agent powered by [OpenClaw](https://github.com/openclaw/openclaw) + [PolyClaw](https://github.com/chainstacklabs/polyclaw).

This repo contains the configuration, automation scripts, and setup instructions for running a self-hosted Polymarket trading bot. OpenClaw setup is handled separately via its own onboarding wizard.

## Prerequisites

- [OpenClaw](https://github.com/openclaw/openclaw) installed and running (`openclaw onboard --install-daemon`)
- Node >= 22
- Python >= 3.11 with [uv](https://github.com/astral-sh/uv) package manager
- A funded Polygon wallet (USDC.e for trading, POL for gas)
- A Polygon RPC endpoint (free tier from [Chainstack](https://chainstack.com/) works)

## Quick Start

```bash
# 1. Install the PolyClaw skill
clawhub install polyclaw
cd ~/.openclaw/skills/polyclaw && uv sync

# 2. Copy config into OpenClaw
cp config/openclaw-trading.json ~/.openclaw/openclaw.json

# 3. Set your environment variables
cp .env.example .env
# Edit .env with your keys

# 4. Source env and apply config
source .env
./scripts/apply-config.sh

# 5. One-time wallet approval (~0.01 POL gas)
cd ~/.openclaw/skills/polyclaw
uv run python scripts/polyclaw.py wallet approve

# 6. Start automated trading
./scripts/start-trading.sh
```

## Project Structure

```
claw-trader/
├── README.md
├── .env.example              # Environment variable template
├── config/
│   ├── openclaw-trading.json # OpenClaw config with model + skill settings
│   ├── cron-jobs.json        # Automated trading schedule definitions
│   └── local-model.json      # Config overlay for local LLM setup
├── scripts/
│   ├── apply-config.sh       # Merges config into ~/.openclaw/
│   ├── start-trading.sh      # Registers cron jobs and starts trading
│   ├── stop-trading.sh       # Removes cron jobs
│   ├── check-status.sh       # Shows wallet balance + active positions
│   └── setup-local-model.sh  # Helper to configure local LLM endpoint
└── strategies/
    ├── scan-and-trade.md     # Default strategy prompt for market scanning
    ├── hedge-discovery.md    # Hedge-finding strategy prompt
    └── conservative.md       # Lower-risk strategy prompt
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CHAINSTACK_NODE` | Yes | Polygon RPC endpoint URL |
| `POLYCLAW_PRIVATE_KEY` | Yes | EVM wallet private key (hex, no 0x prefix) |
| `OPENROUTER_API_KEY` | Yes* | API key for LLM hedge analysis |
| `TRADE_MAX_POSITION` | No | Max USD per position (default: 50) |
| `TRADE_MIN_EDGE` | No | Minimum perceived edge % to trade (default: 5) |
| `SCAN_INTERVAL_MINUTES` | No | How often to scan markets (default: 30) |
| `ANNOUNCE_CHANNEL` | No | Channel for trade notifications (e.g., `slack`, `telegram`) |
| `ANNOUNCE_TARGET` | No | Channel ID for notifications |

*Not required if using a local model for hedge analysis.

### Using a Local LLM Instead of API

See [Local Model Setup](#local-model-setup) below.

## Strategies

Strategy files in `strategies/` are prompt templates that tell the agent how to trade. The default `scan-and-trade.md` scans trending markets and executes trades when it finds edges above your threshold.

You can customize strategies or create new ones. Reference them in `config/cron-jobs.json`.

## Trading Automation

The bot uses OpenClaw's built-in cron scheduler. Trading jobs are defined in `config/cron-jobs.json` and registered via `scripts/start-trading.sh`.

Default schedule:
- **Market scan**: Every 30 minutes, scans trending markets for opportunities
- **Position check**: Every 2 hours, reviews open positions and P&L
- **Hedge scan**: Every 6 hours, runs LLM-powered hedge discovery

## Local Model Setup

You can replace hosted LLM APIs with a local model. Any server exposing an OpenAI-compatible `/v1` endpoint works.

### Supported Local Servers

| Server | Command | Endpoint |
|--------|---------|----------|
| Ollama | `ollama serve` | `http://localhost:11434/v1` |
| LM Studio | Start from GUI | `http://localhost:1234/v1` |
| vLLM | `python -m vllm.entrypoints.openai.api_server --model <model>` | `http://localhost:8000/v1` |
| llama.cpp | `./server -m model.gguf --port 8080` | `http://localhost:8080/v1` |
| LiteLLM | `litellm --model ollama/<model>` | `http://localhost:4000/v1` |

### Hardware Requirements

| Model Size | VRAM Needed | Example Hardware | Trading Capability |
|------------|-------------|------------------|-------------------|
| 8B (Q4) | 6-8 GB | RTX 3060/4060 | Basic commands, no hedge analysis |
| 14B (Q4) | 10-16 GB | RTX 4080 | Decent reasoning, slow hedges |
| 70B (Q4) | 40-48 GB | 2x RTX 3090 / Mac Studio M2 Ultra | Good quality hedge analysis |
| 70B (FP16) | 140 GB+ | 2x Mac Studio M4 Ultra | Near-API quality |

### Setup

```bash
# Using the helper script (interactive):
./scripts/setup-local-model.sh

# Or manually: apply the local model config overlay
cp config/local-model.json ~/.openclaw/openclaw.json
# Edit the baseUrl and model name to match your local server
```

### Hybrid Mode (Recommended)

Run cheap tasks locally, fall back to API for complex reasoning:

The default `config/local-model.json` configures this. Your local model handles basic trade execution and market browsing. Hedge discovery (which requires strong logical reasoning) falls back to the hosted API.

## Key Contracts (Polygon)

| Contract | Address |
|----------|---------|
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |
| Conditional Token Framework | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |

## Risk Disclaimer

This software is for educational and experimental purposes only. It is not financial advice. Trading prediction markets involves risk of loss. The code is unaudited. Only use funds you can afford to lose. Keep small amounts in your trading wallet.

## License

MIT
