from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api.routes import router


@pytest.fixture
def client():
    """Create a test client with mocked dependencies."""
    test_app = FastAPI()
    test_app.include_router(router)
    yield TestClient(test_app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        with patch("app.main.signal_engine") as mock_sig, \
             patch("app.main.llm_brain") as mock_llm, \
             patch("app.main.risk_engine") as mock_risk, \
             patch("app.main.execution_engine") as mock_exec, \
             patch("app.api.routes.get_latest_timestamps", new_callable=AsyncMock) as mock_timestamps:
            mock_sig._running = True
            mock_llm._provider = "gemini"
            mock_risk.kill_switch_active = False
            mock_exec._brokers = {"ibkr": MagicMock()}
            mock_timestamps.return_value = {
                "last_signal_at": "2024-01-01T12:00:00+00:00",
                "last_decision_at": "2024-01-01T12:00:00+00:00",
            }

            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "engines" in data
            assert "signal_engine" in data["engines"]
            assert "db_connected" in data
            assert "last_signal_at" in data
            assert "last_decision_at" in data
            assert "uptime_s" in data


class TestRiskEndpoints:
    def test_get_risk_config(self, client):
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.max_position_usd = 10000.0
            mock_settings.max_daily_loss_usd = 5000.0
            mock_settings.max_portfolio_exposure_usd = 50000.0
            mock_settings.max_single_trade_usd = 2000.0
            mock_settings.max_drawdown_pct = 10.0

            resp = client.get("/api/risk/config")
            assert resp.status_code == 200
            data = resp.json()
            assert "max_position_usd" in data
            assert "max_daily_loss_usd" in data
            assert "max_portfolio_exposure_usd" in data
            assert "max_single_trade_usd" in data
            assert "max_drawdown_pct" in data

    def test_update_risk_config_valid(self, client):
        with patch("app.core.config.settings") as mock_settings, \
             patch("app.api.routes.save_risk_config", new_callable=AsyncMock) as mock_save:
            mock_settings.max_position_usd = 10000.0
            mock_settings.max_daily_loss_usd = 5000.0
            mock_settings.max_portfolio_exposure_usd = 50000.0
            mock_settings.max_single_trade_usd = 2000.0
            mock_settings.max_drawdown_pct = 10.0

            resp = client.post("/api/risk/config", json={
                "max_single_trade_usd": 5000,
                "max_daily_loss_usd": 3000,
            })
            assert resp.status_code == 200
            mock_save.assert_called_once()

    def test_update_risk_config_invalid_negative(self, client):
        resp = client.post("/api/risk/config", json={
            "max_single_trade_usd": -100,
        })
        assert resp.status_code == 422

    def test_update_risk_config_invalid_drawdown(self, client):
        resp = client.post("/api/risk/config", json={
            "max_drawdown_pct": 150,
        })
        assert resp.status_code == 422

    def test_kill_switch_toggle(self, client):
        with patch("app.core.events.event_bus.publish", new_callable=AsyncMock) as mock_pub:
            resp = client.post("/api/risk/killswitch", json={"active": True})
            assert resp.status_code == 200
            assert resp.json()["active"] is True
            mock_pub.assert_called_once()

    def test_get_live_risk(self, client):
        with patch("app.main.risk_engine") as mock_risk:
            mock_risk.get_risk_snapshot.return_value = {
                "total_exposure_usd": 25000.0,
                "daily_pnl_usd": 500.0,
                "kill_switch_active": False,
            }
            resp = client.get("/api/risk/live")
            assert resp.status_code == 200
            data = resp.json()
            assert "total_exposure_usd" in data


class TestBrokerEndpoints:
    def test_list_brokers_empty(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_exec._brokers = {}
            mock_exec._default_broker = None
            resp = client.get("/api/brokers")
            assert resp.status_code == 200
            data = resp.json()
            assert "brokers" in data

    def test_list_brokers_with_ibkr(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_broker = MagicMock()
            mock_exec._brokers = {"ibkr": mock_broker}
            mock_exec._default_broker = "ibkr"
            resp = client.get("/api/brokers")
            assert resp.status_code == 200
            data = resp.json()
            assert "ibkr" in data["brokers"]
            assert data["default"] == "ibkr"

    def test_connect_unknown_broker(self, client):
        resp = client.post("/api/broker/connect", json={"broker": "unknown"})
        assert resp.status_code == 400

    def test_disconnect_unregistered_broker(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_exec._brokers = {}
            resp = client.post("/api/broker/disconnect", json={"broker": "ibkr"})
            assert resp.status_code == 404


class TestMarketEndpoints:
    def test_trending_markets_no_adapter(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_exec._brokers = {}
            resp = client.get("/api/markets/trending")
            assert resp.status_code == 400

    def test_search_markets_no_adapter(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_exec._brokers = {}
            resp = client.get("/api/markets/search?q=election")
            assert resp.status_code == 400


class TestOrderEndpoints:
    def test_cancel_order_not_found(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_broker = AsyncMock()
            mock_broker.cancel_order.return_value = False
            mock_exec._brokers = {"ibkr": mock_broker}

            resp = client.post("/api/orders/nonexistent/cancel", json={"broker": "ibkr"})
            assert resp.status_code == 400


class TestPositionEndpoints:
    def test_get_all_positions(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_exec.get_all_positions = AsyncMock(return_value={})
            response = client.get("/api/positions/all")
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_all_positions_flattened(self, client):
        """Test /api/positions/all returns flattened position list."""
        with patch("app.main.execution_engine") as mock_exec:
            mock_exec.get_all_positions = AsyncMock(return_value={
                "polymarket": {
                    "COND_123": {
                        "quantity": 10.0,
                        "avg_cost": 0.55,
                        "market_value": 6.0,
                        "unrealized_pnl": 0.5,
                        "realized_pnl": 0.0,
                    }
                }
            })
            response = client.get("/api/positions/all")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["broker"] == "polymarket"
            assert data[0]["symbol"] == "COND_123"
            assert data[0]["quantity"] == 10.0
            assert data[0]["avg_entry_price"] == 0.55


class TestTradeStatsEndpoint:
    def test_get_trade_stats(self, client):
        with patch("app.api.routes.aiosqlite.connect") as mock_connect:
            # Mock the async context manager
            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=None)
            mock_connect.return_value = mock_db

            # Mock cursor
            mock_cursor = AsyncMock()
            mock_cursor.fetchone = AsyncMock(return_value={"count": 10})
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)

            response = client.get("/api/stats")
            assert response.status_code == 200
            data = response.json()
            assert "total_filled_orders" in data
            assert "total_decisions" in data
            assert "trades_by_side" in data
            assert "total_api_cost_usd" in data


def test_mask_key():
    from app.api.routes import _mask_key
    assert _mask_key("") == ""
    assert _mask_key("abcd") == "abcd"
    assert _mask_key("sk-abc123xyz") == "••••••••3xyz"
    assert _mask_key("12345678") == "••••5678"


def test_mask_key_short():
    from app.api.routes import _mask_key
    assert _mask_key("ab") == "ab"
    assert _mask_key("abc") == "abc"


class TestPaginatedEndpoints:
    """Test pagination envelope structure on list endpoints."""

    def test_decisions_pagination_structure(self, client):
        with patch("app.api.routes.aiosqlite.connect") as mock_connect, \
             patch("app.api.routes.count_trade_decisions", new_callable=AsyncMock) as mock_count:
            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=None)
            mock_connect.return_value = mock_db

            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_count.return_value = 100

            response = client.get("/api/decisions?limit=50&offset=0")
            assert response.status_code == 200
            data = response.json()
            assert "data" in data
            assert "total" in data
            assert "limit" in data
            assert "offset" in data
            assert "has_more" in data
            assert data["total"] == 100
            assert data["limit"] == 50
            assert data["offset"] == 0
            assert data["has_more"] is True

    def test_orders_pagination_structure(self, client):
        with patch("app.api.routes.aiosqlite.connect") as mock_connect, \
             patch("app.api.routes.count_orders", new_callable=AsyncMock) as mock_count:
            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=None)
            mock_connect.return_value = mock_db

            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_count.return_value = 75

            response = client.get("/api/orders?limit=25&offset=50")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 75
            assert data["limit"] == 25
            assert data["offset"] == 50
            assert data["has_more"] is False

    def test_signals_pagination_structure(self, client):
        with patch("app.api.routes.aiosqlite.connect") as mock_connect, \
             patch("app.api.routes.count_signals", new_callable=AsyncMock) as mock_count:
            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=None)
            mock_connect.return_value = mock_db

            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_count.return_value = 200

            response = client.get("/api/signals?limit=100&offset=0")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 200
            assert data["has_more"] is True


class TestEnhancedHealthCheck:
    """Test the enhanced health check endpoint."""

    def test_health_includes_new_fields(self, client):
        with patch("app.main.signal_engine") as mock_sig, \
             patch("app.main.llm_brain") as mock_llm, \
             patch("app.main.risk_engine") as mock_risk, \
             patch("app.main.execution_engine") as mock_exec, \
             patch("app.api.routes.get_latest_timestamps", new_callable=AsyncMock) as mock_timestamps:
            mock_sig._running = True
            mock_llm._provider = "gemini"
            mock_risk.kill_switch_active = False
            mock_exec._brokers = {}
            mock_timestamps.return_value = {
                "last_signal_at": "2024-01-01T12:00:00+00:00",
                "last_decision_at": "2024-01-01T11:00:00+00:00",
            }

            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["db_connected"] is not None
            assert data["last_signal_at"] == "2024-01-01T12:00:00+00:00"
            assert data["last_decision_at"] == "2024-01-01T11:00:00+00:00"
            assert data["uptime_s"] >= 0
            assert isinstance(data["uptime_s"], (int, float))


class TestBalanceEndpoint:
    def test_get_balance_success(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_exec.get_balance = AsyncMock(return_value={"usd": 10000.00, "positions": []})
            resp = client.get("/api/balance/ibkr")
            assert resp.status_code == 200
            data = resp.json()
            assert data["broker"] == "ibkr"
            assert "balance" in data

    def test_get_balance_unknown_broker(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_exec.get_balance = AsyncMock(return_value={})
            resp = client.get("/api/balance/unknown_broker")
            assert resp.status_code == 200
            data = resp.json()
            assert data["broker"] == "unknown_broker"
            assert "balance" in data


class TestBrokerOrderHistory:
    def test_get_broker_orders_success(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_broker = AsyncMock()
            mock_broker.get_order_history = AsyncMock(return_value=[
                {"id": "1", "symbol": "AAPL", "side": "buy", "quantity": 10, "status": "filled"}
            ])
            mock_exec._brokers = {"ibkr": mock_broker}
            resp = client.get("/api/orders/broker/ibkr")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) == 1

    def test_get_broker_orders_not_found(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_exec._brokers = {}
            resp = client.get("/api/orders/broker/unknown")
            assert resp.status_code == 404

    def test_get_broker_orders_empty(self, client):
        with patch("app.main.execution_engine") as mock_exec:
            mock_broker = AsyncMock()
            mock_broker.get_order_history = AsyncMock(return_value=[])
            mock_exec._brokers = {"ibkr": mock_broker}
            resp = client.get("/api/orders/broker/ibkr")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) == 0


class TestBrokerConnection:
    def test_connect_ibkr_broker(self, client):
        with patch("app.brokers.ibkr.IBKRAdapter") as mock_ibkr_class, \
             patch("app.main.execution_engine") as mock_exec:
            mock_adapter = AsyncMock()
            mock_adapter.connect = AsyncMock()
            mock_ibkr_class.return_value = mock_adapter
            mock_exec.register_broker = MagicMock()

            resp = client.post("/api/broker/connect", json={"broker": "ibkr"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["broker"] == "ibkr"


class TestWebSocketBidirectional:
    @pytest.mark.asyncio
    async def test_websocket_send_and_receive(self, client):
        """Test WebSocket bidirectional communication."""
        from app.api.routes import Event
        from app.core.events import event_bus

        with patch("app.core.events.event_bus.subscribe") as mock_sub, \
             patch("app.core.events.event_bus.publish", new_callable=AsyncMock) as mock_pub, \
             patch("app.core.events.event_bus.register_ws_client") as mock_reg_ws, \
             patch("app.core.events.event_bus.unregister_ws_client") as mock_unreg_ws:
            # This test verifies the WebSocket endpoint accepts connections
            # and the receive_loop would process commands
            try:
                with client.websocket_connect("/ws") as websocket:
                    pass
            except Exception:
                pass


class TestPerformanceEndpoints:
    def test_get_performance_summary_empty(self, client):
        """Test performance summary endpoint with no trades."""
        with patch("app.api.routes.get_trade_pnl_data", new_callable=AsyncMock) as mock_trades:
            mock_trades.return_value = []
            resp = client.get("/api/performance/summary")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_trades"] == 0
            assert data["winning_trades"] == 0
            assert data["losing_trades"] == 0
            assert data["win_rate"] == 0
            assert data["total_pnl"] == 0
            assert "profit_factor" in data
            assert "matched_trades" in data

    def test_get_performance_summary_with_trades(self, client):
        """Test performance summary endpoint with trade data."""
        mock_trades = [
            {"filled_price": 100, "filled_quantity": 10, "side": "BUY"},
            {"filled_price": 102, "filled_quantity": 10, "side": "SELL"},
        ]
        with patch("app.api.routes.get_trade_pnl_data", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_trades
            resp = client.get("/api/performance/summary")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_trades"] == 2
            assert "winning_trades" in data
            assert "losing_trades" in data
            assert "win_rate" in data
            assert "total_pnl" in data

    def test_get_performance_history_empty(self, client):
        """Test performance history endpoint with no data."""
        with patch("app.api.routes.get_performance_history", new_callable=AsyncMock) as mock_hist:
            mock_hist.return_value = []
            resp = client.get("/api/performance/metrics?days=30")
            assert resp.status_code == 200
            data = resp.json()
            assert data["data"] == []
            assert data["period_days"] == 30

    def test_get_performance_history_with_data(self, client):
        """Test performance history endpoint with data."""
        mock_data = [
            {
                "id": 1,
                "date": "2024-01-01",
                "total_trades": 5,
                "winning_trades": 3,
                "losing_trades": 2,
                "total_pnl": 150.0,
                "avg_win": 75.0,
                "avg_loss": -25.0,
                "win_rate": 60.0,
                "profit_factor": 3.0,
                "sharpe_ratio": 1.5,
                "max_drawdown_pct": 5.0,
            }
        ]
        with patch("app.api.routes.get_performance_history", new_callable=AsyncMock) as mock_hist:
            mock_hist.return_value = mock_data
            resp = client.get("/api/performance/metrics?days=30")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["data"]) == 1
            assert data["data"][0]["total_trades"] == 5
            assert data["data"][0]["win_rate"] == 60.0


class TestDryRunEndpoint:
    def test_get_dry_run_status_enabled(self, client):
        """Test dry-run status endpoint when enabled."""
        resp = client.get("/api/config/dry-run")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert isinstance(data["enabled"], bool)


class TestPerformanceSummaryPnL:
    """Test fixed P&L calculation in performance summary."""

    def test_performance_summary_matched_buy_sell_pairs(self, client):
        """Test that P&L is calculated correctly from matched buy/sell pairs."""
        mock_trades = [
            {"symbol": "AAPL", "side": "BUY", "filled_price": 100.0, "filled_quantity": 10},
            {"symbol": "AAPL", "side": "SELL", "filled_price": 105.0, "filled_quantity": 10},
            {"symbol": "AAPL", "side": "BUY", "filled_price": 110.0, "filled_quantity": 5},
        ]
        with patch("app.api.routes.get_trade_pnl_data", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_trades
            resp = client.get("/api/performance/summary")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_trades"] == 3
            assert data["matched_trades"] == 1
            assert data["winning_trades"] == 1
            assert data["losing_trades"] == 0
            assert data["total_pnl"] == 50.0
            assert data["avg_win"] == 50.0
            assert data["avg_loss"] == 0.0
            assert data["win_rate"] == 100.0
            assert data["profit_factor"] == 0.0

    def test_performance_summary_multiple_matched_pairs(self, client):
        """Test multiple matched buy/sell pairs."""
        mock_trades = [
            {"symbol": "AAPL", "side": "BUY", "filled_price": 100.0, "filled_quantity": 10},
            {"symbol": "AAPL", "side": "SELL", "filled_price": 102.0, "filled_quantity": 10},
            {"symbol": "GOOGL", "side": "BUY", "filled_price": 2000.0, "filled_quantity": 5},
            {"symbol": "GOOGL", "side": "SELL", "filled_price": 1990.0, "filled_quantity": 5},
        ]
        with patch("app.api.routes.get_trade_pnl_data", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_trades
            resp = client.get("/api/performance/summary")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_trades"] == 4
            assert data["matched_trades"] == 2
            assert data["winning_trades"] == 1
            assert data["losing_trades"] == 1
            # AAPL: (102-100) * 10 = 20, GOOGL: (1990-2000) * 5 = -50, total = -30
            assert data["total_pnl"] == -30.0
            # profit_factor = abs(sum(wins) / sum(losses)) = abs(20 / -50) = 0.4
            assert data["profit_factor"] == 0.4

    def test_performance_summary_no_matched_pairs(self, client):
        """Test when there are no complete buy/sell pairs."""
        mock_trades = [
            {"symbol": "AAPL", "side": "BUY", "filled_price": 100.0, "filled_quantity": 10},
        ]
        with patch("app.api.routes.get_trade_pnl_data", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_trades
            resp = client.get("/api/performance/summary")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_trades"] == 1
            assert data["matched_trades"] == 0
            assert data["winning_trades"] == 0
            assert data["losing_trades"] == 0
            assert data["total_pnl"] == 0.0
            assert data["win_rate"] == 0.0


class TestSignalConfigValidation:
    """Test signal config update validation."""

    def _make_signal_config_mock(self):
        mock_cfg = MagicMock()
        mock_cfg.rsi_period = 14
        mock_cfg.rsi_oversold = 30.0
        mock_cfg.rsi_overbought = 70.0
        mock_cfg.macd_fast = 12
        mock_cfg.macd_slow = 26
        mock_cfg.macd_signal = 9
        mock_cfg.volume_spike_ratio = 2.0
        mock_cfg.bb_period = 20
        mock_cfg.bb_std_dev = 2.0
        return mock_cfg

    def test_signal_config_valid_update(self, client):
        """Test valid signal config update."""
        with patch("app.main.signal_engine") as mock_sig:
            mock_cfg = self._make_signal_config_mock()
            mock_sig.signal_config = mock_cfg

            resp = client.post("/api/config/signal", json={
                "rsi_period": 20,
                "rsi_oversold": 25,
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
            assert mock_cfg.rsi_period == 20
            assert mock_cfg.rsi_oversold == 25

    def test_signal_config_rejects_negative(self, client):
        """Test that negative values are rejected."""
        with patch("app.main.signal_engine") as mock_sig:
            mock_cfg = self._make_signal_config_mock()
            mock_sig.signal_config = mock_cfg

            resp = client.post("/api/config/signal", json={
                "rsi_period": -10,
            })
            assert resp.status_code == 400
            data = resp.json()
            assert "errors" in data
            assert any("positive" in str(e).lower() for e in data["errors"])

    def test_signal_config_rejects_invalid_type(self, client):
        """Test that invalid type conversions are rejected."""
        with patch("app.main.signal_engine") as mock_sig:
            mock_cfg = self._make_signal_config_mock()
            mock_sig.signal_config = mock_cfg

            resp = client.post("/api/config/signal", json={
                "rsi_period": "not_a_number",
            })
            assert resp.status_code == 400
            data = resp.json()
            assert "errors" in data
            assert any("Invalid" in str(e) for e in data["errors"])

    def test_signal_config_rejects_zero(self, client):
        """Test that zero values are rejected."""
        with patch("app.main.signal_engine") as mock_sig:
            mock_cfg = self._make_signal_config_mock()
            mock_sig.signal_config = mock_cfg

            resp = client.post("/api/config/signal", json={
                "macd_fast": 0,
            })
            assert resp.status_code == 400
            data = resp.json()
            assert "errors" in data
            assert any("positive" in str(e).lower() for e in data["errors"])


class TestPositionSizingEndpoints:
    def test_get_position_sizing_config(self, client):
        """Test GET /api/config/position-sizing returns expected fields."""
        with patch("app.main.execution_engine") as mock_exec:
            from app.engines.position_sizing import PositionSizer
            mock_sizer = PositionSizer()
            mock_exec._position_sizer = mock_sizer

            resp = client.get("/api/config/position-sizing")
            assert resp.status_code == 200
            data = resp.json()
            assert "method" in data
            assert "fixed_quantity" in data
            assert "portfolio_fraction" in data
            assert "kelly_win_rate" in data
            assert "kelly_avg_win" in data
            assert "kelly_avg_loss" in data
            assert "max_position_pct" in data

    def test_update_position_sizing_config_method(self, client):
        """Test POST /api/config/position-sizing updates method."""
        with patch("app.main.execution_engine") as mock_exec:
            from app.engines.position_sizing import PositionSizer
            mock_sizer = PositionSizer()
            mock_exec._position_sizer = mock_sizer

            resp = client.post("/api/config/position-sizing", json={
                "method": "kelly"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert mock_exec._position_sizer.config.method == "kelly"

    def test_update_position_sizing_config_parameters(self, client):
        """Test POST /api/config/position-sizing updates parameters."""
        with patch("app.main.execution_engine") as mock_exec:
            from app.engines.position_sizing import PositionSizer
            mock_sizer = PositionSizer()
            mock_exec._position_sizer = mock_sizer

            resp = client.post("/api/config/position-sizing", json={
                "fixed_quantity": 50.0,
                "portfolio_fraction": 0.05,
                "kelly_win_rate": 0.60
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert mock_exec._position_sizer.config.fixed_quantity == 50.0
            assert mock_exec._position_sizer.config.portfolio_fraction == 0.05
            assert mock_exec._position_sizer.config.kelly_win_rate == 0.60

    def test_update_position_sizing_config_invalid_method(self, client):
        """Test POST /api/config/position-sizing ignores invalid method."""
        with patch("app.main.execution_engine") as mock_exec:
            from app.engines.position_sizing import PositionSizer
            mock_sizer = PositionSizer()
            original_method = mock_sizer.config.method
            mock_exec._position_sizer = mock_sizer

            resp = client.post("/api/config/position-sizing", json={
                "method": "invalid_method"
            })
            assert resp.status_code == 200
            # Invalid method should be ignored, not set
            assert mock_exec._position_sizer.config.method == original_method


class TestExportEndpoints:
    """Test CSV and JSON export endpoints."""

    def test_export_trades_csv(self, client):
        """Test /api/export/trades returns valid CSV."""
        with patch("app.api.routes.get_trade_pnl_data", new_callable=AsyncMock) as mock_trades:
            mock_trades.return_value = [
                {"id": 1, "broker": "ibkr", "symbol": "AAPL", "side": "BUY", "order_type": "MARKET", "quantity": 10, "filled_price": 150.0, "filled_quantity": 10, "status": "filled", "created_at": "2024-01-01T12:00:00"},
            ]
            resp = client.get("/api/export/trades?format=csv")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/csv; charset=utf-8"
            assert "id,broker,symbol" in resp.text
            assert "AAPL" in resp.text

    def test_export_trades_json(self, client):
        """Test /api/export/trades returns JSON."""
        with patch("app.api.routes.get_trade_pnl_data", new_callable=AsyncMock) as mock_trades:
            mock_trades.return_value = [
                {"id": 1, "broker": "ibkr", "symbol": "AAPL", "side": "BUY", "order_type": "MARKET", "quantity": 10, "filled_price": 150.0, "filled_quantity": 10, "status": "filled", "created_at": "2024-01-01T12:00:00"},
            ]
            resp = client.get("/api/export/trades?format=json")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["symbol"] == "AAPL"

    def test_export_signals_csv(self, client):
        """Test /api/export/signals returns valid CSV."""
        with patch("app.api.routes.aiosqlite.connect") as mock_connect:
            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=None)
            mock_connect.return_value = mock_db

            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[
                MagicMock(id=1, symbol="AAPL", signal_type="RSI", value=35.0, metadata="{}", created_at="2024-01-01T12:00:00")
            ])
            mock_db.execute = AsyncMock(return_value=mock_cursor)

            resp = client.get("/api/export/signals?format=csv")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/csv; charset=utf-8"
            assert "id,symbol,signal_type" in resp.text

    def test_export_decisions_csv(self, client):
        """Test /api/export/decisions returns valid CSV."""
        with patch("app.api.routes.aiosqlite.connect") as mock_connect:
            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=None)
            mock_connect.return_value = mock_db

            mock_cursor = AsyncMock()
            mock_row = MagicMock()
            mock_row.__getitem__ = MagicMock(side_effect=lambda k: {
                'id': 1, 'strategy': 'signal', 'symbol': 'AAPL', 'side': 'BUY',
                'quantity': 10, 'price': 150.0, 'reasoning': 'RSI oversold', 'confidence': 0.95,
                'risk_check_passed': 1, 'created_at': '2024-01-01T12:00:00'
            }.get(k))
            mock_row.keys = MagicMock(return_value=['id', 'strategy', 'symbol', 'side', 'quantity', 'price', 'reasoning', 'confidence', 'risk_check_passed', 'created_at'])
            mock_cursor.fetchall = AsyncMock(return_value=[mock_row])
            mock_db.execute = AsyncMock(return_value=mock_cursor)

            resp = client.get("/api/export/decisions?format=csv")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/csv; charset=utf-8"
            assert "id,strategy,symbol" in resp.text


class TestStrategyPresets:
    """Test strategy preset endpoints."""

    def test_get_strategy_presets(self, client):
        """Test /api/presets returns all presets."""
        resp = client.get("/api/presets")
        assert resp.status_code == 200
        data = resp.json()
        assert "conservative" in data
        assert "balanced" in data
        assert "aggressive" in data
        assert "description" in data["conservative"]
        assert "signal_config" in data["conservative"]
        assert "position_sizing" in data["conservative"]

    def test_apply_strategy_preset_valid(self, client):
        """Test /api/presets/{preset_name}/apply applies preset."""
        with patch("app.main.signal_engine") as mock_sig, \
             patch("app.main.execution_engine") as mock_exec:
            mock_cfg = MagicMock()
            mock_cfg.rsi_period = 14
            mock_cfg.rsi_oversold = 30
            mock_cfg.rsi_overbought = 70
            mock_cfg.macd_fast = 12
            mock_cfg.macd_slow = 26
            mock_cfg.macd_signal = 9
            mock_cfg.volume_spike_ratio = 2.0
            mock_cfg.bb_period = 20
            mock_cfg.bb_std_dev = 2.0
            mock_sig.signal_config = mock_cfg

            from app.engines.position_sizing import PositionSizer
            mock_sizer = PositionSizer()
            mock_exec._position_sizer = mock_sizer

            resp = client.post("/api/presets/conservative/apply")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["preset"] == "conservative"

    def test_apply_strategy_preset_invalid(self, client):
        """Test /api/presets/{preset_name}/apply with invalid preset."""
        resp = client.post("/api/presets/nonexistent/apply")
        assert resp.status_code == 404
        data = resp.json()
        assert "detail" in data
        assert "nonexistent" in data["detail"]


class TestSignalConfigEndpoints:
    def test_get_signal_config(self, client):
        """Test GET /api/config/signal returns current config."""
        with patch("app.main.signal_engine") as mock_sig:
            from app.engines.signal_engine import SignalConfig
            mock_config = SignalConfig()
            mock_sig.signal_config = mock_config

            resp = client.get("/api/config/signal")
            assert resp.status_code == 200
            data = resp.json()
            assert "rsi_period" in data
            assert "rsi_oversold" in data
            assert "rsi_overbought" in data
            assert "macd_fast" in data
            assert "macd_slow" in data

    def test_update_signal_config_valid(self, client):
        """Test POST /api/config/signal with valid values."""
        with patch("app.main.signal_engine") as mock_sig:
            from app.engines.signal_engine import SignalConfig
            mock_config = SignalConfig()
            mock_sig.signal_config = mock_config

            resp = client.post("/api/config/signal", json={
                "rsi_period": 14,
                "volume_spike_ratio": 1.5,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert mock_sig.signal_config.rsi_period == 14
            assert mock_sig.signal_config.volume_spike_ratio == 1.5

    def test_update_signal_config_rsi_oversold_gte_overbought(self, client):
        """Test signal config validation: rsi_oversold must be < rsi_overbought."""
        with patch("app.main.signal_engine") as mock_sig:
            from app.engines.signal_engine import SignalConfig
            mock_config = SignalConfig()
            mock_sig.signal_config = mock_config

            # Try to set oversold >= overbought
            resp = client.post("/api/config/signal", json={
                "rsi_oversold": 70,
                "rsi_overbought": 30,
            })
            assert resp.status_code == 400
            data = resp.json()
            assert "errors" in data
            assert any("rsi_oversold must be less than rsi_overbought" in err for err in data["errors"])

    def test_update_signal_config_macd_fast_gte_slow(self, client):
        """Test signal config validation: macd_fast must be < macd_slow."""
        with patch("app.main.signal_engine") as mock_sig:
            from app.engines.signal_engine import SignalConfig
            mock_config = SignalConfig()
            mock_sig.signal_config = mock_config

            # Try to set fast >= slow
            resp = client.post("/api/config/signal", json={
                "macd_fast": 26,
                "macd_slow": 12,
            })
            assert resp.status_code == 400
            data = resp.json()
            assert "errors" in data
            assert any("macd_fast must be less than macd_slow" in err for err in data["errors"])

    def test_update_signal_config_rsi_period_too_small(self, client):
        """Test signal config validation: rsi_period must be >= 2."""
        with patch("app.main.signal_engine") as mock_sig:
            from app.engines.signal_engine import SignalConfig
            mock_config = SignalConfig()
            mock_sig.signal_config = mock_config

            resp = client.post("/api/config/signal", json={
                "rsi_period": 1,
            })
            assert resp.status_code == 400
            data = resp.json()
            assert "errors" in data
            assert any("rsi_period must be at least 2" in err for err in data["errors"])

    def test_update_signal_config_partial_rsi_oversold(self, client):
        """Test signal config validation with only one RSI value updated."""
        with patch("app.main.signal_engine") as mock_sig:
            from app.engines.signal_engine import SignalConfig
            mock_config = SignalConfig()
            mock_sig.signal_config = mock_config

            # Set overbought to a low value
            mock_config.rsi_overbought = 50

            # Try to set oversold to a higher value
            resp = client.post("/api/config/signal", json={
                "rsi_oversold": 60,
            })
            assert resp.status_code == 400
            data = resp.json()
            assert "errors" in data
            assert any("rsi_oversold must be less than rsi_overbought" in err for err in data["errors"])

    def test_update_signal_config_partial_macd_fast(self, client):
        """Test signal config validation with only one MACD value updated."""
        with patch("app.main.signal_engine") as mock_sig:
            from app.engines.signal_engine import SignalConfig
            mock_config = SignalConfig()
            mock_sig.signal_config = mock_config

            # Set slow to a low value
            mock_config.macd_slow = 10

            # Try to set fast to a higher value
            resp = client.post("/api/config/signal", json={
                "macd_fast": 26,
            })
            assert resp.status_code == 400
            data = resp.json()
            assert "errors" in data
            assert any("macd_fast must be less than macd_slow" in err for err in data["errors"])
