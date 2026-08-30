# Maths-Based Calendar Spread Strategy — Trading Playbook
### Derived from "This Maths Professor Cracked Options Trading" (Chirag Jain / TradeWise)

> **Disclaimer:** This is an educational synthesis of one trader's publicly stated approach, not personalised investment advice. Options trading carries real risk of loss, including loss beyond initial margin in adverse scenarios. Validate every rule below with your own backtesting and paper trading before risking capital. I am not a SEBI-registered investment adviser.

---

## 1. Core Idea in One Line

Sell a near-week option and buy a far-month option **at the same strike** (a calendar / time spread), sized by **premium price**, not by directional forecast — and treat every "loss" as a rollover-to-be-managed rather than a stop-loss to be booked.

The engine is **theta decay differential**: the weekly (sold) leg loses time value faster than the monthly (bought) leg, so each week's rollover harvests a small, historically-consistent "rollover benefit" almost regardless of which way the market moves.

---

## 2. Option Chain Analysis — What To Actually Look At

Forget OI charts, PCR, max pain and Greek dashboards for this system. The only thing that matters is **premium price at each strike, across two expiries simultaneously**:

1. Pull up the option chain for the **current/next week expiry** and the **current/next month expiry** side by side, same underlying (e.g. Nifty), same option type (Calls preferred).
2. For each strike from roughly 1,000 points ITM to 1,000 points OTM, note the **premium** at both expiries.
3. You're building a mental (or spreadsheet) table of "premium at strike X, weekly vs monthly" — this is your entire option-chain workflow. No Greeks, no OI required.
4. Do this at consistent times of day (his research used 10:00 AM, 12:00 PM, 2:00 PM snapshots) so your data is comparable day to day.
5. Track it over time and you'll notice: rollover benefit is smallest near ATM in low-VIX conditions and larger the further price sits from the mean / the higher VIX runs — this is naturally self-correcting and works in the strategy's favour either way.

**Action item:** Before trading this live, spend 1–2 hours a day for several weeks logging weekly-vs-monthly premiums at the ₹150 / ₹200 / ₹450 price points (see below) so you have your own current-regime rollover-benefit numbers rather than trusting someone else's historical average.

---

## 3. Strike Price Selection — Premium-Driven, Not Strike-Driven

This is the counter-intuitive core of the method: **you never pick a strike number. You pick a premium price, then find whichever strike currently has that premium.**

| Leg | Target premium | Role |
|---|---|---|
| Buy (monthly expiry) | ~₹200 | The "swing"/delta-generating leg |
| Sell (weekly expiry) #1 | ~₹150 | Closer-to-money short leg |
| Sell (weekly expiry) #2 | ~₹450 | Deeper short leg (further from money) |

**Balancing rule:** size lots so total buy premium ≈ total sell premium.
- Example: **Buy 3 lots @ ₹200 = ₹600** vs **Sell 1 lot @ ₹150 + 1 lot @ ₹450 = ₹600**.
- This gives a 3-buy : 1-sell : 1-sell structure — deliberately more long lots than short lots, so a large move in *either* direction still profits (asymmetric by design).

**Tie-breaker:** if two adjacent strikes both roughly match your target premium, pick the **round-number strike** (e.g. 24,500 over 24,540).

**Call vs Put bias by "zone"** (see fair-value framework in §8):
- Near a lifetime-high zone → sell heavier, buy lighter (defensive skew).
- Near fair value → keep buy:sell roughly 50:50.
- Near an extreme-low zone → buy heavier, sell lighter (upside skew).

---

## 4. Target Setting

- **Standard target: 3–4% return on capital deployed**, taken as the "flow" exit whenever the position reaches it without needing adjustment.
- **For beginners: cap ambition at 3%/month.** The claim (unverified, take as directional only) is that ~93% of retail options traders lose money — the first goal is consistency, not size.
- Occasional large gap-moves (≈1,000 Nifty points) can produce **7–12% on a single trade**; if 1–2 such moves occur in a year alongside steady 2–3%/month base-rate trades, annualised returns compound meaningfully — but do not plan around this as a reliable input.
- Bigger targets (5–10%+) are achievable but come with **proportionally more "time risk"** — you'll hold (and roll) longer to get there.
- Run **cascading exits** if you stack multiple calendar spreads tuned to different levels (+500, +1000, flat): book profit on whichever leg crosses its threshold first, let the rest continue.

---

## 5. Stop Loss — Replaced by a Rollover Discipline, Not a Percentage

There is **no fixed percentage stop-loss** in this system — the claim is that mechanical %-stop-losses on option-selling strategies just convert one bad week into a permanent realised loss, when a rollover could have recovered it. Instead:

- **Risk is managed by structure, not by exit price**: the maximum gap between your buy-leg premium point (₹200) and your farthest sell-leg premium point (₹450) should be **no more than ~2× your expected cumulative rollover coverage** for the remaining cycle (roughly 900–1,000 Nifty points in his data). **If the gap is wider than that when you're about to enter — skip the trade.** This is your real risk control, applied *before* entry, not after.
- If a short leg is about to be breached before its expiry, **roll it to the next weekly expiry at the same strike** rather than closing for a loss — this both re-collects time value and reduces net directional exposure over time.
- If an entire month passes with no meaningful move, simply **extend the buy leg by another month** — do not force an exit.
- For larger capital, add a **butterfly counter-trade** as your defined-risk hedge: small, capped loss (e.g. ₹5,000–7,000) if a big move happens; larger payoff (e.g. ₹80,000) if the market stays flat. This is the closest thing to a conventional "worst case" cap in the whole system.
- **Discipline note:** if you find yourself hitting repeated "stop-losses" back to back and mentally resetting each one as "just 2% again," that's the drawdown accumulating for real — don't hide from it psychologically. This system tries to avoid that trap structurally by not stop-lossing at all, but it only works if the entry gap rule (above) was actually respected.

---

## 6. Margin & Capital Management

- **Typical capital for one calendar-spread unit (3 buy + 2 sell lots, Nifty)**: roughly **₹1.5 lakh** of option-selling margin (verify current SPAN+exposure margin with your broker — margin rules change).
- **Do not trade this with ₹5,000–20,000.** Below a certain capital floor the position sizing/lot math simply doesn't work, and claims of turning small capital into large multiples are, per this framework, essentially never realistic.
- **Scale by ratio, not by doubling exposure blindly**: as capital grows, scale the 3:1:1 lot ratio proportionally (e.g. 6:2:2), re-tuning the buy:sell balance to the current zone (§3).
- **Diversify as capital grows**: across zones (fair value / high / low) and across strategy types (pure calendar spread + butterfly hedge + other consolidation-favouring structures) rather than concentrating one large single-direction bet.
- **No revenge trading**: escalating position size to recover a loss (₹5k → ₹10k → ₹15k...) is explicitly the failure pattern to avoid.
- **Progressive scaling path suggested**: start around ₹1 lakh, prove consistency (e.g. ~10% over a stretch), then scale to ₹20 lakh, then ₹40 lakh, etc. — treat capital scaling like opening business locations one at a time, not all at once.

---

## 7. Expiry Selection

- **Structure is always cross-expiry**: buy monthly, sell weekly, same strike.
- **Which monthly to buy:**
  - Entering **early in the month** → use the **current month's** expiry.
  - Entering **mid-month (≈15th–17th)** → use the **next month's** expiry (more time value left to harvest).
- **Weekly leg rollover timing:** roll **one day before weekly expiry** (not on expiry day itself) — same-day rollovers lose the margin benefit that exchanges have removed for expiry-day positions, making them costlier.
- **Typical cadence:** ~3 weekly rollovers per monthly cycle (e.g. rolling on the 10th → 17th → 24th → 30th style calendar).
- **Rolling the buy (monthly) leg itself is rare** — only done if the trade needs to be extended beyond one month with no resolution yet.

---

## 8. Supporting Framework: "Fair Value" Zones (for strike/skew bias only — not a chart/indicator)

This is used only to decide the buy:sell skew in §3, not for entries/exits:

- Anchor to the last major crash low (>25% drawdown), e.g. the COVID crash reference point.
- Project forward at a long-run "fair" growth rate (~11.7%/year, i.e. roughly the commonly-cited long-term Nifty CAGR) to estimate a **current fair-value level**.
- Define three zones: **lifetime-high**, **fair value**, and a symmetric **extreme-low** (fair value minus the high-to-fair-value gap).
- Use only to bias lot ratios (§3) — near highs sell heavier, near fair value balance 50:50, near extreme lows buy heavier.

---

## 9. When NOT to Trade (Filters Before Entry)

Skip the month/trade entirely if any of these hold:
- The gap between your buy premium point (₹200) and farthest sell premium point (₹450) exceeds ~2× achievable rollover coverage (~900–1,000 points).
- It's an abnormally high-VIX day where a balanced 3:1:1 setup can't be constructed cleanly.
- No configuration satisfies your zone/gap rules for the month — sit out, even for the full month, rather than forcing a trade out of FOMO.
- You have less than roughly ₹1–1.5 lakh of dedicated, risk-capital margin for one unit.
- You haven't yet backtested/paper-traded this specific structure for at least a few months in current market conditions.

---

## 10. Suggested Operating Rhythm

- **Trade frequency:** very low — often just 1–2 new setups per month, not a daily-trading system.
- **Daily time commitment:** ~30 min post-market journalling (high/low/volume/why-it-moved) + separately, while building the system, 1.5–2 hrs/day backtesting; check live positions ~twice a day.
- **Before going live:** keep a daily market journal and backtest/paper-trade for several months to build the intuition this system assumes.
- **Suggested background reading:** *Option Volatility and Pricing* (Sheldon Natenberg) for the underlying option-pricing mechanics this strategy leans on.

---

## 11. Quick-Reference Checklist (Pin This)

| Step | Rule |
|---|---|
| Option chain | Compare weekly vs monthly premium, same strike, ~1000 pts ITM→OTM |
| Buy leg | Find strike priced ~₹200 (monthly expiry), prefer round strike |
| Sell leg 1 | Find strike priced ~₹150 (weekly expiry) |
| Sell leg 2 | Find strike priced ~₹450 (weekly expiry) |
| Lot ratio | 3 buy : 1 sell : 1 sell (rebalance by zone) |
| Entry filter | Skip if buy↔far-sell gap > ~900–1,000 pts |
| Margin | ~₹1.5L per unit; never below ₹1L dedicated capital |
| Expiry (buy) | Current month if entering early; next month if entering mid-month |
| Expiry (sell) | Roll weekly, one day before expiry |
| Target | 3–4% on capital per flow; beginners cap at 3%/month |
| Stop loss | None fixed — roll instead; hard filter is the entry gap rule |
| Skip trade | If gap rule fails, VIX unworkable, or no valid zone setup |

---

*Source: extracted and restructured from `maths to crack market.md` (transcript of a Hindi-language interview with Chirag Jain, TradeWise LearnCast series). Numbers cited (rollover benefit averages, backtest month counts, etc.) are the guest's self-reported historical figures over ~5 years / ~40 months of his own data — treat as claims to independently verify, not proven universal constants.*
