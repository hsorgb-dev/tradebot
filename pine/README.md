# MNQ 1 AM Range Breakout (TradingView, Pine Script v6)

`MNQ_1AM_Range_Breakout.pine` is a TradingView strategy for **MNQ1! on the 5-minute chart**.
All clock times are New York time. Daylight saving changes are handled automatically.

## Rules

| Step | Rule |
|---|---|
| Range | Highest high and lowest low of the 01:00, 01:05 and 01:10 bars (01:00–01:15 NY) |
| Long | First 5-min candle that **closes above** the range high → buy at the next bar's open |
| Short | First 5-min candle that **closes below** the range low → sell at the next bar's open |
| Stop | Opposite side of the range |
| Target | 0.5 R from the actual fill price (R = entry − stop) |

### Choices the spec left open (all are inputs)

- **Breakout candle:** "Close outside" by default. "Full body outside" requires open and close both beyond the range.
- **One trade per day.** With `Max trades per day` > 1, another trade needs a fresh breakout: price closes back inside the range, then outside again.
- **Last entry 12:00 NY, flatten at 15:55 NY.**
- **Costs:** $0.62 commission per contract per side, 1 tick slippage on market and stop orders. A target counts as filled only when price trades 1 tick through it.
- **Margin checks off** (`margin_long/short = 0`), so a small account isn't liquidated by TradingView's stock-style margin model.

A 0.5 R target with a 1 R stop needs a win rate above **66.7 % before costs** to break even. On narrow ranges, costs take a large share of each win.

## Setup

1. Pine Editor → paste the file → **Add to chart** on `MNQ1!`, 5-minute timeframe.
2. Strategy Tester → Overview / List of trades.
3. Recommended:
   - **Bar magnifier** (Properties, Premium plans): stop and target are often inside the same 5-min bar.
   - **Back-adjustment** for contract rolls in the chart's symbol settings: this keeps the daily EMA in Filter 1 clean.
4. History: TradingView loads only a limited number of 5-min bars, depending on your plan. Use **Deep Backtesting** if you have it, otherwise you only test a few months.

## The five regime filters

All filters are **off** by default. Each one is a separate input group with an `Enable` box and Min/Max limits.

| # | Regime | Value | Default pass rule | Idea being tested |
|---|---|---|---|---|
| 1 | **Trend direction** | Previous daily close vs daily EMA(20) | Longs only above, shorts only below | Breakouts in the direction of the higher-timeframe trend follow through more often |
| 2 | **Volatility (VIX)** | Previous daily close of `CBOE:VIX` | 0 – 20 | Calm markets give cleaner breakouts. Switch to 20 – 100 to test the opposite. `CBOE:VXN` is the Nasdaq-100 version |
| 3 | **Range width** | Today's 1 AM range ÷ average of the last 10 days' 1 AM ranges | 0.5 – 1.5 | Skips very tight ranges (costs and noise) and very wide ranges (stop too far away) |
| 4 | **Trend strength (ADX)** | ADX(14) of the last closed 60-min bar, at the moment of the breakout | 20 – 100 | Trending markets follow through; choppy markets (try 0 – 20) whip back to the stop |
| 5 | **Overnight participation** | Volume 18:00 → 01:15 ÷ average of the last 10 days | 1.0 – 100 | Above-normal overnight activity means real interest behind the move |

No filter repaints. Higher-timeframe values only use closed bars. VIX, range width and overnight volume are fixed for the day when the range completes at 01:15.

### Regime filter table (bottom right of the chart)

For every filter, the table splits the **trades of the current run** into those the filter would have allowed (*Pass*) and those it would have blocked (*Fail*). Each side shows trade count, win rate and average R after costs. The *Today* column shows today's state and value.

The split is only a fair comparison in a run with **every filter off**. Gray ✕ markers show breakouts that an enabled filter blocked.

## How to test which filter works best

1. **Baseline:** run with all filters off. Note net profit, profit factor, win rate, max drawdown and the number of trades.
2. **Shortlist:** in the table, look for filters where *Pass* avg R is clearly above *Fail* avg R, with at least about 30 trades on each side.
3. **Confirm:** enable one filter at a time and compare its Strategy Tester results with the baseline. A blocked first breakout can be followed by a later one, so these numbers can differ slightly from the table.
4. **Robustness:** move each threshold a little (for example ADX 15 / 20 / 25). An edge that only exists at one exact value is noise.
5. **Out of sample:** use `Backtest window` to pick the filter and thresholds on the first ~70 % of the history, then check them on the last ~30 % without changing anything.
6. Combine the best two filters only after each one holds up on its own.

## Automation (webhooks)

Every order carries a JSON `alert_message`:

```json
{"symbol":"MNQ1!","action":"buy","qty":1,"price":25010.25,"stop":24990.00,"target":25020.25}
{"symbol":"MNQ1!","action":"exit","reason":"tp_or_sl"}
{"symbol":"MNQ1!","action":"exit","reason":"eod"}
```

1. Create an alert on the strategy → *Order fills only* → message `{{strategy.order.alert_message}}`.
2. Put your bridge's webhook URL in the alert. Adapt the field names to what your bridge expects (TradersPost, PickMyTrade, etc.).
3. `Symbol in alert messages` overrides `MNQ1!`, for example with the front-month contract your broker needs.

There are two ways to run it:

- **TradingView manages the exits:** each exit fill sends `exit`.
- **The broker holds a bracket:** send stop and target with the entry, and forward only the `eod` exit.

In the second mode the target in the entry message is based on the signal candle's close, not the fill, so it can differ from the backtest by a tick or two.

Run it on a paper or sim account first.
