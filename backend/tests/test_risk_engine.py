from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import threading
import time
from app.engines.risk_engine import RiskEngine, RiskCheckResult, PortfolioState
from app.engines.llm_brain import TradeAction
from app.core.config import settings


class TestRiskCheckResult:
    """Tests for RiskCheckResult dataclass."""

    def test_default_values(self):
        """RiskCheckResult should have correct default values."""
        result = RiskCheckResult(passed=True)
        assert result.passed is True
        assert result.rejection_reason is None
        assert result.adjusted_quantity is None
        assert result.exposure_after == 0.0
        assert result.var_95 == 0.0

    def test_all_fields_set(self):
        """RiskCheckResult should store all provided fields."""
        result = RiskCheckResult(
            passed=False,
            rejection_reason="Test reason",
            adjusted_quantity=100.5,
            exposure_after=5000.0,
            var_95=150.0,
        )
        assert result.passed is False
        assert result.rejection_reason == "Test reason"
        assert result.adjusted_quantity == 100.5
        assert result.exposure_after == 5000.0
        assert result.var_95 == 150.0


class TestPortfolioState:
    """Tests for PortfolioState dataclass."""

    def test_default_values(self):
        """PortfolioState should have correct default values."""
        state = PortfolioState()
        assert state.total_exposure_usd == 0.0
        assert state.daily_pnl_usd == 0.0
        assert state.max_drawdown_pct == 0.0

    def test_post_init_initializes_empty_dicts(self):
        """PortfolioState __post_init__ should initialize positions and sector_exposure as empty dicts."""
        state = PortfolioState()
        assert isinstance(state.positions, dict)
        assert state.positions == {}
        assert isinstance(state.sector_exposure, dict)
        assert state.sector_exposure == {}

    def test_post_init_preserves_provided_dicts(self):
        """PortfolioState __post_init__ should preserve provided dicts."""
        positions = {"AAPL": 5000.0, "MSFT": 3000.0}
        sector_exp = {"tech": 8000.0}
        state = PortfolioState(positions=positions, sector_exposure=sector_exp)
        assert state.positions == positions
        assert state.sector_exposure == sector_exp


@pytest.fixture
def risk_engine():
    """Create a fresh RiskEngine instance for each test."""
    return RiskEngine()


@pytest.fixture
def trade_action():
    """Create a basic trade action for testing."""
    return TradeAction(
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        reasoning="Test trade",
        confidence=0.8,
        strategy="test",
    )


class TestRiskEngineCheckTrade:
    """Tests for RiskEngine.check_trade() method."""

    def test_check_trade_passes_normal_trade_within_all_limits(self, risk_engine, trade_action):
        """A normal trade within all limits should pass."""
        # Set up: No positions, normal price
        risk_engine.update_portfolio({}, 0.0)

        result = risk_engine.check_trade(trade_action, current_price=100.0)

        assert result.passed is True
        assert result.rejection_reason is None
        assert result.adjusted_quantity is None
        assert result.exposure_after == 1000.0

    def test_check_trade_adjusts_quantity_when_exceeds_max_single_trade(self, risk_engine, trade_action):
        """Trade should be adjusted when trade_value > max_single_trade_usd."""
        risk_engine.update_portfolio({}, 0.0)
        # Default max_single_trade_usd is 2000
        # 10 shares at $300 = $3000, which exceeds $2000

        result = risk_engine.check_trade(trade_action, current_price=300.0)

        assert result.passed is True
        assert result.adjusted_quantity is not None
        assert result.adjusted_quantity == pytest.approx(2000.0 / 300.0)
        assert result.rejection_reason is not None
        assert "adjusted" in result.rejection_reason.lower()

    def test_check_trade_rejects_position_concentration_exceeds_limit(self, risk_engine, trade_action):
        """Trade should be rejected when position concentration > 20% of max_portfolio_exposure_usd."""
        # max_portfolio_exposure_usd = 50000, so max per position = 10000
        risk_engine.update_portfolio({"AAPL": 9500.0}, 0.0)

        # Try to add 1000 more -> total 10500, exceeds 10000 limit
        result = risk_engine.check_trade(trade_action, current_price=100.0)

        assert result.passed is False
        assert "concentration" in result.rejection_reason.lower()

    def test_check_trade_rejects_total_exposure_exceeds_limit(self, risk_engine, trade_action):
        """Trade should be rejected when total exposure would exceed max_portfolio_exposure_usd."""
        # max_portfolio_exposure_usd = 50000
        risk_engine.update_portfolio({"AAPL": 5000.0, "MSFT": 45500.0}, 0.0)

        # Total is 50500, trying to add 1000 more
        result = risk_engine.check_trade(trade_action, current_price=100.0)

        assert result.passed is False
        assert "total exposure" in result.rejection_reason.lower()

    def test_check_trade_rejects_and_activates_kill_switch_on_daily_loss_breach(self, risk_engine, trade_action):
        """Trade should be rejected and kill switch activated when daily_pnl < -max_daily_loss_usd."""
        # max_daily_loss_usd = 5000
        risk_engine.update_portfolio({}, daily_pnl=-5500.0)

        result = risk_engine.check_trade(trade_action, current_price=100.0)

        assert result.passed is False
        assert "Daily loss limit breached" in result.rejection_reason
        assert risk_engine.kill_switch_active is True

    def test_check_trade_rejects_and_activates_kill_switch_on_max_drawdown_breach(self, risk_engine, trade_action):
        """Trade should be rejected and kill switch activated when max_drawdown > max_drawdown_pct."""
        # max_drawdown_pct = 10.0
        risk_engine.update_portfolio({}, daily_pnl=0.0)
        # Manually set drawdown to trigger limit
        risk_engine._portfolio.max_drawdown_pct = 10.5

        result = risk_engine.check_trade(trade_action, current_price=100.0)

        assert result.passed is False
        assert "Max drawdown limit breached" in result.rejection_reason
        assert risk_engine.kill_switch_active is True

    def test_check_trade_rejects_all_trades_when_kill_switch_active(self, risk_engine, trade_action):
        """All trades should be rejected when kill switch is active."""
        risk_engine.update_portfolio({}, 0.0)
        risk_engine.activate_kill_switch("Test activation")

        result = risk_engine.check_trade(trade_action, current_price=100.0)

        assert result.passed is False
        assert "Kill switch is active" in result.rejection_reason


class TestRiskEngineKillSwitch:
    """Tests for kill switch toggle behavior."""

    def test_activate_kill_switch(self, risk_engine):
        """activate_kill_switch should set the kill switch flag to True."""
        assert risk_engine.kill_switch_active is False

        risk_engine.activate_kill_switch("Test reason")

        assert risk_engine.kill_switch_active is True

    def test_deactivate_kill_switch(self, risk_engine):
        """deactivate_kill_switch should set the kill switch flag to False."""
        risk_engine.activate_kill_switch("Test")
        assert risk_engine.kill_switch_active is True

        risk_engine.deactivate_kill_switch()

        assert risk_engine.kill_switch_active is False


class TestRiskEngineResetDaily:
    """Tests for RiskEngine.reset_daily() method."""

    def test_reset_daily_clears_pnl(self, risk_engine):
        """reset_daily() should reset daily_pnl_usd to 0."""
        risk_engine.update_portfolio({"AAPL": 5000}, -1000)
        assert risk_engine._portfolio.daily_pnl_usd == -1000

        risk_engine.reset_daily()

        assert risk_engine._portfolio.daily_pnl_usd == 0.0

    def test_reset_daily_clears_drawdown(self, risk_engine):
        """reset_daily() should reset max_drawdown_pct to 0."""
        risk_engine.update_portfolio({"AAPL": 10000}, 0)
        risk_engine.update_portfolio({"AAPL": 5000}, 0)
        assert risk_engine._portfolio.max_drawdown_pct > 0

        risk_engine.reset_daily()

        assert risk_engine._portfolio.max_drawdown_pct == 0.0

    def test_reset_daily_deactivates_kill_switch(self, risk_engine):
        """reset_daily() should deactivate the kill switch if active."""
        risk_engine.activate_kill_switch("test")
        assert risk_engine.kill_switch_active is True

        risk_engine.reset_daily()

        assert risk_engine.kill_switch_active is False

    def test_reset_daily_sets_peak_to_current_exposure(self, risk_engine):
        """reset_daily() should set _peak_portfolio_value to current total_exposure_usd."""
        risk_engine.update_portfolio({"AAPL": 5000}, 0)
        assert risk_engine._peak_portfolio_value == 5000.0

        # Simulate a decline
        risk_engine.update_portfolio({"AAPL": 3000}, 0)
        assert risk_engine._peak_portfolio_value == 5000.0  # Peak unchanged

        # After reset, peak should be set to current exposure
        risk_engine.reset_daily()
        assert risk_engine._peak_portfolio_value == 3000.0

    def test_reset_daily_thread_safety(self, risk_engine):
        """reset_daily() should be thread-safe with concurrent calls."""
        risk_engine.update_portfolio({"AAPL": 5000}, -1000.0)
        initial_daily_pnl = risk_engine._portfolio.daily_pnl_usd
        assert initial_daily_pnl == -1000.0

        reset_count = 0
        errors = []

        def reset_thread():
            nonlocal reset_count
            try:
                risk_engine.reset_daily()
                reset_count += 1
            except Exception as e:
                errors.append(str(e))

        # Launch multiple threads calling reset_daily concurrently
        threads = [threading.Thread(target=reset_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Should have no errors
        assert len(errors) == 0

        # Daily PnL should be reset exactly once
        assert risk_engine._portfolio.daily_pnl_usd == 0.0

        # All threads should have completed
        assert reset_count == 5


class TestRiskEngineUpdatePortfolio:
    """Tests for RiskEngine.update_portfolio() method."""

    def test_update_portfolio_updates_positions(self, risk_engine):
        """update_portfolio should update positions dict."""
        positions = {"AAPL": 5000.0, "MSFT": 3000.0}

        risk_engine.update_portfolio(positions, 0.0)

        assert risk_engine._portfolio.positions == positions

    def test_update_portfolio_updates_total_exposure(self, risk_engine):
        """update_portfolio should calculate total_exposure_usd as sum of abs values."""
        positions = {"AAPL": 5000.0, "MSFT": -3000.0}

        risk_engine.update_portfolio(positions, 0.0)

        assert risk_engine._portfolio.total_exposure_usd == 8000.0

    def test_update_portfolio_updates_daily_pnl(self, risk_engine):
        """update_portfolio should update daily_pnl_usd."""
        positions = {"AAPL": 5000.0}
        daily_pnl = 150.5

        risk_engine.update_portfolio(positions, daily_pnl)

        assert risk_engine._portfolio.daily_pnl_usd == daily_pnl

    def test_update_portfolio_updates_peak_value(self, risk_engine):
        """update_portfolio should track peak portfolio value."""
        # First update sets peak
        risk_engine.update_portfolio({"AAPL": 5000.0}, 0.0)
        assert risk_engine._peak_portfolio_value == 5000.0

        # Larger exposure sets new peak
        risk_engine.update_portfolio({"AAPL": 7000.0}, 0.0)
        assert risk_engine._peak_portfolio_value == 7000.0

        # Smaller exposure doesn't change peak
        risk_engine.update_portfolio({"AAPL": 4000.0}, 0.0)
        assert risk_engine._peak_portfolio_value == 7000.0

    def test_update_portfolio_calculates_drawdown(self, risk_engine):
        """update_portfolio should calculate max_drawdown_pct correctly."""
        # Peak at 10000
        risk_engine.update_portfolio({"AAPL": 10000.0}, 0.0)
        assert risk_engine._portfolio.max_drawdown_pct == 0.0

        # Drawdown to 9000 = 10% drawdown
        risk_engine.update_portfolio({"AAPL": 9000.0}, 0.0)
        assert risk_engine._portfolio.max_drawdown_pct == pytest.approx(10.0)

        # Recovery doesn't decrease max_drawdown
        risk_engine.update_portfolio({"AAPL": 9500.0}, 0.0)
        assert risk_engine._portfolio.max_drawdown_pct == pytest.approx(10.0)

    def test_update_portfolio_drawdown_with_new_peak(self, risk_engine):
        """Max drawdown should reset properly with new peak."""
        # Peak at 10000
        risk_engine.update_portfolio({"AAPL": 10000.0}, 0.0)
        # Down to 5000 = 50% drawdown
        risk_engine.update_portfolio({"AAPL": 5000.0}, 0.0)
        assert risk_engine._portfolio.max_drawdown_pct == pytest.approx(50.0)

        # New peak at 12000
        risk_engine.update_portfolio({"AAPL": 12000.0}, 0.0)
        # Down to 11000 = 8.33% from peak
        risk_engine.update_portfolio({"AAPL": 11000.0}, 0.0)
        assert risk_engine._portfolio.max_drawdown_pct == pytest.approx(50.0)


class TestRiskEngineVaR:
    """Tests for RiskEngine._calculate_var() method."""

    def test_calculate_var_returns_zero_with_insufficient_samples(self, risk_engine):
        """_calculate_var() should return 0.0 if < 10 return samples."""
        risk_engine.update_portfolio({"AAPL": 10000.0}, 0.0)

        # Add 9 returns (less than 10)
        for i in range(9):
            risk_engine.add_return(-0.01 * (i + 1))

        var = risk_engine._calculate_var()

        assert var == 0.0

    def test_calculate_var_returns_correct_var_with_sufficient_samples(self, risk_engine):
        """_calculate_var() should calculate correct VaR with >= 10 samples."""
        risk_engine.update_portfolio({"AAPL": 10000.0}, 0.0)

        # Add 10 negative returns for VaR calculation
        returns = [-0.02, -0.015, -0.01, -0.012, -0.018, -0.008, -0.011, -0.013, -0.016, -0.009]
        for r in returns:
            risk_engine.add_return(r)

        var = risk_engine._calculate_var()

        # VaR should be positive and within reasonable bounds
        assert var > 0.0
        assert var < 1000.0  # Less than full portfolio

    def test_calculate_var_scales_with_exposure(self, risk_engine):
        """VaR should scale with portfolio exposure."""
        # Small exposure
        risk_engine.update_portfolio({"AAPL": 1000.0}, 0.0)
        returns = [-0.02] * 10
        for r in returns:
            risk_engine.add_return(r)
        var_small = risk_engine._calculate_var()

        # Large exposure with same returns
        risk_engine.update_portfolio({"AAPL": 10000.0}, 0.0)
        risk_engine._return_history = returns.copy()
        var_large = risk_engine._calculate_var()

        # Larger exposure should have proportionally larger VaR
        assert var_large > var_small
        assert var_large == pytest.approx(var_small * 10, rel=0.01)


class TestRiskEngineReturnHistory:
    """Tests for RiskEngine.add_return() method."""

    def test_add_return_stores_return(self, risk_engine):
        """add_return() should store return in history."""
        risk_engine.add_return(0.01)

        assert len(risk_engine._return_history) == 1
        assert risk_engine._return_history[0] == 0.01

    def test_add_return_accumulates_returns(self, risk_engine):
        """add_return() should accumulate multiple returns."""
        returns = [0.01, -0.02, 0.015, -0.01]
        for r in returns:
            risk_engine.add_return(r)

        assert len(risk_engine._return_history) == 4
        assert risk_engine._return_history == returns

    def test_add_return_trims_to_252_days(self, risk_engine):
        """add_return() should trim history to 252 days when exceeded."""
        # Add 260 returns
        for i in range(260):
            risk_engine.add_return(0.001 * (i % 3 - 1))

        # Should keep only last 252
        assert len(risk_engine._return_history) == 252
        # First 8 should be trimmed
        assert risk_engine._return_history[0] == 0.001 * (8 % 3 - 1)


class TestRiskEngineGetRiskSnapshot:
    """Tests for RiskEngine.get_risk_snapshot() method."""

    def test_get_risk_snapshot_returns_correct_dict(self, risk_engine, trade_action):
        """get_risk_snapshot() should return correct dict with all metrics."""
        positions = {"AAPL": 5000.0, "MSFT": 3000.0}
        risk_engine.update_portfolio(positions, daily_pnl=150.0)
        risk_engine._portfolio.max_drawdown_pct = 5.0

        snapshot = risk_engine.get_risk_snapshot()

        assert isinstance(snapshot, dict)
        assert snapshot["total_exposure_usd"] == 8000.0
        assert snapshot["daily_pnl_usd"] == 150.0
        assert snapshot["max_drawdown_pct"] == 5.0
        assert snapshot["positions_count"] == 2
        assert snapshot["kill_switch_active"] is False
        assert snapshot["positions"] == positions
        assert "var_95_usd" in snapshot

    def test_get_risk_snapshot_reflects_kill_switch_status(self, risk_engine):
        """get_risk_snapshot() should reflect kill switch status."""
        risk_engine.update_portfolio({}, 0.0)

        snapshot_before = risk_engine.get_risk_snapshot()
        assert snapshot_before["kill_switch_active"] is False

        risk_engine.activate_kill_switch("Test")
        snapshot_after = risk_engine.get_risk_snapshot()
        assert snapshot_after["kill_switch_active"] is True

    def test_get_risk_snapshot_includes_var(self, risk_engine):
        """get_risk_snapshot() should include VaR calculation."""
        risk_engine.update_portfolio({"AAPL": 10000.0}, 0.0)

        # Add returns for VaR
        for i in range(10):
            risk_engine.add_return(-0.01)

        snapshot = risk_engine.get_risk_snapshot()

        assert "var_95_usd" in snapshot
        assert snapshot["var_95_usd"] > 0.0


class TestRiskEngineIntegration:
    """Integration tests for RiskEngine."""

    def test_full_trading_scenario(self, risk_engine):
        """Test a complete trading scenario with multiple trades."""
        # Initial state: no positions
        risk_engine.update_portfolio({}, 0.0)

        # First trade: buy 10 shares of AAPL at $100
        trade1 = TradeAction(
            symbol="AAPL", side="buy", quantity=10.0,
            reasoning="Good signal", confidence=0.8, strategy="test"
        )
        result1 = risk_engine.check_trade(trade1, current_price=100.0)
        assert result1.passed is True

        # Update portfolio
        risk_engine.update_portfolio({"AAPL": 1000.0}, 0.0)

        # Second trade: buy more AAPL
        trade2 = TradeAction(
            symbol="AAPL", side="buy", quantity=5.0,
            reasoning="Another signal", confidence=0.7, strategy="test"
        )
        result2 = risk_engine.check_trade(trade2, current_price=100.0)
        assert result2.passed is True

        # Update portfolio: now has 9500 in AAPL (close to 10000 limit)
        risk_engine.update_portfolio({"AAPL": 9500.0}, 0.0)

        # Try to add 1000 more (exceeds 10000 limit for concentration)
        # Even with adjustment to $2000 trade limit, it becomes 20 shares * $100 = $2000
        # 9500 + 2000 = 11500 > 10000
        trade3 = TradeAction(
            symbol="AAPL", side="buy", quantity=50.0,  # Gets adjusted to $2000 = 20 shares
            reasoning="Big trade", confidence=0.9, strategy="test"
        )
        result3 = risk_engine.check_trade(trade3, current_price=100.0)
        assert result3.passed is False
        assert "concentration" in result3.rejection_reason.lower()

    def test_kill_switch_scenario(self, risk_engine):
        """Test kill switch activation and blocking."""
        risk_engine.update_portfolio({}, daily_pnl=-6000.0)

        trade = TradeAction(
            symbol="AAPL", side="buy", quantity=10.0,
            reasoning="Test", confidence=0.8, strategy="test"
        )

        # First trade triggers kill switch
        result1 = risk_engine.check_trade(trade, current_price=100.0)
        assert result1.passed is False
        assert risk_engine.kill_switch_active is True

        # Subsequent trades blocked
        result2 = risk_engine.check_trade(trade, current_price=100.0)
        assert result2.passed is False
        assert "Kill switch is active" in result2.rejection_reason

        # Deactivate and trade works again
        risk_engine.deactivate_kill_switch()
        risk_engine.update_portfolio({}, daily_pnl=0.0)
        result3 = risk_engine.check_trade(trade, current_price=100.0)
        assert result3.passed is True

    def test_multi_symbol_portfolio(self, risk_engine):
        """Test portfolio management across multiple symbols."""
        # Set up diverse portfolio
        positions = {
            "AAPL": 8000.0,
            "MSFT": 9000.0,
            "GOOGL": 7000.0,
        }
        risk_engine.update_portfolio(positions, daily_pnl=500.0)

        # Each position is within 20% limit (10000 per symbol)
        snapshot = risk_engine.get_risk_snapshot()
        assert snapshot["positions_count"] == 3
        assert snapshot["total_exposure_usd"] == 24000.0

        # Try trade that doesn't exceed any limit
        trade = TradeAction(
            symbol="AMZN", side="buy", quantity=10.0,
            reasoning="New position", confidence=0.8, strategy="test"
        )
        result = risk_engine.check_trade(trade, current_price=100.0)
        assert result.passed is True
        assert result.exposure_after == pytest.approx(25000.0)

    def test_drawdown_tracking(self, risk_engine):
        """Test that drawdown is tracked correctly through portfolio updates."""
        # Build to peak
        risk_engine.update_portfolio({"AAPL": 10000.0}, 0.0)
        assert risk_engine._peak_portfolio_value == 10000.0
        assert risk_engine._portfolio.max_drawdown_pct == 0.0

        # First drawdown
        risk_engine.update_portfolio({"AAPL": 7000.0}, 0.0)
        assert risk_engine._portfolio.max_drawdown_pct == pytest.approx(30.0)

        # Slight recovery (doesn't lower max_drawdown)
        risk_engine.update_portfolio({"AAPL": 7500.0}, 0.0)
        assert risk_engine._portfolio.max_drawdown_pct == pytest.approx(30.0)

        # Further decline (increases max_drawdown)
        risk_engine.update_portfolio({"AAPL": 6000.0}, 0.0)
        assert risk_engine._portfolio.max_drawdown_pct == pytest.approx(40.0)
