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
             patch("app.main.execution_engine") as mock_exec:
            mock_sig._running = True
            mock_llm._provider = "gemini"
            mock_risk.kill_switch_active = False
            mock_exec._brokers = {"ibkr": MagicMock()}

            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "engines" in data
            assert "signal_engine" in data["engines"]


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

            resp = client.post("/api/orders/nonexistent/cancel")
            assert resp.status_code == 404


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
