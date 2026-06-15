"""Phase 6 — execution, backtest runner, metrics, and walk-forward tests."""
from __future__ import annotations

import math
import unittest

from apex.backtest.metrics import BacktestMetrics, compute_metrics
from apex.backtest.runner import BacktestResult, BacktestRunner
from apex.backtest.walk_forward import WalkForwardValidator
from apex.execution.gateway import PaperGateway
from apex.execution.order import Fill
from apex.strategy.bars import Bar
from apex.strategy.decision import StrategyConfig


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _bar(close: float, *, high: float | None = None, low: float | None = None, ts: float = 0.0) -> Bar:
    h = high if high is not None else close * 1.005
    l = low if low is not None else close * 0.995
    return Bar(ts=ts, open=close, high=h, low=l, close=close)


def _trending_bars(n: int = 200, *, start: float = 100.0, step: float = 0.5) -> list[Bar]:
    bars, price = [], start
    for i in range(n):
        bars.append(Bar(ts=float(i), open=price, high=price + 0.5, low=price - 0.5, close=price))
        price += step
    return bars


def _flat_bars(n: int = 200, price: float = 100.0) -> list[Bar]:
    return [Bar(ts=float(i), open=price, high=price, low=price, close=price) for i in range(n)]


# --------------------------------------------------------------------------- #
# PaperGateway
# --------------------------------------------------------------------------- #
class PaperGatewayTests(unittest.TestCase):
    def _gw(self, **kw) -> PaperGateway:
        return PaperGateway(initial_cash=1000.0, slippage_bps=0.0, fee_bps=0.0, **kw)

    def test_buy_reduces_cash(self) -> None:
        gw = self._gw(position_size=0.5)
        gw.fill("BUY", 100.0, 0.0)
        self.assertAlmostEqual(gw.cash, 500.0, places=5)
        self.assertGreater(gw.position.qty, 0)

    def test_sell_after_buy_restores_cash(self) -> None:
        gw = self._gw(position_size=1.0)
        gw.fill("BUY", 100.0, 0.0)
        gw.fill("SELL", 100.0, 1.0)
        self.assertTrue(gw.position.is_flat)
        self.assertAlmostEqual(gw.cash, 1000.0, places=5)

    def test_buy_while_long_returns_none(self) -> None:
        gw = self._gw()
        gw.fill("BUY", 100.0, 0.0)
        result = gw.fill("BUY", 100.0, 1.0)
        self.assertIsNone(result)

    def test_sell_while_flat_returns_none(self) -> None:
        gw = self._gw()
        result = gw.fill("SELL", 100.0, 0.0)
        self.assertIsNone(result)

    def test_slippage_applied(self) -> None:
        gw = PaperGateway(initial_cash=1000.0, slippage_bps=100.0, fee_bps=0.0, position_size=1.0)
        fill = gw.fill("BUY", 100.0, 0.0)
        self.assertIsNotNone(fill)
        self.assertAlmostEqual(fill.price, 101.0, places=5)  # 1% slippage

    def test_fee_applied_on_sell(self) -> None:
        # position_size=0.5 so buy cost (with 1% fee) stays within cash
        gw = PaperGateway(initial_cash=1000.0, slippage_bps=0.0, fee_bps=100.0, position_size=0.5)
        gw.fill("BUY", 100.0, 0.0)
        f = gw.fill("SELL", 100.0, 1.0)
        self.assertIsNotNone(f)
        # sell fill price = 100 * (1 - 0.01) = 99; net proceeds < cost → negative pnl
        self.assertLess(f.gross_pnl, 0.0)

    def test_profit_on_rising_price(self) -> None:
        gw = self._gw(position_size=1.0)
        gw.fill("BUY", 100.0, 0.0)
        f = gw.fill("SELL", 110.0, 1.0)
        self.assertGreater(f.gross_pnl, 0.0)

    def test_equity_includes_open_position(self) -> None:
        gw = self._gw(position_size=0.5)
        gw.fill("BUY", 100.0, 0.0)
        self.assertAlmostEqual(gw.equity(100.0), 1000.0, places=4)
        self.assertAlmostEqual(gw.equity(110.0), 1050.0, places=4)

    def test_reset_clears_state(self) -> None:
        gw = self._gw()
        gw.fill("BUY", 100.0, 0.0)
        gw.reset()
        self.assertEqual(gw.cash, 1000.0)
        self.assertTrue(gw.position.is_flat)
        self.assertEqual(gw.fills, [])

    def test_invalid_side_raises(self) -> None:
        gw = self._gw()
        with self.assertRaises(ValueError):
            gw.fill("HOLD", 100.0, 0.0)

    def test_invalid_initial_cash_raises(self) -> None:
        with self.assertRaises(ValueError):
            PaperGateway(initial_cash=-1.0)

    def test_invalid_position_size_raises(self) -> None:
        with self.assertRaises(ValueError):
            PaperGateway(position_size=0.0)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
class MetricsTests(unittest.TestCase):
    def test_empty_returns_zeros(self) -> None:
        m = compute_metrics([], [])
        self.assertEqual(m.n_trades, 0)
        self.assertEqual(m.total_return, 0.0)

    def test_total_return_positive(self) -> None:
        equity = [1000.0, 1050.0, 1100.0]
        m = compute_metrics(equity, [])
        self.assertAlmostEqual(m.total_return, 0.10, places=5)

    def test_total_return_negative(self) -> None:
        equity = [1000.0, 900.0]
        m = compute_metrics(equity, [])
        self.assertAlmostEqual(m.total_return, -0.10, places=5)

    def test_max_drawdown_correct(self) -> None:
        equity = [100.0, 120.0, 80.0, 90.0]
        m = compute_metrics(equity, [])
        # peak=120, trough=80, dd = 40/120 ≈ 0.333
        self.assertAlmostEqual(m.max_drawdown, 40 / 120, places=5)

    def test_no_drawdown_when_monotone(self) -> None:
        equity = [100.0, 110.0, 120.0]
        m = compute_metrics(equity, [])
        self.assertAlmostEqual(m.max_drawdown, 0.0, places=5)

    def test_win_rate_and_n_trades(self) -> None:
        fills = [
            Fill("SELL", 1.0, 110.0, 0.0, gross_pnl=10.0),  # win
            Fill("SELL", 1.0, 90.0, 1.0, gross_pnl=-10.0),  # loss
        ]
        m = compute_metrics([1000.0], fills)
        self.assertEqual(m.n_trades, 2)
        self.assertAlmostEqual(m.win_rate, 0.5, places=5)

    def test_profit_factor_all_wins(self) -> None:
        fills = [Fill("SELL", 1.0, 110.0, 0.0, gross_pnl=10.0)]
        m = compute_metrics([1000.0], fills)
        self.assertTrue(math.isinf(m.profit_factor))

    def test_sharpe_positive_for_monotone_growth(self) -> None:
        equity = [1000.0 + i * 10 for i in range(100)]
        m = compute_metrics(equity, [])
        self.assertGreater(m.sharpe, 0.0)

    def test_str_doesnt_raise(self) -> None:
        m = BacktestMetrics(5, 0.6, 0.12, 1.5, 0.05, 2.0)
        self.assertIn("trades=5", str(m))


# --------------------------------------------------------------------------- #
# BacktestRunner
# --------------------------------------------------------------------------- #
class BacktestRunnerTests(unittest.TestCase):
    def _cfg(self) -> StrategyConfig:
        return StrategyConfig(
            atr_min=0.001, atr_max=0.9, max_spread=0.5, score_threshold=0.5
        )

    def test_run_returns_result(self) -> None:
        bars = _trending_bars(200)
        runner = BacktestRunner(self._cfg(), warmup_bars=20)
        result = runner.run(bars)
        self.assertIsInstance(result, BacktestResult)
        self.assertEqual(result.bars_processed, 200)

    def test_equity_curve_length_matches_bars(self) -> None:
        bars = _trending_bars(150)
        runner = BacktestRunner(self._cfg(), warmup_bars=20)
        result = runner.run(bars)
        self.assertEqual(len(result.equity_curve), 150)

    def test_no_trades_on_flat_bars(self) -> None:
        bars = _flat_bars(200)
        runner = BacktestRunner(self._cfg(), warmup_bars=20)
        result = runner.run(bars)
        # flat bars → atr_too_low → no signals → no trades
        self.assertEqual(len(result.fills), 0)

    def test_uptrend_is_profitable(self) -> None:
        bars = _trending_bars(200, step=1.0)
        runner = BacktestRunner(self._cfg(), warmup_bars=20)
        result = runner.run(bars)
        self.assertGreater(result.metrics.total_return, 0.0)

    def test_reset_between_runs(self) -> None:
        bars = _trending_bars(200, step=1.0)
        cfg = self._cfg()
        gw = PaperGateway(initial_cash=10_000.0, slippage_bps=0.0, fee_bps=0.0)
        runner = BacktestRunner(cfg, gateway=gw, warmup_bars=20)
        r1 = runner.run(bars)
        r2 = runner.run(bars)
        # same input → same result after reset
        self.assertEqual(r1.metrics.n_trades, r2.metrics.n_trades)
        self.assertAlmostEqual(r1.metrics.total_return, r2.metrics.total_return, places=6)

    def test_bars_traded_is_bars_after_warmup(self) -> None:
        bars = _trending_bars(100)
        runner = BacktestRunner(self._cfg(), warmup_bars=30)
        result = runner.run(bars)
        self.assertEqual(result.bars_traded, 100 - 30)


# --------------------------------------------------------------------------- #
# WalkForwardValidator
# --------------------------------------------------------------------------- #
class WalkForwardTests(unittest.TestCase):
    def _cfg(self) -> StrategyConfig:
        return StrategyConfig(
            atr_min=0.001, atr_max=0.9, max_spread=0.5, score_threshold=0.5
        )

    def test_produces_correct_fold_count(self) -> None:
        bars = _trending_bars(500)
        wf = WalkForwardValidator(self._cfg(), n_splits=5, warmup_bars=20)
        result = wf.validate(bars)
        self.assertEqual(len(result.folds), 5)

    def test_fold_bar_counts_are_positive(self) -> None:
        bars = _trending_bars(500)
        wf = WalkForwardValidator(self._cfg(), n_splits=5, warmup_bars=20)
        for fold in wf.validate(bars).folds:
            self.assertGreater(fold.in_sample_bars, 0)
            self.assertGreater(fold.out_sample_bars, 0)

    def test_mean_oos_return_computed(self) -> None:
        bars = _trending_bars(500)
        wf = WalkForwardValidator(self._cfg(), n_splits=5, warmup_bars=20)
        result = wf.validate(bars)
        self.assertIsInstance(result.mean_oos_return, float)

    def test_summary_str_doesnt_raise(self) -> None:
        bars = _trending_bars(500)
        wf = WalkForwardValidator(self._cfg(), n_splits=3, warmup_bars=20)
        result = wf.validate(bars)
        self.assertIn("folds=3", result.summary())

    def test_invalid_n_splits_raises(self) -> None:
        with self.assertRaises(ValueError):
            WalkForwardValidator(self._cfg(), n_splits=1)

    def test_invalid_fraction_raises(self) -> None:
        with self.assertRaises(ValueError):
            WalkForwardValidator(self._cfg(), in_sample_fraction=0.0)
        with self.assertRaises(ValueError):
            WalkForwardValidator(self._cfg(), in_sample_fraction=1.0)

    def test_config_attached_to_result(self) -> None:
        cfg = self._cfg()
        wf = WalkForwardValidator(cfg, n_splits=3, warmup_bars=20)
        result = wf.validate(_trending_bars(300))
        self.assertIs(result.config, cfg)

    def test_oos_bars_do_not_overlap_is_bars(self) -> None:
        # For each fold: IS ends where OOS begins → IS + OOS ≤ total bars.
        bars = _trending_bars(500)
        wf = WalkForwardValidator(self._cfg(), n_splits=5, warmup_bars=20)
        result = wf.validate(bars)
        for fold in result.folds:
            self.assertLessEqual(fold.in_sample_bars + fold.out_sample_bars, len(bars))


if __name__ == "__main__":
    unittest.main()
