from __future__ import annotations

import asyncio
import json
from typing import Any

import aiosqlite
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import DB_PATH
from app.core.events import event_bus

router = APIRouter()


# --- Pydantic models ---

class LLMConfigRequest(BaseModel):
    provider: str  # "gemini", "openai", "local"
    model_name: str
    api_key: str
    base_url: str | None = None


class RiskConfigRequest(BaseModel):
    max_position_usd: float | None = None
    max_daily_loss_usd: float | None = None
    max_portfolio_exposure_usd: float | None = None
    max_single_trade_usd: float | None = None
    max_drawdown_pct: float | None = None


class KillSwitchRequest(BaseModel):
    active: bool


# --- LLM Config ---

@router.get("/api/llm/config")
async def get_llm_config():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM llm_config WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"provider": "gemini", "model_name": "gemini-2.0-flash", "api_key": "", "is_active": True}


@router.post("/api/llm/config")
async def update_llm_config(req: LLMConfigRequest):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE llm_config SET is_active = 0")
        await db.execute(
            """INSERT INTO llm_config (provider, model_name, api_key, is_active)
               VALUES (?, ?, ?, 1)""",
            (req.provider, req.model_name, req.api_key),
        )
        await db.commit()

    # Reconfigure the brain (will be wired in main.py)
    await event_bus.publish(
        type("Event", (), {"type": "llm_config_changed", "data": req.model_dump(), "timestamp": ""})()
    )
    return {"status": "ok"}


# --- API Usage ---

@router.get("/api/usage")
async def get_api_usage(limit: int = 100):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM api_usage ORDER BY id DESC LIMIT ?", (limit,)
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
async def get_trade_decisions(limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM trade_decisions ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# --- Orders ---

@router.get("/api/orders")
async def get_orders(limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
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


# --- Balance ---

@router.get("/api/balance/{broker}")
async def get_balance(broker: str):
    # This will be wired to the actual broker adapter in main.py
    # For now, return from the event bus state
    return {"broker": broker, "balance": {}, "note": "Connect broker to see live balance"}


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


@router.get("/api/risk/config")
async def get_risk_config():
    return {
        "max_position_usd": settings.max_position_usd,
        "max_daily_loss_usd": settings.max_daily_loss_usd,
        "max_portfolio_exposure_usd": settings.max_portfolio_exposure_usd,
        "max_single_trade_usd": settings.max_single_trade_usd,
        "max_drawdown_pct": settings.max_drawdown_pct,
    }


@router.post("/api/risk/config")
async def update_risk_config(req: RiskConfigRequest):
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
    return {"status": "ok"}


@router.post("/api/risk/killswitch")
async def toggle_kill_switch(req: KillSwitchRequest):
    await event_bus.publish(
        type("Event", (), {"type": "kill_switch_toggle", "data": {"active": req.active}, "timestamp": ""})()
    )
    return {"status": "ok", "active": req.active}


# --- Signals ---

@router.get("/api/signals")
async def get_recent_signals(limit: int = 100):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
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


# --- System ---

@router.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.app_name}
