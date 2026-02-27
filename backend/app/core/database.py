from __future__ import annotations

import aiosqlite
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings


def _get_db_path() -> str:
    url = settings.database_url
    if ":///" in url:
        return url.split("///", 1)[1]
    return url


DB_PATH = _get_db_path()


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS llm_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL DEFAULT 'gemini',
                model_name TEXT NOT NULL DEFAULT 'gemini-2.0-flash',
                api_key TEXT NOT NULL DEFAULT '',
                base_url TEXT DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                latency_ms REAL NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                request_type TEXT NOT NULL DEFAULT 'trade_decision',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS trade_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 0,
                price REAL NOT NULL DEFAULT 0,
                reasoning TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                signals_snapshot TEXT NOT NULL DEFAULT '{}',
                risk_check_passed INTEGER NOT NULL DEFAULT 0,
                risk_rejection_reason TEXT,
                executed INTEGER NOT NULL DEFAULT 0,
                execution_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker TEXT NOT NULL,
                broker_order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'MARKET',
                quantity REAL NOT NULL,
                limit_price REAL,
                filled_price REAL,
                filled_quantity REAL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                decision_id INTEGER REFERENCES trade_decisions(id),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 0,
                avg_entry_price REAL NOT NULL DEFAULT 0,
                current_price REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS risk_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_exposure_usd REAL NOT NULL DEFAULT 0,
                daily_pnl_usd REAL NOT NULL DEFAULT 0,
                max_drawdown_pct REAL NOT NULL DEFAULT 0,
                var_95_usd REAL NOT NULL DEFAULT 0,
                positions_count INTEGER NOT NULL DEFAULT 0,
                kill_switch_active INTEGER NOT NULL DEFAULT 0,
                details TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                value REAL NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS risk_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                max_position_usd REAL NOT NULL DEFAULT 10000,
                max_daily_loss_usd REAL NOT NULL DEFAULT 5000,
                max_portfolio_exposure_usd REAL NOT NULL DEFAULT 50000,
                max_single_trade_usd REAL NOT NULL DEFAULT 2000,
                max_drawdown_pct REAL NOT NULL DEFAULT 10,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        await db.commit()


async def log_api_usage(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    cost_usd: float,
    request_type: str = "trade_decision",
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO api_usage
               (provider, model, prompt_tokens, completion_tokens, total_tokens,
                latency_ms, cost_usd, request_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (provider, model, prompt_tokens, completion_tokens,
             prompt_tokens + completion_tokens, latency_ms, cost_usd, request_type),
        )
        await db.commit()


async def log_trade_decision(
    strategy: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    reasoning: str,
    confidence: float,
    signals_snapshot: dict,
    risk_check_passed: bool,
    risk_rejection_reason: str | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO trade_decisions
               (strategy, symbol, side, quantity, price, reasoning, confidence,
                signals_snapshot, risk_check_passed, risk_rejection_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (strategy, symbol, side, quantity, price, reasoning, confidence,
             json.dumps(signals_snapshot), int(risk_check_passed),
             risk_rejection_reason),
        )
        await db.commit()
        return cursor.lastrowid


async def log_order(
    broker: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    decision_id: int | None = None,
    limit_price: float | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO orders
               (broker, symbol, side, order_type, quantity, limit_price, decision_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (broker, symbol, side, order_type, quantity, limit_price, decision_id),
        )
        await db.commit()
        return cursor.lastrowid


async def update_order_status(
    order_id: int,
    status: str,
    broker_order_id: str | None = None,
    filled_price: float | None = None,
    filled_quantity: float | None = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE orders SET status = ?, broker_order_id = COALESCE(?, broker_order_id),
               filled_price = COALESCE(?, filled_price),
               filled_quantity = COALESCE(?, filled_quantity),
               updated_at = datetime('now')
               WHERE id = ?""",
            (status, broker_order_id, filled_price, filled_quantity, order_id),
        )
        await db.commit()


async def mark_decision_executed(decision_id: int, execution_id: str | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE trade_decisions SET executed = 1, execution_id = ? WHERE id = ?",
            (execution_id, decision_id),
        )
        await db.commit()


async def log_signal(symbol: str, signal_type: str, value: float, metadata: dict) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO signals (symbol, signal_type, value, metadata) VALUES (?, ?, ?, ?)",
            (symbol, signal_type, value, json.dumps(metadata)),
        )
        await db.commit()


async def save_risk_snapshot(
    total_exposure_usd: float,
    daily_pnl_usd: float,
    max_drawdown_pct: float,
    var_95_usd: float,
    positions_count: int,
    kill_switch_active: bool,
    details: dict,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO risk_snapshots
               (total_exposure_usd, daily_pnl_usd, max_drawdown_pct, var_95_usd,
                positions_count, kill_switch_active, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (total_exposure_usd, daily_pnl_usd, max_drawdown_pct, var_95_usd,
             positions_count, int(kill_switch_active), json.dumps(details)),
        )
        await db.commit()


async def upsert_position(
    broker: str, symbol: str, quantity: float,
    avg_entry_price: float, current_price: float,
    unrealized_pnl: float, realized_pnl: float,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM positions WHERE broker = ? AND symbol = ?",
            (broker, symbol),
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                """UPDATE positions SET quantity = ?, avg_entry_price = ?,
                   current_price = ?, unrealized_pnl = ?, realized_pnl = ?,
                   updated_at = datetime('now')
                   WHERE id = ?""",
                (quantity, avg_entry_price, current_price, unrealized_pnl, realized_pnl, row[0]),
            )
        else:
            await db.execute(
                """INSERT INTO positions (broker, symbol, quantity, avg_entry_price,
                   current_price, unrealized_pnl, realized_pnl)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (broker, symbol, quantity, avg_entry_price, current_price,
                 unrealized_pnl, realized_pnl),
            )
        await db.commit()


async def save_risk_config(
    max_position_usd: float,
    max_daily_loss_usd: float,
    max_portfolio_exposure_usd: float,
    max_single_trade_usd: float,
    max_drawdown_pct: float,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM risk_config")
        await db.execute(
            """INSERT INTO risk_config
               (max_position_usd, max_daily_loss_usd, max_portfolio_exposure_usd,
                max_single_trade_usd, max_drawdown_pct)
               VALUES (?, ?, ?, ?, ?)""",
            (max_position_usd, max_daily_loss_usd, max_portfolio_exposure_usd,
             max_single_trade_usd, max_drawdown_pct),
        )
        await db.commit()


async def load_risk_config() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM risk_config ORDER BY id DESC LIMIT 1")
        row = await cursor.fetchone()
        return dict(row) if row else None
