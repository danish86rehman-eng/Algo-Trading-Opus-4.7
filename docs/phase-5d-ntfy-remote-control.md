# Phase 5D — Remote Notify + Control via ntfy.sh

> **Status:** Brainstorm / design. Not implemented.
> **Depends on:** Phase 5C (Notifier seam + heartbeat) — *not yet executed*. This document
> designs **against 5C's committed interfaces**, which are **assumed** here (see
> [§2 Assumptions](#2-assumptions-about-5c)). Correct these before building 5D.
> **Driver:** Telegram is blocked in Pakistan → use [ntfy.sh](https://ntfy.sh) for
> push notifications and remote control of the APEX trading bot.

---

## 1. Goals & scope

**Notify (outbound):** push trade / kill / error alerts to the operator's phone.

**Control (inbound):** trigger `KILL` / `STOP` and query `STATUS` remotely from the phone.

### In scope for 5D
- `NtfyNotifier` implementing the 5C `Notifier` seam (trade/kill/error/heartbeat alerts).
- `CommandListener` — inbound channel that receives remote commands.
- `ControlPlane` — a thin, auditable seam wrapping the bot's existing kill-switch / state API.
- Command set: **`KILL`, `STOP`, `STATUS`** only.
- Security model (auth, signing, replay protection) appropriate to "an attacker can publish".
- Supervised reconnect; isolation so the comms layer can never crash the trading loop.

### Out of scope (defer to 5E+)
- Remote `START` / `RESUME` (re-arming the bot remotely is the dangerous direction — keep it manual).
- Remote parameter / risk changes.
- Multi-operator, RBAC, conversational control.
- Full dead-man's-switch infrastructure (noted in §8 as complementary).

### Design principle: KILL is the *safe* direction
For a trading bot, `KILL`/`STOP` move toward **flat & halted**. Even if an attacker
publishes a `KILL`, the worst case is a **nuisance DoS**, not financial loss. That meaningfully
lowers the security bar for those two commands. The dangerous actions (re-arm, change risk)
are deliberately **excluded** from 5D. `STATUS` is the one inbound command that *leaks*
information (positions/PnL), so it still needs auth.

---

## 2. Assumptions about 5C

These are the "committed interfaces" 5D binds to. **Verify against the real 5C code in `C:\apex`.**

```text
# Outbound seam (assumed)
Notifier (protocol/ABC)
    notify(event: NotifierEvent) -> None        # fire-and-forget, best-effort, non-blocking
    # or specialized: notify_trade(...), notify_kill(...), notify_error(...)
NullNotifier / LogNotifier                       # default, no-op / log-only
build_notifier(config) -> Notifier               # factory selected by config

NotifierEvent
    kind: {TRADE, KILL, ERROR, HEARTBEAT, INFO}
    level/severity, title, message, ts, meta: dict

# Heartbeat (assumed)
A periodic task (every N seconds) that emits a HEARTBEAT event / liveness ping
through the Notifier (or a dedicated sink).
```

**If 5C differs**, the only things that move are: the method name `NtfyNotifier` implements,
and where the heartbeat is wired. The rest of 5D (listener, control plane, security) is independent.

**New control-side interface 5D introduces** (does not exist in 5C):

```text
ControlPlane (protocol)
    request_kill(reason: str, source: str) -> Ack
    request_stop(reason: str, source: str) -> Ack
    get_status() -> StatusSnapshot
# Wraps the bot's EXISTING kill-switch / state machine. Does NOT reach into internals.
# If the bot core already exposes a kill-switch API, ControlPlane is a 5-line adapter.
```

---

## 3. ntfy.sh primer (capabilities this design relies on)

**Publish (outbound):** `POST https://ntfy.sh/<topic>` with body = message text.
Useful headers: `Title`, `Priority` (1–5 / min–max), `Tags` (emoji + keywords),
`Click` (open URL on tap), `Actions` (action buttons: `view` / `http` / `broadcast`),
`Markdown: yes`, `Authorization: Bearer <token>`.

**Subscribe (inbound):** long-lived stream `GET /<topic>/json` (newline-delimited JSON),
also `/sse`, `/ws`, `/raw`; or **poll** `GET /<topic>/json?poll=1&since=<id|time>`.
Server-side filters: `?priority=`, `?tags=`, `?message=`.

**Action buttons** are the lever for one-tap control: an `http` action on a notification
can POST a (pre-signed) command back to the bot's control topic.

**Auth/ACL:** ntfy.sh **reserved topics** (Pro) or **self-hosted ntfy** allow per-topic
read/write tokens. On the free public server, a topic is effectively public to anyone who
knows its name.

---

## 4. Architecture (additive — no trading-core changes)

```
            ┌───────────────────────── APEX bot process ─────────────────────────┐
            │                                                                     │
 trade/err  │   Trading core ──events──► Notifier seam (5C) ──► NtfyNotifier ─┐   │
 events     │        ▲                                                        │   │
            │        │ request_kill/stop/get_status                          POST  │
            │   ControlPlane (5D) ◄── dispatch ── CommandListener (5D) ◄─stream─┐  │
            │        │                                                        │  │ │
            │   Heartbeat (5C) ──HEARTBEAT event──► NtfyNotifier ─────────────┘  │ │
            └────────────────────────────────────────────────────────────────│──┘
                                                                              ▼  │
                                        ntfy server (self-hosted or ntfy.sh)     │
                                          notify topic   ◄── outbound POST ──────┘
                                          control topic  ──── inbound stream ────┘
                                                ▲ │
                                          (publish cmd)│ (receive alerts)
                                                │ ▼
                                          Operator phone (ntfy app)
```

Two **separate topics**:
- **notify topic** — outbound-only from bot. Read by phone. Less sensitive.
- **control topic** — inbound to bot. Written by phone (commands). **Must be guarded.**

### New components
1. **`NtfyNotifier`** — implements 5C `Notifier`. Maps events → ntfy publish (see §5).
   Pure addition; selected by `build_notifier(config)`.
2. **`CommandListener`** — own thread/async task. Subscribes to control topic, validates,
   decodes, deduplicates, and dispatches commands to `ControlPlane`. Supervised + auto-reconnect.
3. **`ControlPlane`** — adapter over the bot's existing kill-switch/state API. The *only*
   thing the listener is allowed to call. Keeps blast radius tiny and auditable.

---

## 5. Outbound: event → ntfy mapping

| Event   | Priority | Tags                  | Title                | Notes |
|---------|----------|-----------------------|----------------------|-------|
| TRADE   | default  | `chart_with_upwards_trend` | `APEX • Fill` | symbol/side/qty/price in body |
| KILL    | urgent(5)| `skull`, `rotating_light` | `APEX • KILLED` | always delivered, bypass mute |
| ERROR   | high(4)  | `warning`             | `APEX • Error`       | coalesced (see below) |
| HEARTBEAT | min(1) | `green_heart`         | `APEX • alive`       | mutable; low priority so phone can silence |
| STATUS reply | default | `information_source` | `APEX • Status`  | response to a STATUS command |

**Reliability rules (outbound):**
- **Non-blocking & best-effort.** A failed/slow POST must never block or crash the trading
  loop. Publish via an internal queue + worker thread with a hard timeout.
- **Backpressure:** bounded queue; on overflow drop low-priority (HEARTBEAT/TRADE) first,
  never drop KILL.
- **Error coalescing:** dedupe by error signature + rate-limit (e.g. max N/min) so an error
  storm can't fire 500 notifications.
- **Retry** with capped exponential backoff for transient failures; give up gracefully.
- Secrets/topic names are **never logged**.

---

## 6. Inbound: how a command travels phone → bot

Three UX paths, in increasing friction / decreasing convenience:

1. **Action button (one tap).** Every alert carries an `http` action, e.g. **"KILL"**, that
   POSTs a *pre-signed, short-lived* command to the control topic. The bot signs the token at
   publish time (valid for a window, single-use nonce). Best UX; needs the token-minting logic.
2. **Type a command** in the ntfy app to the control topic: `KILL` (if topic is token-ACL'd)
   or `KILL <totp>` (if relying on app-layer auth). Simple, more friction.
3. **Token-protected control topic** (self-hosted/Pro): the ntfy app stores the write token;
   publishing `kill` is authorized by ntfy itself — no app-layer signing needed.

**Listener mechanics:**
- Prefer **streaming** (`/json` long-lived) for low KILL latency, with **poll fallback**
  (`?poll=1&since=`) for flaky networks.
- Run in an isolated supervised task; crash → reconnect with backoff; **a listener failure
  never touches the trading loop**.
- **Idempotency:** reconnect can replay messages `since` last id → dedupe by command nonce.
  `KILL`/`STOP` are idempotent anyway.

---

## 7. Security model (the crux of "control")

Threat: **anyone who learns the topic name can publish to it.** Layered defense:

1. **Separate notify vs control topics.** Only the control topic needs hard guarding.
2. **Transport ACL (preferred primary):** self-hosted ntfy *or* ntfy.sh reserved topics with
   per-topic read/write **access tokens**. The phone holds the write token; the bot holds the
   read token. Stops random publishers cold.
3. **Application-layer signing (defense-in-depth):** every control command carries
   `HMAC-SHA256(secret, cmd|nonce|ts)`. Bot rejects bad signatures even if the topic leaks.
4. **Replay protection:** `nonce` (single-use, remembered for a window) + `ts` freshness
   window. Requires the bot clock to be sane.
5. **Command allowlist:** only `KILL`/`STOP`/`STATUS` are dispatchable; anything else is dropped
   and logged.
6. **`STATUS` is the sensitive read** (leaks positions/PnL) — it requires the same auth as KILL.
7. **No remote re-arm.** Because the only state-changing remote commands move toward *safe*,
   a worst-case compromise is a DoS, not a loss.

**Recommendation:** transport ACL (token) **+** app-layer HMAC. Action buttons embed a
short-lived single-use signed token for one-tap KILL.

---

## 8. Liveness & dead-man's switch

- The 5C **heartbeat** publishes to the notify (or a dedicated heartbeat) topic at `min`
  priority. Operator can confirm "alive" on demand and mute the routine pings.
- ntfy **cannot** tell you the bot *died*. True dead-man detection (alert on *absence* of
  heartbeat) needs an external watchdog — e.g. heartbeat pings **healthchecks.io**, which
  alerts (it can webhook into ntfy) when a ping is missed. Recommended as a **complementary**
  piece; full watchdog infra is deferred past 5D.

---

## 9. Config & secrets

All via the 5C config seam (env/secrets store), never hard-coded, never logged:

```text
NTFY_BASE_URL        = https://ntfy.sh   # or self-hosted https://ntfy.example.com
NTFY_NOTIFY_TOPIC    = <high-entropy>
NTFY_CONTROL_TOPIC   = <high-entropy, different>
NTFY_READ_TOKEN      = <bot reads control topic>
NTFY_WRITE_TOKEN     = <bot publishes notify topic>
NTFY_HMAC_SECRET     = <app-layer command signing>
NTFY_ENABLED         = true|false        # false → falls back to NullNotifier (testable)
```

---

## 10. Testing

The 5C seam already makes the notifier swappable. Add:
- **Publish mapping:** golden tests for event → ntfy payload (priority/tags/title/body).
- **Fake ntfy server** (httpx mock / local stub) for `NtfyNotifier` and a scripted stream
  for `CommandListener`.
- **Command validation matrix:** good signature, bad signature, expired ts, replayed nonce,
  unknown command, malformed body → only valid ones dispatch.
- **Isolation:** inject listener/publish failures; assert the trading loop is unaffected.
- **Backpressure/coalescing:** queue overflow drops low-priority first, never KILL.

---

## 11. Build order (suggested)

1. `NtfyNotifier` (outbound only) behind the 5C seam + config + tests. Ship alerts first.
2. Wire heartbeat → ntfy.
3. `ControlPlane` adapter over the existing kill-switch/state API.
4. `CommandListener` (poll-only first, then streaming) + security layer + tests.
5. Action-button one-tap KILL (token minting).
6. (Later) external dead-man's-switch.

---

## 12. Reachability verification + self-host fallback runbook

**Why this is a runbook, not a checked fact:** ntfy.sh reachability could not be confirmed
from outside Pakistan. (1) Datacenter/CI egress is policy-restricted and not representative;
(2) PTA blocking is path-specific — a mobile carrier and a DSL line can behave differently —
so only a test on the **operator's own PK connection** is authoritative. No public reports
were found of ntfy.sh being PTA-blocked, but absence of reports ≠ confirmation.

### 12.1 Go/no-go test (run on the real PK connection, before building)
```powershell
# Publish, then read back. Run from C:\apex.
curl.exe -d "apex reachability test" https://ntfy.sh/apex_test_9f3k
curl.exe "https://ntfy.sh/apex_test_9f3k/json?poll=1&since=5m"   # should echo the message
```
Then the real end-to-end check: install the **ntfy app**, subscribe to `apex_test_9f3k`,
re-publish, confirm the push lands. **Test on Wi-Fi AND mobile data** — carriers block
differently. Timeout / RST / hang on any path ⇒ blocked or throttled there.

### 12.2 Decision
- **All paths OK** → ntfy.sh is viable. Still prefer reserved topics (Pro) for the token ACL
  in §7, or self-host for full control.
- **Any path blocked/flaky** → **self-host** (next).

### 12.3 Self-host fallback (also the recommended default)
The PTA blocks *known* hosts by SNI / IP / DNS. Your own domain on a generic VPS is not on any
blocklist, which sidesteps regional blocking **and** gives the per-topic read/write token ACLs
the §7 security model wants anyway.
1. VPS (any region reachable from PK) running ntfy via Docker; put it behind **your own domain**,
   ideally **Cloudflare-fronted** (shared IP + standard SNI = hard to single out).
2. Enable auth: create read token (bot) + write token (phone); lock the control topic ACL.
3. Point the bot at it: `NTFY_BASE_URL=https://ntfy.example.com` — **no other code changes**;
   the entire 5D architecture is unchanged.
4. Re-run §12.1 against the self-hosted URL to confirm.

> Net effect: reachability is a config/ops decision (`NTFY_BASE_URL`), not an architectural one.
> The design in this doc holds whether the backend is ntfy.sh or self-hosted.

---

## 13. Open questions for the operator

1. **Self-hosted ntfy vs ntfy.sh reserved topics (Pro)?** Determines the transport-auth model
   in §7. (Self-hosting also de-risks regional blocking.)
2. **Is ntfy.sh actually reachable & reliable from your PK network?** Run the §12 runbook
   before building; if any path is blocked, self-host per §12.3 (recommended default anyway).
3. **What is 5C's exact `Notifier` method signature** and event model? (§2 is assumed.)
4. **Does the bot core already expose a kill-switch / state API** for `ControlPlane` to wrap,
   or does 5D need to define it?
5. **One-tap action buttons vs typed commands** as the primary control UX?
