"""Hilega Milega signal engine.

Every rule below maps to a numbered section of docs/hilega_milega_rules.md, and
each is a fixed mechanical condition -- S.9 of that document forbids any
discretionary "wait for a pullback if it feels right" logic in v1.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import floor
from typing import Any, Sequence

from .indicators import HM

RSI_MID = 50.0

# S.4 -- no fixed number exists in the source material; it must be calibrated
# per instrument and timeframe. hilega/calibrate.py derives it from history,
# and this is only the fallback when no calibration has been stored.
DEFAULT_NO_TRADE_LOOKBACK = 10
DEFAULT_NO_TRADE_THRESHOLD = 6.0
# S.4 says the dead zone is the RSI oscillating *around 50* with the lines
# bunched. Both halves matter: a strong trend saturates the RSI near 0/100,
# which also bunches the lines, and that is the opposite of a no-trade tape.
DEFAULT_NO_TRADE_MID_BAND = 8.0

DEFAULT_MIN_RR = 2.0            # S.10 standing rule
DEFAULT_RISK_PCT = 0.02         # S.10 standing rule


@dataclass
class Candle:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass
class Gate:
    name: str
    passed: bool
    detail: str


@dataclass
class Signal:
    signal: str                      # 'LONG' | 'SHORT' | 'HOLD'
    raw_signal: str                  # before the higher-timeframe gate
    bar_date: str
    close: float
    rsi: float
    green: float
    red: float
    separation: float
    trend_bias: str                  # bias from the higher timeframe
    gates: list[Gate] = field(default_factory=list)
    states: dict[str, Any] = field(default_factory=dict)
    entry: float | None = None
    stop_loss: float | None = None
    target: float | None = None
    risk_reward: float | None = None
    quantity: int | None = None
    risk_amount: float | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gates"] = [asdict(g) for g in self.gates]
        return d


def trend_bias(hm: HM, i: int | None = None) -> str:
    """S.5 -- higher-timeframe bias is simply which side of 50 the RSI sits on."""
    i = i if i is not None else (hm.last_ready_index() or -1)
    if i < 0 or hm.rsi[i] is None:
        return "NEUTRAL"
    if hm.rsi[i] > RSI_MID:
        return "BULLISH"
    if hm.rsi[i] < RSI_MID:
        return "BEARISH"
    return "NEUTRAL"


def _in_range(value: float, window: Sequence[float]) -> bool:
    vals = [v for v in window if v is not None]
    return bool(vals) and min(vals) <= value <= max(vals)


def describe_states(hm: HM, i: int) -> dict[str, Any]:
    """S.3a / S.3b / S.3d -- context flags. Informational, never trade triggers."""
    prev = i - 1
    states: dict[str, Any] = {}

    # S.3a momentum exhaustion. EMA(3) lags the RSI, so it trails BELOW a rising
    # RSI; the two swapping sides means the RSI has rolled over. The source
    # names this "green crosses down onto the RSI" -- the cross that warns of a
    # downward turn, not a downward cross of the green line itself.
    g0, g1 = hm.green[prev], hm.green[i]
    r0, r1 = hm.rsi[prev], hm.rsi[i]
    states["bull_exhaustion"] = bool(g0 is not None and g0 <= r0 and g1 > r1)
    states["bear_exhaustion"] = bool(g0 is not None and g0 >= r0 and g1 < r1)

    # S.3b preliminary top/bottom: the red WMA re-entering the RSI's recent range.
    lo = max(0, i - 10)
    red_now_inside = _in_range(hm.red[i], hm.rsi[lo:i])
    red_was_inside = _in_range(hm.red[prev], hm.rsi[max(0, prev - 10):prev])
    states["red_re_entered_zone"] = bool(red_now_inside and not red_was_inside)
    states["red_inside_zone"] = bool(red_now_inside)

    # S.3d continuation: RSI held its side of 50 while the red line came back in.
    held_side = all(v is not None and v > RSI_MID for v in hm.rsi[lo:i + 1]) or \
                all(v is not None and v < RSI_MID for v in hm.rsi[lo:i + 1])
    states["continuation_setup"] = bool(held_side and states["red_re_entered_zone"])

    states["rsi_zone"] = "bull" if hm.rsi[i] > RSI_MID else "bear"
    states["red_above_mid"] = bool(hm.red[i] > RSI_MID)
    states["green_above_rsi"] = bool(hm.green[i] > hm.rsi[i])
    return states


def detect(candles: Sequence[Candle], hm: HM, i: int) -> tuple[str, list[Gate]]:
    """S.3c -- the only conditions that actually fire a trade."""
    prev = i - 1
    rsi_up = hm.rsi[prev] <= RSI_MID < hm.rsi[i]
    rsi_down = hm.rsi[prev] >= RSI_MID > hm.rsi[i]
    red_above = hm.red[i] > RSI_MID
    red_below = hm.red[i] < RSI_MID

    c, p = candles[i], candles[prev]
    closes_above_prev_high = c.close > p.high
    closes_below_prev_low = c.close < p.low

    gates: list[Gate] = []
    if rsi_up or rsi_down:
        side = "LONG" if rsi_up else "SHORT"
        gates.append(Gate("rsi_cross_50", True,
                          f"RSI(9) crossed {'above' if rsi_up else 'below'} 50 "
                          f"({hm.rsi[prev]:.1f} -> {hm.rsi[i]:.1f})."))
        red_ok = red_above if rsi_up else red_below
        gates.append(Gate("red_confirms", red_ok,
                          f"Red WMA(21) at {hm.red[i]:.1f} is "
                          f"{'above' if hm.red[i] > RSI_MID else 'below'} 50; a {side} "
                          f"needs it {'above' if side == 'LONG' else 'below'}."))
        candle_ok = closes_above_prev_high if rsi_up else closes_below_prev_low
        gates.append(Gate("candle_confirms", candle_ok,
                          f"Close {c.close:.2f} vs previous "
                          f"{'high ' + format(p.high, '.2f') if side == 'LONG' else 'low ' + format(p.low, '.2f')}"
                          f" -- S.3c candlestick confirmation."))
        if all(g.passed for g in gates):
            return side, gates
        return "HOLD", gates

    gates.append(Gate("rsi_cross_50", False,
                      f"RSI(9) did not cross 50 on this bar (now {hm.rsi[i]:.1f})."))
    return "HOLD", gates


def evaluate(candles: Sequence[Candle], higher_candles: Sequence[Candle],
             rsi_length: int = 9, ema_length: int = 3, wma_length: int = 21,
             no_trade_lookback: int = DEFAULT_NO_TRADE_LOOKBACK,
             no_trade_threshold: float = DEFAULT_NO_TRADE_THRESHOLD,
             no_trade_mid_band: float = DEFAULT_NO_TRADE_MID_BAND,
             capital: float = 0.0, risk_pct: float = DEFAULT_RISK_PCT,
             min_rr: float = DEFAULT_MIN_RR, lot_size: int = 1,
             at: int | None = None) -> Signal:
    """Full rule stack: signal, no-trade zone, higher-timeframe gate, risk sizing."""
    hm = HM([c.close for c in candles], rsi_length, ema_length, wma_length)
    i = at if at is not None else hm.last_ready_index()
    if i is None or i < 1:
        return Signal("HOLD", "HOLD", candles[-1].date if candles else "", 0, 0, 0, 0,
                      "NEUTRAL", notes=["Not enough history to compute the indicator."])

    hm_hi = HM([c.close for c in higher_candles], rsi_length, ema_length, wma_length)
    bias = trend_bias(hm_hi)

    raw, gates = detect(candles, hm, i)
    states = describe_states(hm, i)

    sig = Signal(signal=raw, raw_signal=raw, bar_date=candles[i].date,
                 close=candles[i].close, rsi=round(hm.rsi[i], 2),
                 green=round(hm.green[i], 2), red=round(hm.red[i], 2),
                 separation=round(hm.separation(i) or 0.0, 2),
                 trend_bias=bias, gates=gates, states=states)

    # S.4 no-trade zone -- bunched lines around 50 are where the source says
    # repeated stop-outs come from.
    avg_sep = hm.avg_separation(i, no_trade_lookback) or 0.0
    lo = max(0, i - no_trade_lookback + 1)
    near = [abs(v - RSI_MID) for v in hm.rsi[lo:i + 1] if v is not None]
    avg_dist_from_mid = sum(near) / len(near) if near else 99.0
    bunched = avg_sep < no_trade_threshold
    hugging_mid = avg_dist_from_mid < no_trade_mid_band
    quiet = bunched and hugging_mid
    sig.states["avg_separation"] = round(avg_sep, 2)
    sig.states["avg_distance_from_50"] = round(avg_dist_from_mid, 2)
    sig.gates.append(Gate("no_trade_zone", not quiet,
                          f"Over {no_trade_lookback} bars: line separation {avg_sep:.2f} "
                          f"(bunched below {no_trade_threshold:.2f}) and average distance "
                          f"from 50 is {avg_dist_from_mid:.2f} (hugging below "
                          f"{no_trade_mid_band:.2f}). "
                          + ("Both true - S.4 dead zone, stand aside." if quiet else
                             "Not both true, so this is not the S.4 dead zone.")))
    if quiet:
        sig.signal = "HOLD"

    # S.6 higher-timeframe gate.
    if raw != "HOLD":
        want = "BULLISH" if raw == "LONG" else "BEARISH"
        ok = bias == want
        sig.gates.append(Gate("higher_timeframe", ok,
                              f"Higher timeframe is {bias}; a {raw} needs {want}. "
                              f"{'Aligned.' if ok else 'S.6 says wait rather than fight it.'}"))
        if not ok:
            sig.signal = "HOLD"

    if sig.signal in ("LONG", "SHORT"):
        _size(sig, candles, i, capital, risk_pct, min_rr, lot_size)

    if sig.signal == "HOLD" and raw != "HOLD":
        sig.notes.append(f"A {raw} setup formed but was blocked by a failing gate above.")
    if states["bull_exhaustion"]:
        sig.notes.append("S.3a: upward momentum fading (EMA-3 crossed above the RSI). "
                         "Not a trade trigger; do not open fresh longs here.")
    if states["bear_exhaustion"]:
        sig.notes.append("S.3a: downward momentum fading. Not a trade trigger.")
    if states["continuation_setup"]:
        sig.notes.append("S.3d: RSI held its side of 50 while the red line came back "
                         "inside the zone - continuation, so the prior swing extreme "
                         "is likely to be breached.")
    return sig


def _size(sig: Signal, candles: Sequence[Candle], i: int, capital: float,
          risk_pct: float, min_rr: float, lot_size: int) -> None:
    """S.7 -- stop from the swing extreme, target at min R:R, size by 2% risk."""
    entry = candles[i].close
    if sig.signal == "LONG":
        stop = min(candles[i].low, candles[i - 1].low)
        target = entry + abs(entry - stop) * min_rr
    else:
        stop = max(candles[i].high, candles[i - 1].high)
        target = entry - abs(entry - stop) * min_rr

    risk_per_unit = abs(entry - stop)
    sig.entry, sig.stop_loss, sig.target = round(entry, 2), round(stop, 2), round(target, 2)
    if risk_per_unit <= 0:
        sig.gates.append(Gate("risk_reward", False,
                              "Stop equals entry - no measurable risk, cannot size."))
        sig.signal = "HOLD"
        return

    sig.risk_reward = round(abs(target - entry) / risk_per_unit, 2)
    sig.gates.append(Gate("risk_reward", sig.risk_reward >= min_rr,
                          f"Risk/reward is 1:{sig.risk_reward} against a 1:{min_rr:g} "
                          f"minimum (S.10)."))
    if sig.risk_reward < min_rr:
        sig.signal = "HOLD"
        return

    if capital > 0:
        budget = capital * risk_pct
        raw_qty = floor(budget / risk_per_unit)
        qty = (raw_qty // lot_size) * lot_size if lot_size > 1 else raw_qty
        sig.quantity = qty
        sig.risk_amount = round(qty * risk_per_unit, 2)
        ok = qty > 0
        sig.gates.append(Gate("position_size", ok,
                              f"{risk_pct:.0%} of Rs.{capital:,.0f} is Rs.{budget:,.0f}; "
                              f"at Rs.{risk_per_unit:.2f} risk per unit that is {qty} units"
                              + (f" ({qty // lot_size} lots of {lot_size})" if lot_size > 1 else "")
                              + ("." if ok else " - too small to trade one lot.")))
        if not ok:
            sig.signal = "HOLD"


def trailing_exit(hm: HM, position_side: str, i: int | None = None) -> bool:
    """S.5/S.9 -- exit when the red line crosses back through 50 against you."""
    i = i if i is not None else (hm.last_ready_index() or -1)
    if i < 1 or hm.red[i] is None or hm.red[i - 1] is None:
        return False
    if position_side == "LONG":
        return hm.red[i] < RSI_MID <= hm.red[i - 1]
    if position_side == "SHORT":
        return hm.red[i] > RSI_MID >= hm.red[i - 1]
    return False
