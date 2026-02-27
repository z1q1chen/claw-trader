"""
Tests for Iteration 31 (Agent 1) fixes:
1. Polymarket place_order to not assume immediate fill
2. ExecutionEngine to handle pending orders
3. Polymarket feed infinite retry fix
4. Signal config and position sizing config persistence
5. DryRun order history pruning
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import json

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.brokers.polymarket import PolymarketAdapter
from app.brokers.dryrun import DryRunBrokerAdapter
from app.engines.execution_engine import ExecutionEngine, OrderResult
from app.engines.llm_brain import TradeAction
from app.engines.risk_engine import RiskEngine
from app.feeds.polymarket_feed import PolymarketPriceFeed
from app.core.database import (
    save_signal_config, load_signal_config,
    save_position_sizing_config, load_position_sizing_config,
    init_db, DB_PATH
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def polymarket_adapter():
    """Create a PolymarketAdapter with mocked settings."""
    with patch("app.brokers.polymarket.settings") as mock_settings:
        mock_settings.polymarket_private_key = ""
        mock_settings.polygon_rpc_url = ""
        mock_settings.polymarket_api_key = ""
        return PolymarketAdapter()


@pytest.fixture
def risk_engine():
    return RiskEngine()


@pytest.fixture
def execution_engine(risk_engine):
    return ExecutionEngine(risk_engine)


@pytest.fixture
def db_mocks():
    """Context manager that mocks all database calls in execution_engine."""
    with patch("app.engines.execution_engine.log_trade_decision", new_callable=AsyncMock, return_value=1) as log_dec, \
         patch("app.engines.execution_engine.log_order", new_callable=AsyncMock, return_value=100) as log_ord, \
         patch("app.engines.execution_engine.update_order_status", new_callable=AsyncMock) as upd_ord, \
         patch("app.engines.execution_engine.mark_decision_executed", new_callable=AsyncMock) as mark_exec, \
         patch("app.engines.execution_engine.log_journal_entry", new_callable=AsyncMock) as log_journal, \
         patch("app.engines.execution_engine.event_bus.publish", new_callable=AsyncMock) as pub:
        yield {
            "log_trade_decision": log_dec,
            "log_order": log_ord,
            "update_order_status": upd_ord,
            "mark_decision_executed": mark_exec,
            "log_journal_entry": log_journal,
            "event_bus_publish": pub,
        }


@pytest.fixture
def fake_broker():
    """Fake broker adapter for testing."""
    class FakeBroker:
        async def place_order(self, symbol, side, quantity, order_type="MARKET", limit_price=None):
            return OrderResult(
                success=True,
                broker_order_id="TEST-001",
                filled_price=100.0,
                filled_quantity=quantity,
            )

        async def get_positions(self):
            return {}

        async def get_balance(self):
            return {}

        async def get_order_history(self, limit=50):
            return []

        async def cancel_order(self, order_id):
            return True

    return FakeBroker()


# ============================================================================
# Test 1: Polymarket place_order returns None for filled_price/filled_quantity
# ============================================================================

class TestPolymarketPlaceOrderPending:
    @pytest.mark.asyncio
    async def test_place_order_returns_none_filled_values_on_201(self, polymarket_adapter):
        """When API returns 201, filled_price and filled_quantity should be None (order pending, not filled)."""
        polymarket_adapter._api_key = "test_key"
        market_data = {
            "tokens": [
                {"token_id": "yes_token", "price": 0.6},
                {"token_id": "no_token", "price": 0.4}
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "order_123"}

        with patch.object(polymarket_adapter, "get_market", new_callable=AsyncMock, return_value=market_data), \
             patch.object(polymarket_adapter._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await polymarket_adapter.place_order("test_id", "buy", 10.0, limit_price=0.55)

            assert result.success is True
            assert result.broker_order_id == "order_123"
            assert result.filled_price is None
            assert result.filled_quantity is None

    @pytest.mark.asyncio
    async def test_place_order_sell_returns_none_filled_values(self, polymarket_adapter):
        """Sell order should also return None for filled values."""
        polymarket_adapter._api_key = "test_key"
        market_data = {
            "tokens": [
                {"token_id": "yes_token", "price": 0.6},
                {"token_id": "no_token", "price": 0.4}
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "order_456"}

        with patch.object(polymarket_adapter, "get_market", new_callable=AsyncMock, return_value=market_data), \
             patch.object(polymarket_adapter._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await polymarket_adapter.place_order("test_id", "sell", 5.0, limit_price=0.45)

            assert result.success is True
            assert result.broker_order_id == "order_456"
            assert result.filled_price is None
            assert result.filled_quantity is None


# ============================================================================
# Test 2: ExecutionEngine handles pending orders correctly
# ============================================================================

class TestExecutionEnginePendingOrders:
    @pytest.mark.asyncio
    async def test_execute_trade_sets_status_pending_when_filled_price_is_none(self, execution_engine, fake_broker, db_mocks):
        """When broker returns filled_price=None, order status should be 'pending' not 'filled'."""
        execution_engine.register_broker("test", fake_broker, default=True)

        action = TradeAction(
            symbol="TEST",
            side="buy",
            quantity=10.0,
            reasoning="Test",
            confidence=0.9,
            strategy="test",
        )

        with patch.object(fake_broker, "place_order", new_callable=AsyncMock) as mock_order:
            mock_order.return_value = OrderResult(
                success=True,
                broker_order_id="ORDER-123",
                filled_price=None,
                filled_quantity=None,
            )

            await execution_engine.execute_trade(action, current_price=100.0)

            # Check that update_order_status was called with "pending" status
            assert db_mocks["update_order_status"].called
            call_args = db_mocks["update_order_status"].call_args
            # First argument is order_id, second is status
            status_arg = call_args[0][1]
            assert status_arg == "pending", f"Expected 'pending' status, got '{status_arg}'"

    @pytest.mark.asyncio
    async def test_execute_trade_sets_status_filled_when_filled_price_is_not_none(self, execution_engine, fake_broker, db_mocks):
        """When broker returns filled_price with value, order status should be 'filled'."""
        execution_engine.register_broker("test", fake_broker, default=True)

        action = TradeAction(
            symbol="TEST",
            side="buy",
            quantity=10.0,
            reasoning="Test",
            confidence=0.9,
            strategy="test",
        )

        with patch.object(fake_broker, "place_order", new_callable=AsyncMock) as mock_order:
            mock_order.return_value = OrderResult(
                success=True,
                broker_order_id="ORDER-123",
                filled_price=100.5,
                filled_quantity=10.0,
            )

            await execution_engine.execute_trade(action, current_price=100.0)

            # Check that update_order_status was called with "filled" status
            assert db_mocks["update_order_status"].called
            call_args = db_mocks["update_order_status"].call_args
            status_arg = call_args[0][1]
            assert status_arg == "filled", f"Expected 'filled' status, got '{status_arg}'"


# ============================================================================
# Test 3: Polymarket feed stops retrying after max_retries
# ============================================================================

class TestPolymarketFeedMaxRetries:
    @pytest.mark.asyncio
    async def test_fetch_with_backoff_logs_error_after_max_retries(self):
        """Feed should log error and return empty dict after max_retries consecutive failures."""
        feed = PolymarketPriceFeed(["test_id"])

        import httpx
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        feed._http = mock_http

        # Mock get to always fail (non-200 status)
        mock_response = MagicMock()
        mock_response.status_code = 500  # API error
        mock_http.get.return_value = mock_response

        with patch("app.feeds.polymarket_feed.logger") as mock_logger:
            # With max_retries=1, should hit limit quickly
            result = await feed._fetch_with_backoff(max_retries=1)

            # Should return empty after hitting max retries
            assert result == {}
            # Should have logged the error
            assert mock_logger.error.called

    @pytest.mark.asyncio
    async def test_fetch_with_backoff_retrieves_data_on_success(self):
        """Feed should return data when successfully fetched."""
        feed = PolymarketPriceFeed(["test_id"])

        import httpx
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        feed._http = mock_http

        # Mock successful response with valid data
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "outcomePrices": "[0.6]",
            "volume24hr": 1000
        }

        mock_http.get.return_value = mock_response

        result = await feed._fetch_with_backoff(max_retries=10)

        # Should return valid data
        assert result == {"test_id": (0.6, 1000)}


# ============================================================================
# Test 4: Signal config and position sizing config persistence
# ============================================================================

class TestConfigPersistence:
    @pytest.mark.asyncio
    async def test_save_and_load_signal_config(self):
        """Signal config should persist to DB and reload correctly."""
        await init_db()

        config = {
            "rsi_period": 10,
            "rsi_oversold": 25,
            "rsi_overbought": 75,
            "macd_fast": 8,
            "macd_slow": 24,
            "macd_signal": 7,
            "volume_spike_ratio": 2.5,
            "bb_period": 18,
            "bb_std_dev": 2.2,
        }

        await save_signal_config(config)
        loaded = await load_signal_config()

        assert loaded is not None
        assert loaded["rsi_period"] == 10
        assert loaded["rsi_oversold"] == 25
        assert loaded["rsi_overbought"] == 75
        assert loaded["macd_fast"] == 8
        assert loaded["macd_slow"] == 24
        assert loaded["macd_signal"] == 7
        assert loaded["volume_spike_ratio"] == 2.5
        assert loaded["bb_period"] == 18
        assert loaded["bb_std_dev"] == 2.2

    @pytest.mark.asyncio
    async def test_save_and_load_position_sizing_config(self):
        """Position sizing config should persist to DB and reload correctly."""
        await init_db()

        config = {
            "method": "kelly",
            "fixed_quantity": 2.0,
            "portfolio_fraction": 0.03,
            "kelly_win_rate": 0.6,
            "kelly_avg_win": 2.0,
            "kelly_avg_loss": 1.5,
            "max_position_pct": 0.15,
        }

        await save_position_sizing_config(config)
        loaded = await load_position_sizing_config()

        assert loaded is not None
        assert loaded["method"] == "kelly"
        assert loaded["fixed_quantity"] == 2.0
        assert loaded["portfolio_fraction"] == 0.03
        assert loaded["kelly_win_rate"] == 0.6
        assert loaded["kelly_avg_win"] == 2.0
        assert loaded["kelly_avg_loss"] == 1.5
        assert loaded["max_position_pct"] == 0.15

    @pytest.mark.asyncio
    async def test_signal_config_update_overwrites_previous(self):
        """Updating signal config should overwrite the previous one."""
        await init_db()

        config1 = {
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "volume_spike_ratio": 2.0,
            "bb_period": 20,
            "bb_std_dev": 2.0,
        }

        config2 = {
            "rsi_period": 20,
            "rsi_oversold": 35,
            "rsi_overbought": 65,
            "macd_fast": 10,
            "macd_slow": 25,
            "macd_signal": 8,
            "volume_spike_ratio": 1.8,
            "bb_period": 18,
            "bb_std_dev": 1.8,
        }

        await save_signal_config(config1)
        loaded1 = await load_signal_config()
        assert loaded1["rsi_period"] == 14

        await save_signal_config(config2)
        loaded2 = await load_signal_config()
        assert loaded2["rsi_period"] == 20


# ============================================================================
# Test 5: DryRun order history stays bounded
# ============================================================================

class TestDryRunOrderHistoryPruning:
    @pytest.mark.asyncio
    async def test_order_history_pruned_above_max(self):
        """Order history should be pruned to MAX_ORDER_HISTORY when exceeded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "dryrun_state.json"

            with patch("app.brokers.dryrun.STATE_FILE", state_file):
                broker = DryRunBrokerAdapter()

                # Place buy orders only to avoid position shortage
                for i in range(50):
                    result = await broker.place_order(
                        symbol="TEST",
                        side="buy",
                        quantity=1.0,
                    )
                    assert result.success

                # Manually trigger pruning by manipulating order history
                # to simulate reaching 5100+ orders
                broker._order_history.extend([{
                    "order_id": f"DRY-{i:06d}",
                    "symbol": "TEST",
                    "side": "buy",
                    "quantity": 1.0,
                    "filled_price": 100.0,
                    "status": "filled",
                    "timestamp": 0,
                } for i in range(50, 5150)])

                # Now place one more order to trigger pruning
                result = await broker.place_order(
                    symbol="TEST",
                    side="buy",
                    quantity=1.0,
                )
                assert result.success

                # Check order history is pruned to max size
                assert len(broker._order_history) <= 5000
                # The last order added should be in history
                assert broker._order_history[-1]["order_id"].startswith("DRY-")

    @pytest.mark.asyncio
    async def test_order_history_not_pruned_below_max(self):
        """Order history should not be pruned if below MAX_ORDER_HISTORY."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "dryrun_state.json"

            with patch("app.brokers.dryrun.STATE_FILE", state_file):
                broker = DryRunBrokerAdapter()

                # Place just a few orders
                for i in range(100):
                    result = await broker.place_order(
                        symbol="TEST",
                        side="buy",
                        quantity=1.0,
                    )
                    assert result.success

                # Check order history is exactly 100 (not pruned)
                assert len(broker._order_history) == 100

    @pytest.mark.asyncio
    async def test_order_history_state_persists_after_prune(self):
        """State should persist correctly even after history is pruned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "dryrun_state.json"

            with patch("app.brokers.dryrun.STATE_FILE", state_file):
                broker = DryRunBrokerAdapter()

                # Place some orders
                for i in range(50):
                    await broker.place_order(
                        symbol="TEST",
                        side="buy" if i % 2 == 0 else "sell",
                        quantity=1.0,
                    )

                # Simulate large history by extending it
                broker._order_history.extend([{
                    "order_id": f"DRY-{i:06d}",
                    "symbol": "TEST",
                    "side": "buy",
                    "quantity": 1.0,
                    "filled_price": 100.0,
                    "status": "filled",
                    "timestamp": 0,
                } for i in range(50, 5150)])

                # Place one more order to trigger save with prune
                await broker.place_order(
                    symbol="TEST",
                    side="buy",
                    quantity=1.0,
                )

                # Load state from file
                with open(state_file, "r") as f:
                    saved_state = json.load(f)

                # Verify order count in saved state is correct (pruned)
                assert len(saved_state["orders"]) <= 5000
