"""Notification event model.

NOTE (5C boundary): In the real codebase this model is expected to be *committed by
Phase 5C* as part of the Notifier seam. It is reproduced here so the 5D module is
self-contained and runnable. When 5C lands, replace this module with an import of the
real 5C types (the field names below are the only contract 5D depends on).
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Mapping


class EventKind(enum.Enum):
    TRADE = "trade"
    KILL = "kill"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    STATUS = "status"
    INFO = "info"


class Severity(enum.IntEnum):
    """Maps 1:1 onto ntfy priorities 1..5 (min..max)."""

    MIN = 1
    LOW = 2
    DEFAULT = 3
    HIGH = 4
    URGENT = 5


@dataclass(frozen=True)
class NotifierEvent:
    kind: EventKind
    title: str
    message: str
    severity: Severity = Severity.DEFAULT
    ts: float = field(default_factory=time.time)
    meta: Mapping[str, Any] = field(default_factory=dict)
