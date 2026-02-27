from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.brokers.ibkr import IBKRAdapter
from app.engines.execution_engine import OrderResult


@pytest.mark.asyncio
async def test_ibkr_not_connected_place_order():
    adapter = IBKRAdapter()
    result = await adapter.place_order("AAPL", "BUY", 10)
    assert not result.success
    assert "Not connected" in result.error


@pytest.mark.asyncio
async def test_ibkr_not_connected_positions():
    adapter = IBKRAdapter()
    positions = await adapter.get_positions()
    assert positions == {}


@pytest.mark.asyncio
async def test_ibkr_not_connected_balance():
    adapter = IBKRAdapter()
    balance = await adapter.get_balance()
    assert balance == {}


@pytest.mark.asyncio
async def test_ibkr_not_connected_cancel():
    adapter = IBKRAdapter()
    result = await adapter.cancel_order("123")
    assert result is False


@pytest.mark.asyncio
async def test_ibkr_not_connected_order_history():
    adapter = IBKRAdapter()
    orders = await adapter.get_order_history()
    assert orders == []


def test_ibkr_adapter_initialization():
    adapter = IBKRAdapter(host="localhost", port=7496, client_id=2)
    assert adapter._host == "localhost"
    assert adapter._port == 7496
    assert adapter._client_id == 2
    assert adapter._connected is False
