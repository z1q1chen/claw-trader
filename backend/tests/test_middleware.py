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
