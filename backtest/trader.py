"""
BacktestTrader — production Trader subclass with mocked I/O for replay.

Key design: skip the prod Trader.__init__ (too many file/exchange side
effects) and reproduce its attribute setup by hand using empty defaults.
The 520-line manage_trades decision loop is reused verbatim — overrides
intercept only at the I/O boundaries:

  - clock seam:        _now() / _sleep()   → simulated time
  - filesystem:        _atomic_read/write_json, _append_jsonl   → no-op
  - per-coin paths:    state/coins/<COIN>/<file>   → in-memory dicts
  - signal reads:      _read_long/short_dca_signal / _read_long_price_levels
                       → engine-populated state
  - notifications:     pt_notify.notify_*  → no-op
  - ledger persistence: _save_pnl_ledger   → no-op

Engine plumbing (not in the prod Trader interface):
  - set_now(ts)              advance simulated clock
  - set_signals(coin, l, s, levels)  feed thinker output for this bar
"""

from __future__ import annotations

import os
from typing import Optional

import logging

import pt_trader
from pt_trader import (
    CryptoAPITrading,
    DCA_LEVELS, TRAILING_GAP_PCT,
    PM_START_PCT_NO_DCA, PM_START_PCT_WITH_DCA,
    MAX_DCA_BUYS_PER_24H,
)
from pt_env import utcnow

# Backtest runs at ~6Hz of decisions per second; the prod trader logs an
# "Account:" snapshot every cycle. Mute it to keep output readable. Errors
# and warnings still propagate.
logging.getLogger("trader-demo").setLevel(logging.WARNING)
logging.getLogger("trader-kraken").setLevel(logging.WARNING)

from .exchange import BacktestExchange


class BacktestTrader(CryptoAPITrading):
    """Drives Trader.manage_trades on a simulated clock with in-memory I/O."""

    def __init__(self, exchange: BacktestExchange):
        # ── skip CryptoAPITrading.__init__ — replicate attribute setup ──
        self.exchange = exchange

        # Bookkeeping primitives
        self._skipped_coins: set = set()
        self._skip_throttle: dict = {}
        self.dca_levels_triggered: dict = {}
        self.dca_levels = list(DCA_LEVELS)

        # PM-trail state
        self.trailing_pm: dict = {}
        self.trailing_gap_pct = float(TRAILING_GAP_PCT)
        self.pm_start_pct_no_dca = float(PM_START_PCT_NO_DCA)
        self.pm_start_pct_with_dca = float(PM_START_PCT_WITH_DCA)
        self._last_trailing_settings_sig = (
            float(self.trailing_gap_pct),
            float(self.pm_start_pct_no_dca),
            float(self.pm_start_pct_with_dca),
        )

        # Bot-order tracking (empty in backtest — no real exchange history)
        self._bot_order_ids: dict = {}
        self._bot_order_ids_from_history: dict = {}
        self._bot_order_ids_mtime = None

        # PnL ledger — in-memory, no disk persistence
        self._pnl_ledger: dict = {
            "total_realized_profit_usd": 0.0,
            "last_updated_ts": utcnow(),
            "open_positions": {},
            "pending_orders": {},
            "lth_profit_bucket_usd": 0.0,
            "lth_last_buy": None,
        }

        # Cost basis populated lazily by Trader.calculate_cost_basis
        self.cost_basis: dict = {}

        # Cached snapshots
        self._last_good_bid_ask: dict = {}
        self._last_good_account_snapshot = {
            "total_account_value": None,
            "buying_power": None,
            "holdings_sell_value": None,
            "holdings_buy_value": None,
            "percent_in_trade": None,
        }

        # DCA rate-limit (24h rolling window)
        self.max_dca_buys_per_24h = int(MAX_DCA_BUYS_PER_24H)
        self.dca_window_seconds = 24 * 60 * 60
        self._dca_buy_ts: dict = {}
        self._dca_last_sell_ts: dict = {}

        # Ledger-seed gate (no orders to seed from in backtest)
        self._needs_ledger_seed_from_orders = False

        # Cadence trackers
        self._last_history_write_ts = 0.0
        self._last_notify_summary_ts = 0.0

        # ── Engine-plumbing state ─────────────────────────────────
        self._sim_now_ts: float = 0.0
        # signal_state[base] = (long_count, short_count, long_levels_list)
        self._signal_state: dict = {}

    # ------------------------------------------------------------------
    # Engine plumbing
    # ------------------------------------------------------------------

    def set_now(self, ts: float) -> None:
        self._sim_now_ts = float(ts)
        # keep exchange's view of "now" aligned so its order timestamps match
        if isinstance(self.exchange, BacktestExchange):
            self.exchange.set_time(ts)

    def set_signals(
        self, coin: str, long_count: int, short_count: int,
        long_levels: Optional[list] = None,
    ) -> None:
        self._signal_state[coin.upper()] = (
            int(long_count), int(short_count), list(long_levels or []),
        )

    # ------------------------------------------------------------------
    # Clock/sleep seam overrides
    # ------------------------------------------------------------------

    def _now(self) -> float:
        return self._sim_now_ts

    def _sleep(self, seconds: float) -> None:
        # All in-loop sleeps in prod are retry-pacers around I/O that can't
        # fail in backtest (in-memory exchange). Convert to no-op.
        return

    # ------------------------------------------------------------------
    # Signal-reader overrides
    # ------------------------------------------------------------------

    def _read_long_dca_signal(self, symbol: str) -> int:
        base = symbol.split("_")[0].upper()
        return self._signal_state.get(base, (0, 0, []))[0]

    def _read_short_dca_signal(self, symbol: str) -> int:
        base = symbol.split("_")[0].upper()
        return self._signal_state.get(base, (0, 0, []))[1]

    def _read_long_price_levels(self, symbol: str) -> list:
        base = symbol.split("_")[0].upper()
        return list(self._signal_state.get(base, (0, 0, []))[2])

    # ------------------------------------------------------------------
    # File I/O overrides — no-op for non-essential paths
    # ------------------------------------------------------------------

    def _atomic_read_json(self, path: str):
        # All ledger/state reads return None → prod helpers fall back to defaults.
        return None

    def _atomic_write_json(self, path: str, data: dict) -> None:
        return

    def _append_jsonl(self, path: str, obj: dict) -> None:
        return

    def _save_pnl_ledger(self) -> None:
        return

    def _save_bot_order_ids(self) -> None:
        return

    def _write_trader_status(self, status: dict) -> None:
        # Engine snapshots state from instance attrs directly — no disk write needed.
        return

    # ------------------------------------------------------------------
    # LTH EMA gate — backtest skips LTH allocation
    # ------------------------------------------------------------------

    def _read_lth_ema200_snapshot(self) -> dict:
        return {}

    def _pick_lth_symbol_to_buy(self) -> Optional[str]:
        return None
