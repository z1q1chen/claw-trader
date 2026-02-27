from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import init_db, save_risk_snapshot, log_signal, upsert_position
from app.core.events import Event, event_bus
from app.core.logging import setup_logging, logger
from app.engines.signal_engine import SignalEngine
from app.engines.llm_brain import LLMBrain
from app.engines.risk_engine import RiskEngine
from app.engines.execution_engine import ExecutionEngine
from app.api.routes import router

# Global engine instances
signal_engine = SignalEngine()
llm_brain = LLMBrain()
risk_engine = RiskEngine()
execution_engine = ExecutionEngine(risk_engine)


class DummyPriceFeed:
    """Placeholder price feed that returns synthetic data for testing."""

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols
        self._prices: dict[str, float] = {s: 100.0 for s in symbols}
        self._tick = 0

    async def get_latest_prices(self) -> dict[str, tuple[float, float]]:
        import random
        self._tick += 1
        result = {}
        for symbol in self._symbols:
            change = random.gauss(0, 0.5)
            self._prices[symbol] = max(1.0, self._prices[symbol] + change)
            volume = random.uniform(10000, 100000)
            result[symbol] = (self._prices[symbol], volume)
        return result


async def handle_signal(event: Event) -> None:
    """Pipeline: Signal -> LLM Decision -> Risk Check -> Execute."""
    action = await llm_brain.decide(event)
    if action is None:
        return

    price = event.data.get("price", 0)
    await execution_engine.execute_trade(action, price)


async def handle_llm_config_changed(event: Event) -> None:
    """Reconfigure LLM brain when user updates settings via dashboard."""
    data = event.data
    llm_brain.configure(
        provider=data["provider"],
        model=data["model_name"],
        api_key=data["api_key"],
        base_url=data.get("base_url"),
    )
    logger.info(f"LLM Brain reconfigured: {data['provider']}/{data['model_name']}")


async def handle_kill_switch(event: Event) -> None:
    if event.data.get("active"):
        risk_engine.activate_kill_switch("Manual activation via dashboard")
    else:
        risk_engine.deactivate_kill_switch()


async def handle_signal_log(event: Event) -> None:
    """Log detected signals to database."""
    data = event.data
    await log_signal(
        symbol=data["symbol"],
        signal_type=data["signal_type"],
        value=data["value"],
        metadata=data.get("metadata", {}),
    )


async def periodic_portfolio_sync() -> None:
    """Periodically sync broker positions and persist risk snapshots."""
    while True:
        try:
            for broker_name, broker in execution_engine._brokers.items():
                positions = await broker.get_positions()
                exposure_map: dict[str, float] = {}
                for symbol, pos_data in positions.items():
                    exposure = abs(pos_data.get("quantity", 0) * pos_data.get("avg_cost", 0))
                    exposure_map[symbol] = exposure
                    await upsert_position(
                        broker=broker_name,
                        symbol=symbol,
                        quantity=pos_data.get("quantity", 0),
                        avg_entry_price=pos_data.get("avg_cost", 0),
                        current_price=pos_data.get("market_value", 0) / max(pos_data.get("quantity", 1), 0.01),
                        unrealized_pnl=pos_data.get("unrealized_pnl", 0),
                        realized_pnl=pos_data.get("realized_pnl", 0),
                    )

                balance = await broker.get_balance()
                daily_pnl = balance.get("UnrealizedPnL", 0) + balance.get("RealizedPnL", 0)
                risk_engine.update_portfolio(exposure_map, daily_pnl)

            snapshot = risk_engine.get_risk_snapshot()
            await save_risk_snapshot(
                total_exposure_usd=snapshot["total_exposure_usd"],
                daily_pnl_usd=snapshot["daily_pnl_usd"],
                max_drawdown_pct=snapshot["max_drawdown_pct"],
                var_95_usd=snapshot["var_95_usd"],
                positions_count=snapshot["positions_count"],
                kill_switch_active=snapshot["kill_switch_active"],
                details=snapshot.get("positions", {}),
            )
        except Exception as e:
            logger.error(f"Portfolio sync error: {e}")

        await asyncio.sleep(30)  # Sync every 30 seconds


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()

    # Configure LLM brain with defaults
    if settings.gemini_api_key:
        llm_brain.configure("gemini", "gemini-2.0-flash", settings.gemini_api_key)
    elif settings.openai_api_key:
        llm_brain.configure("openai", "gpt-4o", settings.openai_api_key)

    # Wire up event handlers
    event_bus.subscribe("signal", handle_signal)
    event_bus.subscribe("llm_config_changed", handle_llm_config_changed)
    event_bus.subscribe("kill_switch_toggle", handle_kill_switch)
    event_bus.subscribe("signal", handle_signal_log)

    # Start periodic portfolio sync
    sync_task = asyncio.create_task(periodic_portfolio_sync())

    # Start signal engine with dummy feed (replace with IBKR feed in production)
    feed = DummyPriceFeed(settings.price_feed_symbols)
    signal_task = asyncio.create_task(signal_engine.run(feed))

    logger.info(f"Claw Trader started. Monitoring: {settings.price_feed_symbols}")
    logger.info(f"Signal scan interval: {settings.signal_scan_interval_ms}ms")

    yield

    signal_engine.stop()
    signal_task.cancel()
    try:
        await signal_task
    except asyncio.CancelledError:
        pass
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Claw Trader", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )
