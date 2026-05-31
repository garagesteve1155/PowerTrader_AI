# PowerTrader_AI Backtest — Plan

Branch: `backtest-research`
Goal: explore a 3D parameter space `(trade_start_level, start_allocation_pct, pm_start_pct_no_dca)` against full historical performance, with downstream daily-returns timeseries.

## Confirmed assumptions
- The three swept params are **trader-only** (verified: no references in `pt_trainer.py` / `pt_thinker.py`). Training artifacts are reused across all 350 param points.
- `pm_start_pct_with_dca = pm_start_pct_no_dca` always (per user spec).
- Sweep dims: `7 × 5 × 10 = 350` points × N coins. Single training set per epoch reused 350 times.
- Training cadence: every 14 days.
- Training history: per-coin, starts after the coin has 100 weekly candles (≈ 700 days post-listing).
- Mid-price model: **decide at bar T `open`, fill at bar T `close`** (5-min slip, no look-ahead).
- Initial scope: 1–3 coins (BTC + ETH + SOL), full history, default params — pipeline validation before scaling.
- Frictionless: no fees, no spread.

## Architecture

```
                    ┌─────────────────────────┐
   PROD (untouched  │ pt_trader.py            │
   behavior)        │  └─ now calls           │
                    │     pt_strategy.decide_*│  ◄── pure functions
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │ pt_strategy.py (NEW)    │  ◄── extracted from pt_trader
                    │  decide_entry()         │
                    │  decide_dca()           │
                    │  decide_sell()          │
                    │  update_pm_trail()      │
                    └────────────┬────────────┘
                                 │
                                 │
            ┌────────────────────┼────────────────────┐
            ▼                                         ▼
┌────────────────────┐                ┌──────────────────────────┐
│ Live runtime       │                │ backtest/engine.py (NEW) │
│ (existing trader)  │                │  - walks 5min bars       │
└────────────────────┘                │  - replays decision fn   │
                                      │  - records fills         │
                                      └─────────┬────────────────┘
                                                │
                                                ▼
                                      ┌─────────────────────┐
                                      │ Ray-parallel sweep  │
                                      │ 350 params × N coins│
                                      └─────────┬───────────┘
                                                │
                                                ▼
                                      ┌─────────────────────┐
                                      │ Marimo notebook     │
                                      │ backtest/research.py│
                                      └─────────────────────┘
```

### Abstractions

1. **`pt_pricesource.py` (new module, shared between prod and backtest):**
   - `PriceSource` ABC: `get_candles(coin, tf_minutes, asof_ts, n_back) -> DataFrame`
   - `LivePriceSource`: wraps the existing KuCoin REST kline calls — production keeps using this. **Default for prod; behavior identical to current.**
   - `ArcticPriceSource`: reads from local ArcticDB (`kucoin5`, `kucoin60`, ..., `kucoin10080`) with optional `asof_ts` cutoff — backtest only.

2. **`pt_strategy.py` (new module, refactored out of `pt_trader.py`):**
   - Pure functions for trader decisions. No file I/O, no time.sleep, no globals.
   - Takes `(state, signals, prices, params) -> action`.
   - Production trader is refactored to call these. **Behavior must remain bit-identical** — validated by spot-checking against pre-refactor outputs.

3. **`pt_trainer.py` extension:**
   - Add `asof_ts: Optional[float] = None` arg to `train_for_coin`. When set, prevents loading any candle with timestamp ≥ asof. Backwards-compatible default.

## Phases

### Phase 0 — pricesource abstraction (small prod touch)
- New `pt_pricesource.py` with `PriceSource`, `LivePriceSource`, `ArcticPriceSource`.
- pt_thinker.py: thin shim that uses `LivePriceSource` by default. Behavior unchanged.
- No commit of strategy refactor yet — pricesource lands as standalone PR.
- **Validation:** run prod thinker for a few cycles, compare output files vs. main branch baseline.

### Phase 1 — strategy extraction (real prod touch, behavior-preserving)
- Pull entry / DCA / PM / sell logic out of `Trader.manage_trades` into pure functions in `pt_strategy.py`.
- Keep state explicit: pass dataclasses or dicts in/out, no instance attribute mutation inside the pure layer.
- `pt_trader.py` becomes the I/O / orchestration shell that calls these.
- **Validation:** run prod trader against demo exchange for a few hours, diff trader_status.json vs baseline. Goal: zero behavioral diff.

### Phase 2 — trainer asof support
- Add `asof_ts` to `train_for_coin` and `fetch_candles`. Default = None (current behavior).
- Add `backtest/train.py` driver: for each `(coin, asof_ts)`, invokes trainer in an isolated output dir.
- Ray task per `(coin, epoch)`.
- **Output:** `backtest/runs/<run_id>/training/<YYYYMMDD>/<coin>/training_data.json`

### Phase 3 — backtest engine (1–3 coins pilot)
- `backtest/engine.py` walks the 5min bar timeline per coin:
  - Determine current 14-day training epoch; load `training_data.json`.
  - For each 5min bar T:
    - Update signals using `pt_strategy` scoring fns against bars `[…, T-1]` + T.open.
    - Run trader decision (entry / DCA / sell / PM update) with swept params.
    - Fill at T.close.
  - Record every fill + per-bar position state.
- Single-coin, single-param run validates end-to-end.

### Phase 4 — outputs & aggregation
- Per (param_point, coin) — capture trade log + state at every 5min.
- Resample to **hourly**: `$pnl`, `$invested`, `$total_account_value`, `% return`.
- Aggregate across coins per param point → portfolio level.
- Derive **daily % return** series for downstream stats (vol, Sharpe, drawdown, etc.).
- Store as Parquet under `backtest/runs/<run_id>/results/`.

### Phase 5 — 3D sweep
- 350 param points × N coins. Ray task per `(param_point, coin)`. Embarrassingly parallel.
- For 30 coins × 350 points = 10500 tasks. With ~30s per task on local box that's ~88 hours single-threaded → ~3-4h on 24 cores.

### Phase 6 — Marimo research notebook
- `backtest/research.py`: interactive sweep + heatmap exploration.
- 3D slicing: 2D heatmaps holding one param fixed.
- Cross-coin correlation, drawdown, Sharpe panels.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Strategy refactor breaks prod | Bit-identity test against baseline before/after; demo exchange runs |
| Thinker logic depends on file state in ways hard to replicate | First port the pure scoring math, then audit residual file deps |
| Backtest survivorship bias (we know which coins survived) | Note in the writeup; not a fixable issue without delisting data |
| Memory weights drift over time in prod that's not captured by retraining alone | Verify: does prod adjust weights between training runs? If yes, model in backtest |
| 5min data gaps in KuCoin history | Handle via the existing `gaps_gt_2x` topup logic — log + interpolate forward |
| Ray + ArcticDB lmdb single-writer constraint | Use read-only Arctic handles in workers, or pre-stage per-coin parquet |

## Investigated follow-ups

**Thinker cadence.** Tight loop with `time.sleep(0.15)` between full coin passes. Scoring runs on every iteration using the latest (still-evolving) candle. `_get_kline` cached 30s. → 5min backtest sampling = snapshot at each 5min boundary. Captures bar-resolution dynamics; misses sub-5min signal flips. Acceptable per user spec.

**PM-trail cadence.** Trader loops ~0.5s. Trail updates every tick when peak/trough moves. → Backtest at 5min cadence: 1 update/bar vs prod's ~600/bar. Sharp intra-bar spikes could elevate prod trail-peak but be missed by backtest. Same roughness category as (1); refines with 1min later.

**Memory weight & threshold mutation between trainings — NONE.** `update_weight` is only called inside `pt_trainer.py`. Thinker reads `training_data.json` and never writes back. The `perfect_threshold` round-trip via `thinker_state.json` exists but is functionally a no-op (loaded value never mutated before re-write — confirmed by inspection of all 5 sites). Trader doesn't touch weights/thresholds. **Implication: a frozen per-epoch `training_data.json` is faithful to prod behavior within each 14-day window — per-epoch-training-once is sound.**

## Backtest data plane summary

- **5min `kucoin5`**: synthetic "live price tick" feed. Provides bar opens (for decision) and closes (for fills). NOT used as a training timeframe.
- **`kucoin60`..`kucoin10080`**: training timeframes, fed to `pt_trainer.train_for_coin(coin, asof_ts)` at each 14-day epoch and to the thinker's scoring at each 5min replay step.
- **No fees, no spread.** Pure mid-grid fills.

## File inventory (planned)

```
PowerTrader_AI/
├── pt_pricesource.py          # NEW   (Phase 0)
├── pt_strategy.py             # NEW   (Phase 1)
├── pt_trainer.py              # MOD   (Phase 2: +asof)
├── pt_trader.py               # MOD   (Phase 1: orchestration only)
├── pt_thinker.py              # MOD   (Phase 0: pricesource shim)
└── backtest/
    ├── PLAN.md                # THIS FILE
    ├── train.py               # Training driver
    ├── engine.py              # 5min replay engine
    ├── sweep.py               # Ray 3D sweep
    ├── research.py            # Marimo notebook
    └── runs/                  # Output artifacts (gitignored)
```
