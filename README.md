# Claw Trader

Autonomous quant trading agent with LLM-driven decision making, real-time signal detection, and multi-broker execution.

## Architecture

```
┌──────────────────── Next.js Dashboard (port 3000) ──────────────────┐
│  LLM Config │ Balance │ Positions │ Orders │ P&L │ Risk │ Live Log  │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ WebSocket + REST
┌───────────────────────────┴─────────────────────────────────────────┐
│                   Python Backend (FastAPI, port 8000)                │
│                                                                     │
│  ┌────────────┐  ┌───────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ Signal     │  │ LLM Brain │  │ Risk     │  │ Execution      │  │
│  │ Engine     │─>│ (Gemini+) │─>│ Engine   │─>│ Engine         │  │
│  │ (sub-sec)  │  │ (seconds) │  │ (gates)  │  │ (IBKR/Poly)   │  │
│  └────────────┘  └───────────┘  └──────────┘  └────────────────┘  │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  SQLite (local) │ Event Bus (async) │ WebSocket broadcast   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Pipeline: Signal → Brain → Risk → Execute

1. **Signal Engine** (sub-second): Monitors price feeds, computes RSI/MACD/Bollinger/volume indicators, emits signals when thresholds breach.
2. **LLM Brain** (seconds): Receives signals, asks configured LLM (Gemini/OpenAI/local) for autonomous trade decisions with confidence scores.
3. **Risk Engine** (instant): Gates every trade through position limits, daily loss limits, drawdown protection, VaR, and a kill switch.
4. **Execution Engine**: Routes approved trades to the appropriate broker adapter (IBKR for stocks, Polymarket for prediction markets).

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example ../.env  # Edit with your API keys
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000

### IBKR Connection

1. Download [IBKR TWS](https://www.interactivebrokers.com/en/trading/tws.php) or IB Gateway.
2. Enable API access: Configure > API > Settings > Enable ActiveX and Socket Clients.
3. Use port 7497 for paper trading, 7496 for live.
4. Set `CT_IBKR_PORT=7497` in your `.env`.

## Supported LLM Providers

| Provider | Config | Notes |
|----------|--------|-------|
| Google Gemini | `provider=gemini`, `model=gemini-2.0-flash` | Default, cheapest |
| OpenAI | `provider=openai`, `model=gpt-4o` | Higher quality reasoning |
| Local (Ollama/LM Studio/vLLM) | `provider=local`, set base_url | Zero API cost |

All configurable via the dashboard at runtime - no restart needed.

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

## Project Structure

```
claw-trader/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, lifespan, wiring
│   │   ├── api/routes.py        # REST + WebSocket endpoints
│   │   ├── core/
│   │   │   ├── config.py        # Settings from env
│   │   │   ├── database.py      # SQLite schema + helpers
│   │   │   └── events.py        # Async event bus
│   │   ├── engines/
│   │   │   ├── signal_engine.py # Technical indicator detection
│   │   │   ├── llm_brain.py     # LLM trade decision maker
│   │   │   ├── risk_engine.py   # Pre-trade risk checks
│   │   │   └── execution_engine.py # Broker routing
│   │   ├── brokers/
│   │   │   ├── ibkr.py          # Interactive Brokers adapter
│   │   │   └── polymarket.py    # Polymarket adapter
│   │   └── strategies/          # Strategy definitions (future)
│   └── tests/
├── frontend/
│   └── src/
│       ├── app/page.tsx         # Main dashboard
│       └── lib/api.ts           # API client + WebSocket
└── .env.example
```

## Status

**Implemented (end-to-end path):**
- Signal engine with RSI, MACD, Bollinger Bands, volume spike detection
- LLM brain with Gemini and OpenAI providers (+ local via OpenAI-compatible API)
- Risk engine with VaR, drawdown, position limits, kill switch
- Execution engine with broker adapter pattern
- IBKR broker adapter (full implementation)
- Polymarket adapter (market data implemented, execution stubbed)
- Dashboard: LLM config, API usage, decisions, orders, positions, risk, live events
- Real-time WebSocket event stream
- SQLite persistence for all decisions, orders, and API usage

**Next steps:**
- Polymarket on-chain trade execution (CTF split + CLOB)
- Strategy framework (pluggable strategy modules)
- Backtesting engine
- Multi-timeframe signal analysis
- Options chain data integration
- News/sentiment feed (LLM-analyzed)

## License

MIT
