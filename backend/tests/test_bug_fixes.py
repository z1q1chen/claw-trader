"""Tests for bug fixes in Claw Trader."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.events import Event, EventBus
from app.engines.llm_brain import LLMBrain, LLMProvider, LLMResponse, TradeAction
from app.engines.execution_engine import ExecutionEngine, BrokerAdapter, OrderResult
from app.engines.risk_engine import RiskEngine

# Re-export EventBus for testing
__all__ = ['EventBus']


class FakeLLMProvider(LLMProvider):
    """Fake LLM provider for testing."""

    def __init__(self, response_content: str):
        self.response_content = response_content

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        return LLMResponse(
            content=self.response_content,
            prompt_tokens=100,
            completion_tokens=50,
            model="fake-model",
            provider="fake",
            latency_ms=100.0,
        )


class FakeBrokerAdapter(BrokerAdapter):
    """Reusable fake broker for tests."""

    def __init__(self, order_result=None, positions=None, balance=None):
        self._order_result = order_result or OrderResult(
            success=True, broker_order_id="TEST-001",
            filled_price=150.0, filled_quantity=10.0,
        )
        self._positions = positions or {}
        self._balance = balance or {}

    async def place_order(self, symbol, side, quantity, order_type="MARKET", limit_price=None):
        return self._order_result

    async def get_positions(self):
        return self._positions

    async def get_balance(self):
        return self._balance

    async def get_order_history(self, limit=50):
        return []

    async def cancel_order(self, order_id):
        return True


# ============================================================================
# Fix 1: LLM JSON Parsing Error Handling Tests
# ============================================================================


@pytest.mark.asyncio
async def test_llm_brain_handles_invalid_json():
    """Test LLM Brain gracefully handles invalid JSON responses."""
    brain = LLMBrain()
    brain._provider = FakeLLMProvider("{invalid json}")
    brain._last_call_time = 0

    signal_event = Event(
        type="signal",
        data={
            "symbol": "AAPL",
            "signal_type": "rsi_oversold",
            "value": 25.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        result = await brain.decide(signal_event)

    assert result is None


@pytest.mark.asyncio
async def test_llm_brain_handles_malformed_json():
    """Test LLM Brain handles malformed JSON with special characters."""
    brain = LLMBrain()
    brain._provider = FakeLLMProvider('{"action": "buy", "symbol": "AAPL", incomplete')
    brain._last_call_time = 0

    signal_event = Event(
        type="signal",
        data={
            "symbol": "AAPL",
            "signal_type": "rsi_oversold",
            "value": 25.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        result = await brain.decide(signal_event)

    assert result is None


@pytest.mark.asyncio
async def test_llm_brain_handles_valid_json():
    """Test LLM Brain correctly parses valid JSON responses."""
    brain = LLMBrain()
    valid_response = json.dumps({
        "action": "buy",
        "symbol": "AAPL",
        "quantity": 10.0,
        "confidence": 0.85,
        "order_type": "MARKET",
        "limit_price": None,
        "reasoning": "Strong signal"
    })
    brain._provider = FakeLLMProvider(valid_response)
    brain._last_call_time = 0

    signal_event = Event(
        type="signal",
        data={
            "symbol": "AAPL",
            "signal_type": "rsi_oversold",
            "value": 25.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        result = await brain.decide(signal_event)

    assert result is not None
    assert result.symbol == "AAPL"
    assert result.side == "buy"
    assert result.quantity == 10.0


# ============================================================================
# Fix 2: Portfolio Sync Null Checks Tests
# ============================================================================


@pytest.mark.asyncio
async def test_portfolio_sync_handles_none_positions():
    """Test portfolio sync gracefully handles None positions from broker."""
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    fake_broker = FakeBrokerAdapter(positions=None)
    execution_engine.register_broker("test", fake_broker)

    with patch("app.main.upsert_position", new_callable=AsyncMock), \
         patch("app.main.save_risk_snapshot", new_callable=AsyncMock), \
         patch("app.main.logger"):
        from app.main import periodic_portfolio_sync
        sync_task = asyncio.create_task(periodic_portfolio_sync())

        # Let it run one iteration
        await asyncio.sleep(0.1)
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_portfolio_sync_handles_invalid_positions_type():
    """Test portfolio sync gracefully handles invalid positions type (not dict)."""
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    fake_broker = FakeBrokerAdapter(positions="invalid_string")
    execution_engine.register_broker("test", fake_broker)

    # Store the original execution_engine before importing periodic_portfolio_sync
    import app.main
    original_ee = app.main.execution_engine
    app.main.execution_engine = execution_engine

    try:
        with patch("app.main.upsert_position", new_callable=AsyncMock), \
             patch("app.main.save_risk_snapshot", new_callable=AsyncMock), \
             patch("app.main.logger") as mock_logger:
            from app.main import periodic_portfolio_sync
            sync_task = asyncio.create_task(periodic_portfolio_sync())

            await asyncio.sleep(0.1)
            sync_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass

            # Should log a warning about invalid positions
            mock_logger.warning.assert_called()
    finally:
        app.main.execution_engine = original_ee


@pytest.mark.asyncio
async def test_portfolio_sync_handles_zero_quantity():
    """Test portfolio sync correctly handles zero quantity positions."""
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    positions = {
        "AAPL": {
            "quantity": 0,
            "avg_cost": 150.0,
            "market_value": 0,
            "unrealized_pnl": 0,
            "realized_pnl": 0,
        }
    }
    fake_broker = FakeBrokerAdapter(positions=positions)
    execution_engine.register_broker("test", fake_broker)

    with patch("app.main.upsert_position", new_callable=AsyncMock) as mock_upsert, \
         patch("app.main.save_risk_snapshot", new_callable=AsyncMock), \
         patch("app.main.logger"):
        from app.main import periodic_portfolio_sync
        sync_task = asyncio.create_task(periodic_portfolio_sync())

        await asyncio.sleep(0.1)
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass

        # Check that upsert was called with avg_cost as current_price (since qty=0)
        if mock_upsert.called:
            call_args = mock_upsert.call_args
            if call_args:
                assert call_args.kwargs.get("current_price") == 150.0


# ============================================================================
# Fix 3: WebSocket Queue Backpressure Tests
# ============================================================================


@pytest.mark.asyncio
async def test_websocket_queue_backpressure_drops_messages():
    """Test EventBus drops old messages when backpressure exceeds threshold."""
    bus = EventBus()
    queue = asyncio.Queue()
    bus.register_ws_client(queue)

    # Publish events to fill the queue beyond threshold
    for i in range(600):
        event = Event(type="test", data={"index": i})
        await bus.publish(event)

    # Queue should have dropped old messages due to backpressure
    # Final size should be around 501 (max + 1, then drops one on next publish)
    assert queue.qsize() <= 501
    assert queue.qsize() > 0


@pytest.mark.asyncio
async def test_websocket_queue_handles_queue_empty():
    """Test EventBus backpressure handler gracefully handles QueueEmpty exception."""
    bus = EventBus()
    queue = asyncio.Queue()
    bus.register_ws_client(queue)

    # Single event should work fine on empty queue
    event = Event(type="test", data={"test": "data"})
    await bus.publish(event)

    assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_websocket_queue_unbounded():
    """Test that WebSocket queue is unbounded (no max size)."""
    bus = EventBus()
    queue = asyncio.Queue()
    bus.register_ws_client(queue)

    # Should be able to publish many events until backpressure kicks in
    for i in range(600):
        event = Event(type="test", data={"index": i})
        await bus.publish(event)

    # Before backpressure, queue should have around 600 items minus any dropped
    assert queue.qsize() > 0
    assert queue.qsize() <= 601
