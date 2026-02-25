from __future__ import annotations

import aiosqlite
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("claw_trader.db")


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
