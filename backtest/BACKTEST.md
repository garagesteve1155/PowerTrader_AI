# PowerTrader Backtest

Reference doc for the joint multi-coin backtest. Covers what the
engine does, the accounting methodology behind its outputs, and the
schemas it emits. Sections will be added as new capabilities ship.

---

## 1. Accounting methodology

The joint engine runs one shared `BacktestExchange` and one
`BacktestTrader` for all coins. At each snapshot boundary `t` we
record cash, per-coin position values, and total account value. From
those plus the fill log we derive the daily portfolio return and a
per-coin attribution that **sums to the total return exactly** (no
decomposition gap).

### 1.1 State variables

For each snapshot day `t`:

```
cash[t]              cash USD at end of day t
qty[c, t]            held quantity of coin c at end of day t
price[c, t]          mark price of coin c at end of day t
position_usd[c, t]   = qty[c, t] × price[c, t]            (mark-to-market)
V[t]                 = cash[t] + Σ_c position_usd[c, t]   (total portfolio)
```

`buy_notional[c, t]` and `sell_notional[c, t]` are the total USD that
moved into and out of cash for coin `c` during day `t` — derived from
the fills parquet by grouping fills on `(snapshot_day, coin, side)`.

### 1.2 Portfolio identity

By definition of `V`:

```
ΔV[t]    = V[t] − V[t−1]                                     (1)
         = Δcash[t] + Σ_c Δposition_usd[c, t]
```

Cash only moves via fills (frictionless backtest, no fees yet):

```
Δcash[t] = − Σ_c buy_notional[c, t]  +  Σ_c sell_notional[c, t]   (2)
```

Substituting (2) into (1) and grouping by coin:

```
ΔV[t] = Σ_c [ sell_notional[c, t] − buy_notional[c, t]
              + Δposition_usd[c, t] ]
      = Σ_c contrib_usd[c, t]
```

Both (1) and (2) are exact bookkeeping identities, so the
decomposition is exact too — no missing residual, no model assumption.

### 1.3 Per-coin contribution

```
contrib_usd[c, t] =   sell_notional[c, t]
                    − buy_notional[c, t]
                    + Δposition_usd[c, t]
```

Three terms, one identity, one row per `(coin, day)`.

### 1.4 What each term captures (typical scenarios)

Notation for the table: `p₀ = price[c, t−1]`, `p₁ = price[c, t]`,
`pᵇ` = average buy fill price during day `t`, `pˢ` = average sell
fill price during day `t`.

| Scenario | sell_not | buy_not | Δposition_usd | contrib_usd | What it represents |
|---|---|---|---|---|---|
| Pure hold, no fills | 0 | 0 | `qty × (p₁ − p₀)` | `qty × (p₁ − p₀)` | Pure mark-to-market |
| Open new position today | 0 | `qty × pᵇ` | `qty × p₁` | `qty × (p₁ − pᵇ)` | Unrealized PnL from buy → EOD |
| Close prior position today | `qty × pˢ` | 0 | `−qty × p₀` | `qty × (pˢ − p₀)` | Yesterday's mark → today's fill |
| Same-day round trip | `qty × pˢ` | `qty × pᵇ` | 0 | `qty × (pˢ − pᵇ)` | Pure realized PnL |
| DCA into existing position | 0 | `qᵃ × pᵇ` | `(qᵖ+qᵃ)p₁ − qᵖp₀` | `qᵖ(p₁−p₀) + qᵃ(p₁−pᵇ)` | Prior MTM + new-leg PnL |

`qᵖ` = qty held before today, `qᵃ` = qty added today.

The DCA case decomposes naturally into two pieces (prior position's
MTM **plus** the new leg's intraday PnL) without double-counting the
cash that came in.

The formula **does not need a separate realized vs unrealized split**.
Realized PnL flows through whenever `sell_notional > 0` and the
position drops; unrealized PnL flows through whenever the price moves
(via `Δposition_usd`). Both end up in the same `contrib_usd` term.

### 1.5 Cross-coin rebalance sanity

Suppose you sell $100 of A and buy $100 of B on day `t`, ending the
day with cash unchanged:

```
contrib_usd[A, t] = (+100) − 0 + Δposition[A]
contrib_usd[B, t] =      0 − 100 + Δposition[B]
```

Sum: `100 − 100 + Δposition[A] + Δposition[B]` = `ΔV[t]` ✓ — the
money is correctly tagged as leaving A and entering B, each leg's
intra-day mark-to-market priced separately.

### 1.6 Portfolio-level return + per-coin %

```
daily_pct_return[t] =  ΔV[t]              / V[t−1] × 100
contrib_pct[c, t]   =  contrib_usd[c, t]  / V[t−1] × 100
```

Same `V[t−1]` denominator → `Σ_c contrib_pct[c, t] ≡ daily_pct_return[t]`
exactly. The smoke run showed `1.82e−14 %` residual (float noise).

### 1.7 Cumulative caveats: additive % vs compound return

Two quantities that look like they should be equal but aren't:

```
total_return_compound  = (V_final / V_initial − 1) × 100      ← the truth
sum_of_daily_pct       = Σ_t daily_pct_return[t]              ← additive proxy
```

These differ by the cross-product terms of compounding. The
3-month BTC+ETH+SOL smoke had **0.2552% vs 0.2551%** — tiny here, but
the gap widens with longer / more-volatile runs.

For cumulative per-coin attribution, the additive form decomposes
cleanly:

```
total_contrib_pct[c]            =  Σ_t contrib_pct[c, t]
Σ_c total_contrib_pct[c]        =  sum_of_daily_pct
```

`sum_of_daily_pct` is what almost every portfolio-attribution tool
reports because it decomposes linearly per coin. If you want
reconciliation with the compound `(V_final/V_initial − 1)`, you need
log returns:

```
log_return[t]      = ln(V[t] / V[t−1])
Σ_t log_return[t]  = ln(V_final / V_initial)                  ← exact
log_contrib[c, t]  ≈ contrib_usd[c, t] / V[t−1]               ← first-order
```

The `log_contrib` form **is not an identity** — it's a first-order
Taylor expansion that misses the convexity term. Most attribution
reporting accepts the small linear-vs-log mismatch. If we ever want
exact reconciliation against compound return, we add a log-return
column to the daily parquet and switch the notebook's denominator
choice.

### 1.8 "Day" labelling convention

The engine takes snapshots at calendar-day boundaries (00:00 UTC). A
fill at `2023-09-01 02:35Z` is assigned to snapshot `2023-09-02 00:00Z`
because that's the next snapshot ≥ the fill time. So the row
**labelled** `2023-09-02` represents the state at the close of the
trading day `2023-09-01`.

Two reasonable conventions exist (label by day-start vs day-end);
this one is consistent with **"snapshot index = boundary after which
the day's events have been incorporated"**. The math doesn't change
either way — it's a labelling choice. To shift to a day-start label,
shift the index back by one snapshot interval after the daily build.

---

## 2. Output schemas

### 2.1 `runs/<run_id>/fills.parquet`

One row per fill across all coins.

| column | type | meaning |
|---|---|---|
| ts | float | Unix seconds at fill time |
| ts_iso | str | `YYYY-MM-DDTHH:MM:SSZ` UTC |
| side | str | `buy` or `sell` |
| symbol | str | canonical, e.g. `BTC_USD` |
| qty | float | filled quantity |
| price | float | fill price |
| notional | float | qty × price |
| tag | str/None | DCA / LTH / etc. (None for default) |
| order_id | str | uuid4 |
| cash_after | float | exchange cash AFTER this fill |

### 2.2 `runs/<run_id>/series.parquet`

One row per snapshot day (daily by default).

| column | type | meaning |
|---|---|---|
| ts | datetime | snapshot boundary (UTC) |
| ts_iso | str | same as ISO string |
| cash | float | end-of-day cash |
| total_position_usd | float | Σ_c position_usd[c, t] |
| total_account_value | float | cash + total_position_usd = V[t] |
| qty_<COIN> | float | qty[c, t] per active coin |
| position_usd_<COIN> | float | position_usd[c, t] per active coin |
| dca_rejects_<COIN>_no_price | int | DCA rejected because `_fill_prices[c]` is None or ≤ 0 |
| dca_rejects_<COIN>_zero_amount | int | DCA rejected because `dca_amount` ≤ 0 |
| dca_rejects_<COIN>_no_cash | int | DCA rejected because `dca_amount > cash` (stale-BP cache, see §2.2a) |

#### 2.2a Why `dca_rejects_<COIN>_*` exists

`pt_trader.manage_trades` fetches `buying_power` *once* at the top of
the tick, then loops over every held coin and may issue multiple buys
in that single tick. The gate at line ~3098 compares `dca_amount`
against the stale cached `buying_power`. By the time the loop reaches
later coins, real `self._cash` may have dropped below the cached
snapshot. The exchange correctly rejects (insufficient funds /
no fill price), the trader logs `"DCA buy FAILED for X"` and moves
on — no state corruption.

In the backtest this fires often (28 coins × 1% alloc × $10k = $100
per entry; first few coins drain the wallet within a single 5-min
tick). The log warning is suppressed by a backtest-only
`logging.Filter` in `backtest/trader.py`; the counter persists the
*signal*: per-coin per-day rejected-DCA pressure.

To measure cash-competition pressure post-hoc:
```python
import pandas as pd
s = pd.read_parquet("runs/<run_id>/series.parquet")
reject_cols = [c for c in s.columns if c.startswith("dca_rejects_")]
# Pivot into one row per coin, one column per reason
rows = []
for col in reject_cols:
    rest = col.removeprefix("dca_rejects_")
    for r in ("no_price", "zero_amount", "no_cash"):
        if rest.endswith("_" + r):
            rows.append((rest[: -(len(r)+1)], r, int(s[col].sum())))
df = pd.DataFrame(rows, columns=["coin","reason","total"])
pivot = df.pivot(index="coin", columns="reason", values="total").fillna(0).astype(int)
pivot["ALL"] = pivot.sum(axis=1)
print(pivot[pivot["ALL"] > 0].sort_values("ALL", ascending=False))
```
A coin that fails predominantly on `no_cash` is losing to cash
competition (other coins drained the wallet first in the same tick).
`no_price` indicates the coin's bar was never set this tick — either
a kucoin5 gap or it dropped out of `active`. `zero_amount` means the
trader sent a non-positive `dca_amount` (should never happen unless
`total_account_value` went non-positive — a bug signal).

### 2.3 `runs/<run_id>/portfolio_daily.parquet`

Derived from `fills.parquet` + `series.parquet` by
`portfolio_aggregate.write_portfolio_daily(run_id)`.

| column | type | meaning |
|---|---|---|
| ts (index) | datetime | snapshot boundary |
| ts_iso | str | ISO string |
| cash | float | cash[t] |
| total_position_usd | float | Σ_c position_usd[c, t] |
| total_account_value | float | V[t] |
| daily_pct_return | float | `ΔV[t] / V[t−1] × 100` |
| contrib_pct_<COIN> | float | per-coin attribution (`Σ_c = daily_pct_return`) |

### 2.4 `portfolio_aggregate.attribution_residual(daily)`

Returns `Σ_c contrib_pct − daily_pct_return` per day. Values
> 1e−6 % in absolute terms indicate an accounting bug, not float
noise. Use as a regression test after any aggregator change.

### 2.5 `runs/<parent>/sweep_results.parquet`

One row per `(lvl, alloc, pm)` point, written by `cmd_sweep` after
all sub-runs complete. The leaderboard / headline table.

| column | type | meaning |
|---|---|---|
| lvl | int | trade_start_level |
| alloc | float | start_allocation_pct |
| pm | float | pm_start_pct |
| sub_run_id | str | `<parent>/sweep/l<L>_a<A>_p<P>` |
| starting_value | float | V[0] for that sub-run |
| final_value | float | V[T] for that sub-run |
| pct_return | float | `(V[T] / V[0] − 1) × 100` |
| n_fills | int | total fills across all coins |
| n_snapshots | int | daily snapshot count |
| coins_active | int | coins with at least one viable epoch |
| coins_skipped | int | coins skipped (no training, no kucoin5 data) |
| error | str/None | exception message if the sub-run crashed |

### 2.6 `runs/<parent>/sweep_daily.parquet`

Long-format daily timeseries stitched from every sub-run's
`portfolio_daily.parquet`. One row per `(param-set, day)`. Use this
as the input to statistical analyses across the param grid.

| column | type | meaning |
|---|---|---|
| lvl | int | trade_start_level |
| alloc | float | start_allocation_pct |
| pm | float | pm_start_pct |
| sub_run_id | str | parent sub-run id |
| ts | datetime | snapshot boundary (UTC) |
| ts_iso | str | ISO string |
| total_account_value | float | V[t] for this sub-run on this day |
| daily_pct_return | float | portfolio daily return % |
| contrib_pct_<COIN> | float | per-coin daily contribution % (sums to daily_pct_return per row) |

Sample analyses this enables (pandas):
```python
import pandas as pd
df = pd.read_parquet("runs/<parent>/sweep_daily.parquet")

# Cumulative compound return per param-set
def compound(group):
    return ((1 + group["daily_pct_return"] / 100).prod() - 1) * 100
df.groupby(["lvl", "alloc", "pm"]).apply(compound)

# Vol per param-set
df.groupby(["lvl", "alloc", "pm"])["daily_pct_return"].std()

# Coin attribution heatmap (avg daily contribution by coin × param set)
contrib_cols = [c for c in df.columns if c.startswith("contrib_pct_")]
df.groupby(["lvl", "alloc", "pm"])[contrib_cols].mean()
```

---

## 3. Decision-price model (what the trader "sees")

The backtest walks a 5-min `kucoin5` grid. The live trader runs
continuously — call it ~5-second ticks inside every 5-min window.
The model below maps live's continuous view onto the coarser 5-min
grid without introducing look-ahead.

### 3.1 Three prices per bar, three jobs

For each 5-min bar with open `O`, high `H`, low `L`, close `C`,
the engine derives three prices and routes them to distinct uses:

| Use | Price | Why |
|---|---|---|
| **Buy decision** (entry vote, DCA gate) | `L` | Live's continuous trader would have seen the intra-bar dip; bar LOW is the most-aggressive "saw the dip" the 5-min grid can express |
| **Sell decision** (trailing PM, profit-take) | `H × (1 − trailing_gap_pct/100)` | The trailing-stop arms at the peak `H`, then fires when price drops `trailing_gap_pct` below the peak; this models the realistic slip from peak to fire |
| **Fill** (BUY notional and SELL proceeds) | `C` | Realistic execution delay between signal and placed order; using `C` keeps the model look-ahead-free since `C` IS the latest price at fill time |
| **MTM** (snapshot `total_account_value`) | `C` | Same as fill — fair "latest" mark, neither dip nor peak |

`trailing_gap_pct` is read from prod config (default 0.5 %).

No look-ahead: `L`, `H`, and `C` are all within the **current** bar.
The bar's open `O` is no longer used as a decision price (it was
under the prior "decide at open, fill at close" model).

### 3.2 Voting uses `live_price = L`

The TF voting loop in [`portfolio_engine.py`](portfolio_engine.py)
calls `bt_thinker.vote_one(live_price, …)`. With `live_price = L`,
`long_count` is maximised (price is at its most-below-low-bound) and
`short_count` is minimised. This mirrors what a live trader would
have computed during the intra-bar dip.

Consequence: entry signals fire more readily than under the old
open-based model. Since prod's continuous trader would have caught
those same dips tick-by-tick, this is closer to prod-parity.

### 3.3 Why not finer grids?

Sub-bar granularity is the principled fix to the "live ticks 60× per
5-min bar" sampling gap. We don't do it because:

- `kucoin1` libraries aren't backfilled for the full universe;
- it's a ~5× compute and storage hit on top of the joint engine;
- the `L`/`H × (1−gap)`/`C` model captures the **decision-time**
  extremes a tick-trader would have seen in the same window. What
  it doesn't capture is the **frequency** of trader checks within
  the bar (1 per bar in backtest vs ~60 in live), which is where
  the residual deployment gap (see §3.5) comes from.

### 3.4 Implementation

[`backtest/exchange.py`](exchange.py)
stores the three derived prices per coin in three dicts on
`BacktestExchange`. `get_price` returns `L` for buys and
`H × (1−gap)` for sells; `place_buy` / `place_sell` fill at `C`;
`get_account_value` marks-to-market at `C`.

The engine
[`backtest/portfolio_engine.py`](portfolio_engine.py)
computes the three prices per coin per bar from
`row[low/high/close]` and calls
`ex.set_bar(coin, buy_price, sell_price, fill_price)`.

### 3.5 Granularity ceiling — known fidelity gap vs live

On the 6-week window 2026-04-25 → 2026-06-08 with default params
(`lvl=2 alloc=1 pm=4` at `$10k` start), comparing backtest variants
against a live PowerTrader instance the user reported on:

| Model | Coins entered | Final deployed % | Notes |
|---|---|---|---|
| Open-based (decide at `O`, fill at `C`) | 12 / 28 | 16.5 % | Old model |
| Close-based (decide and fill at `C`) | 12 / 28 | 19.0 % | Modest improvement |
| **`L` / `H × (1−gap)` / `C`** | **11 / 28** | **24.9 %** | Current model |
| Live (reported) | ~28 / 28 | ~97 % | — |

The current model picks up most of the *decision-pricing* fidelity
gain available without changing grid. The residual gap (≈ 25 % vs
≈ 97 %) is dominated by **signal-firing frequency**: live's ~60
signal evaluations per 5-min bar give it many more chances to catch
transient long-vote conditions across coins, especially in fast
crashes (the user's live started 2026-04-25; the crash from 1 June
onward is what carried it from ~20 % → ~97 % deployment in the
final 7 days).

Therefore: **the backtest's deployment is a conservative lower
bound on what live can do at the same params.** Returns produced
by the backtest are achievable; live can additionally exploit
intra-bar signal frequency that the 5-min grid can't represent.

If we ever need to close this further: backfill `kucoin1` for the
full universe and stream that into the engine alongside `kucoin5`.

---

## 4. Training methodology — prod-parity, expanding window

The backtest reuses `pt_trainer.py` unchanged. Every (coin, epoch)
pair invokes the same `TrainingLoop(config).run()` that prod calls,
with one extra setting: `asof_ts`.

### 4.1 What `asof_ts` does

`asof_ts` is a Unix-seconds cutoff pushed all the way down to the
ArcticDB read at
[pt_trainer.py:208-210](../pt_trainer.py#L208-L210):

```python
if asof_ts is not None:
    cutoff = pd.Timestamp(asof_ts, unit="s", tz="UTC") - pd.Timedelta(microseconds=1)
    df = lib.read(symbol, date_range=(None, cutoff)).data
else:
    df = lib.read(symbol).data
```

So `asof_ts`:

- Prod (no cutoff): reads **all** stored candles, every TF, every
  call.
- Backtest (`asof_ts = epoch_start`): reads all candles **with index
  < epoch_start**, every TF, every call.

The `date_range=(None, cutoff)` form has no left bound — neither
prod nor backtest restricts how far back the trainer looks. The
trainer always sees all history available at the moment of
training.

### 4.2 Expanding-window: every epoch sees more

The schedule walks 14-day epochs from each coin's
`earliest_viable_asof` (the first timestamp at which the coin has
≥ 100 weekly bars — see
[backtest/train.py:40-67](train.py#L40-L67)) up to `until`. So:

- Epoch *t* trains on data from coin's earliest stored bar to
  `asof_ts(t)`.
- Epoch *t+1* trains on the same earliest bar to `asof_ts(t) + 14d`.
- Each subsequent epoch's training window is one epoch longer.

The window **expands** rather than slides — there is no rolling
horizon. The trainer is presumed to handle longer histories without
degradation; its own "growing-window phases" inside `TrainingLoop`
(pt_trainer.py:715-724) re-weight learning across the history each
call.

### 4.3 Prod-parity, item by item

| Aspect | Prod | Backtest | Identical? |
|---|---|---|---|
| Trainer module | `pt_trainer.py` | `pt_trainer.py` (reused) | ✓ |
| Algorithm | `TrainingLoop(config).run()` | `TrainingLoop(config).run()` | ✓ |
| Data source | ArcticDB `kucoin{tf}` (or `kraken{tf}` etc.) | Same ArcticDB libraries | ✓ |
| TF set | 7 TFs from `TRAIN_TF_NAMES` | Same | ✓ |
| Lookback | All stored data up to "now" | All stored data up to `asof_ts` | ✓ (cutoff only difference) |
| Min-candles gate | 100 weekly bars (`MIN_CANDLES`) | Same (and used by `earliest_viable_asof`) | ✓ |
| Output | `training_data.json` + `trainer_state.json` per coin | Same, written into `runs/<id>/training/<YYYYMMDD>/<COIN>/` | ✓ |
| Cadence | One-shot at trainer launch, scheduled by Airflow / cron | Every 14 days across the historical timeline | structural difference — backtest replays the cadence prod would have followed |

### 4.4 Things to verify before trusting epoch boundaries

When the engine swaps training data at an epoch boundary (line
~344 of [portfolio_engine.py](portfolio_engine.py)) it reads
`runs/<run_id>/training/<YYYYMMDD>/<COIN>/training_data.json` and
parses each TF's payload via `bt_thinker.parse_tf_training_data`.

- If the file doesn't exist, the engine sets `parsed_td[c] = None`
  and skips this coin for the rest of the epoch. **No on-the-fly
  training.** Coverage gaps in the training tree silently suppress
  trading. Check `runs/<run_id>/training/` per epoch directory
  after `train` to confirm full coverage before launching `run`.
- Sweep sub-runs read training from `runs/<parent>/training/` via
  `PortfolioRunConfig.training_run_id` (see
  [portfolio_engine.py:80-83](portfolio_engine.py#L80-L83)).

### 4.5 Cost note

A full-universe `train` over the default 28 coins × ~170 epochs is
≈ 25 min on a 24-core machine (one Ray task per (coin, epoch)).
Training is param-independent — one train run feeds unlimited
downstream `run` / `pilot` / `sweep` invocations.

---

## 5. Performance — optimisation phases + validation recipe

The engine has been progressively optimised through three phases.
Every phase produced **byte-identical** `fills.parquet`,
`series.parquet`, and `portfolio_daily.parquet` against the previous
phase's baseline (excluding `fills.order_id` which is a random
uuid4 per fill). The numbers below are from one fixed 1-month
benchmark pilot (28 coins, 8 641 master-grid bars, default params).

### 5.1 Benchmark progression

| Phase | What changed | Un-profiled wall | bars/s | Cumulative vs pre-2a |
|---|---|---|---|---|
| pre-2a | Original | ~360 s | 24 | 1.0× |
| **2a** | Hoist string parsing — `parse_tf_training_data` emits 5 numpy float64 arrays + 2 bool masks; `score_tf` consumes them | 209 s | 41 | 1.7× |
| **2b** | Replace pandas `.loc[T_pd]` with `dict[ts_ns → int]` master-grid lookup + numpy column arrays. Same for per-TF `searchsorted + iloc[pos]["open"]` → `np.searchsorted` on int64 ns + `np.float64[pos]` | 142 s | 61 | 2.5× |
| **3**  | `numba.njit` the four pure-numeric inner functions: `_score_tf_core`, `_compute_tf_prices_core`, `_vote_one_core`, `_rebuild_bounds_core`. Public string-based API preserved at thin Python wrapper level | **28 s** | **305** | **~13×** |

Full-history full-universe extrapolations from the post-3 rate:

| Workload | Pre-2a estimate | Post-3 estimate |
|---|---|---|
| Single run, ~800 k bars | ~10 h | **~45 min** |
| 27-point sweep on 24 cores | ~11 h | **~50 min** |
| Hypothetical `kucoin1` master grid (5× bars) | ~50 h (infeasible) | **~3.6 h** |

### 5.2 Dependencies

Phase 3 introduces `numba` (tested at 0.65.0). JIT compilation
adds a one-off ~350 ms cost on first call per process; `cache=True`
on each `@njit` writes the compiled artefact to disk so subsequent
runs in the same env skip recompilation.

### 5.3 Validation recipe (run this after any backtest engine change)

Two-step regression test that has caught every parquet-affecting bug
during the optimisation work:

1. **Baseline** — snapshot the current outputs into a side dir:
   ```bash
   SANDBOX=backtest/runs/profile_<id>
   mkdir -p backtest/runs/_baseline
   cp $SANDBOX/{fills,series,portfolio_daily}.parquet backtest/runs/_baseline/
   ```

2. **Re-run with the change**, then diff:
   ```python
   import pandas as pd, numpy as np
   def cmp(name, before_path, after_path, ignore=()):
       b = pd.read_parquet(before_path).drop(columns=list(ignore))
       a = pd.read_parquet(after_path ).drop(columns=list(ignore))
       if b.shape != a.shape:
           return False, f"shape mismatch {b.shape} vs {a.shape}"
       for col in b.columns:
           bv, av = b[col].values, a[col].values
           eq = ((bv == av) | (np.isnan(bv) & np.isnan(av))
                  if bv.dtype.kind == 'f' else (bv == av))
           if not eq.all():
               idx = int(np.argmax(~eq))
               return False, f"{col}[{idx}]: {bv[idx]!r} vs {av[idx]!r}"
       return True, "ok"

   for f in ("fills","series","portfolio_daily"):
       ig = ["order_id"] if f == "fills" else []
       ok, msg = cmp(f, f"backtest/runs/_baseline/{f}.parquet",
                       f"backtest/runs/profile_<id>/{f}.parquet", ignore=ig)
       print(f"{f}: {'✓' if ok else '❌'}  {msg}")
   ```

   `series` and `portfolio_daily` must match **exactly** (no exclusions).
   `fills` must match exactly **excluding `order_id`** — that column is
   a fresh uuid4 per fill in [exchange.py](exchange.py) so it differs
   between any two runs.

A passing diff is sufficient to land the change. A failing diff
points at the exact (col, row) of the first divergence.

### 5.4 Profiling on demand

`pilot` and `run` accept `--profile`. The flag wraps the
`run_portfolio()` call in `cProfile.Profile()`, dumps:

- `runs/<id>/engine.prof` (binary, for `snakeviz`, `tuna`, `pyprof2calltree`)
- `runs/<id>/engine.prof.top40.txt` (sorted by cumulative time)

Profile overhead is significant (~50-80 %) — never measure raw
throughput with `--profile` on. Use it only to find hot spots.

### 5.5 What's left, ranked by ROI

| Option | Effort | Un-profiled speedup | Risk |
|---|---|---|---|
| Cache `pt_env.get_config` reads inside `BacktestTrader` (visible in profile via `pt_env.coin_dir`, `pathlib` chain ~7 s/month) | 1 hour, monkey-patch in `BacktestTrader.__init__` | 10-15 % | low |
| Reduce heartbeat / stdout flush frequency from per-1000-bars to something coarser | trivial | 5-10 % under cProfile | none |
| Phase 4: stack per-coin `ThinkerState` into `(n_coins, n_tfs)` numpy arrays and njit the engine's outer coin loop | 2 days | 1.5-2× → ~15-20 s per month | **CHANGES `portfolio_checkpoint.pkl` FORMAT** — needs `_CHECKPOINT_VERSION` bump + migration path, or "old checkpoints unsupported, rerun" |

Phase 4 is the only one that touches a persisted format. The first
two are drop-in.

---

## 6. (placeholder) Engine internals

To be written: master timeline construction, epoch swaps, watchdog
behaviour, snapshot cadence.

## 7. (placeholder) Resume + checkpoint

To be written: pickle layout, resume semantics, what happens after
a Ctrl-C.

## 8. (placeholder) Sweep parallelism

To be written: Ray task layout, when to use `--serial`, sizing
choices for the 3D grid.

## 9. (placeholder) CLI reference

To be written: subcommands, options matrix, monitoring recipes.
