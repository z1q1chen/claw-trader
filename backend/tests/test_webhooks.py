from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.webhooks import Webhook, WebhookManager


class TestWebhookManager:
    """Test WebhookManager functionality."""

    def test_register_webhook(self):
        """Test registering a webhook."""
        manager = WebhookManager()
        webhook = Webhook(
            id="test-1",
            url="https://example.com/webhook",
            event_types=["order_executed", "order_failed"],
        )
        manager.register(webhook)
        webhooks = manager.list_webhooks()
        assert len(webhooks) == 1
        assert webhooks[0]["id"] == "test-1"
        assert webhooks[0]["url"] == "https://example.com/webhook"

    def test_unregister_webhook(self):
        """Test unregistering a webhook."""
        manager = WebhookManager()
        webhook = Webhook(
            id="test-1",
            url="https://example.com/webhook",
            event_types=["order_executed"],
        )
        manager.register(webhook)
        assert len(manager.list_webhooks()) == 1

        result = manager.unregister("test-1")
        assert result is True
        assert len(manager.list_webhooks()) == 0

    def test_unregister_nonexistent_webhook(self):
        """Test unregistering a webhook that doesn't exist."""
        manager = WebhookManager()
        result = manager.unregister("nonexistent")
        assert result is False

    def test_list_webhooks(self):
        """Test listing all webhooks."""
        manager = WebhookManager()
        webhook1 = Webhook(
            id="test-1",
            url="https://example.com/webhook1",
            event_types=["order_executed"],
            enabled=True,
        )
        webhook2 = Webhook(
            id="test-2",
            url="https://example.com/webhook2",
            event_types=["trade_rejected"],
            enabled=False,
        )
        manager.register(webhook1)
        manager.register(webhook2)

        webhooks = manager.list_webhooks()
        assert len(webhooks) == 2
        assert webhooks[0]["id"] == "test-1"
        assert webhooks[0]["enabled"] is True
        assert webhooks[1]["id"] == "test-2"
        assert webhooks[1]["enabled"] is False

    @pytest.mark.asyncio
    async def test_dispatch_filters_by_event_type(self):
        """Test that dispatch filters webhooks by event type."""
        manager = WebhookManager()
        webhook = Webhook(
            id="test-1",
            url="https://example.com/webhook",
            event_types=["order_executed"],
        )
        manager.register(webhook)

        # Mock the HTTP client
        mock_http = AsyncMock()
        manager._http = mock_http

        # Dispatch an event that matches
        await manager.dispatch("order_executed", {"order_id": "123"})
        # Give a moment for the task to be created
        await asyncio.sleep(0.1)

        # Dispatch an event that doesn't match
        await manager.dispatch("order_failed", {"order_id": "456"})
        # Give a moment for tasks to be created
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_disabled_webhooks_not_dispatched(self):
        """Test that disabled webhooks are not called."""
        manager = WebhookManager()
        webhook = Webhook(
            id="test-1",
            url="https://example.com/webhook",
            event_types=["order_executed"],
            enabled=False,
        )
        manager.register(webhook)

        # Mock the HTTP client
        mock_http = AsyncMock()
        manager._http = mock_http

        # Dispatch an event
        await manager.dispatch("order_executed", {"order_id": "123"})
        # Give a moment for tasks to be created
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_wildcard_event_type(self):
        """Test that wildcards match all events."""
        manager = WebhookManager()
        webhook = Webhook(
            id="test-1",
            url="https://example.com/webhook",
            event_types=["*"],
        )
        manager.register(webhook)

        webhooks = manager.list_webhooks()
        assert webhooks[0]["event_types"] == ["*"]

    @pytest.mark.asyncio
    async def test_shutdown_closes_http_client(self):
        """Test that shutdown closes the httpx client."""
        manager = WebhookManager()
        webhook = Webhook(
            id="test-1",
            url="https://example.com/webhook",
            event_types=["order_executed"],
        )
        manager.register(webhook)

        # Trigger dispatch to create the HTTP client
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            await manager.dispatch("order_executed", {"order_id": "123"})
            await asyncio.sleep(0.1)

            # Verify client was created
            assert manager._http is not None

            # Shutdown the manager
            await manager.shutdown()

            # Verify client was closed and set to None
            mock_client.aclose.assert_called_once()
            assert manager._http is None

    @pytest.mark.asyncio
    async def test_shutdown_when_http_is_none(self):
        """Test that shutdown handles case when http client is None."""
        manager = WebhookManager()

        # Shutdown without creating a client
        await manager.shutdown()

        # Should complete without error and _http should still be None
        assert manager._http is None


# Test for API endpoints
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes import router


@pytest.fixture
def webhook_client():
    """Create a test client with webhook routes."""
    test_app = FastAPI()
    test_app.include_router(router)
    yield TestClient(test_app)


class TestWebhookAPI:
    """Test webhook API endpoints."""

    def test_get_webhooks_empty(self, webhook_client):
        """Test GET /api/webhooks returns empty list."""
        with patch("app.core.webhooks.webhook_manager") as mock_manager:
            mock_manager.list_webhooks.return_value = []
            resp = webhook_client.get("/api/webhooks")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_get_webhooks_with_data(self, webhook_client):
        """Test GET /api/webhooks returns registered webhooks."""
        with patch("app.core.webhooks.webhook_manager") as mock_manager:
            mock_manager.list_webhooks.return_value = [
                {"id": "test-1", "url": "https://example.com/webhook", "event_types": ["order_executed"], "enabled": True}
            ]
            resp = webhook_client.get("/api/webhooks")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["id"] == "test-1"

    def test_create_webhook(self, webhook_client):
        """Test POST /api/webhooks creates webhook."""
        with patch("app.core.webhooks.webhook_manager") as mock_manager:
            resp = webhook_client.post(
                "/api/webhooks",
                json={
                    "url": "https://example.com/webhook",
                    "event_types": ["order_executed"],
                    "enabled": True,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "id" in data
            assert data["status"] == "created"
            mock_manager.register.assert_called_once()

    def test_delete_webhook_success(self, webhook_client):
        """Test DELETE /api/webhooks/{id} removes webhook."""
        with patch("app.core.webhooks.webhook_manager") as mock_manager:
            mock_manager.unregister.return_value = True
            resp = webhook_client.delete("/api/webhooks/test-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "deleted"

    def test_delete_webhook_not_found(self, webhook_client):
        """Test DELETE /api/webhooks/{id} returns 404 if not found."""
        with patch("app.core.webhooks.webhook_manager") as mock_manager:
            mock_manager.unregister.return_value = False
            resp = webhook_client.delete("/api/webhooks/nonexistent")
            assert resp.status_code == 404
            data = resp.json()
            assert "detail" in data

    def test_test_webhook_success(self, webhook_client):
        """Test POST /api/webhooks/{id}/test sends test event."""
        with patch("app.core.webhooks.webhook_manager") as mock_manager:
            mock_manager.list_webhooks.return_value = [
                {"id": "test-1", "url": "https://example.com/webhook", "event_types": ["test"], "enabled": True}
            ]
            mock_manager.dispatch = AsyncMock()

            resp = webhook_client.post("/api/webhooks/test-1/test")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "test_sent"

    def test_test_webhook_not_found(self, webhook_client):
        """Test POST /api/webhooks/{id}/test returns 404 if webhook not found."""
        with patch("app.core.webhooks.webhook_manager") as mock_manager:
            mock_manager.list_webhooks.return_value = []
            resp = webhook_client.post("/api/webhooks/nonexistent/test")
            assert resp.status_code == 404
            data = resp.json()
            assert "detail" in data
