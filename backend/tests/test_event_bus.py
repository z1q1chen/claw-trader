from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
from datetime import datetime, timezone

import pytest

from app.core.events import Event, EventBus


class TestEvent:
    """Tests for the Event dataclass."""

    def test_event_creates_with_type_and_data(self) -> None:
        """Event creates with type and data, auto-generates timestamp."""
        event = Event(type="test_event", data={"key": "value"})

        assert event.type == "test_event"
        assert event.data == {"key": "value"}
        assert event.timestamp is not None

    def test_event_timestamp_is_valid_iso_format(self) -> None:
        """Event timestamp is a valid ISO format datetime string."""
        event = Event(type="test_event", data={"key": "value"})

        # Should not raise an exception
        parsed_time = datetime.fromisoformat(event.timestamp)
        assert parsed_time is not None

    def test_event_default_data_is_empty_dict(self) -> None:
        """Event default data is empty dict when not provided."""
        event = Event(type="test_event")

        assert event.data == {}
        assert isinstance(event.data, dict)

    def test_event_timestamp_includes_timezone(self) -> None:
        """Event timestamp includes timezone info (UTC)."""
        event = Event(type="test_event")

        # ISO format with timezone info ends with +00:00
        assert event.timestamp.endswith("+00:00")


class TestEventBusSubscribePublish:
    """Tests for EventBus subscribe and publish functionality."""

    @pytest.mark.asyncio
    async def test_subscribe_and_publish_handler_receives_event(self) -> None:
        """Handler receives the event when it's published."""
        bus = EventBus()
        received_events = []

        async def handler(event: Event) -> None:
            received_events.append(event)

        bus.subscribe("test_event", handler)
        event = Event(type="test_event", data={"key": "value"})
        await bus.publish(event)

        assert len(received_events) == 1
        assert received_events[0].type == "test_event"
        assert received_events[0].data == {"key": "value"}

    @pytest.mark.asyncio
    async def test_multiple_handlers_for_same_event_type_all_get_called(self) -> None:
        """Multiple handlers for same event type all get called."""
        bus = EventBus()
        received_events_1 = []
        received_events_2 = []

        async def handler1(event: Event) -> None:
            received_events_1.append(event)

        async def handler2(event: Event) -> None:
            received_events_2.append(event)

        bus.subscribe("test_event", handler1)
        bus.subscribe("test_event", handler2)
        event = Event(type="test_event", data={"key": "value"})
        await bus.publish(event)

        assert len(received_events_1) == 1
        assert len(received_events_2) == 1
        assert received_events_1[0] == received_events_2[0]

    @pytest.mark.asyncio
    async def test_handlers_for_different_event_types_dont_cross_fire(self) -> None:
        """Handlers for different event types don't cross-fire."""
        bus = EventBus()
        received_events_1 = []
        received_events_2 = []

        async def handler1(event: Event) -> None:
            received_events_1.append(event)

        async def handler2(event: Event) -> None:
            received_events_2.append(event)

        bus.subscribe("event_type_1", handler1)
        bus.subscribe("event_type_2", handler2)

        event1 = Event(type="event_type_1", data={"type": "1"})
        event2 = Event(type="event_type_2", data={"type": "2"})

        await bus.publish(event1)
        await bus.publish(event2)

        assert len(received_events_1) == 1
        assert len(received_events_2) == 1
        assert received_events_1[0].type == "event_type_1"
        assert received_events_2[0].type == "event_type_2"

    @pytest.mark.asyncio
    async def test_handler_exceptions_are_caught(self) -> None:
        """Handler exceptions are caught and don't crash the bus."""
        bus = EventBus()
        received_events = []

        async def failing_handler(event: Event) -> None:
            raise ValueError("Handler error!")

        async def good_handler(event: Event) -> None:
            received_events.append(event)

        bus.subscribe("test_event", failing_handler)
        bus.subscribe("test_event", good_handler)

        event = Event(type="test_event", data={"key": "value"})

        # Should not raise an exception
        await bus.publish(event)

        # Good handler should still be called
        assert len(received_events) == 1

    @pytest.mark.asyncio
    async def test_publish_with_no_subscribers(self) -> None:
        """Publish works with no subscribers for that event type."""
        bus = EventBus()
        event = Event(type="test_event", data={"key": "value"})

        # Should not raise an exception
        await bus.publish(event)


class TestEventBusWebSocketClients:
    """Tests for EventBus WebSocket client functionality."""

    @pytest.mark.asyncio
    async def test_register_ws_client_and_publish_receives_json_message(self) -> None:
        """WebSocket queue receives JSON message after registering and publishing."""
        bus = EventBus()
        queue = asyncio.Queue()
        bus.register_ws_client(queue)

        event = Event(type="test_event", data={"key": "value"})
        await bus.publish(event)

        # Check the queue received the message
        message = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(message, str)

        # Parse and verify the message
        data = json.loads(message)
        assert data["type"] == "test_event"
        assert data["data"] == {"key": "value"}
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_unregister_ws_client_stops_receiving(self) -> None:
        """Queue stops receiving after unregister."""
        bus = EventBus()
        queue = asyncio.Queue()
        bus.register_ws_client(queue)
        bus.unregister_ws_client(queue)

        event = Event(type="test_event", data={"key": "value"})
        await bus.publish(event)

        # Queue should be empty (no messages received)
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_full_queue_removes_client(self) -> None:
        """Client is removed from ws_clients when queue is full (QueueFull)."""
        bus = EventBus()
        # Create a queue with size 1, so it will fill up
        queue = asyncio.Queue(maxsize=1)
        bus.register_ws_client(queue)

        # Fill the queue
        event1 = Event(type="event1", data={"msg": "first"})
        await bus.publish(event1)

        # Now the queue is full; publishing another event should trigger QueueFull
        event2 = Event(type="event2", data={"msg": "second"})
        await bus.publish(event2)

        # Client should have been removed
        assert queue not in bus._ws_clients

    @pytest.mark.asyncio
    async def test_multiple_ws_clients_all_receive_broadcast(self) -> None:
        """Multiple ws clients all receive the broadcast."""
        bus = EventBus()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        queue3 = asyncio.Queue()

        bus.register_ws_client(queue1)
        bus.register_ws_client(queue2)
        bus.register_ws_client(queue3)

        event = Event(type="test_event", data={"msg": "broadcast"})
        await bus.publish(event)

        # All queues should receive the message
        msg1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        msg2 = await asyncio.wait_for(queue2.get(), timeout=1.0)
        msg3 = await asyncio.wait_for(queue3.get(), timeout=1.0)

        data1 = json.loads(msg1)
        data2 = json.loads(msg2)
        data3 = json.loads(msg3)

        assert data1["type"] == "test_event"
        assert data2["type"] == "test_event"
        assert data3["type"] == "test_event"
        assert data1["data"]["msg"] == "broadcast"
        assert data2["data"]["msg"] == "broadcast"
        assert data3["data"]["msg"] == "broadcast"

    @pytest.mark.asyncio
    async def test_partial_queue_full_scenario(self) -> None:
        """Some clients fill up while others don't, only full ones removed."""
        bus = EventBus()
        queue_small = asyncio.Queue(maxsize=1)  # Will fill up
        queue_large = asyncio.Queue(maxsize=100)  # Won't fill up

        bus.register_ws_client(queue_small)
        bus.register_ws_client(queue_large)

        assert len(bus._ws_clients) == 2

        # Publish to fill the small queue
        event1 = Event(type="event1", data={"msg": "first"})
        await bus.publish(event1)

        # Publish again to trigger QueueFull on small queue
        event2 = Event(type="event2", data={"msg": "second"})
        await bus.publish(event2)

        # Small queue should be removed, large queue should remain
        assert len(bus._ws_clients) == 1
        assert queue_large in bus._ws_clients
        assert queue_small not in bus._ws_clients

    @pytest.mark.asyncio
    async def test_ws_message_format_includes_all_event_fields(self) -> None:
        """WebSocket message includes all event fields."""
        bus = EventBus()
        queue = asyncio.Queue()
        bus.register_ws_client(queue)

        event = Event(type="test_event", data={"key1": "value1", "key2": 42})
        await bus.publish(event)

        message = await asyncio.wait_for(queue.get(), timeout=1.0)
        data = json.loads(message)

        assert "type" in data
        assert "data" in data
        assert "timestamp" in data
        assert data["type"] == "test_event"
        assert data["data"]["key1"] == "value1"
        assert data["data"]["key2"] == 42


class TestEventBusIntegration:
    """Integration tests for EventBus."""

    @pytest.mark.asyncio
    async def test_handlers_and_ws_clients_both_receive_events(self) -> None:
        """Both handlers and ws clients receive events independently."""
        bus = EventBus()
        received_events = []
        queue = asyncio.Queue()

        async def handler(event: Event) -> None:
            received_events.append(event)

        bus.subscribe("test_event", handler)
        bus.register_ws_client(queue)

        event = Event(type="test_event", data={"msg": "test"})
        await bus.publish(event)

        assert len(received_events) == 1
        message = await asyncio.wait_for(queue.get(), timeout=1.0)
        data = json.loads(message)
        assert data["type"] == "test_event"

    @pytest.mark.asyncio
    async def test_multiple_events_maintain_order(self) -> None:
        """Multiple events maintain publishing order."""
        bus = EventBus()
        queue = asyncio.Queue()
        bus.register_ws_client(queue)

        events = [
            Event(type="event1", data={"seq": 1}),
            Event(type="event2", data={"seq": 2}),
            Event(type="event3", data={"seq": 3}),
        ]

        for event in events:
            await bus.publish(event)

        received_data = []
        for _ in range(3):
            message = await asyncio.wait_for(queue.get(), timeout=1.0)
            data = json.loads(message)
            received_data.append(data)

        assert received_data[0]["data"]["seq"] == 1
        assert received_data[1]["data"]["seq"] == 2
        assert received_data[2]["data"]["seq"] == 3
