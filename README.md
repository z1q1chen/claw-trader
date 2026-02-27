# Claw Trader

An autonomous quantitative trading agent powered by LLMs. Monitors markets, detects technical signals, consults an AI brain for trade decisions, and executes through connected brokers.

## Overview

Claw Trader is a sophisticated trading system that combines:
- **Real-time technical analysis** (RSI, MACD, Bollinger Bands, volume spikes)
- **LLM-powered decision making** (Gemini, OpenAI, Anthropic, or local models)
- **Multi-layer risk management** (7-point checks, VaR, kill switch, position limits)
- **Multi-broker execution** (Interactive Brokers, Polymarket, DryRun)
- **Full audit trail** (trade journal with lifecycle tracking)
- **Real-time dashboard** (WebSocket updates, dark mode, responsive design)

## Features

### Trading Engine
- Signal detection from real-time market data (sub-second latency)
- LLM-powered trade decisions with confidence scoring
- Risk-aware execution with multiple pre-trade checks
- Broker routing with automatic retry logic
- Manual trade execution via dashboard

### Brokers
- **Interactive Brokers (IBKR)** — Stocks, ETFs, options via ib_insync
- **Polymarket** — Prediction markets via CLOB API
- **DryRun** — Paper trading for backtesting

### Signal Detection
- **RSI** — Relative Strength Index (oversold/overbought)
- **MACD** — Moving Average Convergence Divergence
- **Bollinger Bands** — Mean reversion signals
- **Volume Spikes** — Anomaly detection
- Configurable thresholds and periods

### Position Sizing
- **Fixed** — Trade a constant quantity
- **Fixed Fractional** — Risk a percentage of portfolio per trade
- **Kelly Criterion** — Optimal sizing based on win rate and payoff ratios

### Risk Management
- **7-Point Risk Check**:
  1. Max position size limit
  2. Max single trade limit
  3. Daily loss limit (kill switch trigger)
  4. Max portfolio exposure
  5. Max drawdown check
  6. Position concentration limit
  7. Max losing streak check
- **Value at Risk (VaR)** — 95% confidence interval monitoring
- **Kill Switch** — Automatic emergency stop on max daily loss
- **Position Concentration** — Single symbol exposure limits

### Strategy Presets
- **Conservative** — Low risk, fewer trades, wider signal thresholds
- **Balanced** — Moderate risk/reward, standard parameters
- **Aggressive** — High risk, frequent trades, tight thresholds

### Performance Tracking
- Win rate calculation
- Profit factor (wins/losses ratio)
- Sharpe ratio (risk-adjusted returns)
- Realized vs unrealized P&L
- Trade-by-trade P&L matching
- Daily performance metrics

### Notifications
- **Webhooks** with event filtering
- Event types: order_executed, order_failed, trade_rejected, order_cancelled
- Automatic retry logic for failed deliveries
- Test webhook functionality

### Dashboard
- Real-time WebSocket event stream
- Dark mode with persistent preference
- Responsive design (mobile-friendly)
- Real-time risk snapshot
- LLM usage and cost tracking
- Performance summary

### Security
- API key authentication (optional)
- Rate limiting
- CORS configuration
- Environment-based secrets management

### Data Export
- CSV export of trades, signals, decisions
- JSON export for programmatic access
- Trade journal with full lifecycle tracking

### Audit Trail
- Trade journal tracking all events:
  - Signal detection
  - LLM decisions
  - Risk checks
  - Execution attempts
  - Fills and cancellations
- Full decision history with reasoning
- Order lifecycle tracking

## Architecture

```
Signal Engine → LLM Brain → Risk Engine → Execution Engine → Broker
     ↑                                                          |
     └──────────── Price Feeds ←────────────────────────────────┘

                          Dashboard
                            ↑
                         WebSocket
                          Event Bus
```

- **Signal Engine** — Sub-second technical analysis (RSI, MACD, Bollinger Bands, volume)
- **LLM Brain** — AI-powered trade decisions via LLM provider
- **Risk Engine** — Pre-trade risk checks with 7-point verification
- **Execution Engine** — Order routing with retry logic and broker abstraction
- **Event Bus** — Real-time event streaming to WebSocket clients
- **Dashboard** — Next.js frontend with real-time updates

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- (Optional) IBKR TWS/Gateway for stock trading
- (Optional) Polygon RPC + Polymarket API key for prediction markets

### Backend Setup

```bash
cd backend
cp ../.env.example .env
# Edit .env with your API keys
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Dashboard available at http://localhost:3000

API available at http://localhost:8000

### Using Local LLM

Claw Trader supports any OpenAI-compatible local LLM server:

1. Start your local LLM (e.g., `lm-studio --port 1234`)
2. In dashboard, select **Local (OpenAI-compatible)**
3. Set model name (e.g., `mistral-7b-instruct`)
4. Set base URL (e.g., `http://127.0.0.1:1234/v1`)
5. API key can be any non-empty string (e.g., `local`)

### Trading on Polymarket

1. Set `CT_POLYMARKET_API_KEY` and `CT_POLYMARKET_PRIVATE_KEY` in `.env`
2. Set `CT_POLYGON_RPC_URL` to a Polygon RPC endpoint
3. Connect Polymarket broker from dashboard
4. Browse markets and execute trades

## Configuration

All settings via environment variables with `CT_` prefix.

### LLM Configuration
- `CT_GEMINI_API_KEY` — Google Gemini API key
- `CT_OPENAI_API_KEY` — OpenAI API key
- `CT_ANTHROPIC_API_KEY` — Anthropic Claude API key

### Risk Management
- `CT_MAX_SINGLE_TRADE_USD` — Max per trade (default 5000)
- `CT_MAX_DAILY_LOSS_USD` — Daily loss limit (default 10000, triggers kill switch)
- `CT_MAX_PORTFOLIO_EXPOSURE_USD` — Total exposure cap (default 50000)
- `CT_MAX_POSITION_USD` — Max per position (default 20000)
- `CT_MAX_DRAWDOWN_PCT` — Max drawdown before kill switch (default 10)
- `CT_MAX_POSITION_CONCENTRATION_PCT` — Max single symbol exposure (default 20)

### Signal Detection
- `CT_SIGNAL_SCAN_INTERVAL_MS` — Detection frequency (default 500ms)
- `CT_SIGNAL_COOLDOWN_S` — Minimum time between signals per symbol (default 60s)

### Trading
- `CT_DRY_RUN_MODE` — Paper trading mode (default false)
- `CT_LLM_MIN_CALL_INTERVAL_S` — Min seconds between LLM calls (default 2)

### Broker Configuration
- `CT_POLYMARKET_API_KEY` — Polymarket CLOB API key
- `CT_POLYMARKET_PRIVATE_KEY` — Private key for signing (hex)
- `CT_POLYGON_RPC_URL` — Polygon RPC endpoint

### Authentication
- `CT_AUTH_ENABLED` — Enable API key auth (default false)
- `CT_API_SECRET_KEY` — API secret key (if auth enabled)

See `.env.example` for complete list.

## API Reference

### System
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | System health and component status |
| `/api/stats` | GET | Trading statistics |

### LLM Configuration
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/llm/config` | GET | Get current LLM config |
| `/api/llm/config` | POST | Update LLM config |
| `/api/config/llm-interval` | POST | Set LLM call interval |
| `/api/usage` | GET | LLM usage history |
| `/api/usage/summary` | GET | Usage summary by provider/model |

### Risk Management
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/risk` | GET | Current risk snapshot |
| `/api/risk/live` | GET | Live risk metrics |
| `/api/risk/config` | GET | Risk limit configuration |
| `/api/risk/config` | POST | Update risk limits |
| `/api/risk/killswitch` | POST | Toggle kill switch |
| `/api/risk/history` | GET | Risk snapshot history |

### Trading
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/trade` | POST | Manual trade execution |
| `/api/decisions` | GET | LLM trade decisions |
| `/api/orders` | GET | Order history (paginated) |
| `/api/orders/{id}/cancel` | POST | Cancel order |
| `/api/orders/broker/{broker}` | GET | Orders from specific broker |
| `/api/positions` | GET | Current open positions |
| `/api/positions/all` | GET | Positions from all brokers |
| `/api/balance/{broker}` | GET | Account balance |

### Signals
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/signals` | GET | Detected signals |
| `/api/config/signal` | GET | Signal detection config |
| `/api/config/signal` | POST | Update signal config |

### Position Sizing
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config/position-sizing` | GET | Position sizing config |
| `/api/config/position-sizing` | POST | Update position sizing |

### Strategy
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/presets` | GET | Available strategy presets |
| `/api/presets/{name}/apply` | POST | Apply strategy preset |

### Markets (Polymarket)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/markets/trending` | GET | Trending markets |
| `/api/markets/search` | GET | Search markets |

### Brokers
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/brokers` | GET | Connected brokers |
| `/api/broker/connect` | POST | Connect broker |
| `/api/broker/disconnect` | POST | Disconnect broker |

### Webhooks
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/webhooks` | GET | List registered webhooks |
| `/api/webhooks` | POST | Register webhook |
| `/api/webhooks/{id}` | DELETE | Delete webhook |
| `/api/webhooks/{id}/test` | POST | Test webhook |

### Trade Journal
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/journal` | GET | Trade journal entries (paginated) |

### Performance
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/performance/summary` | GET | Performance summary (win rate, P&L, Sharpe) |
| `/api/performance/metrics` | GET | Historical performance metrics |

### Data Export
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/export/trades` | GET | Export trades (CSV/JSON) |
| `/api/export/signals` | GET | Export signals (CSV/JSON) |
| `/api/export/decisions` | GET | Export decisions (CSV/JSON) |

### Configuration
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config/dry-run` | GET | Dry-run mode status |
| `/api/config/signal-cooldown` | POST | Set signal cooldown |

### Authentication
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/generate-key` | POST | Generate new API key |

### Real-time
| Endpoint | Description |
|----------|-------------|
| `/ws` | WebSocket for real-time events |

## Testing

### Run Tests
```bash
cd backend
pip install -r requirements-dev.txt
pytest tests/ -v
```

### Run Specific Test
```bash
pytest tests/test_signal_engine.py -v
```

### Test Coverage
```bash
pytest tests/ --cov=app --cov-report=html
```

## Docker

### Docker Compose
```bash
docker-compose up
```

Services:
- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Database: SQLite (persistent volume)

### Manual Docker Build
```bash
# Backend
docker build -f backend/Dockerfile -t claw-trader-backend .
docker run -p 8000:8000 -e CT_GEMINI_API_KEY=xxx claw-trader-backend

# Frontend
docker build -f frontend/Dockerfile -t claw-trader-frontend .
docker run -p 3000:3000 claw-trader-frontend
```

## Development

### Code Structure
```
backend/
  app/
    main.py           — FastAPI app startup
    api/routes.py     — API endpoints
    core/
      database.py     — SQLite schema and queries
      config.py       — Settings and environment
      events.py       — Event bus and WebSocket
      webhooks.py     — Webhook management
      auth.py         — API authentication
    engines/
      signal_engine.py     — Technical analysis
      llm_brain.py         — LLM decision making
      risk_engine.py       — Risk checks
      execution_engine.py   — Order execution
      position_sizing.py    — Position sizing
    brokers/
      base.py         — Base adapter
      ibkr.py         — Interactive Brokers
      polymarket.py   — Polymarket adapter
      dryrun.py       — Paper trading
    feeds/
      base.py         — Data feed interface
      ibkr_feed.py    — IBKR price feed
      dummy.py        — Test feed
  tests/              — Unit and integration tests

frontend/
  src/
    app/page.tsx      — Dashboard component
    lib/
      api.ts          — API client
      types.ts        — TypeScript interfaces
  public/             — Static assets
```

### Adding a New Broker
1. Create `app/brokers/yourbroker.py` extending `BrokerAdapter`
2. Implement `connect()`, `place_order()`, `cancel_order()`, `get_positions()`
3. Register in dashboard broker connection UI
4. Add to `app/main.py` initialization

### Adding a New Signal
1. Extend `app/engines/signal_engine.py`
2. Implement indicator calculation
3. Add configuration in `SignalConfig` (types.ts)
4. Expose in `/api/config/signal` endpoint

## License

MIT

## Support

For issues or questions:
- Check logs in backend console
- Review database.db for data consistency
- Test webhook deliveries with `/api/webhooks/{id}/test`
- Check LLM provider API status in `/api/health`

## Roadmap

- [ ] Multi-leg options strategies
- [ ] Portfolio rebalancing
- [ ] Machine learning signal generation
- [ ] Advanced order types (OCO, brackets)
- [ ] Market microstructure analysis
- [ ] Multi-account management
