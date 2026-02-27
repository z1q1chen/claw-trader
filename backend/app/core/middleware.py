from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import logger


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter.

    Limits requests per client IP per time window.
    Skips rate limiting for WebSocket upgrades and health checks.
    Applies tighter limits to sensitive endpoints.
    """

    _SENSITIVE_PATHS = {"/api/trade", "/api/broker/connect", "/api/config/llm"}
    _SENSITIVE_RPM = 30

    def __init__(self, app, requests_per_minute: int = 120):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.window_seconds = 60
        self._request_counts: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for WebSocket and health
        if request.url.path in ("/ws", "/api/health"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()

        # Clean old entries
        self._request_counts[client_ip] = [
            t for t in self._request_counts[client_ip]
            if now - t < self.window_seconds
        ]

        # Determine rate limit based on endpoint sensitivity
        is_sensitive = any(
            request.url.path.startswith(path) for path in self._SENSITIVE_PATHS
        )
        limit = self._SENSITIVE_RPM if is_sensitive else self.requests_per_minute

        if len(self._request_counts[client_ip]) >= limit:
            logger.warning(f"Rate limit exceeded for {client_ip} on {request.url.path}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down."},
            )

        self._request_counts[client_ip].append(now)
        return await call_next(request)
