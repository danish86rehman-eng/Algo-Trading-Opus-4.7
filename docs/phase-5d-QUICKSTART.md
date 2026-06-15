# Phase 5D Quickstart (plain language)

You said "you decide" — so here are the decisions already made for you, and the short list
of things *you* do. No servers to run, no keys to invent.

## Decisions made for you
| Question | Decision | Why |
|---|---|---|
| Which notification service? | **ntfy.sh** (free public server) | Telegram is blocked in PK; ntfy needs no account and no hosting. |
| How to keep control safe? | **Every KILL/STOP command is cryptographically signed** (built in) | Even if someone guesses your topic, they can't send commands. |
| Self-host or not? | **No — start on ntfy.sh.** Self-hosting is a *later* option | Less to manage. KILL only ever makes the bot *safer*, so the bar is low. |
| How do you press KILL? | **Both**: type `KILL` in the app, *or* tap the KILL button on any alert | One-tap is easiest; typing always works. |
| The "5C" interface bits I assumed | **Left as working stand-ins** | When your real 5C code is in front of me, swapping them is a 2-line change. |

You do **not** need to understand the code to use this.

## What you do (about 5 minutes)
1. Run the setup once — it creates your private keys and topic names:
   ```
   python3 scripts/setup_comms.py
   ```
   This writes a `.env` file (kept private, never committed).
2. Install the **ntfy** app on your phone and **Subscribe** to the two topic names it printed
   (one ends in `-notify`, one in `-control`).
3. **Test that ntfy reaches your phone** (do this on both Wi-Fi and mobile data). Copy the
   `curl` line the setup script printed and run it — a notification should pop up on your phone.
4. If it works: open `.env`, change `NTFY_ENABLED=false` to `true`, and start the bot as usual.

## If the test notification never arrives
ntfy.sh might be blocked on your network. **Just tell me** — I'll switch you to a private
server. For you that's still only: install an app, subscribe, test. The bot code doesn't change.

## What you get once it's on
- 📈 Phone alerts for trades, ❌ errors, and 💀 kills.
- A 💚 "still alive" heartbeat you can mute.
- Send **KILL** / **STOP** / **STATUS** from your phone — tap a button or type the word.

See `docs/phase-5d-ntfy-remote-control.md` for the full design, and `apex/comms/README.md`
for how it plugs into the bot.
