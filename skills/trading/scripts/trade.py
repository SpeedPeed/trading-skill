#!/usr/bin/env python3
"""trade.py - Kurse, News, Analyse, Simulation und Backtests fuer Aktien & Co.

Datenquelle: oeffentliche Yahoo-Finance-Endpunkte (kein API-Key noetig).
Abhaengigkeiten: Python >= 3.9 und numpy.

Beispiele:
    trade.py quote AAPL SAP.DE BTC-USD
    trade.py market
    trade.py analyze NVDA --range 2y
    trade.py simulate NVDA --days 252 --paths 10000 --svg nvda.svg
    trade.py simulate AAPL MSFT GOOGL --weights 0.5 0.3 0.2 --amount 10000
    trade.py backtest SPY --strategy all --range 10y
    trade.py news "Nvidia earnings"
    trade.py watch add AAPL MSFT && trade.py watch show && trade.py watch scan

Nur zu Analyse- und Lernzwecken. Keine Anlageberatung.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.exit("numpy fehlt: pip install numpy")

# Ein voller Browser-UA ohne Cookies wird von Yahoo mit 429 abgewiesen, der kurze nicht.
UA = os.environ.get("TRADING_UA", "Mozilla/5.0 (X11; Linux x86_64)")
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
CACHE_DIR = Path(os.environ.get("TRADING_CACHE", Path.home() / ".cache" / "trading-skill"))
CACHE_TTL = int(os.environ.get("TRADING_CACHE_TTL", "300"))
WATCHLIST = Path(os.environ.get(
    "TRADING_WATCHLIST", Path.home() / ".config" / "trading-skill" / "watchlist.json"))
PERIODS_PER_YEAR = {"1d": 252, "5d": 52, "1wk": 52, "1mo": 12, "3mo": 4}

MARKET_OVERVIEW = [
    ("^GSPC", "S&P 500"), ("^NDX", "Nasdaq 100"), ("^DJI", "Dow Jones"),
    ("^GDAXI", "DAX"), ("^STOXX50E", "Euro Stoxx 50"), ("^FTSE", "FTSE 100"),
    ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"), ("^VIX", "VIX (Volatilitaet)"),
    ("^TNX", "US 10J Rendite (%)"), ("EURUSD=X", "EUR/USD"), ("GC=F", "Gold"),
    ("CL=F", "Oel WTI"), ("BTC-USD", "Bitcoin"),
]


class DataError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Daten holen
# --------------------------------------------------------------------------- #

def _http_json(path: str, params: dict, use_cache: bool = True) -> dict:
    qs = urlencode(params)
    key = hashlib.sha1(f"{path}?{qs}".encode()).hexdigest()
    cache_file = CACHE_DIR / f"{key}.json"
    if use_cache and CACHE_TTL > 0 and cache_file.exists():
        if time.time() - cache_file.stat().st_mtime < CACHE_TTL:
            try:
                return json.loads(cache_file.read_text())
            except (OSError, ValueError):
                pass

    last_err: Exception | None = None
    for attempt in range(4):
        host = HOSTS[attempt % len(HOSTS)]
        req = Request(f"{host}{path}?{qs}", headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urlopen(req, timeout=20) as resp:
                data = json.load(resp)
            if use_cache and CACHE_TTL > 0:
                try:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps(data))
                except OSError:
                    pass
            return data
        except HTTPError as e:
            last_err = e
            if e.code == 404:
                try:
                    body = json.load(e)
                    msg = (body.get("chart", {}).get("error") or {}).get("description")
                except Exception:
                    msg = None
                raise DataError(msg or "Nicht gefunden (Symbol pruefen, z.B. mit 'trade.py search').")
            if e.code in (429,) or e.code >= 500:
                time.sleep(1.5 * 2 ** attempt)
                continue
            raise DataError(f"HTTP {e.code} von Yahoo Finance")
        except (URLError, TimeoutError, ValueError) as e:
            last_err = e
            time.sleep(1.5 * 2 ** attempt)
    raise DataError(f"Yahoo Finance nicht erreichbar: {last_err}")


@dataclass
class Series:
    symbol: str
    name: str
    currency: str
    exchange: str
    interval: str
    dates: list
    close: np.ndarray          # dividenden-/split-bereinigt, wenn verfuegbar
    raw_close: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    volume: np.ndarray
    meta: dict = field(default_factory=dict)

    @property
    def ppy(self) -> int:
        if self.interval == "1d" and self.meta.get("instrumentType") == "CRYPTOCURRENCY":
            return 365  # Krypto handelt taeglich
        return PERIODS_PER_YEAR.get(self.interval, 252)

    @property
    def price(self) -> float:
        p = self.meta.get("regularMarketPrice")
        return float(p) if p is not None else float(self.raw_close[-1])


def fetch(symbol: str, range_: str = "1y", interval: str = "1d") -> Series:
    data = _http_json(f"/v8/finance/chart/{quote(symbol)}", {
        "range": range_, "interval": interval, "includeAdjustedClose": "true",
        "events": "div,split",
    })
    chart = data.get("chart") or {}
    if chart.get("error"):
        raise DataError(f"{symbol}: {chart['error'].get('description')}")
    results = chart.get("result") or []
    if not results:
        raise DataError(f"{symbol}: keine Daten")
    r = results[0]
    meta = r.get("meta", {})
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    adj = (r.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
    raw = q.get("close") or []
    closes = adj if adj and len(adj) == len(ts) else raw
    off = int(meta.get("gmtoffset") or 0)
    daily = interval in ("1d", "5d", "1wk", "1mo", "3mo")

    rows = []
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        rc = raw[i] if i < len(raw) else None
        if c is None or rc is None or not math.isfinite(c):
            continue
        d = datetime.fromtimestamp(t + off, tz=timezone.utc).replace(tzinfo=None)  # Boersen-Ortszeit
        d = d.date() if daily else d
        def g(key, default=rc):
            v = (q.get(key) or [None] * len(ts))[i]
            return default if v is None else v
        rows.append((d, c, rc, g("open"), g("high"), g("low"), g("volume", 0)))
    if not rows:
        raise DataError(f"{symbol}: keine gueltigen Kurse im Zeitraum {range_}")

    # Doppelte Tage (z.B. Intraday-Punkt des aktuellen Tages) -> letzten behalten
    dedup = {}
    for row in rows:
        dedup[row[0]] = row
    rows = [dedup[d] for d in sorted(dedup)] if daily else rows
    # Bei Wochen-/Monatsdaten haengt Yahoo einen Live-Punkt an, der in die laufende Periode faellt.
    if len(rows) > 1 and interval in ("1wk", "1mo", "3mo"):
        a, b = rows[-2][0], rows[-1][0]
        same = (b - a).days < 7 if interval == "1wk" else (a.year, (a.month - 1) // (3 if interval == "3mo" else 1)) \
            == (b.year, (b.month - 1) // (3 if interval == "3mo" else 1))
        if same:
            rows.pop()

    cols = list(zip(*rows))
    return Series(
        symbol=meta.get("symbol", symbol),
        name=(meta.get("longName") or meta.get("shortName") or symbol).strip(),
        currency=meta.get("currency") or "",
        exchange=meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        interval=interval,
        dates=list(cols[0]),
        close=np.array(cols[1], dtype=float),
        raw_close=np.array(cols[2], dtype=float),
        open=np.array(cols[3], dtype=float),
        high=np.array(cols[4], dtype=float),
        low=np.array(cols[5], dtype=float),
        volume=np.array(cols[6], dtype=float),
        meta=meta,
    )


def fetch_many(symbols, range_="1y", interval="1d", strict=False):
    """Mehrere Symbole parallel laden. Gibt {symbol: Series|DataError} zurueck."""
    def one(s):
        try:
            return s, fetch(s, range_, interval)
        except DataError as e:
            if strict:
                raise
            return s, e
    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(ex.map(one, symbols))


def search(query: str, news: int = 0, quotes: int = 8) -> dict:
    return _http_json("/v1/finance/search", {
        "q": query, "quotesCount": quotes, "newsCount": news,
        "enableFuzzyQuery": "false",
    }, use_cache=False)


# --------------------------------------------------------------------------- #
# Indikatoren & Kennzahlen
# --------------------------------------------------------------------------- #

def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2.0 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def rsi(x: np.ndarray, n: int = 14) -> np.ndarray:
    """RSI nach Wilder."""
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return out
    d = np.diff(x)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[:n].mean(), dn[:n].mean()
    for i in range(n, len(x)):
        if i > n:
            au = (au * (n - 1) + up[i - 1]) / n
            ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def macd(x: np.ndarray, fast=12, slow=26, signal=9):
    line = ema(x, fast) - ema(x, slow)
    sig = np.full(len(x), np.nan)
    valid = ~np.isnan(line)
    if valid.sum() >= signal:
        sig[valid] = ema(line[valid], signal)
    return line, sig, line - sig


def max_drawdown(x: np.ndarray) -> float:
    peak = np.maximum.accumulate(x)
    return float((x / peak - 1).min())


def log_returns(x: np.ndarray) -> np.ndarray:
    return np.diff(np.log(x))


def ret_over(x: np.ndarray, n: int) -> float:
    return float(x[-1] / x[-1 - n] - 1) if len(x) > n else float("nan")


def align(series: list) -> tuple:
    """Gemeinsame Handelstage mehrerer Serien -> (dates, matrix T x k)."""
    common = set(series[0].dates)
    for s in series[1:]:
        common &= set(s.dates)
    dates = sorted(common)
    mat = np.column_stack([
        np.array([dict(zip(s.dates, s.close))[d] for d in dates]) for s in series])
    return dates, mat


# --------------------------------------------------------------------------- #
# Formatierung
# --------------------------------------------------------------------------- #

def pct(x, digits=2, sign=True) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    return f"{x * 100:{'+' if sign else ''}.{digits}f}%"


def num(x, digits=2) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    if abs(x) >= 1e12:
        return f"{x / 1e12:.2f} Bio."
    if abs(x) >= 1e9:
        return f"{x / 1e9:.2f} Mrd."
    if abs(x) >= 1e6:
        return f"{x / 1e6:.2f} Mio."
    return f"{x:,.{digits}f}"


def table(rows, headers, align_right=None) -> str:
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    right = align_right if align_right is not None else [i > 0 for i in range(len(headers))]
    def fmt(r):
        return "  ".join(c.rjust(w) if right[i] else c.ljust(w)
                         for i, (c, w) in enumerate(zip(r, widths)))
    out = [fmt(headers), "  ".join("-" * w for w in widths)]
    out += [fmt(r) for r in rows]
    return "\n".join(out)


def emit(args, payload: dict, text: str):
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
    else:
        print(text)


# --------------------------------------------------------------------------- #
# SVG-Charts (ohne matplotlib)
# --------------------------------------------------------------------------- #

PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#db2777"]


def svg_chart(path: str, title: str, x_labels: list, lines: list, bands=(), y_label=""):
    """lines: [(label, y-array, farbe)], bands: [(lo, hi, farbe, deckkraft)]."""
    W, H, L, R, T, B = 960, 460, 70, 220, 40, 40
    ys = [v for _, y, _ in lines for v in y if math.isfinite(v)]
    ys += [v for lo, hi, _, _ in bands for v in list(lo) + list(hi) if math.isfinite(v)]
    ymin, ymax = min(ys), max(ys)
    pad = (ymax - ymin) * 0.05 or 1
    ymin, ymax = ymin - pad, ymax + pad
    n = max(len(y) for _, y, _ in lines) if lines else len(bands[0][0])

    def px(i):
        return L + (W - L - R) * i / max(n - 1, 1)

    def py(v):
        return T + (H - T - B) * (1 - (v - ymin) / (ymax - ymin))

    def poly(y):
        pts, segs = [], []
        for i, v in enumerate(y):
            if math.isfinite(v):
                pts.append(f"{px(i):.1f},{py(v):.1f}")
            elif pts:
                segs.append(pts)
                pts = []
        if pts:
            segs.append(pts)
        return segs

    el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
          f'viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="12">',
          f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
          f'<text x="{L}" y="24" font-size="16" font-weight="bold" fill="#111">{title}</text>']
    for k in range(6):
        v = ymin + (ymax - ymin) * k / 5
        y = py(v)
        el.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        el.append(f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" fill="#555">{v:,.2f}</text>')
    if y_label:
        el.append(f'<text x="14" y="{T + (H - T - B) / 2}" fill="#555" '
                  f'transform="rotate(-90 14 {T + (H - T - B) / 2})" text-anchor="middle">{y_label}</text>')
    for k in range(5):
        i = round((n - 1) * k / 4)
        if i < len(x_labels):
            el.append(f'<text x="{px(i):.1f}" y="{H - 14}" text-anchor="middle" fill="#555">'
                      f'{x_labels[i]}</text>')
    for lo, hi, color, op in bands:
        top = [f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(hi)]
        bot = [f"{px(i):.1f},{py(v):.1f}" for i, v in reversed(list(enumerate(lo)))]
        el.append(f'<polygon points="{" ".join(top + bot)}" fill="{color}" fill-opacity="{op}"/>')
    legend = 0
    for label, y, color in lines:
        for seg in poly(y):
            el.append(f'<polyline points="{" ".join(seg)}" fill="none" stroke="{color}" stroke-width="1.8"/>')
        if not label:
            continue
        ly = T + 16 + legend * 18
        legend += 1
        el.append(f'<line x1="{W - R + 12}" x2="{W - R + 30}" y1="{ly - 4}" y2="{ly - 4}" '
                  f'stroke="{color}" stroke-width="3"/>')
        el.append(f'<text x="{W - R + 36}" y="{ly}" fill="#111">{label}</text>')
    el.append("</svg>")
    Path(path).write_text("\n".join(el))
    return path


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #

def simulate_paths(R: np.ndarray, weights: np.ndarray, days: int, paths: int, method: str,
                   drift, block: int, rng: np.random.Generator, start_value: float,
                   ppy: int = 252, quantiles=(5, 25, 50, 75, 95)) -> dict:
    """Monte-Carlo-Simulation eines Buy-and-Hold-Portfolios.

    R: Log-Renditen (T x k). drift: "hist" | "zero" | float (erwartete Jahresrendite, einfach).
    method: "gbm" (multivariate Normalverteilung, korreliert) oder
            "bootstrap" (Block-Bootstrap historischer Tage -> fette Raender, Vol-Cluster).
    """
    T, k = R.shape
    mean = R.mean(axis=0)
    cov = np.atleast_2d(np.cov(R, rowvar=False)) if T > 1 else np.zeros((k, k))
    var = np.diag(cov)
    target = None if drift == "hist" else np.log1p(0.0 if drift == "zero" else float(drift)) / ppy

    if method == "gbm":
        # Lognormal: E[exp(r)] = exp(mu + var/2) -> mu so waehlen, dass E[Endwert] = (1+a)^Jahre
        mu = mean if target is None else target - 0.5 * var
        growth = mu + 0.5 * var
        try:
            chol = np.linalg.cholesky(cov)
        except np.linalg.LinAlgError:
            # singulaere Kovarianz (z.B. identische Werte): Wurzel ueber Eigenzerlegung
            vals, vecs = np.linalg.eigh(cov)
            chol = vecs * np.sqrt(np.clip(vals, 0, None))
    elif method == "bootstrap":
        # Empirische Verteilung: Verschiebung so, dass der erwartete Brutto-Ertrag eines
        # gezogenen Blocks exakt dem Ziel entspricht (beruecksichtigt Schiefe, fette Raender
        # und Autokorrelation innerhalb der Bloecke, nicht nur die Varianz).
        block = max(1, min(block, T))
        centered = R - mean
        c = np.vstack([np.zeros((1, k)), np.cumsum(centered, axis=0)])
        block_sums = c[block:] - c[:-block]                      # alle moeglichen Bloecke
        log_mgf = np.log(np.exp(block_sums).mean(axis=0)) / block
        mu = mean if target is None else target - log_mgf
        Rc = centered + mu
        growth = log_mgf + mu
        nblocks = math.ceil(days / block)
        starts = rng.integers(0, T - block + 1, size=(paths, nblocks), dtype=np.int32)
        idx = (starts[:, :, None] + np.arange(block, dtype=np.int32)).reshape(paths, -1)[:, :days]
    else:
        raise ValueError(method)

    start_value = float(start_value)
    cum = np.zeros((paths, k))
    value = np.full(paths, start_value)
    peak = value.copy()
    mdd = np.zeros(paths)
    bands = np.empty((days + 1, len(quantiles)))
    bands[0] = start_value
    for d in range(days):
        step = (rng.standard_normal((paths, k)) @ chol.T + mu) if method == "gbm" else Rc[idx[:, d]]
        cum += step
        value = start_value * (np.exp(cum) @ weights)
        np.maximum(peak, value, out=peak)
        np.minimum(mdd, value / peak - 1, out=mdd)
        bands[d + 1] = np.percentile(value, quantiles)

    return {"final": value, "bands": bands, "max_drawdown": mdd, "mu": mu, "var": var,
            "growth": growth, "quantiles": list(quantiles)}


def summarize_sim(sim: dict, start_value: float, target=None) -> dict:
    f = sim["final"]
    rets = f / start_value - 1
    var95 = -np.percentile(rets, 5)
    cvar95 = -rets[rets <= np.percentile(rets, 5)].mean()
    out = {
        "start": start_value,
        "mean": float(f.mean()),
        "percentiles": {f"p{q}": float(np.percentile(f, q)) for q in (1, 5, 10, 25, 50, 75, 90, 95, 99)},
        "prob_gain": float((f > start_value).mean()),
        "prob_loss_10": float((rets < -0.10).mean()),
        "prob_loss_25": float((rets < -0.25).mean()),
        "prob_gain_25": float((rets > 0.25).mean()),
        "var95": float(var95),
        "cvar95": float(cvar95),
        "max_drawdown_median": float(np.median(sim["max_drawdown"])),
        "max_drawdown_p95": float(np.percentile(sim["max_drawdown"], 5)),
    }
    if target is not None:
        out["target"] = target
        out["prob_above_target"] = float((f >= target).mean())
    return out


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #

STRATEGIES = ("buyhold", "sma", "rsi", "momentum", "breakout")


def positions(close: np.ndarray, strategy: str, p: dict) -> tuple:
    """Position (0/1) am Schluss von Tag t, gehalten bis t+1. Gibt (pos, warmup) zurueck.
    Signale nutzen nur Daten bis einschliesslich t -> kein Look-ahead."""
    n = len(close)
    pos = np.zeros(n)
    if strategy == "buyhold":
        return np.ones(n), 0
    if strategy == "sma":
        f, s = sma(close, p["fast"]), sma(close, p["slow"])
        warm = p["slow"] - 1
        pos[warm:] = (f[warm:] > s[warm:]).astype(float)
        return pos, warm
    if strategy == "rsi":
        r = rsi(close, p["rsi_period"])
        warm = p["rsi_period"]
        inpos = 0.0
        for t in range(warm, n):
            if inpos == 0 and r[t] < p["rsi_low"]:
                inpos = 1.0
            elif inpos == 1 and r[t] > p["rsi_high"]:
                inpos = 0.0
            pos[t] = inpos
        return pos, warm
    if strategy == "momentum":
        L = p["lookback"]
        pos[L:] = (close[L:] / close[:-L] - 1 > 0).astype(float)
        return pos, L
    if strategy == "breakout":
        N, M = p["entry"], p["exit"]
        warm = max(N, M)
        inpos = 0.0
        for t in range(warm, n):
            if inpos == 0 and close[t] >= close[t - N:t].max():
                inpos = 1.0
            elif inpos == 1 and close[t] <= close[t - M:t].min():
                inpos = 0.0
            pos[t] = inpos
        return pos, warm
    raise ValueError(strategy)


def run_backtest(close: np.ndarray, pos: np.ndarray, start: int, cost: float, ppy: int) -> dict:
    """Rendite von t nach t+1 mit Position pos[t]; Kosten bei Positionswechsel (pro Seite)."""
    ret = close[1:] / close[:-1] - 1
    p = pos[start:-1]
    prev = np.concatenate([[0.0], p[:-1]])
    strat = p * ret[start:] - cost * np.abs(p - prev)
    equity = np.concatenate([[1.0], np.cumprod(1 + strat)])
    n = len(strat)
    years = n / ppy
    vol = float(strat.std(ddof=1) * math.sqrt(ppy)) if n > 1 else float("nan")
    sharpe = float(strat.mean() * ppy / vol) if vol and vol > 0 else float("nan")
    downside = strat[strat < 0]
    dvol = float(math.sqrt((downside ** 2).sum() / n) * math.sqrt(ppy)) if n else float("nan")
    sortino = float(strat.mean() * ppy / dvol) if dvol and dvol > 0 else float("nan")

    trades = []
    entry = None
    seg = pos[start:]
    c = close[start:]
    for i in range(len(seg)):
        if seg[i] == 1 and (i == 0 or seg[i - 1] == 0):
            entry = i
        if entry is not None and (seg[i] == 0 or i == len(seg) - 1) and i > entry:
            trades.append(c[i] / c[entry] - 1)
            entry = None
    trades = np.array(trades)
    cagr = float(equity[-1] ** (1 / years) - 1) if years > 0 and equity[-1] > 0 else float("nan")
    mdd = max_drawdown(equity)
    return {
        "total_return": float(equity[-1] - 1), "cagr": cagr, "volatility": vol,
        "sharpe": sharpe, "sortino": sortino, "max_drawdown": mdd,
        "calmar": float(cagr / -mdd) if mdd < 0 else float("nan"),
        "exposure": float(p.mean()) if n else float("nan"),
        "trades": int(len(trades)),
        "win_rate": float((trades > 0).mean()) if len(trades) else float("nan"),
        "avg_trade": float(trades.mean()) if len(trades) else float("nan"),
        "equity": equity,
    }


# --------------------------------------------------------------------------- #
# Kommandos
# --------------------------------------------------------------------------- #

def snapshot(s: Series) -> dict:
    c = s.close
    m = s.meta
    ppy = s.ppy
    week, month, quarter = round(ppy / 52), round(ppy / 12), round(ppy / 4)
    day = (m["regularMarketChangePercent"] / 100) if m.get("regularMarketChangePercent") is not None \
        else ret_over(s.raw_close, 1)
    r14 = rsi(c, 14)
    s50, s200 = sma(c, 50), sma(c, 200)
    hi52 = m.get("fiftyTwoWeekHigh") or float(s.high[-ppy:].max())
    lo52 = m.get("fiftyTwoWeekLow") or float(s.low[-ppy:].min())
    ytd_idx = next((i for i, d in enumerate(s.dates) if d.year == s.dates[-1].year), None)
    ytd = float(c[-1] / c[ytd_idx - 1] - 1) if ytd_idx and ytd_idx > 0 else float("nan")
    lr = log_returns(c[-quarter - 1:]) if len(c) > 20 else np.array([np.nan])
    return {
        "symbol": s.symbol, "name": s.name, "currency": s.currency, "price": s.price,
        "day": day, "w1": ret_over(c, week), "m1": ret_over(c, month), "m3": ret_over(c, quarter),
        "ytd": ytd, "y1": ret_over(c, ppy) if len(c) > ppy else float(c[-1] / c[0] - 1),
        "rsi14": float(r14[-1]), "sma50": float(s50[-1]), "sma200": float(s200[-1]),
        "vs_sma200": float(c[-1] / s200[-1] - 1) if math.isfinite(s200[-1]) else float("nan"),
        "high52": hi52, "low52": lo52, "from_high52": s.price / hi52 - 1 if hi52 else float("nan"),
        "vol3m": float(lr.std(ddof=1) * math.sqrt(ppy)) if len(lr) > 2 else float("nan"),
        "ppy": ppy,
        "volume": m.get("regularMarketVolume"),
        "avg_volume20": float(s.volume[-20:].mean()) if len(s.volume) else float("nan"),
        "date": s.dates[-1],
    }


def signals(snap: dict, s: Series) -> list:
    out = []
    c = s.close
    if math.isfinite(snap["rsi14"]):
        if snap["rsi14"] < 30:
            out.append(f"RSI {snap['rsi14']:.0f} ueberverkauft")
        elif snap["rsi14"] > 70:
            out.append(f"RSI {snap['rsi14']:.0f} ueberkauft")
    s50, s200 = sma(c, 50), sma(c, 200)
    if len(c) > 205 and math.isfinite(s200[-6]):
        diff_now, diff_then = s50[-1] - s200[-1], s50[-6] - s200[-6]
        if diff_now > 0 >= diff_then:
            out.append("Golden Cross (SMA50 > SMA200, letzte 5 Tage)")
        elif diff_now < 0 <= diff_then:
            out.append("Death Cross (SMA50 < SMA200, letzte 5 Tage)")
    if len(c) > 2 and math.isfinite(s200[-1]) and math.isfinite(s200[-2]):
        if c[-1] > s200[-1] and c[-2] <= s200[-2]:
            out.append("Kurs hat SMA200 nach oben gekreuzt")
        elif c[-1] < s200[-1] and c[-2] >= s200[-2]:
            out.append("Kurs hat SMA200 nach unten gekreuzt")
    if math.isfinite(snap["from_high52"]) and snap["from_high52"] > -0.02:
        out.append("nahe 52-Wochen-Hoch")
    if snap["low52"] and snap["price"] / snap["low52"] - 1 < 0.02:
        out.append("nahe 52-Wochen-Tief")
    if math.isfinite(snap["day"]) and math.isfinite(snap["vol3m"]):
        daily_sigma = snap["vol3m"] / math.sqrt(snap["ppy"])
        if daily_sigma > 0 and abs(snap["day"]) > 2.5 * daily_sigma:
            out.append(f"grosse Tagesbewegung ({pct(snap['day'])}, {abs(snap['day']) / daily_sigma:.1f} Sigma)")
    if snap["volume"] and snap["avg_volume20"] and snap["volume"] > 2 * snap["avg_volume20"]:
        out.append(f"Volumen {snap['volume'] / snap['avg_volume20']:.1f}x ueber 20-Tage-Schnitt")
    return out


def _load_ok(results: dict) -> tuple:
    ok, errs = {}, []
    for sym, v in results.items():
        if isinstance(v, DataError):
            errs.append(f"! {sym}: {v}")
        else:
            ok[sym] = v
    return ok, errs


def cmd_quote(args):
    res = fetch_many(args.symbols, "1y", "1d")
    ok, errs = _load_ok(res)
    snaps = [snapshot(ok[s]) for s in args.symbols if s in ok]
    rows = [[sn["symbol"], sn["name"][:28], f"{num(sn['price'])} {sn['currency']}", pct(sn["day"]),
             pct(sn["m1"]), pct(sn["ytd"]), pct(sn["y1"]), f"{num(sn['low52'])} - {num(sn['high52'])}",
             num(sn["volume"], 0) if sn["volume"] else "-", str(sn["date"])] for sn in snaps]
    text = table(rows, ["Symbol", "Name", "Kurs", "Heute", "1M", "YTD", "1J", "52W-Spanne", "Volumen", "Stand"],
                 [False, False] + [True] * 8)
    emit(args, {"quotes": snaps, "errors": errs}, "\n".join([text] + errs))


def cmd_market(args):
    syms = [s for s, _ in MARKET_OVERVIEW]
    names = dict(MARKET_OVERVIEW)
    ok, errs = _load_ok(fetch_many(syms, "1y", "1d"))
    rows, payload = [], []
    for s in syms:
        if s not in ok:
            continue
        sn = snapshot(ok[s])
        sn["label"] = names[s]
        payload.append(sn)
        rows.append([names[s], s, num(sn["price"]), pct(sn["day"]), pct(sn["w1"]), pct(sn["m1"]),
                     pct(sn["ytd"]), pct(sn["from_high52"])])
    text = table(rows, ["Markt", "Symbol", "Stand", "Heute", "1W", "1M", "YTD", "vom 52W-Hoch"],
                 [False, False] + [True] * 6)
    emit(args, {"market": payload, "errors": errs}, "\n".join([text] + errs))


def cmd_history(args):
    s = fetch(args.symbol, args.range, args.interval)
    rows = list(zip(s.dates, s.open, s.high, s.low, s.raw_close, s.close, s.volume))
    if args.csv:
        p = Path(args.csv)
        with p.open("w") as f:
            f.write("date,open,high,low,close,adj_close,volume\n")
            for r in rows:
                f.write(f"{r[0]},{r[1]:.4f},{r[2]:.4f},{r[3]:.4f},{r[4]:.4f},{r[5]:.4f},{int(r[6])}\n")
        print(f"{len(rows)} Zeilen -> {p}")
        return
    tail = rows[-args.tail:]
    text = f"{s.symbol} ({s.name}, {s.currency}) - {len(rows)} Datenpunkte, letzte {len(tail)}:\n"
    text += table([[str(r[0]), num(r[1]), num(r[2]), num(r[3]), num(r[4]), num(r[5]), num(r[6], 0)]
                   for r in tail], ["Datum", "Open", "High", "Low", "Close", "Adj.Close", "Volumen"])
    emit(args, {"symbol": s.symbol, "rows": [dict(zip(
        ["date", "open", "high", "low", "close", "adj_close", "volume"], r)) for r in rows]}, text)


def cmd_analyze(args):
    s = fetch(args.symbol, args.range, "1d")
    c = s.close
    if len(c) < 30:
        raise DataError("Zu wenig Daten fuer eine Analyse (mind. 30 Handelstage).")
    lr = log_returns(c)
    ppy = s.ppy
    vol = float(lr.std(ddof=1) * math.sqrt(ppy))
    years = len(lr) / ppy
    cagr = float((c[-1] / c[0]) ** (1 / years) - 1)
    sharpe = (lr.mean() * ppy - math.log1p(args.rf)) / vol if vol > 0 else float("nan")
    m_line, m_sig, m_hist = macd(c)
    sn = snapshot(s)

    beta = corr = float("nan")
    if args.benchmark and args.benchmark != s.symbol:
        try:
            b = fetch(args.benchmark, args.range, "1d")
            dates, mat = align([s, b])
            r = np.diff(np.log(mat), axis=0)
            if len(r) > 20:
                cv = np.cov(r, rowvar=False)
                beta = float(cv[0, 1] / cv[1, 1])
                corr = float(np.corrcoef(r, rowvar=False)[0, 1])
        except DataError:
            pass

    skew = float(((lr - lr.mean()) ** 3).mean() / lr.std() ** 3)
    kurt = float(((lr - lr.mean()) ** 4).mean() / lr.std() ** 4 - 3)
    var95 = float(-np.percentile(np.expm1(lr), 5))
    trend = []
    if math.isfinite(sn["sma200"]):
        trend.append("ueber SMA200 (langfristiger Aufwaertstrend)" if c[-1] > sn["sma200"]
                     else "unter SMA200 (langfristiger Abwaertstrend)")
    if math.isfinite(sn["sma50"]) and math.isfinite(sn["sma200"]):
        trend.append("SMA50 > SMA200" if sn["sma50"] > sn["sma200"] else "SMA50 < SMA200")
    if math.isfinite(m_hist[-1]):
        trend.append("MACD ueber Signallinie (Momentum positiv)" if m_hist[-1] > 0
                     else "MACD unter Signallinie (Momentum negativ)")

    res = {
        "symbol": s.symbol, "name": s.name, "currency": s.currency, "exchange": s.exchange,
        "range": args.range, "from": s.dates[0], "to": s.dates[-1], "price": s.price,
        "total_return": float(c[-1] / c[0] - 1), "cagr": cagr, "volatility": vol,
        "sharpe": float(sharpe), "max_drawdown": max_drawdown(c),
        "var95_1d": var95, "skew": skew, "excess_kurtosis": kurt,
        "beta": beta, "correlation": corr, "benchmark": args.benchmark,
        "rsi14": sn["rsi14"], "macd": float(m_line[-1]), "macd_signal": float(m_sig[-1]),
        "sma20": float(sma(c, 20)[-1]), "sma50": sn["sma50"], "sma200": sn["sma200"],
        "high52": sn["high52"], "low52": sn["low52"], "from_high52": sn["from_high52"],
        "returns": {"1W": sn["w1"], "1M": sn["m1"], "3M": sn["m3"], "YTD": sn["ytd"]},
        "trend": trend, "signals": signals(sn, s),
    }
    lines = [
        f"{s.name} ({s.symbol}) - {s.exchange}, {s.currency}",
        f"Zeitraum {res['from']} bis {res['to']} ({args.range}), Kurs {num(s.price)} {s.currency}",
        "",
        "Rendite & Risiko",
        f"  Gesamtrendite     {pct(res['total_return'])}    CAGR {pct(cagr)}",
        f"  Volatilitaet p.a. {pct(vol, sign=False)}    Sharpe {res['sharpe']:.2f} (rf {pct(args.rf, sign=False)})",
        f"  Max. Drawdown     {pct(res['max_drawdown'])}",
        f"  VaR 95% (1 Tag)   {pct(var95, sign=False)}   Schiefe {skew:.2f}   Exzess-Kurtosis {kurt:.2f}",
        f"  Beta/Korr. zu {args.benchmark}: {beta:.2f} / {corr:.2f}" if math.isfinite(beta) else None,
        "",
        "Performance  " + "   ".join(f"{k} {pct(v)}" for k, v in res["returns"].items()),
        "",
        "Technik",
        f"  RSI(14) {sn['rsi14']:.1f}   MACD {res['macd']:.3f} / Signal {res['macd_signal']:.3f}",
        f"  SMA20 {num(res['sma20'])}   SMA50 {num(sn['sma50'])}   SMA200 {num(sn['sma200'])}",
        f"  52W: {num(sn['low52'])} - {num(sn['high52'])} (aktuell {pct(sn['from_high52'])} vom Hoch)",
        "  Trend: " + "; ".join(trend),
        "  Signale: " + ("; ".join(res["signals"]) or "keine besonderen"),
    ]
    if args.svg:
        labels = [d.strftime("%Y-%m") for d in s.dates]
        svg_chart(args.svg, f"{s.symbol} - Kurs (bereinigt) mit SMA50/200", labels,
                  [(s.symbol, c, PALETTE[0]), ("SMA50", sma(c, 50), PALETTE[3]),
                   ("SMA200", sma(c, 200), PALETTE[1])], y_label=s.currency)
        lines.append(f"\nChart: {args.svg}")
    emit(args, res, "\n".join(l for l in lines if l is not None))


def _parse_drift(v: str):
    if v in ("hist", "zero"):
        return v
    try:
        x = float(v.rstrip("%"))
        return x / 100 if v.endswith("%") or abs(x) > 1 else x
    except ValueError:
        raise argparse.ArgumentTypeError("drift: hist | zero | Jahresrendite, z.B. 0.07 oder 7%")


def cmd_simulate(args):
    syms = args.symbols
    if args.weights and len(args.weights) != len(syms):
        raise DataError("Anzahl --weights muss der Anzahl Symbole entsprechen.")
    w = np.array(args.weights or [1.0] * len(syms), dtype=float)
    w = w / w.sum()
    res = fetch_many(syms, args.range, "1d", strict=True)
    series = [res[s] for s in syms]
    dates, mat = align(series)
    if len(dates) < 60:
        raise DataError("Zu wenig gemeinsame Historie (mind. 60 Tage) - laengeren --range waehlen.")
    R = np.diff(np.log(mat), axis=0)
    single = len(syms) == 1
    start = series[0].price if single else args.amount
    rng = np.random.default_rng(args.seed)
    ppy = min(s.ppy for s in series)  # gemeinsame Tage: Aktien 252, reine Krypto 365
    sim = simulate_paths(R, w, args.days, args.paths, args.method, args.drift, args.block, rng, start, ppy)
    summ = summarize_sim(sim, start, args.target)
    unit = series[0].currency if single else "(Portfoliowert)"

    exp_ann = float(w @ np.expm1(sim["growth"] * ppy))
    vol_ann = [float(math.sqrt(v * ppy)) for v in sim["var"]]
    corr = np.corrcoef(R, rowvar=False) if not single else None
    p = summ["percentiles"]
    lines = [
        f"Monte-Carlo: {', '.join(f'{s} {wi:.0%}' for s, wi in zip(syms, w))}",
        f"Methode {args.method}" + (f" (Block {args.block} Tage)" if args.method == "bootstrap" else "") +
        f", {args.paths:,} Pfade, {args.days} Handelstage (~{args.days / ppy:.1f} J.), "
        f"Historie {dates[0]} bis {dates[-1]}",
        f"Drift: {args.drift} -> erwartete Rendite ca. {pct(exp_ann)} p.a.; "
        f"Vola p.a.: " + ", ".join(f"{s} {pct(v, sign=False)}" for s, v in zip(syms, vol_ann)),
        "",
        f"Startwert {num(start)} {unit}",
        table([[f"{q}%", num(p[f'p{q}']), pct(p[f'p{q}'] / start - 1)]
               for q in (1, 5, 10, 25, 50, 75, 90, 95, 99)],
              ["Perzentil", "Endwert", "Rendite"]),
        "",
        f"Mittelwert         {num(summ['mean'])} ({pct(summ['mean'] / start - 1)})",
        f"P(Gewinn)          {pct(summ['prob_gain'], 1, False)}",
        f"P(Gewinn > 25%)    {pct(summ['prob_gain_25'], 1, False)}",
        f"P(Verlust > 10%)   {pct(summ['prob_loss_10'], 1, False)}",
        f"P(Verlust > 25%)   {pct(summ['prob_loss_25'], 1, False)}",
        f"VaR 95% / CVaR 95% {pct(summ['var95'], 1, False)} / {pct(summ['cvar95'], 1, False)} Verlust",
        f"Max. Drawdown      Median {pct(summ['max_drawdown_median'], 1)}, "
        f"schlechteste 5% {pct(summ['max_drawdown_p95'], 1)} oder schlimmer",
    ]
    if args.target is not None:
        lines.append(f"P(Endwert >= {num(args.target)}) {pct(summ['prob_above_target'], 1, False)}")
    if corr is not None:
        lines += ["", "Korrelation der Tagesrenditen:",
                  table([[s] + [f"{corr[i, j]:.2f}" for j in range(len(syms))] for i, s in enumerate(syms)],
                        [""] + list(syms))]
    lines += ["", "Hinweis: Modell auf Basis historischer Daten - keine Prognose, keine Anlageberatung."]
    if args.svg:
        b = sim["bands"]
        start_d = dates[-1]
        labels = [(start_d + timedelta(days=round(i * 365 / ppy))).strftime("%Y-%m") for i in range(args.days + 1)]
        svg_chart(args.svg, f"Monte-Carlo {'+'.join(syms)}: {args.paths:,} Pfade ({args.method})", labels,
                  [("Median", b[:, 2], PALETTE[0]), ("5% / 95%", b[:, 0], "#93c5fd"),
                   ("", b[:, 4], "#93c5fd"), ("Start", np.full(len(b), start), "#9ca3af")],
                  bands=[(b[:, 0], b[:, 4], PALETTE[0], 0.12), (b[:, 1], b[:, 3], PALETTE[0], 0.22)],
                  y_label=unit)
        lines.append(f"Chart: {args.svg}")
    summ.update({"symbols": syms, "weights": w.tolist(), "method": args.method, "days": args.days,
                 "paths": args.paths, "drift": args.drift, "expected_return_annual": exp_ann,
                 "volatility_annual": dict(zip(syms, vol_ann)),
                 "bands": {f"p{q}": sim["bands"][:, i].tolist() for i, q in enumerate(sim["quantiles"])}
                 if args.json and args.bands else None})
    emit(args, summ, "\n".join(lines))


def cmd_backtest(args):
    s = fetch(args.symbol, args.range, "1d")
    c = s.close
    params = {"fast": args.fast, "slow": args.slow, "rsi_period": args.rsi_period,
              "rsi_low": args.rsi_low, "rsi_high": args.rsi_high, "lookback": args.lookback,
              "entry": args.entry, "exit": args.exit}
    strategies = [x for x in STRATEGIES if x != "buyhold"] if args.strategy == "all" else [args.strategy]
    pos_map = {st: positions(c, st, params) for st in strategies}
    start = max(w for _, w in pos_map.values())
    if len(c) - start < 30:
        raise DataError("Zu wenig Daten nach der Aufwaermphase - laengeren --range waehlen.")
    results = {"buyhold": run_backtest(c, np.ones(len(c)), start, args.cost, s.ppy)}
    for st, (pos, _) in pos_map.items():
        results[st] = run_backtest(c, pos, start, args.cost, s.ppy)

    desc = {
        "buyhold": "Buy & Hold",
        "sma": f"SMA {args.fast}/{args.slow} Crossover",
        "rsi": f"RSI({args.rsi_period}) <{args.rsi_low:g} kaufen, >{args.rsi_high:g} verkaufen",
        "momentum": f"Momentum {args.lookback} Tage > 0",
        "breakout": f"Donchian Breakout {args.entry}/{args.exit}",
    }
    rows = [[desc[k], pct(r["total_return"], 1), pct(r["cagr"], 1), pct(r["volatility"], 1, False),
             f"{r['sharpe']:.2f}", f"{r['sortino']:.2f}", pct(r["max_drawdown"], 1),
             pct(r["exposure"], 0, False), str(r["trades"]), pct(r["win_rate"], 0, False)]
            for k, r in results.items()]
    text = "\n".join([
        f"Backtest {s.symbol} ({s.name}), {s.dates[start]} bis {s.dates[-1]}, Kosten {args.cost * 100:.2f}% pro Trade",
        "Signal am Schlusskurs, Ausfuehrung zum selben Schlusskurs, Cash ohne Zins, nur Long.",
        "",
        table(rows, ["Strategie", "Gesamt", "CAGR", "Vola", "Sharpe", "Sortino", "MaxDD", "Investiert",
                     "Trades", "Trefferq."], [False] + [True] * 9),
        "",
        "Achtung: Vergangene Ergebnisse sind keine Garantie. Parameter-Optimierung auf denselben",
        "Daten fuehrt zu Overfitting - Ergebnisse immer auf anderen Zeitraeumen/Werten gegenpruefen.",
    ])
    if args.svg:
        labels = [d.strftime("%Y-%m") for d in s.dates[start:]]
        svg_chart(args.svg, f"Backtest {s.symbol}: Equity-Kurven (Start = 1)", labels,
                  [(desc[k][:30], r["equity"], PALETTE[i % len(PALETTE)]) for i, (k, r) in enumerate(results.items())])
        text += f"\nChart: {args.svg}"
    payload = {k: {kk: vv for kk, vv in r.items() if kk != "equity"} for k, r in results.items()}
    emit(args, {"symbol": s.symbol, "from": s.dates[start], "to": s.dates[-1], "results": payload}, text)


def cmd_correlate(args):
    ok, errs = _load_ok(fetch_many(args.symbols, args.range, "1d"))
    syms = [s for s in args.symbols if s in ok]
    if len(syms) < 2:
        raise DataError("Mindestens zwei gueltige Symbole noetig.\n" + "\n".join(errs))
    dates, mat = align([ok[s] for s in syms])
    R = np.diff(np.log(mat), axis=0)
    corr = np.corrcoef(R, rowvar=False)
    vol = R.std(axis=0, ddof=1) * math.sqrt(min(ok[s].ppy for s in syms))
    perf = mat[-1] / mat[0] - 1
    text = "\n".join([
        f"Korrelation der Tagesrenditen, {dates[0]} bis {dates[-1]} ({len(R)} Tage)",
        table([[s] + [f"{corr[i, j]:.2f}" for j in range(len(syms))] for i, s in enumerate(syms)],
              [""] + syms),
        "",
        table([[s, pct(perf[i], 1), pct(vol[i], 1, False)] for i, s in enumerate(syms)],
              ["Symbol", "Rendite", "Vola p.a."]),
    ] + errs)
    emit(args, {"symbols": syms, "correlation": corr.tolist(), "volatility": vol.tolist(),
                "return": perf.tolist(), "errors": errs}, text)


def cmd_news(args):
    data = search(args.query, news=args.count, quotes=3)
    items = []
    for n in data.get("news", []):
        t = n.get("providerPublishTime")
        items.append({
            "time": datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if t else "",
            "publisher": n.get("publisher", ""), "title": n.get("title", ""), "link": n.get("link", ""),
            "tickers": n.get("relatedTickers", []),
        })
    lines = [f"News zu '{args.query}' (Yahoo Finance, {len(items)} Treffer):", ""]
    for it in items:
        lines.append(f"[{it['time']}] {it['title']}")
        lines.append(f"    {it['publisher']}" + (f" | {', '.join(it['tickers'])}" if it["tickers"] else "")
                     + f" | {it['link']}")
    if not items:
        lines.append("Keine News gefunden - WebSearch fuer breitere Suche nutzen.")
    emit(args, {"query": args.query, "news": items}, "\n".join(lines))


def cmd_search(args):
    data = search(args.query, news=0, quotes=args.count)
    rows = [{"symbol": q.get("symbol"), "name": q.get("longname") or q.get("shortname"),
             "type": q.get("quoteType"), "exchange": q.get("exchDisp") or q.get("exchange"),
             "sector": q.get("sector", ""), "industry": q.get("industry", "")}
            for q in data.get("quotes", []) if q.get("symbol")]
    text = table([[r["symbol"], (r["name"] or "")[:40], r["type"] or "", r["exchange"] or "", r["sector"]]
                  for r in rows], ["Symbol", "Name", "Typ", "Boerse", "Sektor"], [False] * 5) \
        if rows else "Keine Treffer."
    emit(args, {"query": args.query, "results": rows}, text)


# --- Watchlist --------------------------------------------------------------

def _wl_load() -> list:
    try:
        return json.loads(WATCHLIST.read_text()).get("symbols", [])
    except (OSError, ValueError):
        return []


def _wl_save(symbols: list):
    WATCHLIST.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST.write_text(json.dumps({"symbols": symbols}, indent=2) + "\n")


def cmd_watch(args):
    wl = _wl_load()
    if args.action == "add":
        new = [s.upper() for s in args.symbols if s.upper() not in wl]
        _wl_save(wl + new)
        print(f"Hinzugefuegt: {', '.join(new) or '-'}  ({len(wl) + len(new)} Werte, {WATCHLIST})")
        return
    if args.action == "remove":
        rm = {s.upper() for s in args.symbols}
        _wl_save([s for s in wl if s not in rm])
        print(f"Entfernt: {', '.join(sorted(rm & set(wl))) or '-'}")
        return
    if args.action == "list":
        emit(args, {"file": str(WATCHLIST), "symbols": wl},
             f"Watchlist ({WATCHLIST}):\n" + (", ".join(wl) if wl else "(leer)"))
        return
    syms = args.symbols or wl
    if not syms:
        print("Watchlist ist leer. Hinzufuegen mit: trade.py watch add AAPL MSFT ...")
        return
    ok, errs = _load_ok(fetch_many(syms, "1y", "1d"))
    snaps = {s: snapshot(ok[s]) for s in syms if s in ok}
    if args.action == "show":
        rows = [[sn["symbol"], sn["name"][:22], num(sn["price"]), pct(sn["day"]), pct(sn["w1"]),
                 pct(sn["m1"]), pct(sn["ytd"]), f"{sn['rsi14']:.0f}", pct(sn["vs_sma200"], 1),
                 pct(sn["from_high52"], 1), pct(sn["vol3m"], 0, False)]
                for sn in sorted(snaps.values(), key=lambda x: -x["day"] if math.isfinite(x["day"]) else 0)]
        text = table(rows, ["Symbol", "Name", "Kurs", "Heute", "1W", "1M", "YTD", "RSI", "vs SMA200",
                            "vom 52W-Hoch", "Vola 3M"], [False, False] + [True] * 9)
        emit(args, {"watchlist": list(snaps.values()), "errors": errs}, "\n".join([text] + errs))
    else:  # scan
        found = {s: signals(sn, ok[s]) for s, sn in snaps.items()}
        lines = ["Signal-Scan:"] + [f"  {s:<10} {'; '.join(sig)}" for s, sig in found.items() if sig]
        if len(lines) == 1:
            lines.append("  keine auffaelligen Signale")
        emit(args, {"signals": found, "errors": errs}, "\n".join(lines + errs))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trade.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--json", action="store_true", help="Ausgabe als JSON")
        sp.set_defaults(fn=fn)
        return sp

    sp = add("quote", cmd_quote, "Aktuelle Kurse + Performance")
    sp.add_argument("symbols", nargs="+")

    add("market", cmd_market, "Marktueberblick: Indizes, VIX, Zinsen, FX, Rohstoffe, Krypto")

    sp = add("history", cmd_history, "Historische Kurse (OHLCV)")
    sp.add_argument("symbol")
    sp.add_argument("--range", default="1y", help="1d 5d 1mo 3mo 6mo 1y 2y 5y 10y ytd max")
    sp.add_argument("--interval", default="1d", help="1m 5m 15m 1h 1d 1wk 1mo")
    sp.add_argument("--tail", type=int, default=15)
    sp.add_argument("--csv", help="als CSV speichern")

    sp = add("analyze", cmd_analyze, "Kennzahlen, Risiko, Technik, Signale")
    sp.add_argument("symbol")
    sp.add_argument("--range", default="2y")
    sp.add_argument("--benchmark", default="^GSPC")
    sp.add_argument("--rf", type=float, default=0.0, help="risikofreier Zins p.a. fuer Sharpe")
    sp.add_argument("--svg", help="Kurschart als SVG speichern")

    sp = add("simulate", cmd_simulate, "Monte-Carlo-Simulation (Einzelwert oder Portfolio)")
    sp.add_argument("symbols", nargs="+")
    sp.add_argument("--weights", type=float, nargs="+")
    sp.add_argument("--amount", type=float, default=10000, help="Startkapital (Portfolio)")
    sp.add_argument("--days", type=int, default=252, help="Horizont in Handelstagen (252 ~ 1 Jahr, bei reiner Krypto 365)")
    sp.add_argument("--paths", type=int, default=10000)
    sp.add_argument("--method", choices=("bootstrap", "gbm"), default="bootstrap")
    sp.add_argument("--block", type=int, default=10, help="Blocklaenge fuer Bootstrap")
    sp.add_argument("--range", default="5y", help="Historie fuer die Parameter")
    sp.add_argument("--drift", type=_parse_drift, default="zero",
                    help="zero (Standard) | hist | Jahresrendite z.B. 0.07")
    sp.add_argument("--target", type=float, help="Wahrscheinlichkeit fuer Endwert >= Ziel")
    sp.add_argument("--seed", type=int)
    sp.add_argument("--svg", help="Faecher-Chart als SVG speichern")
    sp.add_argument("--bands", action="store_true", help="mit --json: Perzentilbaender je Tag ausgeben")

    sp = add("backtest", cmd_backtest, "Strategien historisch testen")
    sp.add_argument("symbol")
    sp.add_argument("--strategy", choices=STRATEGIES + ("all",), default="all")
    sp.add_argument("--range", default="10y")
    sp.add_argument("--cost", type=float, default=0.001, help="Kosten pro Trade (0.001 = 0,1%%)")
    sp.add_argument("--fast", type=int, default=50)
    sp.add_argument("--slow", type=int, default=200)
    sp.add_argument("--rsi-period", type=int, default=14)
    sp.add_argument("--rsi-low", type=float, default=30)
    sp.add_argument("--rsi-high", type=float, default=55)
    sp.add_argument("--lookback", type=int, default=126)
    sp.add_argument("--entry", type=int, default=55)
    sp.add_argument("--exit", type=int, default=20)
    sp.add_argument("--svg", help="Equity-Kurven als SVG speichern")

    sp = add("correlate", cmd_correlate, "Korrelationsmatrix")
    sp.add_argument("symbols", nargs="+")
    sp.add_argument("--range", default="1y")

    sp = add("news", cmd_news, "Aktuelle Nachrichten zu Symbol oder Thema")
    sp.add_argument("query")
    sp.add_argument("--count", type=int, default=10)

    sp = add("search", cmd_search, "Ticker-Symbol suchen (Name -> Symbol)")
    sp.add_argument("query")
    sp.add_argument("--count", type=int, default=10)

    sp = add("watch", cmd_watch, "Watchlist: add | remove | list | show | scan")
    sp.add_argument("action", choices=("add", "remove", "list", "show", "scan"))
    sp.add_argument("symbols", nargs="*")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except DataError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
