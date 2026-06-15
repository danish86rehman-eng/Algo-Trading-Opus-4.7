"""Configuration for the comms agent (design doc §9).

All values come from the environment / secrets store; nothing is hard-coded and secrets are
never logged. When disabled, the agent degrades to a NullNotifier and no listener — keeping
the bot fully runnable without comms.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CommsConfig:
    enabled: bool = False
    base_url: str = "https://ntfy.sh"
    notify_topic: str = ""
    control_topic: str = ""
    read_token: str | None = None
    write_token: str | None = None
    hmac_secret: bytes = b""
    heartbeat_interval_s: float = 300.0
    poll_interval_s: float = 2.0
    command_ttl_s: float = 120.0
    max_queue: int = 256
    coalesce_window_s: float = 5.0

    @classmethod
    def from_env(cls) -> "CommsConfig":
        return cls(
            enabled=_flag("NTFY_ENABLED", False),
            base_url=os.environ.get("NTFY_BASE_URL", "https://ntfy.sh"),
            notify_topic=os.environ.get("NTFY_NOTIFY_TOPIC", ""),
            control_topic=os.environ.get("NTFY_CONTROL_TOPIC", ""),
            read_token=os.environ.get("NTFY_READ_TOKEN") or None,
            write_token=os.environ.get("NTFY_WRITE_TOKEN") or None,
            hmac_secret=os.environ.get("NTFY_HMAC_SECRET", "").encode("utf-8"),
            heartbeat_interval_s=float(os.environ.get("NTFY_HEARTBEAT_S", "300")),
            poll_interval_s=float(os.environ.get("NTFY_POLL_S", "2")),
            command_ttl_s=float(os.environ.get("NTFY_CMD_TTL_S", "120")),
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        missing = [
            n
            for n, v in (
                ("NTFY_NOTIFY_TOPIC", self.notify_topic),
                ("NTFY_CONTROL_TOPIC", self.control_topic),
                ("NTFY_HMAC_SECRET", self.hmac_secret),
            )
            if not v
        ]
        if missing:
            raise ValueError(f"comms enabled but missing required config: {', '.join(missing)}")
        if self.notify_topic == self.control_topic:
            raise ValueError("notify and control topics must differ (design doc §4)")
