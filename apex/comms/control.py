"""Control plane seam (design doc §2, §4).

The `ControlPlane` is the *only* surface the inbound listener is allowed to touch — it wraps
the bot's existing kill-switch / state machine, keeping the blast radius of remote control
tiny and auditable. 5D scope is intentionally limited to KILL / STOP / STATUS:
KILL and STOP move the bot toward the SAFE direction (flat & halted); STATUS is read-only.
Re-arming / parameter changes are deliberately out of scope.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol


class ControlAction(enum.Enum):
    KILL = "KILL"
    STOP = "STOP"
    STATUS = "STATUS"

    @classmethod
    def parse(cls, raw: str) -> "ControlAction":
        try:
            return cls(raw.strip().upper())
        except ValueError as exc:
            raise ValueError(f"unknown control action: {raw!r}") from exc


@dataclass(frozen=True)
class Ack:
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class StatusSnapshot:
    state: str
    open_positions: int = 0
    pnl: float = 0.0
    uptime_s: float = 0.0
    extra: Mapping[str, object] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"state={self.state} positions={self.open_positions} "
            f"pnl={self.pnl:+.2f} uptime={self.uptime_s:.0f}s"
        )


class ControlPlane(Protocol):
    def request_kill(self, reason: str, source: str) -> Ack:
        ...

    def request_stop(self, reason: str, source: str) -> Ack:
        ...

    def get_status(self) -> StatusSnapshot:
        ...


class CallbackControlPlane:
    """Adapter that binds the protocol to plain callables.

    Lets 5D drive the real bot's kill-switch/state API without importing it — wire the
    bot's existing functions here at startup.
    """

    def __init__(
        self,
        *,
        on_kill: Callable[[str, str], None],
        on_stop: Callable[[str, str], None],
        status: Callable[[], StatusSnapshot],
    ) -> None:
        self._on_kill = on_kill
        self._on_stop = on_stop
        self._status = status

    def request_kill(self, reason: str, source: str) -> Ack:
        self._on_kill(reason, source)
        return Ack(ok=True, detail="kill requested")

    def request_stop(self, reason: str, source: str) -> Ack:
        self._on_stop(reason, source)
        return Ack(ok=True, detail="stop requested")

    def get_status(self) -> StatusSnapshot:
        return self._status()
