from __future__ import annotations

import hmac
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.auth import generate_api_key, hash_api_key, verify_request
from app.core.middleware import AuthMiddleware


class TestApiKeyGeneration:
    def test_generate_api_key_format(self):
        key = generate_api_key()
        assert key.startswith("ct_")
        assert len(key) > 10

    def test_generate_api_key_uniqueness(self):
        key1 = generate_api_key()
        key2 = generate_api_key()
        assert key1 != key2

    def test_hash_api_key(self):
        key = "test_key"
        hash1 = hash_api_key(key)
        hash2 = hash_api_key(key)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest

    def test_hash_api_key_different_inputs(self):
        hash1 = hash_api_key("key1")
        hash2 = hash_api_key("key2")
        assert hash1 != hash2


@pytest.mark.asyncio
class TestVerifyRequest:
    async def test_verify_request_no_api_key_configured(self):
        request = MagicMock()
        request.headers.get = MagicMock(return_value="")

        with patch("app.core.auth.settings") as mock_settings:
            mock_settings.api_secret_key = ""
            result = await verify_request(request)
            assert result is True

    async def test_verify_request_bearer_token_valid(self):
        test_key = "test_secret_key"
        request = MagicMock()
        request.headers.get = MagicMock(side_effect=lambda h, default="": f"Bearer {test_key}" if h == "authorization" else "")

        with patch("app.core.auth.settings") as mock_settings:
            mock_settings.api_secret_key = test_key
            result = await verify_request(request)
            assert result is True

    async def test_verify_request_bearer_token_invalid(self):
        request = MagicMock()
        request.headers.get = MagicMock(side_effect=lambda h, default="": "Bearer invalid_token" if h == "authorization" else "")

        with patch("app.core.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "correct_key"
            result = await verify_request(request)
            assert result is False

    async def test_verify_request_x_api_key_valid(self):
        test_key = "my_api_key"
        request = MagicMock()
        request.headers.get = MagicMock(side_effect=lambda h, default="": test_key if h == "x-api-key" else "")

        with patch("app.core.auth.settings") as mock_settings:
            mock_settings.api_secret_key = test_key
            result = await verify_request(request)
            assert result is True

    async def test_verify_request_x_api_key_invalid(self):
        request = MagicMock()
        request.headers.get = MagicMock(side_effect=lambda h, default="": "wrong_key" if h == "x-api-key" else "")

        with patch("app.core.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "correct_key"
            result = await verify_request(request)
            assert result is False

    async def test_verify_request_no_headers(self):
        request = MagicMock()
        request.headers.get = MagicMock(return_value="")

        with patch("app.core.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "required_key"
            result = await verify_request(request)
            assert result is False

    async def test_verify_request_uses_hmac_compare_digest(self):
        """Test that timing-safe comparison (hmac.compare_digest) is used."""
        test_key = "test_secret_key"
        request = MagicMock()
        request.headers.get = MagicMock(side_effect=lambda h, default="": f"Bearer {test_key}" if h == "authorization" else "")

        with patch("app.core.auth.settings") as mock_settings, \
             patch("app.core.auth.hmac.compare_digest", wraps=hmac.compare_digest) as mock_compare_digest:
            mock_settings.api_secret_key = test_key
            result = await verify_request(request)
            assert result is True
            mock_compare_digest.assert_called_once()

    async def test_verify_request_bearer_and_x_api_key_both_work(self):
        """Test that both Bearer token and X-API-Key headers work correctly."""
        test_key = "valid_key"

        # Test Bearer token
        request_bearer = MagicMock()
        request_bearer.headers.get = MagicMock(side_effect=lambda h, default="": f"Bearer {test_key}" if h == "authorization" else "")

        with patch("app.core.auth.settings") as mock_settings:
            mock_settings.api_secret_key = test_key
            result = await verify_request(request_bearer)
            assert result is True

        # Test X-API-Key
        request_api_key = MagicMock()
        request_api_key.headers.get = MagicMock(side_effect=lambda h, default="": test_key if h == "x-api-key" else "")

        with patch("app.core.auth.settings") as mock_settings:
            mock_settings.api_secret_key = test_key
            result = await verify_request(request_api_key)
            assert result is True


@pytest.mark.asyncio
class TestAuthMiddleware:
    async def test_auth_middleware_disabled_allows_all(self):
        app = MagicMock()
        middleware = AuthMiddleware(app)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        request = MagicMock()
        request.url.path = "/api/trade"
        request.method = "POST"
        request.headers.get = MagicMock(return_value="")

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.auth_enabled = False
            mock_settings.api_secret_key = ""
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200

    async def test_auth_middleware_allows_public_paths(self):
        app = MagicMock()
        middleware = AuthMiddleware(app)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        for path in ["/api/health", "/ws", "/docs", "/openapi.json", "/redoc"]:
            request = MagicMock()
            request.url.path = path
            request.method = "GET"

            with patch("app.core.config.settings") as mock_settings:
                mock_settings.auth_enabled = True
                mock_settings.api_secret_key = "secret"
                response = await middleware.dispatch(request, call_next)
                assert response.status_code == 200

    async def test_auth_middleware_allows_options_preflight(self):
        app = MagicMock()
        middleware = AuthMiddleware(app)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        request = MagicMock()
        request.url.path = "/api/trade"
        request.method = "OPTIONS"

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.auth_enabled = True
            mock_settings.api_secret_key = "secret"
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200

    async def test_auth_middleware_blocks_unauthenticated_protected_endpoint(self):
        app = MagicMock()
        middleware = AuthMiddleware(app)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        request = MagicMock()
        request.url.path = "/api/trade"
        request.method = "POST"
        request.headers.get = MagicMock(return_value="")

        with patch("app.core.config.settings") as mock_settings, \
             patch("app.core.auth.settings") as mock_auth_settings:
            mock_settings.auth_enabled = True
            mock_settings.api_secret_key = "secret"
            mock_auth_settings.api_secret_key = "secret"
            response = await middleware.dispatch(request, call_next)
            # The response should be a JSONResponse with status 401
            assert response.status_code == 401

    async def test_auth_middleware_allows_valid_bearer_token(self):
        app = MagicMock()
        middleware = AuthMiddleware(app)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        test_key = "valid_token"
        request = MagicMock()
        request.url.path = "/api/trade"
        request.method = "POST"

        def get_header(h, default=""):
            if h == "authorization":
                return f"Bearer {test_key}"
            return default

        request.headers.get = MagicMock(side_effect=get_header)

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.auth_enabled = True
            mock_settings.api_secret_key = test_key
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200

    async def test_auth_middleware_allows_valid_x_api_key(self):
        app = MagicMock()
        middleware = AuthMiddleware(app)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        test_key = "my_secret"
        request = MagicMock()
        request.url.path = "/api/trade"
        request.method = "POST"

        def get_header(h, default=""):
            if h == "x-api-key":
                return test_key
            return default

        request.headers.get = MagicMock(side_effect=get_header)

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.auth_enabled = True
            mock_settings.api_secret_key = test_key
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200
