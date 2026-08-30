# TradeBrain — Material Folder Brief

*A survey of `C:\Trading_Programs\Claude_TradeBrain\material` — 35 files (33 unique; 2 exact duplicates), roughly 250 MB total.*

## The short version

This isn't a random pile of PDFs — it's a fairly deliberate, layered self-study library with one clear specialization (Volume Price Analysis / Wyckoff), a solid institutional backbone (the full Zerodha Varsity course + official NSE material), a growing set of your own concrete strategy rules, and one serious outlier: a 1,200-page professional systematic-trading textbook that's a different league from everything else here. There's also one genuinely time-sensitive item buried in the pile — a real NSE circular about a market-structure change that takes effect in a few days.

---

## 1. The core curriculum — Zerodha Varsity (13 of 14 modules)

The backbone of the folder is almost the complete **Zerodha Varsity** series — the standard free Indian-markets curriculum, written in Karthik Rangappa's informal, first-person style:

- Module 1 – Introduction to Stock Markets *(saved twice, identical file)*
- Module 2 – Technical Analysis
- Module 3 – Fundamental Analysis
- Module 4 – Futures Trading
- Module 5 – Options Theory for Professional Trading
- Module 6 – Option Strategies
- Module 7 – Markets & Taxation
- Module 8 – Currency and Commodity Futures
- Module 9 – Risk Management & Trading Psychology
- Module 10 – Trading Systems *(pair trading, linear regression, cointegration-style logic)*
- Module 11 – Personal Finance (Part 1)
- Module 13 – Financial Modelling
- Module 14 – Personal Finance (Insurance)

**Module 12 is missing.** Not a big deal, but worth grabbing if you want the set complete — it's the one gap in an otherwise full run.

This is genuinely good material: sequential, India-specific (covers F&O, currency/commodity, and Indian taxation — things most Western books skip), and it scales from "what is a stock market" all the way to option Greeks and basic quant pair-trading. It's the right spine for a self-built system because it already speaks in NSE/BSE terms.

## 2. Official NSE material — exam-grade and, in one case, live regulatory news

- **`TA_wrkbk.pdf`** — the actual NCFM/NSE **Technical Analysis Module** workbook used for their certification exam (172 pages: candlestick patterns, indicators, chart theory in exam-ready depth). It also includes NSE's full list of ~46 certification modules and fees — useful if you're weighing which NSE/NISM certificate might be worth pursuing later.
- **`Brochure_Trading_Strategy_for_Market.pdf`** — a paid-course sales brochure (looks like NSE Knowledge Hub): "10 Trading Strategies + 5 Certificate Programs," ₹41,000 discounted to ₹6,999+GST, monthly live sessions. Covers Bollinger Bands, scanners, momentum, Fibonacci, RSI divergence, price/volume, swing trading, OI, sentiment, index trading. Worth knowing this is a bought-or-considered course, not neutral reference material.
- **`FAOP74467.pdf`** — ⚠️ **this is the one item that isn't "study material" at all — it's a live NSE circular**, dated May 29, 2026, about the **Closing Auction Session (CAS)** rollout in the equity derivatives segment, *effective August 3, 2026*. It changes normal market close time from 15:30 to **15:40 hrs**, and defines how the closing price will now be computed (VWAP over 15:10–15:40). If any part of your system relies on end-of-day closing price, closing candles, or EOD entries/exits on NSE instruments, this is directly relevant and current — not background reading.

## 3. Volume Price Analysis (VPA) / Wyckoff — the clear specialization

This is the most-repeated topic in the folder, appearing in **five separate files**, which tells me it's the technique you're trying hardest to internalize:

- `A_Complete_Guide_To_Volume_Price_Analysis_by_Anna_Coulling_z_lib.pdf` — the full book (274 pages)
- `A-Complete-Guide-To-Volume-Price-Analysis-PDF-Book-Images (2).pdf` — a companion extract of just the book's diagrams/images (50 pages) — essentially a visual quick-reference to the same book
- Two Hindi translations of the same book — one just Chapter 1, one running through several more chapters
- `VCA_1.pdf` — your own (or someone's) condensed notes on the book: Wyckoff's three laws (Supply & Demand, Cause & Effect, Effort vs. Result), the six VPA principles, "volume validates price," annotated candle/volume examples

Reading it once in English, then again in Hindi, then re-summarizing it into your own notes is a strong signal this is meant to be the analytical core of the system — volume-confirms-price as the primary lens, with Wyckoff accumulation/distribution as the market model underneath everything else.

## 4. Chart-pattern recognition

- `Idenitfying-Chart-Patterns.pdf` — a Fidelity Investments webinar deck by Charles Kirkpatrick (CMT) — basics of trend, indicators, and pattern ID
- `TRADE-CHART-PATTERNS-GUIDE.pdf` — the **full text** of Suri Duddella's *Trade Chart Patterns Like the Pros* (296 pages: harmonic patterns, Fibonacci tools, geometric formations), republished with front-matter marketing from a signal-service outfit called "Carlos & Company" — the book content itself looks intact, just wrapped in someone else's promo
- `GAP UP DOWN.pdf` and `Class 7 Moving Average.pdf` — short personal/course notes: gap-trading rules (reliable vs. "trap" gaps, Bollinger Band Blast context) and moving-average mechanics (20/50 SMA, "golden entry point" retest logic)
- `class 6 OHOL.docx` — sparse bullet notes on **Bollinger Band Blast** and an "Open High / Open Low" (OH/OL) setup, with timeframe-based move-duration rules (e.g., "10 min TF blast tends to run 1.5–2 hrs, Daily TF 5–15 days...")

The "class 6" / "class 7" naming implies these are numbered sessions from an ongoing course or mentor — you're likely missing classes 1–5 and whatever comes after 7 unless they just weren't saved here.

## 5. Concrete, rule-based strategy sheets — where the actual "system" is being built

This is the most actionable cluster — short, specific, entry/exit-level trading rules rather than theory:

- **`34 EMA Tradetron Setup - Sheet1.pdf`** — a directional/positional Nifty 50 setup using 34 EMA high/low bands on a 5-minute chart, built for **Tradetron** (an Indian algo-trading/strategy-automation platform), with live team commentary debating strike selection (~0.67 delta) and PnL improvements. This reads like an in-progress, collaboratively-refined algo, not finished theory.
- **`Hedge_23_Mastery_The_Volatility_System.pdf`** ("Hedge 23: Master the Mayhem") — a rule-based long-straddle-style system for **expiry-day volatility** on Nifty/Sensex: enter post-1:00 PM, strikes selected via a "Radius Method" (~50–100 pts from spot), stop-loss defined by the sum of premiums. Branded **NK StockTalk**.
- **`The_Trader_s_Playbook_Advanced_Market_Strategies.pdf`** — two more named systems: **"Hammer Down"** (a first-5-minute-candle momentum play: candle opens at its high and breaks the entire previous day's range) and **"MSTHedge"** (a theta-decay option-selling play executed the day before expiry).
- **`Stock_Future_Arbitrage.pdf`** — a mechanical spot/near-month/far-month futures arbitrage rule set (entry when Spot > Near Future > Far Future, position sizing logic as the spread narrows/widens).
- **`ICR DSCR Hindi.pdf`** — NK StockTalk's Hindi explainer on Interest Coverage Ratio and Debt Service Coverage Ratio (fundamental/credit-quality screening ratios, with simple thresholds: ICR > 3 = safe, < 1 = danger).
- **`NK_Stocktalk_Stocks_of_the_Month.pdf`** — a short paid stock-tip note (3 names with CMP/stop-loss/target), carrying a SEBI Registered Research Analyst number.

**Worth noting:** `Hedge_23...` and `The_Trader_s_Playbook...` are both auto-generated slide decks (visible **NotebookLM** watermark), and NK StockTalk shows up three separate times across this folder (Hedge 23, the ICR/DSCR explainer, and the stock tips) — it looks like a specific SEBI-registered research service/newsletter you follow, distinct from the generic public books.

## 6. The outlier — a genuinely professional-grade reference

- **`TSaM.pdf`** — Perry Kaufman's ***Trading Systems and Methods***, 5th Edition. 1,232 pages. This is a different tier of material entirely from everything else in the folder: trend-following, momentum, pattern recognition, seasonality, statistical/cycle analysis, backtesting and optimization methodology, risk control, Kelly/optimal-f position sizing, and portfolio allocation (with actual spreadsheet/TradeStation-code-level rigor). It's the standard reference quant desks use to formalize exactly the kind of ad hoc rule sets in section 5 above — it's the natural next step if the goal is to turn "Hedge 23" or "34 EMA Tradetron" into something properly backtested and risk-controlled rather than discretionary.

## 7. General/rounding-out material

- `The-Complete-Guide-to-Trading.pdf` — a shorter (116-page) general trading primer from the Corporate Finance Institute.

---

## Housekeeping notes

- **Duplicate file:** `Module 1_Introduction to Stock Markets (1).pdf` is byte-identical to `Module 1_Introduction to Stock Markets.pdf` — safe to delete one.
- **Missing Module 12** from the Varsity set.
- The VPA "Images" PDF is a strict subset of the full Coulling book — only useful as a quick visual-glossary, not standalone reading.

## What stood out most

1. **The regulatory circular (`FAOP74467.pdf`) is live and dated days from now** (CAS effective Aug 3, 2026) — easy to file mentally as "just another PDF" next to the textbooks, but it's the one document with an actual deadline and direct mechanical impact on any EOD-based logic.
2. **VPA/Wyckoff is clearly the intended analytical backbone** — five files, three languages/formats of the same core book, plus your own condensed notes. If "TradeBrain" needs one unifying lens, this already is one.
3. **The difficulty range is unusually wide** — from Fidelity beginner webinars up to Kaufman's 1,200-page quant reference — suggesting either a broad exploratory phase, or (more likely, given the concrete Tradetron/Hedge23/Hammer Down sheets) an ambition to eventually take discretionary, rule-of-thumb setups and run them through a rigorous backtesting/risk framework — which is exactly what Kaufman's book supplies and nothing else here does.
4. **NK StockTalk is a specific, named information source** (SEBI-registered) feeding multiple strategy/fundamental pieces here — worth tracking separately from generic public book material when you're deciding what to trust vs. verify independently.
