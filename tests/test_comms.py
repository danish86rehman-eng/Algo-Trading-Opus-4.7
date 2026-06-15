"""Phase 5D comms agent tests — no network required (InMemoryTransport)."""
from __future__ import annotations

import logging
import unittest

# The agent deliberately logs-and-swallows bad commands / handler failures (isolation
# guarantee); silence that noise so the test output reflects only assertions.
logging.disable(logging.CRITICAL)

from apex.comms import (
    CommandError,
    CommandListener,
    CommandSecurity,
    CommsAgent,
    CommsConfig,
    ControlAction,
    EventKind,
    InMemoryTransport,
    NtfyNotifier,
    NotifierEvent,
    OutboundDispatcher,
    OutboundMessage,
    Severity,
    StatusSnapshot,
)
from apex.comms.control import Ack


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class _RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[NotifierEvent] = []

    def notify(self, event: NotifierEvent) -> None:
        self.events.append(event)

    def close(self) -> None:
        pass


class _FakeControlPlane:
    def __init__(self, *, fail: bool = False) -> None:
        self.kills = 0
        self.stops = 0
        self._fail = fail

    def request_kill(self, reason: str, source: str) -> Ack:
        if self._fail:
            raise RuntimeError("boom")
        self.kills += 1
        return Ack(ok=True, detail="killed")

    def request_stop(self, reason: str, source: str) -> Ack:
        self.stops += 1
        return Ack(ok=True, detail="stopped")

    def get_status(self) -> StatusSnapshot:
        return StatusSnapshot(state="RUNNING", open_positions=2, pnl=12.5, uptime_s=60)


def _security(clock: _Clock) -> CommandSecurity:
    return CommandSecurity(b"top-secret", ttl_seconds=120, clock=clock)


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
class SecurityTests(unittest.TestCase):
    def test_sign_verify_roundtrip(self) -> None:
        sec = _security(_Clock())
        cmd = sec.sign("KILL")
        sec.verify(sec.parse(sec.serialize(cmd)))  # no raise

    def test_bad_signature_rejected(self) -> None:
        sec = _security(_Clock())
        raw = sec.serialize(sec.sign("KILL"))
        tampered = raw.rsplit("|", 1)[0] + "|deadbeef"
        with self.assertRaises(CommandError):
            sec.verify_raw(tampered)

    def test_tampered_action_rejected(self) -> None:
        sec = _security(_Clock())
        cmd = sec.sign("STATUS")
        forged = sec.serialize(cmd).replace("STATUS", "KILL", 1)
        with self.assertRaises(CommandError):
            sec.verify_raw(forged)

    def test_expired_rejected(self) -> None:
        clock = _Clock(1000.0)
        sec = _security(clock)
        raw = sec.serialize(sec.sign("STOP"))
        clock.t = 1000.0 + 200  # ttl is 120s
        with self.assertRaises(CommandError):
            sec.verify_raw(raw)

    def test_replay_rejected(self) -> None:
        sec = _security(_Clock())
        raw = sec.serialize(sec.sign("KILL"))
        sec.verify_raw(raw)  # first time ok
        with self.assertRaises(CommandError):
            sec.verify_raw(raw)  # replay

    def test_malformed_rejected(self) -> None:
        sec = _security(_Clock())
        for bad in ["", "KILL", "a|b|c", "KILL|n|notafloat|sig"]:
            with self.assertRaises(CommandError):
                sec.verify_raw(bad)


# --------------------------------------------------------------------------- #
# Outbound mapping + dispatcher reliability
# --------------------------------------------------------------------------- #
class OutboundTests(unittest.TestCase):
    def test_event_payload_mapping(self) -> None:
        t = InMemoryTransport()
        disp = OutboundDispatcher(t)
        n = NtfyNotifier(disp, "notify-topic")
        n.notify(NotifierEvent(EventKind.KILL, "x", "dead", Severity.URGENT))
        # worker not started; inspect by starting then flushing
        disp.start()
        disp.stop()
        sent = t.published[0]
        self.assertEqual(sent.topic, "notify-topic")
        self.assertEqual(sent.priority, 5)
        self.assertIn("skull", sent.tags)
        self.assertTrue(sent.title.endswith("KILLED"))

    def test_kill_action_button_is_signed(self) -> None:
        clock = _Clock()
        sec = _security(clock)
        t = InMemoryTransport()
        disp = OutboundDispatcher(t)
        n = NtfyNotifier(
            disp, "notify", control_topic="ctrl", control_base_url="https://ntfy.sh", security=sec
        )
        n.notify(NotifierEvent(EventKind.TRADE, "fill", "BTC", Severity.DEFAULT))
        disp.start()
        disp.stop()
        action = t.published[0].actions[0]
        self.assertEqual(action["label"], "KILL")
        self.assertIn("ctrl", action["url"])
        cmd = sec.verify_raw(action["body"])  # the embedded token is valid + a KILL
        self.assertEqual(cmd.action, "KILL")

    def test_kill_event_has_no_kill_button(self) -> None:
        sec = _security(_Clock())
        t = InMemoryTransport()
        disp = OutboundDispatcher(t)
        n = NtfyNotifier(disp, "n", control_topic="c", control_base_url="https://x", security=sec)
        n.notify(NotifierEvent(EventKind.KILL, "k", "dead", Severity.URGENT))
        disp.start()
        disp.stop()
        self.assertEqual(t.published[0].actions, ())

    def test_error_coalescing(self) -> None:
        clock = _Clock(0.0)
        t = InMemoryTransport()
        disp = OutboundDispatcher(t, coalesce_window=5.0, clock=clock)
        n = NtfyNotifier(disp, "n")
        e = NotifierEvent(EventKind.ERROR, "err", "same message", Severity.HIGH)
        n.notify(e)
        n.notify(e)  # within window -> coalesced
        self.assertEqual(disp.stats["queued"], 1)
        self.assertEqual(disp.stats["coalesced"], 1)
        clock.t = 10.0
        n.notify(e)  # window passed -> accepted again
        self.assertEqual(disp.stats["queued"], 2)

    def test_backpressure_drops_low_priority_first_never_kill(self) -> None:
        t = InMemoryTransport()
        disp = OutboundDispatcher(t, max_queue=2)
        low = OutboundMessage(topic="n", body="hb", priority=1)
        disp.submit(low, priority=1, droppable=True)
        disp.submit(low, priority=1, droppable=True)
        self.assertEqual(disp.stats["queued"], 2)
        # KILL must get in by evicting a droppable item
        kill = OutboundMessage(topic="n", body="dead", priority=5)
        self.assertTrue(disp.submit(kill, priority=5, droppable=False))
        self.assertEqual(disp.stats["queued"], 2)
        self.assertEqual(disp.stats["dropped"], 1)
        # a fresh low-priority droppable cannot displace an equal-priority victim
        self.assertFalse(disp.submit(low, priority=1, droppable=True))


# --------------------------------------------------------------------------- #
# Inbound listener
# --------------------------------------------------------------------------- #
class ListenerTests(unittest.TestCase):
    def _make(self, *, fail: bool = False):
        clock = _Clock()
        sec = _security(clock)
        cp = _FakeControlPlane(fail=fail)
        notifier = _RecordingNotifier()
        t = InMemoryTransport()
        listener = CommandListener(
            transport=t,
            control_topic="ctrl",
            security=sec,
            control_plane=cp,
            notifier=notifier,
        )
        return clock, sec, cp, notifier, t, listener

    def test_valid_kill_dispatches_and_replies(self) -> None:
        _, sec, cp, notifier, t, listener = self._make()
        t.inject("ctrl", sec.serialize(sec.sign("KILL")))
        handled = listener.poll_once()
        self.assertEqual(handled, 1)
        self.assertEqual(cp.kills, 1)
        self.assertEqual(notifier.events[-1].kind, EventKind.KILL)

    def test_status_returns_snapshot(self) -> None:
        _, sec, cp, notifier, t, listener = self._make()
        t.inject("ctrl", sec.serialize(sec.sign("STATUS")))
        listener.poll_once()
        reply = notifier.events[-1]
        self.assertEqual(reply.kind, EventKind.STATUS)
        self.assertIn("RUNNING", reply.message)

    def test_bad_signature_not_dispatched(self) -> None:
        _, sec, cp, notifier, t, listener = self._make()
        t.inject("ctrl", "KILL|n|1000.0|deadbeef")
        listener.poll_once()
        self.assertEqual(cp.kills, 0)
        self.assertEqual(notifier.events, [])

    def test_unknown_action_dropped(self) -> None:
        _, sec, cp, notifier, t, listener = self._make()
        t.inject("ctrl", sec.serialize(sec.sign("LAUNCH_MISSILES")))
        listener.poll_once()
        self.assertEqual(cp.kills, 0)
        self.assertEqual(notifier.events, [])

    def test_replayed_command_dispatched_once(self) -> None:
        _, sec, cp, notifier, t, listener = self._make()
        raw = sec.serialize(sec.sign("KILL"))
        t.inject("ctrl", raw)
        t.inject("ctrl", raw)  # same signed payload twice
        listener.poll_once()
        self.assertEqual(cp.kills, 1)  # nonce replay protection

    def test_controlplane_failure_isolated(self) -> None:
        _, sec, cp, notifier, t, listener = self._make(fail=True)
        t.inject("ctrl", sec.serialize(sec.sign("KILL")))
        # must not raise
        listener.poll_once()
        self.assertEqual(notifier.events[-1].kind, EventKind.ERROR)


# --------------------------------------------------------------------------- #
# Transport + full agent wiring
# --------------------------------------------------------------------------- #
class IntegrationTests(unittest.TestCase):
    def test_inmemory_transport_roundtrip(self) -> None:
        t = InMemoryTransport()
        t.inject("c", "hello")
        msgs = t.poll("c")
        self.assertEqual(msgs[0].body, "hello")
        self.assertEqual(t.poll("c", since=msgs[0].id), [])

    def test_agent_disabled_uses_null_notifier(self) -> None:
        agent = CommsAgent(CommsConfig(enabled=False), _FakeControlPlane())
        agent.notifier.notify(NotifierEvent(EventKind.INFO, "x", "y"))  # no-op, no raise
        agent.start()
        agent.stop()

    def test_agent_end_to_end_with_inmemory(self) -> None:
        cfg = CommsConfig(
            enabled=True,
            notify_topic="notify",
            control_topic="ctrl",
            hmac_secret=b"secret",
        )
        t = InMemoryTransport()
        cp = _FakeControlPlane()
        agent = CommsAgent(cfg, cp, transport=t)
        # operator publishes a signed STOP
        sec = CommandSecurity(b"secret", ttl_seconds=120)
        t.inject("ctrl", sec.serialize(sec.sign("STOP")))
        agent._listener.poll_once()  # drive one cycle deterministically
        self.assertEqual(cp.stops, 1)

    def test_config_validation(self) -> None:
        with self.assertRaises(ValueError):
            CommsConfig(enabled=True, notify_topic="a", control_topic="a", hmac_secret=b"x").validate()
        with self.assertRaises(ValueError):
            CommsConfig(enabled=True).validate()  # missing fields


if __name__ == "__main__":
    unittest.main()
