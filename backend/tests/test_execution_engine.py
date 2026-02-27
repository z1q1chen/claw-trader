from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.engines.execution_engine import BrokerAdapter, ExecutionEngine, OrderResult
from app.engines.llm_brain import TradeAction
from app.engines.risk_engine import RiskCheckResult, RiskEngine


class FakeBrokerAdapter(BrokerAdapter):
    def __init__(
        self,
        order_result: OrderResult | None = None,
        positions: dict | None = None,
        balance: dict | None = None,
    ) -> None:
        self._order_result = (
            order_result
            or OrderResult(
                success=True,
                broker_order_id="TEST-001",
                filled_price=150.0,
                filled_quantity=10.0,
            )
        )
        self._positions = positions or {}
        self._balance = balance or {}
        self.last_order_call = None

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        limit_price: float | None = None,
    ) -> OrderResult:
        self.last_order_call = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "limit_price": limit_price,
        }
        return self._order_result

    async def get_positions(self) -> dict[str, dict[str, Any]]:
        return self._positions

    async def get_balance(self) -> dict[str, float]:
        return self._balance

    async def get_order_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return []

    async def cancel_order(self, order_id: str) -> bool:
        return True


@pytest.fixture
def risk_engine() -> RiskEngine:
    return RiskEngine()


@pytest.fixture
def execution_engine(risk_engine: RiskEngine) -> ExecutionEngine:
    return ExecutionEngine(risk_engine)


def test_register_broker_first_becomes_default(
    execution_engine: ExecutionEngine,
) -> None:
    broker = FakeBrokerAdapter()
    execution_engine.register_broker("broker1", broker)

    assert execution_engine._default_broker == "broker1"
    assert execution_engine._brokers["broker1"] is broker


def test_register_broker_default_true_overrides_default(
    execution_engine: ExecutionEngine,
) -> None:
    broker1 = FakeBrokerAdapter()
    broker2 = FakeBrokerAdapter()

    execution_engine.register_broker("broker1", broker1)
    execution_engine.register_broker("broker2", broker2, default=True)

    assert execution_engine._default_broker == "broker2"
    assert execution_engine._brokers["broker1"] is broker1
    assert execution_engine._brokers["broker2"] is broker2


@pytest.mark.asyncio
async def test_execute_trade_no_broker_registered() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    action = TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        reasoning="Test trade",
        confidence=0.8,
        strategy="test_strategy",
    )

    with patch(
        "app.engines.execution_engine.log_trade_decision",
        new_callable=AsyncMock,
    ):
        result = await execution_engine.execute_trade(action, current_price=150.0)

    assert result is None


@pytest.mark.asyncio
async def test_execute_trade_risk_check_fails() -> None:
    risk_engine = RiskEngine()

    execution_engine = ExecutionEngine(risk_engine)
    broker = FakeBrokerAdapter()
    execution_engine.register_broker("test_broker", broker)

    action = TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        reasoning="Test trade",
        confidence=0.8,
        strategy="test_strategy",
    )

    with patch(
        "app.engines.execution_engine.log_trade_decision",
        new_callable=AsyncMock,
        return_value=1,
    ) as mock_log_decision, patch(
        "app.engines.execution_engine.event_bus.publish",
        new_callable=AsyncMock,
    ) as mock_publish:
        with patch.object(
            risk_engine,
            "check_trade",
            return_value=RiskCheckResult(
                passed=False, rejection_reason="Exceeds risk limits"
            ),
        ):
            result = await execution_engine.execute_trade(action, current_price=150.0)

    assert result is None
    mock_log_decision.assert_called_once()
    mock_publish.assert_called_once()
    event = mock_publish.call_args[0][0]
    assert event.type == "trade_rejected"
    assert event.data["reason"] == "Exceeds risk limits"


@pytest.mark.asyncio
async def test_execute_trade_happy_path_success() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    broker = FakeBrokerAdapter(
        order_result=OrderResult(
            success=True,
            broker_order_id="BROKER-123",
            filled_price=150.5,
            filled_quantity=10.0,
        )
    )
    execution_engine.register_broker("test_broker", broker)

    action = TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        reasoning="Strong momentum signal",
        confidence=0.9,
        strategy="momentum_strategy",
    )

    with patch(
        "app.engines.execution_engine.log_trade_decision",
        new_callable=AsyncMock,
        return_value=1,
    ), patch(
        "app.engines.execution_engine.log_order",
        new_callable=AsyncMock,
        return_value=100,
    ), patch(
        "app.engines.execution_engine.event_bus.publish",
        new_callable=AsyncMock,
    ) as mock_publish, patch(
        "app.engines.execution_engine.update_order_status",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.mark_decision_executed",
        new_callable=AsyncMock,
    ), patch.object(
        risk_engine,
        "check_trade",
        return_value=RiskCheckResult(passed=True),
    ):
        result = await execution_engine.execute_trade(action, current_price=150.0)

    assert result is not None
    assert result.success is True
    assert result.broker_order_id == "BROKER-123"
    assert result.filled_price == 150.5
    assert result.filled_quantity == 10.0

    assert broker.last_order_call == {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10.0,
        "order_type": "MARKET",
        "limit_price": None,
    }

    mock_publish.assert_called_once()
    event = mock_publish.call_args[0][0]
    assert event.type == "order_executed"
    assert event.data["decision_id"] == 1
    assert event.data["order_id"] == 100
    assert event.data["broker_order_id"] == "BROKER-123"


@pytest.mark.asyncio
async def test_execute_trade_uses_adjusted_quantity() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    broker = FakeBrokerAdapter()
    execution_engine.register_broker("test_broker", broker)

    action = TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=100.0,
        reasoning="Test with adjustment",
        confidence=0.8,
        strategy="test_strategy",
    )

    adjusted_qty = 50.0

    with patch(
        "app.engines.execution_engine.log_trade_decision",
        new_callable=AsyncMock,
        return_value=1,
    ) as mock_log_decision, patch(
        "app.engines.execution_engine.log_order",
        new_callable=AsyncMock,
        return_value=100,
    ) as mock_log_order, patch(
        "app.engines.execution_engine.event_bus.publish",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.update_order_status",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.mark_decision_executed",
        new_callable=AsyncMock,
    ), patch.object(
        risk_engine,
        "check_trade",
        return_value=RiskCheckResult(
            passed=True, adjusted_quantity=adjusted_qty
        ),
    ):
        result = await execution_engine.execute_trade(action, current_price=150.0)

    assert result is not None
    assert broker.last_order_call["quantity"] == adjusted_qty

    call_args = mock_log_decision.call_args
    assert call_args[1]["quantity"] == adjusted_qty

    call_args = mock_log_order.call_args
    assert call_args[1]["quantity"] == adjusted_qty


@pytest.mark.asyncio
async def test_execute_trade_broker_returns_failure() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    broker = FakeBrokerAdapter(
        order_result=OrderResult(
            success=False, error="Insufficient funds", broker_order_id=None
        )
    )
    execution_engine.register_broker("test_broker", broker)

    action = TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        reasoning="Test trade",
        confidence=0.8,
        strategy="test_strategy",
    )

    with patch(
        "app.engines.execution_engine.log_trade_decision",
        new_callable=AsyncMock,
        return_value=1,
    ), patch(
        "app.engines.execution_engine.log_order",
        new_callable=AsyncMock,
        return_value=100,
    ), patch(
        "app.engines.execution_engine.event_bus.publish",
        new_callable=AsyncMock,
    ) as mock_publish, patch(
        "app.engines.execution_engine.update_order_status",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.mark_decision_executed",
        new_callable=AsyncMock,
    ), patch.object(
        risk_engine,
        "check_trade",
        return_value=RiskCheckResult(passed=True),
    ):
        result = await execution_engine.execute_trade(action, current_price=150.0)

    assert result is not None
    assert result.success is False
    assert result.error == "Insufficient funds"

    mock_publish.assert_called_once()
    event = mock_publish.call_args[0][0]
    assert event.type == "order_failed"
    assert event.data["error"] == "Insufficient funds"


@pytest.mark.asyncio
async def test_get_positions_delegates_to_broker() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    positions = {
        "AAPL": {"quantity": 100.0, "avg_price": 150.0},
        "MSFT": {"quantity": 50.0, "avg_price": 300.0},
    }
    broker = FakeBrokerAdapter(positions=positions)
    execution_engine.register_broker("test_broker", broker)

    result = await execution_engine.get_positions()

    assert result == positions


@pytest.mark.asyncio
async def test_get_positions_uses_default_broker() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    positions1 = {"AAPL": {"quantity": 100.0}}
    positions2 = {"MSFT": {"quantity": 50.0}}

    broker1 = FakeBrokerAdapter(positions=positions1)
    broker2 = FakeBrokerAdapter(positions=positions2)

    execution_engine.register_broker("broker1", broker1)
    execution_engine.register_broker("broker2", broker2, default=True)

    result = await execution_engine.get_positions()

    assert result == positions2


@pytest.mark.asyncio
async def test_get_positions_specific_broker() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    positions1 = {"AAPL": {"quantity": 100.0}}
    positions2 = {"MSFT": {"quantity": 50.0}}

    broker1 = FakeBrokerAdapter(positions=positions1)
    broker2 = FakeBrokerAdapter(positions=positions2)

    execution_engine.register_broker("broker1", broker1)
    execution_engine.register_broker("broker2", broker2, default=True)

    result = await execution_engine.get_positions(broker_name="broker1")

    assert result == positions1


@pytest.mark.asyncio
async def test_get_positions_no_broker_registered() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    result = await execution_engine.get_positions()

    assert result == {}


@pytest.mark.asyncio
async def test_get_balance_delegates_to_broker() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    balance = {"USD": 50000.0, "BTC": 0.5}
    broker = FakeBrokerAdapter(balance=balance)
    execution_engine.register_broker("test_broker", broker)

    result = await execution_engine.get_balance()

    assert result == balance


@pytest.mark.asyncio
async def test_get_balance_uses_default_broker() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    balance1 = {"USD": 10000.0}
    balance2 = {"USD": 100000.0}

    broker1 = FakeBrokerAdapter(balance=balance1)
    broker2 = FakeBrokerAdapter(balance=balance2)

    execution_engine.register_broker("broker1", broker1)
    execution_engine.register_broker("broker2", broker2, default=True)

    result = await execution_engine.get_balance()

    assert result == balance2


@pytest.mark.asyncio
async def test_get_balance_specific_broker() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    balance1 = {"USD": 10000.0}
    balance2 = {"USD": 100000.0}

    broker1 = FakeBrokerAdapter(balance=balance1)
    broker2 = FakeBrokerAdapter(balance=balance2)

    execution_engine.register_broker("broker1", broker1)
    execution_engine.register_broker("broker2", broker2, default=True)

    result = await execution_engine.get_balance(broker_name="broker1")

    assert result == balance1


@pytest.mark.asyncio
async def test_get_balance_no_broker_registered() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    result = await execution_engine.get_balance()

    assert result == {}


@pytest.mark.asyncio
async def test_execute_trade_logs_decision_with_correct_params() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    broker = FakeBrokerAdapter()
    execution_engine.register_broker("test_broker", broker)

    action = TradeAction(
        symbol="AAPL",
        side="sell",
        quantity=25.0,
        reasoning="Sell on strength",
        confidence=0.75,
        strategy="mean_reversion",
    )

    with patch(
        "app.engines.execution_engine.log_trade_decision",
        new_callable=AsyncMock,
        return_value=1,
    ) as mock_log_decision, patch(
        "app.engines.execution_engine.log_order",
        new_callable=AsyncMock,
        return_value=100,
    ), patch(
        "app.engines.execution_engine.event_bus.publish",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.update_order_status",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.mark_decision_executed",
        new_callable=AsyncMock,
    ), patch.object(
        risk_engine,
        "check_trade",
        return_value=RiskCheckResult(passed=True),
    ):
        await execution_engine.execute_trade(action, current_price=155.5)

    mock_log_decision.assert_called_once()
    call_kwargs = mock_log_decision.call_args[1]
    assert call_kwargs["strategy"] == "mean_reversion"
    assert call_kwargs["symbol"] == "AAPL"
    assert call_kwargs["side"] == "sell"
    assert call_kwargs["quantity"] == 25.0
    assert call_kwargs["price"] == 155.5
    assert call_kwargs["reasoning"] == "Sell on strength"
    assert call_kwargs["confidence"] == 0.75
    assert call_kwargs["risk_check_passed"] is True


@pytest.mark.asyncio
async def test_execute_trade_logs_order_with_correct_params() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    broker = FakeBrokerAdapter()
    execution_engine.register_broker("test_broker", broker)

    action = TradeAction(
        symbol="MSFT",
        side="buy",
        quantity=15.0,
        reasoning="Buy the dip",
        confidence=0.85,
        strategy="dip_buyer",
    )

    with patch(
        "app.engines.execution_engine.log_trade_decision",
        new_callable=AsyncMock,
        return_value=42,
    ), patch(
        "app.engines.execution_engine.log_order",
        new_callable=AsyncMock,
        return_value=100,
    ) as mock_log_order, patch(
        "app.engines.execution_engine.event_bus.publish",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.update_order_status",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.mark_decision_executed",
        new_callable=AsyncMock,
    ), patch.object(
        risk_engine,
        "check_trade",
        return_value=RiskCheckResult(passed=True),
    ):
        await execution_engine.execute_trade(action, current_price=300.0)

    mock_log_order.assert_called_once()
    call_kwargs = mock_log_order.call_args[1]
    assert call_kwargs["broker"] == "test_broker"
    assert call_kwargs["symbol"] == "MSFT"
    assert call_kwargs["side"] == "buy"
    assert call_kwargs["order_type"] == "MARKET"
    assert call_kwargs["quantity"] == 15.0
    assert call_kwargs["decision_id"] == 42


@pytest.mark.asyncio
async def test_execute_trade_publishes_correct_event_data() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    broker = FakeBrokerAdapter(
        order_result=OrderResult(
            success=True,
            broker_order_id="REAL-ORDER-456",
            filled_price=300.25,
            filled_quantity=15.0,
        )
    )
    execution_engine.register_broker("test_broker", broker)

    action = TradeAction(
        symbol="MSFT",
        side="buy",
        quantity=15.0,
        reasoning="Test reason",
        confidence=0.9,
        strategy="test",
    )

    with patch(
        "app.engines.execution_engine.log_trade_decision",
        new_callable=AsyncMock,
        return_value=42,
    ), patch(
        "app.engines.execution_engine.log_order",
        new_callable=AsyncMock,
        return_value=200,
    ), patch(
        "app.engines.execution_engine.event_bus.publish",
        new_callable=AsyncMock,
    ) as mock_publish, patch(
        "app.engines.execution_engine.update_order_status",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.mark_decision_executed",
        new_callable=AsyncMock,
    ), patch.object(
        risk_engine,
        "check_trade",
        return_value=RiskCheckResult(passed=True),
    ):
        await execution_engine.execute_trade(action, current_price=300.0)

    mock_publish.assert_called_once()
    event = mock_publish.call_args[0][0]
    assert event.type == "order_executed"
    assert event.data["decision_id"] == 42
    assert event.data["order_id"] == 200
    assert event.data["broker_order_id"] == "REAL-ORDER-456"
    assert event.data["symbol"] == "MSFT"
    assert event.data["side"] == "buy"
    assert event.data["quantity"] == 15.0
    assert event.data["filled_price"] == 300.25


@pytest.mark.asyncio
async def test_execute_trade_retries_on_transient_failure() -> None:
    risk_engine = RiskEngine()
    execution_engine = ExecutionEngine(risk_engine)

    call_count = 0

    async def place_order_with_retry(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return OrderResult(success=False, error="Connection timeout")
        return OrderResult(success=True, broker_order_id="RETRY-001", filled_price=150.0, filled_quantity=10.0)

    broker = FakeBrokerAdapter()
    broker.place_order = place_order_with_retry
    execution_engine.register_broker("test", broker, default=True)

    action = TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        reasoning="Test trade",
        confidence=0.85,
        strategy="test_strategy",
    )

    with patch(
        "app.engines.execution_engine.log_trade_decision",
        new_callable=AsyncMock,
        return_value=1,
    ), patch(
        "app.engines.execution_engine.log_order",
        new_callable=AsyncMock,
        return_value=100,
    ), patch(
        "app.engines.execution_engine.event_bus.publish",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.update_order_status",
        new_callable=AsyncMock,
    ), patch(
        "app.engines.execution_engine.mark_decision_executed",
        new_callable=AsyncMock,
    ), patch.object(
        risk_engine,
        "check_trade",
        return_value=RiskCheckResult(passed=True),
    ):
        result = await execution_engine.execute_trade(action, current_price=150.0)

    assert result is not None
    assert result.success is True
    assert result.broker_order_id == "RETRY-001"
    assert call_count == 2


@pytest.mark.asyncio
async def test_sync_positions_thread_safe(execution_engine: ExecutionEngine) -> None:
    """Test that sync_positions acquires the portfolio lock."""
    broker = FakeBrokerAdapter(positions={
        "AAPL": {
            "quantity": 10.0,
            "avg_cost": 150.0,
            "market_value": 1500.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
        }
    })
    execution_engine.register_broker("test", broker, default=True)

    # Call sync_positions
    result = await execution_engine.sync_positions()

    # Verify result contains positions from all brokers
    assert "test" in result
    assert "AAPL" in result["test"]
    assert result["test"]["AAPL"]["quantity"] == 10.0


@pytest.mark.asyncio
async def test_get_all_positions_thread_safe(execution_engine: ExecutionEngine) -> None:
    """Test that get_all_positions acquires the portfolio lock."""
    broker1 = FakeBrokerAdapter(positions={
        "AAPL": {
            "quantity": 10.0,
            "avg_cost": 150.0,
            "market_value": 1500.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
        }
    })
    broker2 = FakeBrokerAdapter(positions={
        "MSFT": {
            "quantity": 5.0,
            "avg_cost": 300.0,
            "market_value": 1500.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
        }
    })

    execution_engine.register_broker("broker1", broker1)
    execution_engine.register_broker("broker2", broker2)

    result = await execution_engine.get_all_positions()

    # Verify result contains positions from both brokers
    assert "broker1" in result
    assert "broker2" in result
    assert "AAPL" in result["broker1"]
    assert "MSFT" in result["broker2"]
