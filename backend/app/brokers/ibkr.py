from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import logger
from app.engines.execution_engine import BrokerAdapter, OrderResult


class IBKRAdapter(BrokerAdapter):
    """
    Interactive Brokers adapter using ib_insync.

    Connects to TWS or IB Gateway via the IBKR API.
    Port 7497 = paper trading, 7496 = live trading.
    """

    def __init__(self) -> None:
        self._ib = None
        self._connected = False

    async def connect(self) -> None:
        from ib_insync import IB
        self._ib = IB()
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._ib.connect(
                settings.ibkr_host, settings.ibkr_port, clientId=settings.ibkr_client_id
            ),
        )
        self._connected = True
        logger.info(f"IBKR connected: {settings.ibkr_host}:{settings.ibkr_port}")

    async def disconnect(self) -> None:
        if self._ib and self._connected:
            self._ib.disconnect()
            self._connected = False

    async def place_order(
        self, symbol: str, side: str, quantity: float,
        order_type: str = "MARKET", limit_price: float | None = None,
    ) -> OrderResult:
        if not self._connected or self._ib is None:
            return OrderResult(success=False, error="IBKR not connected")

        try:
            from ib_insync import Stock, MarketOrder, LimitOrder

            contract = Stock(symbol, "SMART", "USD")
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._ib.qualifyContracts(contract)
            )

            action = "BUY" if side == "buy" else "SELL"
            if order_type == "LIMIT" and limit_price is not None:
                order = LimitOrder(action, quantity, limit_price)
            else:
                order = MarketOrder(action, quantity)

            trade = self._ib.placeOrder(contract, order)

            await asyncio.sleep(2)

            if trade.orderStatus.status in ("Filled", "Submitted", "PreSubmitted"):
                return OrderResult(
                    success=True,
                    broker_order_id=str(trade.order.orderId),
                    filled_price=trade.orderStatus.avgFillPrice or None,
                    filled_quantity=trade.orderStatus.filled or 0,
                )
            else:
                return OrderResult(
                    success=False,
                    broker_order_id=str(trade.order.orderId),
                    error=f"Order status: {trade.orderStatus.status}",
                )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def get_positions(self) -> dict[str, dict[str, Any]]:
        if not self._connected or self._ib is None:
            return {}

        positions = {}
        for pos in self._ib.positions():
            symbol = pos.contract.symbol
            positions[symbol] = {
                "quantity": pos.position,
                "avg_cost": pos.avgCost,
                "market_value": pos.position * pos.avgCost,
                "contract_type": pos.contract.secType,
            }
        return positions

    async def get_balance(self) -> dict[str, float]:
        if not self._connected or self._ib is None:
            return {}

        summary = {}
        for item in self._ib.accountSummary():
            if item.tag in (
                "TotalCashValue", "NetLiquidation", "BuyingPower",
                "GrossPositionValue", "MaintMarginReq", "AvailableFunds",
                "UnrealizedPnL", "RealizedPnL",
            ):
                try:
                    summary[item.tag] = float(item.value)
                except ValueError:
                    pass
        return summary

    async def get_order_history(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._connected or self._ib is None:
            return []

        orders = []
        for trade in self._ib.trades()[:limit]:
            orders.append({
                "order_id": trade.order.orderId,
                "symbol": trade.contract.symbol,
                "side": trade.order.action,
                "quantity": trade.order.totalQuantity,
                "order_type": trade.order.orderType,
                "status": trade.orderStatus.status,
                "filled_quantity": trade.orderStatus.filled,
                "avg_fill_price": trade.orderStatus.avgFillPrice,
            })
        return orders

    async def cancel_order(self, order_id: str) -> bool:
        if not self._connected or self._ib is None:
            return False
        for trade in self._ib.trades():
            if str(trade.order.orderId) == order_id:
                self._ib.cancelOrder(trade.order)
                return True
        return False

    async def get_market_data(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
        """Get real-time price + volume for symbols."""
        if not self._connected or self._ib is None:
            return {}

        from ib_insync import Stock

        results = {}
        for symbol in symbols:
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            ticker = self._ib.reqMktData(contract, "", False, False)
            await asyncio.sleep(0.5)
            if ticker.last and ticker.volume:
                results[symbol] = (ticker.last, ticker.volume)
            self._ib.cancelMktData(contract)
        return results
