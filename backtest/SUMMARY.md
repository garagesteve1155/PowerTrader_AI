# Backtest implementation — overnight session summary

Updated as I go. If I'm partway through when you wake up, the **Status**
section below reflects the latest committed state.

## Branch
`backtest-research` (off `main`). No pushes to remote. Diff against main:
`git log --oneline main..backtest-research`.

## Status

In progress. See the latest `git log --oneline main..` to see exact commit
list.

## Production code touch (cumulative)

| File | Lines | Behavior change | Why |
|---|---|---|---|
| `pt_trader.py` | +25 / -14 | None | `_now()` / `_sleep()` seams for backtest clock control |
| `pt_trainer.py` | +25 / -5 | None | `asof_ts` param for no-look-ahead training |

Everything else is in new files under `backtest/` or new top-level modules.
Production behavior is bit-identical when seam methods are not overridden.

## Plan recap

Walks 14-day epochs from each coin's earliest viable date (when it has 100
weekly bars). Per epoch: train via `pt_trainer` with `asof_ts=epoch_end`,
write training_data.json to a per-epoch workspace, then replay 5min bars
through the production trader (subclassed to swap I/O). Output: hourly
$pnl / $invested / $total / %return per coin and aggregated.

3D sweep over `(trade_start_level, start_allocation_pct, pm_start_pct)`.

## Quick start (when ready)

```bash
cd /home/dave/dev/code/git/PowerTrader_AI
git checkout backtest-research

# 1) Single-coin smoke run (small slice)
python3 -m backtest.cli pilot --coin ETH --epochs 3

# 2) Full single-coin run (all epochs, default params)
python3 -m backtest.cli run --coin ETH

# 3) 3D sweep on one coin (Ray)
python3 -m backtest.cli sweep --coin ETH

# 4) Marimo notebook
marimo edit backtest/research.py
```

(Update with actual CLI shape once implemented.)

## Phase log

### Phase 0 — pricesource abstraction ✅
**Commit `9296858`** — `pt_pricesource.py`. Three classes:
- `PriceSource` ABC: `get_candles(coin, tf_minutes, asof_ts, n_back) -> DataFrame`
- `ArcticPriceSource`: reads `~/dev/data/arcticdb` `kucoin{tf}` libs, USDT pairs. Pushes `asof_ts` to ArcticDB `date_range`.
- `LivePriceSource`: wraps `kucoin.client.Market`. Not wired into prod thinker yet.

### Phase 1 — clock/sleep seams ✅
**Commit `ed2b32c`** — `pt_trader.py` +25/-14.
- `_now() -> float = time.time()` and `_sleep(sec) = time.sleep(sec)` added to Trader class.
- 14 call sites in `manage_trades` and its decision-path helpers redirected to use the seams.
- Sites not redirected: init-time seeding, `created_ts` in place_*_order (overridden in backtest), `run()`'s outer sleep, module-level init.
- Behavior in prod: bit-identical to before.

### Phase 2 — trainer asof_ts ✅
**Commit `4e39f7d`** — `pt_trainer.py` +25/-5.
- `TrainerConfig.asof_ts: Optional[float] = None`
- `fetch_candles(..., asof_ts=None)` filters via Arctic `date_range` push-down.
- Live KuCoin fallback rejects `asof_ts` (raises `InsufficientDataError`).
- Default `None` preserves prod behavior. Verified: BTC 1d returns 3141 rows without asof, 2258 rows with asof 2024-01-01.

### Phase 3a — workspace + training driver ✅
**Commit `6ffaea6`** — `backtest/{__init__,workspace,train}.py`.
- `workspace.py`: directory layout helpers, `chdir()` context manager, `new_run_id()`.
- `train.py`: epoch schedule generator (14-day cadence from earliest viable per-coin), `train_one_epoch()` (chdir into workspace, run TrainingLoop), `train_coin()` (full timeline serial).
- **End-to-end validated**: ETH 2019-11-01 epoch trains in 6.5s, full 7-TF `training_data.json` written.
- Extrapolated full training: ~25 min on 24 cores via Ray for 30 coins × 173 epochs.

### Phase 3c — thinker math primitives ✅
**Commit `97ac693`** — `backtest/thinker.py`. 310 lines, four primitives:
- `score_tf(parsed, open, close)` → `(high_diff_frac, low_diff_frac, "active"|"inactive")`
- `compute_tf_prices(close, high_diff, low_diff, status)` → `(high_tf, low_tf)`
- `rebuild_bounds(high_tf_prices, low_tf_prices, perfects)` → `(high_bound, low_bound)`
- `vote_one(current, high_bound, low_bound, high_tf, low_tf)` → `"long"|"short"|"none"`
- `ParsedTFMemory`, `ThinkerState` dataclasses.
- Bit-exact port of `pt_thinker.py:631-1143` numerics. Validated via controlled unit cases.

### Phase 3d — BacktestExchange + BacktestTrader
*(in progress)*

### Phase 3e — engine
*(pending)*

### Phase 4 — aggregation
*(pending)*

### Phase 5 — Ray sweep
*(pending)*

### Phase 6 — Marimo notebook
*(pending)*

## Open issues / next morning checklist

(Populated as I encounter them.)

## How to validate

When you wake up, after pulling the latest commits:

```bash
cd /home/dave/dev/code/git/PowerTrader_AI
git checkout backtest-research
git log --oneline main..backtest-research   # see all commits
git diff main pt_trader.py pt_trainer.py    # prod-touch diff
```

Run any pilot/sweep commands in `Quick start` above (assuming I made it
that far). The `backtest/runs/` directory holds outputs.
