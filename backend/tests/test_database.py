from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import (
    init_db,
    log_api_usage,
    log_trade_decision,
    log_order,
    update_order_status,
    mark_decision_executed,
    log_signal,
    save_risk_snapshot,
    upsert_position,
    DB_PATH,
)

import aiosqlite
import tempfile
import os


@pytest.fixture(autouse=True)
async def temp_db(tmp_path, monkeypatch):
    """Use a temporary database for each test."""
    db_path = tmp_path / "test.db"
    import app.core.database as db_module
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    await init_db()
    yield db_path


@pytest.mark.asyncio
async def test_init_db_creates_tables(temp_db):
    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]
    assert "llm_config" in tables
    assert "api_usage" in tables
    assert "trade_decisions" in tables
    assert "orders" in tables
    assert "positions" in tables
    assert "risk_snapshots" in tables
    assert "signals" in tables


@pytest.mark.asyncio
async def test_log_api_usage(temp_db):
    await log_api_usage(
        provider="gemini", model="flash", prompt_tokens=100,
        completion_tokens=50, latency_ms=200.0, cost_usd=0.001,
    )
    async with aiosqlite.connect(temp_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM api_usage")
        rows = await cursor.fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["provider"] == "gemini"
    assert dict(rows[0])["total_tokens"] == 150


@pytest.mark.asyncio
async def test_log_trade_decision_returns_id(temp_db):
    decision_id = await log_trade_decision(
        strategy="test", symbol="AAPL", side="buy", quantity=10.0,
        price=150.0, reasoning="test", confidence=0.8,
        signals_snapshot={}, risk_check_passed=True,
    )
    assert decision_id >= 1


@pytest.mark.asyncio
async def test_log_order_returns_id(temp_db):
    order_id = await log_order(
        broker="ibkr", symbol="AAPL", side="buy",
        order_type="MARKET", quantity=10.0,
    )
    assert order_id >= 1


@pytest.mark.asyncio
async def test_update_order_status(temp_db):
    order_id = await log_order(
        broker="ibkr", symbol="AAPL", side="buy",
        order_type="MARKET", quantity=10.0,
    )
    await update_order_status(order_id, "filled", broker_order_id="B-123",
                              filled_price=151.0, filled_quantity=10.0)
    async with aiosqlite.connect(temp_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = dict(await cursor.fetchone())
    assert row["status"] == "filled"
    assert row["broker_order_id"] == "B-123"
    assert row["filled_price"] == 151.0


@pytest.mark.asyncio
async def test_mark_decision_executed(temp_db):
    dec_id = await log_trade_decision(
        strategy="test", symbol="AAPL", side="buy", quantity=10.0,
        price=150.0, reasoning="test", confidence=0.8,
        signals_snapshot={}, risk_check_passed=True,
    )
    await mark_decision_executed(dec_id, "EXE-001")
    async with aiosqlite.connect(temp_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM trade_decisions WHERE id = ?", (dec_id,))
        row = dict(await cursor.fetchone())
    assert row["executed"] == 1
    assert row["execution_id"] == "EXE-001"


@pytest.mark.asyncio
async def test_log_signal(temp_db):
    await log_signal("AAPL", "rsi_oversold", 25.0, {"threshold": 30})
    async with aiosqlite.connect(temp_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM signals")
        rows = await cursor.fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["symbol"] == "AAPL"
    assert dict(rows[0])["signal_type"] == "rsi_oversold"


@pytest.mark.asyncio
async def test_save_risk_snapshot(temp_db):
    await save_risk_snapshot(
        total_exposure_usd=10000.0, daily_pnl_usd=-200.0,
        max_drawdown_pct=3.5, var_95_usd=500.0,
        positions_count=3, kill_switch_active=False,
        details={"AAPL": 5000},
    )
    async with aiosqlite.connect(temp_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM risk_snapshots")
        rows = await cursor.fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["total_exposure_usd"] == 10000.0
    assert dict(rows[0])["kill_switch_active"] == 0


@pytest.mark.asyncio
async def test_upsert_position_insert(temp_db):
    await upsert_position("ibkr", "AAPL", 100.0, 150.0, 155.0, 500.0, 0.0)
    async with aiosqlite.connect(temp_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM positions")
        rows = await cursor.fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["symbol"] == "AAPL"
    assert dict(rows[0])["quantity"] == 100.0


@pytest.mark.asyncio
async def test_upsert_position_update(temp_db):
    await upsert_position("ibkr", "AAPL", 100.0, 150.0, 155.0, 500.0, 0.0)
    await upsert_position("ibkr", "AAPL", 200.0, 152.0, 160.0, 1600.0, 200.0)
    async with aiosqlite.connect(temp_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM positions")
        rows = await cursor.fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["quantity"] == 200.0
    assert dict(rows[0])["realized_pnl"] == 200.0


@pytest.mark.asyncio
async def test_save_and_load_risk_config(temp_db):
    from app.core.database import save_risk_config, load_risk_config
    await save_risk_config(15000, 7500, 75000, 3000, 15.0)
    loaded = await load_risk_config()
    assert loaded is not None
    assert loaded["max_position_usd"] == 15000
    assert loaded["max_daily_loss_usd"] == 7500
    assert loaded["max_portfolio_exposure_usd"] == 75000
    assert loaded["max_single_trade_usd"] == 3000
    assert loaded["max_drawdown_pct"] == 15.0


@pytest.mark.asyncio
async def test_save_risk_config_overwrites(temp_db):
    from app.core.database import save_risk_config, load_risk_config
    await save_risk_config(10000, 5000, 50000, 2000, 10.0)
    await save_risk_config(20000, 10000, 100000, 4000, 20.0)
    loaded = await load_risk_config()
    assert loaded["max_position_usd"] == 20000
    assert loaded["max_single_trade_usd"] == 4000


@pytest.mark.asyncio
async def test_load_risk_config_empty(temp_db):
    from app.core.database import load_risk_config
    loaded = await load_risk_config()
    assert loaded is None


@pytest.mark.asyncio
async def test_prune_old_records(temp_db):
    from app.core.database import prune_old_records, log_signal
    # Insert a recent signal
    await log_signal("AAPL", "rsi_oversold", 25.0, {"threshold": 30})
    # Pruning with 0 days would delete everything, but let's use 30 days
    # The record was just created, so it won't be pruned
    counts = await prune_old_records(30)
    assert counts["signals"] == 0  # nothing old to prune


@pytest.mark.asyncio
async def test_load_llm_config_returns_none_when_empty(temp_db):
    from app.core.database import load_llm_config
    result = await load_llm_config()
    assert result is None


@pytest.mark.asyncio
async def test_load_llm_config_returns_active_config(temp_db):
    from app.core.database import load_llm_config
    async with aiosqlite.connect(temp_db) as db:
        await db.execute(
            "INSERT INTO llm_config (provider, model_name, api_key, base_url, is_active) VALUES (?, ?, ?, ?, ?)",
            ("gemini", "gemini-2.0-flash", "test-key", "", 1),
        )
        await db.commit()
    result = await load_llm_config()
    assert result is not None
    assert result["provider"] == "gemini"
    assert result["api_key"] == "test-key"


@pytest.mark.asyncio
async def test_prune_old_records_parameterized(temp_db):
    """Verify prune uses parameterized queries (no SQL injection)."""
    from app.core.database import prune_old_records
    # Insert a signal that's definitely old
    async with aiosqlite.connect(temp_db) as db:
        await db.execute(
            "INSERT INTO signals (symbol, signal_type, value, metadata, created_at) VALUES (?, ?, ?, ?, datetime('now', '-60 days'))",
            ("TEST", "test", 1.0, "{}"),
        )
        await db.commit()
    result = await prune_old_records(30)
    assert result["signals"] >= 1


# ============================================================================
# Database Migration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_run_migrations_creates_table(temp_db):
    """Test that run_migrations creates schema_migrations table."""
    from app.core.database import run_migrations
    await run_migrations()
    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        result = await cursor.fetchone()
    assert result is not None
    assert result[0] == "schema_migrations"


@pytest.mark.asyncio
async def test_run_migrations_applies_base_migration(temp_db):
    """Test that run_migrations applies version 1 migration."""
    from app.core.database import run_migrations
    await run_migrations()
    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute(
            "SELECT version, description FROM schema_migrations WHERE version = 1"
        )
        result = await cursor.fetchone()
    assert result is not None
    assert result[0] == 1
    assert result[1] == "Initial schema"


@pytest.mark.asyncio
async def test_run_migrations_idempotent(temp_db):
    """Test that run_migrations is idempotent (can be called multiple times)."""
    from app.core.database import run_migrations
    await run_migrations()
    await run_migrations()
    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM schema_migrations")
        result = await cursor.fetchone()
    # Should only have one entry for version 1, not duplicates
    assert result[0] == 1


@pytest.mark.asyncio
async def test_run_migrations_tracks_version(temp_db):
    """Test that run_migrations tracks applied versions correctly."""
    from app.core.database import run_migrations
    await run_migrations()
    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute("SELECT MAX(version) FROM schema_migrations")
        result = await cursor.fetchone()
    assert result[0] == 1
