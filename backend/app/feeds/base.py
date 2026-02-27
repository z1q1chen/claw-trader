from __future__ import annotations

from abc import ABC, abstractmethod


class PriceFeed(ABC):
    """Base class for real-time price data feeds."""

    @abstractmethod
    async def get_latest_prices(self) -> dict[str, tuple[float, float]]:
        """Returns {symbol: (price, volume)} for all monitored symbols."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Initialize the feed connection."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect and clean up."""
        ...
