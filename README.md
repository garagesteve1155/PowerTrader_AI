# PowerTrader_AI
Fully automated crypto trading powered by a custom price prediction AI and a structured/tiered DCA system.

I have not checked any PowerTrader AI forks and cannot confirm or deny their legitimacy.

This is my personal trading bot that I decided to make open source. I made this strategy to match my personal goals. This system is meant to be a foundation/framework for you to build your dream bot! You are responsible for all financial and security risks associated with PowerTrader AI.

I know there are "commonly essential" trading features that are missing (like no stop loss for example). This is by design because many of those things would just not work with this system's strategy as it stands, for my personal reasons below:

I do not believe in selling worthwhile coins at a loss (and why would you trade anything besides worthwhile coins with a trading bot, anyways???).

I DO believe in crypto. I'd rather just wait and maybe add more money to my account if need be so that the bot can buy even more of the coin while the price is down.

I personally feel like many of those common things people use, like stop loss, are actually a trick or something, and I personally have absolutely no problem adding more money to my account to afford more DCA or having to wait for extended periods of time, if need be. In my opinion, anything else is just greedy and desperate, which is the exact OPPOSITE of needed attributes for long term growth. Plus, this is just spot trading... there's no worry of liquidation and it feels to me like many "risk management" tactics are really only meant for futures trading but people blindly apply them to spot trading when it just plain isn't necessary.

I know the AI and the trading strategy are extremely simple because I'm the one that designed and made them. I've been developing this specific trading strategy for almost a decade and the design of the AI system for the last few years. The overall strategy is based on what ACTUALLY works from real trading experience, not just stuff I read in LLM responses or search engine results.


Ok now that all of that is out of the way...

I am not selling this trading bot. This trading bot is not a product. This system is for experimentation and education. The only reason you would EVER send me money is if you are voluntarily donating (donation routes can be found at the bottom of this readme :) ) or buying merch. Do not fall for any scams! PowerTrader AI is COMPLETELY FREE FOREVER!!!!

IMPORTANT: This software places real trades automatically. You are responsible for everything it does to your money and your account. Keep your API keys private. I am not giving financial advice. I am not responsible for any losses incurred or any security breaches to your computer (the code is entirely open source and can be confirmed non-malicious). You are fully responsible for doing your own due diligence to learn and understand this trading system and to use it properly. You are fully responsible for all of your money and all of the bot's actions, and any gains or losses.

“It’s an instance-based (kNN/kernel-style) predictor with online per-instance reliability weighting, used as a multi-timeframe trading signal.” - ChatGPT on the type of AI used in this trading bot.

So what exactly does that mean?

When people think AI, they usually think about LLM style AIs and neural networks. What many people don't realize is there are many types of Artificial Intelligence and Machine Learning - and the one in my trading system falls under the "Other" category.

When training for a coin, it goes through the entire history for that coin on multiple timeframes and saves each pattern it sees, along with what happens on the next candle AFTER the pattern. It uses these saved patterns to generate a predicted candle by taking a weighted average of the closest matches in memory to the current pattern in time. This weighted average output is done once for each timeframe, from 1 hour up to 1 week. Each timeframe gets its own predicted candle. The low and high prices from these candles are what are shown as the blue and orange horizontal lines on the price charts. 

After a candle closes, it checks what happened against what it predicted, and adjusts the weight for each "memory pattern" that was used to generate the weighted average, depending on how accurate each pattern was compared to what actually happened.

Yes, it is EXTREMELY simple. Yes, it is STILL considered AI.

Here is how the trading bot utilizes the price prediction ai to automatically make trades:

For determining when to start trades, the AI's Thinker script sends a signal to start a trade for a coin if the ask price for the coin drops below at least 3 of the the AI's predicted low prices for the coin (it predicts the currently active candle's high and low prices for each timeframe across all timeframes from 1hr to 1wk).

For determining when to DCA, it uses either the current price level from the AI that is tied to the current amount of DCA buys that have been done on the trade (for example, right after a trade starts when 3 blue lines get crossed, its first DCA wont happen until the price crosses the 4th line, so on so forth), or it uses the hardcoded drawdown % for its current level, whichever it hits first. It only allows a max of 2 DCAs within a rolling 24hr window to keep from dumping all of your money in too quickly on coins that are having an extended downtrend. Other risk management features can easily be added, as well, with just a bit of Python code!

For determining when to sell, the bot uses a trailing profit margin to maximize the potential gains. The margin line is set at either 5% gain if no DCA has happened on the trade, or 2.5% gain if any DCA has happened. The trailing margin gap is 0.5% (this is the amount the price has to go over the profit margin to begin raising the profit margin up to TRAIL after the price and maximize how much profit is gained once the price drops below the profit margin again and the bot sells the trade.


# Setup & First-Time Use (Windows)

THESE INSTRUCTIONS WERE WRITTEN BY AI! PLEASE LET ME KNOW IF THERE ARE ANY ERRORS OR ISSUES WITH THIS SETUP PROCESS!

If you have any crypto holdings in Robinhood currently, either transfer them out of your Robinhood account or sell them to dollars BEFORE going through this setup process!

This page walks you through installing PowerTrader AI from start to finish, in the exact order a first-time user should do it.  
No coding knowledge needed.  
These instructions are Windows-based but PowerTrader AI *should* be able to run on any OS.

IMPORTANT: This software places real trades automatically. You are responsible for everything it does to your money and your account. Keep your API keys private. I am not giving financial advice. I am not responsible for any losses incurred or any security breaches to your computer (the code is entirely open source and can be confirmed non-malicious). You are fully responsible for doing your own due diligence to learn and understand this trading system and to use it properly. You are fully responsible for all of your money and all of the bot's actions, and any gains or losses.

---

## Step 1 — Install Python

1. Go to **python.org** and download Python for Windows.
2. Run the installer.
3. **Check the box** that says: **“Add Python to PATH”**.
4. Click **Install Now**.

---

## Step 2 — Download PowerTrader AI

1. Do not download the zip file of the repo! There is an issue I have to fix.
2. Create a folder on your computer, like: `C:\PowerTraderAI\`
3. On the PowerTrader_AI repo page, go to the code page for pt_hub.py, click the "Download Raw File" button, save it into the folder you just created.
4. Repeat that for all files in the repo (except the readme and the license).

---

## Step 3 — Install PowerTrader AI (one command)

1. Open **Command Prompt** (Windows key → type **cmd** → Enter).
2. Go into your PowerTrader AI folder. Example:

   `cd C:\PowerTraderAI`

3. If using Python 3.12 or higher (or, later on, if you just run into the pkg_resources error) , run this command:

   `python -m pip install "setuptools==81.0.0"`

v81 is required, pkg_resources is not included with v82. I'll change the code away from it soon.

4. Install everything PowerTrader AI needs:

   `python -m pip install -r requirements.txt`

---

## Step 4 — Start PowerTrader AI

From the same Command Prompt window (inside your PowerTrader folder), run:

`python pt_hub.py`

The app that opens is the **PowerTrader Hub**.  
This is the only thing you need to run day-to-day.

---

## Step 5 — Set your folder, coins, and Robinhood keys (inside the Hub)

### Open Settings

In the Hub, open **Settings** and do this in order:

- **Main Neural Folder**: set this to the same folder that contains `pt_hub.py` (recommended easiest).
- **Choose which coins to trade**: start with **BTC**.
- **While you are still in Settings**, click **Robinhood API Setup** and do this:

1. Click **Generate Keys**.
2. Copy the **Public Key** shown in the wizard.
3. On Robinhood, add a new API key and paste that Public Key.
4. Set permissions to allow trading (the wizard tells you what to select).
5. Robinhood will show your API Key (often starts with `rh`). Copy it.
6. Paste the API Key back into the wizard and click **Save**.
7. Close the wizard and go back to the **Settings** screen.
8. **NOW** click **Save** in Settings.

After saving, you will have two files in your PowerTrader AI folder:  
`r_key.txt` and `r_secret.txt`  
Keep them private.

PowerTrader AI uses a simple folder style:  
**BTC uses the main folder**, and other coins use their own subfolders (like `ETH\`).

---

## Step 6 — Train (inside the Hub)

Training builds the system’s coin “memory” so it can generate signals.

1. In the Hub, click **Train All**.
2. Wait until training finishes.

---

## Step 7 — Start the system (inside the Hub)

When all coins have completed training, click:

1. **Start All**

The Hub will:  
**start pt_thinker.py**, wait until it is ready, then it will **start pt_trader.py**.  
You don’t need to manually start separate programs. The hub handles everything!

---

## Neural Levels (the LONG/SHORT numbers)

- These are signal strength levels from low to high.
- They are the predicted high and low prices for all timeframes from 1hr to 1wk.
- They are used to show how stretched a coin's price is and for determining when to start trades and potentially when to DCA for the first few levels of DCA (Whichever price is higher, the Neural level or the hardcoded drawdown % for the current DCA level.
- Higher number = stronger signal.
- LONG = buy-direction signal. SHORT = No-start signal

A TRADE WILL START FOR A COIN IF THAT COIN REACHES A LONG LEVEL OF 3 OR HIGHER WHILE HAVING A SHORT LEVEL OF 0! This is adjustable in the settings.

---

## Adding more coins (later)

1. Open **Settings**
2. Add one new coin
3. Save
4. Click **Train All**, wait for training to complete
5. Click **Start All**

---

## Donate

PowerTrader AI is COMPLETELY free and open source! If you want to support the project, you can donate or become a member:

- Cash App: **$garagesteve**
- PayPal: **@garagesteve**
- Facebook (Subscribe to my Facebook page for only $1/month): **https://www.facebook.com/stephen.bryant.hughes**

---

## License

PowerTrader AI is released under the **Apache 2.0** license.

---

## Error Handling & Observability

### Architecture

Errors surface through two independent channels so that different audiences get the right signal:

- **Logs tab** (`pt_log.py` → stdout → controller queue) — narrative, timestamped, per-process. Format: `20260516:143201.042 WARNING  [trader-kucoin] could not save bot order IDs: …`. Useful for debugging what happened and when.
- **Errors tab** (`pt_errors.py` → `hub_data/errors.jsonl`) — structured, filterable, actionable. Only written for events that a user should know about and potentially act on. Each entry carries a `component`, `level`, `message`, and `detail` field.

The two channels are intentionally separate: the Errors tab should not be noise, and the Logs tab should not omit anything.

### Three-Tier Classification

**Tier 1 — Exchange adapters, thinker, controller**
These are external boundaries (network I/O, subprocess management). Failures here are always surfaced with both `log.exception` and `pt_errors.emit()` because a broken adapter or dead subprocess directly affects trading.

**Tier 2 — Trader, web layer, data manager**
Internal business logic with mixed failure modes. Each failure site is individually classified:
- `pt_errors.emit()` is added when the consequence is user-visible and actionable (wrong positions displayed, $0 buying power, broken PnL accounting, failed data backfill).
- `log.warning` or `log.exception` only when the failure is self-contained or already gracefully degraded (file backup steps, per-item loop guards, best-effort state cleanup).

**Tier 3 — Supporting modules** (`pt_env`, `pt_models`, `pt_trainer`)
Structural exceptions and typed errors only. `pt_env` is excluded from `pt_errors` instrumentation due to circular import constraints.

### Log Levels

| Level | When to use |
|---|---|
| `log.debug` | High-frequency operational detail (loop timing, cache hits, skipped coins). Off in production. |
| `log.info` | Normal state transitions, trade decisions, startup/shutdown. |
| `log.warning` | Something went wrong but the system recovered or degraded gracefully. |
| `log.exception` | Unexpected exception with full traceback; the operation failed entirely. |
| `pt_errors.emit(level="warning")` | User should be aware; system is still running but degraded. |
| `pt_errors.emit(level="error")` | User should act; a critical path has failed. |

### Moving Towards Fail-Fast

The current defensive `try/except` coverage is intentionally broad to capture a baseline during initial production monitoring. The plan is to progressively tighten it once real failure patterns are understood:

1. **Monitor first.** The structured Errors tab and detailed logs will show which exceptions actually occur and how often. Fix root causes rather than noting the errors.

2. **Classify exceptions by type when they appear:**
   - *Transient* (network timeout, API rate limit, file lock): keep the guard, consider retry/backoff.
   - *Invariant violation* (unexpected `None`, wrong type, logic bug): remove the guard and let it propagate — the exception is the information, and swallowing it corrupts state silently.

3. **The natural fail-fast boundary already exists.** The outer `run()` loop catches everything from `manage_trades` and restarts automatically. The right direction is to let errors propagate up to that boundary rather than handling them deep in sub-functions. Most of the scattered guards exist because the code doesn't fully trust its own invariants — removing them makes those invariants explicit.

4. **Priority order for removing guards:**
   - First: pure in-memory operations (dict access, arithmetic, type coercions). Exceptions here are bugs, not conditions.
   - Second: file I/O on files that should always exist (ledger, trade history). A missing or corrupt ledger should fail loudly, not return empty state and trade on wrong data.
   - Keep: per-item loop guards (`continue` on bad data), external API call sites, and the single outer `manage_trades → run()` boundary.

5. **Design signal.** If removing a guard feels wrong, that usually means the function is doing too much — mixing network calls, arithmetic, and file writes. Fail-fast pressure naturally pushes toward separating those concerns: pure functions can assert preconditions, I/O functions handle transient errors at the call site.

### TODO: revisit trade_history.jsonl storage

The current `state/hub_data/exchanges/<exchange>/trade_history.jsonl` format is
a plain-text JSON-lines append-only log. `initialize_dca_levels` and
`_load_bot_order_ids_from_trade_history` both line-scan the full file at every
trader-process restart to reconstruct DCA stage state + bot order ID
membership. That's fine on Kraken/shadow (~30-50 KiB each in steady state)
but the demo file has grown to ~5 GiB and is now expensive to bring up:

- ~3 minutes of CPU on restart just to scan the file
- ~28 GiB transient RSS as Python `json.loads` allocates one dict per line —
  enough to OOM-kill workers under Ray on a 32 GiB box, observed first
  during backtest sweeps but applies equally to prod demo restart

Backtest works around it by overriding the relevant trader methods in
`backtest/trader.py`. Prod can still get bitten by it on the next demo
restart.

Options worth evaluating when there's bandwidth:

1. **Rotate by month + gzip archives.** Keep `trade_history_YYYY-MM.jsonl`
   for the current month, gzip older segments. Startup only reads the
   last N months. Smallest behavioural change, no schema migration.

2. **Compaction snapshot.** Periodically write a `dca_state_snapshot.json`
   (small dict of `{coin: {dca_count, last_sell_ts}}`). `initialize_dca_levels`
   reads the snapshot + only the JSONL lines after its timestamp. Lookup
   becomes ~50 ms regardless of total history size.

3. **SQLite with an index on `(coin, ts)`.** `initialize_dca_levels` becomes
   a single indexed SQL query, ~5 ms regardless of history. stdlib, no new
   deps. One-time migration script reads existing JSONL → bulk insert.

4. **ArcticDB.** Already a dependency. Same consistent storage story as the
   OHLCV data; sorted-DataFrame reads are millisecond.

Quick mitigation today if a demo restart is imminent: archive the current
file and truncate to the recent tail, e.g.

```bash
cp state/hub_data/exchanges/demo/trade_history.jsonl \
   state/hub_data/exchanges/demo/trade_history_archive_$(date +%Y%m%d).jsonl
tail -10000 state/hub_data/exchanges/demo/trade_history.jsonl > /tmp/tail.jsonl
mv /tmp/tail.jsonl state/hub_data/exchanges/demo/trade_history.jsonl
```

`initialize_dca_levels` only needs entries since the most recent sell per
coin, so retaining the last few thousand lines is safe for the demo
trader's restart-recovery logic.

---

IMPORTANT: This software places real trades automatically. You are responsible for everything it does to your money and your account. Keep your API keys private. I am not giving financial advice. I am not responsible for any losses incurred or any security breaches to your computer (the code is entirely open source and can be confirmed non-malicious). You are fully responsible for doing your own due diligence to learn and understand this trading system and to use it properly. You are fully responsible for all of your money and all of the bot's actions, and any gains or losses.
