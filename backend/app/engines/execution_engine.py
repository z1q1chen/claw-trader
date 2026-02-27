from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.core.database import log_order, log_trade_decision, update_order_status, mark_decision_executed
from app.core.events import Event, event_bus
from app.core.logging import logger
from app.engines.llm_brain import TradeAction
from app.engines.risk_engine import RiskCheckResult, RiskEngine


@dataclass
class OrderResult:
    success: bool
    broker_order_id: str | None = None
    filled_price: float | None = None
    filled_quantity: float | None = None
    error: str | None = None


class BrokerAdapter(ABC):
    @abstractmethod
    async def place_order(
        self, symbol: str, side: str, quantity: float,
        order_type: str = "MARKET", limit_price: float | None = None,
    ) -> OrderResult:
        ...

    @abstractmethod
    async def get_positions(self) -> dict[str, dict[str, Any]]:
        ...

    @abstractmethod
    async def get_balance(self) -> dict[str, float]:
        ...

    @abstractmethod
    async def get_order_history(self, limit: int = 50) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        ...


class ExecutionEngine:
    """
    Orchestrates the full trade pipeline:
    Signal -> LLM Decision -> Risk Check -> Broker Execution.

    Sits between the risk engine and broker adapters.
    """

    def __init__(self, risk_engine: RiskEngine) -> None:
        self._risk_engine = risk_engine
        self._brokers: dict[str, BrokerAdapter] = {}
        self._default_broker: str | None = None

    def register_broker(self, name: str, adapter: BrokerAdapter, default: bool = False) -> None:
        self._brokers[name] = adapter
        if default or self._default_broker is None:
            self._default_broker = name

    async def execute_trade(
        self, action: TradeAction, current_price: float,
        broker_name: str | None = None,
    ) -> OrderResult | None:
        broker_name = broker_name or self._default_broker
        if broker_name is None or broker_name not in self._brokers:
            logger.warning(f"Execution engine: No broker '{broker_name}' registered")
            return None

        risk_result = self._risk_engine.check_trade(action, current_price)

        quantity = action.quantity
        if risk_result.adjusted_quantity is not None:
            quantity = risk_result.adjusted_quantity

        decision_id = await log_trade_decision(
            strategy=action.strategy,
            symbol=action.symbol,
            side=action.side,
            quantity=quantity,
            price=current_price,
            reasoning=action.reasoning,
            confidence=action.confidence,
            signals_snapshot={},
            risk_check_passed=risk_result.passed,
            risk_rejection_reason=risk_result.rejection_reason,
        )

        if not risk_result.passed:
            await event_bus.publish(Event(
                type="trade_rejected",
                data={
                    "decision_id": decision_id,
                    "symbol": action.symbol,
                    "reason": risk_result.rejection_reason,
                }
            ))
            return None

        broker = self._brokers[broker_name]
        order_id = await log_order(
            broker=broker_name,
            symbol=action.symbol,
            side=action.side,
            order_type=action.order_type,
            quantity=quantity,
            decision_id=decision_id,
            limit_price=action.limit_price,
        )

        result = await broker.place_order(
            symbol=action.symbol,
            side=action.side,
            quantity=quantity,
            order_type=action.order_type,
            limit_price=action.limit_price,
        )

        if result.success:
            await update_order_status(
                order_id, "filled",
                broker_order_id=result.broker_order_id,
                filled_price=result.filled_price,
                filled_quantity=result.filled_quantity,
            )
            await mark_decision_executed(decision_id, result.broker_order_id)
        else:
            await update_order_status(order_id, "failed", broker_order_id=result.broker_order_id)

        await event_bus.publish(Event(
            type="order_executed" if result.success else "order_failed",
            data={
                "decision_id": decision_id,
                "order_id": order_id,
                "broker_order_id": result.broker_order_id,
                "symbol": action.symbol,
                "side": action.side,
                "quantity": quantity,
                "filled_price": result.filled_price,
                "error": result.error,
            }
        ))

        return result

    async def get_positions(self, broker_name: str | None = None) -> dict[str, dict[str, Any]]:
        broker_name = broker_name or self._default_broker
        if broker_name and broker_name in self._brokers:
            return await self._brokers[broker_name].get_positions()
        return {}

    async def get_balance(self, broker_name: str | None = None) -> dict[str, float]:
        broker_name = broker_name or self._default_broker
        if broker_name and broker_name in self._brokers:
            return await self._brokers[broker_name].get_balance()
        return {}
