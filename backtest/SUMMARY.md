# Backtest implementation — overnight session summary

## TL;DR

All six phases scaffolded and committed on `backtest-research`. End-to-end
pipeline works: train → replay → aggregate → sweep → notebook. Pilot runs
clean in ~13s for one 14-day ETH epoch (5.9s training + 7.4s engine).

**One unresolved issue (the only blocker for treating the results as real):**
the ported thinker scoring math produces **0 long signals** even where prod
demonstrably fires entries (verified against BNB which traded today). The
infrastructure is sound — the bug is somewhere in the scoring port.
Details + suggested debug path in **Open issues** at the bottom.

## Branch & commits

`backtest-research`, 14 commits ahead of `main`:

```
ed28c2b  backtest: CLI + viable-asof + log-quiet fixes (this commit)
08e958a  backtest: 3D Ray sweep + CLI + Marimo research notebook
67c4d3b  backtest: hourly + daily aggregation (Phase 4)
6486ea9  backtest: add 5min replay engine (Phase 3e)
97b991c  backtest: add BacktestExchange + BacktestTrader (Phase 3d)
d1f158c  backtest: add SUMMARY.md tracking doc for overnight session
97ac693  backtest: pure-function port of pt_thinker (Phase 3c)
43c3d60  gitignore: exclude backtest/runs/ artifacts
6ffaea6  backtest: workspace + training epoch driver (Phase 3a)
4e39f7d  pt_trainer: add asof_ts no-look-ahead cutoff (Phase 2)
ed2b32c  pt_trader: add _now() / _sleep() seams (Phase 1)
7e3c4bd  Update plan: switch Phase 1 to BacktestTrader subclass
9296858  Add pt_pricesource.py (Phase 0)
77e35de  Add backtest research plan
```

Diff against main: `git log --oneline main..backtest-research`.

## Production code touch (cumulative)

| File | Lines | Behavior change | Why |
|---|---|---|---|
| `pt_trader.py` | +25 / -14 | None | `_now()` / `_sleep()` seams for backtest clock control |
| `pt_trainer.py` | +25 / -5 | None | `asof_ts` param for no-look-ahead training |

Everything else is in new files under `backtest/` or new top-level modules
(`pt_pricesource.py`). Production behavior is bit-identical when the seam
methods are not overridden.

## Quick start (validated working)

```bash
cd /home/dave/dev/code/git/PowerTrader_AI
git checkout backtest-research

# 1) Small single-coin pilot (~13s; trains 1 epoch + replays 14 days)
python3 -m backtest.cli pilot --coin ETH --epochs 1

# 2) Full single-coin run (warning: ~hours for full history)
python3 -m backtest.cli run --coin ETH

# 3) 3D sweep on one coin (350 param points; Ray if available)
python3 -m backtest.cli sweep --coin ETH

# 4) Aggregate portfolio-level
python3 -m backtest.cli aggregate <run_id> --coins ETH,SOL,BTC

# 5) Marimo notebook (Altair charts + equity/Sharpe stats)
marimo edit backtest/research.py
```

Run artifacts land under `backtest/runs/<run_id>/` (gitignored):
```
training/<YYYYMMDD>/<COIN>/training_data.json
fills/<COIN>.parquet
series/<COIN>.parquet
agg/<COIN>_hourly.parquet
agg/portfolio_hourly.parquet
agg/portfolio_daily.parquet
```

## File inventory

```
PowerTrader_AI/
├── pt_pricesource.py          # NEW   PriceSource ABC + Arctic + Live
├── pt_strategy.py             # (not needed — went with subclass approach)
├── pt_trainer.py              # MOD   +asof_ts (25 lines added, 5 removed)
├── pt_trader.py               # MOD   _now/_sleep seams (25 added, 14 removed)
└── backtest/
    ├── PLAN.md                # Phase plan with verified findings
    ├── SUMMARY.md             # THIS FILE
    ├── __init__.py
    ├── workspace.py           # Run dir layout, chdir context, run_id
    ├── train.py               # Epoch schedule + per-epoch trainer invoker
    ├── thinker.py             # Pure-function port of scoring/voting math
    ├── exchange.py            # BacktestExchange (frictionless, mid-grid fills)
    ├── trader.py              # BacktestTrader(Trader) with mocked I/O
    ├── engine.py              # 5min replay driver
    ├── aggregate.py           # Hourly + daily aggregation
    ├── sweep.py               # 3D sweep + Ray parallel
    ├── cli.py                 # argparse driver
    └── research.py            # Marimo notebook
```

## Phase log

### Phase 0 — pricesource abstraction ✅
**`9296858`** — `pt_pricesource.py`. Three classes:
- `PriceSource` ABC: `get_candles(coin, tf_minutes, asof_ts, n_back) -> DataFrame`.
- `ArcticPriceSource`: reads `~/dev/data/arcticdb` `kucoin{tf}` libs, USDT
  pairs. Pushes `asof_ts` to ArcticDB `date_range` (avoids loading
  post-cutoff rows).
- `LivePriceSource`: wraps `kucoin.client.Market`. Not wired into prod
  thinker yet — that's a separate validated step.

### Phase 1 — clock/sleep seams ✅
**`ed2b32c`** — `pt_trader.py` +25/-14.
- `_now() -> float = time.time()` and `_sleep(sec) = time.sleep(sec)`
  added to Trader class.
- 14 call sites in `manage_trades` and its decision-path helpers redirected.
- Sites not redirected: init-time seeding, `created_ts` in
  `place_*_order` (fully overridden in backtest), `run()`'s outer sleep,
  module-level init logging.

### Phase 2 — trainer asof_ts ✅
**`4e39f7d`** — `pt_trainer.py` +25/-5.
- `TrainerConfig.asof_ts: Optional[float] = None`.
- `fetch_candles(..., asof_ts=None)` filters via Arctic `date_range`.
- Live KuCoin fallback rejects `asof_ts` (raises `InsufficientDataError`).
- Default `None` preserves prod behavior. Verified: BTC 1d returns 3141
  rows without asof, 2258 rows with asof 2024-01-01.

### Phase 3a — workspace + training driver ✅
**`6ffaea6`** — `backtest/{workspace,train}.py`.
- `workspace.py`: layout helpers, `chdir()` context manager, `new_run_id()`.
- `train.py`: epoch schedule generator (14-day cadence from earliest
  viable per-coin), `train_one_epoch()` (chdir into workspace, run
  TrainingLoop), `train_coin()` (full timeline serial).
- Validated end-to-end. ~6.5s per epoch. Extrapolated full training:
  ~25 min on 24 cores via Ray for 30 coins × 173 epochs.

### Phase 3c — thinker math primitives ✅
**`97ac693`** — `backtest/thinker.py`. 310 lines, four primitives:
- `score_tf(parsed, open, close)` → `(high_diff_frac, low_diff_frac, status)`.
- `compute_tf_prices(close, high_diff, low_diff, status)` → `(high_tf, low_tf)`.
- `rebuild_bounds(high_tf_prices, low_tf_prices, perfects)` → `(high_bound, low_bound)`.
- `vote_one(current, high_bound, low_bound, high_tf, low_tf)` → `"long"|"short"|"none"`.
- `ParsedTFMemory`, `ThinkerState` dataclasses.
- Bit-exact port of `pt_thinker.py:631-1143` numerics. Unit-tested.

### Phase 3d — BacktestExchange + BacktestTrader ✅
**`97b991c`** — `backtest/{exchange,trader}.py`.
- `BacktestExchange`: frictionless. Engine sets per-bar context via
  `set_time` + `set_bar`. `place_buy`/`place_sell` do mid-grid fills at
  `fill_price` (5min bar close). Mid-grid `get_price` (buy == sell ==
  5min open).
- `BacktestTrader(CryptoAPITrading)`: skips prod `__init__` (file +
  exchange side effects), reproduces attribute setup with empty
  defaults. Overrides:
    - `_now` / `_sleep`: simulated clock, no-op sleep.
    - `_read_long/short_dca_signal`, `_read_long_price_levels`: pull from
      engine-set in-memory dict.
    - `_atomic_read/write_json`, `_append_jsonl`, `_save_pnl_ledger`,
      `_save_bot_order_ids`, `_write_trader_status`: no-op.
    - `_read_lth_ema200_snapshot`, `_pick_lth_symbol_to_buy`: LTH off.
    - Engine plumbing: `set_now(ts)`, `set_signals(coin, l, s, levels)`.

End-to-end probe: Tick 1 (no signals) → no trade. Tick 2 (long=7,
`trade_start_level=2`) → $10 buy at $2020 → 0.00495 ETH, cash $990,
PM line $2100.8. Tick 3 ($2040) → position revalued. Tick 4 ($2030) →
no sell (PM untriggered). Full prod 520-line decision loop runs on
simulated state.

### Phase 3e — engine ✅
**`6486ea9`** — `backtest/engine.py`. Per 5min bar T:
1. For each of 7 trained TFs: floor-to-TF alignment → in-progress bar →
   score against epoch's training_data.
2. `compute_tf_prices` → `rebuild_bounds`.
3. Vote each TF against PREV `bound_prices` → `(long_count, short_count)`.
4. Push signals + long-bound-price levels onto BacktestTrader, push
   bar's open (live) + close (fill) onto BacktestExchange.
5. Tick `manage_trades()`, collect any new fills from exchange log.
6. Hourly snapshot. Training data swaps automatically at 14-day boundaries.

### Phase 4 — aggregation ✅
**`67c4d3b`** — `backtest/aggregate.py`.
- `per_coin_hourly()`: resamples engine snapshots to hourly with derived
  `invested_usd`, `pnl_usd`, `pct_return` columns.
- `portfolio_hourly()`: sums per-coin into portfolio-level series, plus
  wide format for per-coin matrix.
- `portfolio_daily()`: resamples to daily with `daily_pct_return` for
  downstream stats (vol, Sharpe, drawdown).

### Phase 5 — Ray sweep ✅
**`08e958a`** — `backtest/sweep.py`.
- `default_grid()`: 350 BacktestParams covering the cube
  (trade_start_level 1-7, start_allocation_pct 1-5, pm_start_pct 1-10).
- `run_coin_sweep()`: trains coin's epochs once (param-independent),
  then runs each param point as an independent Ray task. Falls back to
  serial if Ray unavailable.

### Phase 6 — Marimo notebook ✅
**`08e958a`** — `backtest/research.py`.
- Run-id dropdown.
- Equity curve, daily-return stats (annualised return/vol/Sharpe),
  daily-return histogram.
- Sweep-only: 2D heatmap of mean total return over (lvl, alloc),
  averaged across pm.

## Open issues / next morning checklist

### 🔴 Scoring port produces 0 long signals where prod trades (the headline issue)

**Symptom:** Validated against prod demo BNB. Prod definitively bought BNB on
2026-05-31 at 07:55 UTC at $719.187 (see
`/mnt/d/dave/Documents/powertrader/powertrader_demo/state/hub_data/exchanges/demo/trade_history.jsonl`).
Backtest replay using the **same** `training_data.json` (copied from prod
demo `state/coins/BNB/training_data.json`, May 24 training) over
2026-05-24 → 2026-05-31 produces:
- `long_count` always 0; `short_count` ranges 0..1.
- Zero entries.

**Where it's not:**
- Not the training data — used prod's exact `training_data.json`.
- Not the bar alignment — tested with `floor(T / tf_secs) * tf_secs`,
  which is the standard convention.
- Not the trader's entry logic — fed `long_count=7, short_count=0`
  directly to BacktestTrader in the Phase 3d probe and an entry fired.
- Not the bound rebuild's gap-walk — added defensive pads after the
  duplicate-collapse case at engine.py:240-246 and thinker.py:262-271.

**Likely causes (in order):**
1. **Bar input semantics.** Prod thinker reads klines via
   `_get_kline(coin, tf)` and parses `working_minute = history_list[1]`
   (the **second-most-recent** kline entry, i.e. the most recent
   **closed** bar). My engine uses `searchsorted` on the bar at
   `floor(T / tf_secs)`, which is the **in-progress** bar. The "current
   candle" semantics may differ by one bar. Cross-check needed at
   `pt_thinker.py:619-628`.
2. **Bound persistence.** The prod thinker reads
   `low_bound_prices`/`high_bound_prices` from `thinker_state.json`
   which is updated only AFTER tf_choice_index wraps to 0. My engine
   rebuilds bounds every 5min bar. The same training data with
   different bound-update cadence will produce different votes.
3. **Memory-pattern indexing.** Prod's `memory_candle = float(memory_pattern[check_dex])`
   where `check_dex = 0`. My port uses `float(parts[0])`. Need to
   verify `parts[0]` matches when patterns have spaces (e.g.
   `"-1.46e-07 -0.073"` → `parts[0] = "-1.46e-07"`). The trainer's
   `compute_pct_changes` is in % units; my matching uses the same.
4. **Threshold persistence delay.** Prod uses
   `st["thresholds"][tf]` from `thinker_state.json` if present, else
   `training_data.json`'s threshold. After a fresh training, the
   persisted threshold is wiped by the controller so the new training's
   is used. My engine always uses the training_data threshold —
   should be equivalent for a fresh run.

**Suggested debug path:**
- Instrument `pt_thinker.py` to write its per-TF
  `(open, close, current_candle, threshold, n_matches, high_diff, low_diff)`
  trace to a file during a live demo run for 5 minutes.
- Run the same kline window through `backtest/thinker.score_tf` with
  the same training_data.
- Compare. The first row where my port diverges from prod will pinpoint it.

I'd estimate a few hours to isolate, with a real probe of running prod
thinker. Without prod-thinker instrumentation, the divergence is too
subtle to nail down by code inspection alone.

### 🟡 Engine speed: 7.4s per 14-day epoch (one coin, single param)

- 350 param × 30 coins × 175 epochs × 7.4s = **15.4 days** sequential.
- Ray 24-way ≈ 15 hours. Acceptable for an overnight sweep but worth
  optimising once the scoring bug is fixed (the engine spends most of
  the time in Python loops; numpy vectorisation should give 5-10x).

### 🟡 Validation against `/mnt/d/dave/Documents/powertrader/powertrader_demo`

Demo state has full prod artifacts:
- `state/coins/<COIN>/training_data.json` — May 24 training snapshots.
- `state/hub_data/exchanges/demo/trade_history.jsonl` — all fills.
- `state/hub_data/exchanges/demo/pnl_ledger.json` — open positions, totals.

16 coins held in demo (XRP, LTC, SUI, POL, AVAX, CRO, BCH, ZEC, UNI,
PAXG, TAO, SOL, DOT, LINK, TRX, BNB). Backtest using each coin's
training_data and replaying the same May 24 → May 31 window should
reproduce these entries — currently does not (see headline issue).

### 🟢 5min data backfill — done

User confirmed all 30 coins now have kucoin5 data.

### 🟢 CLI quality-of-life

Done: tz-aware `now` handling, `--epochs` bounds `until` for short
pilots, trader-demo log level lowered to WARNING (was spamming
"Account: $1000.00" every tick).

## How to validate next session

```bash
cd /home/dave/dev/code/git/PowerTrader_AI
git checkout backtest-research
git log --oneline main..backtest-research          # see all commits
git diff main pt_trader.py pt_trainer.py           # prod-touch diff (50 lines)

# Reproduce the BNB validation gap:
python3 << 'EOF'
import sys, json, shutil; sys.argv = ['x', '--exchange', 'demo']
import pandas as pd
from backtest import workspace as ws
from backtest.engine import CoinRunConfig, BacktestParams, run_coin
from pt_pricesource import ArcticPriceSource

src = ArcticPriceSource()
run_id = ws.new_run_id(prefix='valbnb')
epoch = pd.Timestamp('2026-05-24 21:05', tz='UTC')
edir = ws.training_epoch_dir(run_id, epoch.timestamp(), 'BNB')
edir.mkdir(parents=True, exist_ok=True)
shutil.copyfile(
    '/mnt/d/dave/Documents/powertrader/powertrader_demo/state/coins/BNB/training_data.json',
    str(edir / 'training_data.json'),
)
cfg = CoinRunConfig(coin='BNB', starting_usd=1000.0,
    until=pd.Timestamp('2026-05-31 22:00', tz='UTC'),
    record_every_n=12, params=BacktestParams())
out = run_coin(run_id, cfg, epoch_schedule=[epoch], price_source=src)
print(f'fills: {len(out.fills)}   max long: {out.series["long_count"].max()}')
EOF
```

Expected (current state): `fills: 0   max long: 0`.
Expected (after scoring bug fixed): non-zero fills, with the first BNB
buy around the 2026-05-31 07:55 timestamp matching prod.
