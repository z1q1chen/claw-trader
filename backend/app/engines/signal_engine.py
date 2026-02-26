from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.config import settings
from app.core.events import Event, event_bus
from app.core.logging import logger


@dataclass
class PriceBar:
    symbol: str
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    symbol: str
    signal_type: str  # e.g. "rsi_oversold", "volume_spike", "macd_crossover"
    value: float
    metadata: dict[str, Any]


class TechnicalIndicators:
    """Lightweight technical indicator calculations on price arrays."""

    @staticmethod
    def rsi(closes: np.ndarray, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def sma(closes: np.ndarray, period: int) -> float:
        if len(closes) < period:
            return closes[-1] if len(closes) > 0 else 0.0
        return float(np.mean(closes[-period:]))

    @staticmethod
    def ema(closes: np.ndarray, period: int) -> float:
        if len(closes) < period:
            return closes[-1] if len(closes) > 0 else 0.0
        multiplier = 2.0 / (period + 1)
        ema_val = float(np.mean(closes[:period]))
        for price in closes[period:]:
            ema_val = (price - ema_val) * multiplier + ema_val
        return ema_val

    @staticmethod
    def _ema_series(closes: np.ndarray, period: int) -> np.ndarray:
        if len(closes) < period:
            return closes.copy()
        multiplier = 2.0 / (period + 1)
        ema = np.empty(len(closes) - period + 1)
        ema[0] = float(np.mean(closes[:period]))
        for i, price in enumerate(closes[period:], 1):
            ema[i] = (price - ema[i-1]) * multiplier + ema[i-1]
        return ema

    @staticmethod
    def macd(closes: np.ndarray) -> tuple[float, float, float]:
        if len(closes) < 26:
            return 0.0, 0.0, 0.0
        ema12_series = TechnicalIndicators._ema_series(closes, 12)
        ema26_series = TechnicalIndicators._ema_series(closes, 26)
        # Align to shorter series (ema26 starts later)
        macd_line_series = ema12_series[-len(ema26_series):] - ema26_series
        signal_line = TechnicalIndicators.ema(macd_line_series, 9)
        macd_line = float(macd_line_series[-1])
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(
        closes: np.ndarray, period: int = 20, std_dev: float = 2.0
    ) -> tuple[float, float, float]:
        sma = TechnicalIndicators.sma(closes, period)
        if len(closes) < period:
            return sma, sma, sma
        std = float(np.std(closes[-period:]))
        return sma + std_dev * std, sma, sma - std_dev * std

    @staticmethod
    def volume_sma(volumes: np.ndarray, period: int = 20) -> float:
        if len(volumes) < period:
            return float(np.mean(volumes)) if len(volumes) > 0 else 0.0
        return float(np.mean(volumes[-period:]))


class SignalEngine:
    """
    Sub-second signal detection engine.

    Maintains rolling price/volume windows per symbol and emits signals
    when technical thresholds are breached. Designed to run in a tight
    async loop at configurable intervals (default 500ms).
    """

    def __init__(self) -> None:
        self._price_history: dict[str, list[float]] = {}
        self._volume_history: dict[str, list[float]] = {}
        self._running = False
        self._max_history = 200
        self._last_signal_time: dict[str, float] = {}  # "symbol:signal_type" -> timestamp
        self._signal_cooldown_s: float = 60.0

    def update_price(self, symbol: str, price: float, volume: float) -> list[Signal]:
        closes = self._price_history.setdefault(symbol, [])
        volumes = self._volume_history.setdefault(symbol, [])
        closes.append(price)
        volumes.append(volume)

        if len(closes) > self._max_history:
            self._price_history[symbol] = closes[-self._max_history:]
            closes = self._price_history[symbol]
        if len(volumes) > self._max_history:
            self._volume_history[symbol] = volumes[-self._max_history:]
            volumes = self._volume_history[symbol]

        return self._detect_signals(symbol, np.array(closes), np.array(volumes))

    def _should_emit(self, symbol: str, signal_type: str) -> bool:
        key = f"{symbol}:{signal_type}"
        now = time.monotonic()
        last = self._last_signal_time.get(key, 0)
        if now - last < self._signal_cooldown_s:
            return False
        self._last_signal_time[key] = now
        return True

    def _detect_signals(
        self, symbol: str, closes: np.ndarray, volumes: np.ndarray
    ) -> list[Signal]:
        signals: list[Signal] = []
        if len(closes) < 15:
            return signals

        ti = TechnicalIndicators

        rsi = ti.rsi(closes)
        if rsi < 30:
            if self._should_emit(symbol, "rsi_oversold"):
                signals.append(Signal(symbol, "rsi_oversold", rsi, {"threshold": 30}))
        elif rsi > 70:
            if self._should_emit(symbol, "rsi_overbought"):
                signals.append(Signal(symbol, "rsi_overbought", rsi, {"threshold": 70}))

        macd_line, signal_line, histogram = ti.macd(closes)
        if len(closes) > 26:
            if macd_line > signal_line and histogram > 0:
                if self._should_emit(symbol, "macd_bullish"):
                    signals.append(Signal(symbol, "macd_bullish", macd_line, {
                        "signal_line": signal_line, "histogram": histogram
                    }))
            elif macd_line < signal_line and histogram < 0:
                if self._should_emit(symbol, "macd_bearish"):
                    signals.append(Signal(symbol, "macd_bearish", macd_line, {
                        "signal_line": signal_line, "histogram": histogram
                    }))

        if len(volumes) >= 20:
            vol_avg = ti.volume_sma(volumes, 20)
            if vol_avg > 0 and volumes[-1] > vol_avg * 2.0:
                if self._should_emit(symbol, "volume_spike"):
                    signals.append(Signal(symbol, "volume_spike", float(volumes[-1]), {
                        "avg_volume": vol_avg, "ratio": float(volumes[-1] / vol_avg)
                    }))

        upper, middle, lower = ti.bollinger_bands(closes)
        current = float(closes[-1])
        if current <= lower:
            if self._should_emit(symbol, "bb_lower_touch"):
                signals.append(Signal(symbol, "bb_lower_touch", current, {
                    "lower": lower, "middle": middle, "upper": upper
                }))
        elif current >= upper:
            if self._should_emit(symbol, "bb_upper_touch"):
                signals.append(Signal(symbol, "bb_upper_touch", current, {
                    "upper": upper, "middle": middle, "lower": lower
                }))

        return signals

    async def run(self, price_feed) -> None:
        """Main loop: pull prices from feed, detect signals, publish events."""
        self._running = True
        interval_s = settings.signal_scan_interval_ms / 1000.0

        while self._running:
            start = time.monotonic()
            try:
                prices = await price_feed.get_latest_prices()
                for symbol, (price, volume) in prices.items():
                    signals = self.update_price(symbol, price, volume)
                    for signal in signals:
                        await event_bus.publish(Event(
                            type="signal",
                            data={
                                "symbol": signal.symbol,
                                "signal_type": signal.signal_type,
                                "value": signal.value,
                                "metadata": signal.metadata,
                                "price": price,
                            }
                        ))
            except Exception as e:
                logger.error(f"Signal engine error: {e}")

            elapsed = time.monotonic() - start
            sleep_time = max(0, interval_s - elapsed)
            await asyncio.sleep(sleep_time)

    def stop(self) -> None:
        self._running = False
