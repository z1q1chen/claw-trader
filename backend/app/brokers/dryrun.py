from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from app.engines.execution_engine import BrokerAdapter, OrderResult
from app.core.logging import logger

STATE_FILE = Path("/tmp/claw-trader/data/dryrun_state.json")


class DryRunBrokerAdapter(BrokerAdapter):
    """Simulated broker for paper trading without risking real capital."""

    def __init__(self) -> None:
        self._positions: dict[str, dict[str, Any]] = {}
        self._balance: float = 100000.0  # Start with $100k virtual
        self._order_counter: int = 0
        self._order_history: list[dict[str, Any]] = []
        self._last_prices: dict[str, float] = {}
        self.load_state()
        logger.info(f"DryRun broker initialized with ${self._balance:,.2f} virtual balance")

    def load_state(self) -> None:
        """Load broker state from persistent storage."""
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
                    self._balance = state.get("balance", 100000.0)
                    self._positions = state.get("positions", {})
                    self._order_history = state.get("orders", [])
                    self._order_counter = state.get("order_counter", 0)
                    logger.info(f"Loaded DryRun state from {STATE_FILE}: balance=${self._balance:,.2f}, {len(self._positions)} positions")
            else:
                logger.debug(f"No persisted state found at {STATE_FILE}, starting fresh")
        except Exception as e:
            logger.warning(f"Failed to load DryRun state: {e}, starting with defaults")

    def save_state(self) -> None:
        """Save broker state to persistent storage."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "balance": self._balance,
                "positions": self._positions,
                "orders": self._order_history,
                "order_counter": self._order_counter,
            }
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save DryRun state: {e}")

    def set_price(self, symbol: str, price: float) -> None:
        """Update last known price for a symbol (called during portfolio sync)."""
        self._last_prices[symbol] = price

    async def place_order(
        self, symbol: str, side: str, quantity: float,
        order_type: str = "MARKET", limit_price: float | None = None,
    ) -> OrderResult:
        self._order_counter += 1
        order_id = f"DRY-{self._order_counter:06d}"

        # Simulate slippage (0.1% - 0.3%)
        slippage = random.uniform(0.001, 0.003)
        base_price = limit_price or self._last_prices.get(symbol, 100.0)

        if side.upper() == "BUY":
            filled_price = base_price * (1 + slippage)
        else:
            filled_price = base_price * (1 - slippage)

        cost = filled_price * quantity

        # For BUY, check if we have sufficient funds
        if side.upper() == "BUY":
            if cost > self._balance:
                return OrderResult(
                    success=False,
                    error=f"Insufficient funds: need ${cost:.2f}, have ${self._balance:.2f}",
                )

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
            if pos["quantity"] < quantity:
                return OrderResult(
                    success=False,
                    error=f"Insufficient position for {symbol}: trying to sell {quantity} but only have {pos['quantity']}",
                )
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
        self.save_state()

        return OrderResult(
            success=True,
            broker_order_id=order_id,
            filled_price=round(filled_price, 4),
            filled_quantity=quantity,
        )

    async def get_positions(self) -> dict[str, dict[str, Any]]:
        result = {}
        for symbol, pos in self._positions.items():
            current_price = self._last_prices.get(symbol, pos["avg_cost"])
            market_value = pos["quantity"] * current_price
            result[symbol] = {
                "quantity": pos["quantity"],
                "avg_cost": pos["avg_cost"],
                "market_value": market_value,
                "unrealized_pnl": market_value - (pos["quantity"] * pos["avg_cost"]),
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
