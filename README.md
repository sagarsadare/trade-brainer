# TradeBrain

Two tools over one Zerodha Kite Connect session, one process, one port:

1. **PCR pipeline** — 15-minute Put-Call Ratio collection with previous-session
   reconstruction and a live dashboard (`/`).
2. **Calendar spread strategy** — the premium-driven maths-based calendar spread,
   with option chain, expiry selection, multi-index support, margin and order
   placement (`/strategy`).

---

## 1. PCR pipeline

15-minute Put-Call Ratio collection from Zerodha Kite Connect, with previous-session
reconstruction and a live dashboard.

Tracks **four series**: OI-PCR and Volume-PCR, for the **weekly** and **monthly**
expiry separately, computed over an **ATM ±N strike** window (`STRIKE_WINDOW` in
`.env`; currently 8 → 17 strikes → 68 option legs per snapshot).

---

## Quick start

```bash
pip install -r requirements.txt
```

1. Create a Kite Connect app at <https://developers.kite.trade/apps> (₹500/mo).
   Set its **Redirect URL** to exactly `http://127.0.0.1:8000/auth/kite/callback`.
2. Copy your credentials into `.env` (`KITE_API_KEY`, `KITE_API_SECRET`).
3. Log in — click **Connect Kite** on the dashboard, or:

```bash
python run.py login
```

   If the redirect does not come back to the app, use **Connect manually**
   (`/auth/kite/manual`): log in, then paste the whole URL from your address bar,
   even if that page failed to load. Same thing from a terminal:

```bash
python run.py login --token "<paste the redirect URL here>"
```

```bash
python probe_kite.py
```

4. Start the service:

```bash
python run.py serve
```

Dashboard: <http://127.0.0.1:8000/>

---

## Run `probe_kite.py` first — it decides half the design

Everything about *live* collection works on the base ₹500/mo Kite Connect plan.
**Previous-day PCR at 15-minute granularity does not.** `quote()` only returns a
point-in-time OI snapshot; the only endpoint in the entire Kite API that returns
*past intraday* open interest is `historical_data(..., oi=True)`, which requires
the **Historical Data add-on (₹2000/mo)**.

`probe_kite.py` runs six checks and prints a verdict:

| # | Check | Needs |
|---|-------|-------|
| 1 | Profile / session | login |
| 2 | NFO dump + expiry resolution | base plan |
| 3 | Live index quote | base plan |
| 4 | Live option-chain quote (OI + volume) | base plan → **live collection** |
| 5 | Historical index candles | historical add-on |
| 6 | **Historical OPTION candles with OI** | historical add-on → **backfill** |

If check 6 fails, the system still works — "yesterday's" line simply accrues from
the days your collector actually ran, appearing from your second session onward.
Nothing else in the design changes.

---

## Architecture

```
run.py                  CLI: serve | collect | backfill | login
probe_kite.py           capability diagnostic
tools/seed_demo.py      synthetic data for UI work (no Kite needed)

pcr/
  config.py             .env, IST calendar, 15-min slot grid, rate limits
  kite_client.py        auth + token cache + per-endpoint throttle + retry
  instruments.py        NFO dump cache, weekly/monthly expiry pick, ATM window
  pcr.py                the ratio maths — quote path AND candle path
  collector.py          live 15-min snapshot job
  backfill.py           past-session reconstruction from OI candles
  store.py              SQLite (WAL) schema, upserts, queries
  api.py                FastAPI: /api/series, /api/status, auth, triggers
  scheduler.py          APScheduler cron wiring
  static/dashboard.html Plotly.js dashboard
data/pcr.db             the database
```

### Data flow

**Live** (every 15 min, 09:15–15:30 IST, Mon–Fri):
`quote(NIFTY 50)` → ATM → `chain.window(spot, ±N)` → one batched `quote()` of
every option leg → sum CE/PE OI and volume per expiry → upsert.
Two API calls per tick.

**Backfill** (a past session, on demand or 08:45 each morning):
Nifty 15-min candles give the spot at every slot → the ATM window is
**re-derived per slot**, exactly as the live collector would have → the union of
all strikes touched that day is fetched once with `oi=True` → per-slot windows
are sliced out of that union.
Roughly 80 historical calls ≈ **30 seconds per session** at the 3 req/s limit
(measured: 32 s for 2026-08-28 at `STRIKE_WINDOW=8`).

### Intraday trend table

`GET /api/intraday?date=&expiry_kind=` returns one row per 15-minute slot:
call OI, put OI, **diff** (put minus call, so it shares the sign of PCR − 1), PCR,
option signal (BUY at PCR ≥ 1), price, **VWAP** and its signal, and max pain.

The card also carries its own line chart: time on the x-axis, PCR on the y-axis,
with a dashed 1.00 neutral line. Points are green at PCR ≥ 1 and red below, using
the same rule as the signal column, so chart and table cannot disagree.

VWAP comes from the **front-month future**, because the index reports no traded
volume at all — Kite returns 0 — so it cannot supply one. It is a cumulative
session VWAP over typical price `(H+L+C)/3`, using the same forward candle-label
shift as the backfill so it lines up with the OI columns.

### The alignment problem, and how it's solved

The two data sources do not mean the same thing, and naively mixing them produces
a comparison chart that is quietly wrong:

| | `quote()` (live) | `historical_data()` (backfill) |
|---|---|---|
| `oi` | open interest **now** | open interest at the **candle's close** |
| `volume` | **cumulative** for the day | **that candle's** volume only |

So the backfill does two things:

1. **Cumulatively sums** candle volume from the open, to match live semantics.
2. **Shifts candle labels forward one interval** — the candle stamped 09:15 closes
   at 09:30, so it describes the 09:30 state.

Both paths then mean *"open interest and traded volume as at HH:MM"*, and the live
job fires 20 s after each mark for the same reason.

**Consequence:** backfilled sessions start at `09:30` (25 slots), live sessions
start at `09:15` (26 slots). A 15-minute candle cannot report the opening state,
and F&O has no pre-open session to borrow it from. The dashboard renders this
honestly rather than interpolating.

---

## Commands

```bash
python run.py serve --port 8000
```

```bash
python run.py collect
```

```bash
python run.py backfill --days 5
```

```bash
python run.py backfill --date 2026-08-28
```

The dashboard's **Snapshot now** and **Backfill session** buttons call the same code.

---

## Known limits — read before trusting a number

- **Daily login.** Kite access tokens die around 06:00 IST. Run `python run.py login`
  each morning (or click *Connect Kite* on the dashboard). The token is cached in
  `data/kite_token.json` and reused all day.
- **Backfill across a past expiry is refused, not guessed.** Kite drops expired
  contracts from the instrument dump. Nifty weeklies are ~7 days apart, so if the
  resolved weekly expiry is 7+ days after the session being rebuilt, the contract that
  was actually the front weekly that day has delisted — and backfilling anyway would
  label the *next* week's contract as "weekly" and quietly corrupt the series.
  `backfill_day` refuses and logs `skipped`. Override with `--allow-stale-expiry`
  only if you understand the series will not be comparable across days.
  `data/instrument_archive/` fills from your first run, so sessions from then on are
  always recoverable; earlier ones are not.
- **Illiquid strikes.** Legs Kite omits from a quote are skipped, not counted as zero,
  so a missing strike narrows the window rather than deflating one side of the ratio.
  `n_strikes` in every row records what actually went into the number.
- **Weekly ≠ monthly collision.** In expiry week the two would resolve to the same
  contract, so the monthly series rolls to the following month. `expiry_date` is
  stored on every row — always check it rather than assuming.
- **Market close is moving.** NSE circular FAOP74467 introduces the Closing Auction
  Session (15:30–15:40) from 2026-08-03. Continuous trading still ends 15:30, so slots
  stop there. Note that `SESSION_CLOSE=15:40` adds no slot — the 15-minute grid from
  09:15 lands on 15:30 then 15:45 — it only widens the tick guard. Use `15:45` to
  actually capture a post-CAS slot.
- **Holidays.** No NSE holiday calendar is wired in; holidays surface as sessions with
  no candles and are logged as `skipped`.

---

## Demo data

`tools/seed_demo.py` writes synthetic sessions tagged `source='demo'` so the
dashboard can be worked on without Kite. Remove them with:

```bash
python tools/seed_demo.py --clear
```

---

## Schema

`pcr_snapshot`, keyed `(session_date, slot, expiry_kind)` — re-running a slot
overwrites it, so collection and backfill are both idempotent.

Raw `ce_oi`, `pe_oi`, `ce_volume`, `pe_volume` are stored alongside `oi_pcr` and
`vol_pcr`, so any other PCR definition (a different strike window, a weighted
ratio, a change-in-OI PCR) can be recomputed later without re-hitting the API.

---

# 2. Calendar spread strategy

Implements the playbook in [`docs/calendar_spread_playbook.md`](docs/calendar_spread_playbook.md):
sell a near-week option and buy a far-month option at premium-selected strikes,
sized 3:1:1 so bought premium roughly equals sold premium.

Open **<http://127.0.0.1:8000/strategy>**.

## What it does

| Playbook rule | Where it lives |
|---|---|
| S.2 chain workflow — weekly vs monthly premium, same strike | `strategy/chain.py`, `GET /api/strategy/chain` |
| S.3 premium-driven strikes (Rs.200 buy / Rs.150 + Rs.450 sell) | `find_by_premium()` |
| S.3 round-strike tie-breaker | `ROUND_STRIKE_TOLERANCE` then prefer `strike % round_step == 0` |
| S.3 balance bought vs sold premium | `buy_lots = round(sold / buy_premium)` — the 3:1:1 ratio emerges, it is not hard-coded |
| S.5/S.9 entry gap filter | `_run_checks()` — the strategy's only real risk control |
| S.7 expiry selection (current month early, next month from the 15th) | `strategy/expiries.py` |
| S.8 fair-value zones for skew bias | `fair_value_zone()` — compounds the crash **top**, see below |

### Fair value is compounded from the crash top, not the low

Growing the 2020 crash *low* (7,511) forward at 11.7% gives a fair value near
**15,300** against a 24,175 spot — which labels every level since 2023 a "lifetime
high" and permanently biases the strategy to sell. Compounding the pre-crash *peak*
(12,430 on 2020-01-20) answers the question actually being asked: where would the
index sit had the crash never happened and it simply grown at trend? That gives
**25,827**, with spot 6.4% below it — the **fair-value** zone, and a balanced
buy:sell skew.

`GET /api/strategy/fairvalue` returns the crash top and its date, the crash year and
low, the drawdown, the **latest top** (derived from Kite history, ~4 years), today's
fair value, spot, and the zone.

## Index support

| Index | Exchange | Lot | Strike step | Usable? |
|---|---|---|---|---|
| NIFTY | NFO | 65 | 50 | Yes |
| SENSEX | BFO | 20 | 100 | Yes |
| BANKNIFTY | NFO | 30 | 100 | **No** — NSE cut index weeklies to Nifty only, so there is no weekly leg to sell. The engine refuses rather than substituting a wrong expiry. |

## Order placement — paper by default

Three independent gates before anything reaches the exchange:

1. **Mode defaults to `paper`.** The basket is recorded in SQLite; nothing is sent.
2. **`ALLOW_LIVE_ORDERS=false`** in `.env` refuses live placement outright (HTTP 403),
   even with a valid confirmation.
3. **An exact typed confirmation** naming the index and leg count
   (e.g. `PLACE 3 NIFTY LIVE`) is required per basket.

Long legs are placed before short legs so the exchange grants the spread margin
benefit. Placement stops at the first rejection rather than building out a
half-hedged position — check your Kite orderbook if that happens.

Nothing in this codebase places an order on its own. There is no auto-trading path.

## Margin — use the basket number, not the sum of legs

Measured on a live NIFTY 3:1:1 CE calendar (2026-08-30):

| | |
|---|---|
| Basket margin required | **Rs. 25,002** |
| Same legs without hedge benefit | Rs. 145,835 |

The playbook's "~Rs. 1.5 lakh per unit" (S.6) is close to the **un-hedged** figure.
The hedged requirement was ~83% lower on this structure. Margin rules change —
`POST /api/strategy/margin` asks Kite for the live number rather than trusting either.

## Endpoints

```
GET  /api/strategy/indices     GET  /api/strategy/fairvalue?index=NIFTY
GET  /api/strategy/expiries    
GET  /api/strategy/chain       POST /api/strategy/plan
POST /api/strategy/margin      POST /api/strategy/execute
GET  /api/strategy/baskets     GET  /api/strategy/status
```

## Deliberate deviations from the source

- **The gap filter is scaled to spot, not fixed in points.** The playbook's
  ~1000-point limit is 4.14% of Nifty; applying 1000 points to Sensex at ~77,000
  would be a 4x tighter filter by accident. Stored as `max_gap_pct` per index.
- **`MIN_WEEKLY_DAYS = 2`.** S.7 rolls one day before expiry, so an expiry that
  close is not a valid leg for a *new* entry.
- **Month-end contracts are eligible short legs.** During expiry week the monthly
  *is* that week's front contract. What disqualifies an index is having no
  weeklies at all.

## Not advice

This is tooling built to a specification you supplied. The playbook is one trader's
self-reported approach, and it asks you to paper-trade and backtest for months before
risking capital. It has no stop-loss by design (S.5), which means position management
is entirely on you. Nobody here is a SEBI-registered adviser.

---

# 3. Hilega Milega strategy

Implements [`docs/hilega_milega_rules.md`](docs/hilega_milega_rules.md) (with
[`docs/hilega_milega_pseudocode.md`](docs/hilega_milega_pseudocode.md) as the Kite mapping).
Open **<http://127.0.0.1:8000/hilega>**.

## The indicator

`RSI(9)` + `EMA(3) on the RSI` + `WMA(21) on the RSI` — all three in RSI space, midline 50.
The EMA and WMA are computed **on the RSI series, never on price**; the WMA's linear
weighting is what makes the red line a strength proxy.

| Rule | Where |
|---|---|
| S.3c entry: RSI crosses 50, red WMA on the same side, candle closes beyond the previous H/L | `strategy.detect()` |
| S.3a/b/d exhaustion, preliminary top/bottom, continuation | `strategy.describe_states()` — informational, never triggers |
| S.4 no-trade zone | `strategy.evaluate()` |
| S.6 higher-timeframe gate | `strategy.evaluate()` |
| S.5/S.9 trailing exit (red crosses back through 50) | `strategy.trailing_exit()` |
| S.7/S.10 stop, 1:2 R:R, 2% risk sizing | `strategy._size()` |
| S.9 backtest before live | `backtest.run()` |

## Two corrections to the supplied pseudocode

**1. The no-trade-zone test was incomplete and inverted in effect.** The pseudocode's §4
measures only average line separation. But a *strong trend* saturates the RSI near 0/100,
which bunches all three lines — separation drops to ~0.03 and a healthy trend gets flagged
"don't trade", while choppy tape scores high separation and passes. The rules doc §4 actually
says the dead zone is *"RSI oscillates **around 50** with the three lines bunched"*. Both
halves are required, so `evaluate()` tests separation **and** average distance from 50.

**2. `no_trade_zone_threshold` is calibrated, not guessed.** The docs leave it explicitly TBD.
`backtest.calibrate_thresholds()` derives both thresholds from the instrument's own
distribution (default p25), so they travel between Nifty and a stock, and between daily and
5-minute bars. Measured on Nifty daily: separation p25 = 9.05, |RSI−50| p25 = 8.49.

## What the backtest actually showed

Nifty daily, weekly gate, ~4 years (Kite caps daily history around there). Signal funnel:

| Stage | Survivors | Lost |
|---|---|---|
| RSI crosses 50 | 119 | — |
| Red WMA confirms | 48 | −71 |
| Candle closes beyond previous H/L | 37 | −11 |
| No-trade zone | 30 | −7 |
| Weekly gate | 23 | −7 |
| Tradeable after sizing | 6 | −17 |

**The single biggest filter is position sizing, not any indicator rule.** With ₹10 lakh and
2% risk, one Nifty lot (65) needs risk/unit ≤ ₹308, but the median stop distance is 379
points — so most valid signals are unfundable. Taking the median signal at 2% risk needs
about **₹12.3 lakh**. Either size up, accept a higher risk %, or trade a smaller-lot proxy.

Six trades over four years is **not a sample you can judge an edge from**. The source claims
70–75% accuracy; nothing here confirms or refutes that. The API returns an explicit `caveat`
whenever a backtest produces fewer than 30 trades.

## No-lookahead

At bar `i` the engine sees candles `0..i` and only higher-timeframe bars that had already
**closed** by bar `i`'s date. Weekly bars are stamped with their last trading day, so
mid-week the bias comes from the previous completed week — which is what you would have had
in real time. Within a bar, the stop is checked before the target: we cannot know which came
first, so the adverse fill is assumed.

## Orders

Same three gates as the calendar spread: paper by default, `ALLOW_LIVE_ORDERS=true` required,
and an exact typed phrase (`PLACE HM LONG NIFTY`). Live placement sends a MARKET entry
followed immediately by an SL-M stop. If the entry fills and the stop is rejected, the
response says so in plain terms — that is an unprotected position and it tells you to check
your orderbook. The same bar's signal can never be recorded twice.

Weekly and monthly candles are resampled from daily; Kite has no native interval for either.

## Not advice

Tooling built to a specification you supplied. The source insists on backtesting, then paper
forward-testing, before any live order — and on this evidence the sample is nowhere near
large enough to skip that.
