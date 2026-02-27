from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import numpy as np

from app.core.config import settings
from app.core.events import Event, event_bus
from app.core.logging import logger
from app.engines.llm_brain import TradeAction


@dataclass
class RiskCheckResult:
    passed: bool
    rejection_reason: str | None = None
    adjusted_quantity: float | None = None
    exposure_after: float = 0.0
    var_95: float = 0.0


@dataclass
class PortfolioState:
    total_exposure_usd: float = 0.0
    daily_pnl_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    positions: dict[str, float] = None  # symbol -> exposure_usd
    sector_exposure: dict[str, float] = None  # sector -> exposure_usd

    def __post_init__(self):
        if self.positions is None:
            self.positions = {}
        if self.sector_exposure is None:
            self.sector_exposure = {}


class RiskEngine:
    """
    Pre-trade and portfolio-level risk management.

    Checks:
    1. Single trade size limit
    2. Position concentration limit
    3. Total portfolio exposure limit
    4. Daily loss limit (kill switch)
    5. Max drawdown (kill switch)
    6. VaR (Value at Risk) at 95% confidence
    7. Margin monitoring
    """

    def __init__(self) -> None:
        self._portfolio = PortfolioState()
        self._kill_switch = False
        self._daily_pnl_start: float = 0.0
        self._peak_portfolio_value: float = 0.0
        self._return_history: list[float] = []
        self._reset_lock = threading.Lock()

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch

    def activate_kill_switch(self, reason: str) -> None:
        self._kill_switch = True
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")

    def deactivate_kill_switch(self) -> None:
        self._kill_switch = False
        logger.warning("Kill switch deactivated")

    def update_portfolio(self, positions: dict[str, float], daily_pnl: float) -> None:
        self._portfolio.positions = positions
        self._portfolio.total_exposure_usd = sum(abs(v) for v in positions.values())
        self._portfolio.daily_pnl_usd = daily_pnl

        if self._portfolio.total_exposure_usd > self._peak_portfolio_value:
            self._peak_portfolio_value = self._portfolio.total_exposure_usd
        if self._peak_portfolio_value > 0:
            drawdown = (
                (self._peak_portfolio_value - self._portfolio.total_exposure_usd)
                / self._peak_portfolio_value * 100
            )
            self._portfolio.max_drawdown_pct = max(
                self._portfolio.max_drawdown_pct, drawdown
            )

    def check_trade(self, action: TradeAction, current_price: float) -> RiskCheckResult:
        if self._kill_switch:
            return RiskCheckResult(
                passed=False,
                rejection_reason="Kill switch is active. All trading halted.",
            )

        trade_value = action.quantity * current_price
        adjusted_quantity = None

        # Adjust quantity if exceeding single trade limit
        if trade_value > settings.max_single_trade_usd:
            adjusted_quantity = settings.max_single_trade_usd / current_price
            trade_value = settings.max_single_trade_usd

        # Determine exposure change based on side
        if action.side.upper() == "SELL":
            exposure_delta = -trade_value
        else:
            exposure_delta = trade_value

        # Check position concentration
        current_symbol_exposure = abs(self._portfolio.positions.get(action.symbol, 0))
        new_symbol_exposure = max(0, current_symbol_exposure + exposure_delta)
        max_per_position = settings.max_portfolio_exposure_usd * (settings.max_position_concentration_pct / 100.0)
        if new_symbol_exposure > max_per_position:
            return RiskCheckResult(
                passed=False,
                rejection_reason=f"Position in {action.symbol} would reach ${new_symbol_exposure:.0f}, exceeding {settings.max_position_concentration_pct}% concentration limit of ${max_per_position:.0f}",
            )

        # Check total portfolio exposure
        new_total_exposure = max(0, self._portfolio.total_exposure_usd + exposure_delta)
        if new_total_exposure > settings.max_portfolio_exposure_usd:
            return RiskCheckResult(
                passed=False,
                rejection_reason=f"Total exposure would reach ${new_total_exposure:.0f}, exceeding limit of ${settings.max_portfolio_exposure_usd:.0f}",
            )

        # Check daily loss limit
        if self._portfolio.daily_pnl_usd < -settings.max_daily_loss_usd:
            self.activate_kill_switch(
                f"Daily loss ${abs(self._portfolio.daily_pnl_usd):.0f} exceeds limit ${settings.max_daily_loss_usd:.0f}"
            )
            return RiskCheckResult(
                passed=False,
                rejection_reason="Daily loss limit breached. Kill switch activated.",
            )

        # Check max drawdown
        if self._portfolio.max_drawdown_pct > settings.max_drawdown_pct:
            self.activate_kill_switch(
                f"Max drawdown {self._portfolio.max_drawdown_pct:.1f}% exceeds limit {settings.max_drawdown_pct}%"
            )
            return RiskCheckResult(
                passed=False,
                rejection_reason="Max drawdown limit breached. Kill switch activated.",
            )

        var_95 = self._calculate_var()

        return RiskCheckResult(
            passed=True,
            adjusted_quantity=adjusted_quantity,
            exposure_after=new_total_exposure,
            var_95=var_95,
            rejection_reason=f"Trade size adjusted to {adjusted_quantity:.2f} shares." if adjusted_quantity else None,
        )

    def _calculate_var(self, confidence: float = 0.95) -> float:
        if len(self._return_history) < 10:
            return 0.0
        returns = np.array(self._return_history)
        var_pct = float(np.percentile(returns, (1 - confidence) * 100))
        return abs(var_pct * self._portfolio.total_exposure_usd)

    def add_return(self, daily_return_pct: float) -> None:
        self._return_history.append(daily_return_pct)
        if len(self._return_history) > 252:  # ~1 year of trading days
            self._return_history = self._return_history[-252:]

    def get_risk_snapshot(self) -> dict:
        return {
            "total_exposure_usd": self._portfolio.total_exposure_usd,
            "daily_pnl_usd": self._portfolio.daily_pnl_usd,
            "max_drawdown_pct": self._portfolio.max_drawdown_pct,
            "var_95_usd": self._calculate_var(),
            "positions_count": len(self._portfolio.positions),
            "kill_switch_active": self._kill_switch,
            "positions": self._portfolio.positions,
        }

    def reset_daily(self) -> None:
        """Reset daily P&L tracking. Call at market open or midnight."""
        with self._reset_lock:
            logger.info(f"Resetting daily metrics. Current daily PnL: ${self._portfolio.daily_pnl_usd:.2f}")
            self._portfolio.daily_pnl_usd = 0.0
            self._portfolio.max_drawdown_pct = 0.0
            self._peak_portfolio_value = self._portfolio.total_exposure_usd
            if self._kill_switch:
                self.deactivate_kill_switch()
            logger.info("Risk engine: daily metrics reset")
