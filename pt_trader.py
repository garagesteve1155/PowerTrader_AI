import base64
import datetime
import json
import uuid
import time
import math
import shutil
import hmac
import hashlib
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional
import requests
import os
import colorama
from colorama import Fore, Style
import traceback



# -----------------------------
# GUI HUB OUTPUTS
# -----------------------------
HUB_DATA_DIR = os.environ.get("POWERTRADER_HUB_DIR", os.path.join(os.path.dirname(__file__), "hub_data"))
os.makedirs(HUB_DATA_DIR, exist_ok=True)

TRADER_STATUS_PATH = os.path.join(HUB_DATA_DIR, "trader_status.json")
TRADE_HISTORY_PATH = os.path.join(HUB_DATA_DIR, "trade_history.jsonl")
PNL_LEDGER_PATH = os.path.join(HUB_DATA_DIR, "pnl_ledger.json")
ACCOUNT_VALUE_HISTORY_PATH = os.path.join(HUB_DATA_DIR, "account_value_history.jsonl")
BOT_ORDER_IDS_PATH = os.path.join(HUB_DATA_DIR, "bot_order_ids.json")
LTH_EMA200_PATH = os.path.join(HUB_DATA_DIR, "lth_daily_ema200.json")



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
	"coins": ['BTC', 'ETH', 'BNB', 'PAXG', 'SOL', 'XRP', 'DOGE'],  # fallback defaults
	"main_neural_dir": None,
	"trade_start_level": 4,
	"start_allocation_pct": 0.5,
	"dca_multiplier": 2.0,
	"dca_levels": [-5.0, -10.0, -20.0, -30.0, -40.0, -50.0, -50.0],
	"max_dca_buys_per_24h": 1,

	# Long-term holdings symbols (optional UI grouping only).
	# IMPORTANT: no amounts are stored. The bot auto-ignores any extra holdings.
	# Format: ["BTC","ETH"]
	"long_term_holdings": ['BTC', 'ETH', 'BNB', 'PAXG', 'SOL', 'XRP', 'DOGE'],

	# % of realized trade profits to auto-buy into long-term holdings
	"lth_profit_alloc_pct": 50.0,


	# Trailing PM settings
	"pm_start_pct_no_dca": 3.0,
	"pm_start_pct_with_dca": 3.0,
	"trailing_gap_pct": 0.1,
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

		# --- LTH auto-buy from profits (% of realized profit) ---
		lth_profit_alloc_pct = data.get("lth_profit_alloc_pct", _gui_settings_cache.get("lth_profit_alloc_pct", 0.0))
		try:
			lth_profit_alloc_pct = float(str(lth_profit_alloc_pct).replace("%", "").strip())
		except Exception:
			lth_profit_alloc_pct = float(_gui_settings_cache.get("lth_profit_alloc_pct", 0.0))
		if lth_profit_alloc_pct < 0.0:
			lth_profit_alloc_pct = 0.0
		if lth_profit_alloc_pct > 100.0:
			lth_profit_alloc_pct = 100.0

		# --- Long-term (ignored) holdings symbols (NO amounts) ---
		long_term_holdings = data.get("long_term_holdings", _gui_settings_cache.get("long_term_holdings", []))

		# Accept:
		# - list/tuple/set: ["BTC","ETH"]
		# - str: "BTC,ETH" (or newline separated)
		# - dict legacy: {"BTC": 0.01, "ETH": 1.0}  -> keep keys only
		raw_syms = []
		if isinstance(long_term_holdings, dict):
			raw_syms = list(long_term_holdings.keys())
		elif isinstance(long_term_holdings, str):
			raw_syms = [x.strip() for x in long_term_holdings.replace("\n", ",").split(",")]
		elif isinstance(long_term_holdings, (list, tuple, set)):
			raw_syms = list(long_term_holdings)
		else:
			raw_syms = []

		parsed_lth_syms = []
		seen = set()
		for v in raw_syms:
			s = str(v).strip()
			if not s:
				continue

			# If someone still hand-edits "BTC:0.001" or "BTC=0.001", keep symbol only.
			if ":" in s:
				s = s.split(":", 1)[0].strip()
			elif "=" in s:
				s = s.split("=", 1)[0].strip()

			sym = s.upper().strip()
			if not sym or sym in seen:
				continue
			seen.add(sym)
			parsed_lth_syms.append(sym)



		_gui_settings_cache["mtime"] = mtime
		_gui_settings_cache["coins"] = coins
		_gui_settings_cache["main_neural_dir"] = main_neural_dir
		_gui_settings_cache["trade_start_level"] = trade_start_level
		_gui_settings_cache["start_allocation_pct"] = start_allocation_pct
		_gui_settings_cache["dca_multiplier"] = dca_multiplier
		_gui_settings_cache["dca_levels"] = dca_levels
		_gui_settings_cache["max_dca_buys_per_24h"] = max_dca_buys_per_24h
		_gui_settings_cache["long_term_holdings"] = list(parsed_lth_syms)

		_gui_settings_cache["pm_start_pct_no_dca"] = pm_start_pct_no_dca
		_gui_settings_cache["pm_start_pct_with_dca"] = pm_start_pct_with_dca
		_gui_settings_cache["trailing_gap_pct"] = trailing_gap_pct
		_gui_settings_cache["lth_profit_alloc_pct"] = lth_profit_alloc_pct


		return {
			"mtime": mtime,
			"coins": list(coins),
			"main_neural_dir": main_neural_dir,
			"trade_start_level": trade_start_level,
			"start_allocation_pct": start_allocation_pct,
			"dca_multiplier": dca_multiplier,
			"dca_levels": list(dca_levels),
			"max_dca_buys_per_24h": max_dca_buys_per_24h,
			"long_term_holdings": list(parsed_lth_syms),

			"pm_start_pct_no_dca": pm_start_pct_no_dca,
			"pm_start_pct_with_dca": pm_start_pct_with_dca,
			"trailing_gap_pct": trailing_gap_pct,
			"lth_profit_alloc_pct": lth_profit_alloc_pct,
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

# % of realized trade profits to auto-buy into long-term holdings
LTH_PROFIT_ALLOC_PCT = 0.0

# Long-term (ignored) holdings symbols (optional UI grouping), updated by _refresh_paths_and_symbols()

LONG_TERM_SYMBOLS: set[str] = set()



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
	global LTH_PROFIT_ALLOC_PCT, LONG_TERM_SYMBOLS
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

	# LTH profit allocation %
	try:
		LTH_PROFIT_ALLOC_PCT = float(str(s.get("lth_profit_alloc_pct", LTH_PROFIT_ALLOC_PCT) or LTH_PROFIT_ALLOC_PCT).replace("%", "").strip())
	except Exception:
		LTH_PROFIT_ALLOC_PCT = float(LTH_PROFIT_ALLOC_PCT)
	if LTH_PROFIT_ALLOC_PCT < 0.0:
		LTH_PROFIT_ALLOC_PCT = 0.0
	if LTH_PROFIT_ALLOC_PCT > 100.0:
		LTH_PROFIT_ALLOC_PCT = 100.0

	# Long-term holdings symbols (comma list in settings)

	lth = s.get("long_term_holdings", []) or []
	if isinstance(lth, str):
		lth = [x.strip() for x in lth.replace("\n", ",").split(",")]
	if not isinstance(lth, (list, tuple)):
		lth = []
	cleaned_syms: set[str] = set()
	for v in lth:
		sym = str(v).upper().strip()
		if sym:
			cleaned_syms.add(sym)
	LONG_TERM_SYMBOLS.clear()
	LONG_TERM_SYMBOLS.update(cleaned_syms)




	# Keep it safe if folder isn't real on this machine
	if not os.path.isdir(mndir):
		mndir = os.getcwd()

	crypto_symbols = list(coins)
	main_dir = mndir
	base_paths = _build_base_paths(main_dir, crypto_symbols)






#API STUFF
API_KEY = ""
API_SECRET = ""

try:
    with open('cb_key.txt', 'r', encoding='utf-8') as f:
        API_KEY = (f.read() or "").strip()
    with open('cb_secret.txt', 'r', encoding='utf-8') as f:
        API_SECRET = (f.read() or "").strip()
except Exception:
    API_KEY = ""
    API_SECRET = ""

if not API_KEY or not API_SECRET:
    print(
        "\n[PowerTrader] Coinbase API credentials not found.\n"
        "Open the GUI and go to Settings → Coinbase API → Setup / Update.\n"
        "That wizard will help you create your API key, and will save cb_key.txt + cb_secret.txt\n"
        "so this trader can authenticate.\n"
    )
    raise SystemExit(1)

class CryptoAPITrading:
    def __init__(self):
        # keep a copy of the folder map (same idea as trader.py)
        self.path_map = dict(base_paths)

        self.api_key = API_KEY
        self.api_secret = API_SECRET
        self.base_url = "https://api.coinbase.com"

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
            self._bot_order_ids_mtime = os.path.getmtime(BOT_ORDER_IDS_PATH) if os.path.isfile(BOT_ORDER_IDS_PATH) else None
        except Exception:
            self._bot_order_ids_mtime = None

        # GUI hub persistence
        self._pnl_ledger = self._load_pnl_ledger()
        self._reconcile_pending_orders()

        self.cost_basis = self.calculate_cost_basis()  # Initialize cost basis at startup
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

        self._dca_buy_ts = {}         # { "BTC": [ts, ts, ...] } (DCA buys only)
        self._dca_last_sell_ts = {}   # { "BTC": ts_of_last_sell }
        self._seed_dca_window_from_history()








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

        except Exception:
            # If anything goes wrong, do NOT delete the old file.
            pass

    def _append_jsonl(self, path: str, obj: dict) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj) + "\n")
        except Exception:
            pass

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
        except Exception:
            pass
        return out

    def _save_bot_order_ids(self) -> None:
        try:
            data: Dict[str, list] = {}
            for sym, ids in (self._bot_order_ids or {}).items():
                if not sym or not ids:
                    continue
                data[str(sym).upper().strip()] = sorted({str(x).strip() for x in ids if str(x).strip()})
            self._atomic_write_json(BOT_ORDER_IDS_PATH, data)
        except Exception:
            pass

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
                    base = sym_full.split("-")[0].strip() if sym_full else ""
                    if not base:
                        continue

                    # Keep row so we can do a second pass without rereading the file
                    rows.append(obj)

                    if side != "sell":
                        continue
                    try:
                        ts_f = float(obj.get("ts", 0.0) or 0.0)
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
                base = sym_full.split("-")[0].strip() if sym_full else ""
                if not base:
                    continue

                try:
                    ts_f = float(obj.get("ts", 0.0) or 0.0)
                except Exception:
                    ts_f = 0.0

                if ts_f > float(last_sell_ts.get(base, 0.0) or 0.0):
                    out.setdefault(base, set()).add(oid)

        except Exception:
            pass
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
        except Exception:
            pass

    def _clear_bot_order_ids_for_coin(self, base_symbol: str) -> None:
        """Clear the tracked order_ids for the current open trade on this coin (called after a filled bot SELL)."""
        try:
            base = str(base_symbol).upper().strip()
            if not base:
                return
            if isinstance(self._bot_order_ids, dict):
                self._bot_order_ids.pop(base, None)
            self._save_bot_order_ids()
        except Exception:
            pass


    def _is_bot_order_id(self, base_symbol: str, order_id: Optional[str]) -> bool:
        base = str(base_symbol).upper().strip()
        oid = str(order_id or "").strip()
        if not base or not oid:
            return False
        if oid in (self._bot_order_ids.get(base, set()) if isinstance(self._bot_order_ids, dict) else set()):
            return True
        if oid in (self._bot_order_ids_from_history.get(base, set()) if isinstance(self._bot_order_ids_from_history, dict) else set()):
            return True
        return False
    def _maybe_reload_bot_order_ids(self) -> bool:
        """
        If the GUI updates bot_order_ids.json while the trader is running,
        reload it and refresh any cached derived state (cost_basis + DCA history).
        Returns True if a reload occurred.
        """
        try:
            mtime = os.path.getmtime(BOT_ORDER_IDS_PATH) if os.path.isfile(BOT_ORDER_IDS_PATH) else None
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
            self._bot_order_ids_from_history = self._load_bot_order_ids_from_trade_history()
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
            d.setdefault("last_updated_ts", time.time())
            d.setdefault("open_positions", {})   # { "BTC": {"usd_cost": float, "qty": float} }
            d.setdefault("pending_orders", {})   # { "<order_id>": {...} }
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
        return _upgrade({
            "total_realized_profit_usd": 0.0,
            "last_updated_ts": time.time(),
            "open_positions": {},
            "pending_orders": {},
            "lth_profit_bucket_usd": 0.0,
            "lth_last_buy": None,
        })


    def _save_pnl_ledger(self) -> None:
        try:
            self._pnl_ledger["last_updated_ts"] = time.time()
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
        except Exception:
            pass
        return out

    def _pick_lth_symbol_to_buy(self) -> Optional[str]:
        """
        Pick the coin from LONG_TERM_SYMBOLS that is furthest % below daily 200 EMA.
        If none are below (pct>=0), pick the one closest to EMA (smallest positive pct).
        Returns base symbol like "BTC" or None.
        """
        try:
            syms = sorted({str(x).upper().strip() for x in (LONG_TERM_SYMBOLS or set()) if str(x).strip()})
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
        except Exception:
            return None

    def _lth_market_buy_for_usd(self, base_symbol: str, usd_amount: float) -> bool:
        """
        Places a market BUY sized by USD.

        IMPORTANT:
        - Uses place_buy_order(...) so we size safely and record ACTUAL fills from executions.
        - Passes tag="LTH" so it does NOT become a bot-owned trade.
        """
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

            full_symbol = f"{sym}-USD"

            # This already:
            # - persists pending_orders metadata
            # - waits for terminal fill
            # - records the trade using executions (accurate qty/price/fees)
            order = self.place_buy_order(
                str(uuid.uuid4()),
                "buy",
                "market",
                full_symbol,
                float(usd_amount),
                tag="LTH",
            )

            return isinstance(order, dict) and bool(str(order.get("id") or "").strip())
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
            bucket = float((self._pnl_ledger or {}).get("lth_profit_bucket_usd", 0.0) or 0.0)
        except Exception:
            bucket = 0.0

        # Apply this trade's allocation (can be negative; can also be 0.0).
        prev_bucket = bucket
        bucket = bucket + float(alloc)

        # Never allow the bucket to go below $0.
        if bucket < 0.0:
            bucket = 0.0

        spend_now = 0.0

        # Spec rule: if a single trade's allocated portion >= $0.50, spend that whole portion (plus any bucket)
        # Note: for losses alloc will be negative, so this branch won't trigger.
        if alloc >= 0.50:
            spend_now = float(alloc) + float(prev_bucket)
            bucket = 0.0
        else:
            if bucket >= 0.50:
                spend_now = float(bucket)
                bucket = 0.0

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
            return

        pct_map = self._read_lth_ema200_snapshot()
        pct_from_ema = pct_map.get(pick, None)

        ok = self._lth_market_buy_for_usd(pick, spend_now)
        if ok:
            self._pnl_ledger["lth_profit_bucket_usd"] = 0.0
            self._pnl_ledger["lth_last_buy"] = {
                "ts": time.time(),
                "symbol": pick,
                "usd": float(spend_now),
                "pct_from_ema200": (float(pct_from_ema) if pct_from_ema is not None else None),
            }
            self._save_pnl_ledger()
        else:
            # failed buy -> restore funds to bucket so they're not lost
            self._pnl_ledger["lth_profit_bucket_usd"] = float(bucket + spend_now)
            self._save_pnl_ledger()


    # -----------------------------
    # Ledger seeding from selected bot order IDs
    # -----------------------------
    def _rebuild_open_position_from_selected_bot_buys(self, base_symbol: str, tradable_qty: float) -> None:
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
                if sym in (self._bot_order_ids or {}) and (self._bot_order_ids.get(sym) or set()):
                    has_any_ids = True
                if sym in (self._bot_order_ids_from_history or {}) and (self._bot_order_ids_from_history.get(sym) or set()):
                    has_any_ids = True
            except Exception:
                has_any_ids = False

            if not has_any_ids:
                return

            # Pull orders and keep ONLY filled bot-owned BUY orders.
            symbol_full = f"{sym}-USD"
            orders = self.get_orders(symbol_full)
            results = orders.get("results", []) if isinstance(orders, dict) else []
            if not isinstance(results, list) or not results:
                return

            buys = []
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

                    qty, avg_px, notional_usd, fees_usd = self._extract_amounts_and_fees_from_order(o)
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
                    cost_used += (c * ratio)

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
            pass


    def _bot_net_qty_from_selected_orders(self, base_symbol: str) -> float:
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
        """
        try:
            sym = str(base_symbol).upper().strip()
            if not sym or sym == "USDC":
                return 0.0

            symbol_full = f"{sym}-USD"
            orders = self.get_orders(symbol_full)
            results = orders.get("results", []) if isinstance(orders, dict) else []
            if not isinstance(results, list) or not results:
                return 0.0

            # Selected BUY order IDs (from GUI) are in self._bot_order_ids[sym]
            selected_ids = set()
            try:
                selected_ids = set(self._bot_order_ids.get(sym, set()) or set())
            except Exception:
                selected_ids = set()

            # Bot-owned history IDs (from trade_history.jsonl) are in self._bot_order_ids_from_history[sym]
            hist_ids = set()
            try:
                hist_ids = set(self._bot_order_ids_from_history.get(sym, set()) or set())
            except Exception:
                hist_ids = set()

            # Pass 1: sum selected BUY fills and find earliest selected BUY created_at
            selected_buy_qty = 0.0
            earliest_selected_buy_created = None

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
                        selected_buy_qty += qty
                        if created:
                            if earliest_selected_buy_created is None or created < earliest_selected_buy_created:
                                earliest_selected_buy_created = created

                    # Sells are NOT selected; infer bot sells from history IDs
                    if side == "sell" and oid in hist_ids:
                        filled_bot_sells.append((created, qty))
                except Exception:
                    continue

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

                    # Never claim more bot inventory than actually exists in holdings.
                    tradable_qty = min(float(total_qty), float(in_trade_qty))
                    if tradable_qty < 0.0:
                        tradable_qty = 0.0

                    self._rebuild_open_position_from_selected_bot_buys(asset, tradable_qty)
                except Exception:
                    continue
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
        """Returns (filled_qty, avg_fill_price). avg_fill_price may be None.
        
        Coinbase provides filled_size and average_filled_price in the order response.
        """
        try:
            # Try Coinbase's format first
            filled_qty = float(order.get("filled_size") or 0.0)
            avg_fill_price = float(order.get("average_filled_price") or 0.0)
            
            if filled_qty > 0.0 and avg_fill_price > 0.0:
                return filled_qty, avg_fill_price
            
            # Fallback to other possible fields
            for qty_key in ("filled_size", "filled_asset_quantity", "filled_quantity", "size"):
                if qty_key in order:
                    try:
                        qty = float(order.get(qty_key) or 0.0)
                        if qty > 0.0:
                            filled_qty = qty
                            break
                    except Exception:
                        continue

            # Try to get average price from various fields
            for price_key in ("average_filled_price", "average_price", "price"):
                if price_key in order:
                    try:
                        price = float(order.get(price_key) or 0.0)
                        if price > 0.0:
                            avg_fill_price = price
                            break
                    except Exception:
                        continue

            return float(filled_qty), (float(avg_fill_price) if avg_fill_price > 0.0 else None)
        except Exception:
            return 0.0, None

    def _extract_amounts_and_fees_from_order(self, order: dict) -> tuple:
        """
        Returns (filled_qty, avg_fill_price, notional_usd, fees_usd).

        Coinbase provides fills/fees via:
          - filled_size and average_filled_price in the order
          - total_fees in the order
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
                    for k in ("amount", "value", "fee"):
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
            # ----- fees (Coinbase provides total_fees) -----
            fee_total = 0.0
            
            # Try Coinbase's total_fees field
            if "total_fees" in order:
                fee_total += _fee_to_float(order.get("total_fees"))
            
            # Try other common fee fields
            for fk in ("fee", "fees"):
                if fk in order:
                    fee_total += _fee_to_float(order.get(fk))

            fees_usd = float(fee_total) if fee_total > 0 else None

            # ----- filled qty + avg price (Coinbase uses filled_size) -----
            avg_p_raw = order.get("average_filled_price", None)
            filled_q_raw = order.get("filled_size", None)

            avg_p_d = _to_decimal(avg_p_raw)
            filled_q_d = _to_decimal(filled_q_raw)

            avg_fill_price = float(avg_p_d) if avg_p_d > 0 else None
            filled_qty = float(filled_q_d) if filled_q_d > 0 else 0.0

            notional_usd = None

            # ----- USD notional (SOURCE OF TRUTH) -----
            if avg_p_d > 0 and filled_q_d > 0:
                notional_usd = float(_usd_cents(avg_p_d * filled_q_d))

            return float(filled_qty), (float(avg_fill_price) if avg_fill_price is not None else None), notional_usd, fees_usd

        except Exception:
            return 0.0, None, None, None




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
        If the hub/trader restarts mid-order, we keep minimal order metadata on disk and
        finish the accounting once the order shows as terminal in Robinhood.

        IMPORTANT:
        We do *not* use before/after buying power deltas here. We compute the trade amounts
        and fees from the order's own executions (API trade history), which is the only
        robust way to keep per-coin cost basis and realized PnL correct.
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

                        filled_qty, avg_price, notional_usd, fees_usd = self._extract_amounts_and_fees_from_order(order)

                        self._record_trade(
                            side=side,
                            symbol=symbol,
                            qty=float(filled_qty),
                            price=float(avg_price) if avg_price is not None else None,
                            notional_usd=float(notional_usd) if notional_usd is not None else None,
                            fees_usd=float(fees_usd) if fees_usd is not None else None,
                            avg_cost_basis=info.get("avg_cost_basis", None),
                            pnl_pct=info.get("pnl_pct", None),
                            tag=info.get("tag", None),
                            order_id=order_id,
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
        ts = time.time()
        side_l = str(side or "").lower().strip()
        base = str(symbol or "").upper().split("-")[0].strip()
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

        fees_missing = (fees_usd is None)
        fee_val_actual = float(fees_usd) if fees_usd is not None else 0.0

        # Per-trade fallback: only apply on SELL realized profit if fees were missing.
        fee_fallback = 0.02 if (fees_missing and side_l == "sell") else 0.0

        # Prefer API-provided notional; fall back to qty*price if needed.
        #
        # IMPORTANT (matches RH app/accounting precisely):
        # Robinhood settles USD debits/credits to the cent. If we keep fractional-cent
        # notional from price*qty math, we will systematically overstate profit.
        #
        # Settlement rounding behavior:
        # - BUY: cost is rounded UP to the nearest cent (more debit)
        # - SELL: proceeds are rounded DOWN to the nearest cent (less credit)
        def _rh_round_usd_to_cents(amount: float, side_lower: str) -> float:
            try:
                d = Decimal(str(amount))
                if side_lower == "buy":
                    return float(d.quantize(Decimal("0.01"), rounding=ROUND_CEILING))
                if side_lower == "sell":
                    return float(d.quantize(Decimal("0.01"), rounding=ROUND_FLOOR))
                return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            except Exception:
                return float(amount)

        notional = None
        if notional_usd is not None:
            try:
                notional = float(notional_usd)
            except Exception:
                notional = None

        if notional is None and price is not None:
            try:
                notional = _rh_round_usd_to_cents(float(price) * float(qty), side_l)
            except Exception:
                notional = None


        # Best-effort net USD (does NOT include fallback fee; fallback is only applied to realized profit).
        net_usd = None
        try:
            if notional is not None:
                if side_l == "buy":
                    net_usd = -(float(notional) + float(fee_val_actual))
                elif side_l == "sell":
                    net_usd = (float(notional) - float(fee_val_actual))
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
                            pos["usd_cost"] = float(pos.get("usd_cost", 0.0) or 0.0) + usd_used
                            pos["qty"] = float(pos.get("qty", 0.0) or 0.0) + max(0.0, float(qty))
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
                        pos["usd_cost"] = pos_cost - cost_used
                        pos["qty"] = pos_qty - q

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
                            realized = float(usd_got) - float(cost_used) - float(fee_fallback)
                            self._pnl_ledger["total_realized_profit_usd"] = float(self._pnl_ledger.get("total_realized_profit_usd", 0.0) or 0.0) + float(realized)

                        # Clean up tiny dust
                        if float(pos.get("qty", 0.0) or 0.0) <= 1e-12 or float(pos.get("usd_cost", 0.0) or 0.0) <= 1e-6:
                            open_pos.pop(base, None)

                        self._save_pnl_ledger()

                        # Keep in-memory cost_basis aligned with the ledger for fallbacks/plots.
                        try:
                            pos2 = (self._pnl_ledger.get("open_positions", {}) or {}).get(base, None)
                            if isinstance(pos2, dict):
                                self.cost_basis[base] = {
                                    "total_cost": float(pos2.get("usd_cost", 0.0) or 0.0),
                                    "total_quantity": float(pos2.get("qty", 0.0) or 0.0),
                                }
                        except Exception:
                            pass

                    except Exception:
                        pass
        except Exception:
            pass

        # --- Fallback realized profit calc (if not found on ledger; should be rare) ---
        if tag_u != "LTH" and realized is None and side_l == "sell" and price is not None and avg_cost_basis is not None:
            try:
                realized = (float(price) - float(avg_cost_basis)) * float(qty) - float(fee_val_actual) - float(fee_fallback)
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
            "notional_usd": float(notional) if notional is not None else None,
            "net_usd": float(net_usd) if net_usd is not None else None,
            "avg_cost_basis": avg_cost_basis,
            "pnl_pct": pnl_pct,

            # fees_usd is None when we couldn't find fees in the API payload
            "fees_usd": (float(fees_usd) if fees_usd is not None else None),
            "fees_missing": bool(fees_missing),
            "fees_fallback_applied_usd": (float(fee_fallback) if fee_fallback > 0.0 else 0.0),

            "realized_profit_usd": realized,
            "order_id": order_id,
            "position_cost_used_usd": float(position_cost_used) if position_cost_used is not None else None,
            "position_cost_after_usd": float(position_cost_after) if position_cost_after is not None else None,
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
                if not isinstance(getattr(self, "_bot_order_ids_from_history", None), dict):
                    self._bot_order_ids_from_history = {}
                self._bot_order_ids_from_history.setdefault(base, set()).add(str(order_id))
        except Exception:
            pass

        # If this was a bot sell, let profit allocation update the bucket (wins and losses)
        try:
            if tag_u != "LTH" and side_l == "sell" and realized is not None:
                self._maybe_process_lth_profit_allocation(float(realized))
        except Exception:
            pass










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
        - Start gate: start trades at the configured TRADE_START_LEVEL+
        - DCA assist: the *next* DCA neural level is derived from TRADE_START_LEVEL at runtime
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
        - Start gate confirmation (typically requires short == 0)
        - Additional context alongside the configured TRADE_START_LEVEL logic
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
            print("No holdings found. Skipping DCA levels initialization.")
            return

        for holding in holdings.get("results", []):
            symbol = str(holding.get("asset_code") or "").upper().strip()
            if not symbol:
                continue

            try:
                pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(symbol)
                bot_qty = float(pos.get("qty", 0.0) or 0.0) if isinstance(pos, dict) else 0.0
            except Exception:
                bot_qty = 0.0

            if bot_qty <= 1e-12:
                self.dca_levels_triggered[symbol] = []
                continue

            selected_ids = set()
            try:
                selected_ids = {str(x).strip() for x in (self._bot_order_ids.get(symbol, set()) or set()) if str(x).strip()}
            except Exception:
                selected_ids = set()

            if not selected_ids:
                self.dca_levels_triggered[symbol] = []
                continue

            full_symbol = f"{symbol}-USD"
            orders = self.get_orders(full_symbol)
            if not orders or "results" not in orders:
                self.dca_levels_triggered[symbol] = []
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

        timestamp = str(int(time.time()))
        headers = self.get_authorization_header(method, path, body, timestamp)
        url = self.base_url + path

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=10)
            elif method == "POST":
                response = requests.post(url, headers=headers, data=body, timeout=10)

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
            self, method: str, path: str, body: str, timestamp: str
    ) -> Dict[str, str]:
        """Generate Coinbase API authorization headers using HMAC SHA256."""
        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        signature_b64 = base64.b64encode(signature).decode('utf-8')

        return {
            "CB-ACCESS-KEY": self.api_key,
            "CB-ACCESS-SIGN": signature_b64,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def get_account(self) -> Any:
        path = "/api/v3/brokerage/accounts"
        return self.make_api_request("GET", path)

    def get_holdings(self) -> Any:
        """Get holdings from Coinbase accounts endpoint."""
        path = "/api/v3/brokerage/accounts"
        return self.make_api_request("GET", path)

    def get_trading_pairs(self) -> Any:
        """Get available trading products from Coinbase."""
        path = "/api/v3/brokerage/products"
        response = self.make_api_request("GET", path)

        if not response or "products" not in response:
            return []

        trading_pairs = response.get("products", [])
        if not trading_pairs:
            return []

        return trading_pairs

    def get_orders(self, symbol: str, max_pages: int = 25) -> Any:
        """Fetch crypto orders for a symbol, following pagination so older bot buys
        (which may be on earlier pages) are included.

        Coinbase's orders endpoint is paginated. This method returns a dict with an
        aggregated "orders" list.
        """
        path = f"/api/v3/brokerage/orders/historical/batch?product_id={symbol}-USD&limit=100"
        first = self.make_api_request("GET", path)

        # If the API didn't return the expected shape, keep legacy behavior.
        if not isinstance(first, dict):
            return first

        orders = list(first.get("orders", []) or [])
        pagination = first.get("pagination", {})
        next_cursor = pagination.get("next_cursor", None) if pagination else None

        # Follow pagination links (best-effort, capped).
        pages = 1
        while next_cursor and pages < int(max_pages):
            try:
                if not next_cursor:
                    break

                path = f"/api/v3/brokerage/orders/historical/batch?product_id={symbol}-USD&limit=100&after={next_cursor}"
                resp = self.make_api_request("GET", path)
                if not isinstance(resp, dict):
                    break

                orders.extend(list(resp.get("orders", []) or []))
                pagination = resp.get("pagination", {})
                next_cursor = pagination.get("next_cursor", None) if pagination else None
                pages += 1
            except Exception:
                break

        out = dict(first)
        out["orders"] = orders
        return out


    def _execution_qty_price(self, execution: dict) -> Optional[tuple]:
        """
        Returns (qty, price) from an execution entry.
        """
        try:
            q = float(execution.get("quantity") or 0.0)
            p = float(execution.get("effective_price") or 0.0)
            if q <= 0.0 or p <= 0.0:
                return None
            return (q, p)
        except Exception:
            return None

    
    def _replay_lots_fifo(self, filled_orders_oldest_first: list) -> list:
        """
        Return remaining lots as [(qty, price), ...] after applying buys then sells FIFO.

        CRITICAL FIX:
        Some Robinhood "filled" orders may not include executions[] (often older orders).
        In that case, fall back to order-level filled quantity + average price via
        self._extract_fill_from_order(order), so cost basis is truly weighted across ALL buys.
        """
        lots = []  # oldest first

        def _buy_lots_from_order(order: dict) -> list:
            # Prefer per-execution detail if present
            execs = order.get("executions", []) or []
            out = []
            for ex in execs:
                qp = self._execution_qty_price(ex)
                if qp:
                    out.append([float(qp[0]), float(qp[1])])

            # Fallback: use order-level filled qty + avg price if no executions were usable
            if not out:
                q, p = self._extract_fill_from_order(order)
                try:
                    q = float(q or 0.0)
                    p = float(p or 0.0) if p is not None else 0.0
                except Exception:
                    q, p = 0.0, 0.0
                if q > 0.0 and p > 0.0:
                    out.append([q, p])

            return out

        def _sell_qty_from_order(order: dict) -> float:
            # Prefer per-execution detail if present
            execs = order.get("executions", []) or []
            total = 0.0
            for ex in execs:
                qp = self._execution_qty_price(ex)
                if qp:
                    total += float(qp[0])

            # Fallback: use order-level filled qty if executions missing
            if total <= 0.0:
                q, _p = self._extract_fill_from_order(order)
                try:
                    q = float(q or 0.0)
                except Exception:
                    q = 0.0
                total = q

            return float(total) if total > 0.0 else 0.0

        for order in filled_orders_oldest_first:
            side = str(order.get("side") or "").lower().strip()

            if side == "buy":
                for q, p in _buy_lots_from_order(order):
                    if float(q) > 0.0 and float(p) > 0.0:
                        lots.append([float(q), float(p)])

            elif side == "sell":
                sell_qty = _sell_qty_from_order(order)
                if sell_qty <= 0.0:
                    continue

                # Consume FIFO lots
                remaining = float(sell_qty)
                while remaining > 0.0 and lots:
                    lot_q, lot_p = lots[0]
                    if float(lot_q) > remaining:
                        lots[0][0] = float(lot_q) - float(remaining)
                        remaining = 0.0
                    else:
                        remaining -= float(lot_q)
                        lots.pop(0)

        # Convert to tuples for safety
        return [(float(q), float(p)) for (q, p) in lots if float(q) > 0.0 and float(p) > 0.0]

    
    def calculate_cost_basis(self):
        """Calculate avg entry cost for the bot's *current* tradable position only.

        IMPORTANT:
        - We intentionally ignore ALL sells here.
          With the new order-selection system, sells (especially manual sells) must NOT
          be allowed to wipe earlier buy lots and collapse the avg to the latest DCA.
        - We compute the weighted average from bot-owned FILLED BUY orders only.
        """
        holdings = self.get_holdings()
        if not holdings or "results" not in holdings:
            return {}

        cost_basis: Dict[str, float] = {}

        for holding in holdings.get("results", []) or []:
            asset_code = str(holding.get("asset_code") or "").upper().strip()
            if not asset_code:
                continue

            try:
                total_qty = float(holding.get("total_quantity") or 0.0)
            except Exception:
                total_qty = 0.0

            # Bot-managed qty is the ledger qty. Any extra exchange holdings are treated as manual/long-term.
            try:
                pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(asset_code)
                tradable_target_qty = float(pos.get("qty", 0.0) or 0.0) if isinstance(pos, dict) else 0.0
            except Exception:
                tradable_target_qty = 0.0

            if tradable_target_qty <= 1e-12:
                cost_basis[asset_code] = 0.0
                continue


            orders = self.get_orders(f"{asset_code}-USD")
            if not orders or "results" not in orders:
                continue

            # Only consider bot-owned FILLED BUY orders so sells can never collapse avg -> last DCA.
            filled_bot_buys = []
            for o in orders.get("results", []) or []:
                try:
                    if o.get("state") != "filled":
                        continue
                    side = str(o.get("side") or "").lower().strip()
                    if side != "buy":
                        continue
                    if not self._is_bot_order_id(asset_code, o.get("id")):
                        continue
                    filled_bot_buys.append(o)
                except Exception:
                    continue

            if not filled_bot_buys:
                continue

            # Oldest-first (stable + deterministic)
            filled_bot_buys.sort(key=lambda x: x.get("created_at") or "")

            # Build buy lots (qty, price) directly (no sell replay)
            lots = []
            for o in filled_bot_buys:
                try:
                    q, p = self._extract_fill_from_order(o)
                    if q > 0.0 and p is not None and float(p) > 0.0:
                        lots.append((float(q), float(p)))
                except Exception:
                    continue

            bot_qty = sum(q for (q, _p) in lots)
            if bot_qty <= 1e-12:
                cost_basis[asset_code] = 0.0
                continue

            # Never claim more bot inventory than the tradable amount
            target_qty = min(float(bot_qty), float(tradable_target_qty))

            # Weighted avg over the first target_qty of buy lots (FIFO)
            remaining = float(target_qty)
            total_cost = 0.0
            for q, p in lots:
                if remaining <= 0.0:
                    break
                use_q = min(float(q), float(remaining))
                total_cost += float(use_q) * float(p)
                remaining -= float(use_q)

            if target_qty > 1e-12:
                cost_basis[asset_code] = float(total_cost) / float(target_qty)
            else:
                cost_basis[asset_code] = 0.0

        return cost_basis

    def get_price(self, symbols: list) -> Dict[str, float]:
        """Get current bid/ask prices from Coinbase.
        
        Coinbase product IDs are formatted as BTC-USD, ETH-USD, etc.
        """
        buy_prices = {}
        sell_prices = {}
        valid_symbols = []

        for symbol in symbols:
            if symbol == "USDC-USD":
                continue

            # Format product ID for Coinbase (symbol-USD)
            product_id = f"{symbol}-USD" if "-USD" not in symbol else symbol

            path = f"/api/v3/brokerage/products/{product_id}"
            response = self.make_api_request("GET", path)

            if response and "bid" in response and "ask" in response:
                try:
                    ask = float(response["ask"])
                    bid = float(response["bid"])

                    buy_prices[symbol] = ask
                    sell_prices[symbol] = bid
                    valid_symbols.append(symbol)

                    # Update cache for transient failures later
                    try:
                        self._last_good_bid_ask[symbol] = {"ask": ask, "bid": bid, "ts": time.time()}
                    except Exception:
                        pass
                except Exception:
                    pass
            else:
                # Fallback to cached bid/ask so account value never drops due to a transient miss
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
        """Place a buy order on Coinbase."""
        # Fetch the current price of the asset (for sizing only)
        current_buy_prices, current_sell_prices, valid_symbols = self.get_price([symbol])
        if symbol not in current_buy_prices:
            return None
            
        current_price = current_buy_prices[symbol]
        asset_quantity = amount_in_usd / current_price

        max_retries = 3
        retries = 0

        while retries < max_retries:
            retries += 1
            response = None
            try:
                # Format product ID for Coinbase
                product_id = f"{symbol}-USD" if "-USD" not in symbol else symbol
                
                # Round to reasonable precision (8 decimals)
                rounded_quantity = round(asset_quantity, 8)

                # Coinbase order format
                body = {
                    "client_order_id": client_order_id,
                    "product_id": product_id,
                    "side": side.lower(),
                    "order_configuration": {
                        "market_market_ious": {
                            "quote_size": str(amount_in_usd)
                        }
                    }
                }

                path = "/api/v3/brokerage/orders"

                response = self.make_api_request("POST", path, json.dumps(body))
                
                if response and "error_response" not in response and "order_id" in response:
                    order_id = response.get("order_id", None)

                    # Persist minimal metadata so restarts can reconcile precisely
                    try:
                        if order_id:
                            self._pnl_ledger.setdefault("pending_orders", {})
                            self._pnl_ledger["pending_orders"][order_id] = {
                                "symbol": symbol,
                                "side": "buy",
                                "avg_cost_basis": float(avg_cost_basis) if avg_cost_basis is not None else None,
                                "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                                "tag": tag,
                                "created_ts": time.time(),
                            }
                            self._save_pnl_ledger()
                    except Exception:
                        pass

                    # Wait until the order is terminal
                    if order_id:
                        order = self._wait_for_order_terminal(symbol, order_id)
                        state = str(order.get("status", "")).lower().strip() if isinstance(order, dict) else ""
                        
                        if state not in ("filled", "done"):
                            # Not filled -> clear pending and do not record a trade
                            try:
                                self._pnl_ledger.get("pending_orders", {}).pop(order_id, None)
                                self._save_pnl_ledger()
                            except Exception:
                                pass
                            return None

                        # --- DEBUG DUMP ---
                        try:
                            debug_dir = os.path.join(HUB_DATA_DIR, "debug_trade_dumps")
                            os.makedirs(debug_dir, exist_ok=True)

                            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                            debug_path = os.path.join(
                                debug_dir,
                                f"{ts}_{symbol}_BUY_{order_id or client_order_id}.txt"
                            )

                            with open(debug_path, "w", encoding="utf-8") as f:
                                f.write(json.dumps(order, indent=2))
                        except Exception:
                            pass

                        filled_qty, avg_fill_price, notional_usd, fees_usd = self._extract_amounts_and_fees_from_order(order)

                        # Record for GUI history
                        self._record_trade(
                            side="buy",
                            symbol=symbol,
                            qty=float(filled_qty),
                            price=float(avg_fill_price) if avg_fill_price is not None else None,
                            notional_usd=float(notional_usd) if notional_usd is not None else None,
                            fees_usd=float(fees_usd) if fees_usd is not None else None,
                            avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                            pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                            tag=tag,
                            order_id=order_id,
                        )

                        # Mark as bot order
                        try:
                            base_symbol = str(symbol).upper().split("-")[0].strip()
                            self._mark_bot_order_id(base_symbol, order_id)
                        except Exception:
                            pass

                        # Maintain DCA stage
                        try:
                            base_symbol = str(symbol).upper().split("-")[0].strip()
                            if str(tag or "").upper().strip() == "DCA":
                                current_levels = list(self.dca_levels_triggered.get(base_symbol, []) or [])
                                current_levels.append(len(current_levels))
                                self.dca_levels_triggered[base_symbol] = current_levels
                            else:
                                self.dca_levels_triggered[base_symbol] = []
                        except Exception:
                            pass

                        # Clear pending
                        try:
                            self._pnl_ledger.get("pending_orders", {}).pop(order_id, None)
                            self._save_pnl_ledger()
                        except Exception:
                            pass

                    return response

            except Exception:
                pass

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
        """Place a sell order on Coinbase."""
        # Format product ID for Coinbase
        product_id = f"{symbol}-USD" if "-USD" not in symbol else symbol
        
        body = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": side.lower(),
            "order_configuration": {
                "market_market_ious": {
                    "base_size": str(asset_quantity)
                }
            }
        }

        path = "/api/v3/brokerage/orders"

        response = self.make_api_request("POST", path, json.dumps(body))

        if response and isinstance(response, dict) and "error_response" not in response and "order_id" in response:
            order_id = response.get("order_id", None)

            # Persist minimal metadata
            try:
                if order_id:
                    self._pnl_ledger.setdefault("pending_orders", {})
                    self._pnl_ledger["pending_orders"][order_id] = {
                        "symbol": symbol,
                        "side": "sell",
                        "avg_cost_basis": float(avg_cost_basis) if avg_cost_basis is not None else None,
                        "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                        "tag": tag,
                        "created_ts": time.time(),
                    }
                    self._save_pnl_ledger()
            except Exception:
                pass

            try:
                if order_id:
                    match = self._wait_for_order_terminal(symbol, order_id)
                    if not match:
                        return response

                    if str(match.get("status", "")).lower() not in ("filled", "done"):
                        # Not filled -> clear pending
                        try:
                            self._pnl_ledger.get("pending_orders", {}).pop(order_id, None)
                            self._save_pnl_ledger()
                        except Exception:
                            pass
                        return response

                    # --- DEBUG DUMP ---
                    try:
                        debug_dir = os.path.join(HUB_DATA_DIR, "debug_trade_dumps")
                        os.makedirs(debug_dir, exist_ok=True)

                        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        debug_path = os.path.join(
                            debug_dir,
                            f"{ts}_{symbol}_SELL_{order_id or client_order_id}.txt"
                        )

                        with open(debug_path, "w", encoding="utf-8") as f:
                            f.write(json.dumps(match, indent=2))
                    except Exception:
                        pass

                    actual_qty, actual_price, notional_usd, fees_usd = self._extract_amounts_and_fees_from_order(match)

                    # If we managed to get a better fill price, update the displayed PnL%
                    if avg_cost_basis is not None and actual_price is not None:
                        try:
                            acb = float(avg_cost_basis)
                            if acb > 0:
                                pnl_pct = ((float(actual_price) - acb) / acb) * 100.0
                        except Exception:
                            pass

                    self._record_trade(
                        side="sell",
                        symbol=symbol,
                        qty=float(actual_qty),
                        price=float(actual_price) if actual_price is not None else None,
                        notional_usd=float(notional_usd) if notional_usd is not None else None,
                        fees_usd=float(fees_usd) if fees_usd is not None else None,
                        avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                        pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                        tag=tag,
                        order_id=order_id,
                    )

                    # Filled sell = current trade is over
                    try:
                        base_symbol = str(symbol).upper().split("-")[0].strip()
                        self._clear_bot_order_ids_for_coin(base_symbol)
                        self.dca_levels_triggered[base_symbol] = []
                        self.trailing_pm.pop(base_symbol, None)
                    except Exception:
                        pass

                    # Clear pending
                    try:
                        if order_id:
                            self._pnl_ledger.get("pending_orders", {}).pop(order_id, None)
                            self._save_pnl_ledger()
                    except Exception:
                        pass
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

        # NEW: Also allow LTH bucket spending during normal loops (not only right after sells)
        try:
            self._maybe_process_lth_profit_allocation(0.0)
        except Exception:
            pass

        # Fetch account details
        account = self.get_account()
        # Fetch holdings
        holdings = self.get_holdings()
        # Fetch trading pairs
        trading_pairs = self.get_trading_pairs()

        # Hot-reload bot order ownership selections from the Hub (so avg entry reflects what you picked)
        try:
            reloaded = self._maybe_reload_bot_order_ids()
            if reloaded:
                # selection changed -> force a re-seed of open_positions from selected order IDs
                self._needs_ledger_seed_from_orders = True
        except Exception:
            pass


        # Use the (possibly refreshed) stored cost_basis
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
            pass



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

                sym = f"{asset}-USD"
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
                    tradable_qty = float(pos.get("qty", 0.0) or 0.0) if isinstance(pos, dict) else 0.0
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
        in_use = (trade_holdings_sell_value / total_account_value) * 100 if total_account_value > 0 else 0.0


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

        os.system('cls' if os.name == 'nt' else 'clear')
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
                pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(str(symbol).upper().strip())
                quantity = float(pos.get("qty", 0.0) or 0.0) if isinstance(pos, dict) else 0.0
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

                # keep the per-coin current price file behavior for consistency
                try:
                    file = open(symbol + '_current_price.txt', 'w+')
                    file.write(str(current_buy_price))
                    file.close()
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
                        if avg_cost_basis <= 0.0 or (abs((avg_cost_basis / fresh_cb) - 1.0) > 0.5):
                            avg_cost_basis = fresh_cb
                            try:
                                open_pos = self._pnl_ledger.get("open_positions", {})
                                if not isinstance(open_pos, dict):
                                    open_pos = {}
                                    self._pnl_ledger["open_positions"] = open_pos
                                base_key = str(symbol).upper().split("-")[0].strip()
                                open_pos[base_key] = {"usd_cost": float(avg_cost_basis) * float(quantity), "qty": float(quantity)}

                                self._save_pnl_ledger()
                            except Exception:
                                pass
            except Exception:
                pass

            if avg_cost_basis <= 0.0:
                avg_cost_basis = cost_basis.get(symbol, 0) or 0.0


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

                # Long-term (reserved) qty (ignored by trader)
                "lth_reserved_qty": float(reserved_qty),
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
                            self.dca_levels_triggered[symbol] = []
                            print(f"  Successfully sold {quantity} {symbol}.")
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
            hard_level = self.dca_levels[current_stage] if current_stage < len(self.dca_levels) else self.dca_levels[-1]
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

                        self._note_dca_buy(symbol)

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
                    "dca_triggered_stages": int(len(self.dca_levels_triggered.get(sym, []))),
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

        except Exception:
            pass

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
                total_qty = float(h.get("total_quantity", 0.0) or 0.0)

                # Only include symbols where the bot actually has a ledger qty.
                try:
                    pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(asset)
                    bot_qty = float(pos.get("qty", 0.0) or 0.0) if isinstance(pos, dict) else 0.0
                except Exception:
                    bot_qty = 0.0

                if bot_qty > 1e-12:
                    holding_full_symbols.append(f"{asset}-USD")

            except Exception:
                continue

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




            self.dca_levels_triggered[base_symbol] = []
            self.trailing_pm.pop(base_symbol, None)

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
                holding_full_symbols = []
                for h in holdings.get("results", []):
                    try:
                        asset = str(h.get("asset_code", "")).upper().strip()
                        if not asset or asset == "USDC":
                            continue

                        # Only include symbols where the bot actually has a ledger qty.
                        try:
                            pos = (self._pnl_ledger.get("open_positions", {}) or {}).get(asset)
                            bot_qty = float(pos.get("qty", 0.0) or 0.0) if isinstance(pos, dict) else 0.0
                        except Exception:
                            bot_qty = 0.0

                        if bot_qty > 1e-12:
                            holding_full_symbols.append(f"{asset}-USD")
                    except Exception:
                        continue



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
