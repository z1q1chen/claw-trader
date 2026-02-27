# Claw Trader

An autonomous quantitative trading agent powered by LLMs. Monitors markets, detects technical signals, consults an AI brain for trade decisions, and executes through connected brokers.

## Architecture

```
Signal Engine → LLM Brain → Risk Engine → Execution Engine → Broker
     ↑                                                          |
     └──────────── Price Feeds ←────────────────────────────────┘
```

- **Signal Engine** — Sub-second technical analysis (RSI, MACD, Bollinger Bands, volume spikes)
- **LLM Brain** — AI-powered trade decisions via Gemini, OpenAI, Anthropic, or local models
- **Risk Engine** — Pre-trade risk checks, position limits, daily loss limits, kill switch
- **Execution Engine** — Routes orders to brokers with retry logic
- **Dashboard** — Real-time Next.js dashboard with WebSocket event stream

## Supported Brokers

| Broker | Markets | Status |
|--------|---------|--------|
| Interactive Brokers | Stocks, ETFs, Options | Full support via ib_insync |
| Polymarket | Prediction markets | CLOB API integration |

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- (Optional) IBKR TWS/Gateway for stock trading
- (Optional) Polygon RPC + Polymarket API key for prediction markets

### Backend

```bash
cd backend
cp ../.env.example .env  # Edit with your API keys
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Dashboard available at http://localhost:3000

### Using Local LLM

Claw Trader supports any OpenAI-compatible local LLM server (e.g., LM Studio, Ollama, vLLM):

1. Start your local LLM server (e.g., `lm-studio --port 1234`)
2. In the dashboard, select **Local (OpenAI-compatible)** as the provider
3. Set the model name (e.g., `mistral-7b-instruct`)
4. Set the base URL (e.g., `http://127.0.0.1:1234/v1`)
5. API key can be any non-empty string (e.g., `local`)

### Trading on Polymarket

1. Set `CT_POLYMARKET_API_KEY` and `CT_POLYMARKET_PRIVATE_KEY` in `.env`
2. Set `CT_POLYGON_RPC_URL` to a Polygon RPC endpoint
3. Connect the Polymarket broker from the dashboard
4. Browse trending markets and execute trades

## Configuration

All settings are configured via environment variables with the `CT_` prefix. See `.env.example` for the full list.

Key settings:
- `CT_GEMINI_API_KEY` / `CT_OPENAI_API_KEY` / `CT_ANTHROPIC_API_KEY` — LLM provider keys
- `CT_MAX_SINGLE_TRADE_USD` — Maximum trade size
- `CT_MAX_DAILY_LOSS_USD` — Daily loss limit (triggers kill switch)
- `CT_MAX_PORTFOLIO_EXPOSURE_USD` — Total exposure cap
- `CT_SIGNAL_SCAN_INTERVAL_MS` — Signal detection frequency (default 500ms)

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | System health check |
| `/api/llm/config` | GET/POST | LLM provider configuration |
| `/api/risk/config` | GET/POST | Risk limit configuration |
| `/api/risk/live` | GET | Live risk snapshot |
| `/api/risk/killswitch` | POST | Toggle kill switch |
| `/api/trade` | POST | Manual trade execution |
| `/api/decisions` | GET | LLM trade decisions |
| `/api/orders` | GET | Order history |
| `/api/positions` | GET | Open positions |
| `/api/signals` | GET | Detected signals |
| `/api/brokers` | GET | Connected brokers |
| `/api/broker/connect` | POST | Connect a broker |
| `/api/markets/trending` | GET | Trending Polymarket markets |
| `/api/markets/search` | GET | Search Polymarket markets |
| `/ws` | WebSocket | Real-time event stream |

## Testing

```bash
cd backend
pip install -r requirements-dev.txt
pytest tests/ -v
```

## License

MIT
