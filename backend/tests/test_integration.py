from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.engines.signal_engine import SignalEngine, SignalConfig
from app.engines.risk_engine import RiskEngine
from app.engines.execution_engine import ExecutionEngine, OrderResult
from app.engines.llm_brain import TradeAction
from app.core.events import Event, EventBus


class TestFullPipeline:
    """Integration tests for signal → LLM decision → risk → execution pipeline."""

    @pytest.mark.asyncio
    async def test_signal_to_execution_flow(self):
        """Test that a signal triggers LLM decision and execution."""
        risk_engine = RiskEngine()
        exec_engine = ExecutionEngine(risk_engine)

        mock_broker = AsyncMock()
        mock_broker.place_order.return_value = OrderResult(
            success=True, broker_order_id="TEST-001",
            filled_price=150.0, filled_quantity=10.0,
        )
        mock_broker.get_positions.return_value = {}
        mock_broker.get_balance.return_value = {"AvailableFunds": 100000}
        exec_engine.register_broker("test", mock_broker, default=True)

        action = TradeAction(
            strategy="test_strategy",
            symbol="AAPL",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            confidence=0.8,
            reasoning="Test signal detected bullish momentum",
        )

        with patch("app.engines.execution_engine.log_trade_decision", new_callable=AsyncMock, return_value=1), \
             patch("app.engines.execution_engine.log_order", new_callable=AsyncMock, return_value=1), \
             patch("app.engines.execution_engine.update_order_status", new_callable=AsyncMock), \
             patch("app.engines.execution_engine.mark_decision_executed", new_callable=AsyncMock), \
             patch("app.engines.execution_engine.log_journal_entry", new_callable=AsyncMock), \
             patch("app.engines.execution_engine.event_bus.publish", new_callable=AsyncMock):

            result = await exec_engine.execute_trade(action, 150.0)

            assert result is not None
            assert result.success
            assert result.broker_order_id == "TEST-001"
            mock_broker.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_risk_rejection_blocks_execution(self):
        """Test that a risk check failure prevents execution."""
        risk_engine = RiskEngine()
        risk_engine.activate_kill_switch("Test kill switch")
        exec_engine = ExecutionEngine(risk_engine)

        mock_broker = AsyncMock()
        exec_engine.register_broker("test", mock_broker, default=True)

        action = TradeAction(
            strategy="test",
            symbol="AAPL",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            confidence=0.8,
            reasoning="Test",
        )

        with patch("app.engines.execution_engine.log_trade_decision", new_callable=AsyncMock, return_value=1), \
             patch("app.engines.execution_engine.log_journal_entry", new_callable=AsyncMock) as mock_journal, \
             patch("app.engines.execution_engine.event_bus.publish", new_callable=AsyncMock):

            result = await exec_engine.execute_trade(action, 150.0)

            assert result is None
            mock_broker.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_bus_wiring(self):
        """Test that event bus correctly wires signal to handlers."""
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe("signal", handler)
        await bus.publish(Event(type="signal", data={"symbol": "AAPL", "price": 150}))

        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert received[0].data["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_multiple_signals_cooldown(self):
        """Test signal cooldown prevents rapid-fire signals."""
        engine = SignalEngine()
        engine.signal_config = SignalConfig(rsi_period=14, rsi_oversold=30.0, rsi_overbought=70.0)

        engine._last_signal_time[f"AAPL:rsi_oversold"] = 0
        assert engine._should_emit("AAPL", "rsi_oversold") is True

        engine._last_signal_time[f"AAPL:rsi_oversold"] = float('inf')
        assert engine._should_emit("AAPL", "rsi_oversold") is False
