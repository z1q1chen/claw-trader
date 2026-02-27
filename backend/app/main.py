from __future__ import annotations

import asyncio
import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import init_db, save_risk_snapshot, log_signal, upsert_position, load_risk_config, load_llm_config, load_signal_config, load_position_sizing_config, get_stale_orders, update_order_status, get_expired_orders, get_recent_trade_stats
from app.core.events import Event, event_bus
from app.core.logging import setup_logging, logger
from app.core.middleware import RateLimitMiddleware, AuthMiddleware
from app.engines.signal_engine import SignalEngine
from app.engines.llm_brain import LLMBrain
from app.engines.risk_engine import RiskEngine
from app.engines.execution_engine import ExecutionEngine
from app.api.routes import router
from app.feeds.dummy import DummyPriceFeed

# Global engine instances
signal_engine = SignalEngine()
llm_brain = LLMBrain()
risk_engine = RiskEngine()
execution_engine = ExecutionEngine(risk_engine)


async def handle_signal(event: Event) -> None:
    """Pipeline: Signal -> LLM Decision -> Risk Check -> Execute."""
    price = event.data.get("price")
    if price is None or price <= 0:
        logger.warning(f"Skipping signal for {event.data.get('symbol', 'unknown')}: price is missing or invalid ({price})")
        return

    try:
        action = await llm_brain.decide(event)
        if action is None:
            return

        await execution_engine.execute_trade(action, price)
    except Exception as e:
        logger.error(f"Error in signal handling for {event.data.get('symbol', 'unknown')}: {e}")
        return


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


async def handle_webhook_dispatch(event: Event) -> None:
    """Forward events to registered webhooks."""
    from app.core.webhooks import webhook_manager
    await webhook_manager.dispatch(event.type, event.data)


async def supervised(name: str, coro_func, restart_delay: float = 5.0):
    """Supervise a task, restarting it on exception."""
    while True:
        try:
            await coro_func()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Task '{name}' crashed: {e}, restarting in {restart_delay}s")
            await asyncio.sleep(restart_delay)


async def periodic_daily_reset() -> None:
    """Reset risk metrics at midnight UTC each day."""
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        # Calculate seconds until next midnight UTC
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
        seconds_until_midnight = (tomorrow - now).total_seconds()
        await asyncio.sleep(seconds_until_midnight)
        risk_engine.reset_daily()
        logger.info("Daily risk metrics reset at midnight UTC")


async def periodic_portfolio_sync() -> None:
    """Periodically sync broker positions and persist risk snapshots."""
    while True:
        try:
            async with execution_engine._portfolio_lock:
                for broker_name, broker in execution_engine._brokers.items():
                    positions = await broker.get_positions()
                    if not isinstance(positions, dict):
                        logger.warning(f"Broker {broker_name} returned invalid positions: {type(positions)}")
                        continue
                    exposure_map: dict[str, float] = {}
                    for symbol, pos_data in positions.items():
                        exposure = abs(pos_data.get("quantity", 0) * pos_data.get("avg_cost", 0))
                        exposure_map[symbol] = exposure
                        qty = pos_data.get("quantity", 0)
                        if abs(qty) < 0.0001:
                            current_price = pos_data.get("avg_cost", 0)
                        else:
                            market_value = pos_data.get("market_value", 0)
                            if market_value == 0:
                                current_price = pos_data.get("avg_cost", 0)
                            else:
                                current_price = abs(market_value / qty)
                        await upsert_position(
                            broker=broker_name,
                            symbol=symbol,
                            quantity=qty,
                            avg_entry_price=pos_data.get("avg_cost", 0),
                            current_price=current_price,
                            unrealized_pnl=pos_data.get("unrealized_pnl", 0),
                            realized_pnl=pos_data.get("realized_pnl", 0),
                        )

                    balance = await broker.get_balance()
                    daily_pnl = balance.get("UnrealizedPnL", 0) + balance.get("RealizedPnL", 0)
                    risk_engine.update_portfolio(exposure_map, daily_pnl)
                    llm_brain.set_portfolio_context(exposure_map, daily_pnl, sum(exposure_map.values()))

                    # Track returns for VaR calculation
                    total_exposure = sum(exposure_map.values())
                    if total_exposure > 0 and daily_pnl != 0:
                        daily_return_pct = daily_pnl / total_exposure * 100
                        risk_engine.add_return(daily_return_pct)

                    # Update Kelly parameters from live trading stats
                    if execution_engine._position_sizer and execution_engine._position_sizer.config.method == "kelly":
                        try:
                            stats = await get_recent_trade_stats(lookback_days=30)
                            if stats and stats.get("total_trades", 0) >= 10:
                                execution_engine._position_sizer.update_stats(
                                    win_rate=stats["win_rate"],
                                    avg_win=stats["avg_win"],
                                    avg_loss=stats["avg_loss"],
                                )
                                logger.info(f"Updated Kelly stats: win_rate={stats['win_rate']:.2%}, avg_win={stats['avg_win']:.2f}, avg_loss={stats['avg_loss']:.2f}")
                        except Exception as e:
                            logger.warning(f"Failed to update Kelly stats: {e}")

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

        await asyncio.sleep(settings.portfolio_sync_interval_s)


async def periodic_order_reconciliation() -> None:
    """Periodically check stale orders against broker state and update if diverged."""
    while True:
        try:
            stale_orders = await get_stale_orders(max_age_seconds=30)
            for order in stale_orders:
                order_id = order["id"]
                broker_name = order["broker"]
                broker_order_id = order["broker_order_id"]

                if broker_name not in execution_engine._brokers or not broker_order_id:
                    continue

                broker = execution_engine._brokers[broker_name]

                try:
                    order_history = await broker.get_order_history(limit=100)
                    broker_order = next(
                        (o for o in order_history if o.get("id") == broker_order_id or o.get("broker_order_id") == broker_order_id),
                        None
                    )

                    if broker_order:
                        broker_status = broker_order.get("status", "unknown")
                        if broker_status != order["status"]:
                            await update_order_status(
                                order_id,
                                status=broker_status,
                                filled_price=broker_order.get("filled_price"),
                                filled_quantity=broker_order.get("filled_quantity"),
                            )
                            logger.info(f"Reconciled order {order_id}: DB status was {order['status']}, broker status is {broker_status}")
                except Exception as e:
                    logger.warning(f"Failed to reconcile order {order_id} with broker {broker_name}: {e}")

        except Exception as e:
            logger.error(f"Order reconciliation error: {e}")

        await asyncio.sleep(60)


async def periodic_expire_orders() -> None:
    """Periodically cancel expired limit orders."""
    while True:
        try:
            expired_orders = await get_expired_orders()
            for order in expired_orders:
                order_id = order["id"]
                broker_name = order["broker"]
                broker_order_id = order["broker_order_id"]

                if broker_name not in execution_engine._brokers or not broker_order_id:
                    await update_order_status(order_id, "expired")
                    continue

                broker = execution_engine._brokers[broker_name]
                try:
                    success = await broker.cancel_order(broker_order_id)
                    if success:
                        await update_order_status(order_id, "expired")
                        logger.info(f"Cancelled expired order {order_id} (broker order {broker_order_id})")
                    else:
                        await update_order_status(order_id, "expired")
                        logger.debug(f"Order {order_id} already filled or not found, marking as expired")
                except Exception as e:
                    logger.warning(f"Failed to cancel expired order {order_id}: {e}")
                    await update_order_status(order_id, "expired")

        except Exception as e:
            logger.error(f"Order expiry check error: {e}")

        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()

    # Validate CORS configuration
    if "*" in settings.cors_origins and settings.auth_enabled:
        logger.error("DANGEROUS: CORS wildcard '*' is enabled with auth_enabled=True. This allows all origins with credentials. Replacing with localhost origins.")
        settings.cors_origins = ["http://localhost", "http://localhost:3000", "http://localhost:5173"]

    # Validate minimum configuration
    has_llm = bool(settings.gemini_api_key or settings.openai_api_key or settings.anthropic_api_key)
    if not has_llm:
        logger.warning("No LLM API key configured. Set CT_GEMINI_API_KEY, CT_OPENAI_API_KEY, or CT_ANTHROPIC_API_KEY in .env")

    has_broker = bool(settings.polymarket_api_key)
    if not has_broker:
        logger.warning("No broker API key configured. Trading will not be possible until a broker is connected.")

    # Load persisted risk configuration
    saved_risk = await load_risk_config()
    if saved_risk:
        settings.max_position_usd = saved_risk["max_position_usd"]
        settings.max_daily_loss_usd = saved_risk["max_daily_loss_usd"]
        settings.max_portfolio_exposure_usd = saved_risk["max_portfolio_exposure_usd"]
        settings.max_single_trade_usd = saved_risk["max_single_trade_usd"]
        settings.max_drawdown_pct = saved_risk["max_drawdown_pct"]
        if "max_position_concentration_pct" in saved_risk:
            settings.max_position_concentration_pct = saved_risk["max_position_concentration_pct"]
        logger.info("Loaded persisted risk configuration")

    # Load LLM config from database (overrides env defaults)
    saved_llm = await load_llm_config()
    if saved_llm and saved_llm.get("api_key"):
        llm_brain.configure(
            provider=saved_llm["provider"],
            model=saved_llm["model_name"],
            api_key=saved_llm["api_key"],
            base_url=saved_llm.get("base_url") or None,
        )
        logger.info(f"Loaded LLM config from DB: {saved_llm['provider']}/{saved_llm['model_name']}")
    elif settings.gemini_api_key:
        llm_brain.configure("gemini", "gemini-2.0-flash", settings.gemini_api_key)
    elif settings.openai_api_key:
        llm_brain.configure("openai", "gpt-4o", settings.openai_api_key)
    elif settings.anthropic_api_key:
        llm_brain.configure("anthropic", "claude-3-5-sonnet-20241022", settings.anthropic_api_key)

    # Load signal config from database (overrides env defaults)
    saved_signal_cfg = await load_signal_config()
    if saved_signal_cfg:
        signal_engine.signal_config.rsi_period = saved_signal_cfg.get("rsi_period", signal_engine.signal_config.rsi_period)
        signal_engine.signal_config.rsi_oversold = saved_signal_cfg.get("rsi_oversold", signal_engine.signal_config.rsi_oversold)
        signal_engine.signal_config.rsi_overbought = saved_signal_cfg.get("rsi_overbought", signal_engine.signal_config.rsi_overbought)
        signal_engine.signal_config.macd_fast = saved_signal_cfg.get("macd_fast", signal_engine.signal_config.macd_fast)
        signal_engine.signal_config.macd_slow = saved_signal_cfg.get("macd_slow", signal_engine.signal_config.macd_slow)
        signal_engine.signal_config.macd_signal = saved_signal_cfg.get("macd_signal", signal_engine.signal_config.macd_signal)
        signal_engine.signal_config.volume_spike_ratio = saved_signal_cfg.get("volume_spike_ratio", signal_engine.signal_config.volume_spike_ratio)
        signal_engine.signal_config.bb_period = saved_signal_cfg.get("bb_period", signal_engine.signal_config.bb_period)
        signal_engine.signal_config.bb_std_dev = saved_signal_cfg.get("bb_std_dev", signal_engine.signal_config.bb_std_dev)
        logger.info("Loaded signal config from DB")

    # Load position sizing config from database (overrides env defaults)
    saved_sizing_cfg = await load_position_sizing_config()
    if saved_sizing_cfg:
        execution_engine._position_sizer.config.method = saved_sizing_cfg.get("method", execution_engine._position_sizer.config.method)
        execution_engine._position_sizer.config.fixed_quantity = saved_sizing_cfg.get("fixed_quantity", execution_engine._position_sizer.config.fixed_quantity)
        execution_engine._position_sizer.config.portfolio_fraction = saved_sizing_cfg.get("portfolio_fraction", execution_engine._position_sizer.config.portfolio_fraction)
        execution_engine._position_sizer.config.kelly_win_rate = saved_sizing_cfg.get("kelly_win_rate", execution_engine._position_sizer.config.kelly_win_rate)
        execution_engine._position_sizer.config.kelly_avg_win = saved_sizing_cfg.get("kelly_avg_win", execution_engine._position_sizer.config.kelly_avg_win)
        execution_engine._position_sizer.config.kelly_avg_loss = saved_sizing_cfg.get("kelly_avg_loss", execution_engine._position_sizer.config.kelly_avg_loss)
        execution_engine._position_sizer.config.max_position_pct = saved_sizing_cfg.get("max_position_pct", execution_engine._position_sizer.config.max_position_pct)
        logger.info("Loaded position sizing config from DB")

    # Wire up event handlers
    event_bus.subscribe("signal", handle_signal)
    event_bus.subscribe("llm_config_changed", handle_llm_config_changed)
    event_bus.subscribe("kill_switch_toggle", handle_kill_switch)
    event_bus.subscribe("signal", handle_signal_log)
    event_bus.subscribe("order_executed", handle_webhook_dispatch)
    event_bus.subscribe("order_failed", handle_webhook_dispatch)
    event_bus.subscribe("trade_rejected", handle_webhook_dispatch)
    event_bus.subscribe("order_cancelled", handle_webhook_dispatch)

    # Register dry-run broker if enabled
    if settings.dry_run_mode:
        from app.brokers.dryrun import DryRunBrokerAdapter
        dryrun = DryRunBrokerAdapter()
        execution_engine.register_broker("dryrun", dryrun, default=True)
        logger.info("DRY RUN MODE: Using simulated broker (no real trades)")

    # Auto-connect Polymarket if API key is configured
    if settings.polymarket_api_key:
        from app.brokers.polymarket import PolymarketAdapter
        poly_adapter = PolymarketAdapter()
        execution_engine.register_broker("polymarket", poly_adapter)
        logger.info("Polymarket broker auto-connected (API key found)")

    # Start periodic portfolio sync
    sync_task = asyncio.create_task(supervised("periodic_portfolio_sync", periodic_portfolio_sync))

    # Start daily reset task
    reset_task = asyncio.create_task(supervised("periodic_daily_reset", periodic_daily_reset))

    # Start order reconciliation task
    reconciliation_task = asyncio.create_task(supervised("periodic_order_reconciliation", periodic_order_reconciliation))

    # Start order expiry task
    expiry_task = asyncio.create_task(supervised("periodic_expire_orders", periodic_expire_orders))

    # Start signal engine with appropriate feed
    if settings.polymarket_condition_ids:
        from app.feeds.polymarket_feed import PolymarketPriceFeed
        feed = PolymarketPriceFeed(settings.polymarket_condition_ids)
    else:
        feed = DummyPriceFeed(settings.price_feed_symbols)
    await feed.start()
    signal_task = asyncio.create_task(signal_engine.run(feed))

    logger.info(f"Claw Trader started. Monitoring: {settings.price_feed_symbols}")
    logger.info(f"Signal scan interval: {settings.signal_scan_interval_ms}ms")

    yield

    logger.info("Shutting down Claw Trader...")
    await feed.stop()
    signal_engine.stop()

    for broker_name, broker in execution_engine._brokers.items():
        if hasattr(broker, 'disconnect'):
            try:
                await broker.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting broker {broker_name}: {e}")
        logger.info(f"Broker '{broker_name}' disconnected")

    for task_name, task in [("signal", signal_task), ("sync", sync_task), ("reset", reset_task), ("reconciliation", reconciliation_task), ("expiry", expiry_task)]:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        logger.info(f"Task '{task_name}' stopped")

    logger.info("Claw Trader shutdown complete")


app = FastAPI(title="Claw Trader", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_rpm)
app.add_middleware(AuthMiddleware)

app.include_router(router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url.path}: {exc}")
    content = {"detail": "Internal server error"}
    if settings.debug:
        content["error"] = str(exc)
    return JSONResponse(
        status_code=500,
        content=content,
    )
