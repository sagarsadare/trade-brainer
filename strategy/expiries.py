"""Expiry classification and the playbook's cross-expiry selection rule (S.7).

Monthly = the last expiry listed in a calendar month.
Weekly  = every other expiry. An index whose only expiries are month-ends
(Bank Nifty, since NSE cut index weeklies back to Nifty) therefore has no
weekly leg, and the calendar spread cannot be constructed at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

# The playbook rolls the short leg one day before expiry, so an expiry that
# close is not a valid leg for a NEW entry.
MIN_WEEKLY_DAYS = 2
MID_MONTH_DAY = 15          # S.7: entering on/after the 15th buys next month


@dataclass(frozen=True)
class ExpiryChoice:
    weekly: date | None
    monthly: date | None
    reasons: list[str]

    @property
    def usable(self) -> bool:
        return self.weekly is not None and self.monthly is not None \
            and self.weekly < self.monthly


def monthly_expiries(expiries: Sequence[date]) -> list[date]:
    """The last listed expiry of each calendar month."""
    last: dict[tuple[int, int], date] = {}
    for e in expiries:
        key = (e.year, e.month)
        if key not in last or e > last[key]:
            last[key] = e
    return sorted(last.values())


def weekly_expiries(expiries: Sequence[date]) -> list[date]:
    monthlies = set(monthly_expiries(expiries))
    return sorted(e for e in expiries if e not in monthlies)


def has_weeklies(expiries: Sequence[date]) -> bool:
    return bool(weekly_expiries(expiries))


def pick(expiries: Sequence[date], on: date | None = None) -> ExpiryChoice:
    """Apply S.7: sell the nearest usable short expiry, buy the appropriate monthly.

    The buy leg is the current month's expiry when entering early in the month,
    the next month's from the 15th onward (more time value left to harvest).

    The short leg is the nearest expiry that is (a) outside the roll window and
    (b) strictly before the buy leg. Note that during expiry week the month-end
    contract *is* that week's short contract, so month-ends are eligible short
    legs -- what disqualifies an index is having no weeklies at all.
    """
    on = on or date.today()
    reasons: list[str] = []
    if not [e for e in expiries if e >= on]:
        return ExpiryChoice(None, None, ["No expiries listed on or after this date."])

    monthlies = [m for m in monthly_expiries(expiries) if m >= on]

    if not has_weeklies(expiries):
        reasons.append(
            "This index lists no weekly expiries - only month-end contracts. "
            "A sell-weekly / buy-monthly calendar cannot be built. (A month-to-month "
            "calendar is possible but has different theta behaviour and roll cadence, "
            "so it is not this strategy.)")
        return ExpiryChoice(None, monthlies[0] if monthlies else None, reasons)

    if not monthlies:
        return ExpiryChoice(None, None, reasons + ["No monthly expiry available to buy."])

    if on.day < MID_MONTH_DAY:
        monthly = next((m for m in monthlies if (m.year, m.month) == (on.year, on.month)),
                       monthlies[0])
        reasons.append(f"Entering before the {MID_MONTH_DAY}th: buying {monthly}.")
    else:
        monthly = next((m for m in monthlies if (m.year, m.month) != (on.year, on.month)),
                       monthlies[-1])
        reasons.append(f"Entering on/after the {MID_MONTH_DAY}th: buying {monthly} "
                       f"for more time value.")

    nearest = min(e for e in expiries if e >= on)
    weekly = None
    while monthly is not None:
        shorts = [e for e in expiries
                  if e >= on and (e - on).days >= MIN_WEEKLY_DAYS and e < monthly]
        if shorts:
            weekly = shorts[0]
            break
        later = [m for m in monthlies if m > monthly]
        if not later:
            reasons.append("No expiry sits between today and the buy leg.")
            return ExpiryChoice(None, monthly, reasons)
        reasons.append(f"No short leg available before {monthly}; rolling the buy leg "
                       f"out to {later[0]}.")
        monthly = later[0]

    if weekly is not None and weekly != nearest:
        gap = (nearest - on).days
        if gap < MIN_WEEKLY_DAYS:
            reasons.append(f"Nearest expiry {nearest} is {gap}d away, inside the "
                           f"{MIN_WEEKLY_DAYS}d roll window; selling {weekly} instead.")
    if weekly is not None and weekly in set(monthly_expiries(expiries)):
        reasons.append(f"Short leg {weekly} is a month-end contract - it is this "
                       f"week's front expiry.")
    return ExpiryChoice(weekly, monthly, reasons)
