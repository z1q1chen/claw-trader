from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.middleware import RateLimitMiddleware


def make_request(path: str = "/api/test", client_host: str = "127.0.0.1"):
    request = MagicMock()
    request.url.path = path
    request.client.host = client_host
    return request


@pytest.mark.asyncio
async def test_rate_limit_allows_normal_traffic():
    app = MagicMock()
    middleware = RateLimitMiddleware(app, requests_per_minute=10)
    call_next = AsyncMock(return_value=MagicMock(status_code=200))

    request = make_request()
    for _ in range(10):
        response = await middleware.dispatch(request, call_next)
    assert call_next.call_count == 10


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_limit():
    app = MagicMock()
    middleware = RateLimitMiddleware(app, requests_per_minute=5)
    call_next = AsyncMock(return_value=MagicMock(status_code=200))

    request = make_request()
    responses = []
    for _ in range(7):
        response = await middleware.dispatch(request, call_next)
        responses.append(response)

    assert call_next.call_count == 5
    assert responses[5].status_code == 429
    assert responses[6].status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_skips_websocket():
    app = MagicMock()
    middleware = RateLimitMiddleware(app, requests_per_minute=1)
    call_next = AsyncMock(return_value=MagicMock(status_code=200))

    request = make_request(path="/ws")
    for _ in range(5):
        await middleware.dispatch(request, call_next)
    assert call_next.call_count == 5


@pytest.mark.asyncio
async def test_rate_limit_skips_health():
    app = MagicMock()
    middleware = RateLimitMiddleware(app, requests_per_minute=1)
    call_next = AsyncMock(return_value=MagicMock(status_code=200))

    request = make_request(path="/api/health")
    for _ in range(5):
        await middleware.dispatch(request, call_next)
    assert call_next.call_count == 5


@pytest.mark.asyncio
async def test_rate_limit_independent_per_ip():
    app = MagicMock()
    middleware = RateLimitMiddleware(app, requests_per_minute=2)
    call_next = AsyncMock(return_value=MagicMock(status_code=200))

    req1 = make_request(client_host="1.1.1.1")
    req2 = make_request(client_host="2.2.2.2")

    for _ in range(2):
        await middleware.dispatch(req1, call_next)
    for _ in range(2):
        await middleware.dispatch(req2, call_next)

    assert call_next.call_count == 4


@pytest.mark.asyncio
async def test_rate_limit_sensitive_path_lower_limit():
    """Sensitive endpoints should have tighter rate limit."""
    app = MagicMock()
    middleware = RateLimitMiddleware(app, requests_per_minute=100)
    call_next = AsyncMock(return_value=MagicMock(status_code=200))

    # Request to sensitive endpoint (/api/trade)
    sensitive_req = make_request(path="/api/trade")

    # Should allow up to _SENSITIVE_RPM (30) requests
    responses = []
    for _ in range(35):
        response = await middleware.dispatch(sensitive_req, call_next)
        responses.append(response)

    # First 30 should succeed (200), next ones should be 429
    successful = sum(1 for r in responses if r.status_code == 200)
    limited = sum(1 for r in responses if r.status_code == 429)

    assert successful == 30
    assert limited == 5


@pytest.mark.asyncio
async def test_rate_limit_non_sensitive_path_normal_limit():
    """Non-sensitive endpoints should use normal rate limit."""
    app = MagicMock()
    middleware = RateLimitMiddleware(app, requests_per_minute=5)
    call_next = AsyncMock(return_value=MagicMock(status_code=200))

    # Request to non-sensitive endpoint
    normal_req = make_request(path="/api/markets")

    responses = []
    for _ in range(7):
        response = await middleware.dispatch(normal_req, call_next)
        responses.append(response)

    # First 5 should succeed, next should be limited
    successful = sum(1 for r in responses if r.status_code == 200)
    limited = sum(1 for r in responses if r.status_code == 429)

    assert successful == 5
    assert limited == 2


class TestJSONFormatter:
    """Test JSON logging formatter."""

    def test_json_formatter_output_structure(self):
        import json as json_lib
        import logging
        from app.core.logging import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        parsed = json_lib.loads(output)

        assert "timestamp" in parsed
        assert "level" in parsed
        assert "logger" in parsed
        assert "message" in parsed
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test_logger"
        assert parsed["message"] == "Test message"

    def test_json_formatter_with_exception(self):
        import json as json_lib
        import logging
        from app.core.logging import JSONFormatter

        formatter = JSONFormatter()
        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=42,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)
        parsed = json_lib.loads(output)

        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
        assert "Test error" in parsed["exception"]
