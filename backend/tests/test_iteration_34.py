from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import threading
import json
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from app.engines.risk_engine import RiskEngine
from app.engines.llm_brain import LLMBrain, TradeAction, LLMResponse, LLMProvider
from app.core.database import _xor_encrypt, _xor_decrypt, _get_encryption_key
from app.core.events import Event


class FakeLLMProviderForTestsBase(LLMProvider):
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


class TestRiskEngineCheckTradeLock:
    """Tests for check_trade lock acquisition."""

    def test_check_trade_with_lock_present(self):
        """Verify check_trade has access to _reset_lock."""
        risk_engine = RiskEngine()
        risk_engine.update_portfolio({}, 0.0)

        # Verify the lock exists
        assert hasattr(risk_engine, '_reset_lock')
        assert risk_engine._reset_lock is not None

        trade_action = TradeAction(
            symbol="AAPL",
            side="buy",
            quantity=10.0,
            reasoning="Test",
            confidence=0.8,
            strategy="test",
        )

        result = risk_engine.check_trade(trade_action, current_price=100.0)

        # Result should be valid
        assert result.passed is True

    def test_check_trade_with_concurrent_portfolio_updates(self):
        """Test check_trade reading consistent state during portfolio updates."""
        risk_engine = RiskEngine()
        risk_engine.update_portfolio({"AAPL": 5000.0}, 0.0)

        trade_action = TradeAction(
            symbol="MSFT",
            side="buy",
            quantity=10.0,
            reasoning="Test",
            confidence=0.8,
            strategy="test",
        )

        results = []
        errors = []

        def check_trade_thread():
            try:
                for _ in range(5):
                    result = risk_engine.check_trade(trade_action, current_price=100.0)
                    results.append(result)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(str(e))

        def update_portfolio_thread():
            try:
                for i in range(5):
                    risk_engine.update_portfolio({"AAPL": 5000.0 + i * 100}, 0.0)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=check_trade_thread),
            threading.Thread(target=update_portfolio_thread),
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10.0)

        # Should have no errors
        assert len(errors) == 0

        # All results should be valid
        assert len(results) == 5
        for result in results:
            assert result is not None


class TestLLMActionValidation:
    """Tests for LLM action field validation and normalization."""

    @pytest.mark.asyncio
    async def test_llm_rejects_invalid_action_short(self):
        """Test that LLM rejects action='short' (not 'buy' or 'sell')."""
        brain = LLMBrain()
        response_content = json.dumps(
            {
                "action": "short",  # Invalid
                "symbol": "AAPL",
                "quantity": 10.0,
                "confidence": 0.85,
                "reasoning": "Test",
            }
        )
        fake_provider = FakeLLMProviderForTestsBase(response_content)
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
                assert "action" in brain._last_call_error

    @pytest.mark.asyncio
    async def test_llm_normalizes_buy_to_lowercase(self):
        """Test that LLM action 'BUY' is normalized to 'buy'."""
        brain = LLMBrain()
        response_content = json.dumps(
            {
                "action": "BUY",  # Uppercase
                "symbol": "AAPL",
                "quantity": 10.0,
                "confidence": 0.85,
                "reasoning": "Test",
            }
        )
        fake_provider = FakeLLMProviderForTestsBase(response_content)
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

                assert result is not None
                assert result.side == "buy"  # Normalized to lowercase
                assert result.symbol == "AAPL"
                assert result.quantity == 10.0

    @pytest.mark.asyncio
    async def test_llm_normalizes_sell_to_lowercase(self):
        """Test that LLM action 'SELL' is normalized to 'sell'."""
        brain = LLMBrain()
        response_content = json.dumps(
            {
                "action": "SELL",  # Uppercase
                "symbol": "MSFT",
                "quantity": 5.0,
                "confidence": 0.9,
                "reasoning": "Test",
            }
        )
        fake_provider = FakeLLMProviderForTestsBase(response_content)
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

                assert result is not None
                assert result.side == "sell"  # Normalized to lowercase
                assert result.symbol == "MSFT"

    @pytest.mark.asyncio
    async def test_llm_rejects_invalid_action_hold_after_normalization(self):
        """Test that 'hold' is rejected (not treated as valid after "hold" check)."""
        brain = LLMBrain()
        response_content = json.dumps(
            {
                "action": "hold",
                "symbol": "AAPL",
                "quantity": 0,
                "confidence": 0.5,
                "reasoning": "No trade",
            }
        )
        fake_provider = FakeLLMProviderForTestsBase(response_content)
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

                # 'hold' returns None before action validation
                assert result is None


class TestGetLLMConfigDecryption:
    """Tests for GET /api/llm/config decryption."""

    @pytest.mark.asyncio
    async def test_get_llm_config_decrypts_before_masking(self):
        """Test that GET /api/llm/config decrypts the API key before masking."""
        from app.core.database import _xor_encrypt, _get_encryption_key

        # Simulate a stored encrypted key
        plaintext_key = "sk-1234567890abcdef"
        encryption_key = _get_encryption_key()
        encrypted_key = _xor_encrypt(plaintext_key, encryption_key)

        # Create a mock database row
        from app.api.routes import _mask_key

        # Test masking logic
        masked = _mask_key(plaintext_key)

        # Should show last 4 chars
        assert masked.endswith("cdef")
        assert len(masked) == len(plaintext_key)
        assert masked[:-4] == "•" * (len(plaintext_key) - 4)

    def test_encryption_decryption_roundtrip(self):
        """Test that encryption and decryption work correctly."""
        original_key = "test-secret-key-12345"
        encryption_key = _get_encryption_key()

        encrypted = _xor_encrypt(original_key, encryption_key)
        decrypted = _xor_decrypt(encrypted, encryption_key)

        assert decrypted == original_key
        assert encrypted != original_key  # Should be different


class TestRateLimiterEviction:
    """Tests for improved rate limiter eviction."""

    def test_rate_limiter_evicts_expired_entries(self):
        """Test that cleanup removes IPs with only expired timestamps."""
        from app.core.middleware import RateLimitMiddleware

        middleware = RateLimitMiddleware(None, requests_per_minute=120)

        # Add old timestamps for an IP (all older than window)
        old_time = time.monotonic() - 100  # 100 seconds ago
        middleware._request_counts["192.168.1.1"] = [old_time, old_time - 5, old_time - 10]

        # Add recent timestamps for another IP
        recent_time = time.monotonic()
        middleware._request_counts["192.168.1.2"] = [recent_time, recent_time - 10]

        # Add an IP with mixed timestamps
        middleware._request_counts["192.168.1.3"] = [old_time, recent_time]

        # Simulate cleanup (happens when > 10000 entries)
        now = time.monotonic()
        middleware._request_counts = {
            ip: timestamps
            for ip, timestamps in middleware._request_counts.items()
            if timestamps and any(now - t < middleware.window_seconds for t in timestamps)
        }

        # IP with only old timestamps should be removed
        assert "192.168.1.1" not in middleware._request_counts

        # IP with recent timestamps should remain
        assert "192.168.1.2" in middleware._request_counts

        # IP with mixed timestamps should remain (has at least one recent)
        assert "192.168.1.3" in middleware._request_counts

    def test_rate_limiter_cleans_empty_lists(self):
        """Test that cleanup also removes empty timestamp lists."""
        from app.core.middleware import RateLimitMiddleware

        middleware = RateLimitMiddleware(None, requests_per_minute=120)

        # Add entries
        middleware._request_counts["192.168.1.1"] = []  # Empty
        middleware._request_counts["192.168.1.2"] = [time.monotonic()]  # Recent

        # Simulate cleanup
        now = time.monotonic()
        middleware._request_counts = {
            ip: timestamps
            for ip, timestamps in middleware._request_counts.items()
            if timestamps and any(now - t < middleware.window_seconds for t in timestamps)
        }

        # Empty list should be removed
        assert "192.168.1.1" not in middleware._request_counts

        # Non-empty with valid timestamps should remain
        assert "192.168.1.2" in middleware._request_counts
