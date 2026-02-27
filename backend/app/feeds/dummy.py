from __future__ import annotations

import random

from app.feeds.base import PriceFeed


class DummyPriceFeed(PriceFeed):
    """Synthetic price data for development/testing."""

    def __init__(self, symbols: list[str], base_price: float = 100.0) -> None:
        self._symbols = symbols
        self._prices: dict[str, float] = {s: base_price for s in symbols}

    async def get_latest_prices(self) -> dict[str, tuple[float, float]]:
        result = {}
        for symbol in self._symbols:
            change = random.gauss(0, 0.5)
            self._prices[symbol] = max(1.0, self._prices[symbol] + change)
            volume = random.uniform(10000, 100000)
            result[symbol] = (self._prices[symbol], volume)
        return result

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass
