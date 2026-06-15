"""Fetch OHLCV bars and save to CSV for offline use.

Sources
-------
Binance (default, no auth):
    python3 scripts/fetch_ohlcv.py BTCUSDT --interval 4h --limit 2000

Exness / MT5 (requires MetaTrader5 package + terminal running):
    python3 scripts/fetch_ohlcv.py BTCUSD  --interval 4h --limit 2000 --mt5
    python3 scripts/fetch_ohlcv.py XAUUSD  --interval 1h --limit 5000 --mt5

The CSV is written in the format expected by scripts/tune.py --csv and
apex.data.load_bars(csv_path=...).

Exness symbol names (MT5) differ from Binance:
    Binance BTCUSDT → Exness BTCUSD
    Binance ETHUSDT → Exness ETHUSD
    (Gold: XAUUSD, Euro: EURUSD, …)
Run with --list-symbols to see every symbol on your Exness account.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apex.data.loader import load_bars, save_csv


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch OHLCV (Binance or Exness/MT5) and save to CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("symbol", nargs="?",
                   help="Symbol: Binance e.g. BTCUSDT, Exness/MT5 e.g. BTCUSD")
    p.add_argument("--interval",  default="4h",
                   help="Timeframe: 1m 5m 15m 30m 1h 4h 1d … (default 4h)")
    p.add_argument("--limit",     type=int, default=2000,
                   help="Bars to fetch (default 2000)")
    p.add_argument("--out",       metavar="FILE",
                   help="Output CSV (default: <symbol>_<interval>.csv)")
    p.add_argument("--mt5",       action="store_true",
                   help="Fetch from Exness/MT5 instead of Binance")
    p.add_argument("--list-symbols", action="store_true",
                   help="Print all symbols available in your MT5 terminal and exit")
    p.add_argument("--base-url",  default="https://api.binance.com",
                   help="Binance base URL override")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # ── list symbols ─────────────────────────────────────────────────────────
    if args.list_symbols:
        from apex.data.mt5_loader import list_symbols
        print("Querying MT5 terminal for available symbols …")
        syms = list_symbols()
        for s in sorted(syms):
            print(f"  {s}")
        print(f"\n{len(syms)} symbols total.")
        return

    if not args.symbol:
        print("error: symbol is required (or use --list-symbols)")
        sys.exit(1)

    out = args.out or f"{args.symbol.lower()}_{args.interval}.csv"
    source = "Exness/MT5" if args.mt5 else "Binance"

    print(f"Fetching {args.limit} × {args.interval} bars for {args.symbol} from {source} …")
    t0 = time.monotonic()

    bars = load_bars(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        base_url=args.base_url,
        mt5=args.mt5,
    )

    elapsed = time.monotonic() - t0
    print(f"  got {len(bars)} bars in {elapsed:.1f}s")

    if bars:
        fmt = "%Y-%m-%d %H:%M"
        start_dt = datetime.datetime.utcfromtimestamp(bars[0].ts).strftime(fmt)
        end_dt   = datetime.datetime.utcfromtimestamp(bars[-1].ts).strftime(fmt)
        print(f"  range  : {start_dt} → {end_dt} UTC")
        print(f"  price  : {bars[0].close:.5g} → {bars[-1].close:.5g}")

    save_csv(bars, out)
    print(f"  saved  → {out}")
    print(f"\nNext step:")
    print(f"  python3 scripts/tune.py --csv {out} --write")


if __name__ == "__main__":
    main()
