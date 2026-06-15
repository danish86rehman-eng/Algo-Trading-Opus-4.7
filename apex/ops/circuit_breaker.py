"""CircuitBreaker — prevents cascading failures from reaching the order layer.

Three states:
  CLOSED    — normal operation; failures are counted.
  OPEN      — tripped; is_closed() returns False immediately.
  HALF_OPEN — one probe allowed; record_success() → CLOSED, record_failure() → OPEN.

The optional `on_open` callback fires exactly once per trip (never on re-trips while
already open) and MUST NOT raise into the circuit breaker.
"""
from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Callable

_log = logging.getLogger("apex.ops.circuit_breaker")


class CircuitState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 60.0,
        on_open: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._threshold = failure_threshold
        self._timeout = recovery_timeout_s
        self._on_open = on_open
        self._clock = clock

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    # ------------------------------------------------------------------
    # State inquiry
    # ------------------------------------------------------------------
    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._effective_state()

    def is_closed(self) -> bool:
        """Return True if the caller should proceed, False if the circuit is open.

        Calling this from OPEN after the recovery timeout transitions to HALF_OPEN
        and returns True (allowing exactly one probe through).
        """
        with self._lock:
            s = self._effective_state()
            if s is CircuitState.CLOSED:
                return True
            if s is CircuitState.OPEN:
                return False
            # HALF_OPEN — the first is_closed() call after transition already moved us here;
            # state stays HALF_OPEN until record_success/failure resolves the probe.
            return True

    # ------------------------------------------------------------------
    # Recording outcomes
    # ------------------------------------------------------------------
    def record_success(self) -> None:
        with self._lock:
            s = self._effective_state()
            self._consecutive_failures = 0
            if s is CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            s = self._effective_state()
            if s is CircuitState.OPEN:
                return  # already open; don't re-fire callback
            self._consecutive_failures += 1
            if s is CircuitState.HALF_OPEN or self._consecutive_failures >= self._threshold:
                was_closed = s is not CircuitState.OPEN
                self._trip()
                if was_closed and self._on_open:
                    self._fire_callback()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _effective_state(self) -> CircuitState:
        """Resolve OPEN → HALF_OPEN if the recovery timeout has elapsed (lock held)."""
        if self._state is CircuitState.OPEN:
            if self._opened_at is not None and (self._clock() - self._opened_at) >= self._timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def _trip(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._consecutive_failures = 0

    def _fire_callback(self) -> None:
        try:
            self._on_open()  # type: ignore[misc]
        except Exception:
            _log.exception("circuit breaker on_open callback raised")
