from __future__ import annotations

import random
import time
from typing import Any

from app.engines.execution_engine import BrokerAdapter, OrderResult
from app.core.logging import logger


class DryRunBrokerAdapter(BrokerAdapter):
    """Simulated broker for paper trading without risking real capital."""

    def __init__(self) -> None:
        self._positions: dict[str, dict[str, Any]] = {}
        self._balance: float = 100000.0  # Start with $100k virtual
        self._order_counter: int = 0
        self._order_history: list[dict[str, Any]] = []
        logger.info("DryRun broker initialized with $100,000 virtual balance")

    async def place_order(
        self, symbol: str, side: str, quantity: float,
        order_type: str = "MARKET", limit_price: float | None = None,
    ) -> OrderResult:
        self._order_counter += 1
        order_id = f"DRY-{self._order_counter:06d}"

        # Simulate slippage (0.1% - 0.3%)
        slippage = random.uniform(0.001, 0.003)
        base_price = limit_price if limit_price else random.uniform(10, 500)

        if side.upper() == "BUY":
            filled_price = base_price * (1 + slippage)
        else:
            filled_price = base_price * (1 - slippage)

        cost = filled_price * quantity

        # Update virtual positions
        if symbol not in self._positions:
            self._positions[symbol] = {"quantity": 0, "avg_cost": 0, "total_cost": 0}

        pos = self._positions[symbol]
        if side.upper() == "BUY":
            total_cost = pos["total_cost"] + cost
            total_qty = pos["quantity"] + quantity
            pos["avg_cost"] = total_cost / total_qty if total_qty > 0 else 0
            pos["quantity"] = total_qty
            pos["total_cost"] = total_cost
            self._balance -= cost
        else:  # SELL
            pos["quantity"] -= quantity
            self._balance += cost
            if pos["quantity"] <= 0:
                del self._positions[symbol]

        self._order_history.append({
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "filled_price": round(filled_price, 4),
            "status": "filled",
            "timestamp": time.time(),
        })

        logger.info(f"[DRY RUN] {side} {quantity} {symbol} @ {filled_price:.4f} (order {order_id})")

        return OrderResult(
            success=True,
            broker_order_id=order_id,
            filled_price=round(filled_price, 4),
            filled_quantity=quantity,
        )

    async def get_positions(self) -> dict[str, dict[str, Any]]:
        result = {}
        for symbol, pos in self._positions.items():
            result[symbol] = {
                "quantity": pos["quantity"],
                "avg_cost": pos["avg_cost"],
                "market_value": pos["quantity"] * pos["avg_cost"],
                "unrealized_pnl": 0,
                "realized_pnl": 0,
            }
        return result

    async def get_balance(self) -> dict[str, float]:
        total_positions = sum(
            p["quantity"] * p["avg_cost"] for p in self._positions.values()
        )
        return {
            "AvailableFunds": self._balance,
            "NetLiquidation": self._balance + total_positions,
            "TotalPositionValue": total_positions,
        }

    async def get_order_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._order_history[-limit:]

    async def cancel_order(self, order_id: str) -> bool:
        # In dry-run, orders are instantly filled, so cancel always returns False
        return False
