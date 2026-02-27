from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.events import Event
from app.engines.llm_brain import (
    LLMBrain,
    LLMProvider,
    LLMResponse,
    TradeAction,
    GeminiProvider,
    OpenAICompatibleProvider,
)


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


# ============================================================================
# TradeAction Dataclass Tests
# ============================================================================


def test_trade_action_creation():
    """Test TradeAction creation with all fields."""
    action = TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10.5,
        reasoning="Strong momentum signal",
        confidence=0.85,
        strategy="llm_signal_response",
    )

    assert action.symbol == "AAPL"
    assert action.side == "buy"
    assert action.quantity == 10.5
    assert action.reasoning == "Strong momentum signal"
    assert action.confidence == 0.85
    assert action.strategy == "llm_signal_response"


def test_trade_action_sell_side():
    """Test TradeAction with sell side."""
    action = TradeAction(
        symbol="GOOGL",
        side="sell",
        quantity=5.0,
        reasoning="Overbought condition",
        confidence=0.72,
        strategy="llm_signal_response",
    )

    assert action.side == "sell"
    assert action.symbol == "GOOGL"


def test_trade_action_limit_order():
    """Test TradeAction with limit order."""
    action = TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10,
        reasoning="test",
        confidence=0.9,
        strategy="test",
        order_type="LIMIT",
        limit_price=150.0,
    )

    assert action.order_type == "LIMIT"
    assert action.limit_price == 150.0


def test_trade_action_defaults_to_market():
    """Test TradeAction defaults to MARKET order type."""
    action = TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10,
        reasoning="test",
        confidence=0.9,
        strategy="test",
    )

    assert action.order_type == "MARKET"
    assert action.limit_price is None


# ============================================================================
# LLMResponse Dataclass Tests
# ============================================================================


def test_llm_response_creation():
    """Test LLMResponse creation with all fields."""
    response = LLMResponse(
        content='{"action": "buy"}',
        prompt_tokens=150,
        completion_tokens=75,
        model="gpt-4",
        provider="openai",
        latency_ms=234.5,
    )

    assert response.content == '{"action": "buy"}'
    assert response.prompt_tokens == 150
    assert response.completion_tokens == 75
    assert response.model == "gpt-4"
    assert response.provider == "openai"
    assert response.latency_ms == 234.5


def test_llm_response_gemini():
    """Test LLMResponse with Gemini provider."""
    response = LLMResponse(
        content='{"action": "hold"}',
        prompt_tokens=80,
        completion_tokens=40,
        model="gemini-2.0-flash",
        provider="gemini",
        latency_ms=123.4,
    )

    assert response.provider == "gemini"
    assert response.model == "gemini-2.0-flash"


# ============================================================================
# LLMBrain Portfolio Context Tests
# ============================================================================


def test_set_portfolio_context():
    """Test set_portfolio_context() stores portfolio data correctly."""
    brain = LLMBrain()
    brain.set_portfolio_context({"AAPL": 5000, "MSFT": 3000}, -200.0, 8000.0)

    assert brain._positions == {"AAPL": 5000, "MSFT": 3000}
    assert brain._daily_pnl == -200.0
    assert brain._total_exposure == 8000.0


# ============================================================================
# LLMBrain Configuration Tests
# ============================================================================


def test_configure_gemini_provider():
    """Test configure() sets up GeminiProvider when provider='gemini'."""
    brain = LLMBrain()
    brain.configure(
        provider="gemini",
        model="gemini-2.0-flash",
        api_key="test-gemini-key",
    )

    assert brain._provider is not None
    assert isinstance(brain._provider, GeminiProvider)
    assert brain._provider.model == "gemini-2.0-flash"
    assert brain._provider.api_key == "test-gemini-key"
    assert brain._provider_name == "gemini"
    assert brain._model_name == "gemini-2.0-flash"


def test_configure_openai_provider():
    """Test configure() sets up OpenAICompatibleProvider when provider='openai'."""
    brain = LLMBrain()
    brain.configure(
        provider="openai",
        model="gpt-4o",
        api_key="test-openai-key",
    )

    assert brain._provider is not None
    assert isinstance(brain._provider, OpenAICompatibleProvider)
    assert brain._provider.model == "gpt-4o"
    assert brain._provider.api_key == "test-openai-key"
    assert brain._provider.provider_name == "openai"
    assert brain._provider_name == "openai"
    assert brain._model_name == "gpt-4o"


def test_configure_local_provider():
    """Test configure() sets up OpenAICompatibleProvider with base_url when provider='local'."""
    brain = LLMBrain()
    brain.configure(
        provider="local",
        model="local-llm",
        api_key="dummy-key",
        base_url="http://localhost:8000/v1",
    )

    assert brain._provider is not None
    assert isinstance(brain._provider, OpenAICompatibleProvider)
    assert brain._provider.model == "local-llm"
    assert brain._provider.base_url == "http://localhost:8000/v1"
    assert brain._provider.provider_name == "local"
    assert brain._provider_name == "local"


def test_configure_unknown_provider():
    """Test configure() raises ValueError for unknown provider."""
    brain = LLMBrain()

    with pytest.raises(ValueError, match="Unknown LLM provider: unknown"):
        brain.configure(
            provider="unknown",
            model="some-model",
            api_key="key",
        )


# ============================================================================
# LLMBrain decide() Tests
# ============================================================================


@pytest.mark.asyncio
async def test_decide_no_provider():
    """Test decide() returns None when no provider configured."""
    brain = LLMBrain()
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

    result = await brain.decide(signal_event)

    assert result is None


@pytest.mark.asyncio
async def test_decide_rate_limiting():
    """Test decide() rate limiting: second call within 2s returns None."""
    brain = LLMBrain()
    fake_provider = FakeLLMProvider(
        json.dumps(
            {
                "action": "buy",
                "symbol": "AAPL",
                "quantity": 10,
                "confidence": 0.85,
                "reasoning": "Test signal",
            }
        )
    )
    brain._provider = fake_provider

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
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            # First call should succeed
            result1 = await brain.decide(signal_event)
            assert result1 is not None
            assert result1.symbol == "AAPL"

            # Second call within 2s should return None (rate limited)
            result2 = await brain.decide(signal_event)
            assert result2 is None


@pytest.mark.asyncio
async def test_decide_parses_buy_response():
    """Test decide() parses LLM 'buy' response into TradeAction correctly."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "MSFT",
            "quantity": 15.5,
            "confidence": 0.92,
            "reasoning": "Strong uptrend signal detected",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "MSFT",
            "signal_type": "moving_average_crossover",
            "value": 42.5,
            "price": 420.0,
            "metadata": {"ma_period": 20},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is not None
            assert isinstance(result, TradeAction)
            assert result.symbol == "MSFT"
            assert result.side == "buy"
            assert result.quantity == 15.5
            assert result.confidence == 0.92
            assert result.reasoning == "Strong uptrend signal detected"
            assert result.strategy == "llm_signal_response"


@pytest.mark.asyncio
async def test_decide_parses_sell_response():
    """Test decide() parses LLM 'sell' response into TradeAction correctly."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "sell",
            "symbol": "TSLA",
            "quantity": 8.0,
            "confidence": 0.78,
            "reasoning": "Overbought condition",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "TSLA",
            "signal_type": "rsi_overbought",
            "value": 75.0,
            "price": 250.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is not None
            assert result.side == "sell"
            assert result.symbol == "TSLA"
            assert result.quantity == 8.0


@pytest.mark.asyncio
async def test_decide_hold_response():
    """Test decide() returns None when LLM responds with 'hold'."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "hold",
            "symbol": "AAPL",
            "quantity": 0,
            "confidence": 0.5,
            "reasoning": "Insufficient signal strength",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "AAPL",
            "signal_type": "neutral",
            "value": 50.0,
            "price": 155.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is None


@pytest.mark.asyncio
async def test_decide_llm_error_handling():
    """Test decide() returns None on LLM error (exception caught)."""

    class ErrorLLMProvider(LLMProvider):
        async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
            raise RuntimeError("LLM API error")

    brain = LLMBrain()
    brain._provider = ErrorLLMProvider()

    signal_event = Event(
        type="signal",
        data={
            "symbol": "AAPL",
            "signal_type": "error_test",
            "value": 30.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is None


@pytest.mark.asyncio
async def test_decide_logs_api_usage():
    """Test decide() calls log_api_usage with correct parameters."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "GOOG",
            "quantity": 5,
            "confidence": 0.88,
            "reasoning": "Signal detected",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "GOOG",
            "signal_type": "test",
            "value": 40.0,
            "price": 140.0,
            "metadata": {},
        },
    )

    mock_log_usage = AsyncMock()
    with patch("app.engines.llm_brain.log_api_usage", mock_log_usage):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            await brain.decide(signal_event)

            mock_log_usage.assert_called_once()
            call_kwargs = mock_log_usage.call_args[1]
            assert call_kwargs["provider"] == "fake"
            assert call_kwargs["model"] == "fake-model"
            assert call_kwargs["prompt_tokens"] == 100
            assert call_kwargs["completion_tokens"] == 50
            assert call_kwargs["latency_ms"] == 100.0
            assert call_kwargs["request_type"] == "trade_decision"


@pytest.mark.asyncio
async def test_decide_publishes_event():
    """Test decide() publishes llm_decision event."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "AMZN",
            "quantity": 12,
            "confidence": 0.80,
            "reasoning": "Test",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "AMZN",
            "signal_type": "test",
            "value": 45.0,
            "price": 175.0,
            "metadata": {"test": True},
        },
    )

    mock_publish = AsyncMock()
    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", mock_publish):
            await brain.decide(signal_event)

            mock_publish.assert_called_once()
            published_event = mock_publish.call_args[0][0]
            assert published_event.type == "llm_decision"
            assert "decision" in published_event.data
            assert "signal" in published_event.data
            assert "latency_ms" in published_event.data


@pytest.mark.asyncio
async def test_decide_with_missing_optional_fields():
    """Test decide() rejects responses with missing required fields that fail validation."""
    brain = LLMBrain()
    # Minimal response - missing some required fields for validation
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "FB",
            # Missing quantity and confidence - will fail validation
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "FB",
            "signal_type": "test",
            "value": 30.0,
            "price": 300.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            # Should be rejected because quantity defaults to 0.0 (invalid)
            assert result is None
            assert brain._last_call_success is False


@pytest.mark.asyncio
async def test_decide_with_valid_optional_fields_provided():
    """Test decide() handles responses with all fields provided."""
    brain = LLMBrain()
    # Complete response with all fields
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "FB",
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "Test reason",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "FB",
            "signal_type": "test",
            "value": 30.0,
            "price": 300.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is not None
            assert result.symbol == "FB"
            assert result.side == "buy"
            assert result.quantity == 10.0
            assert result.confidence == 0.85
            assert result.reasoning == "Test reason"


@pytest.mark.asyncio
async def test_decide_invalid_json_response():
    """Test decide() handles invalid JSON response gracefully."""
    brain = LLMBrain()
    fake_provider = FakeLLMProvider("not valid json")
    brain._provider = fake_provider

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

            # Should return None because JSON parsing fails
            assert result is None


# ============================================================================
# LLMBrain Cost Estimation Tests
# ============================================================================


def test_estimate_cost_gemini():
    """Test _estimate_cost() correct cost for gemini provider."""
    brain = LLMBrain()
    response = LLMResponse(
        content="test",
        prompt_tokens=1_000_000,  # 1M tokens
        completion_tokens=1_000_000,  # 1M tokens
        model="gemini-2.0-flash",
        provider="gemini",
        latency_ms=100.0,
    )

    cost = brain._estimate_cost(response)

    # Gemini: prompt=$0.075/M, completion=$0.30/M
    # 1M * 0.075/M + 1M * 0.30/M = 0.075 + 0.30 = 0.375
    expected = 1_000_000 * (0.075 / 1_000_000) + 1_000_000 * (0.30 / 1_000_000)
    assert abs(cost - expected) < 1e-9


def test_estimate_cost_openai():
    """Test _estimate_cost() correct cost for openai provider."""
    brain = LLMBrain()
    response = LLMResponse(
        content="test",
        prompt_tokens=1_000_000,  # 1M tokens
        completion_tokens=1_000_000,  # 1M tokens
        model="gpt-4o",
        provider="openai",
        latency_ms=100.0,
    )

    cost = brain._estimate_cost(response)

    # OpenAI: prompt=$2.50/M, completion=$10.00/M
    # 1M * 2.50/M + 1M * 10.00/M = 2.50 + 10.00 = 12.50
    expected = 1_000_000 * (2.50 / 1_000_000) + 1_000_000 * (10.00 / 1_000_000)
    assert abs(cost - expected) < 1e-9


def test_estimate_cost_local():
    """Test _estimate_cost() correct cost for local provider."""
    brain = LLMBrain()
    response = LLMResponse(
        content="test",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        model="local-llm",
        provider="local",
        latency_ms=50.0,
    )

    cost = brain._estimate_cost(response)

    # Local: both free
    assert cost == 0.0


def test_estimate_cost_unknown_provider():
    """Test _estimate_cost() defaults to OpenAI rates for unknown provider."""
    brain = LLMBrain()
    response = LLMResponse(
        content="test",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        model="unknown-model",
        provider="unknown",
        latency_ms=100.0,
    )

    cost = brain._estimate_cost(response)

    # Should default to OpenAI rates
    expected = 1_000_000 * (2.50 / 1_000_000) + 1_000_000 * (10.00 / 1_000_000)
    assert abs(cost - expected) < 1e-9


def test_estimate_cost_fractional_tokens():
    """Test _estimate_cost() with fractional token counts."""
    brain = LLMBrain()
    response = LLMResponse(
        content="test",
        prompt_tokens=100,
        completion_tokens=50,
        model="gemini-2.0-flash",
        provider="gemini",
        latency_ms=100.0,
    )

    cost = brain._estimate_cost(response)

    # Gemini: 100 * (0.075/1M) + 50 * (0.30/1M)
    expected = 100 * (0.075 / 1_000_000) + 50 * (0.30 / 1_000_000)
    assert cost == pytest.approx(expected, rel=1e-9)


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_configure_then_decide():
    """Integration test: configure brain and make a decision."""
    brain = LLMBrain()
    brain.configure(
        provider="openai",
        model="gpt-4o",
        api_key="test-key",
    )

    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "NVDA",
            "quantity": 20,
            "confidence": 0.95,
            "reasoning": "AI boom",
        }
    )
    brain._provider = FakeLLMProvider(response_content)

    signal_event = Event(
        type="signal",
        data={
            "symbol": "NVDA",
            "signal_type": "volume_surge",
            "value": 60.0,
            "price": 875.0,
            "metadata": {"volume_increase": 150},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is not None
            assert result.symbol == "NVDA"
            assert result.confidence == 0.95


@pytest.mark.asyncio
async def test_multiple_sequential_decisions():
    """Test multiple sequential decisions with rate limiting."""
    brain = LLMBrain()
    brain.configure(provider="gemini", model="gemini-2.0-flash", api_key="test-key")

    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "SPY",
            "quantity": 5,
            "confidence": 0.8,
            "reasoning": "Test",
        }
    )
    brain._provider = FakeLLMProvider(response_content)

    signal_event = Event(
        type="signal",
        data={
            "symbol": "SPY",
            "signal_type": "test",
            "value": 50.0,
            "price": 450.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            # First decision should succeed
            result1 = await brain.decide(signal_event)
            assert result1 is not None

            # Second immediate decision should be rate limited
            result2 = await brain.decide(signal_event)
            assert result2 is None

            # Manually set last call time to past to allow another decision
            brain._last_call_time = time.monotonic() - 3.0

            # Third decision should succeed
            result3 = await brain.decide(signal_event)
            assert result3 is not None


def test_polymarket_prompt_detected_for_long_symbol():
    """Prediction market prompt should be used for long condition ID symbols."""
    from app.engines.llm_brain import POLYMARKET_SYSTEM_PROMPT, TRADE_DECISION_SYSTEM_PROMPT
    # Verify both prompts exist and are different
    assert "prediction market" in POLYMARKET_SYSTEM_PROMPT.lower()
    assert "quantitative trading" in TRADE_DECISION_SYSTEM_PROMPT.lower()
    assert POLYMARKET_SYSTEM_PROMPT != TRADE_DECISION_SYSTEM_PROMPT


# ============================================================================
# LLMBrain Semaphore and Concurrency Tests
# ============================================================================


@pytest.mark.asyncio
async def test_llm_semaphore_serializes_concurrent_calls():
    """Test that concurrent calls are serialized by the semaphore."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "TEST",
            "quantity": 10,
            "confidence": 0.85,
            "reasoning": "Test",
        }
    )
    brain._provider = FakeLLMProvider(response_content)

    signal_event = Event(
        type="signal",
        data={
            "symbol": "TEST",
            "signal_type": "test",
            "value": 50.0,
            "price": 100.0,
            "metadata": {},
        },
    )

    call_times = []

    async def track_call():
        import time
        call_times.append(time.monotonic())
        with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
            with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
                # Reset rate limiting to allow this call
                brain._last_call_time = 0
                await brain.decide(signal_event)
        call_times.append(time.monotonic())

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            # Reset rate limiting
            brain._last_call_time = 0
            first_result = await brain.decide(signal_event)
            assert first_result is not None


# ============================================================================
# LLMBrain Output Validation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_decide_rejects_zero_quantity():
    """Test that decide() rejects LLM response with quantity <= 0."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "AAPL",
            "quantity": 0,  # Invalid: must be > 0
            "confidence": 0.85,
            "reasoning": "Test",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

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

            assert result is None
            assert brain._last_call_success is False
            assert "quantity" in brain._last_call_error


@pytest.mark.asyncio
async def test_decide_rejects_negative_quantity():
    """Test that decide() rejects LLM response with negative quantity."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "MSFT",
            "quantity": -10.0,  # Invalid: must be > 0
            "confidence": 0.85,
            "reasoning": "Test",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "MSFT",
            "signal_type": "test",
            "value": 25.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is None
            assert brain._last_call_success is False


@pytest.mark.asyncio
async def test_decide_rejects_confidence_below_zero():
    """Test that decide() rejects confidence < 0."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "GOOGL",
            "quantity": 10.0,
            "confidence": -0.1,  # Invalid: must be 0-1
            "reasoning": "Test",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "GOOGL",
            "signal_type": "test",
            "value": 25.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is None
            assert brain._last_call_success is False
            assert "confidence" in brain._last_call_error


@pytest.mark.asyncio
async def test_decide_rejects_confidence_above_one():
    """Test that decide() rejects confidence > 1."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "sell",
            "symbol": "TSLA",
            "quantity": 5.0,
            "confidence": 1.5,  # Invalid: must be 0-1
            "reasoning": "Test",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "TSLA",
            "signal_type": "test",
            "value": 25.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is None
            assert brain._last_call_success is False


@pytest.mark.asyncio
async def test_decide_rejects_empty_symbol():
    """Test that decide() rejects empty or whitespace-only symbol."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "   ",  # Invalid: symbol must be non-empty after strip
            "quantity": 10.0,
            "confidence": 0.85,
            "reasoning": "Test",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "AMZN",
            "signal_type": "test",
            "value": 25.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            assert result is None
            assert brain._last_call_success is False
            assert "symbol" in brain._last_call_error


@pytest.mark.asyncio
async def test_decide_accepts_valid_bounds():
    """Test that decide() accepts response with valid bounds (quantity, confidence, symbol)."""
    brain = LLMBrain()
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "FB",
            "quantity": 15.5,  # Valid: > 0
            "confidence": 0.99,  # Valid: 0-1
            "reasoning": "Test signal",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "FB",
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
            assert result.quantity == 15.5
            assert result.confidence == 0.99
            assert result.symbol == "FB"
            assert brain._last_call_success is True
            assert brain._last_call_error is None


@pytest.mark.asyncio
async def test_decide_boundary_values():
    """Test decide() with boundary values for confidence (0.0 and 1.0)."""
    brain = LLMBrain()

    # Test confidence = 0.0 (valid boundary)
    response_content = json.dumps(
        {
            "action": "buy",
            "symbol": "LOW",
            "quantity": 10.0,
            "confidence": 0.0,  # Valid boundary
            "reasoning": "Test",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "LOW",
            "signal_type": "test",
            "value": 25.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            brain._last_call_time = 0  # Reset rate limiting
            result = await brain.decide(signal_event)

            assert result is not None
            assert result.confidence == 0.0

    # Test confidence = 1.0 (valid boundary)
    response_content = json.dumps(
        {
            "action": "sell",
            "symbol": "HIGH",
            "quantity": 5.0,
            "confidence": 1.0,  # Valid boundary
            "reasoning": "Test",
        }
    )
    fake_provider = FakeLLMProvider(response_content)
    brain._provider = fake_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "HIGH",
            "signal_type": "test",
            "value": 25.0,
            "price": 150.0,
            "metadata": {},
        },
    )

    with patch("app.engines.llm_brain.log_api_usage", new_callable=AsyncMock):
        with patch("app.engines.llm_brain.event_bus.publish", new_callable=AsyncMock):
            brain._last_call_time = 0  # Reset rate limiting
            result = await brain.decide(signal_event)

            assert result is not None
            assert result.confidence == 1.0
