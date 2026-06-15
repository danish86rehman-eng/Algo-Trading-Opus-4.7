"""Performance metrics computed from an equity curve and fill log."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from apex.execution.order import Fill


@dataclass(frozen=True)
class BacktestMetrics:
    n_trades: int           # number of round-trips (buy+sell pairs)
    win_rate: float         # fraction of winning round-trips in [0, 1]
    total_return: float     # decimal total return (0.10 = +10%)
    sharpe: float           # annualised Sharpe (assumes daily bars, rf=0)
    max_drawdown: float     # peak-to-trough decimal drawdown (always >= 0)
    profit_factor: float    # gross_profit / gross_loss; inf if no losses

    def __str__(self) -> str:
        return (
            f"trades={self.n_trades} win_rate={self.win_rate:.1%} "
            f"return={self.total_return:+.2%} sharpe={self.sharpe:.2f} "
            f"mdd={self.max_drawdown:.2%} pf={self.profit_factor:.2f}"
        )


def compute_metrics(
    equity_curve: Sequence[float],
    fills: Sequence[Fill],
    initial_equity: float | None = None,
) -> BacktestMetrics:
    if not equity_curve:
        return BacktestMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0)

    start = initial_equity if initial_equity is not None else equity_curve[0]
    end = equity_curve[-1]
    total_return = (end - start) / start if start > 0 else 0.0

    # --- drawdown -----------------------------------------------------------
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # --- Sharpe (daily returns, annualised √252) ---------------------------
    if len(equity_curve) > 1:
        rets = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] > 0
        ]
        if len(rets) > 1:
            mean_r = sum(rets) / len(rets)
            variance = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
            std_r = math.sqrt(variance) if variance > 0 else 0.0
            sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    # --- trade statistics (sell fills carry gross_pnl) ---------------------
    sell_fills = [f for f in fills if f.side == "SELL"]
    n_trades = len(sell_fills)
    if n_trades:
        wins = sum(1 for f in sell_fills if f.gross_pnl > 0)
        win_rate = wins / n_trades
        gross_profit = sum(f.gross_pnl for f in sell_fills if f.gross_pnl > 0)
        gross_loss = abs(sum(f.gross_pnl for f in sell_fills if f.gross_pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else math.inf
    else:
        win_rate = 0.0
        profit_factor = 0.0

    return BacktestMetrics(
        n_trades=n_trades,
        win_rate=win_rate,
        total_return=total_return,
        sharpe=sharpe,
        max_drawdown=max_dd,
        profit_factor=profit_factor,
    )
