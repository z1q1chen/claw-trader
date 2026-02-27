from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.feeds.dummy import DummyPriceFeed
from app.feeds.base import PriceFeed


@pytest.mark.asyncio
async def test_dummy_feed_implements_interface():
    feed = DummyPriceFeed(["AAPL", "MSFT"])
    assert isinstance(feed, PriceFeed)


@pytest.mark.asyncio
async def test_dummy_feed_returns_all_symbols():
    symbols = ["AAPL", "MSFT", "GOOGL"]
    feed = DummyPriceFeed(symbols)
    await feed.start()
    prices = await feed.get_latest_prices()
    assert set(prices.keys()) == set(symbols)
    await feed.stop()


@pytest.mark.asyncio
async def test_dummy_feed_returns_price_volume_tuples():
    feed = DummyPriceFeed(["AAPL"])
    await feed.start()
    prices = await feed.get_latest_prices()
    price, volume = prices["AAPL"]
    assert isinstance(price, float)
    assert isinstance(volume, float)
    assert price > 0
    assert volume > 0
    await feed.stop()


@pytest.mark.asyncio
async def test_dummy_feed_prices_change_over_time():
    feed = DummyPriceFeed(["AAPL"])
    await feed.start()
    prices_1 = await feed.get_latest_prices()
    seen_different = False
    for _ in range(20):
        prices_2 = await feed.get_latest_prices()
        if prices_1["AAPL"][0] != prices_2["AAPL"][0]:
            seen_different = True
            break
    assert seen_different
    await feed.stop()


@pytest.mark.asyncio
async def test_dummy_feed_custom_base_price():
    feed = DummyPriceFeed(["AAPL"], base_price=500.0)
    await feed.start()
    prices = await feed.get_latest_prices()
    assert abs(prices["AAPL"][0] - 500.0) < 5.0
    await feed.stop()


@pytest.mark.asyncio
async def test_dummy_feed_price_stays_positive():
    feed = DummyPriceFeed(["AAPL"], base_price=1.5)
    await feed.start()
    for _ in range(100):
        prices = await feed.get_latest_prices()
        assert prices["AAPL"][0] >= 1.0
    await feed.stop()
