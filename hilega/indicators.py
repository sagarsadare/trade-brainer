"""Hilega Milega indicator maths.

RSI(9), then EMA(3) and WMA(21) computed ON THE RSI SERIES -- not on price.
That is the whole trick of the indicator: the WMA's linear weighting acts as a
strength/volume proxy over momentum itself.

Pure Python on purpose: no pandas/numpy dependency for a few hundred bars, and
every function is independently testable.
"""
from __future__ import annotations

from typing import Sequence

Number = float | None


def wilder_rsi(closes: Sequence[float], length: int = 9) -> list[Number]:
    """Wilder's RSI. Returns None for bars before the first full period."""
    n = len(closes)
    out: list[Number] = [None] * n
    if n <= length:
        return out

    gains = losses = 0.0
    for i in range(1, length + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / length, losses / length
    out[length] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)

    for i in range(length + 1, n):
        change = closes[i] - closes[i - 1]
        # Wilder smoothing: equivalent to an EMA with alpha = 1/length.
        avg_gain = (avg_gain * (length - 1) + max(change, 0.0)) / length
        avg_loss = (avg_loss * (length - 1) + max(-change, 0.0)) / length
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def ema(series: Sequence[Number], length: int) -> list[Number]:
    """EMA that tolerates a None-prefix, seeded with an SMA of the first window."""
    n = len(series)
    out: list[Number] = [None] * n
    vals = [(i, v) for i, v in enumerate(series) if v is not None]
    if len(vals) < length:
        return out
    seed_idx = vals[length - 1][0]
    prev = sum(v for _, v in vals[:length]) / length
    out[seed_idx] = prev
    k = 2.0 / (length + 1)
    for i, v in vals[length:]:
        prev = v * k + prev * (1 - k)
        out[i] = prev
    return out


def wma(series: Sequence[Number], length: int) -> list[Number]:
    """Linearly weighted MA; the most recent bar carries the largest weight."""
    n = len(series)
    out: list[Number] = [None] * n
    vals = [(i, v) for i, v in enumerate(series) if v is not None]
    denom = length * (length + 1) / 2.0
    for pos in range(length - 1, len(vals)):
        window = vals[pos - length + 1: pos + 1]
        out[vals[pos][0]] = sum(v * (w + 1) for w, (_, v) in enumerate(window)) / denom
    return out


class HM:
    """The three Hilega Milega lines for one candle series."""

    __slots__ = ("rsi", "green", "red", "length")

    def __init__(self, closes: Sequence[float], rsi_length: int = 9,
                 ema_length: int = 3, wma_length: int = 21):
        self.rsi = wilder_rsi(closes, rsi_length)
        self.green = ema(self.rsi, ema_length)      # 3 EMA on RSI
        self.red = wma(self.rsi, wma_length)        # 21 WMA on RSI = strength line
        self.length = len(closes)

    def ready(self, i: int) -> bool:
        return (self.rsi[i] is not None and self.green[i] is not None
                and self.red[i] is not None)

    def last_ready_index(self) -> int | None:
        for i in range(self.length - 1, -1, -1):
            if self.ready(i):
                return i
        return None

    def separation(self, i: int) -> float | None:
        """|RSI-green| + |RSI-red| at bar i -- the no-trade-zone measure."""
        if not self.ready(i):
            return None
        return abs(self.rsi[i] - self.green[i]) + abs(self.rsi[i] - self.red[i])

    def avg_separation(self, i: int, lookback: int) -> float | None:
        vals = [s for s in (self.separation(j)
                            for j in range(max(0, i - lookback + 1), i + 1))
                if s is not None]
        return sum(vals) / len(vals) if vals else None
