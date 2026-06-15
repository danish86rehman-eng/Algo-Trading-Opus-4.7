"""YAML config loader — no third-party dependencies.

Parses the simple two-level YAML structure used in config/core.yaml.
Handles inline comments and coerces values to int/float automatically.
"""
from __future__ import annotations

import os
import re


def _coerce(raw: str) -> int | float | str | bool:
    v = raw.split("#")[0].strip()  # strip inline comments
    if v.lower() in ("true", "yes", "on"):
        return True
    if v.lower() in ("false", "no", "off"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def load_yaml(path: str) -> dict:
    """Parse a simple two-level YAML file into a nested dict."""
    result: dict = {}
    section: str | None = None
    with open(path) as f:
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.lstrip().startswith("#"):
                continue
            if stripped.startswith("  ") or stripped.startswith("\t"):
                if section is None:
                    continue
                key, _, val = stripped.strip().partition(":")
                result[section][key.strip()] = _coerce(val.strip())
            elif ":" in stripped:
                key, _, val = stripped.partition(":")
                section = key.strip()
                v = val.strip()
                result[section] = _coerce(v) if v else {}
    return result


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG = os.path.join(_REPO_ROOT, "config", "core.yaml")


def load_strategy_config(path: str = _DEFAULT_CONFIG):
    """Load StrategyConfig from config/core.yaml (or a custom path)."""
    from apex.strategy.decision import StrategyConfig
    data = load_yaml(path)
    s = data.get("strategy", {})
    return StrategyConfig(
        atr_period=int(s.get("atr_period", 14)),
        atr_min=float(s.get("atr_min", 0.005)),
        atr_max=float(s.get("atr_max", 0.050)),
        momentum_period=int(s.get("momentum_period", 14)),
        max_spread=float(s.get("max_spread", 0.003)),
        score_threshold=float(s.get("score_threshold", 0.30)),
    )


def load_execution_config(path: str = _DEFAULT_CONFIG) -> dict:
    """Return the [execution] section as a plain dict."""
    data = load_yaml(path)
    return data.get("execution", {})
