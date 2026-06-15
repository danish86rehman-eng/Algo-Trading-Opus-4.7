"""NtfyNotifier — outbound half of Phase 5D (design doc §5).

Implements the 5C `Notifier` seam. Maps NotifierEvent -> ntfy OutboundMessage
(priority/tags/title) and hands it to the OutboundDispatcher (non-blocking, best-effort).
Optionally attaches a one-tap "KILL" action button carrying a short-lived, single-use
signed command, so the operator can halt the bot straight from any notification.
"""
from __future__ import annotations

from typing import Mapping

from .control import ControlAction
from .events import EventKind, NotifierEvent, Severity
from .outbound import OutboundDispatcher
from .security import CommandSecurity
from .transport import OutboundMessage

# Per-kind presentation. Priority comes from the event severity; these are the tags/labels.
_PRESENTATION: dict[EventKind, tuple[str, tuple[str, ...]]] = {
    EventKind.TRADE: ("Fill", ("chart_with_upwards_trend",)),
    EventKind.KILL: ("KILLED", ("skull", "rotating_light")),
    EventKind.ERROR: ("Error", ("warning",)),
    EventKind.HEARTBEAT: ("alive", ("green_heart",)),
    EventKind.STATUS: ("Status", ("information_source",)),
    EventKind.INFO: ("Info", ("information_source",)),
}


class NtfyNotifier:
    def __init__(
        self,
        dispatcher: OutboundDispatcher,
        topic: str,
        *,
        control_topic: str | None = None,
        control_base_url: str | None = None,
        security: CommandSecurity | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._topic = topic
        # One-tap KILL action is only attached when we can both sign it and address the
        # control topic — otherwise we ship the notification without the button.
        self._control_topic = control_topic
        self._control_base_url = control_base_url
        self._security = security

    def notify(self, event: NotifierEvent) -> None:
        label, tags = _PRESENTATION.get(event.kind, ("Info", ()))
        msg = OutboundMessage(
            topic=self._topic,
            body=event.message,
            title=f"APEX • {label}",
            priority=int(event.severity),
            tags=tags,
            actions=self._kill_action(event),
        )
        # ERROR storms coalesce by message signature; KILL must never be dropped.
        droppable = event.kind is not EventKind.KILL
        dedupe_key = f"error:{event.message}" if event.kind is EventKind.ERROR else None
        self._dispatcher.submit(
            msg, priority=int(event.severity), droppable=droppable, dedupe_key=dedupe_key
        )

    def _kill_action(self, event: NotifierEvent) -> tuple[Mapping[str, str], ...]:
        if event.kind is EventKind.KILL:
            return ()  # already dead — no point offering a KILL button
        if not (self._security and self._control_topic and self._control_base_url):
            return ()
        signed = self._security.serialize(self._security.sign(ControlAction.KILL.value))
        url = f"{self._control_base_url.rstrip('/')}/{self._control_topic}"
        return (
            {
                "action": "http",
                "label": "KILL",
                "method": "POST",
                "url": url,
                "body": signed,
                "clear": "true",
            },
        )

    def close(self) -> None:
        self._dispatcher.stop()


__all__ = ["NtfyNotifier", "Severity"]
