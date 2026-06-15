"""CommsAgent — wires the Phase 5D pieces into one start/stop unit.

This is the assembly point: given a CommsConfig and the bot's ControlPlane, it builds the
transport, outbound dispatcher, NtfyNotifier, heartbeat, and CommandListener. The trading
core only ever sees a `Notifier` (5C seam) and hands the agent its ControlPlane.

Backend reachability (ntfy.sh vs self-hosted) is entirely a `CommsConfig.base_url` concern
— flipping it requires no code change (design doc §12).
"""
from __future__ import annotations

from typing import Callable

from .config import CommsConfig
from .control import ControlPlane
from .heartbeat import Heartbeat
from .listener import CommandListener
from .notifier import NullNotifier, Notifier
from .ntfy_notifier import NtfyNotifier
from .outbound import OutboundDispatcher
from .security import CommandSecurity
from .transport import NtfyTransport, Transport


class CommsAgent:
    def __init__(
        self,
        config: CommsConfig,
        control_plane: ControlPlane,
        *,
        transport: Transport | None = None,
        heartbeat_status_fn: Callable[[], str] | None = None,
    ) -> None:
        config.validate()
        self._config = config
        self._control = control_plane
        self._heartbeat_status_fn = heartbeat_status_fn

        self.notifier: Notifier = NullNotifier()
        self._dispatcher: OutboundDispatcher | None = None
        self._listener: CommandListener | None = None
        self._heartbeat: Heartbeat | None = None

        if not config.enabled:
            return

        self._transport = transport or NtfyTransport(
            config.base_url, read_token=config.read_token, write_token=config.write_token
        )
        security = CommandSecurity(config.hmac_secret, ttl_seconds=config.command_ttl_s)

        self._dispatcher = OutboundDispatcher(
            self._transport,
            max_queue=config.max_queue,
            coalesce_window=config.coalesce_window_s,
        )
        self.notifier = NtfyNotifier(
            self._dispatcher,
            config.notify_topic,
            control_topic=config.control_topic,
            control_base_url=config.base_url,
            security=security,
        )
        self._listener = CommandListener(
            transport=self._transport,
            control_topic=config.control_topic,
            security=security,
            control_plane=control_plane,
            notifier=self.notifier,
            poll_interval=config.poll_interval_s,
        )
        self._heartbeat = Heartbeat(
            self.notifier,
            interval_s=config.heartbeat_interval_s,
            status_fn=heartbeat_status_fn,
        )

    def start(self) -> None:
        if self._dispatcher:
            self._dispatcher.start()
        if self._listener:
            self._listener.start()
        if self._heartbeat:
            self._heartbeat.start()

    def stop(self) -> None:
        if self._heartbeat:
            self._heartbeat.stop()
        if self._listener:
            self._listener.stop()
        if self._dispatcher:
            self._dispatcher.stop()
