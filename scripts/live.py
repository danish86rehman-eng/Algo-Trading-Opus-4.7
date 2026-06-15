"""APEX live trading loop — Exness/MT5 + Phase 5C/5D ops + Phase 6 strategy.

Wires together every layer built so far:
  Phase 5C  CircuitBreaker (protects order submission)
            Watchdog       (fires if the loop stalls)
            ThrottledNotifier (rate-limits phone alerts)
  Phase 5D  CommsAgent     (ntfy push alerts + remote KILL/STOP/STATUS)
  Phase 6   StrategyConfig loaded from config/core.yaml
            evaluate()     pure momentum signal
            MT5Executor    live order submission

Usage:
    python3 scripts/live.py BTCUSD --interval 4h
    python3 scripts/live.py XAUUSD --interval 1h --lot 0.01 --dry-run

Flags:
    --dry-run   Run the full loop but skip actual order submission (paper mode).
    --lot       Lot size per trade (default 0.01).
    --interval  Bar timeframe, same as fetch_ohlcv.py (default 4h).
    --bars      Indicator lookback window (default 200 bars).
    --config    Path to core.yaml (default config/core.yaml).

Remote control (via ntfy, requires NTFY_ENABLED=true in .env):
    Send KILL   → closes position and shuts down.
    Send STOP   → closes position and shuts down.
    Send STATUS → replies with current P&L and state.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

# ── import path ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apex.comms.agent import CommsAgent
from apex.comms.config import CommsConfig
from apex.comms.control import CallbackControlPlane, StatusSnapshot
from apex.comms.events import EventKind, NotifierEvent, Severity
from apex.data.mt5_executor import MT5Executor
from apex.data.mt5_loader import load_from_mt5
from apex.ops.circuit_breaker import CircuitBreaker
from apex.ops.throttle import ThrottledNotifier
from apex.ops.watchdog import Watchdog
from apex.strategy.config_loader import load_execution_config, load_strategy_config
from apex.strategy.decision import evaluate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("apex.live")

# ── interval → seconds ───────────────────────────────────────────────────────
_INTERVAL_S: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "8h": 28800, "12h": 43200, "1d": 86400,
}


# ═══════════════════════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════════════════════

class BotState:
    def __init__(self) -> None:
        self.running = True
        self.state_name = "STARTING"
        self.start_ts = time.time()
        self.last_signal = "HOLD"
        self.pnl = 0.0          # running estimate
        self.n_trades = 0

    def uptime(self) -> float:
        return time.time() - self.start_ts

    def snapshot(self, executor: MT5Executor) -> StatusSnapshot:
        return StatusSnapshot(
            state=self.state_name,
            open_positions=1 if executor.in_position else 0,
            pnl=self.pnl,
            uptime_s=self.uptime(),
            extra={"last_signal": self.last_signal, "n_trades": self.n_trades},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Trading loop
# ═══════════════════════════════════════════════════════════════════════════════

def run(args: argparse.Namespace) -> None:
    # ── config ───────────────────────────────────────────────────────────────
    cfg_path = args.config or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "core.yaml",
    )
    strategy_cfg = load_strategy_config(cfg_path)
    exec_cfg = load_execution_config(cfg_path)
    _log.info("loaded strategy config: %s", strategy_cfg)

    interval_s = _INTERVAL_S.get(args.interval, 3600)
    symbol = args.symbol.upper()

    # ── Phase 6: executor + state ─────────────────────────────────────────────
    executor = MT5Executor(symbol, lot_size=args.lot)
    state = BotState()

    # ── Phase 5C: circuit breaker + watchdog ─────────────────────────────────
    cb = CircuitBreaker(
        failure_threshold=3,
        recovery_timeout_s=300.0,
        on_open=lambda: _log.error("circuit OPEN — order submission paused"),
    )
    wd = Watchdog(
        timeout_s=interval_s * 3,
        on_timeout=lambda r: (_log.critical("watchdog: %s", r), _halt(state, executor)),
    )

    # ── Phase 5D: comms agent ────────────────────────────────────────────────
    control = CallbackControlPlane(
        on_kill=lambda reason, src: _kill(state, executor, reason),
        on_stop=lambda reason, src: _kill(state, executor, reason),
        status=lambda: state.snapshot(executor),
    )
    comms = CommsAgent(CommsConfig.from_env(), control)
    raw_notifier = comms.notifier
    notifier = ThrottledNotifier(
        raw_notifier,
        max_per_kind_per_window=5,
        window_s=300.0,
    )

    # ── start background services ─────────────────────────────────────────────
    comms.start()
    wd.start()

    # ── SIGINT / SIGTERM ──────────────────────────────────────────────────────
    def _sig_handler(signum, frame):
        _log.info("signal %s received — stopping", signum)
        _kill(state, executor, "operator signal")

    signal.signal(signal.SIGINT,  _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    state.state_name = "RUNNING"
    _log.info("APEX live loop started — %s %s dry_run=%s", symbol, args.interval, args.dry_run)
    notifier.notify(NotifierEvent(
        EventKind.INFO, "APEX started",
        f"{symbol} {args.interval} dry_run={args.dry_run}",
        Severity.DEFAULT,
    ))

    # ── main loop ─────────────────────────────────────────────────────────────
    while state.running:
        wd.feed()
        try:
            bars = load_from_mt5(symbol, args.interval, count=args.bars)
        except Exception as exc:
            _log.error("data fetch failed: %s", exc)
            notifier.notify(NotifierEvent(EventKind.ERROR, "data error", str(exc), Severity.HIGH))
            _sleep_until_next_bar(interval_s)
            continue

        if not bars:
            _log.warning("no bars returned")
            _sleep_until_next_bar(interval_s)
            continue

        signal_obj = evaluate(bars, strategy_cfg)
        state.last_signal = signal_obj.action
        _log.info("signal=%s score=%.3f reason=%s price=%.5g",
                  signal_obj.action, signal_obj.score, signal_obj.reason, bars[-1].close)

        if signal_obj.is_actionable() and cb.is_closed():
            if not args.dry_run:
                try:
                    if signal_obj.action == "BUY":
                        executor.buy()
                    else:
                        executor.sell()
                    cb.record_success()
                    state.n_trades += 1
                    notifier.notify(NotifierEvent(
                        EventKind.TRADE, "Trade",
                        f"{signal_obj.action} {symbol} @ {bars[-1].close:.5g}",
                        Severity.DEFAULT,
                    ))
                except Exception as exc:
                    _log.error("order failed: %s", exc)
                    cb.record_failure()
                    notifier.notify(NotifierEvent(EventKind.ERROR, "order error", str(exc), Severity.HIGH))
            else:
                _log.info("[DRY RUN] would %s %s @ %.5g", signal_obj.action, symbol, bars[-1].close)

        _sleep_until_next_bar(interval_s)

    # ── shutdown ──────────────────────────────────────────────────────────────
    wd.stop()
    comms.stop()
    _log.info("APEX live loop stopped.")


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _kill(state: BotState, executor: MT5Executor, reason: str) -> None:
    _log.warning("KILL requested: %s", reason)
    state.running = False
    state.state_name = "KILLED"
    try:
        executor.close_all()
    except Exception as exc:
        _log.error("close_all failed: %s", exc)


def _halt(state: BotState, executor: MT5Executor) -> None:
    _kill(state, executor, "watchdog timeout")


def _sleep_until_next_bar(interval_s: int) -> None:
    """Sleep until the next bar boundary (aligned to interval) minus 5 s."""
    now = time.time()
    next_bar = (now // interval_s + 1) * interval_s
    sleep_s = max(1.0, next_bar - now - 5)
    _log.debug("sleeping %.0fs until next bar", sleep_s)
    time.sleep(sleep_s)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="APEX live trading loop (Exness/MT5 + ntfy remote control).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("symbol",      help="MT5 symbol, e.g. BTCUSD or XAUUSD")
    p.add_argument("--interval",  default="4h",
                   help="Bar timeframe matching fetch_ohlcv.py (default 4h)")
    p.add_argument("--lot",       type=float, default=0.01,
                   help="Lot size per trade (default 0.01)")
    p.add_argument("--bars",      type=int, default=200,
                   help="Indicator lookback bars fetched each tick (default 200)")
    p.add_argument("--config",    metavar="FILE",
                   help="Path to core.yaml (default config/core.yaml)")
    p.add_argument("--dry-run",   action="store_true",
                   help="Log signals but do not submit real orders")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
