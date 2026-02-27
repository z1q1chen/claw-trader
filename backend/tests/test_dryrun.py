from __future__ import annotations

import sys
from pathlib import Path
import json

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.brokers.dryrun import DryRunBrokerAdapter, STATE_FILE


@pytest.fixture
def dryrun_broker() -> DryRunBrokerAdapter:
    # Clean up state file before creating broker to ensure fresh state
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    broker = DryRunBrokerAdapter()
    yield broker
    # Clean up state file after test
    if STATE_FILE.exists():
        STATE_FILE.unlink()


@pytest.mark.asyncio
async def test_buy_order_updates_positions_and_balance(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that buy order updates positions and reduces balance."""
    initial_balance = dryrun_broker._balance
    assert initial_balance == 100000.0

    result = await dryrun_broker.place_order("AAPL", "BUY", 10.0, limit_price=150.0)

    assert result.success is True
    assert result.broker_order_id == "DRY-000001"
    assert result.filled_quantity == 10.0
    assert result.filled_price is not None
    assert result.filled_price > 0

    # Check position was created
    positions = await dryrun_broker.get_positions()
    assert "AAPL" in positions
    assert positions["AAPL"]["quantity"] == 10.0

    # Check balance was reduced
    balance = await dryrun_broker.get_balance()
    assert balance["AvailableFunds"] < initial_balance


@pytest.mark.asyncio
async def test_sell_order_reduces_position(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that sell order reduces position size."""
    # First, buy some shares
    await dryrun_broker.place_order("MSFT", "BUY", 20.0, limit_price=300.0)

    positions = await dryrun_broker.get_positions()
    assert positions["MSFT"]["quantity"] == 20.0

    # Now sell half
    result = await dryrun_broker.place_order("MSFT", "SELL", 10.0, limit_price=305.0)

    assert result.success is True
    assert result.filled_quantity == 10.0

    # Check position was reduced
    positions = await dryrun_broker.get_positions()
    assert positions["MSFT"]["quantity"] == 10.0


@pytest.mark.asyncio
async def test_get_positions_returns_correct_data(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that get_positions returns correct position data."""
    await dryrun_broker.place_order("GOOGL", "BUY", 5.0, limit_price=2500.0)

    positions = await dryrun_broker.get_positions()

    assert "GOOGL" in positions
    pos = positions["GOOGL"]
    assert pos["quantity"] == 5.0
    assert pos["avg_cost"] > 0
    assert pos["market_value"] == pos["quantity"] * pos["avg_cost"]
    assert pos["unrealized_pnl"] == 0
    assert pos["realized_pnl"] == 0


@pytest.mark.asyncio
async def test_get_balance_includes_position_value(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that get_balance includes position values in NetLiquidation."""
    initial_balance = dryrun_broker._balance
    await dryrun_broker.place_order("AMZN", "BUY", 15.0, limit_price=3000.0)

    balance = await dryrun_broker.get_balance()

    # Available funds should be less than initial
    assert balance["AvailableFunds"] < initial_balance

    # NetLiquidation should be approximately equal to initial balance
    # (Available + Position Value)
    assert balance["NetLiquidation"] > balance["AvailableFunds"]
    assert balance["TotalPositionValue"] > 0


@pytest.mark.asyncio
async def test_order_history_tracking(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that order history is tracked correctly."""
    await dryrun_broker.place_order("SPY", "BUY", 100.0, limit_price=450.0)
    await dryrun_broker.place_order("SPY", "SELL", 50.0, limit_price=455.0)

    history = await dryrun_broker.get_order_history(limit=10)

    assert len(history) == 2
    assert history[0]["symbol"] == "SPY"
    assert history[0]["side"] == "BUY"
    assert history[0]["quantity"] == 100.0
    assert history[0]["status"] == "filled"

    assert history[1]["symbol"] == "SPY"
    assert history[1]["side"] == "SELL"
    assert history[1]["quantity"] == 50.0
    assert history[1]["status"] == "filled"


@pytest.mark.asyncio
async def test_slippage_is_applied(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that slippage is applied to filled price."""
    limit_price = 200.0
    result = await dryrun_broker.place_order("TEST", "BUY", 10.0, limit_price=limit_price)

    # For BUY, price should be higher (slippage added)
    assert result.filled_price is not None
    assert result.filled_price > limit_price

    result_sell = await dryrun_broker.place_order("TEST", "SELL", 5.0, limit_price=limit_price)

    # For SELL, price should be lower (slippage subtracted)
    assert result_sell.filled_price is not None
    assert result_sell.filled_price < limit_price


@pytest.mark.asyncio
async def test_cancel_order_returns_false(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that cancel_order returns False (orders instantly filled)."""
    result = await dryrun_broker.place_order("TEST", "BUY", 10.0)
    order_id = result.broker_order_id

    cancel_result = await dryrun_broker.cancel_order(order_id)
    assert cancel_result is False


@pytest.mark.asyncio
async def test_multiple_buys_average_cost(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that multiple buys calculate average cost correctly."""
    # Buy 10 shares at 100
    await dryrun_broker.place_order("STOCK", "BUY", 10.0, limit_price=100.0)

    # Buy 10 more shares at 110
    await dryrun_broker.place_order("STOCK", "BUY", 10.0, limit_price=110.0)

    positions = await dryrun_broker.get_positions()
    assert positions["STOCK"]["quantity"] == 20.0
    # Average cost should be between 100 and 110 (excluding slippage)
    assert positions["STOCK"]["avg_cost"] > 100.0


@pytest.mark.asyncio
async def test_sell_closes_position_when_quantity_zero(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that position is removed when quantity reaches zero."""
    await dryrun_broker.place_order("XYZ", "BUY", 5.0, limit_price=100.0)

    positions = await dryrun_broker.get_positions()
    assert "XYZ" in positions

    # Sell all shares
    await dryrun_broker.place_order("XYZ", "SELL", 5.0, limit_price=100.0)

    positions = await dryrun_broker.get_positions()
    assert "XYZ" not in positions


@pytest.mark.asyncio
async def test_set_price_tracks_last_known_price(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that set_price updates the last known price."""
    # Initially no prices tracked
    assert len(dryrun_broker._last_prices) == 0

    # Set prices for symbols
    dryrun_broker.set_price("AAPL", 150.0)
    dryrun_broker.set_price("GOOGL", 2800.0)

    # Verify prices are stored
    assert dryrun_broker._last_prices["AAPL"] == 150.0
    assert dryrun_broker._last_prices["GOOGL"] == 2800.0


@pytest.mark.asyncio
async def test_place_order_uses_last_known_price_when_no_limit(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that place_order uses last known price instead of random when no limit_price."""
    # Set a known price
    dryrun_broker.set_price("MSFT", 400.0)

    # Place order without limit_price (should use tracked price)
    result = await dryrun_broker.place_order("MSFT", "BUY", 10.0)

    # The filled_price should be approximately near 400.0 (with slippage applied)
    assert result.filled_price is not None
    # For BUY with base_price=400 and slippage 0.1-0.3%, should be roughly 400.4-400.12
    assert 399.0 < result.filled_price < 410.0


@pytest.mark.asyncio
async def test_place_order_uses_default_price_for_unknown_symbol(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that place_order uses default price 100.0 for unknown symbols."""
    # Don't set any price for this symbol
    result = await dryrun_broker.place_order("UNKNOWN", "BUY", 10.0)

    # Should use default of 100.0 with slippage
    assert result.filled_price is not None
    # For BUY with base_price=100 and slippage 0.1-0.3%, should be roughly 100.1-100.3
    assert 99.0 < result.filled_price < 101.0


@pytest.mark.asyncio
async def test_limit_price_takes_precedence_over_tracked_price(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that explicit limit_price takes precedence over tracked price."""
    # Set a tracked price
    dryrun_broker.set_price("TSLA", 250.0)

    # Place order with explicit limit_price (should override tracked price)
    result = await dryrun_broker.place_order("TSLA", "BUY", 10.0, limit_price=300.0)

    # Should use the explicit limit_price, not the tracked price
    assert result.filled_price is not None
    # For BUY with base_price=300 and slippage, should be around 300.3-300.9
    assert 299.0 < result.filled_price < 310.0


@pytest.mark.asyncio
async def test_unrealized_pnl_calculated_with_last_price(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that unrealized P&L is calculated using last known price, not avg_cost."""
    # Buy 10 shares at 100
    await dryrun_broker.place_order("STOCK", "BUY", 10.0, limit_price=100.0)

    # Update last known price to 150 (price increased)
    dryrun_broker.set_price("STOCK", 150.0)

    positions = await dryrun_broker.get_positions()
    pos = positions["STOCK"]

    # avg_cost should still be around 100
    assert 99.0 < pos["avg_cost"] < 101.0

    # market_value should be calculated from current price (150)
    assert pos["market_value"] == pytest.approx(1500.0, abs=1.0)

    # unrealized_pnl should be (market_value - cost basis)
    # = 1500 - 1000 = 500
    expected_pnl = pos["market_value"] - (pos["quantity"] * pos["avg_cost"])
    assert pos["unrealized_pnl"] == pytest.approx(expected_pnl, abs=1.0)
    assert pos["unrealized_pnl"] > 0  # Should be positive (profit)


@pytest.mark.asyncio
async def test_unrealized_pnl_negative_on_price_decline(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that unrealized P&L becomes negative when price declines."""
    # Buy 10 shares at 100
    await dryrun_broker.place_order("STOCK", "BUY", 10.0, limit_price=100.0)

    # Update price to 80 (price decreased)
    dryrun_broker.set_price("STOCK", 80.0)

    positions = await dryrun_broker.get_positions()
    pos = positions["STOCK"]

    # market_value should be 800
    assert pos["market_value"] == pytest.approx(800.0, abs=1.0)

    # unrealized_pnl should be negative
    expected_pnl = pos["market_value"] - (pos["quantity"] * pos["avg_cost"])
    assert pos["unrealized_pnl"] == pytest.approx(expected_pnl, abs=1.0)
    assert pos["unrealized_pnl"] < 0  # Should be negative (loss)


@pytest.mark.asyncio
async def test_sell_more_than_owned_returns_failure(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that selling more than owned returns an error instead of allowing phantom shorts."""
    # Buy 5 shares
    await dryrun_broker.place_order("TSLA", "BUY", 5.0, limit_price=200.0)

    positions = await dryrun_broker.get_positions()
    assert positions["TSLA"]["quantity"] == 5.0

    # Try to sell 10 shares (more than owned)
    result = await dryrun_broker.place_order("TSLA", "SELL", 10.0, limit_price=200.0)

    assert result.success is False
    assert "Insufficient position" in result.error
    assert "trying to sell 10" in result.error
    assert "only have 5" in result.error

    # Position should remain unchanged
    positions = await dryrun_broker.get_positions()
    assert positions["TSLA"]["quantity"] == 5.0
    assert len(positions) == 1


@pytest.mark.asyncio
async def test_sell_nonexistent_position_returns_failure(dryrun_broker: DryRunBrokerAdapter) -> None:
    """Test that selling a position that doesn't exist returns an error."""
    # Try to sell a position that was never bought
    result = await dryrun_broker.place_order("NONEXIST", "SELL", 10.0, limit_price=100.0)

    assert result.success is False
    assert "Insufficient position" in result.error


@pytest.mark.asyncio
async def test_state_persistence_save_and_load() -> None:
    """Test that broker state is persisted to JSON and can be restored."""
    # Clean up any existing state file
    if STATE_FILE.exists():
        STATE_FILE.unlink()

    # Create first broker instance and place orders
    broker1 = DryRunBrokerAdapter()
    initial_balance = broker1._balance

    # Place orders to change state
    await broker1.place_order("AAPL", "BUY", 10.0, limit_price=150.0)
    await broker1.place_order("MSFT", "BUY", 5.0, limit_price=300.0)

    # Get state after orders
    positions_after_orders = await broker1.get_positions()
    balance_after_orders = broker1._balance
    order_count_after = broker1._order_counter

    # Verify state file exists
    assert STATE_FILE.exists()

    # Verify state file contains correct data
    with open(STATE_FILE, "r") as f:
        saved_state = json.load(f)
    assert saved_state["balance"] == balance_after_orders
    assert len(saved_state["positions"]) == 2
    assert saved_state["order_counter"] == order_count_after

    # Create new broker instance (should load persisted state)
    broker2 = DryRunBrokerAdapter()

    # Verify state was restored
    assert broker2._balance == balance_after_orders
    assert len(broker2._positions) == 2
    assert broker2._order_counter == order_count_after

    # Verify positions match
    positions_restored = await broker2.get_positions()
    assert positions_restored.keys() == positions_after_orders.keys()
    for symbol in positions_restored.keys():
        assert positions_restored[symbol]["quantity"] == positions_after_orders[symbol]["quantity"]

    # Clean up
    if STATE_FILE.exists():
        STATE_FILE.unlink()
