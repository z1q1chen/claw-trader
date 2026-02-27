from __future__ import annotations

import asyncio
import json
from typing import Any

import aiosqlite
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import DB_PATH, save_risk_config
from app.core.events import Event, event_bus
from app.core.logging import logger

router = APIRouter()


# --- Pydantic models ---

class LLMConfigRequest(BaseModel):
    provider: str  # "gemini", "openai", "local", "anthropic"
    model_name: str
    api_key: str | None = None
    base_url: str | None = None


class RiskConfigRequest(BaseModel):
    max_position_usd: float | None = None
    max_daily_loss_usd: float | None = None
    max_portfolio_exposure_usd: float | None = None
    max_single_trade_usd: float | None = None
    max_drawdown_pct: float | None = None
    max_position_concentration_pct: float | None = None


class KillSwitchRequest(BaseModel):
    active: bool


class ManualTradeRequest(BaseModel):
    symbol: str
    side: str  # "buy" or "sell"
    quantity: float
    price: float = 0.0  # estimated current price
    broker: str | None = None


class BrokerConnectRequest(BaseModel):
    broker: str  # "ibkr" or "polymarket"


# --- LLM Config ---

def _mask_key(key: str) -> str:
    """Mask API key, showing only last 4 characters."""
    if not key or len(key) <= 4:
        return key
    return "•" * (len(key) - 4) + key[-4:]


@router.get("/api/llm/config")
async def get_llm_config():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM llm_config WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            config = dict(row)
            config["api_key"] = _mask_key(config.get("api_key", ""))
            return config
        return {"provider": "gemini", "model_name": "gemini-2.0-flash", "api_key": "", "base_url": "", "is_active": True}


@router.post("/api/llm/config")
async def update_llm_config(req: LLMConfigRequest):
    async with aiosqlite.connect(DB_PATH) as db:
        # If no api_key provided, keep the existing one
        api_key = req.api_key
        if not api_key:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT api_key FROM llm_config WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            api_key = row["api_key"] if row else ""

        await db.execute("UPDATE llm_config SET is_active = 0")
        await db.execute(
            """INSERT INTO llm_config (provider, model_name, api_key, base_url, is_active)
               VALUES (?, ?, ?, ?, 1)""",
            (req.provider, req.model_name, api_key, req.base_url or ""),
        )
        await db.commit()

    data = req.model_dump()
    data["api_key"] = api_key  # Use the resolved key for brain reconfiguration
    await event_bus.publish(Event(type="llm_config_changed", data=data))
    return {"status": "ok"}


# --- API Usage ---

@router.get("/api/usage")
async def get_api_usage(limit: int = 100, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM api_usage ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


@router.get("/api/usage/summary")
async def get_api_usage_summary():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT
                provider,
                model,
                COUNT(*) as request_count,
                SUM(total_tokens) as total_tokens,
                SUM(cost_usd) as total_cost,
                AVG(latency_ms) as avg_latency_ms,
                SUM(CASE WHEN created_at >= datetime('now', '-1 day') THEN 1 ELSE 0 END) as requests_24h,
                SUM(CASE WHEN created_at >= datetime('now', '-1 day') THEN cost_usd ELSE 0 END) as cost_24h
            FROM api_usage
            GROUP BY provider, model
        """)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# --- Trade Decisions ---

@router.get("/api/decisions")
async def get_trade_decisions(limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM trade_decisions ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# --- Orders ---

@router.get("/api/orders")
async def get_orders(limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# --- Positions ---

@router.get("/api/positions")
async def get_positions():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM positions ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


@router.get("/api/positions/all")
async def get_all_positions():
    """Get positions from all connected brokers."""
    from app.main import execution_engine
    try:
        all_positions = await execution_engine.get_all_positions()
        return all_positions
    except Exception as e:
        logger.error(f"All positions fetch error: {e}")
        return {}


# --- Balance ---

@router.get("/api/balance/{broker}")
async def get_balance(broker: str):
    from app.main import execution_engine
    try:
        balance = await execution_engine.get_balance(broker)
        return {"broker": broker, "balance": balance}
    except Exception as e:
        logger.error(f"Balance fetch error: {e}")
        return {"broker": broker, "balance": {}, "error": str(e)}


# --- Risk ---

@router.get("/api/risk")
async def get_risk_snapshot():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM risk_snapshots ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else {"total_exposure_usd": 0, "kill_switch_active": False}


@router.get("/api/risk/history")
async def get_risk_history(limit: int = 100, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM risk_snapshots ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


@router.get("/api/risk/config")
async def get_risk_config():
    return {
        "max_position_usd": settings.max_position_usd,
        "max_daily_loss_usd": settings.max_daily_loss_usd,
        "max_portfolio_exposure_usd": settings.max_portfolio_exposure_usd,
        "max_single_trade_usd": settings.max_single_trade_usd,
        "max_drawdown_pct": settings.max_drawdown_pct,
        "max_position_concentration_pct": settings.max_position_concentration_pct,
    }


@router.post("/api/risk/config")
async def update_risk_config(req: RiskConfigRequest):
    errors = []
    if req.max_position_usd is not None and req.max_position_usd <= 0:
        errors.append("max_position_usd must be positive")
    if req.max_daily_loss_usd is not None and req.max_daily_loss_usd <= 0:
        errors.append("max_daily_loss_usd must be positive")
    if req.max_portfolio_exposure_usd is not None and req.max_portfolio_exposure_usd <= 0:
        errors.append("max_portfolio_exposure_usd must be positive")
    if req.max_single_trade_usd is not None and req.max_single_trade_usd <= 0:
        errors.append("max_single_trade_usd must be positive")
    if req.max_drawdown_pct is not None and (req.max_drawdown_pct <= 0 or req.max_drawdown_pct > 100):
        errors.append("max_drawdown_pct must be between 0 and 100")
    if req.max_position_concentration_pct is not None and (req.max_position_concentration_pct <= 0 or req.max_position_concentration_pct > 100):
        errors.append("max_position_concentration_pct must be between 0 and 100")
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    if req.max_position_usd is not None:
        settings.max_position_usd = req.max_position_usd
    if req.max_daily_loss_usd is not None:
        settings.max_daily_loss_usd = req.max_daily_loss_usd
    if req.max_portfolio_exposure_usd is not None:
        settings.max_portfolio_exposure_usd = req.max_portfolio_exposure_usd
    if req.max_single_trade_usd is not None:
        settings.max_single_trade_usd = req.max_single_trade_usd
    if req.max_drawdown_pct is not None:
        settings.max_drawdown_pct = req.max_drawdown_pct
    if req.max_position_concentration_pct is not None:
        settings.max_position_concentration_pct = req.max_position_concentration_pct

    await save_risk_config(
        settings.max_position_usd,
        settings.max_daily_loss_usd,
        settings.max_portfolio_exposure_usd,
        settings.max_single_trade_usd,
        settings.max_drawdown_pct,
    )
    return {"status": "ok"}


@router.post("/api/risk/killswitch")
async def toggle_kill_switch(req: KillSwitchRequest):
    await event_bus.publish(Event(type="kill_switch_toggle", data={"active": req.active}))
    return {"status": "ok", "active": req.active}


@router.get("/api/risk/live")
async def get_live_risk():
    from app.main import risk_engine
    return risk_engine.get_risk_snapshot()


# --- Signals ---

@router.get("/api/signals")
async def get_recent_signals(limit: int = 100, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# --- WebSocket for real-time updates ---

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    event_bus.register_ws_client(queue)

    try:
        while True:
            msg = await queue.get()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        pass
    finally:
        event_bus.unregister_ws_client(queue)


# --- Broker Management ---

@router.post("/api/broker/connect")
async def connect_broker(req: BrokerConnectRequest):
    from app.main import execution_engine
    from app.core.logging import logger
    if req.broker == "ibkr":
        from app.brokers.ibkr import IBKRAdapter
        adapter = IBKRAdapter()
        try:
            await adapter.connect()
            execution_engine.register_broker("ibkr", adapter, default=True)
            return {"status": "ok", "broker": "ibkr", "message": "IBKR connected"}
        except Exception as e:
            logger.error(f"IBKR connection failed: {e}")
            raise HTTPException(status_code=500, detail=f"IBKR connection failed: {e}")
    elif req.broker == "polymarket":
        from app.brokers.polymarket import PolymarketAdapter
        adapter = PolymarketAdapter()
        execution_engine.register_broker("polymarket", adapter)
        return {"status": "ok", "broker": "polymarket", "message": "Polymarket adapter registered"}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown broker: {req.broker}")


@router.post("/api/broker/disconnect")
async def disconnect_broker(req: BrokerConnectRequest):
    from app.main import execution_engine
    if req.broker in execution_engine._brokers:
        broker = execution_engine._brokers[req.broker]
        if hasattr(broker, 'disconnect'):
            await broker.disconnect()
        del execution_engine._brokers[req.broker]
        if execution_engine._default_broker == req.broker:
            execution_engine._default_broker = next(iter(execution_engine._brokers), None)
        return {"status": "ok", "message": f"{req.broker} disconnected"}
    raise HTTPException(status_code=404, detail=f"Broker {req.broker} not registered")


@router.get("/api/brokers")
async def list_brokers():
    from app.main import execution_engine
    return {
        "brokers": list(execution_engine._brokers.keys()),
        "default": execution_engine._default_broker,
    }


@router.post("/api/trade")
async def manual_trade(req: ManualTradeRequest):
    from app.main import execution_engine
    from app.engines.llm_brain import TradeAction
    from app.core.logging import logger

    if req.quantity <= 0:
        raise HTTPException(status_code=422, detail="Quantity must be positive")
    if req.side not in ("buy", "sell"):
        raise HTTPException(status_code=422, detail="Side must be 'buy' or 'sell'")
    if not req.symbol.strip():
        raise HTTPException(status_code=422, detail="Symbol is required")

    action = TradeAction(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        reasoning="Manual trade via dashboard",
        confidence=1.0,
        strategy="manual",
    )

    # Use the price from request; in production this would come from market data
    result = await execution_engine.execute_trade(action, current_price=req.price, broker_name=req.broker)
    if result is None:
        raise HTTPException(status_code=400, detail="Trade rejected by risk engine or no broker available")
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error or "Trade failed")
    return {
        "status": "ok",
        "broker_order_id": result.broker_order_id,
        "filled_price": result.filled_price,
        "filled_quantity": result.filled_quantity,
    }


@router.post("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: str):
    from app.main import execution_engine
    for broker_name, broker in execution_engine._brokers.items():
        success = await broker.cancel_order(order_id)
        if success:
            return {"status": "ok", "message": f"Order {order_id} cancelled via {broker_name}"}
    raise HTTPException(status_code=404, detail=f"Order {order_id} not found in any broker")


@router.get("/api/orders/broker/{broker}")
async def get_broker_order_history(broker: str, limit: int = 50):
    from app.main import execution_engine
    if broker not in execution_engine._brokers:
        raise HTTPException(status_code=404, detail=f"Broker {broker} not registered")
    try:
        orders = await execution_engine._brokers[broker].get_order_history(limit)
        return orders
    except Exception as e:
        logger.error(f"Order history fetch error: {e}")
        return []


# --- Markets ---

@router.get("/api/markets/trending")
async def get_trending_markets(limit: int = 10):
    from app.main import execution_engine
    broker = execution_engine._brokers.get("polymarket")
    if broker is None:
        raise HTTPException(status_code=400, detail="Polymarket adapter not connected")
    markets = await broker.get_trending_markets(limit)
    return markets


@router.get("/api/markets/search")
async def search_markets(q: str, limit: int = 10):
    from app.main import execution_engine
    broker = execution_engine._brokers.get("polymarket")
    if broker is None:
        raise HTTPException(status_code=400, detail="Polymarket adapter not connected")
    markets = await broker.search_markets(q, limit)
    return markets


# --- Trade Statistics ---

@router.get("/api/stats")
async def get_trade_stats():
    """Aggregate trade statistics."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Total trades
        cursor = await db.execute("SELECT COUNT(*) as count FROM orders WHERE status = 'filled'")
        row = await cursor.fetchone()
        total_trades = row["count"] if row else 0

        # Win/loss from decisions
        cursor = await db.execute("""
            SELECT
                COUNT(*) as total_decisions,
                SUM(CASE WHEN executed = 1 THEN 1 ELSE 0 END) as executed_count,
                SUM(CASE WHEN risk_check_passed = 0 THEN 1 ELSE 0 END) as rejected_count,
                AVG(confidence) as avg_confidence
            FROM trade_decisions
        """)
        row = await cursor.fetchone()
        decision_stats = dict(row) if row else {}

        # Total cost
        cursor = await db.execute("SELECT SUM(cost_usd) as total_cost, COUNT(*) as api_calls FROM api_usage")
        row = await cursor.fetchone()
        cost_stats = dict(row) if row else {}

        # Trades by side
        cursor = await db.execute("""
            SELECT side, COUNT(*) as count, SUM(filled_quantity * filled_price) as total_value
            FROM orders WHERE status = 'filled'
            GROUP BY side
        """)
        rows = await cursor.fetchall()
        by_side = {r["side"]: {"count": r["count"], "total_value": r["total_value"]} for r in rows}

        # Recent P&L from positions
        cursor = await db.execute("""
            SELECT
                SUM(unrealized_pnl) as total_unrealized_pnl,
                SUM(realized_pnl) as total_realized_pnl
            FROM positions
        """)
        row = await cursor.fetchone()
        pnl_stats = dict(row) if row else {}

        return {
            "total_filled_orders": total_trades,
            "total_decisions": decision_stats.get("total_decisions", 0),
            "executed_decisions": decision_stats.get("executed_count", 0),
            "rejected_decisions": decision_stats.get("rejected_count", 0),
            "avg_confidence": decision_stats.get("avg_confidence", 0),
            "total_api_cost_usd": cost_stats.get("total_cost", 0),
            "total_api_calls": cost_stats.get("api_calls", 0),
            "trades_by_side": by_side,
            "total_unrealized_pnl": pnl_stats.get("total_unrealized_pnl", 0),
            "total_realized_pnl": pnl_stats.get("total_realized_pnl", 0),
        }


# --- Maintenance ---

@router.post("/api/maintenance/prune")
async def prune_old_data(days: int = 30):
    from app.core.database import prune_old_records
    if days < 1:
        raise HTTPException(status_code=422, detail="days must be >= 1")
    counts = await prune_old_records(days)
    return {"status": "ok", "pruned": counts}


# --- Configuration ---

class IntervalConfigRequest(BaseModel):
    interval_s: float


class CooldownConfigRequest(BaseModel):
    cooldown_s: float


@router.post("/api/config/llm-interval")
async def update_llm_interval(req: IntervalConfigRequest):
    if req.interval_s < 0.5:
        raise HTTPException(status_code=422, detail="interval_s must be >= 0.5")
    from app.main import llm_brain
    llm_brain._min_call_interval_s = req.interval_s
    settings.llm_min_call_interval_s = req.interval_s
    return {"status": "ok", "interval_s": req.interval_s}


@router.post("/api/config/signal-cooldown")
async def update_signal_cooldown(req: CooldownConfigRequest):
    if req.cooldown_s < 1:
        raise HTTPException(status_code=422, detail="cooldown_s must be >= 1")
    from app.main import signal_engine
    signal_engine._signal_cooldown_s = req.cooldown_s
    settings.signal_cooldown_s = req.cooldown_s
    return {"status": "ok", "cooldown_s": req.cooldown_s}


# --- System ---

@router.get("/api/health")
async def health():
    from app.main import signal_engine, llm_brain, risk_engine, execution_engine
    return {
        "status": "ok",
        "app": settings.app_name,
        "engines": {
            "signal_engine": signal_engine._running,
            "llm_configured": llm_brain._provider is not None,
            "kill_switch": risk_engine.kill_switch_active,
            "brokers_registered": list(execution_engine._brokers.keys()),
        },
    }
