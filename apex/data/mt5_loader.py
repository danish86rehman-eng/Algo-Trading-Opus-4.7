"""MetaTrader 5 market data loader for Exness (and any MT5 broker).

Requires:
  pip install MetaTrader5          # Windows only
  A running MT5 terminal logged in to Exness (or any MT5 broker).

Usage:
  from apex.data.mt5_loader import load_from_mt5
  bars = load_from_mt5("BTCUSD", "4h", count=2000)

Symbol names on Exness MT5 differ from Binance:
  Binance   → Exness MT5
  BTCUSDT   → BTCUSD
  ETHUSDT   → ETHUSD
  (Forex: XAUUSD, EURUSD, GBPUSD …)

MT5 must be running and connected before calling any function here.
call mt5.initialize() once per process; this module manages it internally.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apex.strategy.bars import Bar

if TYPE_CHECKING:
    pass

_log = logging.getLogger("apex.data.mt5")

# Map interval strings (same as Binance convention) → MT5 TIMEFRAME_* constants.
# Values are resolved lazily after import so the module loads without MT5 installed.
_TF_NAMES: dict[str, str] = {
    "1m":  "TIMEFRAME_M1",
    "3m":  "TIMEFRAME_M3",
    "5m":  "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h":  "TIMEFRAME_H1",
    "2h":  "TIMEFRAME_H2",
    "4h":  "TIMEFRAME_H4",
    "6h":  "TIMEFRAME_H6",
    "8h":  "TIMEFRAME_H8",
    "12h": "TIMEFRAME_H12",
    "1d":  "TIMEFRAME_D1",
    "1w":  "TIMEFRAME_W1",
    "1M":  "TIMEFRAME_MN1",
}


def load_from_mt5(symbol: str, interval: str = "4h", count: int = 2000) -> list[Bar]:
    """Return up to `count` bars for `symbol` from the connected MT5 terminal.

    Args:
        symbol:   MT5 symbol name, e.g. "BTCUSD", "XAUUSD", "EURUSD".
        interval: Timeframe string matching Binance convention: 1m 5m 15m 1h 4h 1d …
        count:    Number of bars to fetch (most recent bars, newest last).

    Returns:
        list[Bar] sorted oldest → newest.

    Raises:
        RuntimeError: if MT5 is not installed, terminal is not running, or
                      the symbol / timeframe is unavailable.
    """
    try:
        import MetaTrader5 as mt5  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "MetaTrader5 package not installed.\n"
            "  pip install MetaTrader5\n"
            "  (Windows only — must match your MT5 terminal bitness)"
        ) from exc

    tf_name = _TF_NAMES.get(interval)
    if tf_name is None:
        valid = ", ".join(_TF_NAMES)
        raise ValueError(f"Unknown interval {interval!r}. Valid: {valid}")
    tf = getattr(mt5, tf_name)

    if not mt5.initialize():
        code, msg = mt5.last_error()
        raise RuntimeError(
            f"MT5 initialize() failed ({code}): {msg}\n"
            "Make sure MetaTrader 5 is running and logged in to Exness."
        )

    try:
        rates = mt5.copy_rates_from_pos(symbol.upper(), tf, 0, count)
    finally:
        mt5.shutdown()

    if rates is None or len(rates) == 0:
        code, msg = mt5.last_error()
        raise RuntimeError(
            f"No data returned for {symbol} {interval} ({code}): {msg}\n"
            "Check the symbol name in MT5 Market Watch (View → Market Watch)."
        )

    bars = [
        Bar(
            ts=float(r["time"]),           # MT5 time is already Unix seconds (UTC)
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r["tick_volume"]),
        )
        for r in rates
    ]
    _log.info("loaded %d bars for %s %s from MT5", len(bars), symbol, interval)
    return bars


def list_symbols(pattern: str = "") -> list[str]:
    """Return MT5 symbols matching `pattern` (empty = all).

    Useful for finding the exact symbol name on your Exness account:
        from apex.data.mt5_loader import list_symbols
        print([s for s in list_symbols() if "BTC" in s])
    """
    try:
        import MetaTrader5 as mt5  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package not installed. pip install MetaTrader5") from exc

    if not mt5.initialize():
        code, msg = mt5.last_error()
        raise RuntimeError(f"MT5 initialize() failed ({code}): {msg}")
    try:
        symbols = mt5.symbols_get(pattern) or []
        return [s.name for s in symbols]
    finally:
        mt5.shutdown()
