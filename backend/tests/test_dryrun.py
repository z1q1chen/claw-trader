from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.brokers.dryrun import DryRunBrokerAdapter


@pytest.fixture
def dryrun_broker() -> DryRunBrokerAdapter:
    return DryRunBrokerAdapter()


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
