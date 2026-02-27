from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.brokers.polymarket import PolymarketAdapter


@pytest.fixture
def adapter():
    """Create a PolymarketAdapter with mocked settings."""
    with patch("app.brokers.polymarket.settings") as mock_settings:
        mock_settings.polymarket_private_key = ""
        mock_settings.polygon_rpc_url = ""
        mock_settings.polymarket_api_key = ""
        a = PolymarketAdapter()
        return a


class TestPolymarketAdapterInit:
    def test_adapter_initializes_with_settings(self):
        """Adapter initializes with empty credentials."""
        with patch("app.brokers.polymarket.settings") as mock_settings:
            mock_settings.polymarket_private_key = "test_key"
            mock_settings.polygon_rpc_url = "http://localhost:8545"
            mock_settings.polymarket_api_key = "test_api_key"
            adapter = PolymarketAdapter()

            assert adapter._private_key == "test_key"
            assert adapter._rpc_url == "http://localhost:8545"
            assert adapter._api_key == "test_api_key"


class TestPolymarketPlaceOrder:
    @pytest.mark.asyncio
    async def test_place_order_no_api_key(self, adapter):
        """Place order without API key returns error."""
        result = await adapter.place_order("test_condition_id", "buy", 10.0)
        assert result.success is False
        assert "api_key" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_place_order_with_api_key_market_not_found(self, adapter):
        """Place order with API key but market doesn't exist."""
        adapter._api_key = "test_key"
        with patch.object(adapter, "get_market", new_callable=AsyncMock, return_value=None):
            result = await adapter.place_order("nonexistent", "buy", 10.0)
            assert result.success is False
            assert "not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_place_order_invalid_side(self, adapter):
        """Place order with invalid side returns error."""
        adapter._api_key = "test_key"
        market_data = {"tokens": [{"token_id": "token1"}, {"token_id": "token2"}]}
        with patch.object(adapter, "get_market", new_callable=AsyncMock, return_value=market_data):
            result = await adapter.place_order("test_id", "invalid_side", 10.0)
            assert result.success is False
            assert "Invalid side" in (result.error or "")

    @pytest.mark.asyncio
    async def test_place_order_buy_success(self, adapter):
        """Place buy order successfully."""
        adapter._api_key = "test_key"
        market_data = {
            "tokens": [
                {"token_id": "yes_token", "price": 0.6},
                {"token_id": "no_token", "price": 0.4}
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "order_123"}

        with patch.object(adapter, "get_market", new_callable=AsyncMock, return_value=market_data), \
             patch.object(adapter._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.place_order("test_id", "buy", 10.0, limit_price=0.55)
            assert result.success is True
            assert result.broker_order_id == "order_123"
            # Order is pending (placed on book), not immediately filled
            assert result.filled_price is None
            assert result.filled_quantity is None

    @pytest.mark.asyncio
    async def test_place_order_sell_success(self, adapter):
        """Place sell order successfully."""
        adapter._api_key = "test_key"
        market_data = {
            "tokens": [
                {"token_id": "yes_token", "price": 0.6},
                {"token_id": "no_token", "price": 0.4}
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "order_456"}

        with patch.object(adapter, "get_market", new_callable=AsyncMock, return_value=market_data), \
             patch.object(adapter._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.place_order("test_id", "sell", 5.0, limit_price=0.45)
            assert result.success is True
            assert result.broker_order_id == "order_456"
            # Order is pending (placed on book), not immediately filled
            assert result.filled_price is None
            assert result.filled_quantity is None


class TestPolymarketGetPositions:
    @pytest.mark.asyncio
    async def test_get_positions_no_api_key(self, adapter):
        """Get positions without API key returns empty dict."""
        positions = await adapter.get_positions()
        assert positions == {}

    @pytest.mark.asyncio
    async def test_get_positions_with_api_key_success(self, adapter):
        """Get positions with API key returns positions."""
        adapter._api_key = "test_key"
        positions_data = [
            {
                "condition_id": "market1",
                "token_id": "token1",
                "quantity": 10.0,
                "avg_price": 0.5,
                "market_value": 5.0
            }
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = positions_data

        with patch.object(adapter._http, "get", new_callable=AsyncMock, return_value=mock_response):
            positions = await adapter.get_positions()
            assert "market1" in positions
            assert positions["market1"]["quantity"] == 10.0

    @pytest.mark.asyncio
    async def test_get_positions_api_error(self, adapter):
        """Get positions with API error returns empty dict."""
        adapter._api_key = "test_key"

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.object(adapter._http, "get", new_callable=AsyncMock, return_value=mock_response):
            positions = await adapter.get_positions()
            assert positions == {}


class TestPolymarketGetBalance:
    @pytest.mark.asyncio
    async def test_get_balance_no_credentials(self, adapter):
        """Get balance without credentials returns empty dict."""
        balance = await adapter.get_balance()
        assert balance == {}

    @pytest.mark.asyncio
    async def test_get_balance_no_rpc(self, adapter):
        """Get balance without RPC URL returns empty dict."""
        adapter._private_key = "test_key"
        balance = await adapter.get_balance()
        assert balance == {}


class TestPolymarketGetOrderHistory:
    @pytest.mark.asyncio
    async def test_get_order_history(self, adapter):
        """Get order history returns empty list (not yet implemented)."""
        history = await adapter.get_order_history()
        assert history == []

    @pytest.mark.asyncio
    async def test_get_order_history_with_limit(self, adapter):
        """Get order history with limit parameter."""
        history = await adapter.get_order_history(limit=10)
        assert history == []


class TestPolymarketCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_order(self, adapter):
        """Cancel order returns False (not yet implemented)."""
        result = await adapter.cancel_order("test_order")
        assert result is False


class TestPolymarketGetTrendingMarkets:
    @pytest.mark.asyncio
    async def test_get_trending_markets_success(self, adapter):
        """Get trending markets successfully."""
        markets_data = [
            {"id": "1", "question": "Will X happen?", "volume24hr": 10000},
            {"id": "2", "question": "Will Y happen?", "volume24hr": 8000}
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = markets_data
        mock_response.raise_for_status = MagicMock()

        with patch.object(adapter._http, "get", new_callable=AsyncMock, return_value=mock_response):
            markets = await adapter.get_trending_markets(limit=5)
            assert len(markets) == 2
            assert markets[0]["question"] == "Will X happen?"
            assert markets[1]["question"] == "Will Y happen?"

    @pytest.mark.asyncio
    async def test_get_trending_markets_api_error(self, adapter):
        """Get trending markets with API error raises."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("API Error")

        with patch.object(adapter._http, "get", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(Exception):
                await adapter.get_trending_markets(limit=5)


class TestPolymarketSearchMarkets:
    @pytest.mark.asyncio
    async def test_search_markets_success(self, adapter):
        """Search markets successfully."""
        markets_data = [{"id": "2", "question": "Election winner?"}]

        mock_response = MagicMock()
        mock_response.json.return_value = markets_data
        mock_response.raise_for_status = MagicMock()

        with patch.object(adapter._http, "get", new_callable=AsyncMock, return_value=mock_response):
            markets = await adapter.search_markets("election", limit=3)
            assert len(markets) == 1
            assert markets[0]["question"] == "Election winner?"

    @pytest.mark.asyncio
    async def test_search_markets_empty_query(self, adapter):
        """Search markets with empty query."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch.object(adapter._http, "get", new_callable=AsyncMock, return_value=mock_response):
            markets = await adapter.search_markets("", limit=10)
            assert len(markets) == 0


class TestPolymarketGetMarket:
    @pytest.mark.asyncio
    async def test_get_market_success(self, adapter):
        """Get market details successfully."""
        market_data = {
            "id": "market1",
            "question": "Test market?",
            "tokens": [
                {"token_id": "yes", "price": 0.6},
                {"token_id": "no", "price": 0.4}
            ]
        }

        mock_response = MagicMock()
        mock_response.json.return_value = market_data
        mock_response.raise_for_status = MagicMock()

        with patch.object(adapter._http, "get", new_callable=AsyncMock, return_value=mock_response):
            market = await adapter.get_market("market1")
            assert market["question"] == "Test market?"
            assert len(market["tokens"]) == 2

    @pytest.mark.asyncio
    async def test_get_market_not_found(self, adapter):
        """Get market returns error for non-existent market."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")

        with patch.object(adapter._http, "get", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(Exception):
                await adapter.get_market("nonexistent")


class TestPolymarketGetMarketPrices:
    @pytest.mark.asyncio
    async def test_get_market_prices_success(self, adapter):
        """Get market prices successfully."""
        market_data = {
            "tokens": [
                {"price": 0.6},
                {"price": 0.4}
            ]
        }

        with patch.object(adapter, "get_market", new_callable=AsyncMock, return_value=market_data):
            prices = await adapter.get_market_prices("market1")
            assert prices["yes_price"] == 0.6
            assert prices["no_price"] == 0.4

    @pytest.mark.asyncio
    async def test_get_market_prices_invalid_market(self, adapter):
        """Get market prices for market with no tokens."""
        market_data = {"tokens": []}

        with patch.object(adapter, "get_market", new_callable=AsyncMock, return_value=market_data):
            prices = await adapter.get_market_prices("market1")
            assert prices == {}

    @pytest.mark.asyncio
    async def test_get_market_prices_api_error(self, adapter):
        """Get market prices with API error returns empty dict."""
        with patch.object(adapter, "get_market", new_callable=AsyncMock, side_effect=Exception("API Error")):
            prices = await adapter.get_market_prices("market1")
            assert prices == {}


# ============================================================================
# Polymarket HMAC Signing Tests
# ============================================================================


class TestPolymarketSigning:
    def test_sign_order_adds_signature_field(self):
        """Test that _sign_order adds signature and timestamp fields."""
        with patch("app.brokers.polymarket.settings") as mock_settings:
            mock_settings.polymarket_private_key = "test_secret_key"
            mock_settings.polygon_rpc_url = ""
            mock_settings.polymarket_api_key = ""
            adapter = PolymarketAdapter()

            order_data = {
                "tokenID": "token123",
                "price": 0.55,
                "size": 10.0,
                "side": "BUY",
            }

            signed_order = adapter._sign_order(order_data)

            assert "signature" in signed_order
            assert "timestamp" in signed_order
            assert signed_order["tokenID"] == "token123"
            assert signed_order["price"] == 0.55

    def test_sign_order_without_private_key(self):
        """Test that _sign_order returns data unchanged without private key."""
        with patch("app.brokers.polymarket.settings") as mock_settings:
            mock_settings.polymarket_private_key = ""
            mock_settings.polygon_rpc_url = ""
            mock_settings.polymarket_api_key = ""
            adapter = PolymarketAdapter()

            order_data = {
                "tokenID": "token123",
                "price": 0.55,
                "size": 10.0,
                "side": "BUY",
            }

            signed_order = adapter._sign_order(order_data)

            assert "signature" not in signed_order
            assert "timestamp" not in signed_order
            assert signed_order == order_data

    def test_sign_order_signature_deterministic(self):
        """Test that signing the same order produces consistent signature."""
        with patch("app.brokers.polymarket.settings") as mock_settings:
            mock_settings.polymarket_private_key = "test_secret_key"
            mock_settings.polygon_rpc_url = ""
            mock_settings.polymarket_api_key = ""
            adapter = PolymarketAdapter()

            order_data = {
                "tokenID": "token123",
                "price": 0.55,
                "size": 10.0,
                "side": "BUY",
            }

            # Mock time to ensure same timestamp
            with patch("app.brokers.polymarket.time_module.time", return_value=1234567890):
                signed1 = adapter._sign_order(order_data.copy())

            with patch("app.brokers.polymarket.time_module.time", return_value=1234567890):
                signed2 = adapter._sign_order(order_data.copy())

            assert signed1["signature"] == signed2["signature"]
            assert signed1["timestamp"] == signed2["timestamp"]

    @pytest.mark.asyncio
    async def test_place_order_includes_signature(self, adapter):
        """Test that place_order includes signature in request."""
        adapter._api_key = "test_key"
        adapter._private_key = "test_secret"

        market_data = {
            "tokens": [
                {"token_id": "yes_token", "price": 0.6},
                {"token_id": "no_token", "price": 0.4}
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "order_123"}

        with patch.object(adapter, "get_market", new_callable=AsyncMock, return_value=market_data):
            with patch.object(adapter._http, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
                await adapter.place_order("test_id", "buy", 10.0, limit_price=0.55)

                # Verify that post was called with signature in body
                mock_post.assert_called_once()
                call_args = mock_post.call_args
                posted_data = call_args[1]["json"]

                assert "signature" in posted_data
                assert "timestamp" in posted_data
                assert posted_data["tokenID"] == "yes_token"
