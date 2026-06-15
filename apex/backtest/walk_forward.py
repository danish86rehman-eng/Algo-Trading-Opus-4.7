"""Walk-forward validation — anchored expanding windows.

Each fold's in-sample window grows forward; the out-of-sample window slides.
This mimics how a live strategy would be re-evaluated over time and tests
whether the signal generalises rather than over-fitting to one period.

Fold layout (n_splits=5, in_sample_fraction=0.7 over N bars):
  step = (N - min_is) / n_splits   (evenly spaced OOS windows)
  fold k:
    in-sample  [0, is_end_k)
    out-sample [is_end_k, is_end_k + step)
"""
from __future__ import annotations

from dataclasses import dataclass

from apex.execution.gateway import PaperGateway
from apex.strategy.bars import Bar
from apex.strategy.decision import StrategyConfig

from .metrics import BacktestMetrics
from .runner import BacktestResult, BacktestRunner


@dataclass(frozen=True)
class FoldResult:
    fold: int
    in_sample_bars: int
    out_sample_bars: int
    in_sample: BacktestMetrics
    out_sample: BacktestMetrics


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[FoldResult]
    config: StrategyConfig

    @property
    def mean_oos_return(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.out_sample.total_return for f in self.folds) / len(self.folds)

    @property
    def mean_oos_sharpe(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.out_sample.sharpe for f in self.folds) / len(self.folds)

    @property
    def mean_oos_drawdown(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.out_sample.max_drawdown for f in self.folds) / len(self.folds)

    def summary(self) -> str:
        return (
            f"folds={len(self.folds)} "
            f"mean_oos_return={self.mean_oos_return:+.2%} "
            f"mean_oos_sharpe={self.mean_oos_sharpe:.2f} "
            f"mean_oos_mdd={self.mean_oos_drawdown:.2%}"
        )


class WalkForwardValidator:
    def __init__(
        self,
        config: StrategyConfig,
        *,
        n_splits: int = 5,
        in_sample_fraction: float = 0.70,
        warmup_bars: int = 50,
        gateway_kwargs: dict | None = None,
    ) -> None:
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        if not 0.0 < in_sample_fraction < 1.0:
            raise ValueError("in_sample_fraction must be in (0, 1)")
        self._config = config
        self._n_splits = n_splits
        self._is_frac = in_sample_fraction
        self._warmup = warmup_bars
        self._gw_kwargs = gateway_kwargs or {}

    def validate(self, bars: list[Bar]) -> WalkForwardResult:
        n = len(bars)
        min_is = max(self._warmup + 20, int(n * self._is_frac / self._n_splits))
        step = max(1, (n - min_is) // self._n_splits)

        folds: list[FoldResult] = []
        for k in range(self._n_splits):
            is_end = min_is + k * step
            oos_end = min(is_end + step, n)
            if oos_end <= is_end:
                break

            is_bars = bars[:is_end]
            oos_bars = bars[is_end:oos_end]

            gw = PaperGateway(**self._gw_kwargs)
            runner = BacktestRunner(self._config, gateway=gw, warmup_bars=self._warmup)

            is_result = runner.run(is_bars)
            oos_result = runner.run(oos_bars)

            folds.append(
                FoldResult(
                    fold=k,
                    in_sample_bars=len(is_bars),
                    out_sample_bars=len(oos_bars),
                    in_sample=is_result.metrics,
                    out_sample=oos_result.metrics,
                )
            )

        return WalkForwardResult(folds=folds, config=self._config)
