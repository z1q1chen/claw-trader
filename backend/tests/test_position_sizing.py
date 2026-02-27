from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.engines.position_sizing import PositionSizer, SizingConfig


class TestPositionSizer:
    def test_fixed_sizing_returns_fixed_quantity(self):
        """Test fixed sizing returns the configured fixed quantity."""
        config = SizingConfig(method="fixed", fixed_quantity=100.0)
        sizer = PositionSizer(config)

        qty = sizer.calculate_quantity(
            portfolio_value=100000,
            current_price=50.0,
            side="buy"
        )
        assert qty == 100.0

    def test_fixed_fractional_calculates_correct_quantity(self):
        """Test fixed fractional sizing calculates quantity based on portfolio."""
        config = SizingConfig(
            method="fixed_fractional",
            portfolio_fraction=0.02,
            max_position_pct=0.10
        )
        sizer = PositionSizer(config)

        # 100000 * 0.02 / 50 = 40 shares
        qty = sizer.calculate_quantity(
            portfolio_value=100000,
            current_price=50.0,
            side="buy"
        )
        assert qty == 40.0

    def test_fixed_fractional_respects_max_position_pct(self):
        """Test that fixed fractional sizing respects max position pct."""
        config = SizingConfig(
            method="fixed_fractional",
            portfolio_fraction=0.20,  # 20% is high
            max_position_pct=0.10     # Max 10%
        )
        sizer = PositionSizer(config)

        # Should be capped at 0.10 * portfolio / price
        qty = sizer.calculate_quantity(
            portfolio_value=100000,
            current_price=50.0,
            side="buy"
        )
        # max_amount = 100000 * 0.10 = 10000
        # qty = 10000 / 50 = 200
        assert qty == 200.0

    def test_kelly_criterion_positive_edge(self):
        """Test kelly criterion with positive parameters."""
        config = SizingConfig(
            method="kelly",
            kelly_win_rate=0.55,
            kelly_avg_win=1.5,
            kelly_avg_loss=1.0,
            max_position_pct=0.20
        )
        sizer = PositionSizer(config)

        qty = sizer.calculate_quantity(
            portfolio_value=100000,
            current_price=100.0,
            side="buy"
        )
        assert qty > 0.01
        assert isinstance(qty, float)

    def test_kelly_criterion_negative_edge_returns_minimum(self):
        """Test kelly criterion with poor win rate returns minimum position."""
        config = SizingConfig(
            method="kelly",
            kelly_win_rate=0.30,  # Poor win rate
            kelly_avg_win=1.0,
            kelly_avg_loss=2.0,   # Worse losses
            max_position_pct=0.20
        )
        sizer = PositionSizer(config)

        qty = sizer.calculate_quantity(
            portfolio_value=100000,
            current_price=100.0,
            side="buy"
        )
        # Should return minimum or very small position
        assert qty >= 0.01
        assert isinstance(qty, float)

    def test_calculate_quantity_zero_portfolio_falls_back_to_fixed(self):
        """Test that zero portfolio value falls back to fixed quantity."""
        config = SizingConfig(
            method="fixed_fractional",
            fixed_quantity=50.0,
            portfolio_fraction=0.02
        )
        sizer = PositionSizer(config)

        qty = sizer.calculate_quantity(
            portfolio_value=0,
            current_price=100.0,
            side="buy"
        )
        assert qty == 50.0

    def test_calculate_quantity_negative_price_falls_back_to_fixed(self):
        """Test that negative price falls back to fixed quantity."""
        config = SizingConfig(
            method="fixed_fractional",
            fixed_quantity=50.0
        )
        sizer = PositionSizer(config)

        qty = sizer.calculate_quantity(
            portfolio_value=100000,
            current_price=-10.0,
            side="buy"
        )
        assert qty == 50.0

    def test_update_stats_clamps_values(self):
        """Test that update_stats clamps values to valid ranges."""
        config = SizingConfig()
        sizer = PositionSizer(config)

        # Test with out-of-range values
        sizer.update_stats(
            win_rate=1.5,  # > 0.99
            avg_win=10.0,
            avg_loss=0.001
        )

        # Should be clamped
        assert 0.01 <= sizer.config.kelly_win_rate <= 0.99
        assert sizer.config.kelly_avg_win > 0
        assert sizer.config.kelly_avg_loss > 0

    def test_update_stats_with_zero_values_clamped(self):
        """Test that update_stats clamps zero values."""
        config = SizingConfig()
        sizer = PositionSizer(config)

        sizer.update_stats(
            win_rate=0.0,
            avg_win=0.0,
            avg_loss=0.0
        )

        # Should be clamped to minimums
        assert sizer.config.kelly_win_rate >= 0.01
        assert sizer.config.kelly_avg_win >= 0.01
        assert sizer.config.kelly_avg_loss >= 0.01

    def test_unknown_sizing_method_defaults_to_fixed(self):
        """Test that unknown sizing method defaults to fixed quantity."""
        config = SizingConfig()
        config.method = "unknown_method"  # type: ignore
        sizer = PositionSizer(config)

        qty = sizer.calculate_quantity(
            portfolio_value=100000,
            current_price=50.0,
            side="buy"
        )
        assert qty == config.fixed_quantity
