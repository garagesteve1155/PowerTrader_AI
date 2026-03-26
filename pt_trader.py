import base64
import datetime
import json
import uuid
import time
import math
from typing import Any, Dict, Optional
import requests
from nacl.signing import SigningKey
import os
import colorama
from colorama import Fore, Style
import traceback
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from pt_exchange_api import (
    initialize_exchange,
    buy as exchange_buy,
    sell as exchange_sell,
    get_exchange_mode,
    get_balance as exchange_get_balance,
    get_current_price as exchange_get_current_price,
    get_trade_history as exchange_get_trade_history,
)
from pt_kucoin_api import load_kucoin_credentials

# -----------------------------
# GUI HUB OUTPUTS
# -----------------------------
HUB_DATA_DIR = os.environ.get("POWERTRADER_HUB_DIR", os.path.join(os.path.dirname(__file__), "hub_data"))
os.makedirs(HUB_DATA_DIR, exist_ok=True)

_EXPLICIT_MODE = (os.getenv("POWERTRADER_EXECUTION_MODE") or os.getenv("EXCHANGE_MODE") or "").strip().lower()
_PAPER_REQUESTED = (os.getenv("PAPER_TRADING_ONLY", "").strip().lower() == "true") or _EXPLICIT_MODE in ("paper", "simulator", "true", "1", "yes")
RUNTIME_DATA_DIR = os.path.join(HUB_DATA_DIR, "paper") if _PAPER_REQUESTED else HUB_DATA_DIR
os.makedirs(RUNTIME_DATA_DIR, exist_ok=True)

TRADER_STATUS_PATH = os.path.join(RUNTIME_DATA_DIR, "trader_status.json")
TRADE_HISTORY_PATH = os.path.join(RUNTIME_DATA_DIR, "trade_history.jsonl")
PNL_LEDGER_PATH = os.path.join(RUNTIME_DATA_DIR, "pnl_ledger.json")
ACCOUNT_VALUE_HISTORY_PATH = os.path.join(RUNTIME_DATA_DIR, "account_value_history.jsonl")
SIMULATOR_LOG_PATH = os.path.join(RUNTIME_DATA_DIR, "simulator_trades.json")
os.environ.setdefault("POWERTRADER_SIMULATOR_LOG", SIMULATOR_LOG_PATH)
if _PAPER_REQUESTED:
    os.environ.setdefault("PAPER_TRADING_ONLY", "true")
    os.environ.setdefault("EXCHANGE_MODE", "SIMULATOR")
    os.environ.setdefault("POWERTRADER_EXECUTION_MODE", "paper")
else:
    os.environ.setdefault("POWERTRADER_EXECUTION_MODE", "live")



# Initialize colorama
colorama.init(autoreset=True)

# -----------------------------
# GUI SETTINGS (coins list + main_neural_dir)
# -----------------------------
_GUI_SETTINGS_PATH = os.environ.get("POWERTRADER_GUI_SETTINGS") or os.path.join(
	os.path.dirname(os.path.abspath(__file__)),
	"gui_settings.json"
)

_gui_settings_cache = {
	"mtime": None,
	"coins": ['BTC', 'ETH', 'XRP', 'BNB', 'DOGE'],  # fallback defaults
	"main_neural_dir": None,
	"trade_start_level": 3,
	"start_allocation_pct": 0.005,
	"dca_multiplier": 2.0,
	"dca_levels": [-2.5, -5.0, -10.0, -20.0, -30.0, -40.0, -50.0],
	"max_dca_buys_per_24h": 2,

	# Trailing PM settings (defaults match previous hardcoded behavior)
	"pm_start_pct_no_dca": 5.0,
	"pm_start_pct_with_dca": 2.5,
	"trailing_gap_pct": 0.5,
}







def _load_gui_settings() -> dict:
	"""
	Reads gui_settings.json and returns a dict with:
	- coins: uppercased list
	- main_neural_dir: string (may be None)
	Caches by mtime so it is cheap to call frequently.
	"""
	try:
		if not os.path.isfile(_GUI_SETTINGS_PATH):
			return dict(_gui_settings_cache)

		mtime = os.path.getmtime(_GUI_SETTINGS_PATH)
		if _gui_settings_cache["mtime"] == mtime:
			return dict(_gui_settings_cache)

		with open(_GUI_SETTINGS_PATH, "r", encoding="utf-8") as f:
			data = json.load(f) or {}

		coins = data.get("coins", None)
		if not isinstance(coins, list) or not coins:
			coins = list(_gui_settings_cache["coins"])
		coins = [str(c).strip().upper() for c in coins if str(c).strip()]
		if not coins:
			coins = list(_gui_settings_cache["coins"])

		main_neural_dir = data.get("main_neural_dir", None)
		if isinstance(main_neural_dir, str):
			main_neural_dir = main_neural_dir.strip() or None
		else:
			main_neural_dir = None

		trade_start_level = data.get("trade_start_level", _gui_settings_cache.get("trade_start_level", 3))
		try:
			trade_start_level = int(float(trade_start_level))
		except Exception:
			trade_start_level = int(_gui_settings_cache.get("trade_start_level", 3))
		trade_start_level = max(1, min(trade_start_level, 7))

		start_allocation_pct = data.get("start_allocation_pct", _gui_settings_cache.get("start_allocation_pct", 0.005))
		try:
			start_allocation_pct = float(str(start_allocation_pct).replace("%", "").strip())
		except Exception:
			start_allocation_pct = float(_gui_settings_cache.get("start_allocation_pct", 0.005))
		if start_allocation_pct < 0.0:
			start_allocation_pct = 0.0

		dca_multiplier = data.get("dca_multiplier", _gui_settings_cache.get("dca_multiplier", 2.0))
		try:
			dca_multiplier = float(str(dca_multiplier).strip())
		except Exception:
			dca_multiplier = float(_gui_settings_cache.get("dca_multiplier", 2.0))
		if dca_multiplier < 0.0:
			dca_multiplier = 0.0

		dca_levels = data.get("dca_levels", _gui_settings_cache.get("dca_levels", [-2.5, -5.0, -10.0, -20.0, -30.0, -40.0, -50.0]))
		if not isinstance(dca_levels, list) or not dca_levels:
			dca_levels = list(_gui_settings_cache.get("dca_levels", [-2.5, -5.0, -10.0, -20.0, -30.0, -40.0, -50.0]))
		parsed = []
		for v in dca_levels:
			try:
				parsed.append(float(v))
			except Exception:
				pass
		if parsed:
			dca_levels = parsed
		else:
			dca_levels = list(_gui_settings_cache.get("dca_levels", [-2.5, -5.0, -10.0, -20.0, -30.0, -40.0, -50.0]))

		max_dca_buys_per_24h = data.get("max_dca_buys_per_24h", _gui_settings_cache.get("max_dca_buys_per_24h", 2))
		try:
			max_dca_buys_per_24h = int(float(max_dca_buys_per_24h))
		except Exception:
			max_dca_buys_per_24h = int(_gui_settings_cache.get("max_dca_buys_per_24h", 2))
		if max_dca_buys_per_24h < 0:
			max_dca_buys_per_24h = 0


		# --- Trailing PM settings ---
		pm_start_pct_no_dca = data.get("pm_start_pct_no_dca", _gui_settings_cache.get("pm_start_pct_no_dca", 5.0))
		try:
			pm_start_pct_no_dca = float(str(pm_start_pct_no_dca).replace("%", "").strip())
		except Exception:
			pm_start_pct_no_dca = float(_gui_settings_cache.get("pm_start_pct_no_dca", 5.0))
		if pm_start_pct_no_dca < 0.0:
			pm_start_pct_no_dca = 0.0

		pm_start_pct_with_dca = data.get("pm_start_pct_with_dca", _gui_settings_cache.get("pm_start_pct_with_dca", 2.5))
		try:
			pm_start_pct_with_dca = float(str(pm_start_pct_with_dca).replace("%", "").strip())
		except Exception:
			pm_start_pct_with_dca = float(_gui_settings_cache.get("pm_start_pct_with_dca", 2.5))
		if pm_start_pct_with_dca < 0.0:
			pm_start_pct_with_dca = 0.0

		trailing_gap_pct = data.get("trailing_gap_pct", _gui_settings_cache.get("trailing_gap_pct", 0.5))
		try:
			trailing_gap_pct = float(str(trailing_gap_pct).replace("%", "").strip())
		except Exception:
			trailing_gap_pct = float(_gui_settings_cache.get("trailing_gap_pct", 0.5))
		if trailing_gap_pct < 0.0:
			trailing_gap_pct = 0.0


		_gui_settings_cache["mtime"] = mtime
		_gui_settings_cache["coins"] = coins
		_gui_settings_cache["main_neural_dir"] = main_neural_dir
		_gui_settings_cache["trade_start_level"] = trade_start_level
		_gui_settings_cache["start_allocation_pct"] = start_allocation_pct
		_gui_settings_cache["dca_multiplier"] = dca_multiplier
		_gui_settings_cache["dca_levels"] = dca_levels
		_gui_settings_cache["max_dca_buys_per_24h"] = max_dca_buys_per_24h

		_gui_settings_cache["pm_start_pct_no_dca"] = pm_start_pct_no_dca
		_gui_settings_cache["pm_start_pct_with_dca"] = pm_start_pct_with_dca
		_gui_settings_cache["trailing_gap_pct"] = trailing_gap_pct


		return {
			"mtime": mtime,
			"coins": list(coins),
			"main_neural_dir": main_neural_dir,
			"trade_start_level": trade_start_level,
			"start_allocation_pct": start_allocation_pct,
			"dca_multiplier": dca_multiplier,
			"dca_levels": list(dca_levels),
			"max_dca_buys_per_24h": max_dca_buys_per_24h,

			"pm_start_pct_no_dca": pm_start_pct_no_dca,
			"pm_start_pct_with_dca": pm_start_pct_with_dca,
			"trailing_gap_pct": trailing_gap_pct,
		}




	except Exception:
		return dict(_gui_settings_cache)


def _build_base_paths(main_dir_in: str, coins_in: list) -> dict:
	"""
	Safety rule:
	- BTC uses main_dir directly
	- other coins use <main_dir>/<SYM> ONLY if that folder exists
	  (no fallback to BTC folder — avoids corrupting BTC data)
	"""
	out = {"BTC": main_dir_in}
	try:
		for sym in coins_in:
			sym = str(sym).strip().upper()
			if not sym:
				continue
			if sym == "BTC":
				out["BTC"] = main_dir_in
				continue
			sub = os.path.join(main_dir_in, sym)
			if os.path.isdir(sub):
				out[sym] = sub
	except Exception:
		pass
	return out


# Live globals (will be refreshed inside manage_trades())
crypto_symbols = ['BTC', 'ETH', 'XRP', 'BNB', 'DOGE']

# Default main_dir behavior if settings are missing
main_dir = os.getcwd()
base_paths = {"BTC": main_dir}
TRADE_START_LEVEL = 3
START_ALLOC_PCT = 0.005
DCA_MULTIPLIER = 2.0
DCA_LEVELS = [-2.5, -5.0, -10.0, -20.0, -30.0, -40.0, -50.0]
MAX_DCA_BUYS_PER_24H = 2

# Trailing PM hot-reload globals (defaults match previous hardcoded behavior)
TRAILING_GAP_PCT = 0.5
PM_START_PCT_NO_DCA = 5.0
PM_START_PCT_WITH_DCA = 2.5



_last_settings_mtime = None




def _refresh_paths_and_symbols():
	"""
	Hot-reload GUI settings while trader is running.
	Updates globals: crypto_symbols, main_dir, base_paths,
	                TRADE_START_LEVEL, START_ALLOC_PCT, DCA_MULTIPLIER, DCA_LEVELS, MAX_DCA_BUYS_PER_24H,
	                TRAILING_GAP_PCT, PM_START_PCT_NO_DCA, PM_START_PCT_WITH_DCA
	"""
	global crypto_symbols, main_dir, base_paths
	global TRADE_START_LEVEL, START_ALLOC_PCT, DCA_MULTIPLIER, DCA_LEVELS, MAX_DCA_BUYS_PER_24H
	global TRAILING_GAP_PCT, PM_START_PCT_NO_DCA, PM_START_PCT_WITH_DCA
	global _last_settings_mtime


	s = _load_gui_settings()
	mtime = s.get("mtime", None)

	# If settings file doesn't exist, keep current defaults
	if mtime is None:
		return

	if _last_settings_mtime == mtime:
		return

	_last_settings_mtime = mtime

	coins = s.get("coins") or list(crypto_symbols)
	mndir = s.get("main_neural_dir") or main_dir
	TRADE_START_LEVEL = max(1, min(int(s.get("trade_start_level", TRADE_START_LEVEL) or TRADE_START_LEVEL), 7))
	START_ALLOC_PCT = float(s.get("start_allocation_pct", START_ALLOC_PCT) or START_ALLOC_PCT)
	if START_ALLOC_PCT < 0.0:
		START_ALLOC_PCT = 0.0

	DCA_MULTIPLIER = float(s.get("dca_multiplier", DCA_MULTIPLIER) or DCA_MULTIPLIER)
	if DCA_MULTIPLIER < 0.0:
		DCA_MULTIPLIER = 0.0

	DCA_LEVELS = list(s.get("dca_levels", DCA_LEVELS) or DCA_LEVELS)

	try:
		MAX_DCA_BUYS_PER_24H = int(float(s.get("max_dca_buys_per_24h", MAX_DCA_BUYS_PER_24H) or MAX_DCA_BUYS_PER_24H))
	except Exception:
		MAX_DCA_BUYS_PER_24H = int(MAX_DCA_BUYS_PER_24H)
	if MAX_DCA_BUYS_PER_24H < 0:
		MAX_DCA_BUYS_PER_24H = 0


	# Trailing PM hot-reload values
	TRAILING_GAP_PCT = float(s.get("trailing_gap_pct", TRAILING_GAP_PCT) or TRAILING_GAP_PCT)
	if TRAILING_GAP_PCT < 0.0:
		TRAILING_GAP_PCT = 0.0

	PM_START_PCT_NO_DCA = float(s.get("pm_start_pct_no_dca", PM_START_PCT_NO_DCA) or PM_START_PCT_NO_DCA)
	if PM_START_PCT_NO_DCA < 0.0:
		PM_START_PCT_NO_DCA = 0.0

	PM_START_PCT_WITH_DCA = float(s.get("pm_start_pct_with_dca", PM_START_PCT_WITH_DCA) or PM_START_PCT_WITH_DCA)
	if PM_START_PCT_WITH_DCA < 0.0:
		PM_START_PCT_WITH_DCA = 0.0


	# Keep it safe if folder isn't real on this machine
	if not os.path.isdir(mndir):
		mndir = os.getcwd()

	crypto_symbols = list(coins)
	main_dir = mndir
	base_paths = _build_base_paths(main_dir, crypto_symbols)






# API STUFF
# Allow running `pt_trader.py` in paper/simulator mode without live API keys.
API_KEY = ""
BASE64_PRIVATE_KEY = ""

# If PAPER_TRADING_ONLY is set, we will run in simulator mode and do not require
# live KuCoin API credentials. Otherwise, attempt to read credentials and
# exit if they're missing.
PAPER_ONLY = os.getenv('PAPER_TRADING_ONLY', '').lower() == 'true'
if not PAPER_ONLY:
    try:
        api_key, api_secret, api_passphrase = load_kucoin_credentials()
        if not api_key or not api_secret or not api_passphrase:
            raise ValueError("Kucoin credentials are missing.")
        initialize_exchange(mode='KUCOIN_REAL', initial_usdt=float(os.getenv('SIM_INITIAL_USDT', '50')))
    except Exception as e:
        print(f"[PowerTrader] Failed to initialize Kucoin exchange: {e}")
        raise SystemExit(1)
else:
    print("[PowerTrader] PAPER_TRADING_ONLY detected — running in SIMULATOR mode without live API keys.")

# Initialize optional exchange adapter (SIMULATOR or KUCOIN_REAL)
EXCHANGE_MODE = os.getenv('EXCHANGE_MODE', os.getenv('PAPER_TRADING_ONLY', 'false')).upper()
if EXCHANGE_MODE in ('TRUE', 'FALSE'):
    # PAPER_TRADING_ONLY may be 'true'/'false'
    if EXCHANGE_MODE == 'TRUE' or os.getenv('PAPER_TRADING_ONLY', '').lower() == 'true':
        EXCHANGE_MODE = 'SIMULATOR'
    else:
        EXCHANGE_MODE = 'SIMULATOR'

try:
    if os.getenv('PAPER_TRADING_ONLY', '').lower() == 'true':
        initialize_exchange(mode='SIMULATOR', initial_usdt=float(os.getenv('SIM_INITIAL_USDT', '50')))
    elif os.getenv('EXCHANGE_MODE'):
        initialize_exchange(mode=os.getenv('EXCHANGE_MODE'), initial_usdt=float(os.getenv('SIM_INITIAL_USDT', '50')))
except Exception:
    pass

class CryptoAPITrading:
    def __init__(self):
        # keep a copy of the folder map (same idea as trader.py)
        self.path_map = dict(base_paths)

        self.api_key = API_KEY
        self.private_key = None
        if BASE64_PRIVATE_KEY:
            try:
                private_key_seed = base64.b64decode(BASE64_PRIVATE_KEY)
                if private_key_seed:
                    self.private_key = SigningKey(private_key_seed)
            except Exception:
                self.private_key = None
        self.base_url = "https://api.kucoin.com"

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



        self.cost_basis = self.calculate_cost_basis()  # Initialize cost basis at startup
        self.initialize_dca_levels()  # Initialize DCA levels based on historical buy orders

        # GUI hub persistence
        self._pnl_ledger = self._load_pnl_ledger()
        self._reconcile_pending_orders()


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

        self._dca_buy_ts = {}         # { "BTC": [ts, ts, ...] } (DCA buys only)
        self._dca_last_sell_ts = {}   # { "BTC": ts_of_last_sell }
        self._seed_dca_window_from_history()








    def _atomic_write_json(self, path: str, data: dict) -> None:
        try:
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    def _append_jsonl(self, path: str, obj: dict) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj) + "\n")
        except Exception:
            pass

    def _load_pnl_ledger(self) -> dict:
        try:
            if os.path.isfile(PNL_LEDGER_PATH):
                with open(PNL_LEDGER_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                if not isinstance(data, dict):
                    data = {}
                # Back-compat upgrades
                data.setdefault("total_realized_profit_usd", 0.0)
                data.setdefault("last_updated_ts", time.time())
                data.setdefault("open_positions", {})   # { "BTC": {"usd_cost": float, "qty": float} }
                data.setdefault("pending_orders", {})   # { "<order_id>": {...} }
                return data
        except Exception:
            pass
        return {
            "total_realized_profit_usd": 0.0,
            "last_updated_ts": time.time(),
            "open_positions": {},
            "pending_orders": {},
        }

    def _save_pnl_ledger(self) -> None:
        try:
            self._pnl_ledger["last_updated_ts"] = time.time()
            self._atomic_write_json(PNL_LEDGER_PATH, self._pnl_ledger)
        except Exception:
            pass

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

    def _get_buying_power(self) -> float:
        try:
            acct = self.get_account()
            if isinstance(acct, dict):
                return float(acct.get("buying_power", 0.0) or 0.0)
        except Exception:
            pass
        return 0.0

    def _get_order_by_id(self, symbol: str, order_id: str) -> Optional[dict]:
        try:
            orders = self.get_orders(symbol)
            results = orders.get("results", []) if isinstance(orders, dict) else []
            for o in results:
                try:
                    if o.get("id") == order_id:
                        return o
                except Exception:
                    continue
        except Exception:
            pass
        return None

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
                        total_notional += (q * p)
                except Exception:
                    continue

            avg_price = (total_notional / total_qty) if (total_qty > 0.0 and total_notional > 0.0) else None

            # Fallbacks if executions are not populated yet
            if total_qty <= 0.0:
                for k in ("filled_asset_quantity", "filled_quantity", "asset_quantity", "quantity"):
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

            return float(total_qty), (float(avg_price) if avg_price is not None else None)
        except Exception:
            return 0.0, None

    def _wait_for_order_terminal(self, symbol: str, order_id: str) -> Optional[dict]:
        """Blocks until order is filled/canceled/rejected, then returns the order dict."""
        terminal = {"filled", "canceled", "cancelled", "rejected", "failed", "error"}
        while True:
            o = self._get_order_by_id(symbol, order_id)
            if not o:
                time.sleep(1)
                continue
            st = str(o.get("state", "")).lower().strip()
            if st in terminal:
                return o
            time.sleep(1)

    def _reconcile_pending_orders(self) -> None:
        """
        If the hub/trader restarts mid-order, we keep the pre-order buying_power on disk and
        finish the accounting once the order shows as terminal in the exchange adapter.
        """
        try:
            pending = self._pnl_ledger.get("pending_orders", {})
            if not isinstance(pending, dict) or not pending:
                return

            # Loop until everything pending is resolved (matches your design: bot waits here).
            while True:
                pending = self._pnl_ledger.get("pending_orders", {})
                if not isinstance(pending, dict) or not pending:
                    break

                progressed = False

                for order_id, info in list(pending.items()):
                    try:
                        if self._trade_history_has_order_id(order_id):
                            # Already recorded (e.g., crash after writing history) -> just clear pending.
                            self._pnl_ledger["pending_orders"].pop(order_id, None)
                            self._save_pnl_ledger()
                            progressed = True
                            continue

                        symbol = str(info.get("symbol", "")).strip()
                        side = str(info.get("side", "")).strip().lower()
                        bp_before = float(info.get("buying_power_before", 0.0) or 0.0)

                        if not symbol or not side or not order_id:
                            self._pnl_ledger["pending_orders"].pop(order_id, None)
                            self._save_pnl_ledger()
                            progressed = True
                            continue

                        order = self._wait_for_order_terminal(symbol, order_id)
                        if not order:
                            continue

                        state = str(order.get("state", "")).lower().strip()
                        if state != "filled":
                            # Not filled -> no trade to record, clear pending.
                            self._pnl_ledger["pending_orders"].pop(order_id, None)
                            self._save_pnl_ledger()
                            progressed = True
                            continue

                        filled_qty, avg_price = self._extract_fill_from_order(order)
                        bp_after = self._get_buying_power()
                        bp_delta = float(bp_after) - float(bp_before)

                        self._record_trade(
                            side=side,
                            symbol=symbol,
                            qty=float(filled_qty),
                            price=float(avg_price) if avg_price is not None else None,
                            avg_cost_basis=info.get("avg_cost_basis", None),
                            pnl_pct=info.get("pnl_pct", None),
                            tag=info.get("tag", None),
                            order_id=order_id,
                            fees_usd=None,
                            buying_power_before=bp_before,
                            buying_power_after=bp_after,
                            buying_power_delta=bp_delta,
                        )

                        # Clear pending now that we recorded it
                        self._pnl_ledger["pending_orders"].pop(order_id, None)
                        self._save_pnl_ledger()
                        progressed = True

                    except Exception:
                        continue

                if not progressed:
                    time.sleep(1)

        except Exception:
            pass

    def _record_trade(
        self,
        side: str,
        symbol: str,
        qty: float,
        price: Optional[float] = None,
        avg_cost_basis: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        tag: Optional[str] = None,
        order_id: Optional[str] = None,
        fees_usd: Optional[float] = None,
        buying_power_before: Optional[float] = None,
        buying_power_after: Optional[float] = None,
        buying_power_delta: Optional[float] = None,
    ) -> None:
        """
        Minimal local ledger for GUI:
        - append trade_history.jsonl
        - update pnl_ledger.json on sells (now using buying power delta when available)
        - persist per-coin open position cost (USD) so realized profit is exact
        """
        ts = time.time()

        side_l = str(side or "").lower().strip()
        base = str(symbol or "").upper().split("-")[0].strip()

        # Ensure ledger keys exist (back-compat)
        try:
            if not isinstance(self._pnl_ledger, dict):
                self._pnl_ledger = {}
            self._pnl_ledger.setdefault("total_realized_profit_usd", 0.0)
            self._pnl_ledger.setdefault("open_positions", {})
            self._pnl_ledger.setdefault("pending_orders", {})
        except Exception:
            pass

        realized = None
        position_cost_used = None
        position_cost_after = None

        # --- Exact USD-based accounting (your design) ---
        if base and (buying_power_delta is not None):
            try:
                bp_delta = float(buying_power_delta)
            except Exception:
                bp_delta = None

            if bp_delta is not None:
                try:
                    open_pos = self._pnl_ledger.get("open_positions", {})
                    if not isinstance(open_pos, dict):
                        open_pos = {}
                        self._pnl_ledger["open_positions"] = open_pos

                    pos = open_pos.get(base, None)
                    if not isinstance(pos, dict):
                        pos = {"usd_cost": 0.0, "qty": 0.0}
                        open_pos[base] = pos

                    pos_usd_cost = float(pos.get("usd_cost", 0.0) or 0.0)
                    pos_qty = float(pos.get("qty", 0.0) or 0.0)

                    q = float(qty or 0.0)

                    if side_l == "buy":
                        usd_used = -bp_delta  # buying power drops on buys
                        if usd_used < 0.0:
                            usd_used = 0.0

                        pos["usd_cost"] = float(pos_usd_cost) + float(usd_used)
                        pos["qty"] = float(pos_qty) + float(q if q > 0.0 else 0.0)

                        position_cost_after = float(pos["usd_cost"])

                        # Save because open position changed (needs to persist across restarts)
                        self._save_pnl_ledger()

                    elif side_l == "sell":
                        usd_got = bp_delta  # buying power rises on sells
                        if usd_got < 0.0:
                            usd_got = 0.0

                        # If partial sell ever happens, allocate cost pro-rata by qty.
                        if pos_qty > 0.0 and q > 0.0:
                            frac = min(1.0, float(q) / float(pos_qty))
                        else:
                            frac = 1.0

                        cost_used = float(pos_usd_cost) * float(frac)
                        pos["usd_cost"] = float(pos_usd_cost) - float(cost_used)
                        pos["qty"] = float(pos_qty) - float(q if q > 0.0 else 0.0)

                        position_cost_used = float(cost_used)
                        position_cost_after = float(pos.get("usd_cost", 0.0) or 0.0)

                        realized = float(usd_got) - float(cost_used)
                        self._pnl_ledger["total_realized_profit_usd"] = float(self._pnl_ledger.get("total_realized_profit_usd", 0.0) or 0.0) + float(realized)

                        # Clean up tiny dust
                        if float(pos.get("qty", 0.0) or 0.0) <= 1e-12 or float(pos.get("usd_cost", 0.0) or 0.0) <= 1e-6:
                            open_pos.pop(base, None)

                        self._save_pnl_ledger()

                except Exception:
                    pass

        # --- Fallback (old behavior) if we couldn't compute from buying power ---
        if realized is None and side_l == "sell" and price is not None and avg_cost_basis is not None:
            try:
                fee_val = float(fees_usd) if fees_usd is not None else 0.0
                realized = (float(price) - float(avg_cost_basis)) * float(qty) - fee_val
                self._pnl_ledger["total_realized_profit_usd"] = float(self._pnl_ledger.get("total_realized_profit_usd", 0.0)) + float(realized)
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
            "avg_cost_basis": avg_cost_basis,
            "pnl_pct": pnl_pct,
            "fees_usd": fees_usd,
            "realized_profit_usd": realized,
            "order_id": order_id,
            "buying_power_before": float(buying_power_before) if buying_power_before is not None else None,
            "buying_power_after": float(buying_power_after) if buying_power_after is not None else None,
            "buying_power_delta": float(buying_power_delta) if buying_power_delta is not None else None,
            "position_cost_used_usd": float(position_cost_used) if position_cost_used is not None else None,
            "position_cost_after_usd": float(position_cost_after) if position_cost_after is not None else None,
        }
        self._append_jsonl(TRADE_HISTORY_PATH, entry)




    def _write_trader_status(self, status: dict) -> None:
        self._atomic_write_json(TRADER_STATUS_PATH, status)

    @staticmethod
    def _get_current_timestamp() -> int:
        return int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp())

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
        - Start gate: start trades at level 3+
        - DCA assist: levels 4-7 map to trader DCA stages 0-3 (trade starts at level 3 => stage 0)
        """
        sym = str(symbol).upper().strip()
        folder = base_paths.get(sym, main_dir if sym == "BTC" else os.path.join(main_dir, sym))
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
        - Start gate: start trades at level 3+
        - DCA assist: levels 4-7 map to trader DCA stages 0-3 (trade starts at level 3 => stage 0)
        """
        sym = str(symbol).upper().strip()
        folder = base_paths.get(sym, main_dir if sym == "BTC" else os.path.join(main_dir, sym))
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
        """
        Reads low_bound_prices.html from the per-coin folder and returns a list of LONG (blue) price levels.

        Returned ordering is highest->lowest so:
          N1 = 1st blue line (top)
          ...
          N7 = 7th blue line (bottom)
        """
        sym = str(symbol).upper().strip()
        folder = base_paths.get(sym, main_dir if sym == "BTC" else os.path.join(main_dir, sym))
        path = os.path.join(folder, "low_bound_prices.html")
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = (f.read() or "").strip()
            if not raw:
                return []

            # Normalize common formats: python-list, comma-separated, newline-separated
            raw = raw.strip().strip("[]()")
            raw = raw.replace(",", " ").replace(";", " ").replace("|", " ")
            raw = raw.replace("\n", " ").replace("\t", " ")
            parts = [p for p in raw.split() if p]

            vals = []
            for p in parts:
                try:
                    vals.append(float(p))
                except Exception:
                    continue

            # De-dupe, then sort high->low for stable N1..N7 mapping
            out = []
            seen = set()
            for v in vals:
                k = round(float(v), 12)
                if k in seen:
                    continue
                seen.add(k)
                out.append(float(v))
            out.sort(reverse=True)
            return out
        except Exception:
            return []



    def initialize_dca_levels(self):

        """
        Initializes the DCA levels_triggered dictionary based on the number of buy orders
        that have occurred after the first buy order following the most recent sell order
        for each cryptocurrency.
        """
        holdings = self.get_holdings()
        if not holdings or "results" not in holdings:
            print("No holdings found. Skipping DCA levels initialization.")
            return

        for holding in holdings.get("results", []):
            symbol = holding["asset_code"]

            full_symbol = f"{symbol}-USD"
            orders = self.get_orders(full_symbol)
            
            if not orders or "results" not in orders:
                print(f"No orders found for {full_symbol}. Skipping.")
                continue

            # Filter for filled buy and sell orders
            filled_orders = [
                order for order in orders["results"]
                if order["state"] == "filled" and order["side"] in ["buy", "sell"]
            ]
            
            if not filled_orders:
                print(f"No filled buy or sell orders for {full_symbol}. Skipping.")
                continue

            def _order_ts(order):
                raw_ts = order.get("created_at", order.get("createdAt", 0))
                try:
                    return float(raw_ts or 0)
                except Exception:
                    return 0.0

            # Sort orders by creation time in ascending order (oldest first)
            filled_orders.sort(key=_order_ts)

            # Find the timestamp of the most recent sell order
            most_recent_sell_time = None
            for order in reversed(filled_orders):
                if order["side"] == "sell":
                    most_recent_sell_time = _order_ts(order)
                    break

            # Determine the cutoff time for buy orders
            if most_recent_sell_time:
                # Find all buy orders after the most recent sell
                relevant_buy_orders = [
                    order for order in filled_orders
                    if order["side"] == "buy" and _order_ts(order) > most_recent_sell_time
                ]
                if not relevant_buy_orders:
                    print(f"No buy orders after the most recent sell for {full_symbol}.")
                    self.dca_levels_triggered[symbol] = []
                    continue
                print(f"Most recent sell for {full_symbol} at {most_recent_sell_time}.")
            else:
                # If no sell orders, consider all buy orders
                relevant_buy_orders = [
                    order for order in filled_orders
                    if order["side"] == "buy"
                ]
                if not relevant_buy_orders:
                    print(f"No buy orders for {full_symbol}. Skipping.")
                    self.dca_levels_triggered[symbol] = []
                    continue
                print(f"No sell orders found for {full_symbol}. Considering all buy orders.")

            # Ensure buy orders are sorted by creation time ascending
            relevant_buy_orders.sort(key=_order_ts)

            # Identify the first buy order in the relevant list
            first_buy_order = relevant_buy_orders[0]
            first_buy_time = _order_ts(first_buy_order)

            # Count the number of buy orders after the first buy
            buy_orders_after_first = [
                order for order in relevant_buy_orders
                if _order_ts(order) > first_buy_time
            ]

            triggered_levels_count = len(buy_orders_after_first)

            # Track DCA by stage index (0, 1, 2, ...) rather than % values.
            # This makes neural-vs-hardcoded clean, and allows repeating the -50% stage indefinitely.
            self.dca_levels_triggered[symbol] = list(range(triggered_levels_count))
            print(f"Initialized DCA stages for {symbol}: {triggered_levels_count}")


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
                    base = sym_full.split("-")[0].strip() if sym_full else ""
                    if not base:
                        continue

                    try:
                        ts_f = float(ts)
                    except Exception:
                        continue

                    if side == "sell":
                        prev = float(self._dca_last_sell_ts.get(base, 0.0) or 0.0)
                        if ts_f > prev:
                            self._dca_last_sell_ts[base] = ts_f

                    elif side == "buy" and tag == "DCA":
                        self._dca_buy_ts.setdefault(base, []).append(ts_f)

        except Exception:
            return

        # Keep only DCA buys after the last sell (current trade) and within rolling 24h
        for base, ts_list in list(self._dca_buy_ts.items()):
            last_sell = float(self._dca_last_sell_ts.get(base, 0.0) or 0.0)
            kept = [t for t in ts_list if (t > last_sell) and (t >= cutoff)]
            kept.sort()
            self._dca_buy_ts[base] = kept


    def _dca_window_count(self, base_symbol: str, now_ts: Optional[float] = None) -> int:
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


    def _reset_dca_window_for_trade(self, base_symbol: str, sold: bool = False, ts: Optional[float] = None) -> None:
        base = str(base_symbol).upper().strip()
        if not base:
            return
        if sold:
            self._dca_last_sell_ts[base] = float(ts if ts is not None else time.time())
        self._dca_buy_ts[base] = []


    def make_api_request(self, method: str, path: str, body: Optional[str] = "") -> Any:

        timestamp = self._get_current_timestamp()
        headers = self.get_authorization_header(method, path, body, timestamp)
        url = self.base_url + path

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=10)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=json.loads(body), timeout=10)

            response.raise_for_status()
            return response.json()
        except requests.HTTPError as http_err:
            try:
                # Parse and return the JSON error response
                error_response = response.json()
                return error_response  # Return the JSON error for further handling
            except Exception:
                return None
        except Exception:
            return None

    def get_authorization_header(
            self, method: str, path: str, body: str, timestamp: int
    ) -> Dict[str, str]:
        if not self.private_key or not self.api_key:
            raise RuntimeError("Trading API signing credentials are not configured for this process.")
        message_to_sign = f"{self.api_key}{timestamp}{path}{method}{body}"
        signed = self.private_key.sign(message_to_sign.encode("utf-8"))

        return {
            "x-api-key": self.api_key,
            "x-signature": base64.b64encode(signed.signature).decode("utf-8"),
            "x-timestamp": str(timestamp),
        }

    def get_account(self) -> Any:
        balance = exchange_get_balance() or {}
        return {
            "buying_power": float(balance.get("usdt", 0.0) or 0.0),
            "results": [
                {
                    "currency": "USDT",
                    "available": float(balance.get("usdt", 0.0) or 0.0),
                    "buying_power": float(balance.get("usdt", 0.0) or 0.0),
                }
            ],
        }

    def get_holdings(self) -> Any:
        balance = exchange_get_balance() or {}
        holdings = []
        for symbol, pos in (balance.get("positions") or {}).items():
            qty = float((pos or {}).get("qty", 0.0) or 0.0)
            if qty <= 0:
                continue
            holdings.append(
                {
                    "asset_code": str(symbol).split("-")[0],
                    "total_quantity": qty,
                    "average_price": float((pos or {}).get("entry_price", 0.0) or 0.0),
                }
            )
        return {"results": holdings}

    def get_trading_pairs(self) -> Any:
        pairs = []
        for coin in (self.path_map.keys() if isinstance(self.path_map, dict) else []):
            if coin and coin != "BTC":
                pairs.append({"symbol": f"{coin}-USDT"})
        return pairs

    def get_orders(self, symbol: str) -> Any:
        base_symbol = str(symbol or "").strip().upper()
        if base_symbol.endswith("-USD"):
            base_symbol = base_symbol[:-4]
        if not base_symbol.endswith("-USDT"):
            base_symbol = f"{base_symbol}-USDT"
        history = exchange_get_trade_history(base_symbol, limit=200) or []
        results = []
        for trade in history:
            results.append(
                {
                    "id": trade.get("orderId") or trade.get("timestamp"),
                    "state": "filled",
                    "side": str(trade.get("side", "buy")).lower() if trade.get("side") else "buy",
                    "created_at": trade.get("timestamp"),
                    "executions": [
                        {
                            "quantity": trade.get("qty"),
                            "effective_price": trade.get("entry_price"),
                        }
                    ],
                }
            )
        return {"results": results}

    def calculate_cost_basis(self):
        balance = exchange_get_balance() or {}
        cost_basis = {}
        for symbol, pos in (balance.get("positions") or {}).items():
            qty = float((pos or {}).get("qty", 0.0) or 0.0)
            entry_price = float((pos or {}).get("entry_price", 0.0) or 0.0)
            if qty > 0:
                cost_basis[str(symbol).split("-")[0]] = entry_price
        return cost_basis

    def get_price(self, symbols: list) -> Dict[str, float]:
        buy_prices = {}
        sell_prices = {}
        valid_symbols = []

        for symbol in symbols:
            if symbol == "USDC-USD":
                continue

            lookup = symbol
            if lookup.endswith("-USD"):
                lookup = lookup[:-4]
            if lookup.endswith("-USDT"):
                lookup = lookup[:-5]
            ask = exchange_get_current_price(lookup)
            if ask is not None:
                ask = float(ask)
                bid = float(ask)

                buy_prices[symbol] = ask
                sell_prices[symbol] = bid
                valid_symbols.append(symbol)

                # Update cache for transient failures later
                try:
                    self._last_good_bid_ask[symbol] = {"ask": ask, "bid": bid, "ts": time.time()}
                except Exception:
                    pass
            else:
                cached = None
                try:
                    cached = self._last_good_bid_ask.get(symbol)
                except Exception:
                    cached = None

                if cached:
                    ask = float(cached.get("ask", 0.0) or 0.0)
                    bid = float(cached.get("bid", 0.0) or 0.0)
                    if ask > 0.0 and bid > 0.0:
                        buy_prices[symbol] = ask
                        sell_prices[symbol] = bid
                        valid_symbols.append(symbol)

        return buy_prices, sell_prices, valid_symbols


    def place_buy_order(
        self,
        client_order_id: str,
        side: str,
        order_type: str,
        symbol: str,
        amount_in_usd: float,
        avg_cost_basis: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> Any:
        # Fetch the current price of the asset (for sizing only)
        current_buy_prices, current_sell_prices, valid_symbols = self.get_price([symbol])
        current_price = current_buy_prices[symbol]
        asset_quantity = amount_in_usd / current_price
        asset_quantity = amount_in_usd / current_price

        # If exchange adapter is initialized and indicates SIMULATOR or KUCOIN_REAL, use it
        try:
            mode = get_exchange_mode()
        except Exception:
            mode = None

        if mode in ("SIMULATOR", "KUCOIN_REAL"):
            # Use adapter to place the buy immediately and record the trade locally
            buying_power_before = self._get_buying_power()
            base_sym = symbol.split("-")[0] if "-" in symbol else symbol
            try:
                result = exchange_buy(base_sym, asset_quantity)
            except Exception as e:
                return None

            if result and result.get("success"):
                order_id = result.get("orderId")
                price = result.get("price")
                # attempt to get updated buying power from adapter
                try:
                    bal = exchange_get_balance() or {}
                    buying_power_after = float(bal.get("usdt", buying_power_before))
                except Exception:
                    buying_power_after = self._get_buying_power()

                # Record trade for GUI/history
                try:
                    self._record_trade(
                        side="buy",
                        symbol=symbol,
                        qty=float(asset_quantity),
                        price=float(price) if price is not None else None,
                        avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                        pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                        tag=tag,
                        order_id=order_id,
                        buying_power_before=buying_power_before,
                        buying_power_after=buying_power_after,
                        buying_power_delta=(float(buying_power_after) - float(buying_power_before)),
                    )
                except Exception:
                    pass

                # Return a minimal successful response (no 'errors' key)
                return {"id": order_id, "status": "filled"}
            else:
                return None

        max_retries = 5
        retries = 0

        while retries < max_retries:
            retries += 1
            response = None
            try:
                # Default precision to 8 decimals initially
                rounded_quantity = round(asset_quantity, 8)

                body = {
                    "client_order_id": client_order_id,
                    "side": side,
                    "type": order_type,
                    "symbol": symbol,
                    "market_order_config": {
                        "asset_quantity": f"{rounded_quantity:.8f}"  # Start with 8 decimal places
                    }
                }

                path = "/api/v1/crypto/trading/orders/"

                # --- exact profit tracking snapshot (BEFORE placing order) ---
                buying_power_before = self._get_buying_power()

                response = self.make_api_request("POST", path, json.dumps(body))
                if response and "errors" not in response:
                    order_id = response.get("id", None)

                    # Persist the pre-order buying power so restarts can reconcile precisely
                    try:
                        if order_id:
                            self._pnl_ledger.setdefault("pending_orders", {})
                            self._pnl_ledger["pending_orders"][order_id] = {
                                "symbol": symbol,
                                "side": "buy",
                                "buying_power_before": float(buying_power_before),
                                "avg_cost_basis": float(avg_cost_basis) if avg_cost_basis is not None else None,
                                "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                                "tag": tag,
                                "created_ts": time.time(),
                            }
                            self._save_pnl_ledger()
                    except Exception:
                        pass

                    # Wait until the order is actually complete in the system, then use order history executions
                    if order_id:
                        order = self._wait_for_order_terminal(symbol, order_id)
                        state = str(order.get("state", "")).lower().strip() if isinstance(order, dict) else ""
                        if state != "filled":
                            # Not filled -> clear pending and do not record a trade
                            try:
                                self._pnl_ledger.get("pending_orders", {}).pop(order_id, None)
                                self._save_pnl_ledger()
                            except Exception:
                                pass
                            return None

                        filled_qty, avg_fill_price = self._extract_fill_from_order(order)

                        buying_power_after = self._get_buying_power()
                        buying_power_delta = float(buying_power_after) - float(buying_power_before)

                        # Record for GUI history (ACTUAL fill from order history)
                        self._record_trade(
                            side="buy",
                            symbol=symbol,
                            qty=float(filled_qty),
                            price=float(avg_fill_price) if avg_fill_price is not None else None,
                            avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                            pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                            tag=tag,
                            order_id=order_id,
                            buying_power_before=buying_power_before,
                            buying_power_after=buying_power_after,
                            buying_power_delta=buying_power_delta,
                        )

                        # Clear pending now that it is recorded
                        try:
                            self._pnl_ledger.get("pending_orders", {}).pop(order_id, None)
                            self._save_pnl_ledger()
                        except Exception:
                            pass

                    return response  # Successfully placed (and fully filled) order

            except Exception:
                pass #print(traceback.format_exc())

            # Check for precision errors
            if response and "errors" in response:
                for error in response["errors"]:
                    if "has too much precision" in error.get("detail", ""):
                        # Extract required precision directly from the error message
                        detail = error["detail"]
                        nearest_value = detail.split("nearest ")[1].split(" ")[0]

                        decimal_places = len(nearest_value.split(".")[1].rstrip("0"))
                        asset_quantity = round(asset_quantity, decimal_places)
                        break
                    elif "must be greater than or equal to" in error.get("detail", ""):
                        return None

        return None



    def place_sell_order(
        self,
        client_order_id: str,
        side: str,
        order_type: str,
        symbol: str,
        asset_quantity: float,
        expected_price: Optional[float] = None,
        avg_cost_basis: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> Any:
        # If exchange adapter is active, use it to perform the sell and record trade
        try:
            mode = get_exchange_mode()
        except Exception:
            mode = None

        if mode in ("SIMULATOR", "KUCOIN_REAL"):
            buying_power_before = self._get_buying_power()
            base_sym = symbol.split("-")[0] if "-" in symbol else symbol
            try:
                result = exchange_sell(base_sym, asset_quantity)
            except Exception:
                return None

            if result and result.get("success"):
                order_id = result.get("orderId")
                price = result.get("price")
                try:
                    bal = exchange_get_balance() or {}
                    buying_power_after = float(bal.get("usdt", buying_power_before))
                except Exception:
                    buying_power_after = self._get_buying_power()

                # Record sell trade
                try:
                    self._record_trade(
                        side="sell",
                        symbol=symbol,
                        qty=float(asset_quantity),
                        price=float(price) if price is not None else None,
                        avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                        pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                        tag=tag,
                        order_id=order_id,
                        fees_usd=None,
                        buying_power_before=buying_power_before,
                        buying_power_after=buying_power_after,
                        buying_power_delta=(float(buying_power_after) - float(buying_power_before)),
                    )
                except Exception:
                    pass

                return {"id": order_id, "status": "filled"}
            else:
                return None

        body = {
            "client_order_id": client_order_id,
            "side": side,
            "type": order_type,
            "symbol": symbol,
            "market_order_config": {
                "asset_quantity": f"{asset_quantity:.8f}"
            }
        }

        path = "/api/v1/crypto/trading/orders/"

        # --- exact profit tracking snapshot (BEFORE placing order) ---
        buying_power_before = self._get_buying_power()

        response = self.make_api_request("POST", path, json.dumps(body))

        if response and isinstance(response, dict) and "errors" not in response:
            order_id = response.get("id", None)

            # Persist the pre-order buying power so restarts can reconcile precisely
            try:
                if order_id:
                    self._pnl_ledger.setdefault("pending_orders", {})
                    self._pnl_ledger["pending_orders"][order_id] = {
                        "symbol": symbol,
                        "side": "sell",
                        "buying_power_before": float(buying_power_before),
                        "avg_cost_basis": float(avg_cost_basis) if avg_cost_basis is not None else None,
                        "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                        "tag": tag,
                        "created_ts": time.time(),
                    }
                    self._save_pnl_ledger()
            except Exception:
                pass

            # Best-effort: pull actual avg fill price + fees from order executions
            actual_price = float(expected_price) if expected_price is not None else None
            actual_qty = float(asset_quantity)
            fees_usd = None

            def _fee_to_float(v: Any) -> float:
                try:
                    if v is None:
                        return 0.0
                    if isinstance(v, (int, float)):
                        return float(v)
                    if isinstance(v, str):
                        return float(v)
                    if isinstance(v, dict):
                        # common shapes: {"amount": "0.12"}, {"value": 0.12}, etc.
                        for k in ("amount", "value", "usd_amount", "fee", "quantity"):
                            if k in v:
                                try:
                                    return float(v[k])
                                except Exception:
                                    continue
                    return 0.0
                except Exception:
                    return 0.0

            try:
                if order_id:
                    match = self._wait_for_order_terminal(symbol, order_id)
                    if not match:
                        return response

                    if str(match.get("state", "")).lower() != "filled":
                        # Not filled -> clear pending and do not record a trade
                        try:
                            self._pnl_ledger.get("pending_orders", {}).pop(order_id, None)
                            self._save_pnl_ledger()
                        except Exception:
                            pass
                        return response

                    execs = match.get("executions", []) or []
                    total_qty = 0.0
                    total_notional = 0.0
                    fee_total = 0.0

                    for ex in execs:
                        try:
                            q = float(ex.get("quantity", 0.0) or 0.0)
                            p = float(ex.get("effective_price", 0.0) or 0.0)
                            total_qty += q
                            total_notional += (q * p)

                            # Fees can show up under different keys; handle the common ones.
                            for fk in ("fee", "fees", "fee_amount", "fee_usd", "fee_in_usd"):
                                if fk in ex:
                                    fee_total += _fee_to_float(ex.get(fk))
                        except Exception:
                            continue

                    # Some payloads include order-level fee fields too
                    for fk in ("fee", "fees", "fee_amount", "fee_usd", "fee_in_usd"):
                        if fk in match:
                            fee_total += _fee_to_float(match.get(fk))

                    if total_qty > 0.0 and total_notional > 0.0:
                        actual_qty = total_qty
                        actual_price = total_notional / total_qty

                    fees_usd = float(fee_total) if fee_total else 0.0

            except Exception:
                pass #print(traceback.format_exc())

            # If we managed to get a better fill price, update the displayed PnL% too
            if avg_cost_basis is not None and actual_price is not None:
                try:
                    acb = float(avg_cost_basis)
                    if acb > 0:
                        pnl_pct = ((float(actual_price) - acb) / acb) * 100.0
                except Exception:
                    pass

            # --- exact profit tracking snapshot (AFTER the order is complete) ---
            buying_power_after = self._get_buying_power()
            buying_power_delta = float(buying_power_after) - float(buying_power_before)

            self._record_trade(
                side="sell",
                symbol=symbol,
                qty=float(actual_qty),
                price=float(actual_price) if actual_price is not None else None,
                avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                tag=tag,
                order_id=order_id,
                fees_usd=float(fees_usd) if fees_usd is not None else None,
                buying_power_before=buying_power_before,
                buying_power_after=buying_power_after,
                buying_power_delta=buying_power_delta,
            )

            # Clear pending now that it is recorded
            try:
                if order_id:
                    self._pnl_ledger.get("pending_orders", {}).pop(order_id, None)
                    self._save_pnl_ledger()
            except Exception:
                pass

        return response






    def manage_trades(self):
        trades_made = False  # Flag to track if any trade was made in this iteration

        # Hot-reload coins list + paths + trade params from GUI settings while running
        try:
            _refresh_paths_and_symbols()
            self.path_map = dict(base_paths)
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
        except Exception:
            pass




        # Fetch account details
        account = self.get_account()
        # Fetch holdings
        holdings = self.get_holdings()
        # Fetch trading pairs
        trading_pairs = self.get_trading_pairs()

        # Use the stored cost_basis instead of recalculating
        cost_basis = self.cost_basis
        # Fetch current prices
        symbols = [holding["asset_code"] + "-USD" for holding in holdings.get("results", [])]

        # ALSO fetch prices for tracked coins even if not currently held (so GUI can show bid/ask lines)
        for s in crypto_symbols:
            full = f"{s}-USD"
            if full not in symbols:
                symbols.append(full)

        current_buy_prices, current_sell_prices, valid_symbols = self.get_price(symbols)

        # Calculate total account value (robust: never drop a held coin to $0 on transient API misses)
        snapshot_ok = True

        # buying power
        try:
            buying_power = float(account.get("buying_power", 0))
        except Exception:
            buying_power = 0.0
            snapshot_ok = False

        # holdings list (treat missing/invalid holdings payload as transient error)
        try:
            holdings_list = holdings.get("results", None) if isinstance(holdings, dict) else None
            if not isinstance(holdings_list, list):
                holdings_list = []
                snapshot_ok = False
        except Exception:
            holdings_list = []
            snapshot_ok = False

        holdings_buy_value = 0.0
        holdings_sell_value = 0.0

        for holding in holdings_list:
            try:
                asset = holding.get("asset_code")
                if asset == "USDC":
                    continue

                qty = float(holding.get("total_quantity", 0.0))
                if qty <= 0.0:
                    continue

                sym = f"{asset}-USD"
                bp = float(current_buy_prices.get(sym, 0.0) or 0.0)
                sp = float(current_sell_prices.get(sym, 0.0) or 0.0)

                # If any held asset is missing a usable price this tick, do NOT allow a new "low" snapshot
                if bp <= 0.0 or sp <= 0.0:
                    snapshot_ok = False
                    continue

                holdings_buy_value += qty * bp
                holdings_sell_value += qty * sp
            except Exception:
                snapshot_ok = False
                continue

        total_account_value = buying_power + holdings_sell_value
        in_use = (holdings_sell_value / total_account_value) * 100 if total_account_value > 0 else 0.0

        # If this tick is incomplete, fall back to last known-good snapshot so the GUI chart never gets a bogus dip.
        if (not snapshot_ok) or (total_account_value <= 0.0):
            last = getattr(self, "_last_good_account_snapshot", None) or {}
            if last.get("total_account_value") is not None:
                total_account_value = float(last["total_account_value"])
                buying_power = float(last.get("buying_power", buying_power or 0.0))
                holdings_sell_value = float(last.get("holdings_sell_value", holdings_sell_value or 0.0))
                holdings_buy_value = float(last.get("holdings_buy_value", holdings_buy_value or 0.0))
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

        # Avoid shelling out to `clear` in the container; it spams TERM warnings
        # when the process is not attached to an interactive terminal.
        try:
            if os.name == 'nt':
                os.system('cls')
            elif os.environ.get('TERM'):
                os.system('clear')
        except Exception:
            pass
        print("\n--- Account Summary ---")
        print(f"Total Account Value: ${total_account_value:.2f}")
        print(f"Holdings Value: ${holdings_sell_value:.2f}")
        print(f"Percent In Trade: {in_use:.2f}%")
        print(
            f"Trailing PM: start +{self.pm_start_pct_no_dca:.2f}% (no DCA) / +{self.pm_start_pct_with_dca:.2f}% (with DCA) "
            f"| gap {self.trailing_gap_pct:.2f}%"
        )
        print("\n--- Current Trades ---")

        positions = {}
        for holding in holdings.get("results", []):
            symbol = holding["asset_code"]
            full_symbol = f"{symbol}-USD"

            if full_symbol not in valid_symbols or symbol == "USDC":
                continue

            quantity = float(holding["total_quantity"])
            current_buy_price = current_buy_prices.get(full_symbol, 0)
            current_sell_price = current_sell_prices.get(full_symbol, 0)
            avg_cost_basis = cost_basis.get(symbol, 0)

            if avg_cost_basis > 0:
                gain_loss_percentage_buy = ((current_buy_price - avg_cost_basis) / avg_cost_basis) * 100
                gain_loss_percentage_sell = ((current_sell_price - avg_cost_basis) / avg_cost_basis) * 100
            else:
                gain_loss_percentage_buy = 0
                gain_loss_percentage_sell = 0
                print(f"  Warning: Average Cost Basis is 0 for {symbol}, Gain/Loss calculation skipped.")

            value = quantity * current_sell_price
            triggered_levels_count = len(self.dca_levels_triggered.get(symbol, []))
            triggered_levels = triggered_levels_count  # Number of DCA levels triggered

            # Determine the next DCA trigger for this coin (hardcoded % and optional neural level)
            next_stage = triggered_levels_count  # stage 0 == first DCA after entry (trade starts at neural level 3)

            # Hardcoded % for this stage (repeat -50% after we reach it)
            hard_next = self.dca_levels[next_stage] if next_stage < len(self.dca_levels) else self.dca_levels[-1]

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
                    neural_levels = self._read_long_price_levels(symbol)  # highest->lowest == N1..N7

                    neural_line_price = 0.0
                    if len(neural_levels) >= neural_level_needed_disp:
                        neural_line_price = float(neural_levels[neural_level_needed_disp - 1])

                    # Whichever is higher will be hit first as price drops
                    if neural_line_price > dca_line_price:
                        dca_line_price = neural_line_price
                        dca_line_source = f"NEURAL N{neural_level_needed_disp}"


                # PnL% shown alongside DCA is the normal buy-side PnL%
                # (same calculation as GUI "Buy Price PnL": current buy/ask vs avg cost basis)
                dca_line_pct = gain_loss_percentage_buy




            dca_line_price_disp = self._fmt_price(dca_line_price) if avg_cost_basis > 0 else "N/A"

            # Set color code:
            # - DCA is green if we're above the chosen DCA line, red if we're below it
            # - SELL stays based on profit vs cost basis (your original behavior)
            if dca_line_pct >= 0:
                color = Fore.GREEN
            else:
                color = Fore.RED

            if gain_loss_percentage_sell >= 0:
                color2 = Fore.GREEN
            else:
                color2 = Fore.RED

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
                pm_start_pct_disp = self.pm_start_pct_no_dca if int(triggered_levels) == 0 else self.pm_start_pct_with_dca
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
                    dist_to_trail_pct = ((current_sell_price - trail_line_disp) / trail_line_disp) * 100.0
            file = open(symbol+'_current_price.txt', 'w+')
            file.write(str(current_buy_price))
            file.close()
            positions[symbol] = {
                "quantity": quantity,
                "avg_cost_basis": avg_cost_basis,
                "current_buy_price": current_buy_price,
                "current_sell_price": current_sell_price,
                "gain_loss_pct_buy": gain_loss_percentage_buy,
                "gain_loss_pct_sell": gain_loss_percentage_sell,
                "value_usd": value,
                "dca_triggered_stages": int(triggered_levels_count),
                "next_dca_display": next_dca_display,
                "dca_line_price": float(dca_line_price) if dca_line_price else 0.0,
                "dca_line_source": dca_line_source,
                "dca_line_pct": float(dca_line_pct) if dca_line_pct else 0.0,
                "trail_active": True if (trail_status == "ON") else False,
                "trail_line": float(trail_line_disp) if trail_line_disp else 0.0,
                "trail_peak": float(trail_peak_disp) if trail_peak_disp else 0.0,
                "dist_to_trail_pct": float(dist_to_trail_pct) if dist_to_trail_pct else 0.0,
            }


            print(
                f"\nSymbol: {symbol}"
                f"  |  DCA: {color}{dca_line_pct:+.2f}%{Style.RESET_ALL} @ {self._fmt_price(current_buy_price)} (Line: {dca_line_price_disp} {dca_line_source} | Next: {next_dca_display})"
                f"  |  Gain/Loss SELL: {color2}{gain_loss_percentage_sell:.2f}%{Style.RESET_ALL} @ {self._fmt_price(current_sell_price)}"
                f"  |  DCA Levels Triggered: {triggered_levels}"
                f"  |  Trade Value: ${value:.2f}"
            )




            if avg_cost_basis > 0:
                print(
                    f"  Trailing Profit Margin"
                    f"  |  Line: {self._fmt_price(trail_line_disp)}"
                    f"  |  Above: {above_disp}"
                )
            else:
                print("  PM/Trail: N/A (avg_cost_basis is 0)")



            # --- Trailing profit margin (0.5% trail gap) ---
            # PM "start line" is the normal 5% / 2.5% line (depending on DCA levels hit).
            # Trailing activates once price is ABOVE the PM start line, then line follows peaks up
            # by 0.5%. Forced sell happens ONLY when price goes from ABOVE the trailing line to BELOW it.
            if avg_cost_basis > 0:
                pm_start_pct = self.pm_start_pct_no_dca if int(triggered_levels) == 0 else self.pm_start_pct_with_dca
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
                        print(
                            f"  Trailing PM hit for {symbol}. "
                            f"Sell price {current_sell_price:.8f} fell below trailing line {state['line']:.8f}."
                        )
                        response = self.place_sell_order(
                            str(uuid.uuid4()),
                            "sell",
                            "market",
                            full_symbol,
                            quantity,
                            expected_price=current_sell_price,
                            avg_cost_basis=avg_cost_basis,
                            pnl_pct=gain_loss_percentage_sell,
                            tag="TRAIL_SELL",
                        )

                        if response and isinstance(response, dict) and "errors" not in response:
                            trades_made = True
                            self.trailing_pm.pop(symbol, None)  # clear per-coin trailing state on exit

                            # Trade ended -> reset rolling 24h DCA window for this coin
                            self._reset_dca_window_for_trade(symbol, sold=True)

                            print(f"  Successfully sold {quantity} {symbol}.")
                            time.sleep(5)
                            holdings = self.get_holdings()
                            continue


                # Save this tick’s position relative to the line (needed for “above -> below” detection)
                state["was_above"] = above_now



            # DCA (NEURAL or hardcoded %, whichever hits first for the current stage)
            # Trade starts at neural level 3 => trader is at stage 0.
            # Neural-driven DCA stages (max 4):
            #   stage 0 => neural 4 OR -2.5%
            #   stage 1 => neural 5 OR -5.0%
            #   stage 2 => neural 6 OR -10.0%
            #   stage 3 => neural 7 OR -20.0%
            # After that: hardcoded only (-30, -40, -50, then repeat -50 forever).
            current_stage = len(self.dca_levels_triggered.get(symbol, []))

            # Hardcoded loss % for this stage (repeat last level after list ends)
            hard_level = self.dca_levels[current_stage] if current_stage < len(self.dca_levels) else self.dca_levels[-1]
            hard_hit = gain_loss_percentage_buy <= hard_level

            # Neural trigger only for first 4 DCA stages
            neural_level_needed = None
            neural_level_now = None
            neural_hit = False
            if current_stage < 4:
                neural_level_needed = current_stage + 4
                neural_level_now = self._read_long_dca_signal(symbol)

                # Keep it sane: don't DCA from neural if we're not even below cost basis.
                neural_hit = (gain_loss_percentage_buy < 0) and (neural_level_now >= neural_level_needed)

            if hard_hit or neural_hit:
                if neural_hit and hard_hit:
                    reason = f"NEURAL L{neural_level_now}>=L{neural_level_needed} OR HARD {hard_level:.2f}%"
                elif neural_hit:
                    reason = f"NEURAL L{neural_level_now}>=L{neural_level_needed}"
                else:
                    reason = f"HARD {hard_level:.2f}%"

                print(f"  DCAing {symbol} (stage {current_stage + 1}) via {reason}.")

                print(f"  Current Value: ${value:.2f}")
                dca_amount = value * float(DCA_MULTIPLIER or 0.0)
                print(f"  DCA Amount: ${dca_amount:.2f}")
                print(f"  Buying Power: ${buying_power:.2f}")


                recent_dca = self._dca_window_count(symbol)
                if recent_dca >= int(getattr(self, "max_dca_buys_per_24h", 2)):
                    print(
                        f"  Skipping DCA for {symbol}. "
                        f"Already placed {recent_dca} DCA buys in the last 24h (max {self.max_dca_buys_per_24h})."
                    )

                elif dca_amount <= buying_power:
                    response = self.place_buy_order(
                        str(uuid.uuid4()),
                        "buy",
                        "market",
                        full_symbol,
                        dca_amount,
                        avg_cost_basis=avg_cost_basis,
                        pnl_pct=gain_loss_percentage_buy,
                        tag="DCA",
                    )

                    print(f"  Buy Response: {response}")
                    if response and "errors" not in response:
                        # record that we completed THIS stage (no matter what triggered it)
                        self.dca_levels_triggered.setdefault(symbol, []).append(current_stage)

                        # Only record a DCA buy timestamp on success (so skips never advance anything)
                        self._note_dca_buy(symbol)

                        # DCA changes avg_cost_basis, so the PM line must be rebuilt from the new basis
                        # (this will re-init to 5% if DCA=0, or 2.5% if DCA>=1)
                        self.trailing_pm.pop(symbol, None)

                        trades_made = True
                        print(f"  Successfully placed DCA buy order for {symbol}.")
                    else:
                        print(f"  Failed to place DCA buy order for {symbol}.")

                else:
                    print(f"  Skipping DCA for {symbol}. Not enough funds.")

            else:
                pass


        # --- ensure GUI gets bid/ask lines even for coins not currently held ---
        try:
            for sym in crypto_symbols:
                if sym in positions:
                    continue

                full_symbol = f"{sym}-USD"
                if full_symbol not in valid_symbols or sym == "USDC":
                    continue

                current_buy_price = current_buy_prices.get(full_symbol, 0.0)
                current_sell_price = current_sell_prices.get(full_symbol, 0.0)

                # keep the per-coin current price file behavior for consistency
                try:
                    file = open(sym + '_current_price.txt', 'w+')
                    file.write(str(current_buy_price))
                    file.close()
                except Exception:
                    pass

                positions[sym] = {
                    "quantity": 0.0,
                    "avg_cost_basis": 0.0,
                    "current_buy_price": current_buy_price,
                    "current_sell_price": current_sell_price,
                    "gain_loss_pct_buy": 0.0,
                    "gain_loss_pct_sell": 0.0,
                    "value_usd": 0.0,
                    "dca_triggered_stages": int(len(self.dca_levels_triggered.get(sym, []))),
                    "next_dca_display": "",
                    "dca_line_price": 0.0,
                    "dca_line_source": "N/A",
                    "dca_line_pct": 0.0,
                    "trail_active": False,
                    "trail_line": 0.0,
                    "trail_peak": 0.0,
                    "dist_to_trail_pct": 0.0,
                }
        except Exception:
            pass

        if not trading_pairs:
            return



        alloc_pct = float(START_ALLOC_PCT or 0.005)
        allocation_in_usd = total_account_value * (alloc_pct / 100.0)
        if allocation_in_usd < 0.5:
            allocation_in_usd = 0.5


        holding_full_symbols = [f"{h['asset_code']}-USD" for h in holdings.get("results", [])]

        start_index = 0
        while start_index < len(crypto_symbols):
            base_symbol = crypto_symbols[start_index].upper().strip()
            full_symbol = f"{base_symbol}-USD"

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





            response = self.place_buy_order(
                str(uuid.uuid4()),
                "buy",
                "market",
                full_symbol,
                allocation_in_usd,
            )

            if response and "errors" not in response:
                trades_made = True
                # Do NOT pre-trigger any DCA levels. Hardcoded DCA will mark levels only when it hits your loss thresholds.
                self.dca_levels_triggered[base_symbol] = []

                # Fresh trade -> clear any rolling 24h DCA window for this coin
                self._reset_dca_window_for_trade(base_symbol, sold=False)

                # Reset trailing PM state for this coin (fresh trade, fresh trailing logic)
                self.trailing_pm.pop(base_symbol, None)


                print(
                    f"Starting new trade for {full_symbol} (AI start signal long={buy_count}, short={sell_count}). "
                    f"Allocating ${allocation_in_usd:.2f}."
                )
                time.sleep(5)
                holdings = self.get_holdings()
                holding_full_symbols = [f"{h['asset_code']}-USD" for h in holdings.get("results", [])]


            start_index += 1

        # If any trades were made, recalculate the cost basis
        if trades_made:
            time.sleep(5)
            print("Trades were made in this iteration. Recalculating cost basis...")
            new_cost_basis = self.calculate_cost_basis()
            if new_cost_basis:
                self.cost_basis = new_cost_basis
                print("Cost basis recalculated successfully.")
            else:
                print("Failed to recalculcate cost basis.")
            self.initialize_dca_levels()

        # --- GUI HUB STATUS WRITE ---
        try:
            status = {
                "timestamp": time.time(),
                "account": {
                    "total_account_value": total_account_value,
                    "buying_power": buying_power,
                    "holdings_sell_value": holdings_sell_value,
                    "holdings_buy_value": holdings_buy_value,
                    "percent_in_trade": in_use,
                    # trailing PM config (matches what's printed above current trades)
                    "pm_start_pct_no_dca": float(getattr(self, "pm_start_pct_no_dca", 0.0)),
                    "pm_start_pct_with_dca": float(getattr(self, "pm_start_pct_with_dca", 0.0)),
                    "trailing_gap_pct": float(getattr(self, "trailing_gap_pct", 0.0)),
                },
                "positions": positions,
            }
            self._append_jsonl(
                ACCOUNT_VALUE_HISTORY_PATH,
                {"ts": status["timestamp"], "total_account_value": total_account_value},
            )
            self._write_trader_status(status)
        except Exception:
            pass




    def run(self):
        while True:
            try:
                self.manage_trades()
                time.sleep(0.5)
            except Exception as e:
                print(traceback.format_exc())

if __name__ == "__main__":
    trading_bot = CryptoAPITrading()
    trading_bot.run()
