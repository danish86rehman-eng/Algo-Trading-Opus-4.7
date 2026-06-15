"""Order and fill types for the paper trading gateway."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fill:
    side: str          # "BUY" | "SELL"
    qty: float
    price: float       # simulated fill price (after slippage)
    ts: float
    gross_pnl: float = 0.0   # realised P&L on SELL fills; 0 on BUY


@dataclass
class Position:
    qty: float = 0.0
    avg_cost: float = 0.0   # average entry price

    @property
    def is_flat(self) -> bool:
        return self.qty == 0.0

    def open(self, qty: float, price: float) -> None:
        self.qty = qty
        self.avg_cost = price

    def close(self) -> tuple[float, float]:
        """Return (qty, avg_cost) and reset to flat."""
        qty, cost = self.qty, self.avg_cost
        self.qty = 0.0
        self.avg_cost = 0.0
        return qty, cost
