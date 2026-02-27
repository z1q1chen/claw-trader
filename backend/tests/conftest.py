from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.engines.risk_engine import RiskEngine
from app.engines.execution_engine import ExecutionEngine, BrokerAdapter, OrderResult
from app.engines.llm_brain import TradeAction
from app.core.events import EventBus, Event


@pytest.fixture
def risk_engine():
    return RiskEngine()


@pytest.fixture
def execution_engine(risk_engine):
    return ExecutionEngine(risk_engine)


@pytest.fixture
def sample_trade_action():
    return TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        reasoning="Test trade",
        confidence=0.85,
        strategy="test_strategy",
    )


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def sample_signal_event():
    return Event(
        type="signal",
        data={
            "symbol": "AAPL",
            "signal_type": "rsi_oversold",
            "value": 25.0,
            "price": 150.0,
            "metadata": {"threshold": 30},
        },
    )


class FakeBrokerAdapter(BrokerAdapter):
    """Reusable fake broker for tests."""

    def __init__(self, order_result=None, positions=None, balance=None):
        self._order_result = order_result or OrderResult(
            success=True, broker_order_id="TEST-001",
            filled_price=150.0, filled_quantity=10.0,
        )
        self._positions = positions or {}
        self._balance = balance or {}
        self.last_order_call = None

    async def place_order(self, symbol, side, quantity, order_type="MARKET", limit_price=None):
        self.last_order_call = {"symbol": symbol, "side": side, "quantity": quantity}
        return self._order_result

    async def get_positions(self):
        return self._positions

    async def get_balance(self):
        return self._balance

    async def get_order_history(self, limit=50):
        return []

    async def cancel_order(self, order_id):
        return True


@pytest.fixture
def fake_broker():
    return FakeBrokerAdapter()


@pytest.fixture
def db_mocks():
    """Context manager that mocks all database calls in execution_engine."""
    with patch("app.engines.execution_engine.log_trade_decision", new_callable=AsyncMock, return_value=1) as log_dec, \
         patch("app.engines.execution_engine.log_order", new_callable=AsyncMock, return_value=100) as log_ord, \
         patch("app.engines.execution_engine.update_order_status", new_callable=AsyncMock) as upd_ord, \
         patch("app.engines.execution_engine.mark_decision_executed", new_callable=AsyncMock) as mark_exec, \
         patch("app.engines.execution_engine.event_bus.publish", new_callable=AsyncMock) as pub:
        yield {
            "log_trade_decision": log_dec,
            "log_order": log_ord,
            "update_order_status": upd_ord,
            "mark_decision_executed": mark_exec,
            "event_bus_publish": pub,
        }
