# Phase 5C — Ops Survivability

**Date:** 2026-06-15  
**Branch:** `claude/apex-phase5c-ops-survivability-3zdoft`  
**Package:** `apex/ops/`

Phase 5C delivers the primitives that let the trading bot survive unhealthy conditions
without losing money. It does NOT depend on Phase 5D (comms) — it is a lower layer that
5D hooks into.

---

## Background

Phase 5D's heartbeat notes: *"True dead-man's-switch detection (alerting on absence of a
heartbeat) is an external concern — see design doc §8."* Phase 5C is that external concern.
Phase 5D's `Notifier` / `NotifierEvent` notes say those types are *"expected to be committed
by Phase 5C"*; they are reproduced verbatim here so the seam is clean.

---

## Deliverables (one task per section)

### Task 1 — CircuitBreaker (`apex/ops/circuit_breaker.py`)

A three-state machine (CLOSED → OPEN → HALF_OPEN → CLOSED) that prevents a
malfunctioning subsystem from cascading into uncontrolled order submission.

States:
- **CLOSED** — normal; failures are counted.
- **OPEN** — tripped; all calls are rejected immediately.  After `recovery_timeout_s` the
  breaker self-transitions to HALF_OPEN.
- **HALF_OPEN** — one probe is allowed; success → CLOSED, failure → OPEN (timer reset).

Public API:
```python
cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=30.0)
cb.record_success()            # resets consecutive-failure counter
cb.record_failure()            # increments counter; trips to OPEN at threshold
cb.is_closed() -> bool         # False when OPEN or during initial HALF_OPEN cooldown
cb.state -> CircuitState       # CLOSED | OPEN | HALF_OPEN
```

Optional `on_open: Callable[[], None]` callback fires once when the breaker opens (for
logging / notification). MUST NOT raise.

Tests: state transitions, trip at threshold, recovery after timeout, half-open probe
success/failure, callback fires exactly once per trip, thread-safety smoke-test.

---

### Task 2 — Watchdog (`apex/ops/watchdog.py`)

A dead-man switch that fires a callback if the main loop stops checking in.

```python
wd = Watchdog(timeout_s=60.0, on_timeout=my_callback)
wd.start()
wd.feed()    # call this from the trading loop each iteration
wd.stop()
```

- `feed()` resets the internal timer.
- If `timeout_s` elapses without a `feed()`, `on_timeout(reason: str)` is called once from
  the watchdog thread, then the watchdog stops itself.
- `start()` / `stop()` are idempotent.
- Thread-safe.

Tests: normal feed keeps watchdog quiet, timeout fires callback exactly once, stop cancels
the watchdog before timeout, restart after stop.

---

### Task 3 — ThrottledNotifier (`apex/ops/throttle.py`)

Wraps any `Notifier` to rate-limit noisy events and protect the operator's phone from
notification floods during error cascades.

```python
inner = LogNotifier()
n = ThrottledNotifier(inner, max_per_kind_per_window=5, window_s=60.0)
n.notify(event)   # passes through or silently drops
n.close()
```

Rules:
- Events with `Severity.URGENT` are **never** throttled (KILL must always get through).
- Each `EventKind` has its own independent window counter.
- When a kind is throttled, a single *"N events suppressed"* summary is emitted once the
  window resets (so the operator knows they were throttled).
- Implements the `Notifier` protocol.

Tests: under-threshold events pass, over-threshold events dropped, URGENT always passes,
summary emitted on window reset, close delegates to inner.

---

### Task 4 — Package wiring (`apex/ops/__init__.py`)

Clean public exports and a convenience factory:

```python
from apex.ops import CircuitBreaker, CircuitState, Watchdog, ThrottledNotifier
```

No logic — imports only.

---

## Running the tests

```bash
python3 -m pytest tests/test_ops.py -v
```

All Phase 5D tests must still pass after this phase lands:

```bash
python3 -m pytest tests/ -q
```
