from __future__ import annotations

import asyncio
import csv
import hmac
import io
import ipaddress
import json
import time
import urllib.parse
from typing import Any

import aiosqlite
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import (
    DB_PATH,
    save_risk_config,
    save_signal_config,
    save_position_sizing_config,
    get_latest_timestamps,
    count_orders,
    count_trade_decisions,
    count_signals,
    count_api_usage,
    count_risk_snapshots,
    save_performance_metrics,
    get_performance_history,
    get_trade_pnl_data,
    get_trade_journal,
    count_journal_entries,
)
from app.core.events import Event, event_bus
from app.core.logging import logger

router = APIRouter()
_app_start_time = time.monotonic()


def paginated_response(data: list, total: int, limit: int, offset: int) -> dict:
    """Build a pagination envelope for API responses."""
    return {
        "data": data,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


def _to_csv(rows: list[dict], columns: list[str]) -> str:
    """Convert list of dicts to CSV string."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


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
    side: str
    quantity: float
    price: float | None = None
    broker: str | None = None
    order_type: str = "MARKET"
    limit_price: float | None = None

    def __init__(self, **data):
        super().__init__(**data)
        # Normalize and validate symbol
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol is required and cannot be empty")
        if len(self.symbol) > 100:
            raise ValueError("symbol must be at most 100 characters")

        # Normalize and validate side
        self.side = self.side.upper()
        if self.side not in ("BUY", "SELL"):
            raise ValueError('side must be "BUY" or "SELL"')

        # Validate quantity
        if self.quantity <= 0:
            raise ValueError("quantity must be positive (greater than 0)")

        # Validate price if provided
        if self.price is not None and self.price < 0:
            raise ValueError("price must be non-negative if provided")

        # Validate order_type
        if self.order_type not in ("MARKET", "LIMIT"):
            raise ValueError('order_type must be "MARKET" or "LIMIT"')

        # Validate limit_price
        if self.order_type == "LIMIT" and self.limit_price is None:
            raise ValueError("limit_price is required when order_type is LIMIT")
        if self.limit_price is not None and self.limit_price < 0:
            raise ValueError("limit_price must be non-negative if provided")


class BrokerConnectRequest(BaseModel):
    broker: str  # "ibkr" or "polymarket"


# --- LLM Config ---

def _mask_key(key: str) -> str:
    """Mask API key, showing only last 4 characters."""
    if not key or len(key) <= 4:
        return key
    return "•" * (len(key) - 4) + key[-4:]


def _validate_webhook_url(url: str) -> list[str]:
    """Validate webhook URL. Returns list of error strings if invalid."""
    errors = []

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        errors.append("Invalid URL format")
        return errors

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        errors.append("URL must start with http:// or https://")

    hostname = parsed.hostname
    if not hostname:
        errors.append("URL must have a valid hostname")
        return errors

    try:
        ip_obj = ipaddress.ip_address(hostname)
        if isinstance(ip_obj, ipaddress.IPv4Address):
            if (ip_obj.is_loopback or
                ip_obj.is_private or
                ip_obj.is_link_local):
                errors.append(f"Webhook URL cannot use private/loopback IP: {hostname}")
        elif isinstance(ip_obj, ipaddress.IPv6Address):
            if ip_obj.is_loopback:
                errors.append(f"Webhook URL cannot use loopback IP: {hostname}")
    except ValueError:
        pass

    return errors


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
    from app.core.database import _write_lock

    async with _write_lock:
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

            try:
                await db.execute("BEGIN")
                await db.execute("UPDATE llm_config SET is_active = 0")
                await db.execute(
                    """INSERT INTO llm_config (provider, model_name, api_key, base_url, is_active)
                       VALUES (?, ?, ?, ?, 1)""",
                    (req.provider, req.model_name, api_key, req.base_url or ""),
                )
                await db.execute("COMMIT")
            except Exception as e:
                await db.execute("ROLLBACK")
                logger.error(f"Failed to save LLM config: {e}")
                raise

    data = req.model_dump()
    data["api_key"] = api_key  # Use the resolved key for brain reconfiguration
    await event_bus.publish(Event(type="llm_config_changed", data=data))
    return {"status": "ok"}


# --- API Usage ---

@router.get("/api/usage")
async def get_api_usage(limit: int = 100, offset: int = 0):
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM api_usage ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
    total = await count_api_usage()
    data = [dict(r) for r in rows]
    return paginated_response(data, total, limit, offset)


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
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM trade_decisions ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
    total = await count_trade_decisions()
    data = [dict(r) for r in rows]
    return paginated_response(data, total, limit, offset)


# --- Orders ---

@router.get("/api/orders")
async def get_orders(limit: int = 50, offset: int = 0):
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
    total = await count_orders()
    data = [dict(r) for r in rows]
    return paginated_response(data, total, limit, offset)


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
        all_raw = await execution_engine.get_all_positions()
        result = []
        for broker_name, positions in all_raw.items():
            for symbol, pos_data in positions.items():
                qty = pos_data.get("quantity", 0)
                avg_cost = pos_data.get("avg_cost", 0)
                result.append({
                    "broker": broker_name,
                    "symbol": symbol,
                    "quantity": qty,
                    "avg_entry_price": avg_cost,
                    "current_price": pos_data.get("market_value", 0) / max(qty, 0.01) if qty > 0 else avg_cost,
                    "unrealized_pnl": pos_data.get("unrealized_pnl", 0),
                    "realized_pnl": pos_data.get("realized_pnl", 0),
                })
        return result
    except Exception as e:
        logger.error(f"Failed to get all positions: {e}")
        return []


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
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM risk_snapshots ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
    total = await count_risk_snapshots()
    data = [dict(r) for r in rows]
    return paginated_response(data, total, limit, offset)


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
        settings.max_position_concentration_pct,
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
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
    total = await count_signals()
    data = [dict(r) for r in rows]
    return paginated_response(data, total, limit, offset)


@router.get("/api/journal")
async def get_journal(limit: int = 50, offset: int = 0, symbol: str | None = None):
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    entries = await get_trade_journal(limit, offset, symbol)
    total = await count_journal_entries(symbol)
    return paginated_response(entries, total, limit, offset)


# --- WebSocket for real-time updates ---

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # Check token from query parameter
    token = ws.query_params.get("token", "")
    if settings.auth_enabled and settings.api_secret_key:
        from app.core.auth import hash_api_key
        if not token or not hmac.compare_digest(
            hash_api_key(token),
            hash_api_key(settings.api_secret_key)
        ):
            await ws.close(code=4001, reason="Unauthorized")
            return

    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    event_bus.register_ws_client(queue)
    # Track if this WebSocket is authenticated for processing commands
    ws_authenticated = True

    try:
        async def send_loop():
            while True:
                msg = await queue.get()
                try:
                    await ws.send_text(msg)
                except Exception:
                    break

        async def receive_loop():
            while True:
                data = await ws.receive_json()
                # Only process commands from authenticated connections
                if not ws_authenticated:
                    continue
                command = data.get("command")
                if command == "kill_switch":
                    await event_bus.publish(Event(type="kill_switch_toggle", data={"active": data.get("active", True)}))
                elif command == "refresh":
                    pass

        await asyncio.gather(send_loop(), receive_loop())
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

    try:
        action = TradeAction(
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            reasoning="Manual trade via dashboard",
            confidence=1.0,
            strategy="manual",
        )

        # Use the price from request; in production this would come from market data
        result = await execution_engine.execute_trade(action, current_price=req.price or 0.0, broker_name=req.broker)
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
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class CancelOrderRequest(BaseModel):
    broker: str


@router.post("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: str, req: CancelOrderRequest):
    from app.main import execution_engine
    if req.broker not in execution_engine._brokers:
        raise HTTPException(status_code=404, detail=f"Broker {req.broker} not registered")
    broker = execution_engine._brokers[req.broker]
    success = await broker.cancel_order(order_id)
    if success:
        await event_bus.publish(Event(
            type="order_cancelled",
            data={
                "order_id": order_id,
                "broker": req.broker,
            }
        ))
        return {"success": True, "message": f"Order {order_id} cancelled"}
    raise HTTPException(status_code=400, detail=f"Failed to cancel order {order_id}")


@router.get("/api/orders/broker/{broker}")
async def get_broker_order_history(broker: str, limit: int = 50):
    limit = min(max(limit, 1), 1000)
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
    limit = min(max(limit, 1), 1000)
    from app.main import execution_engine
    broker = execution_engine._brokers.get("polymarket")
    if broker is None:
        raise HTTPException(status_code=400, detail="Polymarket adapter not connected")
    markets = await broker.get_trending_markets(limit)
    return markets


@router.get("/api/markets/search")
async def search_markets(q: str, limit: int = 10):
    limit = min(max(limit, 1), 1000)
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


@router.get("/api/config/dry-run")
async def get_dry_run_status():
    return {"enabled": settings.dry_run_mode}


@router.get("/api/config/signal")
async def get_signal_config():
    from app.main import signal_engine
    cfg = signal_engine.signal_config
    return {
        "rsi_period": cfg.rsi_period,
        "rsi_oversold": cfg.rsi_oversold,
        "rsi_overbought": cfg.rsi_overbought,
        "macd_fast": cfg.macd_fast,
        "macd_slow": cfg.macd_slow,
        "macd_signal": cfg.macd_signal,
        "volume_spike_ratio": cfg.volume_spike_ratio,
        "bb_period": cfg.bb_period,
        "bb_std_dev": cfg.bb_std_dev,
    }


@router.post("/api/config/signal")
async def update_signal_config(req: dict):
    from app.main import signal_engine
    from fastapi.responses import JSONResponse
    cfg = signal_engine.signal_config
    errors = []
    for key in ("rsi_period", "rsi_oversold", "rsi_overbought", "macd_fast", "macd_slow", "macd_signal", "volume_spike_ratio", "bb_period", "bb_std_dev"):
        if key in req:
            try:
                expected_type = type(getattr(cfg, key))
                value = expected_type(req[key])
                if value <= 0:
                    errors.append(f"{key} must be positive")
                else:
                    setattr(cfg, key, value)
            except (ValueError, TypeError) as e:
                errors.append(f"Invalid value for {key}: {e}")

    # Cross-field validation (only if no type errors already)
    try:
        if "rsi_oversold" in req and "rsi_overbought" in req:
            rsi_oversold = float(req["rsi_oversold"])
            rsi_overbought = float(req["rsi_overbought"])
            if rsi_oversold >= rsi_overbought:
                errors.append("rsi_oversold must be less than rsi_overbought")
        elif "rsi_oversold" in req:
            rsi_oversold = float(req["rsi_oversold"])
            if rsi_oversold >= cfg.rsi_overbought:
                errors.append("rsi_oversold must be less than rsi_overbought")
        elif "rsi_overbought" in req:
            rsi_overbought = float(req["rsi_overbought"])
            if cfg.rsi_oversold >= rsi_overbought:
                errors.append("rsi_oversold must be less than rsi_overbought")

        if "macd_fast" in req and "macd_slow" in req:
            macd_fast = int(req["macd_fast"])
            macd_slow = int(req["macd_slow"])
            if macd_fast >= macd_slow:
                errors.append("macd_fast must be less than macd_slow")
        elif "macd_fast" in req:
            macd_fast = int(req["macd_fast"])
            if macd_fast >= cfg.macd_slow:
                errors.append("macd_fast must be less than macd_slow")
        elif "macd_slow" in req:
            macd_slow = int(req["macd_slow"])
            if cfg.macd_fast >= macd_slow:
                errors.append("macd_fast must be less than macd_slow")

        if "rsi_period" in req:
            rsi_period = int(req["rsi_period"])
            if rsi_period < 2:
                errors.append("rsi_period must be at least 2")
    except (ValueError, TypeError):
        pass  # Type errors already captured in the loop above

    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})

    config_dict = {
        "rsi_period": cfg.rsi_period,
        "rsi_oversold": cfg.rsi_oversold,
        "rsi_overbought": cfg.rsi_overbought,
        "macd_fast": cfg.macd_fast,
        "macd_slow": cfg.macd_slow,
        "macd_signal": cfg.macd_signal,
        "volume_spike_ratio": cfg.volume_spike_ratio,
        "bb_period": cfg.bb_period,
        "bb_std_dev": cfg.bb_std_dev,
    }
    await save_signal_config(config_dict)
    return {"status": "ok"}


@router.get("/api/config/position-sizing")
async def get_position_sizing_config():
    from app.main import execution_engine
    cfg = execution_engine._position_sizer.config
    return {
        "method": cfg.method,
        "fixed_quantity": cfg.fixed_quantity,
        "portfolio_fraction": cfg.portfolio_fraction,
        "kelly_win_rate": cfg.kelly_win_rate,
        "kelly_avg_win": cfg.kelly_avg_win,
        "kelly_avg_loss": cfg.kelly_avg_loss,
        "max_position_pct": cfg.max_position_pct,
    }


@router.post("/api/config/position-sizing")
async def update_position_sizing_config(req: dict):
    from app.main import execution_engine
    cfg = execution_engine._position_sizer.config
    valid_methods = ("fixed", "fixed_fractional", "kelly")
    errors = []

    if "method" in req and req["method"] in valid_methods:
        cfg.method = req["method"]

    if "max_position_pct" in req:
        try:
            val = float(req["max_position_pct"])
            if val <= 0 or val > 1.0:
                errors.append("max_position_pct must be between 0 (exclusive) and 1.0 (inclusive)")
            else:
                cfg.max_position_pct = val
        except (ValueError, TypeError):
            errors.append("max_position_pct must be a number")

    if "kelly_avg_loss" in req:
        try:
            val = float(req["kelly_avg_loss"])
            if val <= 0:
                errors.append("kelly_avg_loss must be positive")
            else:
                cfg.kelly_avg_loss = val
        except (ValueError, TypeError):
            errors.append("kelly_avg_loss must be a number")

    if "kelly_avg_win" in req:
        try:
            val = float(req["kelly_avg_win"])
            if val <= 0:
                errors.append("kelly_avg_win must be positive")
            else:
                cfg.kelly_avg_win = val
        except (ValueError, TypeError):
            errors.append("kelly_avg_win must be a number")

    if "kelly_win_rate" in req:
        try:
            val = float(req["kelly_win_rate"])
            if val < 0 or val > 1:
                errors.append("kelly_win_rate must be between 0 and 1")
            else:
                cfg.kelly_win_rate = val
        except (ValueError, TypeError):
            errors.append("kelly_win_rate must be a number")

    if "fixed_quantity" in req:
        try:
            val = float(req["fixed_quantity"])
            if val <= 0:
                errors.append("fixed_quantity must be positive")
            else:
                cfg.fixed_quantity = val
        except (ValueError, TypeError):
            errors.append("fixed_quantity must be a number")

    if "portfolio_fraction" in req:
        try:
            val = float(req["portfolio_fraction"])
            if val < 0 or val > 1:
                errors.append("portfolio_fraction must be between 0 and 1")
            else:
                cfg.portfolio_fraction = val
        except (ValueError, TypeError):
            errors.append("portfolio_fraction must be a number")

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    config_dict = {
        "method": cfg.method,
        "fixed_quantity": cfg.fixed_quantity,
        "portfolio_fraction": cfg.portfolio_fraction,
        "kelly_win_rate": cfg.kelly_win_rate,
        "kelly_avg_win": cfg.kelly_avg_win,
        "kelly_avg_loss": cfg.kelly_avg_loss,
        "max_position_pct": cfg.max_position_pct,
    }
    await save_position_sizing_config(config_dict)
    return {"status": "ok"}


# --- Performance ---

@router.get("/api/performance/metrics")
async def get_performance_metrics(days: int = 30):
    """Get historical performance metrics."""
    history = await get_performance_history(days)
    return {"data": history, "period_days": days}


@router.get("/api/performance/summary")
async def get_performance_summary():
    """Calculate current performance summary from trade data."""
    trades = await get_trade_pnl_data()
    if not trades:
        return {
            "total_trades": 0, "matched_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0, "total_pnl": 0, "avg_win": 0, "avg_loss": 0,
            "profit_factor": 0, "sharpe_ratio": 0,
        }

    total = len(trades)
    # Calculate P&L from matched buy/sell pairs by symbol
    symbol_buys: dict[str, list] = {}
    realized_pnls: list[float] = []

    for t in trades:
        symbol = t.get("symbol", "")
        side = (t.get("side") or "").upper()
        price = t.get("filled_price") or 0
        qty = t.get("filled_quantity") or t.get("quantity") or 0

        if side == "BUY":
            symbol_buys.setdefault(symbol, []).append({"price": price, "qty": qty})
        elif side == "SELL" and symbol in symbol_buys and symbol_buys[symbol]:
            buy = symbol_buys[symbol].pop(0)
            pnl = (price - buy["price"]) * min(qty, buy["qty"])
            realized_pnls.append(pnl)

    wins = [p for p in realized_pnls if p > 0]
    losses = [p for p in realized_pnls if p < 0]

    total_pnl = sum(realized_pnls) if realized_pnls else 0
    winning_count = len(wins)
    losing_count = len(losses)
    matched = winning_count + losing_count
    win_rate = winning_count / matched * 100 if matched > 0 else 0
    avg_win = sum(wins) / winning_count if winning_count > 0 else 0
    avg_loss = sum(losses) / losing_count if losing_count > 0 else 0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0

    # Calculate Sharpe ratio from realized P&L data
    sharpe_ratio = 0
    if realized_pnls and len(realized_pnls) > 1:
        import statistics
        mean_return = statistics.mean(realized_pnls)
        std_return = statistics.stdev(realized_pnls)
        sharpe_ratio = (mean_return / std_return) * (252 ** 0.5) if std_return > 0 else 0

    return {
        "total_trades": total,
        "matched_trades": matched,
        "winning_trades": winning_count,
        "losing_trades": losing_count,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
    }


# --- Data Export ---

@router.get("/api/export/trades")
async def export_trades(format: str = "csv"):
    """Export filled orders as CSV or JSON."""
    trades = await get_trade_pnl_data()
    if format == "json":
        return trades
    columns = ["id", "broker", "symbol", "side", "order_type", "quantity", "filled_price", "filled_quantity", "status", "created_at"]
    csv_data = _to_csv(trades, columns)
    return StreamingResponse(
        io.BytesIO(csv_data.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades.csv"},
    )


@router.get("/api/export/signals")
async def export_signals(limit: int = 1000, format: str = "csv"):
    """Export signals as CSV or JSON."""
    limit = min(max(limit, 1), 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
    signals = [dict(r) for r in rows]
    if format == "json":
        return signals
    columns = ["id", "symbol", "signal_type", "value", "metadata", "created_at"]
    csv_data = _to_csv(signals, columns)
    return StreamingResponse(
        io.BytesIO(csv_data.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=signals.csv"},
    )


@router.get("/api/export/decisions")
async def export_decisions(limit: int = 1000, format: str = "csv"):
    """Export trade decisions as CSV or JSON."""
    limit = min(max(limit, 1), 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM trade_decisions ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
    decisions = [dict(r) for r in rows]
    if format == "json":
        return decisions
    columns = ["id", "strategy", "symbol", "side", "quantity", "price", "reasoning", "confidence", "risk_check_passed", "created_at"]
    csv_data = _to_csv(decisions, columns)
    return StreamingResponse(
        io.BytesIO(csv_data.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=decisions.csv"},
    )


# --- Strategy Presets ---

STRATEGY_PRESETS = {
    "conservative": {
        "description": "Low risk, fewer trades, wider thresholds",
        "signal_config": {"rsi_period": 14, "rsi_oversold": 25, "rsi_overbought": 75, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "volume_spike_ratio": 3.0, "bb_period": 20, "bb_std_dev": 2.5},
        "position_sizing": {"method": "fixed_fractional", "portfolio_fraction": 0.01, "max_position_pct": 0.05},
    },
    "balanced": {
        "description": "Moderate risk/reward balance",
        "signal_config": {"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "volume_spike_ratio": 2.0, "bb_period": 20, "bb_std_dev": 2.0},
        "position_sizing": {"method": "fixed_fractional", "portfolio_fraction": 0.02, "max_position_pct": 0.10},
    },
    "aggressive": {
        "description": "Higher risk, more frequent trades, tighter thresholds",
        "signal_config": {"rsi_period": 7, "rsi_oversold": 35, "rsi_overbought": 65, "macd_fast": 8, "macd_slow": 21, "macd_signal": 5, "volume_spike_ratio": 1.5, "bb_period": 15, "bb_std_dev": 1.5},
        "position_sizing": {"method": "kelly", "kelly_win_rate": 0.55, "kelly_avg_win": 1.5, "kelly_avg_loss": 1.0, "max_position_pct": 0.15},
    },
}


@router.get("/api/presets")
async def get_strategy_presets():
    """Get all available strategy presets."""
    return STRATEGY_PRESETS


@router.post("/api/presets/{preset_name}/apply")
async def apply_strategy_preset(preset_name: str):
    """Apply a strategy preset to signal and position sizing configs."""
    if preset_name not in STRATEGY_PRESETS:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": f"Preset '{preset_name}' not found"})

    preset = STRATEGY_PRESETS[preset_name]

    from app.main import signal_engine, execution_engine

    cfg = signal_engine.signal_config
    for key, value in preset["signal_config"].items():
        setattr(cfg, key, type(getattr(cfg, key))(value))

    sizing_cfg = execution_engine._position_sizer.config
    for key, value in preset["position_sizing"].items():
        if hasattr(sizing_cfg, key):
            setattr(sizing_cfg, key, type(getattr(sizing_cfg, key))(value))

    return {"status": "ok", "preset": preset_name}


# --- Webhooks ---

@router.get("/api/webhooks")
async def list_webhooks():
    from app.core.webhooks import webhook_manager
    return webhook_manager.list_webhooks()


@router.post("/api/webhooks")
async def create_webhook(req: dict):
    from app.core.webhooks import webhook_manager, Webhook
    import uuid

    url = req.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="URL is required")

    errors = _validate_webhook_url(url)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    webhook_id = str(uuid.uuid4())[:8]
    webhook = Webhook(
        id=webhook_id,
        url=url,
        event_types=req.get("event_types", ["*"]),
        enabled=req.get("enabled", True),
    )
    webhook_manager.register(webhook)
    return {"id": webhook_id, "status": "created"}


@router.delete("/api/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str):
    from app.core.webhooks import webhook_manager
    if webhook_manager.unregister(webhook_id):
        return {"status": "deleted"}
    return JSONResponse(status_code=404, content={"detail": "Webhook not found"})


@router.post("/api/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: str):
    from app.core.webhooks import webhook_manager
    webhooks = {w["id"]: w for w in webhook_manager.list_webhooks()}
    if webhook_id not in webhooks:
        return JSONResponse(status_code=404, content={"detail": "Webhook not found"})
    await webhook_manager.dispatch("test", {"message": "Webhook test from Claw Trader"})
    return {"status": "test_sent"}


# --- System ---

@router.post("/api/auth/generate-key")
async def generate_new_api_key():
    """Generate a new API key (only works when auth is disabled or already authenticated)."""
    from app.core.auth import generate_api_key
    key = generate_api_key()
    return {"api_key": key, "note": "Set CT_API_SECRET_KEY to this value and CT_AUTH_ENABLED=true to enable authentication"}


@router.get("/api/health")
async def health():
    from app.main import signal_engine, llm_brain, risk_engine, execution_engine

    db_connected = False
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("SELECT 1")
            db_connected = True
    except Exception:
        pass

    timestamps = await get_latest_timestamps()
    uptime_s = time.monotonic() - _app_start_time

    return {
        "status": "ok",
        "app": settings.app_name,
        "db_connected": db_connected,
        "last_signal_at": timestamps["last_signal_at"],
        "last_decision_at": timestamps["last_decision_at"],
        "uptime_s": uptime_s,
        "engines": {
            "signal_engine": signal_engine._running,
            "llm_configured": llm_brain._provider is not None,
            "kill_switch": risk_engine.kill_switch_active,
            "brokers_registered": list(execution_engine._brokers.keys()),
        },
    }
