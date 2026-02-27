from __future__ import annotations

import aiosqlite
import asyncio
import base64
import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger


_write_lock = asyncio.Lock()


def _xor_encrypt(value: str, key: str) -> str:
    """Simple XOR encryption for secrets at rest. Not cryptographically strong but better than plaintext."""
    key_bytes = sha256(key.encode()).digest()
    val_bytes = value.encode()
    encrypted = bytes(v ^ key_bytes[i % len(key_bytes)] for i, v in enumerate(val_bytes))
    return base64.b64encode(encrypted).decode()


def _xor_decrypt(encrypted: str, key: str) -> str:
    """Decrypt XOR-encrypted value. Returns plaintext if decryption fails (backwards compat)."""
    try:
        key_bytes = sha256(key.encode()).digest()
        val_bytes = base64.b64decode(encrypted)
        decrypted = bytes(v ^ key_bytes[i % len(key_bytes)] for i, v in enumerate(val_bytes))
        return decrypted.decode()
    except Exception:
        # Backwards compatibility: if decryption fails, treat as plaintext
        return encrypted


def _get_encryption_key() -> str:
    """Get the encryption key, defaulting to a standard key if not configured."""
    return settings.api_secret_key or "claw-trader-default-key"


def _get_db_path() -> str:
    url = settings.database_url
    if ":///" in url:
        return url.split("///", 1)[1]
    return url


DB_PATH = _get_db_path()


MIGRATIONS = [
    (1, "Initial schema", None),
    (2, "Add performance_metrics table", """
    CREATE TABLE IF NOT EXISTS performance_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        total_trades INTEGER DEFAULT 0,
        winning_trades INTEGER DEFAULT 0,
        losing_trades INTEGER DEFAULT 0,
        total_pnl REAL DEFAULT 0,
        avg_win REAL DEFAULT 0,
        avg_loss REAL DEFAULT 0,
        win_rate REAL DEFAULT 0,
        profit_factor REAL DEFAULT 0,
        sharpe_ratio REAL DEFAULT 0,
        max_drawdown_pct REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )
"""),
    (3, "Add expires_at to orders", "ALTER TABLE orders ADD COLUMN expires_at TEXT"),
    (4, "Add trade_journal table", """
    CREATE TABLE IF NOT EXISTS trade_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_id INTEGER,
        order_id INTEGER,
        event_type TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT,
        quantity REAL,
        price REAL,
        status TEXT,
        details TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (decision_id) REFERENCES trade_decisions(id),
        FOREIGN KEY (order_id) REFERENCES orders(id)
    )
"""),
    (5, "Add max_position_concentration_pct to risk_config", "ALTER TABLE risk_config ADD COLUMN max_position_concentration_pct REAL DEFAULT 20.0"),
    (6, "Add signal_config table", """
    CREATE TABLE IF NOT EXISTS signal_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rsi_period INTEGER NOT NULL DEFAULT 14,
        rsi_oversold REAL NOT NULL DEFAULT 30,
        rsi_overbought REAL NOT NULL DEFAULT 70,
        macd_fast INTEGER NOT NULL DEFAULT 12,
        macd_slow INTEGER NOT NULL DEFAULT 26,
        macd_signal INTEGER NOT NULL DEFAULT 9,
        volume_spike_ratio REAL NOT NULL DEFAULT 2.0,
        bb_period INTEGER NOT NULL DEFAULT 20,
        bb_std_dev REAL NOT NULL DEFAULT 2.0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""),
    (7, "Add position_sizing_config table", """
    CREATE TABLE IF NOT EXISTS position_sizing_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        method TEXT NOT NULL DEFAULT 'fixed',
        fixed_quantity REAL NOT NULL DEFAULT 1.0,
        portfolio_fraction REAL NOT NULL DEFAULT 0.02,
        kelly_win_rate REAL NOT NULL DEFAULT 0.55,
        kelly_avg_win REAL NOT NULL DEFAULT 1.5,
        kelly_avg_loss REAL NOT NULL DEFAULT 1.0,
        max_position_pct REAL NOT NULL DEFAULT 0.1,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""),
]


async def run_migrations() -> None:
    """Run pending database migrations."""
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT DEFAULT (datetime('now'))
                )"""
            )
            await db.commit()

            cursor = await db.execute("SELECT MAX(version) FROM schema_migrations")
            row = await cursor.fetchone()
            current = row[0] if row[0] is not None else 0

            for version, description, sql in MIGRATIONS:
                if version > current:
                    if sql:
                        await db.execute(sql)
                    await db.execute(
                        "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                        (version, description),
                    )
                    logger.info(f"Applied migration {version}: {description}")
            await db.commit()


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    async with _write_lock:
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

            # Add indexes for performance
            await db.executescript("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_broker_symbol
                    ON positions (broker, symbol);
                CREATE INDEX IF NOT EXISTS idx_signals_created_at
                    ON signals (created_at);
                CREATE INDEX IF NOT EXISTS idx_orders_status
                    ON orders (status);
                CREATE INDEX IF NOT EXISTS idx_trade_decisions_created_at
                    ON trade_decisions (created_at);
            """)
            await db.commit()

    await run_migrations()


async def log_api_usage(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    cost_usd: float,
    request_type: str = "trade_decision",
) -> None:
    async with _write_lock:
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
    async with _write_lock:
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
    expires_at: str | None = None,
) -> int:
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """INSERT INTO orders
                   (broker, symbol, side, order_type, quantity, limit_price, decision_id, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (broker, symbol, side, order_type, quantity, limit_price, decision_id, expires_at),
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
    async with _write_lock:
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
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE trade_decisions SET executed = 1, execution_id = ? WHERE id = ?",
                (execution_id, decision_id),
            )
            await db.commit()


async def log_signal(symbol: str, signal_type: str, value: float, metadata: dict) -> None:
    async with _write_lock:
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
    async with _write_lock:
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
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO positions (broker, symbol, quantity, avg_entry_price,
                   current_price, unrealized_pnl, realized_pnl, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(broker, symbol) DO UPDATE SET
                   quantity = excluded.quantity,
                   avg_entry_price = excluded.avg_entry_price,
                   current_price = excluded.current_price,
                   unrealized_pnl = excluded.unrealized_pnl,
                   realized_pnl = excluded.realized_pnl,
                   updated_at = datetime('now')""",
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
    max_position_concentration_pct: float | None = None,
) -> None:
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            try:
                await db.execute("BEGIN")
                await db.execute("DELETE FROM risk_config")
                await db.execute(
                    """INSERT INTO risk_config
                       (max_position_usd, max_daily_loss_usd, max_portfolio_exposure_usd,
                        max_single_trade_usd, max_drawdown_pct, max_position_concentration_pct)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (max_position_usd, max_daily_loss_usd, max_portfolio_exposure_usd,
                     max_single_trade_usd, max_drawdown_pct,
                     max_position_concentration_pct or 20.0),
                )
                await db.execute("COMMIT")
            except Exception as e:
                await db.execute("ROLLBACK")
                logger.error(f"Failed to save risk config: {e}")
                raise


async def load_risk_config() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM risk_config ORDER BY id DESC LIMIT 1")
        row = await cursor.fetchone()
        return dict(row) if row else None


async def save_signal_config(config_dict: dict) -> None:
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            try:
                await db.execute("BEGIN")
                await db.execute("DELETE FROM signal_config")
                await db.execute(
                    """INSERT INTO signal_config
                       (rsi_period, rsi_oversold, rsi_overbought, macd_fast, macd_slow, macd_signal,
                        volume_spike_ratio, bb_period, bb_std_dev)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (config_dict.get("rsi_period", 14),
                     config_dict.get("rsi_oversold", 30),
                     config_dict.get("rsi_overbought", 70),
                     config_dict.get("macd_fast", 12),
                     config_dict.get("macd_slow", 26),
                     config_dict.get("macd_signal", 9),
                     config_dict.get("volume_spike_ratio", 2.0),
                     config_dict.get("bb_period", 20),
                     config_dict.get("bb_std_dev", 2.0)),
                )
                await db.execute("COMMIT")
            except Exception as e:
                await db.execute("ROLLBACK")
                logger.error(f"Failed to save signal config: {e}")
                raise


async def load_signal_config() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM signal_config ORDER BY id DESC LIMIT 1")
        row = await cursor.fetchone()
        return dict(row) if row else None


async def save_position_sizing_config(config_dict: dict) -> None:
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            try:
                await db.execute("BEGIN")
                await db.execute("DELETE FROM position_sizing_config")
                await db.execute(
                    """INSERT INTO position_sizing_config
                       (method, fixed_quantity, portfolio_fraction, kelly_win_rate, kelly_avg_win,
                        kelly_avg_loss, max_position_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (config_dict.get("method", "fixed"),
                     config_dict.get("fixed_quantity", 1.0),
                     config_dict.get("portfolio_fraction", 0.02),
                     config_dict.get("kelly_win_rate", 0.55),
                     config_dict.get("kelly_avg_win", 1.5),
                     config_dict.get("kelly_avg_loss", 1.0),
                     config_dict.get("max_position_pct", 0.1)),
                )
                await db.execute("COMMIT")
            except Exception as e:
                await db.execute("ROLLBACK")
                logger.error(f"Failed to save position sizing config: {e}")
                raise


async def load_position_sizing_config() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM position_sizing_config ORDER BY id DESC LIMIT 1")
        row = await cursor.fetchone()
        return dict(row) if row else None


async def prune_old_records(days: int = 30) -> dict[str, int]:
    """Delete records older than the specified number of days."""
    if not isinstance(days, int) or days < 1:
        raise ValueError("days must be a positive integer (>= 1)")
    counts = {}
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            # Prune standard tables
            for table in ("signals", "risk_snapshots", "api_usage"):
                cursor = await db.execute(
                    f"DELETE FROM {table} WHERE created_at < datetime('now', ?)",
                    (f'-{days} days',)
                )
                counts[table] = cursor.rowcount

            # Prune trade_journal
            cursor = await db.execute(
                "DELETE FROM trade_journal WHERE created_at < datetime('now', ?)",
                (f'-{days} days',)
            )
            counts["trade_journal"] = cursor.rowcount

            # Prune trade_decisions
            cursor = await db.execute(
                "DELETE FROM trade_decisions WHERE created_at < datetime('now', ?)",
                (f'-{days} days',)
            )
            counts["trade_decisions"] = cursor.rowcount

            # Prune performance_metrics
            cursor = await db.execute(
                "DELETE FROM performance_metrics WHERE created_at < datetime('now', ?)",
                (f'-{days} days',)
            )
            counts["performance_metrics"] = cursor.rowcount

            # Prune terminal orders only (filled, failed, expired, cancelled)
            cursor = await db.execute(
                "DELETE FROM orders WHERE created_at < datetime('now', ?) AND status IN ('filled', 'failed', 'expired', 'cancelled')",
                (f'-{days} days',)
            )
            counts["orders"] = cursor.rowcount

            await db.commit()
    return counts


async def load_llm_config() -> dict | None:
    """Load the most recent active LLM configuration from database."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM llm_config WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if not row:
            return None
        config = dict(row)
        # Decrypt api_key if present
        if config.get("api_key"):
            try:
                config["api_key"] = _xor_decrypt(config["api_key"], _get_encryption_key())
            except Exception as e:
                logger.warning(f"Failed to decrypt api_key: {e}, using as-is")
        return config


async def get_latest_timestamps() -> dict[str, str | None]:
    """Get the most recent signal and trade decision timestamps from database."""
    async with aiosqlite.connect(DB_PATH) as db:
        signal_row = await (await db.execute("SELECT MAX(created_at) FROM signals")).fetchone()
        decision_row = await (await db.execute("SELECT MAX(created_at) FROM trade_decisions")).fetchone()
        return {
            "last_signal_at": signal_row[0] if signal_row and signal_row[0] else None,
            "last_decision_at": decision_row[0] if decision_row and decision_row[0] else None,
        }


async def count_orders() -> int:
    """Get total count of orders in database."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM orders")
        row = await cursor.fetchone()
        return row[0] if row else 0


async def count_trade_decisions() -> int:
    """Get total count of trade decisions in database."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM trade_decisions")
        row = await cursor.fetchone()
        return row[0] if row else 0


async def count_signals() -> int:
    """Get total count of signals in database."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM signals")
        row = await cursor.fetchone()
        return row[0] if row else 0


async def count_api_usage() -> int:
    """Get total count of API usage records in database."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM api_usage")
        row = await cursor.fetchone()
        return row[0] if row else 0


async def count_risk_snapshots() -> int:
    """Get total count of risk snapshots in database."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM risk_snapshots")
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_stale_orders(max_age_seconds: int = 30) -> list[dict]:
    """Get orders that are still pending/submitted and older than max_age_seconds."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM orders WHERE status IN ('pending', 'submitted') AND created_at < datetime('now', ?)",
            (f'-{max_age_seconds} seconds',)
        )).fetchall()
        return [dict(row) for row in rows]


async def save_performance_metrics(metrics: dict) -> None:
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO performance_metrics
                (date, total_trades, winning_trades, losing_trades, total_pnl,
                 avg_win, avg_loss, win_rate, profit_factor, sharpe_ratio, max_drawdown_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (metrics["date"], metrics["total_trades"], metrics["winning_trades"],
                 metrics["losing_trades"], metrics["total_pnl"], metrics["avg_win"],
                 metrics["avg_loss"], metrics["win_rate"], metrics["profit_factor"],
                 metrics["sharpe_ratio"], metrics["max_drawdown_pct"]),
            )
            await db.commit()


async def get_performance_history(days: int = 30) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM performance_metrics WHERE date >= date('now', ?) ORDER BY date DESC",
            (f'-{days} days',)
        )).fetchall()
        return [dict(row) for row in rows]


async def get_trade_pnl_data() -> list[dict]:
    """Get all filled orders with their P&L for performance calculation."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT o.*, d.confidence, d.reasoning
               FROM orders o
               LEFT JOIN trade_decisions d ON o.decision_id = d.id
               WHERE o.status = 'filled'
               ORDER BY o.created_at"""
        )).fetchall()
        return [dict(row) for row in rows]


async def get_expired_orders() -> list[dict]:
    """Get orders that have expired (status pending/submitted AND past expiry time)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM orders WHERE status IN ('pending', 'submitted') AND expires_at IS NOT NULL AND expires_at < datetime('now')"
        )).fetchall()
        return [dict(row) for row in rows]


async def log_journal_entry(
    event_type: str,
    symbol: str,
    side: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
    status: str | None = None,
    decision_id: int | None = None,
    order_id: int | None = None,
    details: dict | None = None,
) -> int:
    """Log a journal entry for trade audit trail."""
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """INSERT INTO trade_journal (decision_id, order_id, event_type, symbol, side, quantity, price, status, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (decision_id, order_id, event_type, symbol, side, quantity, price, status, json.dumps(details or {})),
            )
            await db.commit()
            return cursor.lastrowid


async def get_trade_journal(limit: int = 100, offset: int = 0, symbol: str | None = None) -> list[dict]:
    """Get trade journal entries with optional symbol filter."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if symbol:
            rows = await (await db.execute(
                "SELECT * FROM trade_journal WHERE symbol = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (symbol, limit, offset),
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM trade_journal ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )).fetchall()
        return [dict(row) for row in rows]


async def count_journal_entries(symbol: str | None = None) -> int:
    """Count journal entries with optional symbol filter."""
    async with aiosqlite.connect(DB_PATH) as db:
        if symbol:
            row = await (await db.execute("SELECT COUNT(*) FROM trade_journal WHERE symbol = ?", (symbol,))).fetchone()
        else:
            row = await (await db.execute("SELECT COUNT(*) FROM trade_journal")).fetchone()
        return row[0]


async def get_recent_trade_stats(lookback_days: int = 30) -> dict | None:
    """Get recent trade statistics for live Kelly parameter updates.

    Returns dict with: total_trades, win_rate, avg_win, avg_loss
    Returns None if no completed trades found.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Get filled buy/sell pairs from the last N days
        cursor = await db.execute(
            """
            SELECT o.* FROM orders o
            WHERE o.status = 'filled'
            AND o.created_at >= datetime('now', ?)
            ORDER BY o.created_at ASC
            """,
            (f'-{lookback_days} days',)
        )
        orders = [dict(row) for row in await cursor.fetchall()]

        if not orders:
            return None

        # Match buy/sell pairs FIFO for P&L calculation
        buys = [o for o in orders if o['side'].upper() == 'BUY']
        sells = [o for o in orders if o['side'].upper() == 'SELL']

        wins = []
        losses = []
        matched_trades = 0

        # Match pairs FIFO
        for sell in sells:
            remaining_qty = sell.get('filled_quantity', 0)
            if not buys or remaining_qty <= 0:
                continue

            buy = buys[0]
            buy_qty = buy.get('filled_quantity', 0)
            buy_price = buy.get('filled_price', 0)
            sell_price = sell.get('filled_price', 0)

            if buy_qty <= 0 or buy_price <= 0 or sell_price <= 0:
                continue

            # Match as much as possible from first buy
            match_qty = min(buy_qty, remaining_qty)
            pnl = (sell_price - buy_price) * match_qty

            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(abs(pnl))

            matched_trades += 1

            # Remove buy if fully consumed
            if match_qty >= buy_qty:
                buys.pop(0)

        if not wins and not losses:
            return None

        total_trades = len(orders)
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        return {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
        }
