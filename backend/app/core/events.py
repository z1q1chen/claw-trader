from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Literal

from app.core.logging import logger

EventType = Literal[
    "signal",
    "order_executed",
    "order_failed",
    "order_cancelled",
    "trade_rejected",
    "llm_config_changed",
    "kill_switch_toggle",
    "risk_config_updated",
]


@dataclass
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """In-process async event bus for decoupling signal -> brain -> risk -> execution."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._ws_clients: set[asyncio.Queue] = set()

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def register_ws_client(self, queue: asyncio.Queue) -> None:
        self._ws_clients.add(queue)

    def unregister_ws_client(self, queue: asyncio.Queue) -> None:
        self._ws_clients.discard(queue)

    async def publish(self, event: Event) -> None:
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"Event handler error for {event.type}: {e}")

        msg = json.dumps(asdict(event))
        dead_clients = []
        for queue in self._ws_clients:
            try:
                # Handle backpressure: drop oldest messages if queue exceeds threshold
                if queue.qsize() > 500:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                dead_clients.append(queue)
        for client in dead_clients:
            self._ws_clients.discard(client)


event_bus = EventBus()
