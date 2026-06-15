"""Fetch OHLCV bars from Binance and save to CSV for offline use.

Usage:
    python3 scripts/fetch_ohlcv.py BTCUSDT --interval 1h --limit 1000
    python3 scripts/fetch_ohlcv.py ETHUSDT --interval 4h --limit 500 --out data/eth_4h.csv

The CSV is written in the format expected by scripts/tune.py --csv and
apex.data.load_bars(csv_path=...).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apex.data.loader import load_bars, save_csv
from apex.strategy.bars import Bar


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch OHLCV from Binance and save to CSV.")
    p.add_argument("symbol",   help="Binance symbol, e.g. BTCUSDT")
    p.add_argument("--interval", default="1h",
                   help="Kline interval: 1m 5m 15m 30m 1h 4h 1d … (default 1h)")
    p.add_argument("--limit",    type=int, default=1000,
                   help="Number of bars to fetch (default 1000, pages automatically)")
    p.add_argument("--out",      metavar="FILE",
                   help="Output CSV path (default: <symbol>_<interval>.csv)")
    p.add_argument("--base-url", default="https://api.binance.com",
                   help="Override Binance base URL (e.g. for a local proxy)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out = args.out or f"{args.symbol.lower()}_{args.interval}.csv"

    print(f"Fetching {args.limit} × {args.interval} bars for {args.symbol} …")
    t0 = time.monotonic()
    bars = load_bars(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        base_url=args.base_url,
    )
    elapsed = time.monotonic() - t0
    print(f"  got {len(bars)} bars in {elapsed:.1f}s")

    if bars:
        import datetime
        start_dt = datetime.datetime.utcfromtimestamp(bars[0].ts).strftime("%Y-%m-%d")
        end_dt   = datetime.datetime.utcfromtimestamp(bars[-1].ts).strftime("%Y-%m-%d")
        print(f"  range: {start_dt} → {end_dt}")
        print(f"  price: {bars[0].close:.2f} → {bars[-1].close:.2f}")

    save_csv(bars, out)
    print(f"  saved → {out}")
    print(f"\nNext step:")
    print(f"  python3 scripts/tune.py --csv {out} --write")


if __name__ == "__main__":
    main()
