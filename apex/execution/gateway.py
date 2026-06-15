"""PaperGateway — simulated order execution for backtesting.

Fills happen at the bar close with configurable slippage and fee so the backtest
is not unrealistically clean. One position at a time (no pyramiding): a BUY while
long is a no-op, a SELL while flat is a no-op.
"""
from __future__ import annotations

from .order import Fill, Position


class PaperGateway:
    def __init__(
        self,
        *,
        initial_cash: float = 10_000.0,
        slippage_bps: float = 5.0,   # one-way slippage in basis points
        fee_bps: float = 5.0,        # taker fee in basis points
        position_size: float = 0.10, # fraction of cash risked per trade
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not 0.0 < position_size <= 1.0:
            raise ValueError("position_size must be in (0, 1]")
        self._initial = initial_cash
        self._slip = slippage_bps / 10_000
        self._fee = fee_bps / 10_000
        self._size = position_size

        self.cash: float = initial_cash
        self.position: Position = Position()
        self.fills: list[Fill] = []

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def fill(self, side: str, price: float, ts: float) -> Fill | None:
        """Attempt to fill a market order. Returns Fill on success, None if rejected."""
        if side == "BUY":
            return self._buy(price, ts)
        if side == "SELL":
            return self._sell(price, ts)
        raise ValueError(f"unknown side: {side!r}")

    def _buy(self, price: float, ts: float) -> Fill | None:
        if not self.position.is_flat:
            return None  # already long
        fill_price = price * (1.0 + self._slip)
        qty = (self.cash * self._size) / fill_price
        cost = qty * fill_price * (1.0 + self._fee)
        if cost > self.cash:
            return None  # insufficient funds
        self.cash -= cost
        self.position.open(qty, fill_price)
        f = Fill(side="BUY", qty=qty, price=fill_price, ts=ts, gross_pnl=0.0)
        self.fills.append(f)
        return f

    def _sell(self, price: float, ts: float) -> Fill | None:
        if self.position.is_flat:
            return None  # nothing to sell
        fill_price = price * (1.0 - self._slip)
        qty, entry = self.position.close()
        proceeds = qty * fill_price * (1.0 - self._fee)
        gross_pnl = proceeds - qty * entry
        self.cash += proceeds
        f = Fill(side="SELL", qty=qty, price=fill_price, ts=ts, gross_pnl=gross_pnl)
        self.fills.append(f)
        return f

    # ------------------------------------------------------------------ #
    # State queries
    # ------------------------------------------------------------------ #
    def equity(self, current_price: float) -> float:
        """Mark-to-market total equity at `current_price`."""
        return self.cash + self.position.qty * current_price

    def reset(self) -> None:
        self.cash = self._initial
        self.position = Position()
        self.fills.clear()
