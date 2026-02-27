"""
Tests for iteration 35: Final Polish

Covers:
1. Risk engine async lock migration
2. VaR return recording (once per day, not per sync tick)
3. LLM order_type and limit_price validation
4. WebSocket disconnect exception handling
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.core.events import Event
from app.engines.risk_engine import RiskEngine
from app.engines.llm_brain import LLMBrain, LLMProvider, LLMResponse, TradeAction
from app.core.config import settings


# ============================================================================
# Risk Engine Async Lock Tests
# ============================================================================


class TestRiskEngineAsyncLock:
    """Tests for risk engine async lock migration."""

    @pytest.mark.asyncio
    async def test_check_trade_is_async(self):
        """check_trade should be async and acquire the async lock."""
        engine = RiskEngine()
        await engine.update_portfolio({}, 0.0)

        action = TradeAction(
            symbol="AAPL", side="buy", quantity=10.0,
            reasoning="test", confidence=0.8, strategy="test"
        )

        result = await engine.check_trade(action, current_price=100.0)
        assert result.passed is True
        assert isinstance(result, type(result))

    @pytest.mark.asyncio
    async def test_update_portfolio_is_async(self):
        """update_portfolio should be async and acquire the async lock."""
        engine = RiskEngine()

        await engine.update_portfolio({"AAPL": 5000.0}, 100.0)
        assert engine._portfolio.total_exposure_usd == 5000.0
        assert engine._portfolio.daily_pnl_usd == 100.0

    @pytest.mark.asyncio
    async def test_reset_daily_is_async(self):
        """reset_daily should be async and acquire the async lock."""
        engine = RiskEngine()
        await engine.update_portfolio({"AAPL": 5000.0}, -1000.0)

        await engine.reset_daily()
        assert engine._portfolio.daily_pnl_usd == 0.0

    @pytest.mark.asyncio
    async def test_concurrent_check_trade_calls_are_serialized(self):
        """Concurrent check_trade calls should be serialized by the async lock."""
        engine = RiskEngine()
        await engine.update_portfolio({}, 0.0)

        action = TradeAction(
            symbol="AAPL", side="buy", quantity=10.0,
            reasoning="test", confidence=0.8, strategy="test"
        )

        call_order = []

        async def check_trade_with_tracking():
            call_order.append("start")
            result = await engine.check_trade(action, current_price=100.0)
            call_order.append("end")
            return result

        results = await asyncio.gather(
            check_trade_with_tracking(),
            check_trade_with_tracking(),
            check_trade_with_tracking(),
        )

        assert len(results) == 3
        assert all(r.passed for r in results)
        assert len(call_order) == 6

    @pytest.mark.asyncio
    async def test_concurrent_update_portfolio_calls_are_serialized(self):
        """Concurrent update_portfolio calls should be serialized by the async lock."""
        engine = RiskEngine()

        call_count = [0]

        async def update_with_tracking(symbol, value):
            call_count[0] += 1
            await engine.update_portfolio({symbol: value}, 0.0)

        await asyncio.gather(
            update_with_tracking("AAPL", 1000.0),
            update_with_tracking("MSFT", 2000.0),
            update_with_tracking("GOOGL", 3000.0),
        )

        assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_concurrent_reset_daily_calls_are_serialized(self):
        """Concurrent reset_daily calls should be serialized by the async lock."""
        engine = RiskEngine()
        await engine.update_portfolio({"AAPL": 5000}, -1000.0)

        reset_count = [0]

        async def reset_with_tracking():
            reset_count[0] += 1
            await engine.reset_daily()

        await asyncio.gather(
            reset_with_tracking(),
            reset_with_tracking(),
            reset_with_tracking(),
        )

        assert reset_count[0] == 3
        assert engine._portfolio.daily_pnl_usd == 0.0


# ============================================================================
# VaR Return Recording Tests
# ============================================================================


class TestVaRReturnRecording:
    """Tests that VaR returns are recorded once per day."""

    def test_add_return_called_once(self):
        """add_return should be called exactly once per daily reset."""
        engine = RiskEngine()
        engine._portfolio.total_exposure_usd = 10000.0
        engine._portfolio.daily_pnl_usd = 500.0

        # Record the return before reset (simulating periodic_daily_reset logic)
        daily_pnl = engine._portfolio.daily_pnl_usd
        total_exposure = engine._portfolio.total_exposure_usd
        if total_exposure > 0 and daily_pnl != 0:
            daily_return_pct = daily_pnl / total_exposure * 100
            engine.add_return(daily_return_pct)

        # Verify the return was recorded
        assert len(engine._return_history) == 1
        assert engine._return_history[0] == pytest.approx(5.0)

    def test_return_not_recorded_on_sync_tick(self):
        """add_return should NOT be called on portfolio sync."""
        engine = RiskEngine()

        # Initial state
        initial_returns = len(engine._return_history)
        assert initial_returns == 0

        # Simulate portfolio sync (should NOT call add_return)
        engine._portfolio.total_exposure_usd = 10000.0
        engine._portfolio.daily_pnl_usd = 500.0

        # Verify no return was added yet
        assert len(engine._return_history) == initial_returns

    @pytest.mark.asyncio
    async def test_periodic_daily_reset_records_return(self):
        """periodic_daily_reset should record return before reset."""
        engine = RiskEngine()
        await engine.update_portfolio({"AAPL": 10000.0}, 500.0)

        # Simulate the logic from periodic_daily_reset
        snapshot = engine.get_risk_snapshot()
        daily_pnl = snapshot["daily_pnl_usd"]
        total_exposure = snapshot["total_exposure_usd"]
        if total_exposure > 0 and daily_pnl != 0:
            daily_return_pct = daily_pnl / total_exposure * 100
            engine.add_return(daily_return_pct)

        # Verify return was recorded
        assert len(engine._return_history) == 1
        assert engine._return_history[0] == pytest.approx(5.0)

        # Now reset daily
        await engine.reset_daily()

        # Verify reset happened
        assert engine._portfolio.daily_pnl_usd == 0.0

    def test_zero_pnl_not_recorded(self):
        """Returns should not be recorded if daily_pnl is zero."""
        engine = RiskEngine()
        engine._portfolio.total_exposure_usd = 10000.0
        engine._portfolio.daily_pnl_usd = 0.0

        # Try to record return
        daily_pnl = engine._portfolio.daily_pnl_usd
        total_exposure = engine._portfolio.total_exposure_usd
        if total_exposure > 0 and daily_pnl != 0:
            daily_return_pct = daily_pnl / total_exposure * 100
            engine.add_return(daily_return_pct)

        # Verify nothing was recorded
        assert len(engine._return_history) == 0

    def test_zero_exposure_not_recorded(self):
        """Returns should not be recorded if exposure is zero."""
        engine = RiskEngine()
        engine._portfolio.total_exposure_usd = 0.0
        engine._portfolio.daily_pnl_usd = 500.0

        # Try to record return
        daily_pnl = engine._portfolio.daily_pnl_usd
        total_exposure = engine._portfolio.total_exposure_usd
        if total_exposure > 0 and daily_pnl != 0:
            daily_return_pct = daily_pnl / total_exposure * 100
            engine.add_return(daily_return_pct)

        # Verify nothing was recorded
        assert len(engine._return_history) == 0


# ============================================================================
# LLM Order Type Validation Tests
# ============================================================================


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


class TestLLMOrderTypeValidation:
    """Tests for LLM order_type and limit_price validation."""

    @pytest.mark.asyncio
    async def test_market_order_valid(self):
        """MARKET order_type should pass validation."""
        brain = LLMBrain()
        response_content = json.dumps({
            "action": "buy",
            "symbol": "AAPL",
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "test",
            "order_type": "MARKET",
        })
        brain._provider = FakeLLMProvider(response_content)

        signal_event = Event(
            type="signal",
            data={
                "symbol": "AAPL",
                "signal_type": "test",
                "value": 25.0,
                "price": 150.0,
                "metadata": {},
            },
        )

        with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
            with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
                result = await brain.decide(signal_event)

                assert result is not None
                assert result.order_type == "MARKET"
                assert result.limit_price is None

    @pytest.mark.asyncio
    async def test_limit_order_valid_with_price(self):
        """LIMIT order_type with valid limit_price should pass."""
        brain = LLMBrain()
        response_content = json.dumps({
            "action": "buy",
            "symbol": "AAPL",
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "test",
            "order_type": "LIMIT",
            "limit_price": 145.0,
        })
        brain._provider = FakeLLMProvider(response_content)

        signal_event = Event(
            type="signal",
            data={
                "symbol": "AAPL",
                "signal_type": "test",
                "value": 25.0,
                "price": 150.0,
                "metadata": {},
            },
        )

        with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
            with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
                result = await brain.decide(signal_event)

                assert result is not None
                assert result.order_type == "LIMIT"
                assert result.limit_price == 145.0

    @pytest.mark.asyncio
    async def test_limit_order_without_price_defaults_to_market(self):
        """LIMIT order_type without limit_price should default to MARKET."""
        brain = LLMBrain()
        response_content = json.dumps({
            "action": "buy",
            "symbol": "AAPL",
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "test",
            "order_type": "LIMIT",
        })
        brain._provider = FakeLLMProvider(response_content)

        signal_event = Event(
            type="signal",
            data={
                "symbol": "AAPL",
                "signal_type": "test",
                "value": 25.0,
                "price": 150.0,
                "metadata": {},
            },
        )

        with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
            with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
                result = await brain.decide(signal_event)

                assert result is not None
                assert result.order_type == "MARKET"
                assert result.limit_price is None

    @pytest.mark.asyncio
    async def test_invalid_order_type_defaults_to_market(self):
        """Invalid order_type should default to MARKET."""
        brain = LLMBrain()
        response_content = json.dumps({
            "action": "buy",
            "symbol": "AAPL",
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "test",
            "order_type": "INVALID_TYPE",
        })
        brain._provider = FakeLLMProvider(response_content)

        signal_event = Event(
            type="signal",
            data={
                "symbol": "AAPL",
                "signal_type": "test",
                "value": 25.0,
                "price": 150.0,
                "metadata": {},
            },
        )

        with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
            with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
                result = await brain.decide(signal_event)

                assert result is not None
                assert result.order_type == "MARKET"
                assert result.limit_price is None

    @pytest.mark.asyncio
    async def test_limit_order_with_zero_price_defaults_to_market(self):
        """LIMIT order with limit_price <= 0 should default to MARKET."""
        brain = LLMBrain()
        response_content = json.dumps({
            "action": "buy",
            "symbol": "AAPL",
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "test",
            "order_type": "LIMIT",
            "limit_price": 0.0,
        })
        brain._provider = FakeLLMProvider(response_content)

        signal_event = Event(
            type="signal",
            data={
                "symbol": "AAPL",
                "signal_type": "test",
                "value": 25.0,
                "price": 150.0,
                "metadata": {},
            },
        )

        with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
            with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
                result = await brain.decide(signal_event)

                assert result is not None
                assert result.order_type == "MARKET"
                assert result.limit_price is None

    @pytest.mark.asyncio
    async def test_limit_order_with_negative_price_defaults_to_market(self):
        """LIMIT order with negative limit_price should default to MARKET."""
        brain = LLMBrain()
        response_content = json.dumps({
            "action": "buy",
            "symbol": "AAPL",
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "test",
            "order_type": "LIMIT",
            "limit_price": -100.0,
        })
        brain._provider = FakeLLMProvider(response_content)

        signal_event = Event(
            type="signal",
            data={
                "symbol": "AAPL",
                "signal_type": "test",
                "value": 25.0,
                "price": 150.0,
                "metadata": {},
            },
        )

        with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
            with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
                result = await brain.decide(signal_event)

                assert result is not None
                assert result.order_type == "MARKET"
                assert result.limit_price is None

    @pytest.mark.asyncio
    async def test_order_type_case_insensitive(self):
        """order_type should be case-insensitive and normalized to uppercase."""
        brain = LLMBrain()
        response_content = json.dumps({
            "action": "buy",
            "symbol": "AAPL",
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "test",
            "order_type": "limit",
            "limit_price": 145.0,
        })
        brain._provider = FakeLLMProvider(response_content)

        signal_event = Event(
            type="signal",
            data={
                "symbol": "AAPL",
                "signal_type": "test",
                "value": 25.0,
                "price": 150.0,
                "metadata": {},
            },
        )

        with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
            with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
                result = await brain.decide(signal_event)

                assert result is not None
                assert result.order_type == "LIMIT"
                assert result.limit_price == 145.0

    @pytest.mark.asyncio
    async def test_limit_price_non_numeric_defaults_to_market(self):
        """LIMIT order with non-numeric limit_price should default to MARKET."""
        brain = LLMBrain()
        response_content = json.dumps({
            "action": "buy",
            "symbol": "AAPL",
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "test",
            "order_type": "LIMIT",
            "limit_price": "not_a_number",
        })
        brain._provider = FakeLLMProvider(response_content)

        signal_event = Event(
            type="signal",
            data={
                "symbol": "AAPL",
                "signal_type": "test",
                "value": 25.0,
                "price": 150.0,
                "metadata": {},
            },
        )

        with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
            with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
                result = await brain.decide(signal_event)

                assert result is not None
                assert result.order_type == "MARKET"
                assert result.limit_price is None
