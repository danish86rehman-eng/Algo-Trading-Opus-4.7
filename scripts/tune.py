"""Parameter tuning script for the APEX strategy (Phase 6).

Usage:
    python3 scripts/tune.py                          # runs on built-in synthetic data
    python3 scripts/tune.py --csv path/to/ohlcv.csv # real data: ts,open,high,low,close[,volume]

Workflow:
  1. Characterise the data (spread/ATR/score distributions).
  2. Grid-search StrategyConfig candidates derived from those distributions.
  3. Rank by mean out-of-sample Sharpe across walk-forward folds.
  4. Print the winner and optionally write it to config/core.yaml.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import math
import os
import random
import statistics
import sys
import time

# ── ensure repo root is importable when run as a script ──────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apex.strategy.bars import Bar, atr, momentum_score
from apex.strategy.decision import StrategyConfig, evaluate
from apex.backtest.runner import BacktestRunner
from apex.backtest.walk_forward import WalkForwardValidator, WalkForwardResult


# ═══════════════════════════════════════════════════════════════════════════════
# Data loaders
# ═══════════════════════════════════════════════════════════════════════════════

def synthetic_bars(
    n: int = 800,
    start: float = 30_000.0,
    drift: float = 0.0003,
    vol: float = 0.012,
    seed: int = 42,
) -> list[Bar]:
    """Gaussian random-walk price series with realistic intraday spread."""
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = start
    for i in range(n):
        price *= 1 + rng.gauss(drift, vol)
        half = abs(rng.gauss(0, vol / 2))
        high = price * (1 + half)
        low  = price * (1 - half)
        bars.append(Bar(ts=float(i), open=price, high=high, low=low, close=price))
    return bars


def bars_from_csv(path: str) -> list[Bar]:
    """Read OHLCV CSV (header: ts,open,high,low,close[,volume])."""
    result: list[Bar] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            result.append(
                Bar(
                    ts=float(row["ts"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                )
            )
    if not result:
        raise ValueError(f"No data loaded from {path!r}")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Step 1: Data characterisation
# ═══════════════════════════════════════════════════════════════════════════════

def _percentile(data: list[float], pct: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = max(0, min(len(s) - 1, int(len(s) * pct / 100)))
    return s[idx]


def characterise(bars: list[Bar]) -> dict:
    spreads  = [(b.high - b.low) / b.close for b in bars if b.close > 0]
    atr_pcts = [
        atr(bars[: i + 1], 14) / bars[i].close
        for i in range(14, len(bars))
        if bars[i].close > 0
    ]
    abs_scores = [
        abs(momentum_score(bars[: i + 1], 14))
        for i in range(15, len(bars))
    ]
    return {
        "spread_p25":  _percentile(spreads, 25),
        "spread_p50":  _percentile(spreads, 50),
        "spread_p75":  _percentile(spreads, 75),
        "spread_p90":  _percentile(spreads, 90),
        "atr_p25":     _percentile(atr_pcts, 25),
        "atr_p50":     _percentile(atr_pcts, 50),
        "atr_p75":     _percentile(atr_pcts, 75),
        "atr_p90":     _percentile(atr_pcts, 90),
        "score_p50":   _percentile(abs_scores, 50),
        "score_p75":   _percentile(abs_scores, 75),
        "score_p90":   _percentile(abs_scores, 90),
        "n_bars":      len(bars),
    }


def print_characteristics(ch: dict) -> None:
    print("┌─ Data characteristics ────────────────────────────────┐")
    print(f"│  bars       {ch['n_bars']:5d}")
    print(f"│  spread     p25={ch['spread_p25']:.3%}  p50={ch['spread_p50']:.3%}"
          f"  p75={ch['spread_p75']:.3%}  p90={ch['spread_p90']:.3%}")
    print(f"│  atr/price  p25={ch['atr_p25']:.3%}  p50={ch['atr_p50']:.3%}"
          f"  p75={ch['atr_p75']:.3%}  p90={ch['atr_p90']:.3%}")
    print(f"│  |score|    p50={ch['score_p50']:.3f}   p75={ch['score_p75']:.3f}"
          f"   p90={ch['score_p90']:.3f}")
    print("└───────────────────────────────────────────────────────┘")


# ═══════════════════════════════════════════════════════════════════════════════
# Step 2: Build candidate grid from data characteristics
# ═══════════════════════════════════════════════════════════════════════════════

def _round2(v: float) -> float:
    return round(v, 4)


def build_grid(ch: dict) -> list[StrategyConfig]:
    """Derive a parameter grid from the data's own distributions."""
    # spread candidates: p50 and p90 of actual bar spreads
    max_spreads = sorted({
        _round2(ch["spread_p50"]),
        _round2(ch["spread_p75"]),
        _round2(ch["spread_p90"]),
    })

    # ATR bands: bracket the median ATR with a 0.5x / 2x envelope
    atr_mid = ch["atr_p50"]
    atr_mins = sorted({
        _round2(max(0.001, atr_mid * 0.25)),
        _round2(max(0.001, atr_mid * 0.50)),
    })
    atr_maxs = sorted({
        _round2(atr_mid * 2.0),
        _round2(atr_mid * 4.0),
    })

    # score threshold: p50 and p75 of |momentum_score|
    thresholds = sorted({
        _round2(max(0.10, ch["score_p50"] * 0.8)),
        _round2(max(0.15, ch["score_p50"])),
        _round2(max(0.20, ch["score_p75"])),
    })

    configs: list[StrategyConfig] = []
    for ms, amin, amax, thr in itertools.product(max_spreads, atr_mins, atr_maxs, thresholds):
        if amin >= amax:
            continue
        try:
            configs.append(StrategyConfig(
                atr_min=amin, atr_max=amax,
                max_spread=ms,
                score_threshold=thr,
            ))
        except ValueError:
            pass
    return configs


# ═══════════════════════════════════════════════════════════════════════════════
# Step 3: Walk-forward evaluation
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_config(
    cfg: StrategyConfig,
    bars: list[Bar],
    n_splits: int,
    warmup: int,
) -> WalkForwardResult:
    wf = WalkForwardValidator(cfg, n_splits=n_splits, warmup_bars=warmup)
    return wf.validate(bars)


def rank_configs(
    configs: list[StrategyConfig],
    bars: list[Bar],
    n_splits: int,
    warmup: int,
    verbose: bool = True,
) -> list[tuple[float, int, WalkForwardResult, StrategyConfig]]:
    """
    Returns list of (oos_sharpe, n_trades_total, wf_result, config) sorted best first.
    Primary sort: mean OOS Sharpe. Tie-break: more trades (avoids degenerate zero-trade wins).
    """
    results: list[tuple[float, int, WalkForwardResult, StrategyConfig]] = []
    n = len(configs)

    for i, cfg in enumerate(configs):
        if verbose:
            print(f"\r  evaluating {i+1}/{n} …", end="", flush=True)
        wf_result = evaluate_config(cfg, bars, n_splits, warmup)
        total_trades = sum(f.out_sample.n_trades for f in wf_result.folds)
        results.append((wf_result.mean_oos_sharpe, total_trades, wf_result, cfg))

    if verbose:
        print()

    # Sort: best OOS Sharpe first; among equals prefer more trades
    results.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Step 4: Report + optional config write
# ═══════════════════════════════════════════════════════════════════════════════

def print_top(
    ranked: list[tuple[float, int, WalkForwardResult, StrategyConfig]],
    top_n: int = 5,
) -> None:
    print(f"\n{'rank':<5} {'oos_sharpe':>10} {'trades':>7} {'oos_ret':>9} {'oos_mdd':>8}"
          f"  {'atr_min':>7} {'atr_max':>7} {'spread':>8} {'thr':>6}")
    print("─" * 80)
    for rank, (sharpe, trades, wfr, cfg) in enumerate(ranked[:top_n], 1):
        print(
            f"{rank:<5} {sharpe:>10.3f} {trades:>7} {wfr.mean_oos_return:>+9.2%}"
            f" {wfr.mean_oos_drawdown:>8.2%}"
            f"  {cfg.atr_min:>7.3%} {cfg.atr_max:>7.3%}"
            f" {cfg.max_spread:>8.3%} {cfg.score_threshold:>6.3f}"
        )


def write_yaml(cfg: StrategyConfig, path: str) -> None:
    content = f"""\
# APEX Phase 6 — strategy + backtest configuration
# Auto-generated by scripts/tune.py from walk-forward optimisation.

strategy:
  atr_period: {cfg.atr_period}
  atr_min: {cfg.atr_min}      # filter out markets too quiet
  atr_max: {cfg.atr_max}      # filter out runaway volatility
  momentum_period: {cfg.momentum_period}
  max_spread: {cfg.max_spread}   # skip bars with spread wider than this
  score_threshold: {cfg.score_threshold}  # |momentum score| needed to generate BUY/SELL

execution:
  initial_cash: 10000.0
  slippage_bps: 5.0
  fee_bps: 5.0
  position_size: 0.10

backtest:
  warmup_bars: 50
  window_size: 200
  walk_forward_splits: 5
  in_sample_fraction: 0.70
"""
    with open(path, "w") as f:
        f.write(content)
    print(f"\n  ✓ wrote tuned config → {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tune APEX strategy parameters via walk-forward.")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--csv",    metavar="FILE", help="OHLCV CSV (ts,open,high,low,close[,volume])")
    src.add_argument("--symbol", metavar="SYM",
                     help="Symbol to fetch — Binance: BTCUSDT, Exness/MT5: BTCUSD XAUUSD …")
    p.add_argument("--mt5",      action="store_true",
                   help="Fetch from Exness/MT5 instead of Binance (use with --symbol)")
    p.add_argument("--interval", default="4h",
                   help="Bar timeframe when using --symbol (default 4h)")
    p.add_argument("--limit",    type=int, default=2000,
                   help="Bars to fetch when using --symbol (default 2000)")
    p.add_argument("--base-url", default="https://api.binance.com",
                   help="Binance base URL override (default https://api.binance.com)")
    p.add_argument("--splits",   type=int, default=5,  help="Walk-forward folds (default 5)")
    p.add_argument("--warmup",   type=int, default=50, help="Warmup bars (default 50)")
    p.add_argument("--top",      type=int, default=5,  help="Show top N configs (default 5)")
    p.add_argument("--write",    action="store_true",  help="Write best config to config/core.yaml")
    p.add_argument("--synthetic-n", type=int, default=800, metavar="N",
                   help="Bars of synthetic data when no source given (default 800)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # --- load data ---
    if args.csv:
        print(f"\nLoading data from {args.csv!r} …")
        bars = bars_from_csv(args.csv)
    elif args.symbol:
        from apex.data.loader import load_bars
        source = "Exness/MT5" if args.mt5 else "Binance"
        print(f"\nFetching {args.limit} × {args.interval} bars for {args.symbol} from {source} …")
        bars = load_bars(
            symbol=args.symbol, interval=args.interval,
            limit=args.limit, base_url=args.base_url,
            mt5=args.mt5,
        )
        print(f"  got {len(bars)} bars")
    else:
        print(f"\nNo --csv or --symbol supplied; using {args.synthetic_n}-bar synthetic data.")
        bars = synthetic_bars(args.synthetic_n)

    print(f"Loaded {len(bars)} bars.\n")

    # --- characterise ---
    ch = characterise(bars)
    print_characteristics(ch)

    # --- build grid ---
    grid = build_grid(ch)
    print(f"\nGrid: {len(grid)} candidate configs to evaluate …")

    # --- evaluate ---
    t0 = time.monotonic()
    ranked = rank_configs(grid, bars, n_splits=args.splits, warmup=args.warmup)
    elapsed = time.monotonic() - t0
    print(f"  done in {elapsed:.1f}s")

    # --- report ---
    print(f"\n{'═'*80}")
    print(f"  Top {args.top} configs by mean OOS Sharpe (walk-forward, {args.splits} folds)")
    print('═'*80)
    print_top(ranked, top_n=args.top)

    if not ranked:
        print("\nNo valid configs found.")
        return

    best_sharpe, best_trades, best_wf, best_cfg = ranked[0]
    print(f"\n{'─'*80}")
    print(f"  WINNER  OOS Sharpe={best_sharpe:.3f}  trades={best_trades}")
    print(f"          {best_wf.summary()}")
    print(f"\n  StrategyConfig(")
    print(f"      atr_min={best_cfg.atr_min},")
    print(f"      atr_max={best_cfg.atr_max},")
    print(f"      max_spread={best_cfg.max_spread},")
    print(f"      score_threshold={best_cfg.score_threshold},")
    print(f"  )")

    # --- per-fold detail for winner ---
    print(f"\n  Walk-forward detail:")
    for f in best_wf.folds:
        flag = "✓" if f.out_sample.total_return > 0 else "✗"
        print(
            f"    {flag} fold {f.fold}  IS={f.in_sample_bars:3d}"
            f"  OOS ret={f.out_sample.total_return:+.2%}"
            f"  sharpe={f.out_sample.sharpe:+.2f}"
            f"  mdd={f.out_sample.max_drawdown:.2%}"
            f"  trades={f.out_sample.n_trades}"
        )

    if args.write:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        yaml_path = os.path.join(repo_root, "config", "core.yaml")
        write_yaml(best_cfg, yaml_path)


if __name__ == "__main__":
    main()
