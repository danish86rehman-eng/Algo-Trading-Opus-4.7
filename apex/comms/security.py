"""Application-layer command authentication (design doc §7).

Defense-in-depth for inbound control commands: every command is HMAC-signed and carries a
single-use nonce + timestamp. This holds even if the control topic itself leaks, and is
independent of any transport-level (ntfy token/ACL) protection.

Wire format (one line, pipe-delimited, URL-safe):  action|nonce|ts|sig
  sig = HMAC-SHA256(secret, "action\nnonce\nts")  hex-encoded
"""
from __future__ import annotations

import hmac
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable


class CommandError(Exception):
    """Raised when a command is malformed, mis-signed, stale, or replayed."""


@dataclass(frozen=True)
class SignedCommand:
    action: str
    nonce: str
    ts: float
    sig: str


class CommandSecurity:
    def __init__(
        self,
        secret: bytes,
        *,
        ttl_seconds: float = 120.0,
        clock: Callable[[], float] = time.time,
        replay_cache_size: int = 4096,
    ) -> None:
        if not secret:
            raise ValueError("HMAC secret must be non-empty")
        self._secret = secret
        self._ttl = ttl_seconds
        self._clock = clock
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._cache_size = replay_cache_size

    # --- signing ------------------------------------------------------------
    def _mac(self, action: str, nonce: str, ts: float) -> str:
        payload = f"{action}\n{nonce}\n{ts}".encode("utf-8")
        return hmac.new(self._secret, payload, sha256).hexdigest()

    def sign(self, action: str, *, nonce: str | None = None, ts: float | None = None) -> SignedCommand:
        nonce = nonce or secrets.token_urlsafe(12)
        ts = self._clock() if ts is None else ts
        return SignedCommand(action=action, nonce=nonce, ts=ts, sig=self._mac(action, nonce, ts))

    def serialize(self, cmd: SignedCommand) -> str:
        return f"{cmd.action}|{cmd.nonce}|{cmd.ts}|{cmd.sig}"

    # --- parsing & verification --------------------------------------------
    def parse(self, raw: str) -> SignedCommand:
        parts = raw.strip().split("|")
        if len(parts) != 4:
            raise CommandError("malformed command: expected 4 pipe-delimited fields")
        action, nonce, ts_s, sig = parts
        try:
            ts = float(ts_s)
        except ValueError as exc:
            raise CommandError("malformed timestamp") from exc
        return SignedCommand(action=action, nonce=nonce, ts=ts, sig=sig)

    def verify(self, cmd: SignedCommand) -> None:
        """Raise CommandError unless the command is authentic, fresh, and not replayed.

        On success the nonce is recorded so a later replay is rejected.
        """
        expected = self._mac(cmd.action, cmd.nonce, cmd.ts)
        if not hmac.compare_digest(expected, cmd.sig):
            raise CommandError("bad signature")

        now = self._clock()
        if abs(now - cmd.ts) > self._ttl:
            raise CommandError("stale or future-dated command")

        self._evict_expired(now)
        if cmd.nonce in self._seen:
            raise CommandError("replayed nonce")
        self._seen[cmd.nonce] = cmd.ts
        while len(self._seen) > self._cache_size:
            self._seen.popitem(last=False)

    def verify_raw(self, raw: str) -> SignedCommand:
        cmd = self.parse(raw)
        self.verify(cmd)
        return cmd

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._ttl
        stale = [n for n, ts in self._seen.items() if ts < cutoff]
        for n in stale:
            self._seen.pop(n, None)
