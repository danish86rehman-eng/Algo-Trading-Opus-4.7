# apex.comms — Phase 5D remote notify + control

Provider-agnostic remote **notify** (trade/kill/error/heartbeat alerts) and **control**
(`KILL` / `STOP` / `STATUS`) for the APEX bot. Design: `docs/phase-5d-ntfy-remote-control.md`.

The ntfy backend lives behind the `Transport` seam, so it is fully buildable and testable
today with **no network**, and switching ntfy.sh ↔ self-hosted is a `base_url` change.

## Layout
| Module | Role |
|--------|------|
| `events.py`, `notifier.py` | **(assumed 5C)** event model + Notifier seam — replace with real 5C imports when it lands |
| `transport.py` | `Transport` seam: `InMemoryTransport` (tests), `NtfyTransport` (deferred, urllib) |
| `outbound.py` | non-blocking dispatch: bounded queue, priority-drop, coalescing, retry |
| `ntfy_notifier.py` | event → ntfy payload; one-tap signed KILL action button |
| `security.py` | HMAC + nonce + freshness command auth (defense-in-depth) |
| `control.py` | `ControlPlane` seam + `CallbackControlPlane` adapter, `StatusSnapshot` |
| `listener.py` | inbound: authenticate → allowlist → dispatch → reply; supervised, isolated |
| `heartbeat.py` | **(assumed 5C)** periodic liveness emit |
| `config.py` / `agent.py` | config from env + `CommsAgent` assembly |

## Wiring into the bot
```python
from apex.comms import CommsAgent, CommsConfig, CallbackControlPlane, StatusSnapshot

control = CallbackControlPlane(
    on_kill=lambda reason, src: bot.kill_switch(reason),   # your existing kill-switch
    on_stop=lambda reason, src: bot.halt(reason),
    status=lambda: StatusSnapshot(state=bot.state, open_positions=bot.n_open, pnl=bot.pnl),
)

agent = CommsAgent(CommsConfig.from_env(), control)
agent.start()
notifier = agent.notifier            # hand this to the trading core (5C Notifier seam)
# notifier.notify(NotifierEvent(EventKind.TRADE, "Fill", "BTC 0.1 @ 65000"))
# ... on shutdown:
agent.stop()
```

When `NTFY_ENABLED` is unset/false the agent is a no-op (`NullNotifier`, no listener), so the
bot runs unchanged without comms. Required env when enabled: `NTFY_NOTIFY_TOPIC`,
`NTFY_CONTROL_TOPIC` (must differ), `NTFY_HMAC_SECRET` (see `config.py`).

## Tests
```
python3 -m unittest discover -s tests
```
No network; uses `InMemoryTransport`. Validate `NtfyTransport` against a live server using the
reachability runbook in the design doc (§12) before relying on it.

## Not done here (by request)
- Live ntfy backend validation / reachability decision — deferred ("ntfy later").
- Binding to the **real** 5C `Notifier`/heartbeat/kill-switch signatures (these are assumed).
