"""MT5 order executor — submits live trades through the MetaTrader5 terminal.

Keeps one position at a time (flat → long → flat) matching the PaperGateway
convention. A BUY while already long is a no-op; a SELL while flat is a no-op.

Error handling:
  All MT5 errors are raised as RuntimeError so the CircuitBreaker in the
  trading loop can intercept them and trip if the exchange is misbehaving.
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger("apex.data.mt5_executor")

# MT5 retcode meanings
_RETCODE_OK = 10009   # TRADE_RETCODE_DONE


class MT5Executor:
    def __init__(
        self,
        symbol: str,
        *,
        lot_size: float = 0.01,
        slippage_points: int = 20,
        magic: int = 234_000,
        comment: str = "apex",
    ) -> None:
        self._symbol = symbol.upper()
        self._lot = lot_size
        self._slip = slippage_points
        self._magic = magic
        self._comment = comment
        self._in_position = False

    # ── public ───────────────────────────────────────────────────────────────

    @property
    def in_position(self) -> bool:
        return self._in_position

    def buy(self) -> dict:
        """Open a long position. Returns the MT5 order result dict."""
        if self._in_position:
            _log.debug("BUY skipped — already in position")
            return {}
        result = self._send(self._buy_request())
        self._in_position = True
        _log.info("BUY filled: %s", result)
        return result

    def sell(self) -> dict:
        """Close the long position (SELL). Returns the MT5 order result dict."""
        if not self._in_position:
            _log.debug("SELL skipped — not in position")
            return {}
        result = self._send(self._sell_request())
        self._in_position = False
        _log.info("SELL filled: %s", result)
        return result

    def close_all(self) -> None:
        """Emergency close — force-sell regardless of internal state."""
        try:
            import MetaTrader5 as mt5  # type: ignore[import]
        except ImportError:
            return
        positions = mt5.positions_get(symbol=self._symbol)
        if not positions:
            self._in_position = False
            return
        for pos in positions:
            req = {
                "action":   mt5.TRADE_ACTION_DEAL,
                "symbol":   self._symbol,
                "volume":   pos.volume,
                "type":     mt5.ORDER_TYPE_SELL,
                "position": pos.ticket,
                "price":    mt5.symbol_info_tick(self._symbol).bid,
                "deviation": self._slip,
                "magic":    self._magic,
                "comment":  f"{self._comment} emergency close",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            mt5.order_send(req)
        self._in_position = False
        _log.warning("emergency close_all executed for %s", self._symbol)

    # ── internals ────────────────────────────────────────────────────────────

    def _buy_request(self) -> dict:
        import MetaTrader5 as mt5  # type: ignore[import]
        tick = mt5.symbol_info_tick(self._symbol)
        if tick is None:
            raise RuntimeError(f"No tick for {self._symbol} — symbol not in Market Watch?")
        return {
            "action":   mt5.TRADE_ACTION_DEAL,
            "symbol":   self._symbol,
            "volume":   self._lot,
            "type":     mt5.ORDER_TYPE_BUY,
            "price":    tick.ask,
            "deviation": self._slip,
            "magic":    self._magic,
            "comment":  self._comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

    def _sell_request(self) -> dict:
        import MetaTrader5 as mt5  # type: ignore[import]
        tick = mt5.symbol_info_tick(self._symbol)
        if tick is None:
            raise RuntimeError(f"No tick for {self._symbol}")
        # Find open positions by magic to get the ticket
        positions = mt5.positions_get(symbol=self._symbol) or []
        apex_pos = [p for p in positions if p.magic == self._magic]
        req: dict = {
            "action":   mt5.TRADE_ACTION_DEAL,
            "symbol":   self._symbol,
            "volume":   self._lot,
            "type":     mt5.ORDER_TYPE_SELL,
            "price":    tick.bid,
            "deviation": self._slip,
            "magic":    self._magic,
            "comment":  self._comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if apex_pos:
            req["position"] = apex_pos[0].ticket
        return req

    def _send(self, request: dict) -> dict:
        import MetaTrader5 as mt5  # type: ignore[import]
        result = mt5.order_send(request)
        if result is None:
            code, msg = mt5.last_error()
            raise RuntimeError(f"order_send returned None ({code}): {msg}")
        if result.retcode != _RETCODE_OK:
            raise RuntimeError(
                f"order_send failed retcode={result.retcode} "
                f"comment={result.comment!r}"
            )
        return result._asdict() if hasattr(result, "_asdict") else {"retcode": result.retcode}
