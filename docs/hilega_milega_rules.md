# Hilega Milega (HM) Indicator Strategy — Implementation Rules

Source: Chirag Rathod's "Hilega Milega" indicator, as explained across the advance-strategy video, the Upsurge swing-trade interview, and the Algo Sid automation session. Rules below are normalized into a form you can code directly.

---

## 1. Indicator Construction

Build on top of RSI (not the standard RSI usage):

| Component | Setting |
|---|---|
| Base | RSI, length **9** (not 14) |
| Overbought / Oversold levels | Both set to **50** (acts as the bull/bear midline, not extremes) |
| RSI line color | Red above center logic / Blue below — purely visual, keep as one "RSI line" internally |
| Line 2 — "Green line" | **3-period EMA of the RSI** (dark green) — represents price/trend of momentum |
| Line 3 — "Red line" | **21-period WMA of the RSI** (red) — the weight in a WMA proxies **volume**, so this is the "strength/volume" line |

So the indicator is: `RSI(9)` + `EMA(3) on RSI` + `WMA(21) on RSI`. Three lines, all plotted in RSI-space (0–100), midline at 50.

---

## 2. Core Definitions

- **Bull zone**: RSI(9) > 50
- **Bear zone**: RSI(9) < 50
- **"Strength band"**: the zone traced by the RSI line's recent range — the red (WMA) line being *inside* it vs *outside* it is the key signal, not just its raw value.
- **Line separation / distance**: the visual gap between RSI, green EMA, and red WMA. Larger separation = stronger momentum in whatever direction they're all sloping. Convergence = momentum fading.
- **Leading, not lagging**: signals are meant to show a move *before* it happens — treat crossovers as early warnings, not confirmations, until the 50-cross rule below fires.

---

## 3. Signal Sequence (applies symmetrically to tops and bottoms)

### 3a. Momentum exhaustion warning (first sign of reversal)
- In an uptrend: **green EMA line touches/crosses down onto the black RSI line** → upward momentum is fading, a top may be forming. Do not enter fresh longs here; if short, don't cover yet — wait for confirmation.
- In a downtrend: green EMA line touches/crosses up onto the RSI line → downward momentum fading, possible bottom forming.

### 3b. Preliminary top/bottom (red line reacts)
- **Bottom forming**: red (WMA) line, which was outside/diverging from the RSI while price fell, starts moving back **inside** the RSI's zone → price is very likely to stop falling here. This low will likely **not be broken (on a closing basis)** as long as the red line stays inside/below the strength zone.
- **Top forming**: red line, which was diverging above during the rally, starts moving back inside the zone while RSI is still above 50 → price likely to stall/retrace; recent high may not be broken until red line properly exits again.
- Caveat: this is *not yet the confirmed ultimate top/bottom* — treat as "base is forming," not "reverse now."

### 3c. Confirmed reversal / entry trigger
- **Long entry**: RSI(9) crosses and closes **above 50**, AND the red WMA line also moves above 50 (into the bull zone) → momentum genuinely starting, expect fast continuation (often gap-ups). This is the buy trigger.
- **Short entry**: RSI(9) crosses and closes **decisively below 50**, AND the red WMA line also drops below 50 on the same/next bar → best point to go short; the move down from here tends to be sharp.
- **Extra confirmation filter (recommended for coding)**: only fire the signal if the triggering candle **closes above the previous candle's high** (for longs) or **below the previous candle's low** (for shorts). This is explicitly called out as giving a much higher-probability signal — and it satisfies your existing rule to only trade setups confirmed by candlestick reversal patterns (see [[trading-rules]]).

### 3d. "New high/low will be breached" advanced pattern
- If RSI does **not** dip below 50 during a pullback, and the red line, having gone outside, comes back inside the strength zone → the **previous high will be breached** (bullish continuation). Mirror logic applies to previous lows in a downtrend.
- If red line stays outside (diverging) without RSI crossing 50 the other way, expect **new lows/highs to keep forming** (trend continuation, not reversal) — this is the "no bottom yet" trap case to avoid shorting/longing prematurely.

---

## 4. No-Trade Zone Rule
If RSI oscillates around 50 with the three lines bunched close together (no separation, choppy up/down) → **do not trade**. This is explicitly flagged as the setup that causes repeated stop-losses. Code this as: skip signal generation if the average distance between the 3 lines over N bars is below a threshold (needs backtesting to set the threshold value).

---

## 5. Stop-Loss & Exit Rules
- **Stop-loss placement**: 
  - Long: below the low of the signal/entry candle (or the most recent swing low).
  - Short: above the high of the signal/entry candle (or the most recent swing high).
- **Trailing exit / target**: exit (or trail stop to) the point where the **red line exits back outside the RSI zone** in the opposite direction — i.e., momentum has reversed. This is the primary exit signal, used instead of (or alongside) a fixed price target.
- **Alternative fixed target**: Fibonacci retracement levels (50%/61.8%) off the preceding swing, or the opposite Bollinger Band (20 SMA) if that filter is used (see §7).
- **Retracement rule**: whenever the red line approaches 50 (from either side) during a trending move, expect a **minor retracement**, not a reversal — this is a valid spot to add to a position, not a reason to exit.
- Applies to your standing rule of minimum 1:2 risk/reward — check SL distance vs. the trailing-exit/target distance before taking the signal (see [[trading-rules]]).

---

## 6. Multi-Timeframe Rule (mandatory, from the advance-strategy video)
1. Identify the trend on a **higher timeframe** first, then only take entries in that direction on the lower timeframe:
   - Trading 5 min → confirm trend on 15 min.
   - Trading 15 min → confirm trend on 1 hour.
   - Trading 1 hour → confirm trend on 1 day.
2. If the higher timeframe is bullish (HM above 50), only take long signals on the lower timeframe — ignore lower-timeframe sell signals until the higher timeframe flips.
3. If the lower-timeframe trend contradicts the higher timeframe, **wait** — don't take the counter-trend entry.
4. For your swing setup on Nifty 50 specifically: use **daily** for the entry timeframe and **weekly** to confirm the higher-timeframe trend (per §8 below).

---

## 7. Confluence Filter (optional, improves accuracy per source's own claim of ~70–75% base accuracy)
Pick **one** secondary filter and require agreement before firing a trade — don't stack multiple indicators (explicitly warned against, causes "khichdi"/conflicting signals):
- **Bollinger Bands (20 SMA)**: only take longs when price is above the 20 SMA and RSI(9) > 50; stop-loss = low of entry candle; target = upper band.
- **EMA-based trend indicator (48 EMA custom, "TrendCraft")**: on a higher timeframe (e.g., 15 min for intraday, weekly for swing) confirm green/bullish state before taking HM longs on the lower timeframe.

---

## 8. Timeframe → Use-Case Mapping

| Use case | Signal timeframe | Trend-confirmation timeframe |
|---|---|---|
| Intraday | 1 hour (minimum recommended); if lower, don't go below 15 min | 1 day |
| Swing trading | 15 min or Daily | 1 hour / Weekly |
| Positional / Investment | Weekly | Monthly |

- Avoid the first 15 minutes of the session for intraday entries (start ~10:15 AM) — volatility settles and direction becomes clearer.
- Investment rule: on the **weekly** chart, if HM sustains above 30 or tests above 40 and reverses, treat as an entry zone for accumulation. If a stock/index dropped to ~20 due to fundamental issues (not just technical), verify fundamentals before entering — this rule doesn't override a genuine fundamental breakdown.

---

## 9. Hard Constraints for Coding (from the automation walkthrough)
- Every entry/exit must be a **fixed, mechanical rule** — no discretionary "wait for pullback if it feels right" logic. Anything you can't state as an if/then condition should be left out of the v1 algo, then added later as its own codified rule once you've observed the pattern enough times to define it precisely.
- Backtest before forward-testing with paper money; only go live after a forward-test period.
- Expect this setup's raw accuracy to sit around 70–75% — the source's stated edge comes from *not overtrading* and staying in one setup long enough to know its false-signal patterns, not from a higher hit rate.

---

## 10. Cross-check Against Your Standing Rules
Before any signal from this strategy is allowed to fire in your system, run it through [[trading-rules]]:
- Risk/reward ≥ 1:2 (compare entry→SL distance vs. entry→trail-exit/target distance)
- Risk per trade ≤ 2% of capital
- Candlestick confirmation present (§3c close-above-previous-high/low filter satisfies this)
