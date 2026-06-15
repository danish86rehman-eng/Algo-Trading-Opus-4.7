"""Watchdog — dead-man switch for the trading loop.

The trading loop calls `feed()` every iteration.  If `timeout_s` elapses without a feed,
`on_timeout(reason)` is called once from the watchdog thread, then the watchdog shuts down.
The callback MUST NOT raise.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

_log = logging.getLogger("apex.ops.watchdog")


class Watchdog:
    def __init__(
        self,
        *,
        timeout_s: float,
        on_timeout: Callable[[str], None],
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._timeout = timeout_s
        self._on_timeout = on_timeout

        self._lock = threading.Lock()
        self._fed = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._fed.clear()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="apex-watchdog", daemon=True)
            self._thread.start()

    def stop(self, join_timeout: float = 2.0) -> None:
        self._stop.set()
        self._fed.set()   # unblock the wait so the thread exits promptly
        with self._lock:
            t = self._thread
        if t is not None:
            t.join(timeout=join_timeout)
            with self._lock:
                if self._thread is t:
                    self._thread = None

    def feed(self) -> None:
        self._fed.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._fed.clear()
            timed_out = not self._fed.wait(timeout=self._timeout)
            if self._stop.is_set():
                return
            if timed_out:
                self._fire()
                return  # watchdog stops after firing once

    def _fire(self) -> None:
        reason = f"no feed within {self._timeout:.1f}s"
        try:
            self._on_timeout(reason)
        except Exception:
            _log.exception("watchdog on_timeout callback raised")
