"""Tests for apex.strategy.config_loader — YAML parsing + StrategyConfig round-trip."""
from __future__ import annotations

import os
import tempfile
import unittest

from apex.strategy.config_loader import load_execution_config, load_strategy_config, load_yaml
from apex.strategy.decision import StrategyConfig


_SAMPLE_YAML = """\
# comment line
strategy:
  atr_period: 14
  atr_min: 0.005      # inline comment
  atr_max: 0.050
  momentum_period: 14
  max_spread: 0.003
  score_threshold: 0.30

execution:
  initial_cash: 10000.0
  slippage_bps: 5.0
  fee_bps: 5.0
  position_size: 0.10

backtest:
  warmup_bars: 50
  walk_forward_splits: 5
  in_sample_fraction: 0.70
"""


def _write_temp(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return f.name


class LoadYAMLTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _write_temp(_SAMPLE_YAML)

    def tearDown(self) -> None:
        os.unlink(self.path)

    def test_sections_parsed(self) -> None:
        data = load_yaml(self.path)
        self.assertIn("strategy", data)
        self.assertIn("execution", data)
        self.assertIn("backtest", data)

    def test_int_coerced(self) -> None:
        data = load_yaml(self.path)
        self.assertIsInstance(data["strategy"]["atr_period"], int)
        self.assertEqual(data["strategy"]["atr_period"], 14)

    def test_float_coerced(self) -> None:
        data = load_yaml(self.path)
        self.assertIsInstance(data["strategy"]["atr_min"], float)
        self.assertAlmostEqual(data["strategy"]["atr_min"], 0.005, places=6)

    def test_inline_comments_stripped(self) -> None:
        data = load_yaml(self.path)
        # atr_min line has inline comment — value must be clean float
        self.assertAlmostEqual(data["strategy"]["atr_min"], 0.005, places=6)

    def test_comment_lines_ignored(self) -> None:
        data = load_yaml(self.path)
        # no key called "comment line" should appear
        self.assertNotIn("# comment line", data)

    def test_nested_values_correct(self) -> None:
        data = load_yaml(self.path)
        self.assertAlmostEqual(data["execution"]["initial_cash"], 10000.0, places=2)
        self.assertAlmostEqual(data["backtest"]["in_sample_fraction"], 0.70, places=5)


class LoadStrategyConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _write_temp(_SAMPLE_YAML)

    def tearDown(self) -> None:
        os.unlink(self.path)

    def test_returns_strategy_config(self) -> None:
        cfg = load_strategy_config(self.path)
        self.assertIsInstance(cfg, StrategyConfig)

    def test_values_match_yaml(self) -> None:
        cfg = load_strategy_config(self.path)
        self.assertEqual(cfg.atr_period, 14)
        self.assertAlmostEqual(cfg.atr_min, 0.005, places=6)
        self.assertAlmostEqual(cfg.atr_max, 0.050, places=6)
        self.assertAlmostEqual(cfg.max_spread, 0.003, places=6)
        self.assertAlmostEqual(cfg.score_threshold, 0.30, places=6)

    def test_loads_real_core_yaml(self) -> None:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        core = os.path.join(repo, "config", "core.yaml")
        if not os.path.exists(core):
            self.skipTest("config/core.yaml not present")
        cfg = load_strategy_config(core)
        self.assertIsInstance(cfg, StrategyConfig)
        self.assertGreater(cfg.atr_min, 0)
        self.assertGreater(cfg.atr_max, cfg.atr_min)


class LoadExecutionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _write_temp(_SAMPLE_YAML)

    def tearDown(self) -> None:
        os.unlink(self.path)

    def test_returns_dict(self) -> None:
        ec = load_execution_config(self.path)
        self.assertIsInstance(ec, dict)

    def test_keys_present(self) -> None:
        ec = load_execution_config(self.path)
        self.assertIn("initial_cash", ec)
        self.assertIn("position_size", ec)

    def test_values_correct(self) -> None:
        ec = load_execution_config(self.path)
        self.assertAlmostEqual(ec["initial_cash"], 10_000.0, places=2)
        self.assertAlmostEqual(ec["position_size"], 0.10, places=5)

    def test_missing_section_returns_empty(self) -> None:
        path = _write_temp("strategy:\n  atr_period: 14\n")
        try:
            ec = load_execution_config(path)
        finally:
            os.unlink(path)
        self.assertEqual(ec, {})


if __name__ == "__main__":
    unittest.main()
