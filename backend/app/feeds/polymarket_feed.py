from __future__ import annotations

import asyncio
import time

from app.feeds.base import PriceFeed
from app.core.logging import logger


class PolymarketPriceFeed(PriceFeed):
    """Price feed for Polymarket prediction markets.

    Fetches YES/NO token prices for tracked condition IDs
    from the Polymarket Gamma API.
    """

    def __init__(self, condition_ids: list[str]) -> None:
        self._condition_ids = condition_ids
        self._http = None
        self._last_data_time: float = 0.0
        self._backoff_seconds: float = 1.0
        self._max_backoff_seconds: float = 60.0

    def is_stale(self, max_age_seconds: float = 300.0) -> bool:
        """Check if no data has been received recently."""
        if self._last_data_time == 0.0:
            return True
        age = time.time() - self._last_data_time
        return age > max_age_seconds

    async def _fetch_with_backoff(self) -> dict[str, tuple[float, float]]:
        """Fetch prices with exponential backoff on connection failure."""
        while True:
            try:
                if self._http is None:
                    import httpx
                    self._http = httpx.AsyncClient(timeout=15.0)

                result = {}
                for cid in self._condition_ids:
                    try:
                        resp = await self._http.get(
                            f"https://gamma-api.polymarket.com/markets/{cid}"
                        )
                        if resp.status_code != 200:
                            logger.warning(f"Polymarket API returned status {resp.status_code} for condition {cid}")
                            continue
                        market = resp.json()
                        prices = market.get("outcomePrices", "")
                        if isinstance(prices, str) and prices:
                            import json
                            try:
                                price_list = json.loads(prices)
                                yes_price = float(price_list[0]) if len(price_list) > 0 else 0.5
                            except (json.JSONDecodeError, ValueError, IndexError) as e:
                                logger.warning(f"Failed to parse outcomePrices for {cid}: {e}")
                                continue
                        else:
                            yes_price = 0.5
                        volume = float(market.get("volume24hr", 0))
                        result[cid] = (yes_price, volume)
                    except Exception as e:
                        logger.warning(f"Error fetching price data for condition {cid}: {e}")
                        continue

                # Successfully got data, reset backoff
                if result:
                    self._last_data_time = time.time()
                    self._backoff_seconds = 1.0
                    return result
                else:
                    # No data retrieved, apply backoff
                    logger.debug(f"No price data retrieved, backing off {self._backoff_seconds}s")
                    await asyncio.sleep(self._backoff_seconds)
                    self._backoff_seconds = min(self._backoff_seconds * 2, self._max_backoff_seconds)
                    continue
            except Exception as e:
                logger.warning(f"Polymarket feed connection error: {e}. Reconnecting in {self._backoff_seconds}s...")
                await asyncio.sleep(self._backoff_seconds)
                self._backoff_seconds = min(self._backoff_seconds * 2, self._max_backoff_seconds)
                if self._http:
                    try:
                        await self._http.aclose()
                    except Exception:
                        pass
                self._http = None

    async def get_latest_prices(self) -> dict[str, tuple[float, float]]:
        """Get latest prices with reconnection support."""
        try:
            return await self._fetch_with_backoff()
        except Exception as e:
            logger.error(f"Fatal error in get_latest_prices: {e}")
            return {}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None
