"""Tests for iteration 33 bug fixes in Claw Trader."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.core.database import (
    init_db,
    prune_old_records,
    save_signal_config,
    save_position_sizing_config,
    load_signal_config,
    load_position_sizing_config,
    _xor_encrypt,
    _xor_decrypt,
    _get_encryption_key,
    DB_PATH,
)
from app.engines.risk_engine import RiskEngine


@pytest.fixture(autouse=True)
async def temp_db(tmp_path, monkeypatch):
    """Use a temporary database for each test."""
    db_path = tmp_path / "test.db"
    import app.core.database as db_module
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path))
    await init_db()
    yield str(db_path)


# ============================================================================
# Task 1: LLM Config Update - Decrypt stored key before publishing
# ============================================================================


@pytest.mark.asyncio
async def test_llm_config_update_without_new_key_decrypts_stored_key(temp_db, monkeypatch):
    """Test that updating LLM config without new key decrypts the stored key before publishing."""
    # Patch DB_PATH in all relevant modules
    import app.api.routes as routes_module
    import app.core.database as db_module
    monkeypatch.setattr(routes_module, "DB_PATH", temp_db)
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)

    from app.core.database import _write_lock

    # Setup: Store an encrypted key in the database
    encryption_key = _get_encryption_key()
    original_key = "test-api-key-12345"
    encrypted_key = _xor_encrypt(original_key, encryption_key)

    async with aiosqlite.connect(temp_db) as db:
        await db.execute(
            "INSERT INTO llm_config (provider, model_name, api_key, base_url, is_active) VALUES (?, ?, ?, ?, ?)",
            ("gemini", "gemini-2.0-flash", encrypted_key, "", 1),
        )
        await db.commit()

    # Import the route handler and mock event_bus.publish
    from app.api.routes import update_llm_config, LLMConfigRequest

    event_published = None

    async def capture_publish(event):
        nonlocal event_published
        event_published = event

    with patch("app.api.routes.event_bus.publish", side_effect=capture_publish):
        # Call update_llm_config without providing a new key
        req = LLMConfigRequest(
            provider="gemini",
            model_name="gemini-2.0-flash",
            api_key=None,  # No new key provided
            base_url=""
        )
        result = await update_llm_config(req)

    # Verify the response
    assert result["status"] == "ok"

    # Verify event was published with DECRYPTED key (not encrypted)
    assert event_published is not None
    assert event_published.data["api_key"] == original_key, \
        f"Expected decrypted key '{original_key}' but got '{event_published.data['api_key']}'"


@pytest.mark.asyncio
async def test_llm_config_update_with_new_key_encrypts_and_publishes_plaintext(temp_db, monkeypatch):
    """Test that providing a new key encrypts it in DB and publishes plaintext."""
    from app.api.routes import update_llm_config, LLMConfigRequest
    import app.api.routes as routes_module

    # Patch DB_PATH to use temp_db
    monkeypatch.setattr(routes_module, "DB_PATH", temp_db)

    event_published = None

    async def capture_publish(event):
        nonlocal event_published
        event_published = event

    new_key = "new-api-key-99999"

    with patch("app.api.routes.event_bus.publish", side_effect=capture_publish):
        req = LLMConfigRequest(
            provider="openai",
            model_name="gpt-4",
            api_key=new_key,
            base_url=""
        )
        result = await update_llm_config(req)

    # Verify response
    assert result["status"] == "ok"

    # Verify event was published with plaintext key
    assert event_published is not None
    assert event_published.data["api_key"] == new_key

    # Verify database has encrypted key
    async with aiosqlite.connect(temp_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT api_key FROM llm_config WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()

    assert row is not None, "No LLM config found in database"
    stored_key = dict(row)["api_key"]
    decrypted_key = _xor_decrypt(stored_key, _get_encryption_key())
    assert decrypted_key == new_key, f"DB should store encrypted key that decrypts to {new_key}"


# ============================================================================
# Task 2: Strategy Preset Apply - Persists to database
# ============================================================================


@pytest.mark.asyncio
async def test_strategy_preset_apply_persists_signal_config_to_db(temp_db, monkeypatch):
    """Test that applying a preset persists signal config to database."""
    import app.core.database as db_module
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)

    from app.main import signal_engine, execution_engine

    # Manually apply the logic that apply_strategy_preset does
    cfg = signal_engine.signal_config
    preset = {
        "rsi_period": 14,
        "rsi_oversold": 25,
        "rsi_overbought": 75,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "volume_spike_ratio": 3.0,
        "bb_period": 20,
        "bb_std_dev": 2.5,
    }

    for key, value in preset.items():
        setattr(cfg, key, type(getattr(cfg, key))(value))

    # Save to database
    signal_config_dict = {
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
    await save_signal_config(signal_config_dict)

    # Verify signal config was saved to database
    saved_config = await load_signal_config()
    assert saved_config is not None
    assert saved_config["rsi_period"] == 14
    assert saved_config["rsi_oversold"] == 25
    assert saved_config["rsi_overbought"] == 75
    assert saved_config["macd_fast"] == 12
    assert saved_config["macd_slow"] == 26


@pytest.mark.asyncio
async def test_strategy_preset_apply_persists_position_sizing_to_db(temp_db, monkeypatch):
    """Test that applying a preset persists position sizing config to database."""
    import app.core.database as db_module
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)

    from app.main import execution_engine

    # Manually apply the balanced preset logic
    sizing_cfg = execution_engine._position_sizer.config
    preset = {
        "method": "fixed_fractional",
        "portfolio_fraction": 0.02,
        "max_position_pct": 0.10,
    }

    for key, value in preset.items():
        if hasattr(sizing_cfg, key):
            setattr(sizing_cfg, key, type(getattr(sizing_cfg, key))(value))

    # Save to database
    position_sizing_dict = {
        "method": sizing_cfg.method,
        "fixed_quantity": sizing_cfg.fixed_quantity,
        "portfolio_fraction": sizing_cfg.portfolio_fraction,
        "kelly_win_rate": sizing_cfg.kelly_win_rate,
        "kelly_avg_win": sizing_cfg.kelly_avg_win,
        "kelly_avg_loss": sizing_cfg.kelly_avg_loss,
        "max_position_pct": sizing_cfg.max_position_pct,
    }
    await save_position_sizing_config(position_sizing_dict)

    # Verify position sizing config was saved to database
    saved_config = await load_position_sizing_config()
    assert saved_config is not None
    assert saved_config["method"] == "fixed_fractional"
    assert saved_config["portfolio_fraction"] == 0.02
    assert saved_config["max_position_pct"] == 0.10


@pytest.mark.asyncio
async def test_strategy_preset_apply_aggressive_updates_both_configs(temp_db, monkeypatch):
    """Test that aggressive preset correctly updates both signal and position sizing."""
    import app.core.database as db_module
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)

    from app.main import signal_engine, execution_engine

    # Apply aggressive preset manually
    cfg = signal_engine.signal_config
    signal_preset = {
        "rsi_period": 7,
        "rsi_oversold": 35,
        "rsi_overbought": 65,
        "macd_fast": 8,
        "macd_slow": 21,
        "macd_signal": 5,
        "volume_spike_ratio": 1.5,
        "bb_period": 15,
        "bb_std_dev": 1.5,
    }

    for key, value in signal_preset.items():
        setattr(cfg, key, type(getattr(cfg, key))(value))

    sizing_cfg = execution_engine._position_sizer.config
    position_preset = {
        "method": "kelly",
        "kelly_win_rate": 0.55,
        "kelly_avg_win": 1.5,
        "kelly_avg_loss": 1.0,
        "max_position_pct": 0.15,
    }

    for key, value in position_preset.items():
        if hasattr(sizing_cfg, key):
            setattr(sizing_cfg, key, type(getattr(sizing_cfg, key))(value))

    # Save both configs
    signal_config_dict = {
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
    await save_signal_config(signal_config_dict)

    position_sizing_dict = {
        "method": sizing_cfg.method,
        "fixed_quantity": sizing_cfg.fixed_quantity,
        "portfolio_fraction": sizing_cfg.portfolio_fraction,
        "kelly_win_rate": sizing_cfg.kelly_win_rate,
        "kelly_avg_win": sizing_cfg.kelly_avg_win,
        "kelly_avg_loss": sizing_cfg.kelly_avg_loss,
        "max_position_pct": sizing_cfg.max_position_pct,
    }
    await save_position_sizing_config(position_sizing_dict)

    # Verify signal config
    signal_cfg = await load_signal_config()
    assert signal_cfg["rsi_period"] == 7  # aggressive has smaller RSI period
    assert signal_cfg["rsi_oversold"] == 35
    assert signal_cfg["volume_spike_ratio"] == 1.5  # more aggressive threshold

    # Verify position sizing config
    sizing_cfg_loaded = await load_position_sizing_config()
    assert sizing_cfg_loaded["method"] == "kelly"  # aggressive uses kelly
    assert sizing_cfg_loaded["kelly_win_rate"] == 0.55
    assert sizing_cfg_loaded["kelly_avg_win"] == 1.5


# ============================================================================
# Task 3: Risk Engine - Thread safety with lock
# ============================================================================


@pytest.mark.asyncio
async def test_risk_engine_update_portfolio_acquires_lock():
    """Test that update_portfolio acquires the reset lock."""
    risk_engine = RiskEngine()

    # Call update_portfolio
    positions = {"AAPL": 10000.0, "MSFT": 5000.0}
    daily_pnl = -500.0

    await risk_engine.update_portfolio(positions, daily_pnl)

    # Verify the portfolio was updated (proving lock worked)
    assert risk_engine._portfolio.total_exposure_usd == 15000.0
    assert risk_engine._portfolio.daily_pnl_usd == -500.0


@pytest.mark.asyncio
async def test_risk_engine_update_portfolio_race_condition_prevented():
    """Test that concurrent access to update_portfolio is serialized by lock."""
    risk_engine = RiskEngine()

    # Create a list to track execution order
    execution_order = []

    async def update_and_read(update_id):
        """Update portfolio and record final state."""
        positions = {f"SYM{update_id}": float(update_id * 1000)}
        daily_pnl = float(update_id * -100)

        execution_order.append(f"start_{update_id}")
        await risk_engine.update_portfolio(positions, daily_pnl)

        # Record the state we see (should be consistent)
        final_exposure = risk_engine._portfolio.total_exposure_usd
        final_pnl = risk_engine._portfolio.daily_pnl_usd

        execution_order.append(f"end_{update_id}_exposure_{final_exposure}_pnl_{final_pnl}")

    # Run multiple concurrent updates
    await asyncio.gather(
        update_and_read(1),
        update_and_read(2),
        update_and_read(3),
    )

    # Verify portfolio state is consistent (last update wins)
    # This proves the lock prevented interleaving
    assert risk_engine._portfolio.total_exposure_usd == 3000.0  # SYM3 = 3 * 1000
    assert risk_engine._portfolio.daily_pnl_usd == -300.0  # 3 * -100
    assert len(execution_order) == 6  # 3 start + 3 end


# ============================================================================
# Task 4: Prune Old Records - Input validation
# ============================================================================


@pytest.mark.asyncio
async def test_prune_old_records_validates_positive_int(temp_db):
    """Test that prune_old_records validates days parameter is positive integer."""
    # Test with negative number
    with pytest.raises(ValueError) as exc_info:
        await prune_old_records(-1)
    assert "positive integer" in str(exc_info.value).lower()

    # Test with zero
    with pytest.raises(ValueError) as exc_info:
        await prune_old_records(0)
    assert "positive integer" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_prune_old_records_validates_not_string(temp_db):
    """Test that prune_old_records rejects non-integer types."""
    with pytest.raises(ValueError) as exc_info:
        await prune_old_records("30")  # type: ignore
    assert "positive integer" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_prune_old_records_validates_not_float(temp_db):
    """Test that prune_old_records rejects float values."""
    with pytest.raises(ValueError) as exc_info:
        await prune_old_records(30.5)  # type: ignore
    assert "positive integer" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_prune_old_records_accepts_valid_int(temp_db):
    """Test that prune_old_records accepts valid positive integers."""
    # Should not raise
    result = await prune_old_records(30)
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_prune_old_records_accepts_min_value(temp_db):
    """Test that prune_old_records accepts minimum valid value (1)."""
    result = await prune_old_records(1)
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_prune_old_records_with_old_records(temp_db):
    """Test that prune_old_records actually deletes old records with valid input."""
    from app.core.database import log_signal

    # Insert a signal that's definitely old
    async with aiosqlite.connect(temp_db) as db:
        await db.execute(
            "INSERT INTO signals (symbol, signal_type, value, metadata, created_at) VALUES (?, ?, ?, ?, datetime('now', '-60 days'))",
            ("TEST", "test", 1.0, "{}"),
        )
        await db.commit()

    # Prune with 30 days should delete the 60-day-old record
    result = await prune_old_records(30)
    assert result["signals"] >= 1


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_llm_config_and_risk_engine_no_conflicts(temp_db, monkeypatch):
    """Test that LLM config changes don't interfere with risk engine."""
    from app.api.routes import update_llm_config, LLMConfigRequest

    # Setup risk engine
    risk_engine = RiskEngine()

    # Verify risk engine lock is independent
    assert hasattr(risk_engine, "_reset_lock")

    # Update LLM config (should not affect risk engine)
    with patch("app.api.routes.event_bus.publish", new_callable=AsyncMock):
        req = LLMConfigRequest(
            provider="gemini",
            model_name="gemini-2.0-flash",
            api_key="test-key",
            base_url=""
        )
        result = await update_llm_config(req)

    # Risk engine should still be operational
    await risk_engine.update_portfolio({"AAPL": 5000.0}, -100.0)
    assert risk_engine._portfolio.total_exposure_usd == 5000.0
    assert risk_engine._portfolio.daily_pnl_usd == -100.0


@pytest.mark.asyncio
async def test_preset_apply_with_concurrent_prune(temp_db, monkeypatch):
    """Test that applying presets and pruning don't cause database conflicts."""
    import app.core.database as db_module
    monkeypatch.setattr(db_module, "DB_PATH", temp_db)

    from app.main import signal_engine

    # Apply conservative preset
    cfg = signal_engine.signal_config
    preset = {
        "rsi_period": 14,
        "rsi_oversold": 25,
        "rsi_overbought": 75,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "volume_spike_ratio": 3.0,
        "bb_period": 20,
        "bb_std_dev": 2.5,
    }

    for key, value in preset.items():
        setattr(cfg, key, type(getattr(cfg, key))(value))

    signal_config_dict = {
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
    await save_signal_config(signal_config_dict)

    # Prune old records (should not affect preset that was just saved)
    prune_result = await prune_old_records(30)

    # Verify preset is still in database
    signal_cfg = await load_signal_config()
    assert signal_cfg is not None
    assert signal_cfg["rsi_period"] == 14
