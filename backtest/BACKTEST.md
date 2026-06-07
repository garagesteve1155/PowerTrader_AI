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

---

## 3. (placeholder) Engine internals

To be written: master timeline construction, epoch swaps, watchdog
behaviour, snapshot cadence.

## 4. (placeholder) Resume + checkpoint

To be written: pickle layout, resume semantics, what happens after
a Ctrl-C.

## 5. (placeholder) Sweep parallelism

To be written: Ray task layout, when to use `--serial`, sizing
choices for the 3D grid.

## 6. (placeholder) CLI reference

To be written: subcommands, options matrix, monitoring recipes.
