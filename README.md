# Trading-Skill für Claude

Ein Claude-Code-Skill (auch als Plugin installierbar), mit dem Claude Aktien, ETFs, Indizes, Krypto,
Devisen und Rohstoffe beobachten, analysieren und simulieren kann. Die Daten kommen von Yahoo Finance,
ein API-Key ist nicht nötig.

| Fähigkeit | Beispiel |
|---|---|
| Live-Kurse & Performance | „Wie stehen Apple, SAP und Bitcoin?“ |
| Marktüberblick | „Was ist heute am Markt los?“ (Indizes, VIX, Zinsen, EUR/USD, Gold, Öl, BTC) |
| News | „Gibt es News zu Nvidia?“ |
| Analyse | „Analysiere Rheinmetall“: Rendite, Volatilität, Sharpe, Drawdown, Beta, RSI, MACD, SMAs, Trend, Signale |
| Monte-Carlo-Simulation | „Simuliere 10.000 € in AAPL/MSFT/SAP für 5 Jahre“: Perzentile, Verlustwahrscheinlichkeiten, VaR, Drawdowns, Fächer-Chart |
| Backtests | „Hätte sich ein SMA-50/200-Crossover beim S&P 500 gelohnt?“: Buy & Hold vs. SMA, RSI, Momentum, Breakout |
| Korrelation | „Wie gut diversifizieren Gold, Anleihen und Bitcoin mein Tech-Depot?“ |
| Watchlist | „Setz Tesla auf meine Watchlist“, „Scanne meine Watchlist nach Signalen“ |

Anschließend Claude einfach in normaler Sprache fragen. Der Skill wird automatisch geladen,
alternativ direkt mit `/trading`.

## Installation

**Als Plugin (empfohlen, funktioniert in jedem Projekt):**
```
/plugin marketplace add SpeedPeed/trading-skill
/plugin install trading@trading-skill
```
(Das Repo ist privat, daher muss Git Zugriff darauf haben, z. B. über `gh auth login` oder SSH.)

**Als persönlicher Skill:**
```bash
git clone https://github.com/SpeedPeed/trading-skill
cp -r trading-skill/skills/trading ~/.claude/skills/
```

**Nur in diesem Repo:** Claude Code im Repo-Ordner starten. Der Skill liegt unter `.claude/skills/trading`
(Symlink auf `skills/trading`).

**Claude Code im Web / in der Cloud:** Das Repo zur Sitzung hinzufügen. Dann ist der Skill über
`.claude/skills` verfügbar.

Voraussetzung: Python ≥ 3.9 und `numpy` (`pip install numpy`).

## Direkt im Terminal nutzen

```bash
T=skills/trading/scripts/trade.py
python3 $T market
python3 $T quote AAPL SAP.DE BTC-USD
python3 $T search "Rheinmetall"
python3 $T news NVDA
python3 $T analyze NVDA --range 2y --svg nvda.svg
python3 $T simulate NVDA --days 252 --paths 10000 --target 250 --svg sim.svg
python3 $T simulate AAPL MSFT SAP.DE --weights 0.5 0.3 0.2 --amount 10000 --days 1260 --drift 7%
python3 $T backtest SPY --range 10y --svg backtest.svg
python3 $T correlate AAPL MSFT GLD TLT BTC-USD
python3 $T watch add AAPL NVDA RHM.DE && python3 $T watch show && python3 $T watch scan
```

Jeder Befehl kennt `--json` und `-h`. Methoden und Formeln stehen in
[`skills/trading/reference/methods.md`](skills/trading/reference/methods.md).

### Konfiguration (Umgebungsvariablen)

| Variable | Standard | Zweck |
|---|---|---|
| `TRADING_WATCHLIST` | `~/.config/trading-skill/watchlist.json` | Speicherort der Watchlist |
| `TRADING_CACHE_TTL` | `300` | Cache-Dauer für API-Antworten in Sekunden (`0` = aus) |
| `TRADING_CACHE` | `~/.cache/trading-skill` | Cache-Verzeichnis |
| `TRADING_UA` | kurzer Mozilla-UA | User-Agent für Yahoo-Anfragen |

## Tests

```bash
python3 -m unittest discover -s tests -v
```
Die Tests laufen offline mit synthetischen Daten. Geprüft werden unter anderem: kein Look-ahead in den
Backtests, erwartungstreue Kalibrierung der Simulation und die Grenzwerte der Indikatoren.

## Grenzen

- Yahoo Finance ist eine inoffizielle Schnittstelle. Kurse sind teils ca. 15 Min. verzögert, und bei
  vielen Anfragen kommt `429 Too Many Requests`.
- Fundamentaldaten (KGV, Umsatz, Margen) liefert das Skript nicht. Claude recherchiert sie per Websuche.
- Simulationen und Backtests beruhen auf historischen Daten und Modellannahmen. Sie sind **keine Prognose
  und keine Anlageberatung**. Der Skill führt keine Orders aus.
