"""Backtest and threshold calibration.

The rules document (S.9) requires backtesting before forward-testing and
forward-testing before live, and leaves `no_trade_zone_threshold` explicitly
TBD -- "must be backtested, no fixed number is given in the source material".
This module supplies both.

No-lookahead discipline: at bar i the engine may only see candles 0..i, and
only those higher-timeframe bars that had already CLOSED by bar i's date. A
weekly bar is stamped with its last trading day, so mid-week the bias comes
from the previous completed week -- which is what you would actually have had.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from .indicators import HM
from .strategy import (DEFAULT_MIN_RR, DEFAULT_NO_TRADE_LOOKBACK,
                       DEFAULT_RISK_PCT, RSI_MID, Candle, evaluate)


@dataclass
class Trade:
    side: str
    entry_date: str
    entry: float
    stop_loss: float
    target: float
    exit_date: str = ""
    exit: float = 0.0
    exit_reason: str = ""
    bars_held: int = 0
    r_multiple: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    symbol: str
    interval: str
    higher_interval: str
    bars: int
    from_date: str
    to_date: str
    trades: list[Trade] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.r_multiple > 0)

    @property
    def stats(self) -> dict[str, Any]:
        n = len(self.trades)
        if not n:
            return {"trades": 0}
        rs = [t.r_multiple for t in self.trades]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        equity, peak, max_dd = 0.0, 0.0, 0.0
        for r in rs:
            equity += r
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)
        return {
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / n, 4),
            "total_r": round(sum(rs), 2),
            "avg_r": round(sum(rs) / n, 3),
            "avg_win_r": round(sum(wins) / len(wins), 3) if wins else 0.0,
            "avg_loss_r": round(sum(losses) / len(losses), 3) if losses else 0.0,
            "expectancy_r": round(sum(rs) / n, 3),
            "max_drawdown_r": round(max_dd, 2),
            "avg_bars_held": round(sum(t.bars_held for t in self.trades) / n, 1),
            "exit_reasons": {r: sum(1 for t in self.trades if t.exit_reason == r)
                             for r in {t.exit_reason for t in self.trades}},
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "interval": self.interval,
            "higher_interval": self.higher_interval, "bars": self.bars,
            "from_date": self.from_date, "to_date": self.to_date,
            "settings": self.settings, "stats": self.stats,
            "trades": [t.as_dict() for t in self.trades],
        }


def calibrate_thresholds(candles: Sequence[Candle], lookback: int = DEFAULT_NO_TRADE_LOOKBACK,
                         percentile: float = 0.25, rsi_length: int = 9,
                         ema_length: int = 3, wma_length: int = 21) -> dict[str, Any]:
    """Derive the S.4 no-trade thresholds from this instrument's own history.

    Both measures are taken at the same percentile of their own distribution,
    so the dead zone is "quiet for this instrument on this timeframe" rather
    than an absolute number that would not travel between Nifty and a stock,
    or between daily and 5-minute bars.
    """
    hm = HM([c.close for c in candles], rsi_length, ema_length, wma_length)
    seps: list[float] = []
    dists: list[float] = []
    for i in range(len(candles)):
        sep = hm.avg_separation(i, lookback)
        if sep is None:
            continue
        lo = max(0, i - lookback + 1)
        near = [abs(v - RSI_MID) for v in hm.rsi[lo:i + 1] if v is not None]
        if not near:
            continue
        seps.append(sep)
        dists.append(sum(near) / len(near))

    if not seps:
        return {"calibrated": False,
                "reason": "not enough history to measure line separation"}

    def q(arr: list[float], p: float) -> float:
        arr = sorted(arr)
        return round(arr[min(len(arr) - 1, int(len(arr) * p))], 2)

    return {
        "calibrated": True,
        "samples": len(seps),
        "percentile": percentile,
        "no_trade_threshold": q(seps, percentile),
        "no_trade_mid_band": q(dists, percentile),
        "separation_percentiles": {f"p{int(p*100)}": q(seps, p)
                                   for p in (0.10, 0.25, 0.50, 0.75, 0.90)},
        "distance_from_50_percentiles": {f"p{int(p*100)}": q(dists, p)
                                         for p in (0.10, 0.25, 0.50, 0.75, 0.90)},
    }


def _higher_slice(higher: Sequence[Candle], as_of: str) -> list[Candle]:
    """Higher-timeframe bars that had already closed by `as_of`."""
    return [c for c in higher if c.date <= as_of]


def run(candles: Sequence[Candle], higher: Sequence[Candle], symbol: str,
        interval: str, higher_interval: str, warmup: int = 40,
        capital: float = 1_000_000.0, risk_pct: float = DEFAULT_RISK_PCT,
        min_rr: float = DEFAULT_MIN_RR, lot_size: int = 1,
        **rule_kwargs: Any) -> BacktestResult:
    """Walk the series bar by bar, taking every signal the full rule stack allows."""
    res = BacktestResult(
        symbol=symbol, interval=interval, higher_interval=higher_interval,
        bars=len(candles),
        from_date=candles[0].date if candles else "",
        to_date=candles[-1].date if candles else "",
        settings={"warmup": warmup, "min_rr": min_rr, "risk_pct": risk_pct,
                  "lot_size": lot_size, **rule_kwargs})

    open_trade: Trade | None = None
    entry_index = 0

    for i in range(warmup, len(candles)):
        bar = candles[i]

        if open_trade is not None:
            risk = abs(open_trade.entry - open_trade.stop_loss)
            hit_sl = (bar.low <= open_trade.stop_loss if open_trade.side == "LONG"
                      else bar.high >= open_trade.stop_loss)
            hit_tgt = (bar.high >= open_trade.target if open_trade.side == "LONG"
                       else bar.low <= open_trade.target)
            # Stop is checked first: within a single bar we cannot know which
            # came first, so we assume the adverse fill. Anything else flatters
            # the result.
            if hit_sl:
                _close(open_trade, bar, open_trade.stop_loss, "stop_loss", i - entry_index, risk)
                res.trades.append(open_trade); open_trade = None
            elif hit_tgt:
                _close(open_trade, bar, open_trade.target, "target", i - entry_index, risk)
                res.trades.append(open_trade); open_trade = None
            else:
                hm_now = HM([c.close for c in candles[:i + 1]])
                j = hm_now.last_ready_index()
                if j is not None and j >= 1:
                    red, red_prev = hm_now.red[j], hm_now.red[j - 1]
                    flipped = ((open_trade.side == "LONG" and red < RSI_MID <= red_prev) or
                               (open_trade.side == "SHORT" and red > RSI_MID >= red_prev))
                    if flipped:
                        _close(open_trade, bar, bar.close, "trailing_exit",
                               i - entry_index, risk)
                        res.trades.append(open_trade); open_trade = None
            if open_trade is not None:
                continue

        hi = _higher_slice(higher, bar.date)
        if len(hi) < 25:
            continue
        sig = evaluate(candles[:i + 1], hi, capital=capital, risk_pct=risk_pct,
                       min_rr=min_rr, lot_size=lot_size, **rule_kwargs)
        if sig.signal in ("LONG", "SHORT") and sig.stop_loss is not None:
            open_trade = Trade(side=sig.signal, entry_date=bar.date, entry=sig.entry,
                               stop_loss=sig.stop_loss, target=sig.target)
            entry_index = i

    if open_trade is not None:
        risk = abs(open_trade.entry - open_trade.stop_loss)
        _close(open_trade, candles[-1], candles[-1].close, "still_open",
               len(candles) - 1 - entry_index, risk)
        res.trades.append(open_trade)
    return res


def _close(trade: Trade, bar: Candle, price: float, reason: str,
           bars_held: int, risk: float) -> None:
    trade.exit_date = bar.date
    trade.exit = round(price, 2)
    trade.exit_reason = reason
    trade.bars_held = bars_held
    move = (price - trade.entry) if trade.side == "LONG" else (trade.entry - price)
    trade.r_multiple = round(move / risk, 3) if risk else 0.0
