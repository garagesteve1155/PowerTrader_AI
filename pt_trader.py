"""
pt_trader.py — Live trading engine

FUNCTION
--------
Executes buy/sell orders on a configured exchange based on signals produced by
pt_thinker.py.  It manages the full lifecycle of each position: initial entry,
DCA (dollar-cost-averaging) top-ups, profit-margin trailing stops, and
long-term-holding (LTH) purchases funded from realised profits.

ALGORITHM
---------
Runs manage_trades() in a continuous loop (≈1–5 s cadence per coin):

  1. Refresh prices and account holdings from the exchange.
  2. For each coin:
     a. Read long_dca_signal / short_dca_signal from the thinker's output.
     b. If signal ≥ TRADE_START_LEVEL and no open position exists → place
        initial buy (START_ALLOC_PCT % of buying power).
     c. If an open position exists and the price has dropped through the next
        DCA level → place a DCA buy (DCA_MULTIPLIER × previous size),
        subject to MAX_DCA_BUYS_PER_24H.
     d. Once in profit (price above avg cost + PM threshold), activate a
        trailing stop.  When the trail is broken → sell entire position.
     e. On realised profit, optionally allocate LTH_PROFIT_ALLOC_PCT % to a
        market buy of the lowest-EMA-discounted LTH coin.
  3. Call self.exchange.tick() at the end of each loop iteration for periodic
     exchange-level maintenance (see Exchange Design below).

COMMUNICATION LINKS
--------------------
Reads (inputs from pt_thinker.py):
  state/coins/<SYM>/
    long_dca_signal.txt            — long vote count (thinker output)
    short_dca_signal.txt           — short vote count (thinker output)
    futures_long_profit_margin.txt — profit-margin target (thinker output)
    low_bound_prices.html          — predicted buy levels (thinker output)
  state/hub_data/
    runner_ready.json              — gate: only trade when thinker is ready
    lth_daily_ema200.json          — 200-day EMAs for LTH coin selection

Reads (config):
  pt_config.json                   — coins, DCA params, trailing gap, LTH %

Writes (outputs for pt_web.py / UI):
  state/hub_data/exchanges/<xk>/
    trader_status.json             — per-coin position snapshot (read by UI)
    trade_history.jsonl            — append-only fill ledger
    pnl_ledger.json                — open positions + realised P&L
    account_value_history.jsonl    — equity curve
    bot_order_ids.json             — order IDs owned by this bot
  state/hub_data/
    lth_daily_ema200.json          — (also written here for initial seed)

  In Trading mode a second set of the above files is written under
  exchanges/shadow/ by ShadowedExchange.tick() (see Exchange Design).

EXCHANGE DESIGN
---------------
All exchange interaction is behind the Exchange ABC (exchange_api.py).
The trading loop is completely exchange-agnostic: it calls place_buy(),
place_sell(), get_holdings(), get_price() — nothing else.

Three concrete types exist (exchange_paper.py, exchange_<name>.py):

  PaperExchange
    Frictionless simulated account: zero fees, zero spread, fills at
    KuCoin mid-price.  Persists state (balance, holdings, position cost
    basis) to hub_data/exchanges/<key>/exchange_state.json.

    Used in two roles:
      - Demo mode  : IS the primary account (key='demo').
      - Shadow role: lives inside ShadowedExchange (key='shadow').

  ShadowedExchange
    Wraps a real exchange + a PaperExchange shadow.  Every call to
    place_buy/place_sell is executed on the real exchange first, then
    automatically mirrored into the shadow at mid-price with zero fees.
    All trades are mirrored — LTH included — because the whole point of
    the shadow is an unbroken frictionless baseline.

    The delta between real and shadow quantifies friction (spread + fees)
    accumulated over time.

    Exposes all_accounts() → [self, self._shadow] so pt_web can display
    both accounts without knowing about the wrapper.

  KrakenExchange / BinanceExchange / RobinhoodExchange
    Thin HTTP adapters: auth, wire format, order polling.  No accounting
    or strategy logic.  Each declares key and display_name so callers
    never need to special-case exchange identity by string matching.

Exchange instantiation (load_exchange in exchange_api.py):
  --exchange demo    → PaperExchange(key='demo')
  --exchange kraken  → ShadowedExchange(KrakenExchange(), PaperExchange(key='shadow'))

  The trader never knows whether it holds a PaperExchange or a
  ShadowedExchange — the interface is identical.  Shadow mirroring,
  status writing, and account-value history are all handled inside
  ShadowedExchange.tick(), called at the end of each manage_trades()
  loop with a 5-minute internal throttle.

PROCESS ARCHITECTURE
--------------------
PowerTrader runs as three independent OS processes communicating solely
via the filesystem (state/ directory).  No shared memory, no sockets
between them.

  pt_thinker.py   — Neural signal generator.  Reads OHLCV data, runs
                    trained models, writes long/short signal files per coin.
                    Long-lived; can remain running across mode switches.

  pt_trader.py    — This file.  Reads signals, executes trades, writes
                    hub_data state files.  One process per exchange; in
                    Trading mode a single process runs with a ShadowedExchange
                    so both real and shadow state are written in one loop.

  pt_web.py       — FastAPI server.  Reads hub_data state files to serve the
                    UI; executes manual close/reset via the exchange adapters
                    directly.  Never writes to the files that pt_trader owns
                    (trader_status, trade_history, pnl_ledger) except on
                    explicit user action (close-coin, reset-all).

Rationale for file-based IPC:
  - Zero coupling between processes: each can be restarted independently.
  - Trivially inspectable: all state is human-readable JSON/JSONL on disk.
  - No message queue or broker to operate.
  - pt_web can read state even when pt_trader is stopped (staleness is
    visible via the timestamp field in trader_status.json).

One real exchange at a time:
  The design enforces a single active pt_trader process per run.  Running
  multiple traders against different real exchanges simultaneously is not
  supported; there is one shadow account and it must correspond to one
  real exchange to keep the frictionless comparison meaningful.
"""

import argparse
import datetime
import json
import math
import os
import shutil
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

import colorama
import pt_errors
from exchange_api import Exchange, OrderResult, load_exchange
from pt_env import PTEnv, utcnow, utc_to_ts
from pt_log import get_logger

# Initialize colorama
colorama.init(autoreset=True)


# -----------------------------
# Config + paths (via PTEnv)
# -----------------------------
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_pt_env = PTEnv(_PROJECT_DIR)

_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--exchange", default="demo")
EXCHANGE_KEY = _parser.parse_known_args()[0].exchange
log = get_logger(f"trader-{EXCHANGE_KEY}")

TRADER_STATUS_PATH = str(_pt_env.trader_status_path(EXCHANGE_KEY))
TRADE_HISTORY_PATH = str(_pt_env.trade_history_path(EXCHANGE_KEY))
PNL_LEDGER_PATH = str(_pt_env.pnl_ledger_path(EXCHANGE_KEY))
ACCOUNT_VALUE_HISTORY_PATH = str(_pt_env.account_history_path(EXCHANGE_KEY))
BOT_ORDER_IDS_PATH = str(_pt_env.bot_order_ids_path(EXCHANGE_KEY))
LTH_EMA200_PATH = str(_pt_env.ema200_path())


def _refresh_paths_and_symbols():
    """Hot-reload config while trader is running (delegates to PTEnv mtime cache)."""
    global crypto_symbols, TRADE_START_LEVEL, START_ALLOC_PCT, DCA_MULTIPLIER
    global DCA_LEVELS, MAX_DCA_BUYS_PER_24H, TRAILING_GAP_PCT
    global PM_START_PCT_NO_DCA, PM_START_PCT_WITH_DCA
    global LTH_PROFIT_ALLOC_PCT, LONG_TERM_SYMBOLS, EXCLUDED_COINS

    cfg = _pt_env.get_config()

    crypto_symbols = [str(c).upper() for c in cfg["coins"] if str(c).strip()]
    TRADE_START_LEVEL = int(cfg["trade_start_level"])
    START_ALLOC_PCT = float(cfg["start_allocation_pct"])
    DCA_MULTIPLIER = float(cfg["dca_multiplier"])
    DCA_LEVELS = [float(x) for x in cfg["dca_levels"]]
    MAX_DCA_BUYS_PER_24H = int(cfg["max_dca_buys_per_24h"])
    TRAILING_GAP_PCT = float(cfg["trailing_gap_pct"])
    PM_START_PCT_NO_DCA = float(cfg["pm_start_pct_no_dca"])
    PM_START_PCT_WITH_DCA = float(cfg["pm_start_pct_with_dca"])
    LTH_PROFIT_ALLOC_PCT = float(cfg["lth_profit_alloc_pct"])
    LONG_TERM_SYMBOLS = {
        str(v).upper().strip()
        for v in cfg.get("long_term_holdings") or []
        if str(v).strip()
    }
    EXCLUDED_COINS = {
        str(v).upper().strip()
        for v in cfg.get("excluded_coins") or []
        if str(v).strip()
    }


_refresh_paths_and_symbols()


class CryptoAPITrading:
    def __init__(self, exchange: Exchange):
        self.exchange = exchange

        self._skipped_coins: set = set()
        self._skip_throttle: dict = {}  # key -> last emit timestamp
        self.dca_levels_triggered = {}  # Track DCA levels for each crypto
        self.dca_levels = list(DCA_LEVELS)  # Hard DCA triggers (percent PnL)

        # --- Trailing profit margin (per-coin state) ---
        # Each coin keeps its own trailing PM line, peak, and "was above line" flag.
        self.trailing_pm = {}  # { "BTC": {"active": bool, "line": float, "peak": float, "was_above": bool}, . }
        self.trailing_gap_pct = float(TRAILING_GAP_PCT)  # % trail gap behind peak
        self.pm_start_pct_no_dca = float(PM_START_PCT_NO_DCA)
        self.pm_start_pct_with_dca = float(PM_START_PCT_WITH_DCA)

        # Track trailing-related settings so we can reset trailing state if they change
        self._last_trailing_settings_sig = (
            float(self.trailing_gap_pct),
            float(self.pm_start_pct_no_dca),
            float(self.pm_start_pct_with_dca),
        )

        # -----------------------------
        # Bot order ownership (needed so cost basis / DCA ignore user manual + long-term orders)
        # -----------------------------
        self._bot_order_ids = self._load_bot_order_ids()
        self._bot_order_ids_from_history = self._load_bot_order_ids_from_trade_history()

        # Hot-reload support: Hub can update bot_order_ids.json while trader is running.
        try:
            self._bot_order_ids_mtime = (
                os.path.getmtime(BOT_ORDER_IDS_PATH)
                if os.path.isfile(BOT_ORDER_IDS_PATH)
                else None
            )
        except Exception:
            self._bot_order_ids_mtime = None

        # GUI hub persistence
        self._pnl_ledger = self._load_pnl_ledger()
        log.info("Reconciling pending orders")
        self._reconcile_pending_orders()

        log.info("Calculating cost basis")
        self.cost_basis = (
            self.calculate_cost_basis()
        )  # Initialize cost basis at startup
        log.info("Initializing DCA levels")
        self.initialize_dca_levels()  # Initialize DCA levels based on historical buy orders

        # We must seed open_positions from the selected bot order IDs (Hub picker),
        # otherwise avg entry will only reflect buys recorded since this process started.
        self._needs_ledger_seed_from_orders = True

        # Cache last known bid/ask per symbol so transient API misses don't zero out account value
        self._last_good_bid_ask = {}

        # Cache last *complete* account snapshot so transient holdings/price misses can't write a bogus low value
        self._last_good_account_snapshot = {
            "total_account_value": None,
            "buying_power": None,
            "holdings_sell_value": None,
            "holdings_buy_value": None,
            "percent_in_trade": None,
        }

        # --- DCA rate-limit (per trade, per coin, rolling 24h window) ---
        self.max_dca_buys_per_24h = int(MAX_DCA_BUYS_PER_24H)
        self.dca_window_seconds = 24 * 60 * 60

        self._dca_buy_ts = {}  # { "BTC": [ts, ts, ...] } (DCA buys only)
        self._dca_last_sell_ts = {}  # { "BTC": ts_of_last_sell }
        self._seed_dca_window_from_history()

        self._last_history_write_ts = 0.0
        self._last_notify_summary_ts = 0.0

    def _atomic_read_json(self, path: str) -> Optional[dict]:
        """
        Read JSON dict safely. Returns None on any failure.
        (We keep it strict: only dict payloads count as valid.)
        """
        try:
            if not os.path.isfile(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            raw = (raw or "").strip()
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _atomic_write_json(self, path: str, data: dict) -> None:
        """
        Safer persistence:
        - write to .tmp
        - flush + fsync the file
        - copy current file to .bak (best-effort) BEFORE replacing
        - replace
        """
        try:
            tmp = f"{path}.tmp"
            bak = f"{path}.bak"

            # Write temp
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass

            # Backup existing (best-effort, only if it exists and is non-empty)
            try:
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    shutil.copy2(path, bak)
            except Exception:
                pass

            # Atomic replace
            os.replace(tmp, path)

            # Best-effort fsync directory entry (helps after power loss)
            try:
                dir_fd = os.open(os.path.dirname(path) or ".", os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception:
                pass

        except Exception as e:
            # If anything goes wrong, do NOT delete the old file.
            pt_errors.emit(
                f"trader-{EXCHANGE_KEY}",
                level="error",
                message=f"Failed to write state file {path}: {e}",
                detail=(
                    "A critical state file (PnL ledger, bot order IDs, or trader status) "
                    "could not be saved. The in-memory state is intact and trading continues, "
                    "but if the process restarts the last successfully saved state will be used, "
                    "which may be out of date. Check disk space and write permissions."
                ),
            )

    def _append_jsonl(self, path: str, obj: dict) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj) + "\n")
        except Exception as e:
            pt_errors.emit(
                f"trader-{EXCHANGE_KEY}",
                level="warning",
                message=f"Failed to write history entry to {path}: {e}",
                detail="A trade or account-value history record was not written. Trading continues normally; the missing entry will create a gap in history charts.",
            )

    # -----------------------------
    # BOT ORDER OWNERSHIP (for long-term holdings support)
    # -----------------------------
    def _load_bot_order_ids(self) -> Dict[str, set]:
        """Returns {"BTC": {order_id, ...}, ...}."""
        out: Dict[str, set] = {}
        try:
            raw = self._atomic_read_json(BOT_ORDER_IDS_PATH) or {}
            if not isinstance(raw, dict):
                raw = {}
            for k, v in raw.items():
                sym = str(k).upper().strip()
                if not sym:
                    continue
                if isinstance(v, list):
                    out[sym] = {str(x).strip() for x in v if str(x).strip()}
                elif isinstance(v, set):
                    out[sym] = set(v)
        except Exception as e:
            log.warning(f"could not load bot order IDs: {e}")
        return out

    def _save_bot_order_ids(self) -> None:
        try:
            data: Dict[str, list] = {}
            for sym, ids in (self._bot_order_ids or {}).items():
                if not sym or not ids:
                    continue
                data[str(sym).upper().strip()] = sorted(
                    {str(x).strip() for x in ids if str(x).strip()}
                )
            self._atomic_write_json(BOT_ORDER_IDS_PATH, data)
        except Exception as e:
            log.warning(f"could not save bot order IDs: {e}")

    def _load_bot_order_ids_from_trade_history(self) -> Dict[str, set]:
        """
        Best-effort: trade_history.jsonl contains ONLY bot trades.

        IMPORTANT:
        We only want order_ids that belong to the *current open trade* for each coin.
        So we take all bot trades AFTER the most recent bot SELL for that coin.

        This prevents any old, completed trades (which include sells) from "resetting" which buys
        are considered active, and it also prevents manual/long-term sells (which are not in
        trade_history.jsonl) from affecting bot cost basis or DCA stage reconstruction.
        """
        out: Dict[str, set] = {}
        try:
            if not os.path.isfile(TRADE_HISTORY_PATH):
                return out

            # First pass: find the most recent bot SELL timestamp per coin
            last_sell_ts: Dict[str, float] = {}
            rows: List[dict] = []
            with open(TRADE_HISTORY_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    side = str(obj.get("side", "")).lower().strip()
                    sym_full = str(obj.get("symbol") or "").strip().upper()
                    base = sym_full.split("_")[0].strip() if sym_full else ""
                    if not base:
                        continue

                    # Keep row so we can do a second pass without rereading the file
                    rows.append(obj)

                    if side != "sell":
                        continue
                    try:
                        _ts = obj.get("ts")
                        ts_f = utc_to_ts(_ts) if _ts else 0.0
                    except Exception:
                        ts_f = 0.0

                    prev = float(last_sell_ts.get(base, 0.0) or 0.0)
                    if ts_f > prev:
                        last_sell_ts[base] = ts_f

            # Second pass: keep only order_ids strictly AFTER the last bot sell for that coin
            for obj in rows:
                oid = str(obj.get("order_id") or "").strip()
                if not oid:
                    continue

                sym_full = str(obj.get("symbol") or "").strip().upper()
                base = sym_full.split("_")[0].strip() if sym_full else ""
                if not base:
                    continue

                try:
                    _ts = obj.get("ts")
                    ts_f = utc_to_ts(_ts) if _ts else 0.0
                except Exception:
                    ts_f = 0.0

                if ts_f > float(last_sell_ts.get(base, 0.0) or 0.0):
                    out.setdefault(base, set()).add(oid)

        except Exception as e:
            log.warning(f"could not load bot order IDs from trade history: {e}")
        return out

    def _mark_bot_order_id(self, base_symbol: str, order_id: Optional[str]) -> None:
        """
        Track which order_ids belong to the bot's *current open trade* for a coin.

        - On every bot buy/sell we add the id here.
        - When the bot fully exits a coin (filled SELL), we clear the coin's set.
        - Manual orders (long-term buys/sells) are never added, so they are ignored.
        """
        try:
            base = str(base_symbol).upper().strip()
            oid = str(order_id or "").strip()
            if not base or not oid:
                return
            self._bot_order_ids.setdefault(base, set()).add(oid)
            self._save_bot_order_ids()
        except Exception as e:
            log.warning(f"could not mark bot order ID {order_id}: {e}")

    def _clear_bot_order_ids_for_coin(self, base_symbol: str) -> None:
        """Clear the tracked order_ids for the current open trade on this coin (called after a filled bot SELL)."""
        try:
            base = str(base_symbol).upper().strip()
            if not base:
                return
            if isinstance(self._bot_order_ids, dict):
                self._bot_order_ids.pop(base, None)
            self._save_bot_order_ids()
        except Exception as e:
            log.warning(f"could not clear bot order IDs for {base_symbol}: {e}")

    def _is_bot_order_id(self, base_symbol: str, order_id: Optional[str]) -> bool:
        base = str(base_symbol).upper().strip()
        oid = str(order_id or "").strip()
        if not base or not oid:
            return False
        if oid in (
            self._bot_order_ids.get(base, set())
            if isinstance(self._bot_order_ids, dict)
            else set()
        ):
            return True
        if oid in (
            self._bot_order_ids_from_history.get(base, set())
            if isinstance(self._bot_order_ids_from_history, dict)
            else set()
        ):
            return True
        return False

    def _maybe_reload_bot_order_ids(self) -> bool:
        """
        If the GUI updates bot_order_ids.json while the trader is running,
        reload it and refresh any cached derived state (cost_basis + DCA history).
        Returns True if a reload occurred.
        """
        try:
            mtime = (
                os.path.getmtime(BOT_ORDER_IDS_PATH)
                if os.path.isfile(BOT_ORDER_IDS_PATH)
                else None
            )
        except Exception:
            mtime = None

        if mtime == getattr(self, "_bot_order_ids_mtime", None):
            return False

        # mtime changed (or file appeared/disappeared)
        self._bot_order_ids_mtime = mtime

        try:
            self._bot_order_ids = self._load_bot_order_ids()
        except Exception:
            self._bot_order_ids = {}

        try:
            # trade_history.jsonl is bot-only, so keep this fresh too
            self._bot_order_ids_from_history = (
                self._load_bot_order_ids_from_trade_history()
            )
        except Exception:
            self._bot_order_ids_from_history = {}

        # Refresh cached derived state so avg entry/cost basis uses the updated selection
        try:
            self.cost_basis = self.calculate_cost_basis()
        except Exception:
            pass

        try:
            self.initialize_dca_levels()
        except Exception:
            pass

        return True

    def _load_pnl_ledger(self) -> dict:
        """
        Load pnl_ledger.json with recovery:
        - try main file
        - if bad/empty/corrupt, try .bak
        - if still bad, try .tmp
        Only if all fail do we fall back to zeros.
        """

        def _upgrade(d: dict) -> dict:
            if not isinstance(d, dict):
                d = {}
            d.setdefault("total_realized_profit_usd", 0.0)
            d.setdefault("last_updated_ts", utcnow())
            d.setdefault(
                "open_positions", {}
            )  # { "BTC": {"usd_cost": float, "qty": float} }
            d.setdefault("pending_orders", {})  # { "<order_id>": {...} }
            d.setdefault("lth_profit_bucket_usd", 0.0)
            d.setdefault("lth_last_buy", None)
            return d

        # 1) primary
        data = self._atomic_read_json(PNL_LEDGER_PATH)
        if isinstance(data, dict):
            return _upgrade(data)

        # 2) backup
        bak_path = f"{PNL_LEDGER_PATH}.bak"
        data = self._atomic_read_json(bak_path)
        if isinstance(data, dict):
            # Restore recovered backup back to primary (best-effort)
            try:
                self._atomic_write_json(PNL_LEDGER_PATH, data)
            except Exception:
                pass
            return _upgrade(data)

        # 3) temp (sometimes survives if replace never happened)
        tmp_path = f"{PNL_LEDGER_PATH}.tmp"
        data = self._atomic_read_json(tmp_path)
        if isinstance(data, dict):
            try:
                self._atomic_write_json(PNL_LEDGER_PATH, data)
            except Exception:
                pass
            return _upgrade(data)

        # 4) final fallback (true reset)
        return _upgrade(
            {
                "total_realized_profit_usd": 0.0,
                "last_updated_ts": utcnow(),
                "open_positions": {},
                "pending_orders": {},
                "lth_profit_bucket_usd": 0.0,
                "lth_last_buy": None,
            }
        )

    def _save_pnl_ledger(self) -> None:
        try:
            self._pnl_ledger["last_updated_ts"] = utcnow()
            self._atomic_write_json(PNL_LEDGER_PATH, self._pnl_ledger)
        except Exception:
            pass

    # -----------------------------
    # Long-term holdings auto-buy (profit allocation -> EMA200 chooser)
    # -----------------------------
    def _read_lth_ema200_snapshot(self) -> dict:
        """
        Reads hub_data/lth_daily_ema200.json written by pt_thinker.py:
          {"ts": ..., "coins": {"BTC": {"ema200":..., "price":..., "pct_from_ema200":...}, ...}}
        Returns: {"BTC": pct_float, ...}
        """
        out = {}
        try:
            payload = self._atomic_read_json(LTH_EMA200_PATH) or {}
            coins = payload.get("coins", {}) if isinstance(payload, dict) else {}
            if not isinstance(coins, dict):
                return out
            for sym, row in coins.items():
                s = str(sym).upper().strip()
                if not s:
                    continue
                if not isinstance(row, dict):
                    continue
                pct = row.get("pct_from_ema200", None)
                try:
                    pct_f = float(pct)
                except Exception:
                    continue
                out[s] = pct_f
        except Exception as e:
            log.warning(f"could not read LTH EMA snapshot: {e}")
        return out

    def _pick_lth_symbol_to_buy(self) -> Optional[str]:
        """
        Pick the coin from LONG_TERM_SYMBOLS that is furthest % below daily 200 EMA.
        If none are below (pct>=0), pick the one closest to EMA (smallest positive pct).
        Returns base symbol like "BTC" or None.
        """
        try:
            syms = sorted(
                {
                    str(x).upper().strip()
                    for x in (LONG_TERM_SYMBOLS or set())
                    if str(x).strip()
                }
            )
            if not syms:
                return None

            pct_map = self._read_lth_ema200_snapshot()
            scored = []
            for s in syms:
                if s in pct_map:
                    scored.append((float(pct_map[s]), s))

            if not scored:
                # no EMA snapshot yet; just pick first configured symbol
                return syms[0]

            # Most negative = furthest below EMA200
            below = [(pct, s) for (pct, s) in scored if pct < 0.0]
            if below:
                below.sort(key=lambda t: t[0])  # most negative first
                return below[0][1]

            # None below: pick smallest positive (closest above EMA200)
            scored.sort(key=lambda t: abs(t[0]))
            return scored[0][1]
        except Exception as e:
            log.warning(f"LTH symbol picker failed: {e}")
            return None

    def _lth_market_buy_for_usd(self, base_symbol: str, usd_amount: float) -> bool:
        try:
            sym = str(base_symbol).upper().strip()
            if not sym:
                return False
            try:
                usd_amount = float(usd_amount or 0.0)
            except Exception:
                usd_amount = 0.0
            if usd_amount < 0.50:
                return False

            canonical = f"{sym}_USD"
            try:
                min_cost = self.exchange.get_min_order_cost(canonical)
            except Exception:
                min_cost = 0.0
            if min_cost > 0 and usd_amount < min_cost:
                reason = f"LTH: ${usd_amount:.2f} below min order ${min_cost:.2f}"
                log.debug(f"LTH skip {sym}: {reason}")
                self._record_skip(canonical, reason)
                return False

            result = self.place_buy_order(canonical, float(usd_amount), tag="LTH")
            return result is not None and result.state == "filled"
        except Exception:
            return False

    def _maybe_process_lth_profit_allocation(self, realized_profit_usd: float) -> None:
        """
        Called on each FILLED bot SELL (wins or losses), AND may be called periodically with 0.0
        to spend the existing LTH bucket if it's already >= $0.50.

        - Takes user-selected % (LTH_PROFIT_ALLOC_PCT)
        - If that portion >= $0.50: buy immediately using the WHOLE portion (+ any bucket already saved)
        - Else: add to bucket; when bucket reaches $0.50+ buy using the WHOLE bucket and reset to 0

        IMPORTANT:
        - The bucket is affected by *all* realized outcomes (wins and losses).
          Losses reduce the bucket (down to a floor of $0.00).
        """
        try:
            pct = float(LTH_PROFIT_ALLOC_PCT or 0.0)
        except Exception:
            pct = 0.0

        if pct <= 0.0:
            return

        if not (LONG_TERM_SYMBOLS or set()):
            return

        try:
            rp = float(realized_profit_usd or 0.0)
        except Exception:
            rp = 0.0

        # Note: rp may be 0.0 when called from the main loop; in that case, we just
        # check whether the saved bucket is ready to spend.
        alloc = rp * (pct / 100.0)

        try:
            bucket = float(
                (self._pnl_ledger or {}).get("lth_profit_bucket_usd", 0.0) or 0.0
            )
        except Exception:
            bucket = 0.0

        # Apply this trade's allocation (can be negative; can also be 0.0).
        prev_bucket = bucket
        bucket = bucket + float(alloc)

        # Never allow the bucket to go below $0.
        if bucket < 0.0:
            bucket = 0.0

        if rp != 0.0 and alloc != 0.0:
            log.info(
                f"LTH: realized ${rp:.2f} × {pct:.0f}% = ${alloc:.2f} allocated to bucket (was ${prev_bucket:.2f}, now ${bucket:.2f})"
            )

        spend_now = 0.0

        # Spec rule: if a single trade's allocated portion >= $0.50, spend that whole portion (plus any bucket)
        # Note: for losses alloc will be negative, so this branch won't trigger.
        if alloc >= 0.50:
            spend_now = float(alloc) + float(prev_bucket)
            bucket = 0.0
            log.info(
                f"LTH: single allocation ${alloc:.2f} >= $0.50 — spending ${spend_now:.2f} (allocation + bucket)"
            )
        else:
            if bucket >= 0.50:
                spend_now = float(bucket)
                bucket = 0.0
                log.info(
                    f"LTH: bucket reached ${spend_now:.2f} — spending accumulated bucket"
                )
            elif rp != 0.0:
                log.debug(f"LTH: bucket ${bucket:.2f} below $0.50 — accumulating")

        # Persist bucket update even if we don't buy yet
        self._pnl_ledger["lth_profit_bucket_usd"] = float(bucket)
        self._save_pnl_ledger()

        if spend_now < 0.50:
            return

        pick = self._pick_lth_symbol_to_buy()
        if not pick:
            # can't pick a coin; put it back into bucket so it isn't lost
            self._pnl_ledger["lth_profit_bucket_usd"] = float(bucket + spend_now)
            self._save_pnl_ledger()
            log.info(f"LTH: no eligible coin — returning ${spend_now:.2f} to bucket")
            return

        # Pre-check min order — keep accumulating silently if bucket can't cover it yet
        try:
            min_cost = self.exchange.get_min_order_cost(f"{pick}_USD")
        except Exception:
            min_cost = 0.0
        if min_cost > 0 and spend_now < min_cost:
            self._pnl_ledger["lth_profit_bucket_usd"] = float(bucket + spend_now)
            self._save_pnl_ledger()
            return

        pct_map = self._read_lth_ema200_snapshot()
        pct_from_ema = pct_map.get(pick, None)

        log.info(
            f"LTH: buying ${spend_now:.2f} of {pick}"
            + (
                f" ({pct_from_ema:+.1f}% from EMA200)"
                if pct_from_ema is not None
                else ""
            )
        )
        ok = self._lth_market_buy_for_usd(pick, spend_now)
        if ok:
            self._pnl_ledger["lth_profit_bucket_usd"] = 0.0
            self._pnl_ledger["lth_last_buy"] = {
                "ts": utcnow(),
                "symbol": pick,
                "usd": float(spend_now),
                "pct_from_ema200": (
                    float(pct_from_ema) if pct_from_ema is not None else None
                ),
            }
            self._save_pnl_ledger()
            log.info(f"LTH: buy filled — {pick} ${spend_now:.2f}")
        else:
            # failed buy -> restore funds to bucket so they're not lost
            self._pnl_ledger["lth_profit_bucket_usd"] = float(bucket + spend_now)
            self._save_pnl_ledger()
            log.warning(
                f"LTH: buy FAILED for {pick} — returning ${spend_now:.2f} to bucket"
            )
            self._record_skip(
                f"{pick}_USD", f"LTH: buy ${spend_now:.2f} of {pick} failed"
            )

    # -----------------------------
    # Ledger seeding from selected bot order IDs
    # -----------------------------
    def _rebuild_open_position_from_selected_bot_buys(
        self, base_symbol: str, tradable_qty: float
    ) -> None:
        """
        Rebuild self._pnl_ledger["open_positions"][SYM] from the *selected bot BUY orders* (order IDs),
        ignoring ANY intervening manual orders (buys/sells) that are not bot-owned.

        This is the missing piece that fixes:
          - avg entry / cost basis ignoring selected buys that happened before an unrelated sell
          - avg entry reflecting only buys recorded since this process started
        """
        try:
            sym = str(base_symbol).upper().strip()
            if not sym or sym == "USDC":
                return

            try:
                tradable_qty = float(tradable_qty or 0.0)
            except Exception:
                tradable_qty = 0.0
            if tradable_qty < 0.0:
                tradable_qty = 0.0

            # If we have no bot IDs for this coin, we can't rebuild anything.
            has_any_ids = False
            try:
                if sym in (self._bot_order_ids or {}) and (
                    self._bot_order_ids.get(sym) or set()
                ):
                    has_any_ids = True
                if sym in (self._bot_order_ids_from_history or {}) and (
                    self._bot_order_ids_from_history.get(sym) or set()
                ):
                    has_any_ids = True
            except Exception:
                has_any_ids = False

            if not has_any_ids:
                return

            # Pull orders and keep ONLY filled bot-owned BUY orders.
            symbol_full = f"{sym}_USD"
            orders = self.get_orders(symbol_full)
            results = orders.get("results", []) if isinstance(orders, dict) else []
            if not isinstance(results, list):
                results = []

            # Collect all selected bot order IDs for this coin
            all_selected_ids = set()
            try:
                all_selected_ids |= set(self._bot_order_ids.get(sym, set()) or set())
            except Exception:
                pass
            try:
                all_selected_ids |= set(
                    self._bot_order_ids_from_history.get(sym, set()) or set()
                )
            except Exception:
                pass

            buys = []
            seen_oids = set()
            for o in results:
                try:
                    if str(o.get("state", "")).lower() != "filled":
                        continue
                    if str(o.get("side", "")).lower() != "buy":
                        continue
                    oid = str(o.get("id") or "").strip()
                    if not oid:
                        continue
                    if not self._is_bot_order_id(sym, oid):
                        continue

                    seen_oids.add(oid)

                    qty, avg_px, notional_usd, fees_usd = (
                        self._extract_amounts_and_fees_from_order(o)
                    )
                    qty = float(qty or 0.0)
                    if qty <= 0.0:
                        continue

                    # Prefer true notional; otherwise compute from avg price.
                    if notional_usd is None or float(notional_usd or 0.0) <= 0.0:
                        if avg_px is None or float(avg_px or 0.0) <= 0.0:
                            continue
                        notional_usd = float(avg_px) * qty

                    fees = float(fees_usd or 0.0)
                    cost = float(notional_usd) + fees

                    created = str(o.get("created_at") or "")
                    buys.append((created, qty, cost))
                except Exception:
                    continue

            # Supplement with trade_history for orders that aged out of the exchange API
            missing_ids = all_selected_ids - seen_oids
            if missing_ids:
                hist_fills = self._get_fills_from_trade_history(sym, missing_ids)
                for oid, fill in hist_fills.items():
                    if float(fill.get("qty", 0) or 0) <= 0:
                        continue
                    created = (
                        datetime.datetime.utcfromtimestamp(fill["ts"]).isoformat()
                        if fill.get("ts")
                        else ""
                    )
                    cost = fill["notional_usd"] + fill["fees_usd"]
                    buys.append((created, fill["qty"], cost))

            if not buys:
                return

            # Oldest -> newest for deterministic FIFO-style trimming to tradable_qty
            buys.sort(key=lambda x: x[0] or "")

            # If tradable_qty is 0, clear ledger position for this coin.
            if tradable_qty <= 0.0:
                self._pnl_ledger.setdefault("open_positions", {}).pop(sym, None)
                self._save_pnl_ledger()
                return

            qty_used = 0.0
            cost_used = 0.0
            for _, q, c in buys:
                if qty_used >= tradable_qty - 1e-12:
                    break
                remaining = tradable_qty - qty_used
                if q <= remaining + 1e-12:
                    qty_used += q
                    cost_used += c
                else:
                    # Partial fill usage of this order’s lot
                    ratio = remaining / q if q > 0 else 0.0
                    qty_used += remaining
                    cost_used += c * ratio

            if qty_used <= 0.0:
                self._pnl_ledger.setdefault("open_positions", {}).pop(sym, None)
                self._save_pnl_ledger()
                return

            self._pnl_ledger.setdefault("open_positions", {})[sym] = {
                "qty": float(qty_used),
                "usd_cost": float(cost_used),
            }
            self._save_pnl_ledger()
        except Exception:
            log.exception("rebuild open position from selected orders failed")
            pt_errors.emit(
                f"trader-{EXCHANGE_KEY}",
                level="error",
                message="Failed to rebuild open position from selected bot orders",
                detail="Cost basis and DCA stage for one or more coins may be wrong until the next full reseed. Check bot order ID selection in the Hub.",
            )

    def _bot_net_qty_from_selected_orders(self, base_symbol: str) -> Optional[float]:
        """
        Compute the bot's current *in-trade* qty for a coin for the current trade:

        IMPORTANT:
          - The GUI selection popup includes ONLY the bot's START BUY + DCA BUY orders.
          - The user does NOT select sell orders.

        Therefore:
          selected_buys_qty = sum(filled BUY qty for order IDs in bot_order_ids.json)
          bot_sells_qty      = sum(filled SELL qty for bot-owned orders from trade_history.jsonl)
                              (restricted to sells that occurred after the earliest selected buy)

          in_trade_qty = max(0, selected_buys_qty - bot_sells_qty)

        This stays independent of total exchange holdings; any leftover beyond in_trade_qty
        is treated as long-term/manual.

        Returns None if exchange data is unavailable (caller should preserve existing ledger).
        """
        try:
            sym = str(base_symbol).upper().strip()
            if not sym or sym == "USDC":
                return 0.0

            symbol_full = f"{sym}_USD"
            orders = self.get_orders(symbol_full)
            results = orders.get("results", []) if isinstance(orders, dict) else []
            if not isinstance(results, list):
                results = []

            # Selected BUY order IDs (from GUI) are in self._bot_order_ids[sym]
            selected_ids = set()
            try:
                selected_ids = set(self._bot_order_ids.get(sym, set()) or set())
            except Exception:
                selected_ids = set()

            # Bot-owned history IDs (from trade_history.jsonl) are in self._bot_order_ids_from_history[sym]
            hist_ids = set()
            try:
                hist_ids = set(
                    self._bot_order_ids_from_history.get(sym, set()) or set()
                )
            except Exception:
                hist_ids = set()

            # If we have no results AND no selected_ids, we can't compute anything
            if not results and not selected_ids:
                return None

            # Pass 1: sum selected BUY fills and find earliest selected BUY created_at
            selected_buy_qty = 0.0
            earliest_selected_buy_created = None
            seen_selected_oids = set()

            # We also cache filled sells (created_at, qty) to apply the earliest-selected-buy cutoff
            filled_bot_sells = []

            for o in results:
                try:
                    if str(o.get("state", "")).lower() != "filled":
                        continue

                    oid = str(o.get("id") or "").strip()
                    if not oid:
                        continue

                    side = str(o.get("side") or "").lower().strip()
                    qty, _avg = self._extract_fill_from_order(o)
                    try:
                        qty = float(qty or 0.0)
                    except Exception:
                        qty = 0.0
                    if qty <= 0.0:
                        continue

                    created = str(o.get("created_at") or "")

                    # Selected orders are BUY-only
                    if side == "buy" and oid in selected_ids:
                        seen_selected_oids.add(oid)
                        selected_buy_qty += qty
                        if created:
                            if (
                                earliest_selected_buy_created is None
                                or created < earliest_selected_buy_created
                            ):
                                earliest_selected_buy_created = created

                    # Sells are NOT selected; infer bot sells from history IDs
                    if side == "sell" and oid in hist_ids:
                        filled_bot_sells.append((created, qty))
                except Exception:
                    continue

            # Supplement with trade_history for selected buy IDs that aged out of the exchange API
            missing_buy_ids = selected_ids - seen_selected_oids
            if missing_buy_ids:
                hist_fills = self._get_fills_from_trade_history(sym, missing_buy_ids)
                for oid, fill in hist_fills.items():
                    qty = float(fill.get("qty", 0) or 0)
                    if qty <= 0:
                        continue
                    selected_buy_qty += qty
                    created = (
                        datetime.datetime.utcfromtimestamp(fill["ts"]).isoformat()
                        if fill.get("ts")
                        else ""
                    )
                    if created:
                        if (
                            earliest_selected_buy_created is None
                            or created < earliest_selected_buy_created
                        ):
                            earliest_selected_buy_created = created

            # If the user selected no buys, bot owns zero in-trade qty for this coin.
            if selected_buy_qty <= 0.0:
                return 0.0

            # Pass 2: subtract bot sells that occurred after the earliest selected buy
            bot_sell_qty = 0.0
            cutoff = earliest_selected_buy_created
            for created, qty in filled_bot_sells:
                try:
                    if cutoff and created and created < cutoff:
                        continue
                    bot_sell_qty += float(qty or 0.0)
                except Exception:
                    continue

            net = float(selected_buy_qty) - float(bot_sell_qty)
            if net < 0.0:
                net = 0.0
            return float(net)
        except Exception:
            return 0.0

    def _seed_open_positions_from_selected_orders(self, holdings_list: list) -> None:
        """
        Seed/re-seed open_positions from selected bot order IDs for all held assets.

        The bot-owned (in-trade) qty is computed ONLY from selected bot orders:
          in_trade_qty = filled_bot_buys_qty - filled_bot_sells_qty

        Long-term/manual qty is automatic:
          long_term_qty = max(0, total_exchange_qty - in_trade_qty)
        """
        try:
            if not isinstance(holdings_list, list):
                return

            for h in holdings_list:
                try:
                    asset = str(h.get("asset_code", "")).upper().strip()
                    if not asset or asset == "USDC":
                        continue

                    try:
                        total_qty = float(h.get("total_quantity", 0.0) or 0.0)
                    except Exception:
                        total_qty = 0.0
                    if total_qty < 0.0:
                        total_qty = 0.0

                    in_trade_qty = self._bot_net_qty_from_selected_orders(asset)

                    # None means exchange data unavailable — preserve existing ledger entry
                    if in_trade_qty is None:
                        continue

                    # Never claim more bot inventory than actually exists in holdings.
                    tradable_qty = min(float(total_qty), float(in_trade_qty))
                    if tradable_qty < 0.0:
                        tradable_qty = 0.0

                    self._rebuild_open_position_from_selected_bot_buys(
                        asset, tradable_qty
                    )
                except Exception:
                    continue
        except Exception:
            log.exception("seed open positions from selected orders failed")
            pt_errors.emit(
                f"trader-{EXCHANGE_KEY}",
                level="error",
                message="Failed to seed open positions from selected bot orders",
                detail="Position cost basis and DCA stages may be incorrect. The issue will resolve on the next successful reseed triggered by selection changes.",
            )

    def _trade_history_has_order_id(self, order_id: str) -> bool:
        try:
            if not order_id:
                return False
            if not os.path.isfile(TRADE_HISTORY_PATH):
                return False
            with open(TRADE_HISTORY_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if str(obj.get("order_id", "")).strip() == str(order_id).strip():
                        return True
        except Exception:
            return False
        return False

    def _get_fills_from_trade_history(
        self, base_symbol: str, order_ids: set
    ) -> Dict[str, dict]:
        """Look up fill data from trade_history.jsonl for specific order IDs.

        Returns {order_id: {"qty": float, "notional_usd": float, "fees_usd": float, "ts": float}}
        for each matched order_id found in the local history file.
        """
        out: Dict[str, dict] = {}
        try:
            if not order_ids or not os.path.isfile(TRADE_HISTORY_PATH):
                return out
            sym_prefix = f"{str(base_symbol).upper().strip()}_"
            with open(TRADE_HISTORY_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    oid = str(obj.get("order_id") or "").strip()
                    if oid not in order_ids:
                        continue
                    sym = str(obj.get("symbol") or "").upper()
                    if not sym.startswith(sym_prefix):
                        continue
                    try:
                        qty = float(obj.get("qty", 0) or 0)
                        notional = float(obj.get("notional_usd", 0) or 0)
                        fees = (
                            float(obj.get("fees_usd", 0) or 0)
                            if obj.get("fees_usd") is not None
                            else 0.0
                        )
                        _ts = obj.get("ts")
                        ts = utc_to_ts(_ts) if _ts else 0.0
                        if qty > 0 and notional > 0:
                            out[oid] = {
                                "qty": qty,
                                "notional_usd": notional,
                                "fees_usd": fees,
                                "ts": ts,
                            }
                    except Exception:
                        continue
        except Exception as e:
            log.warning(f"could not read trade history fills: {e}")
        return out

    def _get_buying_power(self) -> float:
        try:
            bp = self.exchange.get_buying_power()
            return float(bp) if bp is not None else 0.0
        except Exception as e:
            log.warning(f"could not fetch buying power: {e}")
            pt_errors.emit(
                f"trader-{EXCHANGE_KEY}",
                level="warning",
                message=f"Buying power fetch failed: {e}",
                detail="The trader will see $0 buying power this cycle and skip new entries. It will retry on the next iteration.",
            )
        return 0.0

    def _extract_fill_from_order(self, order: dict) -> tuple:
        """Returns (filled_qty, avg_fill_price). avg_fill_price may be None."""
        try:
            execs = order.get("executions", []) or []
            total_qty = 0.0
            total_notional = 0.0
            for ex in execs:
                try:
                    q = float(ex.get("quantity", 0.0) or 0.0)
                    p = float(ex.get("effective_price", 0.0) or 0.0)
                    if q > 0.0 and p > 0.0:
                        total_qty += q
                        total_notional += q * p
                except Exception:
                    continue

            avg_price = (
                (total_notional / total_qty)
                if (total_qty > 0.0 and total_notional > 0.0)
                else None
            )

            # Fallbacks if executions are not populated yet
            if total_qty <= 0.0:
                for k in (
                    "filled_asset_quantity",
                    "filled_quantity",
                    "asset_quantity",
                    "quantity",
                ):
                    if k in order:
                        try:
                            v = float(order.get(k) or 0.0)
                            if v > 0.0:
                                total_qty = v
                                break
                        except Exception:
                            continue

            if avg_price is None:
                for k in ("average_price", "avg_price", "price", "effective_price"):
                    if k in order:
                        try:
                            v = float(order.get(k) or 0.0)
                            if v > 0.0:
                                avg_price = v
                                break
                        except Exception:
                            continue

            return float(total_qty), (
                float(avg_price) if avg_price is not None else None
            )
        except Exception:
            return 0.0, None

    def _extract_amounts_and_fees_from_order(self, order: dict) -> tuple:
        """
        Returns (filled_qty, avg_fill_price, notional_usd, fees_usd).

        FIX:
        Your P&L mismatch comes from deriving USD notional by summing executions
        (quantity * effective_price). That often differs by 1–2 cents from the USD
        Robinhood uses in the app and in actual account balance changes.

        Source of truth for the app/accounting is the order-level fill summary:
          - average_price
          - filled_asset_quantity

        We compute:
          notional_usd = round_to_cents(average_price * filled_asset_quantity)
        and only fall back to executions if the order-level fields are missing.
        """

        def _fee_to_float(v: Any) -> float:
            try:
                if v is None:
                    return 0.0
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    return float(v)
                if isinstance(v, list):
                    return float(sum(_fee_to_float(x) for x in v))
                if isinstance(v, dict):
                    for k in ("usd_amount", "amount", "value", "fee", "quantity"):
                        if k in v:
                            try:
                                return float(v[k])
                            except Exception:
                                continue
                return 0.0
            except Exception:
                return 0.0

        def _to_decimal(x: Any) -> Decimal:
            try:
                if x is None:
                    return Decimal("0")
                return Decimal(str(x))
            except Exception:
                return Decimal("0")

        def _usd_cents(d: Decimal) -> Decimal:
            # match settled USD granularity (cents)
            return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        try:
            # ----- fees (unchanged logic) -----
            execs = order.get("executions", []) or []
            fee_total = 0.0
            fee_found = False

            for ex in execs:
                try:
                    for fk in ("fee", "fees", "fee_amount", "fee_usd", "fee_in_usd"):
                        if fk in ex:
                            fee_found = True
                            fee_total += _fee_to_float(ex.get(fk))
                except Exception:
                    continue

            for fk in ("fee", "fees", "fee_amount", "fee_usd", "fee_in_usd"):
                if fk in order:
                    fee_found = True
                    fee_total += _fee_to_float(order.get(fk))

            fees_usd = float(fee_total) if fee_found else None

            # ----- filled qty + avg price (prefer order-level fields) -----
            avg_p_raw = order.get("average_price", None)
            filled_q_raw = order.get("filled_asset_quantity", None)

            avg_p_d = _to_decimal(avg_p_raw)
            filled_q_d = _to_decimal(filled_q_raw)

            avg_fill_price = float(avg_p_d) if avg_p_d > 0 else None
            filled_qty = float(filled_q_d) if filled_q_d > 0 else 0.0

            notional_usd = None

            # ----- USD notional (SOURCE OF TRUTH) -----
            if avg_p_d > 0 and filled_q_d > 0:
                notional_usd = float(_usd_cents(avg_p_d * filled_q_d))

            # ----- fallback only if order-level fields missing -----
            if notional_usd is None:
                total_notional_d = Decimal("0")
                total_qty_d = Decimal("0")
                for ex in execs:
                    try:
                        q_d = _to_decimal(ex.get("quantity", None))
                        p_d = _to_decimal(ex.get("effective_price", None))
                        if q_d > 0 and p_d > 0:
                            total_qty_d += q_d
                            total_notional_d += q_d * p_d
                    except Exception:
                        continue

                if total_qty_d > 0 and avg_fill_price is None:
                    try:
                        avg_fill_price = float(total_notional_d / total_qty_d)
                    except Exception:
                        pass

                if total_notional_d > 0:
                    notional_usd = float(_usd_cents(total_notional_d))

                if filled_qty <= 0.0 and total_qty_d > 0:
                    filled_qty = float(total_qty_d)

            return (
                float(filled_qty),
                (float(avg_fill_price) if avg_fill_price is not None else None),
                notional_usd,
                fees_usd,
            )

        except Exception:
            return 0.0, None, None, None

    def _reconcile_pending_orders(self) -> None:
        """
        If the hub/trader restarts mid-order, we keep minimal order metadata on disk and
        finish the accounting once the order shows as terminal on the exchange.
        """
        MAX_RETRIES = 10
        try:
            pending = self._pnl_ledger.get("pending_orders", {})
            if not isinstance(pending, dict) or not pending:
                return

            log.info(f"[Reconcile] {len(pending)} pending order(s)")
            retries = 0

            while True:
                pending = self._pnl_ledger.get("pending_orders", {})
                if not isinstance(pending, dict) or not pending:
                    break

                progressed = False

                for order_id, info in list(pending.items()):
                    try:
                        if self._trade_history_has_order_id(order_id):
                            log.info(
                                f"[Reconcile] {order_id[:12]}… found in history, removing"
                            )
                            self._pnl_ledger["pending_orders"].pop(order_id, None)
                            self._save_pnl_ledger()
                            progressed = True
                            continue

                        symbol = str(info.get("symbol", "")).strip()
                        side = str(info.get("side", "")).strip().lower()

                        if not symbol or not side or not order_id:
                            self._pnl_ledger["pending_orders"].pop(order_id, None)
                            self._save_pnl_ledger()
                            progressed = True
                            continue

                        log.info(
                            f"[Reconcile] querying exchange for {order_id[:12]}… ({symbol} {side})"
                        )
                        result = self.exchange.get_order_result(symbol, order_id)
                        if not result:
                            continue

                        if result.state != "filled":
                            log.info(
                                f"[Reconcile] {order_id[:12]}… state={result.state}, discarding"
                            )
                            self._pnl_ledger["pending_orders"].pop(order_id, None)
                            self._save_pnl_ledger()
                            progressed = True
                            continue

                        log.info(f"[Reconcile] {order_id[:12]}… filled, recording")
                        self._record_trade(
                            side=side,
                            symbol=symbol,
                            qty=result.filled_qty,
                            price=result.avg_price,
                            notional_usd=result.notional_usd,
                            fees_usd=result.fees_usd,
                            avg_cost_basis=info.get("avg_cost_basis", None),
                            pnl_pct=info.get("pnl_pct", None),
                            tag=info.get("tag", None),
                            order_id=order_id,
                        )

                        self._pnl_ledger["pending_orders"].pop(order_id, None)
                        self._save_pnl_ledger()
                        progressed = True

                    except Exception:
                        continue

                if not progressed:
                    retries += 1
                    if retries >= MAX_RETRIES:
                        remaining = self._pnl_ledger.get("pending_orders", {})
                        for oid, info in list(remaining.items()):
                            sym = info.get("symbol", "?")
                            log.warning(
                                f"[Reconcile] giving up on {oid[:12]}… ({sym}) after {MAX_RETRIES} retries"
                            )
                        self._pnl_ledger["pending_orders"] = {}
                        self._save_pnl_ledger()
                        break
                    log.warning(
                        f"[Reconcile] no progress, retrying ({retries}/{MAX_RETRIES})"
                    )
                    time.sleep(1)

        except Exception:
            log.exception("pending order reconciliation failed")

    def _record_skip(self, symbol: str, reason: str) -> None:
        """Emit a skipped-buy warning to the error panel (throttled to once per 10 min per symbol+reason)."""
        key = f"{symbol}|{reason}"
        now = time.time()
        if now - self._skip_throttle.get(key, 0.0) < 600:
            return
        self._skip_throttle[key] = now
        pt_errors.emit("trader", f"{symbol}: {reason}", level="warning")

    def _record_trade(
        self,
        side: str,
        symbol: str,
        qty: float,
        price: Optional[float] = None,
        notional_usd: Optional[float] = None,
        fees_usd: Optional[float] = None,
        avg_cost_basis: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        tag: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> None:
        """
        Minimal local ledger for GUI:
        - append trade_history.jsonl
        - update pnl_ledger.json open_positions + realized profit

        IMPORTANT CHANGE:
        - If fees_usd is missing (None), we fall back to subtracting $0.02 from the *trade's*
          realized profit (SELL) so totals match the real account.
        """
        ts = utcnow()
        side_l = str(side or "").lower().strip()
        base = str(symbol or "").upper().split("_")[0].split("-")[0].strip()
        tag_u = str(tag or "").upper().strip()

        # Ensure ledger keys exist (back-compat)
        try:
            if not isinstance(self._pnl_ledger, dict):
                self._pnl_ledger = {}
            self._pnl_ledger.setdefault("total_realized_profit_usd", 0.0)
            self._pnl_ledger.setdefault("open_positions", {})
            self._pnl_ledger.setdefault("pending_orders", {})
            self._pnl_ledger.setdefault("lth_profit_bucket_usd", 0.0)
            self._pnl_ledger.setdefault("lth_last_buy", None)
        except Exception:
            pass

        realized = None
        position_cost_used = None
        position_cost_after = None

        fees_missing = fees_usd is None
        fee_val_actual = float(fees_usd) if fees_usd is not None else 0.0

        # Per-trade fallback: only apply on SELL realized profit if fees were missing.
        fee_fallback = 0.02 if (fees_missing and side_l == "sell") else 0.0

        notional = None
        if notional_usd is not None:
            try:
                notional = float(notional_usd)
            except Exception:
                notional = None

        if notional is None and price is not None:
            try:
                notional = float(price) * float(qty)
            except Exception:
                notional = None

        # Best-effort net USD (does NOT include fallback fee; fallback is only applied to realized profit).
        net_usd = None
        try:
            if notional is not None:
                if side_l == "buy":
                    net_usd = -(float(notional) + float(fee_val_actual))
                elif side_l == "sell":
                    net_usd = float(notional) - float(fee_val_actual)
        except Exception:
            net_usd = None

        # Update PnL ledger unless this is an LTH-tagged trade (those must not affect bot PnL / positions)
        try:
            if tag_u != "LTH" and base and base != "USDC":
                open_pos = self._pnl_ledger.setdefault("open_positions", {})

                pos = open_pos.get(base)
                if not isinstance(pos, dict):
                    pos = {"usd_cost": 0.0, "qty": 0.0}
                    open_pos[base] = pos

                # BUY: increase cost + qty
                if side_l == "buy":
                    try:
                        if net_usd is not None and float(net_usd) < 0.0:
                            usd_used = -float(net_usd)
                            pos["usd_cost"] = (
                                float(pos.get("usd_cost", 0.0) or 0.0) + usd_used
                            )
                            pos["qty"] = float(pos.get("qty", 0.0) or 0.0) + max(
                                0.0, float(qty)
                            )
                            self._save_pnl_ledger()
                    except Exception:
                        pass

                # SELL: decrease cost/qty proportionally, compute realized profit
                elif side_l == "sell":
                    try:
                        pos_qty = float(pos.get("qty", 0.0) or 0.0)
                        pos_cost = float(pos.get("usd_cost", 0.0) or 0.0)

                        q = max(0.0, float(qty or 0.0))
                        if pos_qty > 0.0 and q > 0.0:
                            frac = min(1.0, q / pos_qty)
                        else:
                            frac = 1.0

                        cost_used = pos_cost * frac
                        remaining_qty = pos_qty - q
                        # Snap to zero if residual qty is dust — prevents float
                        # precision leaking stale cost into the next trade cycle.
                        if remaining_qty <= 1e-8:
                            pos["usd_cost"] = 0.0
                            pos["qty"] = 0.0
                        else:
                            pos["usd_cost"] = pos_cost - cost_used
                            pos["qty"] = remaining_qty

                        position_cost_used = float(cost_used)
                        position_cost_after = float(pos.get("usd_cost", 0.0) or 0.0)

                        usd_got = None
                        try:
                            if notional is not None:
                                usd_got = float(notional) - float(fee_val_actual)
                            elif net_usd is not None:
                                usd_got = float(net_usd)
                        except Exception:
                            usd_got = None

                        if usd_got is not None:
                            realized = (
                                float(usd_got) - float(cost_used) - float(fee_fallback)
                            )
                            self._pnl_ledger["total_realized_profit_usd"] = float(
                                self._pnl_ledger.get("total_realized_profit_usd", 0.0)
                                or 0.0
                            ) + float(realized)

                        # Clean up tiny dust
                        if (
                            float(pos.get("qty", 0.0) or 0.0) <= 1e-12
                            or float(pos.get("usd_cost", 0.0) or 0.0) <= 1e-6
                        ):
                            open_pos.pop(base, None)

                        self._save_pnl_ledger()

                        # Keep in-memory cost_basis aligned with the ledger for fallbacks/plots.
                        try:
                            pos2 = (
                                self._pnl_ledger.get("open_positions", {}) or {}
                            ).get(base, None)
                            if isinstance(pos2, dict):
                                self.cost_basis[base] = {
                                    "total_cost": float(
                                        pos2.get("usd_cost", 0.0) or 0.0
                                    ),
                                    "total_quantity": float(
                                        pos2.get("qty", 0.0) or 0.0
                                    ),
                                }
                        except Exception:
                            pass

                    except Exception:
                        pass
        except Exception:
            log.exception("PnL ledger update failed in _record_trade")
            pt_errors.emit(
                f"trader-{EXCHANGE_KEY}",
                level="error",
                message="PnL ledger update failed after trade record",
                detail="Realized profit accounting, open position tracking, and cost basis may be out of sync. Check the PnL ledger file for corruption.",
            )

        # --- Fallback realized profit calc (if not found on ledger; should be rare) ---
        if (
            tag_u != "LTH"
            and realized is None
            and side_l == "sell"
            and price is not None
            and avg_cost_basis is not None
        ):
            try:
                realized = (
                    (float(price) - float(avg_cost_basis)) * float(qty)
                    - float(fee_val_actual)
                    - float(fee_fallback)
                )
                self._pnl_ledger["total_realized_profit_usd"] = float(
                    self._pnl_ledger.get("total_realized_profit_usd", 0.0)
                ) + float(realized)
                self._save_pnl_ledger()
            except Exception:
                realized = None

        entry = {
            "ts": ts,
            "side": side,
            "tag": tag,
            "symbol": symbol,
            "qty": qty,
            "price": price,
            "notional_usd": float(notional) if notional is not None else None,
            "net_usd": float(net_usd) if net_usd is not None else None,
            "avg_cost_basis": avg_cost_basis,
            "pnl_pct": pnl_pct,
            # fees_usd is None when we couldn't find fees in the API payload
            "fees_usd": (float(fees_usd) if fees_usd is not None else None),
            "fees_missing": bool(fees_missing),
            "fees_fallback_applied_usd": (
                float(fee_fallback) if fee_fallback > 0.0 else 0.0
            ),
            "realized_profit_usd": realized,
            "order_id": order_id,
            "position_cost_used_usd": float(position_cost_used)
            if position_cost_used is not None
            else None,
            "position_cost_after_usd": float(position_cost_after)
            if position_cost_after is not None
            else None,
        }
        self._append_jsonl(TRADE_HISTORY_PATH, entry)

        # IMPORTANT (LIVE DCA STAGE FIX):
        # trade_history.jsonl is bot-generated. But initialize_dca_levels() filters orders by
        # _is_bot_order_id(), which checks in-memory caches:
        #   self._bot_order_ids and self._bot_order_ids_from_history
        #
        # During runtime, we record the filled order here, but the in-memory
        # _bot_order_ids_from_history cache was NOT being updated, so the very next
        # initialize_dca_levels() (called at end-of-iteration when trades_made=True)
        # would ignore the new DCA buy and rebuild stage back to 0.
        #
        # So: whenever we record a non-LTH trade with an order_id, immediately add it to cache.
        try:
            if tag_u != "LTH" and base and base != "USDC" and order_id:
                if not isinstance(
                    getattr(self, "_bot_order_ids_from_history", None), dict
                ):
                    self._bot_order_ids_from_history = {}
                self._bot_order_ids_from_history.setdefault(base, set()).add(
                    str(order_id)
                )
        except Exception as e:
            log.warning(f"could not update bot order ID cache after trade: {e}")

        # If this was a bot sell, let profit allocation update the bucket (wins and losses)
        try:
            if tag_u != "LTH" and side_l == "sell" and realized is not None:
                self._maybe_process_lth_profit_allocation(float(realized))
        except Exception as e:
            log.warning(f"LTH profit allocation trigger failed: {e}")

        # Push notification (daemon thread; never blocks)
        try:
            import pt_notify
            bp = float(self._pnl_ledger.get("buying_power", 0.0) or 0.0)
            pt_notify.notify_trade(
                side=side, symbol=symbol, qty=qty, price=price,
                avg_cost_basis=avg_cost_basis, pnl_pct=pnl_pct,
                notional_usd=notional_usd, tag=tag,
                buying_power=bp,
                account_history_path=ACCOUNT_VALUE_HISTORY_PATH,
            )
        except Exception:
            pass

    def _write_trader_status(self, status: dict) -> None:
        self._atomic_write_json(TRADER_STATUS_PATH, status)

    @staticmethod
    def _fmt_price(price: float) -> str:
        """
        Dynamic decimal formatting by magnitude:
        - >= 1.0   -> 2 decimals (BTC/ETH/etc won't show 8 decimals)
        - <  1.0   -> enough decimals to show meaningful digits (based on first non-zero),
                     then trim trailing zeros.
        """
        try:
            p = float(price)
        except Exception:
            return "N/A"

        if p == 0:
            return "0"

        ap = abs(p)

        if ap >= 1.0:
            decimals = 2
        else:
            # Example:
            # 0.5      -> decimals ~ 4 (prints "0.5" after trimming zeros)
            # 0.05     -> 5
            # 0.005    -> 6
            # 0.000012 -> 8
            decimals = int(-math.floor(math.log10(ap))) + 3
            decimals = max(2, min(12, decimals))

        s = f"{p:.{decimals}f}"

        # Trim useless trailing zeros for cleaner output (0.5000 -> 0.5)
        if "." in s:
            s = s.rstrip("0").rstrip(".")

        return s

    @staticmethod
    def _read_long_dca_signal(symbol: str) -> int:
        """
        Reads long_dca_signal.txt from the per-coin folder (same folder rules as trader.py).

        Used for:
        - Start gate: start trades at the configured TRADE_START_LEVEL+
        - DCA assist: the *next* DCA neural level is derived from TRADE_START_LEVEL at runtime
        """
        sym = str(symbol).upper().strip()
        folder = str(_pt_env.coin_dir(sym))
        path = os.path.join(folder, "long_dca_signal.txt")
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
            val = int(float(raw))
            return val
        except Exception:
            return 0

    @staticmethod
    def _read_short_dca_signal(symbol: str) -> int:
        """
        Reads short_dca_signal.txt from the per-coin folder (same folder rules as trader.py).

        Used for:
        - Start gate confirmation (typically requires short == 0)
        - Additional context alongside the configured TRADE_START_LEVEL logic
        """
        sym = str(symbol).upper().strip()
        folder = str(_pt_env.coin_dir(sym))
        path = os.path.join(folder, "short_dca_signal.txt")
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
            val = int(float(raw))
            return val
        except Exception:
            return 0

    @staticmethod
    def _read_long_price_levels(symbol: str) -> list:
        """Read LONG (buy) price levels from thinker_state.json, sorted highest->lowest (N1..N7)."""
        sym = str(symbol).upper().strip()
        path = os.path.join(str(_pt_env.coin_dir(sym)), "thinker_state.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            vals = [float(v) for v in data.get("low_bound_prices", []) if v is not None]
            out = list({round(v, 12): v for v in vals}.values())
            out.sort(reverse=True)
            return out
        except Exception:
            return []

    def initialize_dca_levels(self):
        """
        Initialize per-coin DCA stage state ONLY from the currently selected/current-trade BUY orders.

        Rules:
        - A fresh trade always starts at DCA stage 0 after the initial buy.
        - During normal runtime, DCA stage should be maintained by incrementing in memory
          when an actual DCA buy fills.
        - This startup reconstruction exists ONLY so restarts can recover the current trade
          after the hub's order-selection flow has identified which BUY orders belong to the
          active trade.
        - Therefore we count FILLED BUY orders whose IDs are in self._bot_order_ids[symbol].
          We do NOT scan old sell history here and we do NOT infer stages from all bot-owned
          historical orders.
        """
        holdings = self.get_holdings()
        if not holdings or "results" not in holdings:
            log.info("No holdings found. Skipping DCA levels initialization.")
            return

        for holding in holdings.get("results", []):
            symbol = str(holding.get("asset_code") or "").upper().strip()
            if not symbol:
                continue

            try:
                pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(symbol)
                bot_qty = (
                    float(pos.get("qty", 0.0) or 0.0) if isinstance(pos, dict) else 0.0
                )
            except Exception:
                bot_qty = 0.0

            if bot_qty <= 1e-12:
                self.dca_levels_triggered[symbol] = []
                continue

            selected_ids = set()
            try:
                selected_ids = {
                    str(x).strip()
                    for x in (self._bot_order_ids.get(symbol, set()) or set())
                    if str(x).strip()
                }
            except Exception:
                selected_ids = set()

            if not selected_ids:
                self.dca_levels_triggered[symbol] = []
                continue

            full_symbol = f"{symbol}_USD"
            orders = self.get_orders(full_symbol)
            if not orders or "results" not in orders:
                # API failure (e.g. rate-limit) — preserve current in-memory stage
                # rather than silently zeroing it, which would trigger premature DCA.
                current = len(self.dca_levels_triggered.get(symbol, []))
                log.warning(f"initialize_dca_levels: get_orders failed for {symbol} — keeping stage {current}")
                pt_errors.emit(
                    f"trader-{EXCHANGE_KEY}",
                    level="warning",
                    message=f"Could not verify DCA stage for {symbol} — order fetch failed",
                    detail=f"Kraken API did not return orders for {symbol} (rate limit or transient error). "
                           f"DCA stage kept at {current} to avoid triggering a premature buy. "
                           f"If the stage is wrong after recovery, restart the trader or trigger a hub reseed.",
                )
                continue

            relevant_buy_orders = []
            for order in orders.get("results", []):
                try:
                    if order.get("state") != "filled":
                        continue
                    if str(order.get("side") or "").lower().strip() != "buy":
                        continue

                    oid = str(order.get("id") or "").strip()
                    if not oid or oid not in selected_ids:
                        continue

                    relevant_buy_orders.append(order)
                except Exception:
                    continue

            if not relevant_buy_orders:
                self.dca_levels_triggered[symbol] = []
                continue

            relevant_buy_orders.sort(key=lambda x: x.get("created_at") or "")

            triggered_levels_count = max(0, len(relevant_buy_orders) - 1)
            self.dca_levels_triggered[symbol] = list(range(triggered_levels_count))
            log.debug(f"Initialized DCA stages for {symbol}: {triggered_levels_count}")

    def _seed_dca_window_from_history(self) -> None:
        """
        Seeds in-memory DCA buy timestamps from TRADE_HISTORY_PATH so the 24h limit
        works across restarts.

        Uses the local GUI trade history (tag == "DCA") and resets per trade at the most recent sell.
        """
        now_ts = time.time()
        cutoff = now_ts - float(getattr(self, "dca_window_seconds", 86400))

        self._dca_buy_ts = {}
        self._dca_last_sell_ts = {}

        if not os.path.isfile(TRADE_HISTORY_PATH):
            return

        try:
            with open(TRADE_HISTORY_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue

                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    ts = obj.get("ts", None)
                    side = str(obj.get("side", "")).lower()
                    tag = obj.get("tag", None)
                    sym_full = str(obj.get("symbol", "")).upper().strip()
                    base = sym_full.split("_")[0].strip() if sym_full else ""
                    if not base:
                        continue

                    try:
                        ts_f = utc_to_ts(ts) if ts else 0.0
                        if not ts_f:
                            continue
                    except Exception:
                        continue

                    if side == "sell":
                        prev = float(self._dca_last_sell_ts.get(base, 0.0) or 0.0)
                        if ts_f > prev:
                            self._dca_last_sell_ts[base] = ts_f

                    elif side == "buy" and tag == "DCA":
                        self._dca_buy_ts.setdefault(base, []).append(ts_f)

        except Exception as e:
            log.warning(f"DCA window seeding from history failed: {e}")
            return

        # Keep only DCA buys after the last sell (current trade) and within rolling 24h
        for base, ts_list in list(self._dca_buy_ts.items()):
            last_sell = float(self._dca_last_sell_ts.get(base, 0.0) or 0.0)
            kept = [t for t in ts_list if (t > last_sell) and (t >= cutoff)]
            kept.sort()
            self._dca_buy_ts[base] = kept

    def _dca_window_count(
        self, base_symbol: str, now_ts: Optional[float] = None
    ) -> int:
        """
        Count of DCA buys for this coin within rolling 24h in the *current trade*.
        Current trade boundary = most recent sell we observed for this coin.
        """
        base = str(base_symbol).upper().strip()
        if not base:
            return 0

        now = float(now_ts if now_ts is not None else time.time())
        cutoff = now - float(getattr(self, "dca_window_seconds", 86400))
        last_sell = float(self._dca_last_sell_ts.get(base, 0.0) or 0.0)

        ts_list = list(self._dca_buy_ts.get(base, []) or [])
        ts_list = [t for t in ts_list if (t > last_sell) and (t >= cutoff)]
        self._dca_buy_ts[base] = ts_list
        return len(ts_list)

    def _note_dca_buy(self, base_symbol: str, ts: Optional[float] = None) -> None:
        base = str(base_symbol).upper().strip()
        if not base:
            return
        t = float(ts if ts is not None else time.time())
        self._dca_buy_ts.setdefault(base, []).append(t)
        self._dca_window_count(base, now_ts=t)  # prune in-place

    def _reset_dca_window_for_trade(
        self, base_symbol: str, sold: bool = False, ts: Optional[float] = None
    ) -> None:
        base = str(base_symbol).upper().strip()
        if not base:
            return
        if sold:
            self._dca_last_sell_ts[base] = float(ts if ts is not None else time.time())
        self._dca_buy_ts[base] = []

    # ------------------------------------------------------------------
    # Exchange adapter wrappers (delegate to self.exchange)
    # ------------------------------------------------------------------

    def get_holdings(self) -> dict:
        h = self.exchange.get_holdings()
        return {
            "results": [{"asset_code": k, "total_quantity": v} for k, v in h.items()]
        }

    def get_orders(self, symbol: str) -> dict:
        return self.exchange.get_orders(symbol)

    def get_price(self, symbols: list) -> tuple:
        return self.exchange.get_price(symbols)

    def calculate_cost_basis(self) -> dict:
        result = self.exchange.calculate_cost_basis_from_orders(
            self._bot_order_ids,
            self._bot_order_ids_from_history,
            self._pnl_ledger,
        )
        if result:
            return result
        cb: Dict[str, float] = {}
        try:
            for sym, pos in (self._pnl_ledger.get("open_positions", {}) or {}).items():
                if not isinstance(pos, dict):
                    continue
                qty = float(pos.get("qty", 0) or 0)
                cost = float(pos.get("usd_cost", 0) or 0)
                cb[sym] = (cost / qty) if qty > 1e-12 else 0.0
        except Exception:
            pass
        return cb

    def place_buy_order(
        self,
        symbol: str,
        amount_in_usd: float,
        avg_cost_basis: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> Optional[OrderResult]:
        # Store pending metadata for crash recovery
        pending_id = str(uuid.uuid4())
        try:
            self._pnl_ledger.setdefault("pending_orders", {})
            self._pnl_ledger["pending_orders"][pending_id] = {
                "symbol": symbol,
                "side": "buy",
                "avg_cost_basis": float(avg_cost_basis)
                if avg_cost_basis is not None
                else None,
                "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                "tag": tag,
                "created_ts": time.time(),
            }
            self._save_pnl_ledger()
        except Exception as e:
            log.warning(f"could not save pending buy order for {symbol}: {e}")

        result = self.exchange.place_buy(symbol, amount_in_usd, tag=tag)

        # Clear placeholder pending (real order_id comes from result)
        try:
            self._pnl_ledger.get("pending_orders", {}).pop(pending_id, None)
        except Exception:
            pass

        if not result or result.state != "filled":
            try:
                self._save_pnl_ledger()
            except Exception:
                pass
            return None

        order_id = result.order_id

        self._record_trade(
            side="buy",
            symbol=symbol,
            qty=result.filled_qty,
            price=result.avg_price,
            notional_usd=result.notional_usd,
            fees_usd=result.fees_usd,
            avg_cost_basis=float(avg_cost_basis)
            if avg_cost_basis is not None
            else None,
            pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
            tag=tag,
            order_id=order_id,
        )

        if str(tag or "").upper().strip() != "LTH":
            try:
                base_symbol = self.exchange.base_from_canonical(symbol)
                self._mark_bot_order_id(base_symbol, order_id)
            except Exception as e:
                log.warning(f"could not mark bot order ID after buy for {symbol}: {e}")

            try:
                base_symbol = self.exchange.base_from_canonical(symbol)
                if str(tag or "").upper().strip() == "DCA":
                    current_levels = list(
                        self.dca_levels_triggered.get(base_symbol, []) or []
                    )
                    current_levels.append(len(current_levels))
                    self.dca_levels_triggered[base_symbol] = current_levels
                else:
                    self.dca_levels_triggered[base_symbol] = []
            except Exception as e:
                log.warning(
                    f"could not update DCA level count after buy for {symbol}: {e}"
                )

        try:
            self._save_pnl_ledger()
        except Exception:
            pass

        return result

    def place_sell_order(
        self,
        symbol: str,
        asset_quantity: float,
        avg_cost_basis: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> Optional[OrderResult]:
        # Store pending metadata for crash recovery
        pending_id = str(uuid.uuid4())
        try:
            self._pnl_ledger.setdefault("pending_orders", {})
            self._pnl_ledger["pending_orders"][pending_id] = {
                "symbol": symbol,
                "side": "sell",
                "avg_cost_basis": float(avg_cost_basis)
                if avg_cost_basis is not None
                else None,
                "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                "tag": tag,
                "created_ts": time.time(),
            }
            self._save_pnl_ledger()
        except Exception as e:
            log.warning(f"could not save pending sell order for {symbol}: {e}")

        result = self.exchange.place_sell(symbol, asset_quantity)

        try:
            self._pnl_ledger.get("pending_orders", {}).pop(pending_id, None)
        except Exception:
            pass

        if not result or result.state != "filled":
            try:
                self._save_pnl_ledger()
            except Exception:
                pass
            return None

        order_id = result.order_id
        actual_pnl_pct = pnl_pct

        if avg_cost_basis is not None and result.avg_price is not None:
            try:
                acb = float(avg_cost_basis)
                if acb > 0:
                    actual_pnl_pct = ((float(result.avg_price) - acb) / acb) * 100.0
            except Exception:
                pass

        self._record_trade(
            side="sell",
            symbol=symbol,
            qty=result.filled_qty,
            price=result.avg_price,
            notional_usd=result.notional_usd,
            fees_usd=result.fees_usd,
            avg_cost_basis=float(avg_cost_basis)
            if avg_cost_basis is not None
            else None,
            pnl_pct=float(actual_pnl_pct) if actual_pnl_pct is not None else None,
            tag=tag,
            order_id=order_id,
        )

        try:
            base_symbol = self.exchange.base_from_canonical(symbol)
            self._clear_bot_order_ids_for_coin(base_symbol)
            self.dca_levels_triggered[base_symbol] = []
            self.trailing_pm.pop(base_symbol, None)
        except Exception as e:
            log.warning(f"post-sell state cleanup failed for {symbol}: {e}")

        try:
            self._save_pnl_ledger()
        except Exception:
            pass

        return result

    def manage_trades(self):
        _mt_start = time.time()
        trades_made = False  # Flag to track if any trade was made in this iteration

        # Hot-reload coins list + paths + trade params from GUI settings while running
        try:
            _refresh_paths_and_symbols()
            self.dca_levels = list(DCA_LEVELS)
            self.max_dca_buys_per_24h = int(MAX_DCA_BUYS_PER_24H)

            # Trailing PM settings (hot-reload)
            old_sig = getattr(self, "_last_trailing_settings_sig", None)

            new_gap = float(TRAILING_GAP_PCT)
            new_pm0 = float(PM_START_PCT_NO_DCA)
            new_pm1 = float(PM_START_PCT_WITH_DCA)

            self.trailing_gap_pct = new_gap
            self.pm_start_pct_no_dca = new_pm0
            self.pm_start_pct_with_dca = new_pm1

            new_sig = (float(new_gap), float(new_pm0), float(new_pm1))

            # If trailing settings changed, reset ALL trailing PM state so:
            # - the line updates immediately
            # - peak/armed/was_above are cleared
            if (old_sig is not None) and (new_sig != old_sig):
                self.trailing_pm = {}

            self._last_trailing_settings_sig = new_sig
        except Exception as e:
            log.warning(f"settings hot-reload failed: {e}")

        # NEW: Also allow LTH bucket spending during normal loops (not only right after sells)
        try:
            self._maybe_process_lth_profit_allocation(0.0)
        except Exception as e:
            log.warning(f"LTH profit allocation loop failed: {e}")

        # Fetch account details via adapter
        holdings = self.get_holdings()
        trading_pairs = self.exchange.has_valid_trading_pairs()

        # Hot-reload bot order ownership selections from the Hub (so avg entry reflects what you picked)
        try:
            reloaded = self._maybe_reload_bot_order_ids()
            if reloaded:
                # selection changed -> force a re-seed of open_positions from selected order IDs
                self._needs_ledger_seed_from_orders = True
        except Exception as e:
            log.warning(f"bot order ID reload failed: {e}")

        # Use the (possibly refreshed) stored cost_basis
        cost_basis = self.cost_basis

        # Fetch current prices (canonical format: BTC_USD)
        symbols = [
            holding["asset_code"] + "_USD" for holding in holdings.get("results", [])
        ]

        for s in crypto_symbols:
            full = f"{s}_USD"
            if full not in symbols:
                symbols.append(full)

        _t_price = time.time()
        current_buy_prices, current_sell_prices, valid_symbols = self.get_price(symbols)
        _price_dur = time.time() - _t_price

        # Calculate total account value (robust: never drop a held coin to $0 on transient API misses)
        snapshot_ok = True

        # buying power
        try:
            bp = self.exchange.get_buying_power()
            buying_power = float(bp) if bp is not None else 0.0
        except Exception:
            buying_power = 0.0
            snapshot_ok = False

        # holdings list (treat missing/invalid holdings payload as transient error)
        try:
            holdings_list = (
                holdings.get("results", None) if isinstance(holdings, dict) else None
            )
            if not isinstance(holdings_list, list):
                holdings_list = []
                snapshot_ok = False
        except Exception:
            holdings_list = []
            snapshot_ok = False

        # IMPORTANT: seed bot open_positions from the selected bot order IDs (Hub picker).
        # This makes avg entry/cost basis include selected buys even if they occurred before
        # unrelated manual sells in the account history.
        #
        # FIX (DCA stage stuck at 0 after restart):
        # initialize_dca_levels() depends on pnl_ledger["open_positions"][SYM]["qty"] to decide whether
        # the bot is "in trade" for that coin. That ledger is seeded here, so we MUST re-run
        # initialize_dca_levels() immediately after seeding; otherwise DCA stages remain [] (0) in the GUI.
        try:
            if getattr(self, "_needs_ledger_seed_from_orders", False):
                self._seed_open_positions_from_selected_orders(holdings_list)

                # Now that open_positions is seeded, rebuild per-coin DCA stage counts from bot-owned order history.
                try:
                    self.initialize_dca_levels()
                except Exception:
                    pass

                self._needs_ledger_seed_from_orders = False
        except Exception:
            log.exception("ledger seed from selected orders failed")
            pt_errors.emit(
                f"trader-{EXCHANGE_KEY}",
                level="error",
                message="Failed to reseed open positions from selected bot orders",
                detail="Position cost basis and DCA stages may be stale. The reseed will be retried on the next selection change.",
            )

        holdings_buy_value = 0.0
        holdings_sell_value = 0.0

        # value of ONLY the tradable portion (excludes long-term ignored holdings)
        trade_holdings_buy_value = 0.0
        trade_holdings_sell_value = 0.0

        for holding in holdings_list:
            try:
                asset = holding.get("asset_code")
                if asset == "USDC":
                    continue

                qty = float(holding.get("total_quantity", 0.0))
                if qty <= 0.0:
                    continue

                sym = f"{asset}_USD"
                bp = float(current_buy_prices.get(sym, 0.0) or 0.0)
                sp = float(current_sell_prices.get(sym, 0.0) or 0.0)

                # If any held asset is missing a usable price this tick, do NOT allow a new "low" snapshot
                if bp <= 0.0 or sp <= 0.0:
                    snapshot_ok = False
                    continue

                holdings_buy_value += qty * bp
                holdings_sell_value += qty * sp

                # tradable portion = bot-managed ledger qty (anything else is manual/long-term)
                try:
                    sym = str(asset).upper().strip()
                    pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(sym)
                    tradable_qty = (
                        float(pos.get("qty", 0.0) or 0.0)
                        if isinstance(pos, dict)
                        else 0.0
                    )
                except Exception:
                    tradable_qty = 0.0

                if tradable_qty < 0.0:
                    tradable_qty = 0.0
                if tradable_qty > qty:
                    tradable_qty = qty

                # Dust rule: if the ONLY amount beyond in-trade is worth <= $0.01, treat it as in-trade
                # so the next SELL will clear it.
                try:
                    excess_qty = float(qty) - float(tradable_qty)
                except Exception:
                    excess_qty = 0.0
                if excess_qty > 0.0:
                    try:
                        if (float(excess_qty) * float(sp)) <= 0.01:
                            tradable_qty = float(qty)
                    except Exception:
                        pass

                trade_holdings_buy_value += tradable_qty * bp
                trade_holdings_sell_value += tradable_qty * sp
            except Exception:
                snapshot_ok = False
                continue

        total_account_value = buying_power + holdings_sell_value
        in_use = (
            (trade_holdings_sell_value / total_account_value) * 100
            if total_account_value > 0
            else 0.0
        )

        # If this tick is incomplete, fall back to last known-good snapshot so the GUI chart never gets a bogus dip.
        if (not snapshot_ok) or (total_account_value <= 0.0):
            last = getattr(self, "_last_good_account_snapshot", None) or {}
            if last.get("total_account_value") is not None:
                total_account_value = float(last["total_account_value"])
                buying_power = float(last.get("buying_power", buying_power or 0.0))
                holdings_sell_value = float(
                    last.get("holdings_sell_value", holdings_sell_value or 0.0)
                )
                holdings_buy_value = float(
                    last.get("holdings_buy_value", holdings_buy_value or 0.0)
                )
                in_use = float(last.get("percent_in_trade", in_use or 0.0))
        else:
            # Save last complete snapshot
            self._last_good_account_snapshot = {
                "total_account_value": float(total_account_value),
                "buying_power": float(buying_power),
                "holdings_sell_value": float(holdings_sell_value),
                "holdings_buy_value": float(holdings_buy_value),
                "percent_in_trade": float(in_use),
            }

        log.info(
            f"Account: ${total_account_value:.2f} total | ${holdings_sell_value:.2f} holdings | {in_use:.2f}% in trade | PM no-DCA +{self.pm_start_pct_no_dca:.2f}% with-DCA +{self.pm_start_pct_with_dca:.2f}% gap {self.trailing_gap_pct:.2f}%"
        )

        positions = {}
        for holding in holdings.get("results", []):
            symbol = holding["asset_code"]
            full_symbol = f"{symbol}_USD"

            if symbol == "USDC":
                continue

            # IMPORTANT:
            # We still want held coins to publish into positions[] even if they are not in
            # the autotrade/tracked list or did not come back in valid_symbols this tick.
            # The HUB Long-term Holdings tab already knows how to show any coin with a
            # positive lth_reserved_qty, even when it is NOT listed in Settings.
            #
            # So do NOT skip the holding here just because it isn't in valid_symbols.
            total_quantity = float(holding["total_quantity"])

            # Bot-managed qty used for ACTIVE TRADE display comes from the ledger.
            # Keep that behavior so the current-trades table stays exactly as before.
            try:
                pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(
                    str(symbol).upper().strip()
                )
                quantity = (
                    float(pos.get("qty", 0.0) or 0.0) if isinstance(pos, dict) else 0.0
                )
            except Exception:
                quantity = 0.0

            if quantity < 0.0:
                quantity = 0.0
            if quantity > total_quantity:
                quantity = total_quantity

            # Long-term reserved qty should be the SIMPLE rule:
            #   anything currently held on the exchange beyond the bot/autotrade qty
            #
            # DO NOT inspect order history.
            # DO NOT replay old buys.
            # DO NOT use legacy/back-compat logic.
            #
            # The bot/autotrade qty for active trades already comes from the ledger
            # (`quantity` above), so LTH is just:
            #   max(0, live_total_qty - autotrade_qty)
            try:
                reserved_qty = float(total_quantity) - float(quantity)
                if reserved_qty < 0.0:
                    reserved_qty = 0.0
            except Exception:
                reserved_qty = 0.0

            # Dust rule: if the ONLY amount beyond in-trade is worth <= $0.01, treat it as in-trade
            # so the next SELL will clear it.
            if quantity > 0.0 and reserved_qty > 0.0:
                try:
                    dust_sp = float(current_sell_prices.get(full_symbol, 0.0) or 0.0)
                except Exception:
                    dust_sp = 0.0
                if dust_sp > 0.0:
                    try:
                        if (float(reserved_qty) * float(dust_sp)) <= 0.01:
                            quantity = float(total_quantity)
                            reserved_qty = 0.0
                    except Exception:
                        pass

            # If we're only holding the long-term reserved amount, treat as NOT in-trade.
            if quantity <= 0.0:
                # Make sure bot state doesn't treat this as an active trade.
                self.trailing_pm.pop(symbol, None)
                self.dca_levels_triggered.pop(symbol, None)

                # Still publish a positions[] row so the HUB can show Long-Term Holdings (reserved qty only).
                current_buy_price = current_buy_prices.get(full_symbol, 0)
                current_sell_price = current_sell_prices.get(full_symbol, 0)

                try:
                    _cpf = os.path.join(
                        str(_pt_env.coin_dir(symbol)),
                        f"{symbol}_current_price.txt",
                    )
                    with open(_cpf, "w") as _f:
                        _f.write(str(current_buy_price))
                except Exception:
                    pass

                positions[symbol] = {
                    "quantity": 0.0,
                    "avg_cost_basis": 0.0,
                    "current_buy_price": current_buy_price,
                    "current_sell_price": current_sell_price,
                    "gain_loss_pct_buy": 0.0,
                    "gain_loss_pct_sell": 0.0,
                    "value_usd": 0.0,
                    "dca_triggered_stages": 0,
                    "dca_total_stages": len(self.dca_levels),
                    "next_dca_display": "",
                    "dca_line_price": 0.0,
                    "dca_line_source": "N/A",
                    "dca_line_pct": 0.0,
                    "trail_active": False,
                    "trail_line": 0.0,
                    "trail_peak": 0.0,
                    "dist_to_trail_pct": 0.0,
                    # Long-term (reserved) qty (ignored by trader)
                    "lth_reserved_qty": float(reserved_qty),
                }
                continue

            current_buy_price = current_buy_prices.get(full_symbol, 0)
            current_sell_price = current_sell_prices.get(full_symbol, 0)

            # Prefer the bot's own ledger for avg cost basis (so manual/long-term holdings don't affect trading logic)
            # BUT: the ledger can get corrupted if buying_power deltas lag/reflect other fills. If the ledger looks
            # inconsistent, fall back to an order-replay cost basis and also repair the ledger entry.
            avg_cost_basis = 0.0
            ledger_pq = 0.0
            ledger_pc = 0.0
            try:
                pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(symbol)
                if isinstance(pos, dict):
                    ledger_pq = float(pos.get("qty", 0.0) or 0.0)
                    ledger_pc = float(pos.get("usd_cost", 0.0) or 0.0)
                    if ledger_pq > 0.0:
                        avg_cost_basis = ledger_pc / ledger_pq
            except Exception:
                avg_cost_basis = 0.0

            # Sanity check the ledger using an order-replay basis when the ledger is suspicious.
            # (e.g., BNB avg cost showing BTC prices)
            try:
                suspicious = False
                if avg_cost_basis > 0.0 and float(current_buy_price) > 0.0:
                    ratio = float(avg_cost_basis) / float(current_buy_price)
                    if ratio > 5.0 or ratio < 0.2:
                        suspicious = True

                if suspicious:
                    fresh_cb = 0.0
                    try:
                        # expensive, so only do it when ledger looks wrong
                        fresh_map = self.calculate_cost_basis()
                        fresh_cb = float((fresh_map or {}).get(symbol, 0.0) or 0.0)
                    except Exception:
                        fresh_cb = 0.0

                    if fresh_cb > 0.0:
                        # If the ledger differs massively from the order-replay basis, trust the replay and repair ledger.
                        if avg_cost_basis <= 0.0 or (
                            abs((avg_cost_basis / fresh_cb) - 1.0) > 0.5
                        ):
                            avg_cost_basis = fresh_cb
                            try:
                                open_pos = self._pnl_ledger.get("open_positions", {})
                                if not isinstance(open_pos, dict):
                                    open_pos = {}
                                    self._pnl_ledger["open_positions"] = open_pos
                                base_key = str(symbol).upper().split("_")[0].strip()
                                open_pos[base_key] = {
                                    "usd_cost": float(avg_cost_basis) * float(quantity),
                                    "qty": float(quantity),
                                }

                                self._save_pnl_ledger()
                            except Exception:
                                pass
            except Exception as e:
                log.debug(f"suspicious cost basis check failed for {symbol}: {e}")

            if avg_cost_basis <= 0.0:
                avg_cost_basis = cost_basis.get(symbol, 0) or 0.0

            if avg_cost_basis > 0:
                gain_loss_percentage_buy = (
                    (current_buy_price - avg_cost_basis) / avg_cost_basis
                ) * 100
                gain_loss_percentage_sell = (
                    (current_sell_price - avg_cost_basis) / avg_cost_basis
                ) * 100
            else:
                gain_loss_percentage_buy = 0
                gain_loss_percentage_sell = 0
                log.warning(f"avg_cost_basis is 0 for {symbol}, gain/loss skipped")

            value = quantity * current_sell_price
            triggered_levels_count = len(self.dca_levels_triggered.get(symbol, []))

            triggered_levels = triggered_levels_count  # Number of DCA levels triggered

            # Determine the next DCA trigger for this coin (hardcoded % and optional neural level)
            next_stage = triggered_levels_count  # stage 0 == first DCA after entry (trade starts at neural level 3)

            # Hardcoded % for this stage (repeat -50% after we reach it)
            hard_next = (
                self.dca_levels[next_stage]
                if next_stage < len(self.dca_levels)
                else self.dca_levels[-1]
            )

            # Neural DCA applies to the levels BELOW the trade-start level.
            # Example: trade_start_level=3 => stages 0..3 map to N4..N7 (4 total).
            start_level = max(1, min(int(TRADE_START_LEVEL or 3), 7))
            neural_dca_max = max(0, 7 - start_level)

            if next_stage < neural_dca_max:
                neural_next = start_level + 1 + next_stage
                next_dca_display = f"{hard_next:.2f}% / N{neural_next}"
            else:
                next_dca_display = f"{hard_next:.2f}%"

            # --- DCA DISPLAY LINE (show whichever trigger will be hit first: higher of NEURAL line vs HARD line) ---
            # Hardcoded gives an actual price line: cost_basis * (1 + hard_next%).
            # Neural gives an actual price line from low_bound_prices.html (N1..N7).
            dca_line_source = "HARD"
            dca_line_price = 0.0
            dca_line_pct = 0.0

            if avg_cost_basis > 0:
                # Hardcoded trigger line price
                hard_line_price = avg_cost_basis * (1.0 + (hard_next / 100.0))

                # Default to hardcoded unless neural line is higher (hit first)
                dca_line_price = hard_line_price

                if next_stage < neural_dca_max:
                    neural_level_needed_disp = start_level + 1 + next_stage
                    neural_levels = self._read_long_price_levels(
                        symbol
                    )  # highest->lowest == N1..N7

                    neural_line_price = 0.0
                    if len(neural_levels) >= neural_level_needed_disp:
                        neural_line_price = float(
                            neural_levels[neural_level_needed_disp - 1]
                        )

                    # Whichever is higher will be hit first as price drops
                    if neural_line_price > dca_line_price:
                        dca_line_price = neural_line_price
                        dca_line_source = f"NEURAL N{neural_level_needed_disp}"

                # PnL% shown alongside DCA is the normal buy-side PnL%
                # (same calculation as GUI "Buy Price PnL": current buy/ask vs avg cost basis)
                dca_line_pct = gain_loss_percentage_buy

            # --- Trailing PM display (per-coin, isolated) ---
            # Display uses current state if present; otherwise shows the base PM start line.
            trail_status = "N/A"
            pm_start_pct_disp = 0.0
            base_pm_line_disp = 0.0
            trail_line_disp = 0.0
            trail_peak_disp = 0.0
            above_disp = False
            dist_to_trail_pct = 0.0

            if avg_cost_basis > 0:
                pm_start_pct_disp = (
                    self.pm_start_pct_no_dca
                    if int(triggered_levels) == 0
                    else self.pm_start_pct_with_dca
                )
                base_pm_line_disp = avg_cost_basis * (1.0 + (pm_start_pct_disp / 100.0))

                state = self.trailing_pm.get(symbol)
                if state is None:
                    trail_line_disp = base_pm_line_disp
                    trail_peak_disp = 0.0
                    active_disp = False
                else:
                    trail_line_disp = float(state.get("line", base_pm_line_disp))
                    trail_peak_disp = float(state.get("peak", 0.0))
                    active_disp = bool(state.get("active", False))

                above_disp = current_sell_price >= trail_line_disp
                # If we're already above the line, trailing is effectively "on/armed" (even if active flips this tick)
                trail_status = "ON" if (active_disp or above_disp) else "OFF"

                if trail_line_disp > 0:
                    dist_to_trail_pct = (
                        (current_sell_price - trail_line_disp) / trail_line_disp
                    ) * 100.0

            _cpf = os.path.join(
                str(_pt_env.coin_dir(symbol)),
                f"{symbol}_current_price.txt",
            )
            with open(_cpf, "w") as _f:
                _f.write(str(current_buy_price))
            positions[symbol] = {
                "quantity": quantity,
                "avg_cost_basis": avg_cost_basis,
                "current_buy_price": current_buy_price,
                "current_sell_price": current_sell_price,
                "gain_loss_pct_buy": gain_loss_percentage_buy,
                "gain_loss_pct_sell": gain_loss_percentage_sell,
                "value_usd": value,
                "dca_triggered_stages": int(triggered_levels_count),
                "dca_total_stages": len(self.dca_levels),
                "next_dca_display": next_dca_display,
                "dca_line_price": float(dca_line_price) if dca_line_price else 0.0,
                "dca_line_source": dca_line_source,
                "dca_line_pct": float(dca_line_pct) if dca_line_pct else 0.0,
                "trail_active": True if (trail_status == "ON") else False,
                "trail_line": float(trail_line_disp) if trail_line_disp else 0.0,
                "trail_peak": float(trail_peak_disp) if trail_peak_disp else 0.0,
                "dist_to_trail_pct": float(dist_to_trail_pct)
                if dist_to_trail_pct
                else 0.0,
                # Long-term (reserved) qty (ignored by trader)
                "lth_reserved_qty": float(reserved_qty),
            }

            log.info(
                f"{symbol}: DCA {dca_line_pct:+.2f}% @ {self._fmt_price(current_buy_price)}"
                f" | G/L sell {gain_loss_percentage_sell:.2f}% @ {self._fmt_price(current_sell_price)}"
                f" | DCA levels {triggered_levels} | ${value:.2f}"
                + (
                    f" | PM line {self._fmt_price(trail_line_disp)} above={above_disp}"
                    if avg_cost_basis > 0
                    else " | PM N/A"
                )
            )

            # --- Trailing profit margin (0.5% trail gap) ---
            # PM "start line" is the normal 5% / 2.5% line (depending on DCA levels hit).
            # Trailing activates once price is ABOVE the PM start line, then line follows peaks up
            # by 0.5%. Forced sell happens ONLY when price goes from ABOVE the trailing line to BELOW it.
            if avg_cost_basis > 0:
                pm_start_pct = (
                    self.pm_start_pct_no_dca
                    if int(triggered_levels) == 0
                    else self.pm_start_pct_with_dca
                )
                base_pm_line = avg_cost_basis * (1.0 + (pm_start_pct / 100.0))
                trail_gap = self.trailing_gap_pct / 100.0  # 0.5% => 0.005

                # If trailing settings changed since this coin's state was created, reset it.
                settings_sig = (
                    float(self.trailing_gap_pct),
                    float(self.pm_start_pct_no_dca),
                    float(self.pm_start_pct_with_dca),
                )

                state = self.trailing_pm.get(symbol)
                if (state is None) or (state.get("settings_sig") != settings_sig):
                    state = {
                        "active": False,
                        "line": base_pm_line,
                        "peak": 0.0,
                        "was_above": False,
                        "settings_sig": settings_sig,
                    }
                    self.trailing_pm[symbol] = state
                else:
                    # Keep signature up to date
                    state["settings_sig"] = settings_sig

                    # IMPORTANT:
                    # If trailing hasn't activated yet, this is just the PM line.
                    # It MUST track the current avg_cost_basis (so it can move DOWN after each DCA).
                    if not state.get("active", False):
                        state["line"] = base_pm_line
                    else:
                        # Once trailing is active, the line should never be below the base PM start line.
                        if state.get("line", 0.0) < base_pm_line:
                            state["line"] = base_pm_line

                # Use SELL price because that's what you actually get when you market sell
                above_now = current_sell_price >= state["line"]

                # Activate trailing once we first get above the base PM line
                if (not state["active"]) and above_now:
                    state["active"] = True
                    state["peak"] = current_sell_price

                # If active, update peak and move trailing line up behind it
                if state["active"]:
                    if current_sell_price > state["peak"]:
                        state["peak"] = current_sell_price

                    new_line = state["peak"] * (1.0 - trail_gap)
                    if new_line < base_pm_line:
                        new_line = base_pm_line
                    if new_line > state["line"]:
                        state["line"] = new_line

                    # Forced sell on cross from ABOVE -> BELOW trailing line
                    if state["was_above"] and (current_sell_price < state["line"]):
                        log.info(
                            f"Trailing PM hit {symbol}: sell {current_sell_price:.8f} < line {state['line']:.8f}"
                        )
                        response = self.place_sell_order(
                            full_symbol,
                            quantity,
                            avg_cost_basis=avg_cost_basis,
                            pnl_pct=gain_loss_percentage_sell,
                            tag="TRAIL_SELL",
                        )

                        if response is not None:
                            trades_made = True
                            self.trailing_pm.pop(
                                symbol, None
                            )  # clear per-coin trailing state on exit

                            # Trade ended -> reset rolling 24h DCA window for this coin
                            self._reset_dca_window_for_trade(symbol, sold=True)
                            self.dca_levels_triggered[symbol] = []
                            log.info(f"Sold {quantity} {symbol}")
                            time.sleep(5)
                            holdings = self.get_holdings()
                            continue

                # Save this tick’s position relative to the line (needed for “above -> below” detection)
                state["was_above"] = above_now

            # DCA (NEURAL or hardcoded %, whichever hits first for the current stage)
            #
            # IMPORTANT:
            # - DCA stages must be derived from TRADE_START_LEVEL (settings), not hardcoded.
            # - Neural-based DCA should only trigger when price has actually reached the next neural LINE,
            #   otherwise tiny post-fill spread/slippage can cause an immediate DCA right after entry.

            start_level = max(1, min(int(TRADE_START_LEVEL or 3), 7))

            # How many neural DCA stages exist above the start level?
            # Example: start_level=4 -> neural DCAs can be at 5,6,7 => 3 stages
            neural_dca_max = max(0, 7 - start_level)

            current_stage = len(self.dca_levels_triggered.get(symbol, []))

            # Hardcoded loss % for this stage (repeat last level after list ends)
            hard_level = (
                self.dca_levels[current_stage]
                if current_stage < len(self.dca_levels)
                else self.dca_levels[-1]
            )
            hard_hit = gain_loss_percentage_buy <= hard_level

            # Neural trigger for as many stages as exist above start_level
            neural_level_needed = None
            neural_level_now = None
            neural_line_price = None
            neural_hit = False

            if current_stage < neural_dca_max:
                # Stage 0 should be start_level+1, stage 1 -> start_level+2, etc.
                neural_level_needed = start_level + 1 + current_stage
                neural_level_now = self._read_long_dca_signal(symbol)

                # Get the actual neural LINE price for the needed level (N1..N7 => index 0..6)
                long_levels = self._read_long_price_levels(symbol)
                idx = int(neural_level_needed) - 1
                if isinstance(long_levels, list) and 0 <= idx < len(long_levels):
                    try:
                        neural_line_price = float(long_levels[idx])
                    except Exception:
                        neural_line_price = None

                # Trigger neural DCA only if:
                # 1) signal is at/above the required level
                # 2) we're below cost basis (PnL<0)
                # 3) price has actually reached/passed the required neural line price
                neural_hit = (
                    (gain_loss_percentage_buy < 0)
                    and (neural_level_now >= neural_level_needed)
                    and (neural_line_price is not None)
                    and (current_buy_price <= neural_line_price)
                )

            if hard_hit or neural_hit:
                if neural_hit and hard_hit:
                    reason = (
                        f"NEURAL L{neural_level_now}>=L{neural_level_needed} "
                        f"AND px<=N{neural_level_needed}({self._fmt_price(neural_line_price)}) "
                        f"OR HARD {hard_level:.2f}%"
                    )
                elif neural_hit:
                    reason = (
                        f"NEURAL L{neural_level_now}>=L{neural_level_needed} "
                        f"AND px<=N{neural_level_needed}({self._fmt_price(neural_line_price)})"
                    )
                else:
                    reason = f"HARD {hard_level:.2f}%"

                dca_amount = total_account_value * (START_ALLOC_PCT / 100.0) * float(DCA_MULTIPLIER or 1.0)
                log.info(
                    f"DCA {symbol} stage {current_stage + 1} via {reason} | value ${value:.2f} | dca ${dca_amount:.2f} | bp ${buying_power:.2f}"
                )

                recent_dca = self._dca_window_count(symbol)
                if recent_dca >= int(getattr(self, "max_dca_buys_per_24h", 2)):
                    log.info(
                        f"DCA {symbol} skipped — {recent_dca} buys in last 24h (max {self.max_dca_buys_per_24h})"
                    )

                elif dca_amount <= buying_power:
                    response = self.place_buy_order(
                        full_symbol,
                        dca_amount,
                        avg_cost_basis=avg_cost_basis,
                        pnl_pct=gain_loss_percentage_buy,
                        tag="DCA",
                    )

                    if response is not None:
                        self._note_dca_buy(symbol)
                        self.trailing_pm.pop(symbol, None)
                        trades_made = True
                        log.info(f"DCA buy placed for {symbol}")
                    else:
                        log.warning(f"DCA buy FAILED for {symbol}")

                else:
                    log.info(f"DCA {symbol} skipped — insufficient funds")
                    self._record_skip(full_symbol, f"DCA skipped — insufficient funds (need ${dca_amount:.2f})")

            else:
                pass

        # --- ensure GUI gets bid/ask lines even for coins not currently held ---
        try:
            for sym in crypto_symbols:
                if sym in positions:
                    continue

                full_symbol = f"{sym}_USD"
                if full_symbol not in valid_symbols or sym == "USDC":
                    continue

                current_buy_price = current_buy_prices.get(full_symbol, 0.0)
                current_sell_price = current_sell_prices.get(full_symbol, 0.0)

                try:
                    _cpf = os.path.join(
                        str(_pt_env.coin_dir(sym)),
                        f"{sym}_current_price.txt",
                    )
                    with open(_cpf, "w") as _f:
                        _f.write(str(current_buy_price))
                except Exception:
                    pass

                # Not currently held (or not in holdings payload this tick) => no leftover qty to reserve.
                reserved_qty = 0.0

                positions[sym] = {
                    "quantity": 0.0,
                    "avg_cost_basis": 0.0,
                    "current_buy_price": current_buy_price,
                    "current_sell_price": current_sell_price,
                    "gain_loss_pct_buy": 0.0,
                    "gain_loss_pct_sell": 0.0,
                    "value_usd": 0.0,
                    "dca_triggered_stages": int(
                        len(self.dca_levels_triggered.get(sym, []))
                    ),
                    "next_dca_display": "",
                    "dca_line_price": 0.0,
                    "dca_line_source": "N/A",
                    "dca_line_pct": 0.0,
                    "trail_active": False,
                    "trail_line": 0.0,
                    "trail_peak": 0.0,
                    "dist_to_trail_pct": 0.0,
                    # Long-term (reserved) qty (ignored by trader)
                    "lth_reserved_qty": float(reserved_qty),
                }

        except Exception as e:
            log.warning(f"positions update for untracked coins failed: {e}")

        if not trading_pairs:
            return

        alloc_pct = float(START_ALLOC_PCT or 0.005)
        allocation_in_usd = total_account_value * (alloc_pct / 100.0)
        if allocation_in_usd < 0.5:
            allocation_in_usd = 0.5

        holding_full_symbols = []
        for h in holdings.get("results", []):
            try:
                asset = str(h.get("asset_code", "")).upper().strip()
                if not asset or asset == "USDC":
                    continue

                # Only include symbols where the bot actually has a ledger qty.
                try:
                    pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(asset)
                    bot_qty = (
                        float(pos.get("qty", 0.0) or 0.0)
                        if isinstance(pos, dict)
                        else 0.0
                    )
                except Exception:
                    bot_qty = 0.0

                if bot_qty > 1e-12:
                    holding_full_symbols.append(f"{asset}_USD")

            except Exception:
                continue

        # Fallback: if the ledger knows we hold a coin but the exchange API didn't
        # report it (stale response, mid-settlement, or post-restart), treat it as
        # held so we don't double-enter.
        for asset, pos in (self._pnl_ledger.get("open_positions", {}) or {}).items():
            try:
                if not isinstance(pos, dict):
                    continue
                if float(pos.get("qty", 0.0) or 0.0) > 1e-12:
                    sym = f"{asset.upper()}_USD"
                    if sym not in holding_full_symbols:
                        log.debug(f"holding guard: {asset} held in ledger but missing from exchange holdings — treating as held")
                        holding_full_symbols.append(sym)
            except Exception:
                continue

        start_index = 0
        while start_index < len(crypto_symbols):
            base_symbol = crypto_symbols[start_index].upper().strip()
            full_symbol = f"{base_symbol}_USD"

            # Skip if excluded
            if base_symbol in EXCLUDED_COINS:
                start_index += 1
                continue

            # Skip if already held
            if full_symbol in holding_full_symbols:
                start_index += 1
                continue

            # Neural signals are used as a "permission to start" gate.
            buy_count = self._read_long_dca_signal(base_symbol)
            sell_count = self._read_short_dca_signal(base_symbol)

            start_level = max(1, min(int(TRADE_START_LEVEL or 3), 7))

            # Default behavior: long must be >= start_level and short must be 0
            if not (buy_count >= start_level and sell_count == 0):
                start_index += 1
                continue

            min_cost = self.exchange.get_min_order_cost(full_symbol)
            if min_cost > 0 and allocation_in_usd < min_cost:
                reason = f"Min order ${min_cost:.2f} > alloc ${allocation_in_usd:.2f}"
                pos = positions.get(base_symbol)
                if isinstance(pos, dict):
                    pos["skip_reason"] = reason
                if base_symbol not in self._skipped_coins:
                    self._skipped_coins.add(base_symbol)
                    log.debug(f"skip {base_symbol}: {reason}")
                    self._record_skip(full_symbol, reason)
                start_index += 1
                continue

            self.dca_levels_triggered[base_symbol] = []
            self.trailing_pm.pop(base_symbol, None)

            response = self.place_buy_order(
                full_symbol,
                allocation_in_usd,
            )

            if response is None:
                self._record_skip(full_symbol, f"Buy failed — insufficient funds (alloc ${allocation_in_usd:.2f})")
            else:
                trades_made = True
                # Do NOT pre-trigger any DCA levels. Hardcoded DCA will mark levels only when it hits your loss thresholds.
                self.dca_levels_triggered[base_symbol] = []

                # Fresh trade -> clear any rolling 24h DCA window for this coin
                self._reset_dca_window_for_trade(base_symbol, sold=False)

                # Reset trailing PM state for this coin (fresh trade, fresh trailing logic)
                self.trailing_pm.pop(base_symbol, None)

                log.info(
                    f"New trade {full_symbol} long={buy_count} short={sell_count} alloc=${allocation_in_usd:.2f}"
                )
                time.sleep(5)
                holdings = self.get_holdings()
                holding_full_symbols = []
                for h in holdings.get("results", []):
                    try:
                        asset = str(h.get("asset_code", "")).upper().strip()
                        if not asset or asset == "USDC":
                            continue

                        # Only include symbols where the bot actually has a ledger qty.
                        try:
                            pos = (
                                self._pnl_ledger.get("open_positions", {}) or {}
                            ).get(asset)
                            bot_qty = (
                                float(pos.get("qty", 0.0) or 0.0)
                                if isinstance(pos, dict)
                                else 0.0
                            )
                        except Exception:
                            bot_qty = 0.0

                        if bot_qty > 1e-12:
                            holding_full_symbols.append(f"{asset}_USD")
                    except Exception:
                        continue

            start_index += 1

        # If any trades were made, recalculate the cost basis
        if trades_made:
            time.sleep(5)
            log.info("Trades made — recalculating cost basis")
            new_cost_basis = self.calculate_cost_basis()
            if new_cost_basis:
                self.cost_basis = new_cost_basis
                log.info("Cost basis recalculated")
            else:
                log.warning("Cost basis recalculation failed")

        # --- GUI HUB STATUS WRITE ---
        try:
            status = {
                "timestamp": utcnow(),
                "account": {
                    "total_account_value": total_account_value,
                    "buying_power": buying_power,
                    "holdings_sell_value": holdings_sell_value,
                    "holdings_buy_value": holdings_buy_value,
                    "percent_in_trade": in_use,
                    # trailing PM config (matches what's printed above current trades)
                    "pm_start_pct_no_dca": float(
                        getattr(self, "pm_start_pct_no_dca", 0.0)
                    ),
                    "pm_start_pct_with_dca": float(
                        getattr(self, "pm_start_pct_with_dca", 0.0)
                    ),
                    "trailing_gap_pct": float(getattr(self, "trailing_gap_pct", 0.0)),
                },
                "positions": positions,
            }
            self._write_trader_status(status)

            now = time.time()
            if now - self._last_history_write_ts >= 300:
                self._last_history_write_ts = now
                self._append_jsonl(
                    ACCOUNT_VALUE_HISTORY_PATH,
                    {"ts": utcnow(), "total_account_value": total_account_value},
                )
        except Exception as e:
            pt_errors.emit(
                f"trader-{EXCHANGE_KEY}",
                level="warning",
                message=f"Status write failed: {e}",
                detail="The trader could not write its status snapshot this cycle. The UI may show stale position/balance data until the next successful write.",
            )

        # Positions summary notification (configurable interval)
        try:
            now = time.time()
            _interval_min = int(_pt_env.get_config().get("ntfy_summary_interval_minutes") or 60)
            _summary_interval = max(5, _interval_min) * 60 if _interval_min > 0 else 0
            if _summary_interval > 0 and now - self._last_notify_summary_ts >= _summary_interval:
                self._last_notify_summary_ts = now
                import pt_notify
                pt_notify.notify_positions_summary(
                    positions, status["account"], ACCOUNT_VALUE_HISTORY_PATH,
                    exchange=EXCHANGE_KEY,
                )
        except Exception:
            pass

        try:
            self.exchange.tick()
        except Exception as e:
            log.debug(f"exchange.tick() failed: {e}")

        _mt_total = time.time() - _mt_start
        if _mt_total > 5 or not hasattr(self, "_mt_logged"):
            self._mt_logged = True
            log.debug(f"loop total={_mt_total:.1f}s prices={_price_dur:.1f}s")

    def run(self):
        while True:
            try:
                self.manage_trades()
                time.sleep(0.5)
            except Exception as e:
                log.exception("manage_trades crashed")
                pt_errors.emit(
                    f"trader-{EXCHANGE_KEY}",
                    level="error",
                    message=f"manage_trades crashed: {e}",
                    detail="The main trading loop raised an unhandled exception. The loop will restart automatically on the next iteration, but open orders or pending fills may not have been processed this cycle.",
                )


if __name__ == "__main__":
    exchange = load_exchange(EXCHANGE_KEY)
    log.info(f"Exchange: {EXCHANGE_KEY}")
    t0 = time.time()
    trading_bot = CryptoAPITrading(exchange)
    log.info(f"Init took {time.time() - t0:.1f}s")
    trading_bot.run()
