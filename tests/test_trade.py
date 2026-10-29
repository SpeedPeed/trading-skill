"""Offline-Tests mit synthetischen Daten (kein Netzwerk noetig).

Ausfuehren: python3 -m unittest discover -s tests
"""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "trading" / "scripts"))
import trade  # noqa: E402


class Indicators(unittest.TestCase):
    def test_sma(self):
        x = np.arange(1, 11, dtype=float)
        s = trade.sma(x, 3)
        self.assertTrue(np.isnan(s[:2]).all())
        np.testing.assert_allclose(s[2:], np.arange(2, 10))

    def test_rsi_bounds_and_extremes(self):
        up = np.linspace(100, 200, 60)
        self.assertEqual(trade.rsi(up)[-1], 100.0)
        down = np.linspace(200, 100, 60)
        self.assertAlmostEqual(trade.rsi(down)[-1], 0.0)
        rng = np.random.default_rng(0)
        r = trade.rsi(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
        valid = r[~np.isnan(r)]
        self.assertTrue(((valid >= 0) & (valid <= 100)).all())

    def test_max_drawdown(self):
        self.assertAlmostEqual(trade.max_drawdown(np.array([100, 120, 60, 90, 130.0])), -0.5)

    def test_macd_zero_for_constant(self):
        line, sig, hist = trade.macd(np.full(100, 50.0))
        self.assertAlmostEqual(line[-1], 0.0)
        self.assertAlmostEqual(hist[-1], 0.0)


class Backtest(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(1)
        self.close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, 1500)))

    def test_buyhold_equals_price_ratio(self):
        r = trade.run_backtest(self.close, np.ones(len(self.close)), 0, 0.0, 252)
        self.assertAlmostEqual(r["total_return"], self.close[-1] / self.close[0] - 1, places=9)

    def test_costs_reduce_return(self):
        pos, warm = trade.positions(self.close, "sma", {"fast": 20, "slow": 50})
        a = trade.run_backtest(self.close, pos, warm, 0.0, 252)
        b = trade.run_backtest(self.close, pos, warm, 0.01, 252)
        self.assertLess(b["total_return"], a["total_return"])

    def test_no_lookahead(self):
        """Aendern zukuenftiger Kurse darf vergangene Positionen nicht beeinflussen."""
        params = {"fast": 20, "slow": 50, "rsi_period": 14, "rsi_low": 30, "rsi_high": 55,
                  "lookback": 60, "entry": 30, "exit": 10}
        for st in trade.STRATEGIES:
            p1, _ = trade.positions(self.close, st, params)
            mod = self.close.copy()
            mod[1000:] *= 3
            p2, _ = trade.positions(mod, st, params)
            np.testing.assert_array_equal(p1[:1000], p2[:1000], err_msg=st)

    def test_all_strategies_run(self):
        params = {"fast": 50, "slow": 200, "rsi_period": 14, "rsi_low": 30, "rsi_high": 55,
                  "lookback": 126, "entry": 55, "exit": 20}
        for st in trade.STRATEGIES:
            pos, warm = trade.positions(self.close, st, params)
            r = trade.run_backtest(self.close, pos, warm, 0.001, 252)
            self.assertTrue(math.isfinite(r["total_return"]), st)
            self.assertTrue(set(np.unique(pos)) <= {0.0, 1.0}, st)


class Simulation(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(2)
        # fette Raender + negative Schiefe, damit die Kalibrierung gefordert ist
        r = rng.standard_t(3, size=(1500, 2)) * 0.012
        r[:, 1] = 0.6 * r[:, 0] + 0.8 * r[:, 1]
        self.R = r - 0.002 * (rng.random((1500, 2)) < 0.03)

    def _mean_return(self, method, drift):
        sim = trade.simulate_paths(self.R, np.array([0.5, 0.5]), 252, 20000, method, drift, 10,
                                   np.random.default_rng(3), 100.0)
        return sim["final"].mean() / 100 - 1, sim

    def test_zero_drift_is_unbiased(self):
        for method in ("gbm", "bootstrap"):
            m, _ = self._mean_return(method, "zero")
            self.assertLess(abs(m), 0.01, method)

    def test_target_drift(self):
        for method in ("gbm", "bootstrap"):
            m, _ = self._mean_return(method, 0.08)
            self.assertLess(abs(m - 0.08), 0.012, method)

    def test_bands_monotone_and_summary(self):
        _, sim = self._mean_return("bootstrap", "zero")
        b = sim["bands"]
        self.assertTrue((np.diff(b[1:], axis=1) >= 0).all())
        s = trade.summarize_sim(sim, 100.0, target=120)
        self.assertTrue(0 <= s["prob_gain"] <= 1)
        self.assertGreaterEqual(s["cvar95"], s["var95"])
        self.assertTrue(-1 < s["max_drawdown_p95"] <= s["max_drawdown_median"] <= 0)

    def test_zero_vol(self):
        R = np.zeros((300, 1))
        sim = trade.simulate_paths(R, np.array([1.0]), 50, 100, "gbm", "zero", 10,
                                   np.random.default_rng(0), 42)
        np.testing.assert_allclose(sim["final"], 42.0)


def make_series(close, volume=None, meta=None):
    from datetime import date, timedelta
    n = len(close)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    vol = np.full(n, 1e6) if volume is None else volume
    return trade.Series("TEST", "Test AG", "EUR", "XETRA", "1d", dates, close, close, close,
                        close * 1.01, close * 0.99, vol, meta or {})


class Signals(unittest.TestCase):
    def test_oversold_and_near_low(self):
        rng = np.random.default_rng(4)
        close = np.concatenate([np.linspace(100, 150, 250), np.linspace(150, 90, 30)])
        close *= np.exp(rng.normal(0, 0.001, len(close)))
        s = make_series(close)
        sig = trade.signals(trade.snapshot(s), s)
        self.assertTrue(any("ueberverkauft" in x for x in sig), sig)
        self.assertTrue(any("52-Wochen-Tief" in x for x in sig), sig)

    def test_volume_spike_and_big_move(self):
        rng = np.random.default_rng(5)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
        close[-1] = close[-2] * 1.08
        vol = np.full(300, 1e6)
        s = make_series(close, vol, {"regularMarketVolume": 5e6})
        sig = trade.signals(trade.snapshot(s), s)
        self.assertTrue(any("Volumen" in x for x in sig), sig)
        self.assertTrue(any("Tagesbewegung" in x for x in sig), sig)

    def test_crypto_uses_365_days(self):
        s = make_series(np.linspace(100, 200, 400), meta={"instrumentType": "CRYPTOCURRENCY"})
        self.assertEqual(s.ppy, 365)
        self.assertEqual(make_series(np.linspace(100, 200, 400)).ppy, 252)


class Misc(unittest.TestCase):
    def test_parse_drift(self):
        self.assertEqual(trade._parse_drift("zero"), "zero")
        self.assertAlmostEqual(trade._parse_drift("7%"), 0.07)
        self.assertAlmostEqual(trade._parse_drift("0.07"), 0.07)
        self.assertAlmostEqual(trade._parse_drift("7"), 0.07)

    def test_pct_and_table(self):
        self.assertEqual(trade.pct(0.1234), "+12.34%")
        self.assertEqual(trade.pct(float("nan")), "-")
        t = trade.table([["A", "1"]], ["x", "y"])
        self.assertIn("A", t)

    def test_svg_chart(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.svg"
            y = np.array([1.0, 2, np.nan, 3])
            trade.svg_chart(str(p), "t", ["a", "b", "c", "d"], [("y", y, "#000")],
                            bands=[(y - 1, y + 1, "#00f", 0.2)])
            self.assertTrue(p.read_text().startswith("<svg"))


if __name__ == "__main__":
    unittest.main()
