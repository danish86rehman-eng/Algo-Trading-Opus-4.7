"""Pre-flight checks — run this before scripts/live.py.

Validates every dependency in order so you know exactly what's ready
and what needs fixing before real money is at risk.

Usage:
    python3 scripts/preflight.py XAUUSD --interval 4h

Exit code 0 = all checks passed. Non-zero = something needs fixing.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── colour helpers ────────────────────────────────────────────────────────────
_USE_COLOUR = sys.stdout.isatty()

def _green(s: str) -> str:  return f"\033[32m{s}\033[0m" if _USE_COLOUR else s
def _red(s:   str) -> str:  return f"\033[31m{s}\033[0m" if _USE_COLOUR else s
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m" if _USE_COLOUR else s
def _bold(s:   str) -> str: return f"\033[1m{s}\033[0m"  if _USE_COLOUR else s

_PASS = _green("  PASS")
_FAIL = _red("  FAIL")
_WARN = _yellow("  WARN")


class CheckResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.warned: list[str] = []

    def ok(self, msg: str) -> None:
        print(f"{_PASS}  {msg}")
        self.passed.append(msg)

    def fail(self, msg: str, detail: str = "") -> None:
        print(f"{_FAIL}  {msg}")
        if detail:
            for line in detail.splitlines():
                print(f"        {_red(line)}")
        self.failed.append(msg)

    def warn(self, msg: str, detail: str = "") -> None:
        print(f"{_WARN}  {msg}")
        if detail:
            for line in detail.splitlines():
                print(f"        {_yellow(line)}")
        self.warned.append(msg)

    def summary(self) -> int:
        print()
        print(_bold("─" * 55))
        print(f"  {_green(str(len(self.passed)) + ' passed')}  "
              f"{_yellow(str(len(self.warned)) + ' warnings')}  "
              f"{_red(str(len(self.failed)) + ' failed')}")
        if self.failed:
            print(f"\n  {_red('Fix the FAIL items before going live.')}")
            return 1
        if self.warned:
            print(f"\n  {_yellow('Review warnings — bot will run but may be suboptimal.')}")
        else:
            print(f"\n  {_green('All checks passed. Ready to go live.')}")
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Checks
# ═══════════════════════════════════════════════════════════════════════════════

def check_python(r: CheckResult) -> None:
    print(_bold("\n[1/6] Python version"))
    v = sys.version_info
    if v >= (3, 10):
        r.ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        r.fail(f"Python {v.major}.{v.minor} — need 3.10+")


def check_mt5_package(r: CheckResult) -> None:
    print(_bold("\n[2/6] MetaTrader5 package"))
    try:
        import MetaTrader5 as mt5  # type: ignore[import]
        r.ok(f"MetaTrader5 {getattr(mt5, '__version__', 'installed')}")
    except ImportError:
        r.fail(
            "MetaTrader5 not installed",
            "pip install MetaTrader5\n(Windows only — must match terminal bitness)"
        )


def check_mt5_connection(r: CheckResult, symbol: str, interval: str) -> None:
    print(_bold("\n[3/6] MT5 terminal connection"))
    try:
        import MetaTrader5 as mt5  # type: ignore[import]
    except ImportError:
        r.fail("MetaTrader5 not installed — skipping connection check")
        return

    if not mt5.initialize():
        code, msg = mt5.last_error()
        r.fail(
            "MT5 initialize() failed",
            f"({code}) {msg}\nMake sure MetaTrader5 is running and logged in to Exness."
        )
        return

    try:
        info = mt5.terminal_info()
        if info is None:
            r.fail("terminal_info() returned None")
            return
        r.ok(f"terminal connected  build={info.build}  connected={info.connected}")

        if not info.connected:
            r.warn("terminal not connected to broker — no live data")

        # symbol check
        sym = mt5.symbol_info(symbol.upper())
        if sym is None:
            r.fail(
                f"symbol {symbol!r} not found",
                f"Use --list-symbols to see available symbols.\n"
                f"  python3 scripts/fetch_ohlcv.py --list-symbols --mt5"
            )
        else:
            r.ok(f"symbol {symbol.upper()} found  digits={sym.digits}  "
                 f"spread={sym.spread} pts")

        # fetch a small bar window
        from apex.data.mt5_loader import load_from_mt5
        bars = load_from_mt5(symbol, interval, count=20)
        if not bars:
            r.fail(f"no bars returned for {symbol} {interval}")
        else:
            import datetime
            latest = datetime.datetime.utcfromtimestamp(bars[-1].ts).strftime("%Y-%m-%d %H:%M")
            r.ok(f"data fetch OK  bars=20  latest={latest} UTC  close={bars[-1].close:.5g}")

            # check data freshness (warn if last bar is old)
            age_h = (time.time() - bars[-1].ts) / 3600
            if age_h > 24:
                r.warn(f"last bar is {age_h:.0f}h old — is MT5 connected to broker?")

    finally:
        mt5.shutdown()


def check_config(r: CheckResult) -> None:
    print(_bold("\n[4/6] Strategy config (config/core.yaml)"))
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo, "config", "core.yaml")

    if not os.path.exists(path):
        r.fail(
            "config/core.yaml not found",
            "Run: python3 scripts/tune.py --symbol XAUUSD --interval 4h --mt5 --write"
        )
        return

    try:
        from apex.strategy.config_loader import load_strategy_config
        cfg = load_strategy_config(path)
        r.ok(f"atr=[{cfg.atr_min:.3%}, {cfg.atr_max:.3%}]  "
             f"spread<={cfg.max_spread:.3%}  thr={cfg.score_threshold:.3f}")

        # warn if config looks like untuned placeholder defaults
        if abs(cfg.atr_min - 0.005) < 1e-6 and abs(cfg.score_threshold - 0.30) < 1e-6:
            r.warn(
                "config looks like placeholder defaults (not tuned)",
                "Run tune.py with your real data:\n"
                "  python3 scripts/tune.py --symbol XAUUSD --interval 4h --mt5 --write"
            )
    except Exception as exc:
        r.fail(f"config/core.yaml failed to parse: {exc}")


def check_env(r: CheckResult) -> None:
    print(_bold("\n[5/6] .env / ntfy config"))
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(repo, ".env")

    # load .env if present
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
        r.ok(".env file found and loaded")
    else:
        r.warn(
            ".env not found — ntfy notifications will be disabled",
            "Run: python3 scripts/setup_comms.py"
        )
        return

    from apex.comms.config import CommsConfig
    try:
        cfg = CommsConfig.from_env()
    except Exception as exc:
        r.fail(f"CommsConfig.from_env() failed: {exc}")
        return

    if not cfg.enabled:
        r.warn(
            "NTFY_ENABLED=false — phone alerts and remote control disabled",
            "Set NTFY_ENABLED=true in .env to enable."
        )
        return

    try:
        cfg.validate()
        r.ok("ntfy config valid  "
             f"topic={cfg.notify_topic[:8]}…  "
             f"control={cfg.control_topic[:8]}…")
    except ValueError as exc:
        r.fail(f"ntfy config invalid: {exc}")


def check_imports(r: CheckResult) -> None:
    print(_bold("\n[6/6] Core module imports"))
    modules = [
        ("apex.strategy.decision", "evaluate, StrategyConfig"),
        ("apex.ops.circuit_breaker", "CircuitBreaker"),
        ("apex.ops.watchdog", "Watchdog"),
        ("apex.ops.throttle", "ThrottledNotifier"),
        ("apex.comms.agent", "CommsAgent"),
        ("apex.data.mt5_executor", "MT5Executor"),
        ("apex.strategy.config_loader", "load_strategy_config"),
    ]
    all_ok = True
    for mod, names in modules:
        try:
            __import__(mod)
            r.ok(f"{mod}  ({names})")
        except Exception as exc:
            r.fail(f"{mod}: {exc}")
            all_ok = False
    if all_ok:
        r.ok("all core modules importable")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="APEX pre-flight checks.")
    p.add_argument("symbol",     nargs="?", default="XAUUSD",
                   help="MT5 symbol to test (default XAUUSD)")
    p.add_argument("--interval", default="4h",
                   help="Bar timeframe to test (default 4h)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    print(_bold(f"APEX Pre-flight  symbol={args.symbol}  interval={args.interval}"))
    print("─" * 55)

    r = CheckResult()
    check_python(r)
    check_mt5_package(r)
    check_mt5_connection(r, args.symbol, args.interval)
    check_config(r)
    check_env(r)
    check_imports(r)

    sys.exit(r.summary())


if __name__ == "__main__":
    main()
