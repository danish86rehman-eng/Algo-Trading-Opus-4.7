"""Notifier seam.

NOTE (5C boundary): the `Notifier` protocol and the null/log/fanout implementations are
expected to be *committed by Phase 5C*. They are reproduced here so 5D is runnable
standalone. 5D's contribution is `NtfyNotifier` (see ntfy_notifier.py), which implements
this same protocol and is selected by `build_notifier()` (config.py).
"""
from __future__ import annotations

import logging
from typing import Iterable, Protocol, runtime_checkable

from .events import NotifierEvent

_log = logging.getLogger("apex.comms.notify")


@runtime_checkable
class Notifier(Protocol):
    def notify(self, event: NotifierEvent) -> None:
        """Fire-and-forget, best-effort. MUST NOT block or raise into the caller."""

    def close(self) -> None:
        ...


class NullNotifier:
    """Default no-op notifier (used when comms are disabled)."""

    def notify(self, event: NotifierEvent) -> None:  # noqa: D401
        pass

    def close(self) -> None:
        pass


class LogNotifier:
    """Writes events to the logger; handy for local dev and as a fanout sink."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or _log

    def notify(self, event: NotifierEvent) -> None:
        self._log.info("[%s] %s — %s", event.kind.value, event.title, event.message)

    def close(self) -> None:
        pass


class FanoutNotifier:
    """Sends each event to several notifiers, isolating per-notifier failures."""

    def __init__(self, notifiers: Iterable[Notifier]) -> None:
        self._notifiers = list(notifiers)

    def notify(self, event: NotifierEvent) -> None:
        for n in self._notifiers:
            try:
                n.notify(event)
            except Exception:  # never let one sink break the others or the caller
                _log.exception("notifier %r failed", type(n).__name__)

    def close(self) -> None:
        for n in self._notifiers:
            try:
                n.close()
            except Exception:
                _log.exception("notifier %r close failed", type(n).__name__)
