---
name: trading
description: Aktien, ETFs, Indizes, Krypto, Devisen und Rohstoffe beobachten und analysieren - Live-Kurse, Marktueberblick, aktuelle News, technische Analyse und Risiko-Kennzahlen, Monte-Carlo-Simulationen von Kursen und Portfolios, Strategie-Backtests, Korrelationen und eine Watchlist mit Signal-Scan. Verwenden, wenn der Nutzer nach Kursen, Aktien, Boerse, Trading, Investments, Portfolio, "Wie steht X?", "Simuliere ...", "Backtest ...", Markt-News oder einer Watchlist fragt.
---

# Trading-Skill

Werkzeugkasten fuer Marktdaten, Analyse und Simulation. Alles laeuft ueber ein CLI-Skript:

```bash
python3 <skill-dir>/scripts/trade.py <befehl> [optionen]
```

`<skill-dir>` ist das Basisverzeichnis dieses Skills (wird beim Laden angezeigt). Voraussetzung:
Python 3.9+ mit `numpy`. Fehlt numpy: `pip install numpy`.
Jeder Befehl kennt `--json` fuer maschinenlesbare Ausgabe und `-h` fuer alle Optionen.

## Befehle

| Ziel | Befehl |
|---|---|
| Kurse + Performance (Heute/1M/YTD/1J, 52W-Spanne) | `quote AAPL SAP.DE BTC-USD` |
| Marktueberblick (Indizes, VIX, Zinsen, EUR/USD, Gold, Oel, Bitcoin) | `market` |
| Ticker finden ("Rheinmetall" -> `RHM.DE`) | `search "Rheinmetall"` |
| Aktuelle News zu Symbol oder Thema | `news NVDA` / `news "EZB Zinsentscheid"` |
| Analyse: Rendite, Vola, Sharpe, Drawdown, Beta, RSI, MACD, SMAs, Trend, Signale | `analyze NVDA --range 2y [--svg chart.svg]` |
| Monte-Carlo fuer Einzelwert | `simulate NVDA --days 252 --paths 10000 [--target 250] [--svg sim.svg]` |
| Monte-Carlo fuer Portfolio (korreliert) | `simulate AAPL MSFT SAP.DE --weights 0.5 0.3 0.2 --amount 10000` |
| Strategie-Backtest (Buy&Hold vs. SMA, RSI, Momentum, Breakout) | `backtest SPY --range 10y [--strategy sma --fast 20 --slow 100] [--svg bt.svg]` |
| Korrelationsmatrix (Diversifikation) | `correlate AAPL MSFT GLD TLT BTC-USD --range 1y` |
| Historische Kurse / CSV-Export | `history AAPL --range 5y --interval 1wk --csv aapl.csv` |
| Watchlist | `watch add AAPL NVDA` / `watch show` / `watch scan` / `watch remove NVDA` / `watch list` |

### Symbol-Konventionen (Yahoo Finance)
- US: `AAPL`, `MSFT`, ETFs `SPY`, `QQQ`
- Xetra: `.DE` (`SAP.DE`, `RHM.DE`), Frankfurt `.F`, London `.L`, Paris `.PA`, Amsterdam `.AS`, Schweiz `.SW`, Tokio `.T`
- Indizes: `^GSPC` (S&P 500), `^NDX`, `^GDAXI` (DAX), `^STOXX50E`, `^VIX`
- Krypto `BTC-USD`, Devisen `EURUSD=X`, Futures `GC=F` (Gold), `CL=F` (Oel)
- Unsicher? Immer zuerst `search "<Firmenname>"`.

### Wichtige Optionen
- `--range`: `1mo 3mo 6mo 1y 2y 5y 10y ytd max`; `--interval`: `1m 5m 15m 1h 1d 1wk 1mo` (Intraday nur fuer kurze Ranges)
- `simulate --method bootstrap` (Standard: zieht echte historische 10-Tage-Bloecke -> fette Raender und
  Volatilitaets-Cluster bleiben erhalten) oder `--method gbm` (Normalverteilung, korreliert)
- `simulate --drift zero` (Standard: erwartete Rendite 0, reine Risiko-Betrachtung) | `hist` (historischer
  Trend wird fortgeschrieben - meist zu optimistisch nach starken Phasen) | `0.07` bzw. `7%` (eigene Annahme)
- `backtest --cost 0.001` = 0,1 % Kosten pro Positionswechsel; Signale am Schlusskurs, nur Long, kein Look-ahead
- `TRADING_WATCHLIST=/pfad/watchlist.json` legt fest, wo die Watchlist gespeichert wird
  (Standard `~/.config/trading-skill/watchlist.json`). In fluechtigen Cloud-Umgebungen eine Datei im Repo nutzen
  und committen, sonst ist die Liste nach der Sitzung weg.

## Vorgehen je nach Anfrage

**"Wie steht X?" / Kurs-Check** -> `quote X`. Bei Bewegung > 2-3 % zusaetzlich `news X` und kurz den
wahrscheinlichen Grund nennen.

**"Was ist am Markt los?"** -> `market`, dann WebSearch nach den wichtigsten Markt-News des Tages
(z.B. "stock market today", "DAX heute"). Kurz zusammenfassen: Richtung, Treiber, Termine.

**"Analysiere X" / "Soll ich X kaufen?"** ->
1. `analyze X --range 2y` (Technik, Risiko, Trend)
2. `news X` + WebSearch fuer Fundamentaldaten (KGV, Umsatz-/Gewinnwachstum, Marge, Verschuldung,
   naechster Quartalstermin, Analysten-Konsens) - das Skript liefert keine Fundamentaldaten.
3. Optional `simulate X` fuer die Bandbreite moeglicher Ergebnisse.
4. Ergebnis als ausgewogene Einordnung: Chancen, Risiken, was der Markt bereits einpreist, was die These
   widerlegen wuerde. **Keine Kauf-/Verkaufsempfehlung aussprechen**; der Nutzer entscheidet.

**"Simuliere X" / "Was kann aus 10.000 EUR werden?"** -> `simulate` mit passendem Horizont
(`--days`: 21 = 1 Monat, 63 = 1 Quartal, 252 = 1 Jahr, 1260 = 5 Jahre). Perzentile, Verlust-
wahrscheinlichkeiten und Drawdown erklaeren. Bei `--drift hist` deutlich sagen, dass die Vergangenheit
fortgeschrieben wird. Fuer Vergleich gern zwei Laeufe (z.B. `zero` und `7%`).

**"Teste Strategie Y"** -> `backtest`. Immer gegen Buy & Hold vergleichen, Drawdown und Kosten betonen.
Wenn Parameter optimiert werden: auf einem anderen Zeitraum oder anderen Wert gegenpruefen (Overfitting).

**Watchlist** -> `watch show` fuer die Tabelle, `watch scan` fuer Signale (RSI ueberkauft/ueberverkauft,
Golden/Death Cross, SMA200-Durchbruch, 52W-Hoch/Tief, ungewoehnlich grosse Tagesbewegung, Volumen-Spike).
Fuer regelmaessige Beobachtung kann ein wiederkehrender Task/Loop `watch scan` ausfuehren.

**Charts** -> `--svg datei.svg` bei `analyze`, `simulate`, `backtest`. Die SVG-Datei dem Nutzer zeigen
(z.B. als Datei senden oder in eine HTML-Seite einbetten).

## Interpretation (Kurzreferenz)
Details und Formeln: [reference/methods.md](reference/methods.md).
- **Volatilitaet p.a.**: typische Schwankung pro Jahr (1 Sigma). 20 % = breiter Index, 40-60 % = Einzel-Tech/Krypto.
- **Sharpe**: Rendite pro Risiko-Einheit; > 1 gut, > 2 aussergewoehnlich (oft nicht nachhaltig).
- **Max. Drawdown**: groesster Verlust vom Hoch zum Tief - die Zahl, die Anleger wirklich aushalten muessen.
- **VaR 95 %**: in 95 % der Faelle kein groesserer Verlust; **CVaR**: Durchschnittsverlust in den schlimmsten 5 %.
- **Beta**: 1,5 = bewegt sich im Schnitt 1,5x so stark wie der Benchmark.
- **RSI**: < 30 ueberverkauft, > 70 ueberkauft - in starken Trends kann das lange so bleiben.
- **SMA200**: Kurs darueber = langfristiger Aufwaertstrend.

## Grenzen - immer ehrlich kommunizieren
- Daten von Yahoo Finance (inoffizielle Schnittstelle), Kurse teils ~15 Min. verzoegert, gelegentlich luecken-
  oder fehlerhaft. Bei wichtigen Zahlen mit einer zweiten Quelle (WebSearch) gegenpruefen.
- Simulationen zeigen eine Bandbreite unter Modellannahmen, keine Prognose. Extreme Ereignisse
  (Crash, Pleite, Uebernahme) koennen ausserhalb der Historie liegen.
- Backtests sind optimistisch (keine Slippage, keine Steuern, Survivorship Bias).
- Keine Anlageberatung. Keine Orders ausfuehren, keine Zugangsdaten zu Brokern erfragen.
- Bei Fehler `429 Too Many Requests`: kurz warten; Antworten werden 5 Min. gecacht
  (`TRADING_CACHE_TTL` in Sekunden, `0` = aus).
