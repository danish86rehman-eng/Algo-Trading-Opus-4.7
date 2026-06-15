"""Pub/sub transport seam.

This is the boundary that keeps the ntfy decision deferrable (per design doc §12):
everything in 5D is written against `Transport`, so the choice of backend
(ntfy.sh vs self-hosted ntfy) and its reachability is a *config* concern, not an
architectural one. `InMemoryTransport` makes the whole agent testable with no network.
`NtfyTransport` is wired but NOT validated against a live server yet — see the
reachability runbook before relying on it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator, Mapping, Protocol

_log = logging.getLogger("apex.comms.transport")


@dataclass(frozen=True)
class OutboundMessage:
    topic: str
    body: str
    title: str | None = None
    priority: int = 3
    tags: tuple[str, ...] = ()
    # ntfy action buttons, kept as plain dicts so the transport stays generic.
    actions: tuple[Mapping[str, str], ...] = ()
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InboundMessage:
    id: str
    topic: str
    body: str
    ts: float


class Transport(Protocol):
    def publish(self, msg: OutboundMessage) -> None:
        """Deliver one message. May raise on transport failure (caller handles retry)."""

    def poll(self, topic: str, since: str | float | None = None) -> list[InboundMessage]:
        """Return messages available since `since` (non-blocking). Robust to flaky links."""

    def close(self) -> None:
        ...


class InMemoryTransport:
    """In-process pub/sub for tests and local dev. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._published: list[OutboundMessage] = []
        self._inbox: dict[str, list[InboundMessage]] = {}
        self._seq = 0

    # --- outbound -----------------------------------------------------------
    def publish(self, msg: OutboundMessage) -> None:
        with self._lock:
            self._published.append(msg)

    @property
    def published(self) -> list[OutboundMessage]:
        with self._lock:
            return list(self._published)

    # --- inbound (test helper: simulate a phone publishing a command) -------
    def inject(self, topic: str, body: str, *, ts: float | None = None) -> InboundMessage:
        with self._lock:
            self._seq += 1
            m = InboundMessage(id=str(self._seq), topic=topic, body=body, ts=ts or time.time())
            self._inbox.setdefault(topic, []).append(m)
            return m

    def poll(self, topic: str, since: str | float | None = None) -> list[InboundMessage]:
        with self._lock:
            msgs = list(self._inbox.get(topic, ()))
        if since is None:
            return msgs
        try:
            after = int(since)
        except (TypeError, ValueError):
            return msgs
        return [m for m in msgs if int(m.id) > after]

    def close(self) -> None:
        pass


class NtfyTransport:
    """ntfy backend over stdlib urllib (no third-party deps).

    DEFERRED: implemented but not exercised against a live server. Validate with the
    §12 reachability runbook before depending on it in production.
    """

    def __init__(
        self,
        base_url: str,
        *,
        read_token: str | None = None,
        write_token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._read_token = read_token
        self._write_token = write_token
        self._timeout = timeout

    def _request(self, url: str, *, data: bytes | None, headers: dict[str, str]) -> bytes:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 (trusted base_url)
            return resp.read()

    def publish(self, msg: OutboundMessage) -> None:
        headers: dict[str, str] = {
            "Priority": str(msg.priority),
            **dict(msg.headers),
        }
        if msg.title:
            headers["Title"] = msg.title
        if msg.tags:
            headers["Tags"] = ",".join(msg.tags)
        if msg.actions:
            headers["Actions"] = json.dumps(list(msg.actions))
        if self._write_token:
            headers["Authorization"] = f"Bearer {self._write_token}"
        self._request(f"{self._base}/{msg.topic}", data=msg.body.encode("utf-8"), headers=headers)

    def poll(self, topic: str, since: str | float | None = None) -> list[InboundMessage]:
        since_q = "all" if since is None else str(since)
        url = f"{self._base}/{topic}/json?poll=1&since={since_q}"
        headers: dict[str, str] = {}
        if self._read_token:
            headers["Authorization"] = f"Bearer {self._read_token}"
        raw = self._request(url, data=None, headers=headers).decode("utf-8")
        out: list[InboundMessage] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") != "message":
                continue  # skip keepalive/open events
            out.append(
                InboundMessage(
                    id=str(obj.get("id", "")),
                    topic=str(obj.get("topic", topic)),
                    body=str(obj.get("message", "")),
                    ts=float(obj.get("time", time.time())),
                )
            )
        return out

    def close(self) -> None:
        pass
