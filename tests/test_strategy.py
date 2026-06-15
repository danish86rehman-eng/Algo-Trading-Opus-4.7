"""Phase 6 — strategy layer tests.

All tests are pure (no I/O, no threads). Bar fixtures are hand-crafted to exercise
each filter path in evaluate() so the test suite acts as a golden-path spec.
"""
from __future__ import annotations

import unittest

from apex.strategy.bars import Bar, atr, momentum_score
from apex.strategy.decision import Signal, StrategyConfig, evaluate


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _bar(close: float, *, high: float | None = None, low: float | None = None, ts: float = 0.0) -> Bar:
    # Default spread 0.1% — stays within the default max_spread=0.3%.
    h = high if high is not None else close * 1.001
    l = low if low is not None else close * 0.999
    return Bar(ts=ts, open=close, high=h, low=l, close=close)


def _trending_bars(n: int = 60, *, start: float = 100.0, step: float = 1.0) -> list[Bar]:
    """Uptrend/downtrend: close moves by `step` each bar; spread ~0.1% of price."""
    bars = []
    price = start
    for i in range(n):
        bars.append(_bar(price))
        price += step
    return bars


def _flat_bars(n: int = 60, price: float = 100.0) -> list[Bar]:
    """Truly flat: high = low = close → ATR = 0 (triggers atr_too_low)."""
    return [Bar(ts=float(i), open=price, high=price, low=price, close=price) for i in range(n)]


def _volatile_bars(n: int = 60, price: float = 100.0, swing: float = 10.0) -> list[Bar]:
    """Wide-swinging bars → ATR fraction high (triggers atr_too_high)."""
    return [_bar(price, high=price + swing, low=price - swing) for _ in range(n)]


# --------------------------------------------------------------------------- #
# Bar indicator tests
# --------------------------------------------------------------------------- #
class ATRTests(unittest.TestCase):
    def test_zero_when_not_enough_bars(self) -> None:
        self.assertEqual(atr([_bar(100.0)], period=14), 0.0)

    def test_constant_bars_equals_daily_range(self) -> None:
        bars = [_bar(100.0, high=101.0, low=99.0) for _ in range(20)]
        result = atr(bars, period=14)
        # TR per bar = max(2, |101-100|, |99-100|) = 2
        self.assertAlmostEqual(result, 2.0, places=6)

    def test_uses_only_last_period_bars(self) -> None:
        bars_long = [_bar(100.0, high=102.0, low=98.0) for _ in range(10)]
        bars_short = [_bar(100.0, high=101.0, low=99.0) for _ in range(10)]
        result = atr(bars_long + bars_short, period=10)
        self.assertAlmostEqual(result, 2.0, places=5)  # last 10 are the short bars


class MomentumScoreTests(unittest.TestCase):
    def test_close_at_top_is_plus_one(self) -> None:
        bars = [_bar(100.0, high=100.0, low=90.0) for _ in range(15)]
        bars[-1] = _bar(100.0, high=100.0, low=90.0)
        score = momentum_score(bars, period=14)
        self.assertAlmostEqual(score, 1.0, places=5)

    def test_close_at_bottom_is_minus_one(self) -> None:
        bars = [_bar(100.0, high=110.0, low=100.0) for _ in range(15)]
        bars[-1] = _bar(100.0, high=110.0, low=100.0)
        score = momentum_score(bars, period=14)
        self.assertAlmostEqual(score, -1.0, places=5)

    def test_flat_market_returns_zero(self) -> None:
        bars = [Bar(ts=float(i), open=100.0, high=100.0, low=100.0, close=100.0) for i in range(15)]
        self.assertAlmostEqual(momentum_score(bars, period=14), 0.0, places=10)

    def test_insufficient_bars_returns_zero(self) -> None:
        self.assertEqual(momentum_score([_bar(100.0)], period=14), 0.0)


# --------------------------------------------------------------------------- #
# evaluate() filter paths
# --------------------------------------------------------------------------- #
class EvaluateTests(unittest.TestCase):
    CFG = StrategyConfig()  # default config

    def test_insufficient_history_hold(self) -> None:
        bars = [_bar(100.0) for _ in range(5)]
        sig = evaluate(bars, self.CFG)
        self.assertEqual(sig.action, "HOLD")
        self.assertEqual(sig.reason, "insufficient_history")

    def test_flat_market_atr_too_low(self) -> None:
        sig = evaluate(_flat_bars(60), self.CFG)
        self.assertEqual(sig.action, "HOLD")
        self.assertEqual(sig.reason, "atr_too_low")

    def test_volatile_market_atr_too_high(self) -> None:
        sig = evaluate(_volatile_bars(60, swing=20.0), self.CFG)
        self.assertEqual(sig.action, "HOLD")
        self.assertEqual(sig.reason, "atr_too_high")

    def test_wide_spread_filtered(self) -> None:
        cfg = StrategyConfig(atr_min=0.001, atr_max=0.5, max_spread=0.001, score_threshold=0.3)
        bars = [_bar(100.0, high=102.0, low=98.0) for _ in range(60)]
        sig = evaluate(bars, cfg)
        self.assertEqual(sig.action, "HOLD")
        self.assertEqual(sig.reason, "spread_too_wide")

    def test_uptrend_generates_buy(self) -> None:
        # step=1.0 keeps ATR/price ~0.9% throughout the trend (above atr_min=0.5%)
        bars = _trending_bars(60, step=1.0)
        sig = evaluate(bars, self.CFG)
        self.assertEqual(sig.action, "BUY")
        self.assertGreater(sig.score, 0.0)

    def test_downtrend_generates_sell(self) -> None:
        bars = _trending_bars(60, step=-1.0)
        sig = evaluate(bars, self.CFG)
        self.assertEqual(sig.action, "SELL")
        self.assertLess(sig.score, 0.0)

    def test_below_threshold_hold(self) -> None:
        cfg = StrategyConfig(
            atr_min=0.001, atr_max=0.5,
            max_spread=0.5, score_threshold=0.99,  # very high threshold
        )
        bars = _trending_bars(60, step=1.0)
        sig = evaluate(bars, cfg)
        self.assertEqual(sig.action, "HOLD")
        self.assertEqual(sig.reason, "below_threshold")

    def test_signal_is_actionable(self) -> None:
        bars = _trending_bars(60, step=1.0)
        sig = evaluate(bars, self.CFG)
        self.assertTrue(sig.is_actionable())

    def test_hold_not_actionable(self) -> None:
        sig = evaluate(_flat_bars(60), self.CFG)
        self.assertFalse(sig.is_actionable())


# --------------------------------------------------------------------------- #
# StrategyConfig validation
# --------------------------------------------------------------------------- #
class StrategyConfigTests(unittest.TestCase):
    def test_valid_config(self) -> None:
        StrategyConfig()  # no raise

    def test_invalid_atr_range(self) -> None:
        with self.assertRaises(ValueError):
            StrategyConfig(atr_min=0.05, atr_max=0.01)

    def test_invalid_threshold(self) -> None:
        with self.assertRaises(ValueError):
            StrategyConfig(score_threshold=0.0)
        with self.assertRaises(ValueError):
            StrategyConfig(score_threshold=1.5)

    def test_invalid_period(self) -> None:
        with self.assertRaises(ValueError):
            StrategyConfig(atr_period=0)


if __name__ == "__main__":
    unittest.main()
