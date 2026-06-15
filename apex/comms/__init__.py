"""Phase 5D — remote notify + control over a pub/sub transport (default: ntfy).

See docs/phase-5d-ntfy-remote-control.md for the design. The ntfy backend is isolated behind
the `Transport` seam so it can be deferred/swapped without touching the rest of the agent.
"""
from __future__ import annotations

from .agent import CommsAgent
from .config import CommsConfig
from .control import (
    Ack,
    CallbackControlPlane,
    ControlAction,
    ControlPlane,
    StatusSnapshot,
)
from .events import EventKind, NotifierEvent, Severity
from .heartbeat import Heartbeat
from .listener import CommandListener
from .notifier import FanoutNotifier, LogNotifier, Notifier, NullNotifier
from .ntfy_notifier import NtfyNotifier
from .outbound import OutboundDispatcher
from .security import CommandError, CommandSecurity, SignedCommand
from .transport import (
    InboundMessage,
    InMemoryTransport,
    NtfyTransport,
    OutboundMessage,
    Transport,
)

__all__ = [
    "CommsAgent",
    "CommsConfig",
    "Ack",
    "CallbackControlPlane",
    "ControlAction",
    "ControlPlane",
    "StatusSnapshot",
    "EventKind",
    "NotifierEvent",
    "Severity",
    "Heartbeat",
    "CommandListener",
    "FanoutNotifier",
    "LogNotifier",
    "Notifier",
    "NullNotifier",
    "NtfyNotifier",
    "OutboundDispatcher",
    "CommandError",
    "CommandSecurity",
    "SignedCommand",
    "InboundMessage",
    "InMemoryTransport",
    "NtfyTransport",
    "OutboundMessage",
    "Transport",
]
