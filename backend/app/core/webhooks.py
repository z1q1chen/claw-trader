from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.logging import logger


@dataclass
class Webhook:
    id: str
    url: str
    event_types: list[str]
    enabled: bool = True
    retry_count: int = 3


class WebhookManager:
    def __init__(self) -> None:
        self._webhooks: dict[str, Webhook] = {}
        self._http: httpx.AsyncClient | None = None

    def register(self, webhook: Webhook) -> None:
        self._webhooks[webhook.id] = webhook

    def unregister(self, webhook_id: str) -> bool:
        return self._webhooks.pop(webhook_id, None) is not None

    def list_webhooks(self) -> list[dict[str, Any]]:
        return [
            {"id": w.id, "url": w.url, "event_types": w.event_types, "enabled": w.enabled}
            for w in self._webhooks.values()
        ]

    async def dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)

        for webhook in self._webhooks.values():
            if not webhook.enabled:
                continue
            if event_type not in webhook.event_types and "*" not in webhook.event_types:
                continue

            asyncio.create_task(self._deliver(webhook, event_type, data))

    async def _deliver(self, webhook: Webhook, event_type: str, data: dict[str, Any]) -> None:
        payload = {"event": event_type, "data": data}
        for attempt in range(webhook.retry_count):
            try:
                if self._http is None:
                    self._http = httpx.AsyncClient(timeout=10.0)
                resp = await self._http.post(
                    webhook.url,
                    json=payload,
                    headers={"Content-Type": "application/json", "X-Webhook-Event": event_type},
                )
                if resp.status_code < 400:
                    logger.debug(f"Webhook delivered to {webhook.url}: {event_type}")
                    return
                logger.warning(f"Webhook {webhook.url} returned {resp.status_code} (attempt {attempt + 1})")
            except Exception as e:
                logger.warning(f"Webhook delivery failed to {webhook.url}: {e} (attempt {attempt + 1})")

            if attempt < webhook.retry_count - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

        logger.error(f"Webhook delivery permanently failed to {webhook.url} for {event_type}")

    async def shutdown(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


webhook_manager = WebhookManager()
