from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import logger
from app.feeds.base import PriceFeed


class IBKRPriceFeed(PriceFeed):
    """Live market data from Interactive Brokers via ib_insync."""

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols
        self._ib = None
        self._contracts: dict[str, Any] = {}
        self._tickers: dict[str, Any] = {}
        self._connected = False

    async def start(self) -> None:
        from ib_insync import IB, Stock

        self._ib = IB()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._ib.connect(
                settings.ibkr_host, settings.ibkr_port,
                clientId=settings.ibkr_client_id + 10,  # Different clientId from broker
            ),
        )
        self._connected = True
        logger.info(f"IBKR price feed connected: {settings.ibkr_host}:{settings.ibkr_port}")

        for symbol in self._symbols:
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            self._contracts[symbol] = contract
            self._tickers[symbol] = self._ib.reqMktData(contract, "", False, False)
            await asyncio.sleep(0.1)

        logger.info(f"IBKR subscribed to {len(self._symbols)} symbols")

    async def get_latest_prices(self) -> dict[str, tuple[float, float]]:
        if not self._connected or self._ib is None:
            return {}

        self._ib.sleep(0)  # Process pending events

        result = {}
        for symbol, ticker in self._tickers.items():
            price = ticker.last or ticker.close or 0
            volume = ticker.volume or 0
            if price > 0:
                result[symbol] = (float(price), float(volume))
        return result

    async def stop(self) -> None:
        if self._ib and self._connected:
            for contract in self._contracts.values():
                self._ib.cancelMktData(contract)
            self._ib.disconnect()
            self._connected = False
            logger.info("IBKR price feed disconnected")
