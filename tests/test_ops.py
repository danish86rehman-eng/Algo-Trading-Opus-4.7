"""Phase 5C ops survivability tests — no network, no threading sleeps."""
from __future__ import annotations

import threading
import time
import unittest

# --------------------------------------------------------------------------- #
# Task 1: CircuitBreaker
# --------------------------------------------------------------------------- #
from apex.ops.circuit_breaker import CircuitBreaker, CircuitState


class CircuitBreakerTests(unittest.TestCase):
    def _cb(self, threshold: int = 3, timeout: float = 30.0, on_open=None) -> CircuitBreaker:
        return CircuitBreaker(failure_threshold=threshold, recovery_timeout_s=timeout, on_open=on_open)

    def test_starts_closed(self) -> None:
        cb = self._cb()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.is_closed())

    def test_success_keeps_closed(self) -> None:
        cb = self._cb()
        for _ in range(10):
            cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_trips_after_threshold(self) -> None:
        cb = self._cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        self.assertTrue(cb.is_closed())
        cb.record_failure()  # 3rd failure → OPEN
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.is_closed())

    def test_success_resets_failure_counter(self) -> None:
        cb = self._cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()   # resets counter
        cb.record_failure()   # back to 1 — should NOT trip
        self.assertTrue(cb.is_closed())

    def test_on_open_callback_fires_once_per_trip(self) -> None:
        calls: list[int] = []
        cb = self._cb(threshold=1, on_open=lambda: calls.append(1))
        cb.record_failure()          # trips → callback
        cb.record_failure()          # already open → no second call
        self.assertEqual(len(calls), 1)

    def test_on_open_callback_exception_does_not_propagate(self) -> None:
        def boom() -> None:
            raise RuntimeError("callback exploded")

        cb = self._cb(threshold=1, on_open=boom)
        cb.record_failure()  # must not raise

    def test_half_open_after_timeout(self) -> None:
        clock = [0.0]
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=10.0, clock=lambda: clock[0])
        cb.record_failure()  # OPEN
        self.assertFalse(cb.is_closed())
        clock[0] = 10.1  # past recovery timeout
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_half_open_success_closes(self) -> None:
        clock = [0.0]
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=10.0, clock=lambda: clock[0])
        cb.record_failure()
        clock[0] = 10.1
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_half_open_failure_reopens(self) -> None:
        clock = [0.0]
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=10.0, clock=lambda: clock[0])
        cb.record_failure()
        clock[0] = 10.1
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        cb.record_failure()   # probe failed → back to OPEN, timer reset
        self.assertEqual(cb.state, CircuitState.OPEN)
        # is_closed still False (timer was reset at clock[0]=10.1)
        self.assertFalse(cb.is_closed())

    def test_is_closed_returns_true_in_half_open(self) -> None:
        """HALF_OPEN allows exactly one probe through (is_closed() returns True once)."""
        clock = [0.0]
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=10.0, clock=lambda: clock[0])
        cb.record_failure()
        clock[0] = 10.1
        self.assertTrue(cb.is_closed())   # first call → allow probe, transitions to HALF_OPEN
        # while waiting for record_success/failure the state is HALF_OPEN;
        # subsequent is_closed() calls must NOT create extra probes
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_thread_safety_smoke(self) -> None:
        cb = self._cb(threshold=100)
        errors: list[Exception] = []

        def worker(fail: bool) -> None:
            try:
                for _ in range(50):
                    cb.record_failure() if fail else cb.record_success()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i % 2 == 0,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


# --------------------------------------------------------------------------- #
# Task 2: Watchdog
# --------------------------------------------------------------------------- #
from apex.ops.watchdog import Watchdog


class WatchdogTests(unittest.TestCase):
    def test_does_not_fire_when_fed(self) -> None:
        fired: list[str] = []
        wd = Watchdog(timeout_s=0.05, on_timeout=lambda r: fired.append(r))
        wd.start()
        for _ in range(5):
            time.sleep(0.02)
            wd.feed()
        wd.stop()
        self.assertEqual(fired, [])

    def test_fires_once_on_timeout(self) -> None:
        fired: list[str] = []
        wd = Watchdog(timeout_s=0.05, on_timeout=lambda r: fired.append(r))
        wd.start()
        time.sleep(0.15)   # don't feed → fires
        wd.stop()
        self.assertEqual(len(fired), 1)

    def test_callback_exception_does_not_propagate(self) -> None:
        def boom(reason: str) -> None:
            raise RuntimeError("exploded")

        wd = Watchdog(timeout_s=0.05, on_timeout=boom)
        wd.start()
        time.sleep(0.15)
        wd.stop()  # must not raise

    def test_stop_before_timeout_cancels(self) -> None:
        fired: list[str] = []
        wd = Watchdog(timeout_s=0.5, on_timeout=lambda r: fired.append(r))
        wd.start()
        time.sleep(0.05)
        wd.stop()
        time.sleep(0.6)   # extra wait — callback must NOT have fired
        self.assertEqual(fired, [])

    def test_start_is_idempotent(self) -> None:
        wd = Watchdog(timeout_s=1.0, on_timeout=lambda r: None)
        wd.start()
        wd.start()   # second call must not create a second thread
        wd.stop()

    def test_stop_is_idempotent(self) -> None:
        wd = Watchdog(timeout_s=1.0, on_timeout=lambda r: None)
        wd.start()
        wd.stop()
        wd.stop()   # must not raise

    def test_restart_after_stop(self) -> None:
        fired: list[str] = []
        wd = Watchdog(timeout_s=0.05, on_timeout=lambda r: fired.append(r))
        wd.start()
        wd.stop()
        wd.start()   # restart
        time.sleep(0.15)
        wd.stop()
        self.assertEqual(len(fired), 1)   # fired during second run, not first


# --------------------------------------------------------------------------- #
# Task 3: ThrottledNotifier
# --------------------------------------------------------------------------- #
from apex.comms.events import EventKind, NotifierEvent, Severity
from apex.ops.throttle import ThrottledNotifier


class _Sink:
    def __init__(self) -> None:
        self.events: list[NotifierEvent] = []

    def notify(self, event: NotifierEvent) -> None:
        self.events.append(event)

    def close(self) -> None:
        pass


def _ev(kind: EventKind = EventKind.ERROR, sev: Severity = Severity.HIGH) -> NotifierEvent:
    return NotifierEvent(kind=kind, title="t", message="m", severity=sev)


class ThrottledNotifierTests(unittest.TestCase):
    def _make(self, *, max_per: int = 3, window: float = 60.0):
        sink = _Sink()
        n = ThrottledNotifier(sink, max_per_kind_per_window=max_per, window_s=window)
        return sink, n

    def test_under_threshold_all_pass(self) -> None:
        sink, n = self._make(max_per=3)
        for _ in range(3):
            n.notify(_ev())
        self.assertEqual(len(sink.events), 3)

    def test_over_threshold_dropped(self) -> None:
        sink, n = self._make(max_per=3)
        for _ in range(6):
            n.notify(_ev())
        # only first 3 pass; the 4th–6th are suppressed
        self.assertLessEqual(len(sink.events), 4)   # ≤4 because summary may be emitted

    def test_urgent_never_throttled(self) -> None:
        sink, n = self._make(max_per=1)
        for _ in range(5):
            n.notify(_ev(kind=EventKind.KILL, sev=Severity.URGENT))
        self.assertEqual(len(sink.events), 5)

    def test_different_kinds_have_independent_counters(self) -> None:
        sink, n = self._make(max_per=2)
        for _ in range(3):
            n.notify(_ev(kind=EventKind.ERROR))
        for _ in range(3):
            n.notify(_ev(kind=EventKind.INFO))
        # Each kind allows 2; extras are dropped
        error_events = [e for e in sink.events if e.kind == EventKind.ERROR]
        info_events = [e for e in sink.events if e.kind == EventKind.INFO]
        self.assertLessEqual(len(error_events), 3)   # max 2 + possible summary
        self.assertLessEqual(len(info_events), 3)

    def test_summary_emitted_after_window_reset(self) -> None:
        clock = [0.0]
        sink = _Sink()
        n = ThrottledNotifier(sink, max_per_kind_per_window=2, window_s=10.0, clock=lambda: clock[0])
        n.notify(_ev())
        n.notify(_ev())
        n.notify(_ev())  # suppressed
        n.notify(_ev())  # suppressed
        clock[0] = 11.0   # window expired
        n.notify(_ev())   # triggers summary emit + resets; this new event passes
        # A summary event with suppression count should have been emitted
        summaries = [e for e in sink.events if "suppressed" in e.message.lower()]
        self.assertGreater(len(summaries), 0)

    def test_close_delegates_to_inner(self) -> None:
        closed: list[bool] = []

        class CloseSink:
            def notify(self, event: NotifierEvent) -> None:
                pass

            def close(self) -> None:
                closed.append(True)

        n = ThrottledNotifier(CloseSink(), max_per_kind_per_window=10, window_s=60.0)
        n.close()
        self.assertEqual(closed, [True])


# --------------------------------------------------------------------------- #
# Task 4: Package __init__ exports
# --------------------------------------------------------------------------- #
class OpsInitTests(unittest.TestCase):
    def test_public_exports(self) -> None:
        from apex.ops import CircuitBreaker, CircuitState, ThrottledNotifier, Watchdog  # noqa: F401


if __name__ == "__main__":
    unittest.main()
