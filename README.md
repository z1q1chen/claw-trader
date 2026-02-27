# Claw Trader

Autonomous quantitative trading agent with LLM-driven decision making. Supports multiple brokers (IBKR, Polymarket) and configurable LLM providers (Gemini, OpenAI, Anthropic, local models).

## Architecture

```
Signal Engine (500ms) → Event Bus → LLM Brain → Risk Engine → Execution Engine → Broker
     ↓                     ↓                                                        ↓
  Signals DB          WebSocket → Dashboard                                    Orders DB
```

### Components

- **Signal Engine**: Sub-second technical analysis (RSI, MACD, Bollinger Bands, volume spikes) with configurable cooldowns
- **LLM Brain**: Pluggable providers (Gemini, OpenAI, Anthropic, local via OpenAI-compatible API) with rate limiting
- **Risk Engine**: Pre-trade checks (position limits, concentration, VaR, drawdown kill switch)
- **Execution Engine**: Broker adapter pattern decoupling strategy from execution
- **Event Bus**: Async pub/sub with WebSocket broadcast to dashboard
- **Dashboard**: Next.js real-time UI for configuration, monitoring, and manual trading

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- (Optional) IBKR TWS or IB Gateway for live trading
- (Optional) Docker for containerized deployment

### Backend

```bash
cd backend
cp ../.env.example ../.env  # Edit with your API keys
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000 to access the dashboard.

### Docker

```bash
cp .env.example .env  # Edit with your API keys
docker compose up
```

## Configuration

All settings use the `CT_` prefix and can be set via environment variables or `.env` file.

| Setting | Default | Description |
|---------|---------|-------------|
| `CT_DEFAULT_LLM_PROVIDER` | `gemini` | LLM provider: gemini, openai, anthropic, local |
| `CT_GEMINI_API_KEY` | | Google Gemini API key |
| `CT_OPENAI_API_KEY` | | OpenAI API key |
| `CT_IBKR_PORT` | `7497` | IBKR port (7497=paper, 7496=live) |
| `CT_MAX_SINGLE_TRADE_USD` | `2000` | Max single trade size |
| `CT_MAX_DAILY_LOSS_USD` | `5000` | Daily loss kill switch threshold |
| `CT_MAX_PORTFOLIO_EXPOSURE_USD` | `50000` | Max total portfolio exposure |
| `CT_MAX_DRAWDOWN_PCT` | `10` | Max drawdown % before kill switch |
| `CT_SIGNAL_SCAN_INTERVAL_MS` | `500` | Signal detection interval |
| `CT_RATE_LIMIT_RPM` | `120` | API rate limit per IP per minute |

## Local LLM Support

Connect any OpenAI-compatible local server:

1. Start your local LLM server (Ollama, LM Studio, vLLM, etc.)
2. In the dashboard, set Provider to "Local", enter the model name, and set Base URL (e.g., `http://127.0.0.1:1234/v1`)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | System health and engine status |
| GET/POST | `/api/llm/config` | LLM configuration |
| GET | `/api/usage/summary` | API usage statistics |
| GET | `/api/decisions` | Trade decisions history |
| GET | `/api/orders` | Order history |
| GET | `/api/positions` | Current positions |
| GET | `/api/balance/{broker}` | Account balance |
| GET/POST | `/api/risk/config` | Risk limit configuration |
| GET | `/api/risk/live` | Live risk metrics |
| POST | `/api/risk/killswitch` | Toggle kill switch |
| GET | `/api/brokers` | List connected brokers |
| POST | `/api/broker/connect` | Connect to broker |
| POST | `/api/trade` | Place manual trade |
| WS | `/ws` | Real-time event stream |

## Testing

```bash
cd backend
python -m pytest tests/ -v
python -m pytest tests/ --cov=app --cov-report=term-missing
```

## Project Structure

```
backend/
  app/
    api/routes.py        # FastAPI endpoints
    brokers/             # Broker adapters (IBKR, Polymarket)
    core/                # Config, database, events, logging, middleware
    engines/             # Signal, LLM, risk, execution engines
    feeds/               # Price feed implementations
  tests/                 # 162+ tests
frontend/
  src/
    app/page.tsx         # Dashboard UI
    lib/api.ts           # API client
```

## Risk Management

| Check | Default | Description |
|-------|---------|-------------|
| Single trade limit | $2,000 | Max USD per trade |
| Position concentration | 20% | Max % of portfolio in one symbol |
| Total exposure | $50,000 | Max total portfolio exposure |
| Daily loss limit | $5,000 | Triggers kill switch if breached |
| Max drawdown | 10% | Triggers kill switch if breached |
| VaR (95%) | Calculated | Value at Risk from return history |
| Kill switch | Dashboard | Manual emergency stop for all trading |

## IBKR Setup

1. Download [IBKR TWS](https://www.interactivebrokers.com/en/trading/tws.php) or IB Gateway
2. Enable API: Configure > API > Settings > Enable ActiveX and Socket Clients
3. Use port 7497 for paper trading, 7496 for live trading
4. Set `CT_IBKR_PORT=7497` in your `.env`

## Features Implemented

- Signal engine with RSI, MACD, Bollinger Bands, volume spike detection
- LLM brain with Gemini, OpenAI, Anthropic providers (+ local via OpenAI-compatible API)
- Risk engine with VaR, drawdown, position limits, kill switch
- Execution engine with broker adapter pattern
- IBKR broker adapter (full implementation)
- Polymarket adapter (market data implemented, execution stubbed)
- Dashboard: LLM config, API usage, decisions, orders, positions, risk, live events
- Real-time WebSocket event stream
- SQLite persistence for all decisions, orders, and API usage

## Future Roadmap

- Polymarket on-chain trade execution (CTF split + CLOB)
- Strategy framework (pluggable strategy modules)
- Backtesting engine
- Multi-timeframe signal analysis
- Options chain data integration
- News/sentiment feed (LLM-analyzed)

## License

MIT
