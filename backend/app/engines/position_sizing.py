from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from app.core.logging import logger


@dataclass
class SizingConfig:
    method: Literal["fixed", "fixed_fractional", "kelly"] = "fixed"
    fixed_quantity: float = 10.0
    portfolio_fraction: float = 0.02  # 2% of portfolio per trade
    kelly_win_rate: float = 0.55
    kelly_avg_win: float = 1.5
    kelly_avg_loss: float = 1.0
    max_position_pct: float = 0.10  # Max 10% of portfolio in one position


class PositionSizer:
    def __init__(self, config: SizingConfig | None = None) -> None:
        self.config = config or SizingConfig()

    def calculate_quantity(
        self,
        portfolio_value: float,
        current_price: float,
        side: str,
    ) -> float:
        if portfolio_value <= 0 or current_price <= 0:
            return self.config.fixed_quantity

        method = self.config.method
        if method == "fixed":
            return self.config.fixed_quantity
        elif method == "fixed_fractional":
            return self._fixed_fractional(portfolio_value, current_price)
        elif method == "kelly":
            return self._kelly_criterion(portfolio_value, current_price)
        else:
            return self.config.fixed_quantity

    def _fixed_fractional(self, portfolio_value: float, price: float) -> float:
        """Risk a fixed fraction of portfolio per trade."""
        risk_amount = portfolio_value * self.config.portfolio_fraction
        max_amount = portfolio_value * self.config.max_position_pct
        trade_amount = min(risk_amount, max_amount)
        qty = trade_amount / price
        return round(max(qty, 0.01), 4)

    def _kelly_criterion(self, portfolio_value: float, price: float) -> float:
        """Kelly criterion: f* = (bp - q) / b where b=avg_win/avg_loss, p=win_rate, q=1-p."""
        p = self.config.kelly_win_rate
        q = 1 - p
        b = self.config.kelly_avg_win / self.config.kelly_avg_loss if self.config.kelly_avg_loss > 0 else 1

        kelly_fraction = (b * p - q) / b
        # Use half-Kelly for safety
        kelly_fraction = max(kelly_fraction * 0.5, 0)
        kelly_fraction = min(kelly_fraction, self.config.max_position_pct)

        trade_amount = portfolio_value * kelly_fraction
        qty = trade_amount / price
        return round(max(qty, 0.01), 4)

    def update_stats(self, win_rate: float, avg_win: float, avg_loss: float) -> None:
        """Update Kelly parameters from live trading stats."""
        self.config.kelly_win_rate = max(0.01, min(0.99, win_rate))
        self.config.kelly_avg_win = max(0.01, avg_win)
        self.config.kelly_avg_loss = max(0.01, avg_loss)
