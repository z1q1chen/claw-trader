from __future__ import annotations

import hashlib
import secrets
from typing import Callable

from fastapi import Request

from app.core.config import settings
from app.core.logging import logger


def generate_api_key() -> str:
    """Generate a random API key."""
    return f"ct_{secrets.token_urlsafe(32)}"


def hash_api_key(key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


async def verify_request(request: Request) -> bool:
    """Verify request has valid authentication."""
    # If no API key is configured, allow all requests (development mode)
    if not settings.api_secret_key:
        return True

    # Check Authorization header
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if hash_api_key(token) == hash_api_key(settings.api_secret_key):
            return True

    # Check X-API-Key header
    api_key = request.headers.get("x-api-key", "")
    if api_key and hash_api_key(api_key) == hash_api_key(settings.api_secret_key):
        return True

    return False
