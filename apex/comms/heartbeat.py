"""Heartbeat (design doc §8).

NOTE (5C boundary): the heartbeat *task* is expected to be committed by Phase 5C. It is
included here so the 5D agent is runnable end-to-end. It emits a low-priority HEARTBEAT
event through the Notifier so the operator can confirm liveness on demand (and mute the
routine pings). True dead-man's-switch detection (alerting on *absence* of a heartbeat)
is an external concern — see design doc §8.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .events import EventKind, NotifierEvent, Severity
from .notifier import Notifier

_log = logging.getLogger("apex.comms.heartbeat")


class Heartbeat:
    def __init__(
        self,
        notifier: Notifier,
        *,
        interval_s: float = 300.0,
        status_fn: Callable[[], str] | None = None,
    ) -> None:
        self._notifier = notifier
        self._interval = interval_s
        self._status_fn = status_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        message = self._status_fn() if self._status_fn else f"alive @ {time.strftime('%H:%M:%S')}"
        try:
            self._notifier.notify(
                NotifierEvent(EventKind.HEARTBEAT, "alive", message, Severity.MIN)
            )
        except Exception:
            _log.exception("heartbeat emit failed")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="apex-heartbeat", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.beat()
            self._stop.wait(timeout=self._interval)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
