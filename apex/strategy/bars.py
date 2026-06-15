"""OHLCV bar type and pure technical indicator helpers.

All functions are stateless and operate on plain sequences — no pandas, no numpy,
so the strategy layer stays dependency-free and easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Bar:
    ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def atr(bars: Sequence[Bar], period: int) -> float:
    """Average True Range over the last `period` bars (simple average, not Wilder's EMA)."""
    if len(bars) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    tail = trs[-period:]
    return sum(tail) / len(tail) if tail else 0.0


def momentum_score(bars: Sequence[Bar], period: int) -> float:
    """Position of the current close within the [period]-bar rolling high/low, in [-1, 1].

    +1  = close at window high (strong upward momentum)
    -1  = close at window low  (strong downward momentum)
     0  = close at midpoint
    """
    if len(bars) < 2:
        return 0.0
    window = bars[-(period + 1):]
    lo = min(b.low for b in window)
    hi = max(b.high for b in window)
    if hi == lo:
        return 0.0
    return 2.0 * (bars[-1].close - lo) / (hi - lo) - 1.0
