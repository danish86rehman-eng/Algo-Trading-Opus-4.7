"""apex.data.loader tests — no network; Binance HTTP is patched out."""
from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from apex.data.loader import _kline_to_bar, load_bars, save_csv
from apex.strategy.bars import Bar


def _make_kline(open_ms: int, close: float) -> list:
    """Minimal Binance kline fixture."""
    return [
        open_ms,          # openTime (ms)
        str(close),       # open
        str(close * 1.01),# high
        str(close * 0.99),# low
        str(close),       # close
        "100.0",          # volume
        open_ms + 3599999,# closeTime (ms)
        "0", "0", "0", "0", "0",
    ]


class KlineConversionTests(unittest.TestCase):
    def test_ts_converted_from_ms_to_seconds(self) -> None:
        k = _make_kline(1_700_000_000_000, 30_000.0)
        bar = _kline_to_bar(k)
        self.assertAlmostEqual(bar.ts, 1_700_000_000.0, places=0)

    def test_ohlcv_fields_populated(self) -> None:
        k = _make_kline(1_000_000_000_000, 50_000.0)
        bar = _kline_to_bar(k)
        self.assertAlmostEqual(bar.open,   50_000.0, places=2)
        self.assertAlmostEqual(bar.high,   50_500.0, places=2)
        self.assertAlmostEqual(bar.low,    49_500.0, places=2)
        self.assertAlmostEqual(bar.close,  50_000.0, places=2)
        self.assertAlmostEqual(bar.volume, 100.0,    places=2)


class CsvRoundtripTests(unittest.TestCase):
    def _sample_bars(self) -> list[Bar]:
        return [
            Bar(ts=1.0, open=100.0, high=101.0, low=99.0, close=100.5, volume=10.0),
            Bar(ts=2.0, open=100.5, high=102.0, low=100.0, close=101.0, volume=15.0),
        ]

    def test_save_and_load_roundtrip(self) -> None:
        bars = self._sample_bars()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            save_csv(bars, path)
            loaded = load_bars(csv_path=path)
        finally:
            os.unlink(path)

        self.assertEqual(len(loaded), 2)
        self.assertAlmostEqual(loaded[0].close, 100.5, places=5)
        self.assertAlmostEqual(loaded[1].volume, 15.0, places=5)

    def test_csv_header_written(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        try:
            save_csv(self._sample_bars(), path)
            with open(path) as f:
                header = f.readline().strip().split(",")
        finally:
            os.unlink(path)
        self.assertEqual(header, ["ts", "open", "high", "low", "close", "volume"])

    def test_missing_volume_column_defaults_to_zero(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline=""
        ) as f:
            path = f.name
            writer = csv.writer(f)
            writer.writerow(["ts", "open", "high", "low", "close"])
            writer.writerow([1.0, 100.0, 101.0, 99.0, 100.0])
        try:
            bars = load_bars(csv_path=path)
        finally:
            os.unlink(path)
        self.assertEqual(bars[0].volume, 0.0)


class LoadBarsValidationTests(unittest.TestCase):
    def test_both_sources_raises(self) -> None:
        with self.assertRaises(ValueError):
            load_bars(csv_path="x.csv", symbol="BTCUSDT")

    def test_no_source_raises(self) -> None:
        with self.assertRaises(ValueError):
            load_bars()


class BinanceFetchTests(unittest.TestCase):
    """Patch urllib so no real HTTP is made."""

    def _fake_response(self, klines: list[list]) -> MagicMock:
        body = json.dumps(klines).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_single_page_fetch(self) -> None:
        klines = [_make_kline(i * 3_600_000, 30_000.0 + i) for i in range(5)]
        with patch("urllib.request.urlopen", return_value=self._fake_response(klines)):
            bars = load_bars(symbol="BTCUSDT", interval="1h", limit=5)
        self.assertEqual(len(bars), 5)
        self.assertIsInstance(bars[0], Bar)

    def test_multi_page_fetch(self) -> None:
        # Patch _MAX_PER_REQUEST=3 so page 1 fills up (3 bars) and a second page is fetched.
        # Page 2 returns 2 bars (< page size) → pagination stops.
        page1 = [_make_kline(i * 3_600_000, 30_000.0) for i in range(3)]
        page2 = [_make_kline((3 + i) * 3_600_000, 30_001.0) for i in range(2)]

        call_count = [0]
        def fake_urlopen(req, timeout):
            page = page1 if call_count[0] == 0 else page2
            call_count[0] += 1
            return self._fake_response(page)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("apex.data.loader._MAX_PER_REQUEST", 3):
            bars = load_bars(symbol="BTCUSDT", interval="1h", limit=10)
        self.assertEqual(len(bars), 5)   # 3 + 2

    def test_empty_response_returns_no_bars(self) -> None:
        with patch("urllib.request.urlopen", return_value=self._fake_response([])):
            bars = load_bars(symbol="BTCUSDT", interval="1h", limit=10)
        self.assertEqual(bars, [])

    def test_symbol_uppercased_in_request(self) -> None:
        klines = [_make_kline(0, 30_000.0)]
        captured_urls: list[str] = []

        def fake_urlopen(req, timeout):
            captured_urls.append(req.full_url)
            return self._fake_response(klines)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            load_bars(symbol="btcusdt", interval="1h", limit=1)

        self.assertIn("BTCUSDT", captured_urls[0])


if __name__ == "__main__":
    unittest.main()
