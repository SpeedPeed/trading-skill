# Methoden & Formeln

Alle Berechnungen nutzen dividenden- und split-bereinigte Schlusskurse (`adjclose`), sofern Yahoo sie liefert.
`r_t = ln(P_t / P_{t-1})` sind Log-Renditen, ein Jahr = 252 Handelstage.

## Kennzahlen (`analyze`)

| Kennzahl | Formel / Definition |
|---|---|
| Gesamtrendite | `P_end / P_start - 1` |
| CAGR | `(P_end / P_start)^(1/Jahre) - 1` |
| Volatilitaet p.a. | `std(r) * sqrt(252)` |
| Sharpe | `(mean(r) * 252 - ln(1 + rf)) / Vola` |
| Max. Drawdown | `min(P_t / max_{s<=t} P_s - 1)` |
| VaR 95 % (1 Tag) | 5 %-Quantil der einfachen Tagesrenditen (historisch) |
| Schiefe / Exzess-Kurtosis | 3. / 4. standardisiertes Moment (Normalverteilung: 0 / 0) |
| Beta | `cov(r, r_bench) / var(r_bench)` auf gemeinsamen Handelstagen |
| RSI(14) | Wilder-Glaettung der Auf-/Abwaertsbewegungen, `100 - 100 / (1 + RS)` |
| MACD | `EMA12 - EMA26`, Signal = `EMA9(MACD)` |

## Monte-Carlo (`simulate`)

Buy-and-Hold-Portfolio mit Startgewichten `w` (kein Rebalancing):
`V_T = V_0 * sum_i w_i * exp(sum_t r_{i,t})`.

**Bootstrap (Standard).** Es werden zufaellige zusammenhaengende Bloecke (Standard 10 Tage) aus der
gemeinsamen Historie aller Werte gezogen. Dadurch bleiben erhalten:
fette Raender, Schiefe, kurzfristige Volatilitaets-Cluster und die Korrelation zwischen den Werten
(alle Werte nutzen dieselben Tage).

**GBM.** Taegliche Log-Renditen ~ multivariat normal mit historischer Kovarianz (Cholesky-Zerlegung).
Unterschaetzt Extremereignisse, ist dafuer glatt und gut interpretierbar.

**Drift.** Die historische Durchschnittsrendite ist statistisch sehr unsicher (Standardfehler bei 30 % Vola
und 5 Jahren Historie: ca. +-13 %-Punkte p.a.). Deshalb ist der Standard `--drift zero`:
die Renditen werden so verschoben, dass der **Erwartungswert** des Endvermoegens dem Startwert entspricht.
Mit `--drift 0.07` entspricht er `(1.07)^Jahre`. Der Median liegt wegen der Volatilitaet darunter
(Volatility Drag ~ `-sigma^2/2` pro Jahr). Beim Bootstrap wird exakt auf Block-Ebene kalibriert:
`mu = ln(1+a)/252 - ln(mean(exp(Blocksumme)))/Blocklaenge`.

**Ausgaben.** Perzentile des Endwerts, Wahrscheinlichkeiten fuer Gewinn/Verlust-Schwellen,
VaR/CVaR am Horizont, Verteilung des maximalen Drawdowns entlang der Pfade, optional Faecher-Chart.

## Backtest (`backtest`)

Position `pos_t` in {0, 1} wird am Schlusskurs von Tag t aus Daten bis einschliesslich t bestimmt und
von t nach t+1 gehalten: `R_t = pos_t * (P_{t+1}/P_t - 1) - Kosten * |pos_t - pos_{t-1}|`.
Alle Strategien starten nach derselben Aufwaermphase, damit der Vergleich mit Buy & Hold fair ist.

| Strategie | Regel (Standardparameter) |
|---|---|
| `buyhold` | immer investiert |
| `sma` | investiert, wenn SMA50 > SMA200 |
| `rsi` | Einstieg bei RSI(14) < 30, Ausstieg bei RSI > 55 (Mean Reversion) |
| `momentum` | investiert, wenn 126-Tage-Rendite > 0 (Time-Series-Momentum) |
| `breakout` | Einstieg bei neuem 55-Tage-Hoch, Ausstieg bei 20-Tage-Tief (Donchian/Turtle) |

Nicht modelliert: Slippage, Steuern, Zinsen auf Cash, Leerverkaeufe, Hebel, Ausfuehrung zum
Eroeffnungskurs des Folgetags. Ergebnisse sind daher eher optimistisch.

## Signal-Scan (`watch scan`)

- RSI(14) < 30 bzw. > 70
- Golden/Death Cross: SMA50 kreuzt SMA200 innerhalb der letzten 5 Tage
- Schlusskurs kreuzt SMA200
- Kurs innerhalb von 2 % am 52-Wochen-Hoch/-Tief
- Tagesbewegung > 2,5 Sigma (Sigma aus 3-Monats-Volatilitaet)
- Volumen > 2x 20-Tage-Durchschnitt
