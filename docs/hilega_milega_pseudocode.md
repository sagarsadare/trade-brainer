# Hilega Milega Strategy — Kite API Pseudocode

Target: Nifty 50 swing trading, daily signal timeframe, weekly trend confirmation (per §8 of the rules doc). Written against `pykiteconnect` (`kite.*`) method names so it maps directly onto real code.

---

## 0. Architecture

```
[Scheduler: runs once daily, after market close ~3:35pm or before open ~9:00am]
        │
        ▼
[1. Fetch data]  daily candles (signal TF) + weekly candles (trend TF)
        │
        ▼
[2. Compute HM indicator]  RSI(9), EMA(3) on RSI, WMA(21) on RSI  — on both TFs
        │
        ▼
[3. Check no-trade zone]  → if choppy, skip everything below
        │
        ▼
[4. Check weekly trend]   → gate: only allow long signals if weekly bullish, etc.
        │
        ▼
[5. Evaluate daily signal] → LONG / SHORT / HOLD (per rules §3)
        │
        ▼
[6. Risk & sizing check]  → SL distance, R:R ≥ 1:2, 2% capital cap
        │
        ▼
[7. Order execution]      → entry order + SL-M order (+ optional GTT target)
        │
        ▼
[8. State persistence]    → record signal/candle date so we don't re-fire same signal
```

---

## 1. Config

```python
CONFIG = {
    "instrument_token_daily": <NIFTY50_or_futures_token>,   # from kite.instruments()/search
    "tradingsymbol": "NIFTY24DECFUT",   # index itself isn't directly tradable; use fut/option per your setup
    "exchange": "NFO",                  # or NSE if trading via ETF/stock proxy
    "product": "NRML",                  # carry-forward for swing
    "rsi_length": 9,
    "rsi_mid": 50,
    "ema_on_rsi_length": 3,
    "wma_on_rsi_length": 21,
    "capital": <account_capital>,
    "max_risk_pct_per_trade": 0.02,     # from trading-rules memory
    "min_risk_reward": 2.0,             # from trading-rules memory
    "no_trade_zone_lookback": 10,       # bars to measure line separation over — TUNE via backtest
    "no_trade_zone_threshold": <TBD>,   # min avg separation to consider "trending" — TUNE via backtest
}
```

---

## 2. Data Fetch

```python
def fetch_candles(instrument_token, interval, lookback_days):
    to_date = today()
    from_date = today() - lookback_days
    candles = kite.historical_data(
        instrument_token=instrument_token,
        from_date=from_date,
        to_date=to_date,
        interval=interval,          # "day" for daily, use weekly resample (Kite has no native "week")
        continuous=False,
        oi=False,
    )
    return candles   # list of {date, open, high, low, close, volume}

def resample_to_weekly(daily_candles):
    # group daily_candles by ISO week, collapse each week to
    # {open: first, high: max, low: min, close: last, date: week_end_date}
    return weekly_candles

daily_candles  = fetch_candles(CONFIG.instrument_token_daily, "day", lookback_days=250)
weekly_candles = resample_to_weekly(daily_candles)
```

---

## 3. Indicator Calculation

```python
def compute_rsi(closes, length):
    # standard Wilder's RSI, just with length=9 instead of default 14
    return rsi_series   # list aligned with closes

def compute_ema(series, length):
    return ema_series

def compute_wma(series, length):
    # weighted moving average — weights increase linearly toward most recent bar
    return wma_series

def compute_hm_indicator(candles, rsi_length, ema_length, wma_length):
    closes = [c.close for c in candles]
    rsi_line = compute_rsi(closes, rsi_length)          # "black/red RSI line"
    green_line = compute_ema(rsi_line, ema_length)       # 3 EMA on RSI
    red_line = compute_wma(rsi_line, wma_length)         # 21 WMA on RSI ("volume/strength" proxy)
    return {
        "rsi": rsi_line,
        "green": green_line,
        "red": red_line,
    }

hm_daily  = compute_hm_indicator(daily_candles, **rsi/ema/wma lengths)
hm_weekly = compute_hm_indicator(weekly_candles, **rsi/ema/wma lengths)
```

---

## 4. No-Trade Zone Filter (§4 of rules doc)

```python
def is_no_trade_zone(hm, lookback, threshold):
    recent_rsi   = hm.rsi[-lookback:]
    recent_green = hm.green[-lookback:]
    recent_red   = hm.red[-lookback:]

    avg_separation = mean(
        abs(recent_rsi[i] - recent_green[i]) + abs(recent_rsi[i] - recent_red[i])
        for i in range(lookback)
    )
    return avg_separation < threshold   # threshold: TUNE via backtest on Nifty 50 daily data

no_trade = is_no_trade_zone(hm_daily, CONFIG.no_trade_zone_lookback, CONFIG.no_trade_zone_threshold)
if no_trade:
    log("No-trade zone — skipping today")
    exit_run()
```

---

## 5. Weekly Trend Gate (§6 multi-timeframe rule)

```python
def get_trend_bias(hm):
    latest_rsi = hm.rsi[-1]
    if latest_rsi > CONFIG.rsi_mid:
        return "BULLISH"
    elif latest_rsi < CONFIG.rsi_mid:
        return "BEARISH"
    return "NEUTRAL"

weekly_bias = get_trend_bias(hm_weekly)
# weekly_bias gates which daily signals are even allowed to fire (see §6 below)
```

---

## 6. Daily Signal Evaluation (§3 of rules doc)

```python
def detect_signal(hm, candles):
    rsi, green, red = hm.rsi, hm.green, hm.red
    i = len(rsi) - 1          # latest bar index
    prev = i - 1

    signal = "HOLD"

    # --- 3a. momentum exhaustion warning (informational only, not a trade trigger) ---
    green_crossed_rsi_down = green[prev] < rsi[prev] and green[i] >= rsi[i]
    green_crossed_rsi_up   = green[prev] > rsi[prev] and green[i] <= rsi[i]

    # --- 3b. preliminary bottom/top (red line re-entering the RSI's recent range) ---
    def in_recent_rsi_range(value, rsi_window):
        return min(rsi_window) <= value <= max(rsi_window)

    red_back_inside = in_recent_rsi_range(red[i], rsi[i-10:i]) and not in_recent_rsi_range(red[prev], rsi[prev-10:prev])

    # --- 3c. confirmed trigger ---
    rsi_crossed_above_50 = rsi[prev] <= CONFIG.rsi_mid and rsi[i] > CONFIG.rsi_mid
    rsi_crossed_below_50 = rsi[prev] >= CONFIG.rsi_mid and rsi[i] < CONFIG.rsi_mid
    red_above_50 = red[i] > CONFIG.rsi_mid
    red_below_50 = red[i] < CONFIG.rsi_mid

    candle = candles[i]
    prev_candle = candles[prev]
    candle_confirms_long  = candle.close > prev_candle.high
    candle_confirms_short = candle.close < prev_candle.low

    if rsi_crossed_above_50 and red_above_50 and candle_confirms_long:
        signal = "LONG"
    elif rsi_crossed_below_50 and red_below_50 and candle_confirms_short:
        signal = "SHORT"

    return signal

raw_signal = detect_signal(hm_daily, daily_candles)
```

```python
# --- apply weekly trend gate ---
if raw_signal == "LONG" and weekly_bias != "BULLISH":
    final_signal = "HOLD"   # don't fight the higher timeframe
elif raw_signal == "SHORT" and weekly_bias != "BEARISH":
    final_signal = "HOLD"
else:
    final_signal = raw_signal
```

---

## 7. Risk Management & Position Sizing

```python
def compute_sl(signal, candles):
    latest = candles[-1]
    if signal == "LONG":
        return min(latest.low, candles[-2].low)     # swing-low based SL
    elif signal == "SHORT":
        return max(latest.high, candles[-2].high)   # swing-high based SL

def compute_target(signal, entry_price, sl_price):
    risk = abs(entry_price - sl_price)
    reward = risk * CONFIG.min_risk_reward
    if signal == "LONG":
        return entry_price + reward
    elif signal == "SHORT":
        return entry_price - reward

def check_risk_reward_ok(entry_price, sl_price, target_price, signal):
    risk = abs(entry_price - sl_price)
    reward = abs(target_price - entry_price)
    return (reward / risk) >= CONFIG.min_risk_reward

def compute_quantity(entry_price, sl_price):
    max_risk_amount = CONFIG.capital * CONFIG.max_risk_pct_per_trade
    per_unit_risk = abs(entry_price - sl_price)
    qty = floor(max_risk_amount / per_unit_risk)
    # round down to nearest valid lot size for the instrument (e.g., Nifty lot size)
    return round_to_lot_size(qty, lot_size=<instrument_lot_size>)
```

```python
if final_signal in ("LONG", "SHORT"):
    entry_price = daily_candles[-1].close
    sl_price = compute_sl(final_signal, daily_candles)
    target_price = compute_target(final_signal, entry_price, sl_price)

    if not check_risk_reward_ok(entry_price, sl_price, target_price, final_signal):
        log("R:R below 1:2 — skipping trade")
        final_signal = "HOLD"
    else:
        qty = compute_quantity(entry_price, sl_price)
```

---

## 8. Order Execution

```python
def place_entry_order(signal, qty):
    return kite.place_order(
        variety="regular",
        exchange=CONFIG.exchange,
        tradingsymbol=CONFIG.tradingsymbol,
        transaction_type="BUY" if signal == "LONG" else "SELL",
        quantity=qty,
        order_type="MARKET",
        product=CONFIG.product,
        tag="HM_ENTRY",
    )

def place_stop_loss_order(signal, qty, sl_price):
    opposite = "SELL" if signal == "LONG" else "BUY"
    return kite.place_order(
        variety="regular",
        exchange=CONFIG.exchange,
        tradingsymbol=CONFIG.tradingsymbol,
        transaction_type=opposite,
        quantity=qty,
        order_type="SL-M",
        trigger_price=sl_price,
        product=CONFIG.product,
        tag="HM_SL",
    )

def place_target_gtt(signal, qty, entry_price, target_price):
    opposite = "SELL" if signal == "LONG" else "BUY"
    return kite.place_gtt_order(
        exchange=CONFIG.exchange,
        tradingsymbol=CONFIG.tradingsymbol,
        transaction_type=opposite,
        trigger_type="single",
        trigger_value=target_price,
        last_price=entry_price,
        quantity=qty,
        limit_price=target_price,
        product=CONFIG.product,
    )
```

```python
if final_signal in ("LONG", "SHORT") and not already_in_position(CONFIG.tradingsymbol):
    entry_order = place_entry_order(final_signal, qty)
    sl_order    = place_stop_loss_order(final_signal, qty, sl_price)
    target_gtt  = place_target_gtt(final_signal, qty, entry_price, target_price)
    save_trade_state(final_signal, entry_price, sl_price, target_price, entry_order, sl_order)
```

---

## 9. Trailing Exit — "Red Line Exits the Zone" (§5 of rules doc)

Since this exit condition isn't a fixed price, it can't be a single GTT — it needs to be re-evaluated each day the position is open:

```python
def check_trailing_exit(hm, open_position_signal):
    rsi, red = hm.rsi, hm.red
    i = len(rsi) - 1

    if open_position_signal == "LONG":
        # exit if red line, which was inside/above-50 zone, moves back below 50 decisively
        return red[i] < CONFIG.rsi_mid and red[i-1] >= CONFIG.rsi_mid
    elif open_position_signal == "SHORT":
        return red[i] > CONFIG.rsi_mid and red[i-1] <= CONFIG.rsi_mid
    return False

# run this daily for any open HM position, alongside the fixed SL-M order
if position_open and check_trailing_exit(hm_daily, position.signal):
    kite.place_order(
        variety="regular",
        exchange=CONFIG.exchange,
        tradingsymbol=CONFIG.tradingsymbol,
        transaction_type="SELL" if position.signal == "LONG" else "BUY",
        quantity=position.qty,
        order_type="MARKET",
        product=CONFIG.product,
        tag="HM_TRAIL_EXIT",
    )
    cancel_pending_gtt(position.target_gtt_id)   # avoid duplicate exit
```

---

## 10. State Persistence (avoid duplicate/re-fired signals)

```python
def already_in_position(tradingsymbol):
    positions = kite.positions()["net"]
    return any(p.tradingsymbol == tradingsymbol and p.quantity != 0 for p in positions)

def save_trade_state(signal, entry, sl, target, entry_order_id, sl_order_id):
    # persist to local DB/file: {date, signal, entry, sl, target, order_ids, status="OPEN"}
    ...
```

---

## 11. Daily Run Wrapper

```python
def run_daily():
    daily_candles  = fetch_candles(...)
    weekly_candles = resample_to_weekly(daily_candles)
    hm_daily  = compute_hm_indicator(daily_candles, ...)
    hm_weekly = compute_hm_indicator(weekly_candles, ...)

    if position_open_for(CONFIG.tradingsymbol):
        check_trailing_exit(hm_daily, position.signal)   # §9
        return

    if is_no_trade_zone(hm_daily, ...):                  # §4
        return

    weekly_bias = get_trend_bias(hm_weekly)              # §5
    raw_signal = detect_signal(hm_daily, daily_candles)   # §6
    final_signal = apply_trend_gate(raw_signal, weekly_bias)

    if final_signal == "HOLD":
        return

    # §7 risk checks, §8 order placement
    ...

# scheduler: run_daily() once per trading day, after close (candle fully formed)
```

---

## 12. Gaps to Fill Before Going Live

- **`no_trade_zone_threshold`**: must be backtested on Nifty 50 daily history — no fixed number is given in the source material.
- **RSI/EMA/WMA implementations**: use a library (e.g., `ta` or `pandas_ta`) rather than hand-rolling — just make sure RSI length is set to 9 and EMA/WMA are applied *on the RSI series*, not on price.
- **Lot size / instrument choice**: Nifty 50 index isn't directly tradable — decide upfront whether you're trading Nifty futures, Nifty ETF, or options, since `compute_quantity` and `tradingsymbol` depend on that choice.
- **Weekly resampling**: Kite's `historical_data` doesn't have a native weekly interval — resample from daily candles yourself (ISO week boundaries).
- **Backtest before forward-test before live** — per §9 of the rules doc, don't skip straight to live orders.
