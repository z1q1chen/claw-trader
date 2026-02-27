from __future__ import annotations

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

    async def get_latest_prices(self) -> dict[str, tuple[float, float]]:
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
        return result

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None
