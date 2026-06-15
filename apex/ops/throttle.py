"""ThrottledNotifier — rate-limits noisy events to protect the operator's phone.

Rules:
- URGENT severity events are NEVER throttled (KILL always gets through).
- Each EventKind has its own independent sliding window counter.
- When a kind's window expires and there were suppressed events, one summary
  event is emitted ("N events suppressed") before the next real event passes.
- Implements the Notifier protocol from apex.comms.notifier.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from apex.comms.events import EventKind, NotifierEvent, Severity

_log = logging.getLogger("apex.ops.throttle")


class _KindBucket:
    def __init__(self, max_per_window: int, window_s: float) -> None:
        self._max = max_per_window
        self._window = window_s
        self.count = 0
        self.suppressed = 0
        self.window_start: float = 0.0

    def tick(self, now: float) -> tuple[bool, int]:
        """Return (should_pass, suppressed_to_flush).

        suppressed_to_flush > 0 means the caller must emit a summary before the event.
        """
        # Expire old window
        if now - self.window_start >= self._window:
            flushed = self.suppressed
            self.count = 0
            self.suppressed = 0
            self.window_start = now
            self.count += 1
            return True, flushed

        if self.count < self._max:
            self.count += 1
            return True, 0

        self.suppressed += 1
        return False, 0


class ThrottledNotifier:
    def __init__(
        self,
        inner,  # Notifier
        *,
        max_per_kind_per_window: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._max = max_per_kind_per_window
        self._window = window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[EventKind, _KindBucket] = {}

    def notify(self, event: NotifierEvent) -> None:
        if event.severity == Severity.URGENT:
            self._inner.notify(event)
            return

        now = self._clock()
        with self._lock:
            bucket = self._buckets.setdefault(
                event.kind, _KindBucket(self._max, self._window)
            )
            should_pass, suppressed = bucket.tick(now)

        if suppressed > 0:
            summary = NotifierEvent(
                kind=event.kind,
                title="Throttled",
                message=f"{suppressed} events suppressed",
                severity=Severity.LOW,
            )
            try:
                self._inner.notify(summary)
            except Exception:
                _log.exception("throttled notifier summary emit failed")

        if should_pass:
            try:
                self._inner.notify(event)
            except Exception:
                _log.exception("throttled notifier emit failed")

    def close(self) -> None:
        self._inner.close()
