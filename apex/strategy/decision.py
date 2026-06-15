"""Pure strategy decision logic — the heart of Phase 6.

`evaluate()` is deterministic and side-effect-free: given a window of OHLC bars and
a StrategyConfig it returns a Signal. Nothing here touches I/O, time, or state.

Tunable bands (config/core.yaml → StrategyConfig):
  atr_min / atr_max    — ATR as fraction of price; filters out flat/runaway markets
  max_spread           — (high-low)/close filter; skips wide-spread bars
  score_threshold      — minimum |momentum score| to generate a BUY/SELL signal
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .bars import Bar, atr, momentum_score


@dataclass(frozen=True)
class StrategyConfig:
    atr_period: int = 14
    atr_min: float = 0.005    # 0.5% of price minimum volatility
    atr_max: float = 0.050    # 5.0% of price maximum volatility
    momentum_period: int = 14
    max_spread: float = 0.003  # 0.3% bar high-low spread
    score_threshold: float = 0.30  # |score| must exceed this to act

    def __post_init__(self) -> None:
        if self.atr_min < 0 or self.atr_max <= self.atr_min:
            raise ValueError("atr_min must be >= 0 and < atr_max")
        if not 0.0 < self.score_threshold <= 1.0:
            raise ValueError("score_threshold must be in (0, 1]")
        if self.atr_period < 1 or self.momentum_period < 1:
            raise ValueError("periods must be >= 1")


@dataclass(frozen=True)
class Signal:
    action: str    # "BUY" | "SELL" | "HOLD"
    score: float   # momentum score in [-1, 1]; 0 for HOLD-by-filter
    reason: str    # human-readable explanation

    def is_actionable(self) -> bool:
        return self.action in ("BUY", "SELL")


_HOLD_NO_HISTORY = Signal("HOLD", 0.0, "insufficient_history")


def evaluate(bars: Sequence[Bar], config: StrategyConfig) -> Signal:
    """Return a trading signal from a window of OHLC bars.

    Callers should pass at least max(atr_period, momentum_period) + 1 bars;
    fewer bars return a HOLD with reason "insufficient_history".
    """
    min_bars = max(config.atr_period, config.momentum_period) + 1
    if len(bars) < min_bars:
        return _HOLD_NO_HISTORY

    price = bars[-1].close
    if price <= 0 or not math.isfinite(price):
        return Signal("HOLD", 0.0, "invalid_price")

    # --- ATR filter ---------------------------------------------------------
    atr_val = atr(bars, config.atr_period)
    atr_pct = atr_val / price
    if atr_pct < config.atr_min:
        return Signal("HOLD", 0.0, "atr_too_low")
    if atr_pct > config.atr_max:
        return Signal("HOLD", 0.0, "atr_too_high")

    # --- Bar spread filter --------------------------------------------------
    bar = bars[-1]
    spread = (bar.high - bar.low) / bar.close if bar.close > 0 else 0.0
    if spread > config.max_spread:
        return Signal("HOLD", 0.0, "spread_too_wide")

    # --- Momentum signal ----------------------------------------------------
    score = momentum_score(bars, config.momentum_period)

    if score >= config.score_threshold:
        return Signal("BUY", score, "momentum_up")
    if score <= -config.score_threshold:
        return Signal("SELL", score, "momentum_down")
    return Signal("HOLD", score, "below_threshold")
