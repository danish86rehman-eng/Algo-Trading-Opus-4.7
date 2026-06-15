"""OHLCV data loader — CSV and Binance REST (stdlib only, no auth needed).

The single public surface is `load_bars()` which returns `list[Bar]` regardless
of source. Everything else is an implementation detail.

Supported sources:
  - CSV file  (ts,open,high,low,close[,volume])
  - Binance   (GET /api/v3/klines, public endpoint, no API key)

Binance intervals: 1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M
Binance limit:     max 1000 bars per request; this loader pages automatically.
"""
from __future__ import annotations

import csv
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterator

from apex.strategy.bars import Bar

_log = logging.getLogger("apex.data.loader")

_BINANCE_BASE = "https://api.binance.com"
_MAX_PER_REQUEST = 1000


# ─── public API ───────────────────────────────────────────────────────────────

def load_bars(
    *,
    csv_path: str | None = None,
    symbol: str | None = None,
    interval: str = "1h",
    limit: int = 1000,
    start_ms: int | None = None,
    end_ms: int | None = None,
    base_url: str = _BINANCE_BASE,
) -> list[Bar]:
    """Return bars from the specified source.

    Exactly one of `csv_path` or `symbol` must be provided.

    Args:
        csv_path:  Path to a CSV with columns ts,open,high,low,close[,volume].
        symbol:    Binance market symbol, e.g. "BTCUSDT".
        interval:  Binance kline interval (default "1h").
        limit:     Maximum number of bars to return (Binance: pages if > 1000).
        start_ms:  Binance start time in epoch milliseconds.
        end_ms:    Binance end time in epoch milliseconds.
        base_url:  Override the Binance base URL (for tests or self-hosted proxy).
    """
    if csv_path and symbol:
        raise ValueError("supply csv_path OR symbol, not both")
    if not csv_path and not symbol:
        raise ValueError("supply either csv_path or symbol")

    if csv_path:
        return list(_from_csv(csv_path))
    return list(_from_binance(
        symbol=symbol, interval=interval, limit=limit,
        start_ms=start_ms, end_ms=end_ms, base_url=base_url,
    ))


# ─── CSV loader ───────────────────────────────────────────────────────────────

def _from_csv(path: str) -> Iterator[Bar]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield Bar(
                ts=float(row["ts"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0) or 0),
            )


# ─── Binance loader ───────────────────────────────────────────────────────────

def _from_binance(
    *,
    symbol: str,
    interval: str,
    limit: int,
    start_ms: int | None,
    end_ms: int | None,
    base_url: str,
) -> Iterator[Bar]:
    """Page through Binance /api/v3/klines until `limit` bars collected."""
    fetched = 0
    since_ms = start_ms

    while fetched < limit:
        batch_size = min(_MAX_PER_REQUEST, limit - fetched)
        params: dict[str, str | int] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": batch_size,
        }
        if since_ms is not None:
            params["startTime"] = since_ms
        if end_ms is not None:
            params["endTime"] = end_ms

        raw = _get(f"{base_url.rstrip('/')}/api/v3/klines", params)
        klines: list[list] = json.loads(raw)
        if not klines:
            break

        for k in klines:
            yield _kline_to_bar(k)
            fetched += 1

        if len(klines) < batch_size:
            break  # Binance returned fewer than asked → end of data

        # next page starts after the last returned close-time
        since_ms = int(klines[-1][6]) + 1  # closeTime + 1 ms

        if fetched < limit and len(klines) == batch_size:
            time.sleep(0.1)  # gentle rate-limit courtesy

    _log.debug("loaded %d bars from Binance (%s %s)", fetched, symbol, interval)


def _kline_to_bar(k: list) -> Bar:
    # Binance kline: [openTime, open, high, low, close, volume, closeTime, ...]
    return Bar(
        ts=float(k[0]) / 1000.0,   # ms → seconds
        open=float(k[1]),
        high=float(k[2]),
        low=float(k[3]),
        close=float(k[4]),
        volume=float(k[5]),
    )


def _get(url: str, params: dict) -> bytes:
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}"
    try:
        req = urllib.request.Request(full_url, headers={"User-Agent": "apex-bot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Binance API error {exc.code}: {body}") from exc


# ─── CSV writer (save fetched data for offline use) ───────────────────────────

def save_csv(bars: list[Bar], path: str) -> None:
    """Write bars to a CSV that load_bars(csv_path=...) can re-read."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "open", "high", "low", "close", "volume"])
        for b in bars:
            writer.writerow([b.ts, b.open, b.high, b.low, b.close, b.volume])
    _log.info("saved %d bars to %s", len(bars), path)
