"""Outbound dispatch with the reliability rules from design doc §5.

- Non-blocking & best-effort: a slow/failed publish never blocks or crashes the caller.
- Bounded queue with priority-aware drop: on overflow drop the lowest-priority *droppable*
  item first; KILL (non-droppable) is never dropped.
- Coalescing: identical events (same dedupe key within a window) are collapsed so an error
  storm can't fan out into hundreds of pushes.
- Capped exponential-backoff retry in the worker; give up gracefully.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .transport import OutboundMessage, Transport

_log = logging.getLogger("apex.comms.outbound")


@dataclass
class _Item:
    seq: int
    msg: OutboundMessage
    droppable: bool


class OutboundDispatcher:
    def __init__(
        self,
        transport: Transport,
        *,
        max_queue: int = 256,
        coalesce_window: float = 5.0,
        max_retries: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._max_queue = max_queue
        self._coalesce_window = coalesce_window
        self._max_retries = max_retries
        self._clock = clock

        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._queue: list[_Item] = []
        self._seq = 0
        self._last_seen: dict[str, float] = {}
        self._dropped = 0
        self._coalesced = 0

        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    # --- producer side (called from the trading thread) ---------------------
    def submit(
        self,
        msg: OutboundMessage,
        *,
        priority: int,
        droppable: bool = True,
        dedupe_key: str | None = None,
    ) -> bool:
        """Enqueue a message. Returns False if it was coalesced or dropped. Never blocks."""
        now = self._clock()
        with self._not_empty:
            if dedupe_key is not None:
                last = self._last_seen.get(dedupe_key)
                if last is not None and (now - last) < self._coalesce_window:
                    self._coalesced += 1
                    return False
                self._last_seen[dedupe_key] = now

            if len(self._queue) >= self._max_queue and not self._make_room(priority, droppable):
                self._dropped += 1
                return False

            self._seq += 1
            self._queue.append(_Item(seq=self._seq, msg=msg, droppable=droppable))
            self._queue.sort(key=lambda it: (-it.msg.priority, it.seq))  # high priority first
            self._not_empty.notify()
            return True

    def _make_room(self, incoming_priority: int, incoming_droppable: bool) -> bool:
        """Evict the lowest-priority droppable item if the incoming one outranks it."""
        victim_idx = None
        for i, it in enumerate(self._queue):
            if not it.droppable:
                continue
            if victim_idx is None or it.msg.priority < self._queue[victim_idx].msg.priority:
                victim_idx = i
        if victim_idx is None:
            return False  # queue is all non-droppable (e.g. all KILLs)
        victim = self._queue[victim_idx]
        # A droppable incoming item must strictly outrank the victim to justify eviction.
        if incoming_droppable and incoming_priority <= victim.msg.priority:
            return False
        del self._queue[victim_idx]
        self._dropped += 1
        return True

    # --- consumer side ------------------------------------------------------
    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="apex-outbound", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._not_empty:
                while not self._queue and not self._stop.is_set():
                    self._not_empty.wait(timeout=0.5)
                if self._stop.is_set() and not self._queue:
                    return
                item = self._queue.pop(0) if self._queue else None
            if item is not None:
                self._deliver(item.msg)

    def _deliver(self, msg: OutboundMessage) -> None:
        delay = 0.5
        for attempt in range(self._max_retries + 1):
            try:
                self._transport.publish(msg)
                return
            except Exception:
                if attempt >= self._max_retries:
                    _log.warning("dropping message to %s after %d retries", msg.topic, attempt)
                    return
                time.sleep(delay)
                delay = min(delay * 2, 8.0)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._not_empty:
            self._not_empty.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=timeout)
            self._worker = None

    # --- introspection (tests/metrics) -------------------------------------
    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"queued": len(self._queue), "dropped": self._dropped, "coalesced": self._coalesced}
