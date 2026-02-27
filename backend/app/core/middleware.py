from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import logger


# Public paths that don't require authentication
_PUBLIC_PATHS = {"/api/health", "/ws", "/docs", "/openapi.json", "/redoc"}


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
        from app.core.config import settings

        # Skip rate limiting for WebSocket and health
        if request.url.path in ("/ws", "/api/health"):
            return await call_next(request)

        # Only honor X-Forwarded-For if trust_proxy_headers is explicitly enabled
        if settings.trust_proxy_headers:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                client_ip = forwarded.split(",")[0].strip()
            else:
                client_ip = request.client.host if request.client else "unknown"
        else:
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
        response = await call_next(request)

        if len(self._request_counts) > 10000:
            self._request_counts = {
                ip: timestamps for ip, timestamps in self._request_counts.items()
                if timestamps and any(now - t < self.window_seconds for t in timestamps)
            }

        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware that checks API key on protected endpoints."""

    async def dispatch(self, request: Request, call_next):
        from app.core.config import settings

        # Skip auth if not enabled
        if not settings.auth_enabled or not settings.api_secret_key:
            return await call_next(request)

        # Allow public paths
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/docs"):
            return await call_next(request)

        # Allow OPTIONS for CORS preflight
        if request.method == "OPTIONS":
            return await call_next(request)

        from app.core.auth import verify_request
        if not await verify_request(request):
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})

        return await call_next(request)
