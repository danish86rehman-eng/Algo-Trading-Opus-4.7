"""BacktestRunner — drives evaluate() + PaperGateway over a list of Bars.

The runner is stateless across runs: it resets the gateway before each run so the
same runner instance can be called multiple times (e.g., by WalkForwardValidator).

Warmup: the first `warmup_bars` bars are consumed to prime the indicator window
before the first signal is generated.  Fills during warmup are skipped; the equity
curve starts at bar 0 but trades only begin after warmup.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apex.execution.gateway import PaperGateway
from apex.execution.order import Fill
from apex.strategy.bars import Bar
from apex.strategy.decision import StrategyConfig, evaluate

from .metrics import BacktestMetrics, compute_metrics


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: list[float]
    fills: list[Fill]
    metrics: BacktestMetrics
    bars_processed: int
    bars_traded: int         # bars after warmup where a signal was checked


class BacktestRunner:
    def __init__(
        self,
        config: StrategyConfig,
        *,
        gateway: PaperGateway | None = None,
        warmup_bars: int = 50,
        window_size: int = 200,
    ) -> None:
        self._config = config
        self._gw = gateway or PaperGateway()
        self._warmup = warmup_bars
        self._window = window_size

    def run(self, bars: list[Bar]) -> BacktestResult:
        gw = self._gw
        gw.reset()

        equity_curve: list[float] = []
        bars_traded = 0
        initial_equity = gw.equity(bars[0].close) if bars else 0.0

        for i, bar in enumerate(bars):
            # build sliding window; cap at window_size for efficiency
            start = max(0, i + 1 - self._window)
            window = bars[start : i + 1]

            if i >= self._warmup and len(window) >= 2:
                signal = evaluate(window, self._config)
                if signal.is_actionable():
                    gw.fill(signal.action, bar.close, bar.ts)
                bars_traded += 1

            equity_curve.append(gw.equity(bar.close))

        metrics = compute_metrics(equity_curve, gw.fills, initial_equity)
        return BacktestResult(
            equity_curve=equity_curve,
            fills=list(gw.fills),
            metrics=metrics,
            bars_processed=len(bars),
            bars_traded=bars_traded,
        )
