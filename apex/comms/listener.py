"""CommandListener — inbound half of Phase 5D (design doc §6, §7).

Subscribes to the control topic, authenticates each command (HMAC + nonce + freshness),
enforces the KILL/STOP/STATUS allowlist, dispatches to the ControlPlane, and replies via
the Notifier. Runs in its own supervised thread with backoff reconnect.

ISOLATION GUARANTEE: every command is processed inside `handle_raw`, which never raises —
a malformed command, a bad signature, or a ControlPlane failure can never propagate out and
take down the trading loop.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .control import ControlAction, ControlPlane
from .events import EventKind, NotifierEvent, Severity
from .notifier import Notifier
from .security import CommandError, CommandSecurity
from .transport import InboundMessage, Transport

_log = logging.getLogger("apex.comms.listener")


class CommandListener:
    def __init__(
        self,
        *,
        transport: Transport,
        control_topic: str,
        security: CommandSecurity,
        control_plane: ControlPlane,
        notifier: Notifier,
        source: str = "ntfy",
        poll_interval: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._topic = control_topic
        self._security = security
        self._control = control_plane
        self._notifier = notifier
        self._source = source
        self._poll_interval = poll_interval
        self._clock = clock

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._since: str | float | None = None
        self._processed: set[str] = set()

    # --- pure, exception-safe command handling ------------------------------
    def handle_raw(self, inbound: InboundMessage) -> NotifierEvent | None:
        """Validate, dispatch, and return a reply event. NEVER raises."""
        try:
            if inbound.id and inbound.id in self._processed:
                return None  # transport replay of an already-handled message
            cmd = self._security.verify_raw(inbound.body)
        except CommandError as exc:
            _log.warning("rejected command: %s", exc)
            return None
        except Exception:  # paranoia: nothing from a remote peer should ever escape
            _log.exception("unexpected error validating command")
            return None

        if inbound.id:
            self._processed.add(inbound.id)

        try:
            action = ControlAction.parse(cmd.action)
        except ValueError as exc:
            _log.warning("rejected command: %s", exc)
            return None

        try:
            return self._dispatch(action)
        except Exception:
            _log.exception("control action %s failed", action.value)
            return NotifierEvent(
                kind=EventKind.ERROR,
                title="control",
                message=f"remote {action.value} failed (see logs)",
                severity=Severity.HIGH,
            )

    def _dispatch(self, action: ControlAction) -> NotifierEvent:
        reason = f"remote {action.value} via {self._source}"
        if action is ControlAction.KILL:
            ack = self._control.request_kill(reason, self._source)
            return NotifierEvent(EventKind.KILL, "KILLED", ack.detail or reason, Severity.URGENT)
        if action is ControlAction.STOP:
            ack = self._control.request_stop(reason, self._source)
            return NotifierEvent(EventKind.INFO, "STOP", ack.detail or reason, Severity.HIGH)
        # STATUS
        snap = self._control.get_status()
        return NotifierEvent(EventKind.STATUS, "Status", snap.summary(), Severity.DEFAULT)

    # --- supervised polling loop -------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="apex-listener", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        backoff = self._poll_interval
        while not self._stop.is_set():
            try:
                self.poll_once()
                backoff = self._poll_interval  # healthy: reset
                self._stop.wait(timeout=self._poll_interval)
            except Exception:
                _log.exception("listener poll failed; backing off %.1fs", backoff)
                self._stop.wait(timeout=backoff)
                backoff = min(backoff * 2, 60.0)

    def poll_once(self) -> int:
        """Fetch and process one batch. Returns the number of messages handled."""
        messages = self._transport.poll(self._topic, since=self._since)
        handled = 0
        for m in messages:
            if m.id:
                self._since = m.id
            reply = self.handle_raw(m)
            handled += 1
            if reply is not None:
                try:
                    self._notifier.notify(reply)
                except Exception:
                    _log.exception("failed to send reply")
        return handled

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
