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
from exchanges.binance_client import BinanceExchangeClient, BinanceAPIError, BinancePaperExchangeClient
from env_loader import load_env
import indicators

load_env()

# -----------------------------
# GUI HUB OUTPUTS
# -----------------------------
HUB_DATA_DIR = os.environ.get("POWERTRADER_HUB_DIR", os.path.join(os.path.dirname(__file__), "hub_data"))
os.makedirs(HUB_DATA_DIR, exist_ok=True)

TRADER_STATUS_PATH = os.path.join(HUB_DATA_DIR, "trader_status.json")
TRADE_HISTORY_PATH = os.path.join(HUB_DATA_DIR, "trade_history.jsonl")
PNL_LEDGER_PATH = os.path.join(HUB_DATA_DIR, "pnl_ledger.json")
ACCOUNT_VALUE_HISTORY_PATH = os.path.join(HUB_DATA_DIR, "account_value_history.jsonl")



# Initialize colorama
colorama.init(autoreset=True)

# -----------------------------
# GUI SETTINGS (coins list + main_neural_dir)
# -----------------------------
_GUI_SETTINGS_PATH = os.environ.get("POWERTRADER_GUI_SETTINGS") or os.path.join(
	os.path.dirname(os.path.abspath(__file__)),
	"gui_settings.json"
)

_STRATEGY_DEFAULT = {
	"mode": "selector",
	"indicators": {
		"macd": False,
		"stochastic": False,
		"momentum": False,
		"obv": False,
		"rsi": False,
		"bollinger": False,
		"ema": False,
		"atr": False,
		"volume_profile": False,
		"adx": False,
		"pivots": False,
		"ichimoku": False,
	},
	"check_all": False,
	"replace_neural": False,
}

_gui_settings_cache = {
	"mtime": None,
	"coins": ['BTC', 'ETH', 'XRP', 'BNB', 'DOGE'],  # fallback defaults
	"main_neural_dir": None,
	"strategy": dict(_STRATEGY_DEFAULT),
}

def _normalize_strategy(data: dict) -> dict:
	out = {
		"mode": _STRATEGY_DEFAULT["mode"],
		"indicators": dict(_STRATEGY_DEFAULT["indicators"]),
		"check_all": _STRATEGY_DEFAULT["check_all"],
		"replace_neural": _STRATEGY_DEFAULT["replace_neural"],
	}

	if not isinstance(data, dict):
		return out

	strat = data.get("strategy", {})
	if isinstance(strat, dict):
		mode = str(strat.get("mode", out["mode"])).lower().strip()
		if mode in ("selector", "super"):
			out["mode"] = mode

		out["check_all"] = bool(strat.get("check_all", out["check_all"]))
		out["replace_neural"] = bool(strat.get("replace_neural", out["replace_neural"]))

		ind = strat.get("indicators", {})
		if isinstance(ind, dict):
			for k in out["indicators"].keys():
				if k in ind:
					out["indicators"][k] = bool(ind.get(k))

	if out["check_all"]:
		for k in out["indicators"].keys():
			out["indicators"][k] = True
		out["mode"] = "super"

	return out

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

		_gui_settings_cache["mtime"] = mtime
		_gui_settings_cache["coins"] = coins
		_gui_settings_cache["main_neural_dir"] = main_neural_dir
		_gui_settings_cache["strategy"] = _normalize_strategy(data)

		return {
			"mtime": mtime,
			"coins": list(coins),
			"main_neural_dir": main_neural_dir,
			"strategy": dict(_gui_settings_cache["strategy"]),
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

_last_settings_mtime = None

def _refresh_paths_and_symbols():
	"""
	Hot-reload coins + main_neural_dir while trader is running.
	Updates globals: crypto_symbols, main_dir, base_paths
	"""
	global crypto_symbols, main_dir, base_paths, _last_settings_mtime

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

	# Keep it safe if folder isn't real on this machine
	if not os.path.isdir(mndir):
		mndir = os.getcwd()

	crypto_symbols = list(coins)
	main_dir = mndir
	base_paths = _build_base_paths(main_dir, crypto_symbols)


#API STUFF
EXCHANGE_PROVIDER = (os.environ.get("EXCHANGE_PROVIDER") or "robinhood").strip().lower()
API_KEY = ""
BASE64_PRIVATE_KEY = ""

if EXCHANGE_PROVIDER == "robinhood":
    try:
        with open("r_key.txt", "r", encoding="utf-8") as f:
            API_KEY = (f.read() or "").strip()
        with open("r_secret.txt", "r", encoding="utf-8") as f:
            BASE64_PRIVATE_KEY = (f.read() or "").strip()
    except Exception:
        API_KEY = ""
        BASE64_PRIVATE_KEY = ""

    if not API_KEY or not BASE64_PRIVATE_KEY:
        print(
            "\n[PowerTrader] Robinhood API credentials not found.\n"
            "Open the GUI and go to Settings -> Robinhood API -> Setup / Update.\n"
            "That wizard will generate your keypair, tell you where to paste the public key on Robinhood,\n"
            "and will save r_key.txt + r_secret.txt so this trader can authenticate.\n"
        )
        raise SystemExit(1)

class CryptoAPITrading:
    @staticmethod
    def _binance_keys_present() -> bool:
        key = (os.environ.get("BINANCE_API_KEY") or "").strip()
        secret = (os.environ.get("BINANCE_API_SECRET") or "").strip()
        if not key or not secret:
            return False
        placeholders = {"your_key_here", "your_secret_here", "changeme"}
        if key.lower() in placeholders or secret.lower() in placeholders:
            return False
        return True

    @staticmethod
    def _env_flag(name: str, default: bool = False) -> bool:
        raw = os.environ.get(name, None)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

    def __init__(self):
        # keep a copy of the folder map (same idea as trader.py)
        self.path_map = dict(base_paths)

        self.exchange_provider = EXCHANGE_PROVIDER
        self.exchange = None

        if self.exchange_provider == "binance":
            use_paper = self._env_flag("BINANCE_PAPER", False)
            keys_present = self._binance_keys_present()
            if not keys_present and self._env_flag("BINANCE_TESTNET", False):
                use_paper = True

            if use_paper:
                self.exchange = BinancePaperExchangeClient()
            else:
                try:
                    self.exchange = BinanceExchangeClient()
                except ValueError as e:
                    print(f"\n[PowerTrader] Binance API credentials not found.\n{e}\n")
                    raise SystemExit(1)
            self.api_key = ""
            self.private_key = None
            self.base_url = ""
        else:
            self.api_key = API_KEY
            private_key_seed = base64.b64decode(BASE64_PRIVATE_KEY)
            self.private_key = SigningKey(private_key_seed)
            self.base_url = "https://trading.robinhood.com"

        self.dca_levels_triggered = {}  # Track DCA levels for each crypto
        self.dca_levels = [-2.5, -5.0, -10.0, -20.0, -30.0, -40.0, -50.0]  # Moved to instance variable

        # --- Trailing profit margin (per-coin state) ---
        # Each coin keeps its own trailing PM line, peak, and "was above line" flag.
        self.trailing_pm = {}  # { "BTC": {"active": bool, "line": float, "peak": float, "was_above": bool}, ... }
        self.trailing_gap_pct = 0.5  # 0.5% trail gap behind peak
        self.pm_start_pct_no_dca = 5.0
        self.pm_start_pct_with_dca = 2.5

        self.cost_basis = self.calculate_cost_basis()  # Initialize cost basis at startup
        self.initialize_dca_levels()  # Initialize DCA levels based on historical buy orders

        # GUI hub persistence
        self._pnl_ledger = self._load_pnl_ledger()
        if not os.path.isfile(PNL_LEDGER_PATH):
            self._save_pnl_ledger()

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
        self.max_dca_buys_per_24h = 2
        self.dca_window_seconds = 24 * 60 * 60
        self._dca_buy_ts = {}         # { "BTC": [ts, ts, ...] } (DCA buys only)
        self._dca_last_sell_ts = {}   # { "BTC": ts_of_last_sell }
        self._seed_dca_window_from_history()

        # --- Paper test mode (forced entry/exit cycle) ---
        try:
            self.paper_test_mode = self._env_flag("BINANCE_PAPER_TEST", False)
            self.paper_test_hold_seconds = int(os.environ.get("BINANCE_PAPER_TEST_HOLD_SECONDS", "120"))
            self.paper_test_dca_seconds = int(os.environ.get("BINANCE_PAPER_TEST_DCA_SECONDS", "60"))
            self.paper_test_alloc_usd = float(os.environ.get("BINANCE_PAPER_TEST_ALLOC_USD", "50"))
            self.paper_test_coin = (os.environ.get("BINANCE_PAPER_TEST_COIN") or "").strip().upper()
        except Exception:
            self.paper_test_mode = False
            self.paper_test_hold_seconds = 120
            self.paper_test_dca_seconds = 60
            self.paper_test_alloc_usd = 50.0
            self.paper_test_coin = ""
        self._paper_test_state = {}

        # --- Strategy settings ---
        self.strategy_super_threshold = 0.6

        # --- Pine signal settings ---
        self.pine_signal_enabled = self._env_flag("PINE_SIGNAL_ENABLED", False)
        self.pine_signal_mode = (os.environ.get("PINE_SIGNAL_MODE", "filter") or "filter").strip().lower()
        if self.pine_signal_mode not in {"filter", "replace", "off"}:
            self.pine_signal_mode = "filter"
        self.pine_signal_use_exit = self._env_flag("PINE_SIGNAL_USE_EXIT", False)
        self.pine_signal_max_age = int(os.environ.get("PINE_SIGNAL_MAX_AGE_SECONDS", "300") or 300)
        self.pine_signal_file = (os.environ.get("PINE_SIGNAL_FILE") or "").strip()
        if not self.pine_signal_file and self.pine_signal_enabled:
            self.pine_signal_file = os.path.join(HUB_DATA_DIR, "pine_signals.jsonl")
        self._pine_signal_state = {"pos": 0, "signals": {}}








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
                    return json.load(f)
        except Exception:
            pass
        return {"total_realized_profit_usd": 0.0, "last_updated_ts": time.time()}

    def _save_pnl_ledger(self) -> None:
        try:
            self._pnl_ledger["last_updated_ts"] = time.time()
            self._atomic_write_json(PNL_LEDGER_PATH, self._pnl_ledger)
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
    ) -> None:
        """
        Minimal local ledger for GUI:
        - append trade_history.jsonl
        - update pnl_ledger.json on sells (using estimated price * qty)
        - store the exact PnL% at the moment for DCA buys / sells (for GUI trade history)
        """
        ts = time.time()
        realized = None
        if side.lower() == "sell" and price is not None and avg_cost_basis is not None:
            try:
                realized = (float(price) - float(avg_cost_basis)) * float(qty)
                self._pnl_ledger["total_realized_profit_usd"] = float(self._pnl_ledger.get("total_realized_profit_usd", 0.0)) + float(realized)
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
            "realized_profit_usd": realized,
            "order_id": order_id,
        }
        self._append_jsonl(TRADE_HISTORY_PATH, entry)
        if realized is not None:
            self._save_pnl_ledger()


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
    def _normalize_signal_symbol(raw: str) -> str:
        sym = str(raw or "").strip().upper()
        if not sym:
            return ""
        if "-" in sym:
            base, _ = sym.split("-", 1)
            return base
        if "_" in sym:
            base, _ = sym.split("_", 1)
            return base
        if "/" in sym:
            base, _ = sym.split("/", 1)
            return base
        for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "BNB"):
            if sym.endswith(quote) and len(sym) > len(quote):
                return sym[: -len(quote)]
        return sym

    def _refresh_pine_signals(self) -> None:
        if not self.pine_signal_enabled or not self.pine_signal_file:
            return
        if not os.path.isfile(self.pine_signal_file):
            return
        try:
            pos = int(self._pine_signal_state.get("pos", 0))
            with open(self.pine_signal_file, "r", encoding="utf-8") as f:
                f.seek(pos)
                for line in f:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    symbol = self._normalize_signal_symbol(payload.get("symbol") or payload.get("ticker") or "")
                    if not symbol:
                        continue
                    action = str(payload.get("action") or payload.get("signal") or "").strip().lower()
                    if action not in {"buy", "sell", "hold", "stop"}:
                        continue
                    ts_val = payload.get("ts") or payload.get("time") or payload.get("timestamp")
                    try:
                        ts = float(ts_val)
                        if ts > 1e12:
                            ts = ts / 1000.0
                    except Exception:
                        ts = time.time()
                    strength = payload.get("strength")
                    self._pine_signal_state.setdefault("signals", {})[symbol] = {
                        "action": action,
                        "ts": ts,
                        "strength": strength,
                    }
                self._pine_signal_state["pos"] = f.tell()
        except Exception:
            pass

    def _get_pine_signal(self, symbol: str) -> Optional[dict]:
        if not self.pine_signal_enabled or self.pine_signal_mode == "off":
            return None
        self._refresh_pine_signals()
        sym = self._normalize_signal_symbol(symbol)
        signals = self._pine_signal_state.get("signals", {}) if isinstance(self._pine_signal_state, dict) else {}
        sig = signals.get(sym)
        if not isinstance(sig, dict):
            return None
        try:
            ts = float(sig.get("ts", 0.0) or 0.0)
        except Exception:
            ts = 0.0
        if self.pine_signal_max_age > 0 and ts > 0:
            if (time.time() - ts) > float(self.pine_signal_max_age):
                return None
        return dict(sig)

    def _get_strategy_settings(self) -> dict:
        try:
            s = _load_gui_settings()
            strat = s.get("strategy", None)
            if isinstance(strat, dict):
                return dict(strat)
        except Exception:
            pass
        return dict(_STRATEGY_DEFAULT)

    def _fetch_strategy_candles(self, symbol: str, limit: int = 120) -> list:
        if self.exchange_provider != "binance" or not self.exchange:
            return []
        settings = _load_gui_settings()
        tf = str(settings.get("default_timeframe", "1hour")).lower().strip()
        limit = int(settings.get("candles_limit", limit) or limit)
        tf_map = {
            "1min": "1m",
            "5min": "5m",
            "15min": "15m",
            "30min": "30m",
            "1hour": "1h",
            "2hour": "2h",
            "4hour": "4h",
            "8hour": "8h",
            "12hour": "12h",
            "1day": "1d",
            "1week": "1w",
        }
        interval = tf_map.get(tf, "1h")
        try:
            return self.exchange.get_klines(symbol, interval=interval, limit=limit)
        except Exception:
            return []

    @staticmethod
    def _extract_series(candles: list) -> dict:
        closes = []
        highs = []
        lows = []
        volumes = []
        for c in candles or []:
            try:
                closes.append(float(c.get("close", 0.0)))
                highs.append(float(c.get("high", 0.0)))
                lows.append(float(c.get("low", 0.0)))
                volumes.append(float(c.get("volume", 0.0)))
            except Exception:
                continue
        return {"closes": closes, "highs": highs, "lows": lows, "volumes": volumes}

    def _indicator_condition_score(self, name: str, series: dict) -> tuple:
        closes = series["closes"]
        highs = series["highs"]
        lows = series["lows"]
        volumes = series["volumes"]
        price = closes[-1] if closes else 0.0

        if name == "rsi":
            val = indicators.rsi(closes)
            ok = val is not None and val < 30.0
            return ok, 1.0 if ok else 0.0

        if name == "macd":
            macd_line, signal_line, _ = indicators.macd(closes)
            if macd_line is None or signal_line is None or len(closes) < 2:
                return False, 0.0
            # detect bullish cross using last two MACD values
            macd_series = indicators._ema_series(closes, 12)
            slow_series = indicators._ema_series(closes, 26)
            if not macd_series or not slow_series:
                return False, 0.0
            macd_vals = [f - s for f, s in zip(macd_series[-len(slow_series):], slow_series)]
            signal_vals = indicators._ema_series(macd_vals, 9)
            if len(macd_vals) < 2 or len(signal_vals) < 2:
                return False, 0.0
            prev_cross = macd_vals[-2] <= signal_vals[-2]
            now_cross = macd_vals[-1] > signal_vals[-1]
            ok = prev_cross and now_cross
            return ok, 1.0 if ok else 0.0

        if name == "stochastic":
            k, d = indicators.stochastic(highs, lows, closes)
            if k is None or d is None:
                return False, 0.0
            if len(closes) < 2:
                return False, 0.0
            # compute previous k/d using series without last close
            k_prev, d_prev = indicators.stochastic(highs[:-1], lows[:-1], closes[:-1])
            if k_prev is None or d_prev is None:
                return False, 0.0
            ok = (k < 20.0) and (k_prev <= d_prev) and (k > d)
            return ok, 1.0 if ok else 0.0

        if name == "momentum":
            val = indicators.momentum(closes)
            ok = val is not None and val > 0.0
            return ok, 1.0 if ok else 0.0

        if name == "obv":
            if len(closes) < 2 or len(volumes) < 2:
                return False, 0.0
            obv_now = indicators.obv(closes, volumes)
            obv_prev = indicators.obv(closes[:-1], volumes[:-1])
            ok = obv_now is not None and obv_prev is not None and obv_now > obv_prev
            return ok, 1.0 if ok else 0.0

        if name == "bollinger":
            upper, mid, lower = indicators.bollinger_bands(closes)
            ok = lower is not None and price <= lower
            return ok, 1.0 if ok else 0.0

        if name == "ema":
            ema_fast = indicators.ema(closes, 8)
            ema_slow = indicators.ema(closes, 21)
            if ema_slow is None:
                return False, 0.0
            ok = (ema_fast is not None and ema_fast > ema_slow) or (price > ema_slow)
            return ok, 1.0 if ok else 0.0

        if name == "atr":
            val = indicators.atr(highs, lows, closes)
            ok = val is not None
            return True, 0.5 if ok else 0.0

        if name == "volume_profile":
            ratio = indicators.volume_profile(volumes)
            ok = ratio is not None and ratio > 1.0
            return ok, 1.0 if ok else 0.0

        if name == "adx":
            val = indicators.adx(highs, lows, closes)
            ok = val is not None and val > 20.0
            return ok, 1.0 if ok else 0.0

        if name == "pivots":
            piv = indicators.pivots(highs, lows, closes)
            if not piv:
                return False, 0.0
            s1 = piv.get("s1", None)
            if not s1:
                return False, 0.0
            ok = (price >= s1 * 0.99) and (price <= s1 * 1.01)
            return ok, 1.0 if ok else 0.0

        if name == "ichimoku":
            ichi = indicators.ichimoku(highs, lows)
            if not ichi:
                return False, 0.0
            cloud_top = max(ichi["senkou_a"], ichi["senkou_b"])
            ok = (price > cloud_top) and (ichi["tenkan"] > ichi["kijun"])
            return ok, 1.0 if ok else 0.0

        return False, 0.0

    def _strategy_should_enter(self, symbol: str, long_level: int, short_level: int, candles: list) -> tuple:
        cfg = self._get_strategy_settings()
        indicators_selected = [k for k, v in (cfg.get("indicators") or {}).items() if v]
        neural_ok = (long_level >= 3) and (short_level == 0)
        neural_score = (max(0.0, min(7.0, float(long_level))) / 7.0) if short_level == 0 else 0.0

        if not indicators_selected and not cfg.get("replace_neural", False):
            return neural_ok, neural_score
        if not indicators_selected and cfg.get("replace_neural", False):
            return False, 0.0

        series = self._extract_series(candles)
        if not series["closes"] or len(series["closes"]) < 30:
            if cfg.get("replace_neural", False):
                return False, 0.0
            return neural_ok, neural_score

        conditions = []
        scores = []
        for ind in indicators_selected:
            ok, score = self._indicator_condition_score(ind, series)
            conditions.append(ok)
            scores.append(score)

        mode = str(cfg.get("mode", "selector")).lower().strip()
        replace_neural = bool(cfg.get("replace_neural", False))

        if mode == "selector":
            indicators_ok = all(conditions) if conditions else True
            if replace_neural:
                return indicators_ok, 1.0 if indicators_ok else 0.0
            return (neural_ok and indicators_ok), neural_score

        # super mode
        if not replace_neural:
            scores.append(neural_score)
        if not scores:
            return neural_ok, neural_score
        score = sum(scores) / float(len(scores))
        return (score >= float(self.strategy_super_threshold)), score

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

            # Sort orders by creation time in ascending order (oldest first)
            filled_orders.sort(key=lambda x: x["created_at"])

            # Find the timestamp of the most recent sell order
            most_recent_sell_time = None
            for order in reversed(filled_orders):
                if order["side"] == "sell":
                    most_recent_sell_time = order["created_at"]
                    break

            # Determine the cutoff time for buy orders
            if most_recent_sell_time:
                # Find all buy orders after the most recent sell
                relevant_buy_orders = [
                    order for order in filled_orders
                    if order["side"] == "buy" and order["created_at"] > most_recent_sell_time
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
            relevant_buy_orders.sort(key=lambda x: x["created_at"])

            # Identify the first buy order in the relevant list
            first_buy_order = relevant_buy_orders[0]
            first_buy_time = first_buy_order["created_at"]

            # Count the number of buy orders after the first buy
            buy_orders_after_first = [
                order for order in relevant_buy_orders
                if order["created_at"] > first_buy_time
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

    def _paper_test_tick(
        self,
        holdings_list: list,
        buying_power: float,
        current_buy_prices: Dict[str, float],
        current_sell_prices: Dict[str, float],
    ) -> bool:
        traded = False
        if not (self.paper_test_mode and self.exchange_provider == "binance"):
            return False
        if not isinstance(self.exchange, BinancePaperExchangeClient):
            return False

        target = self.paper_test_coin or (crypto_symbols[0] if crypto_symbols else "")
        target = str(target).upper().strip()
        if not target:
            return False

        full_symbol = f"{target}-USD"
        price = float(current_buy_prices.get(full_symbol, 0.0) or 0.0)
        if price <= 0.0:
            return False

        holding = None
        for h in holdings_list:
            if str(h.get("asset_code", "")).upper().strip() == target:
                holding = h
                break

        state = self._paper_test_state.setdefault(
            target, {"stage": "idle", "buy_ts": None, "dca_done": False}
        )
        now = time.time()

        if not holding or float(holding.get("total_quantity", 0.0) or 0.0) <= 0.0:
            if state.get("stage") != "buying":
                alloc = min(float(buying_power or 0.0), float(self.paper_test_alloc_usd))
                if alloc <= 0.0:
                    return False
                resp = self.place_buy_order(
                    str(uuid.uuid4()),
                    "buy",
                    "market",
                    full_symbol,
                    alloc,
                    tag="PAPER_TEST_ENTRY",
                )
                if resp and "errors" not in resp:
                    state["stage"] = "holding"
                    state["buy_ts"] = now
                    state["dca_done"] = False
                    traded = True
            return traded

        buy_ts = float(state.get("buy_ts") or now)
        held_for = now - buy_ts

        if (not state.get("dca_done")) and (held_for >= float(self.paper_test_dca_seconds)):
            alloc = min(float(buying_power or 0.0), float(self.paper_test_alloc_usd) * 0.5)
            if alloc > 0.0:
                resp = self.place_buy_order(
                    str(uuid.uuid4()),
                    "buy",
                    "market",
                    full_symbol,
                    alloc,
                    tag="PAPER_TEST_DCA",
                )
                if resp and "errors" not in resp:
                    state["dca_done"] = True
                    traded = True

        if held_for >= float(self.paper_test_hold_seconds):
            qty = float(holding.get("total_quantity", 0.0) or 0.0)
            sell_price = float(current_sell_prices.get(full_symbol, price) or price)
            avg_cost_basis = None
            try:
                avg_cost_basis = float(self.cost_basis.get(target, 0.0))
                if avg_cost_basis <= 0.0:
                    avg_cost_basis = None
            except Exception:
                avg_cost_basis = None
            if qty > 0.0:
                resp = self.place_sell_order(
                    str(uuid.uuid4()),
                    "sell",
                    "market",
                    full_symbol,
                    qty,
                    expected_price=sell_price,
                    avg_cost_basis=avg_cost_basis,
                    tag="PAPER_TEST_EXIT",
                )
                if resp and "errors" not in resp:
                    state["stage"] = "idle"
                    state["buy_ts"] = None
                    state["dca_done"] = False
                    traded = True
        return traded


    def make_api_request(self, method: str, path: str, body: Optional[str] = "") -> Any:
        if self.exchange_provider != "robinhood":
            raise RuntimeError("Robinhood API request attempted while EXCHANGE_PROVIDER=binance.")

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
        if self.exchange_provider != "robinhood":
            raise RuntimeError("Robinhood authorization requested while EXCHANGE_PROVIDER=binance.")
        message_to_sign = f"{self.api_key}{timestamp}{path}{method}{body}"
        signed = self.private_key.sign(message_to_sign.encode("utf-8"))

        return {
            "x-api-key": self.api_key,
            "x-signature": base64.b64encode(signed.signature).decode("utf-8"),
            "x-timestamp": str(timestamp),
        }

    def _binance_account(self) -> Dict[str, Any]:
        if not self.exchange:
            return {}
        try:
            balances = self.exchange.get_balances()
            quote = getattr(self.exchange, "default_quote", "USDT")
            buying_power = float(balances.get(quote, {}).get("free", 0.0))
            return {"buying_power": buying_power, "balances": balances}
        except BinanceAPIError:
            return {}

    def _binance_holdings(self) -> Dict[str, Any]:
        if not self.exchange:
            return {"results": []}
        try:
            balances = self.exchange.get_balances()
            quote = getattr(self.exchange, "default_quote", "USDT")
            results = []
            for asset, info in balances.items():
                if asset == quote:
                    continue
                total = float(info.get("total", 0.0))
                if total > 0.0:
                    results.append({"asset_code": asset, "total_quantity": total})
            return {"results": results}
        except BinanceAPIError:
            return {"results": []}

    def _binance_orders(self, symbol: str) -> Dict[str, Any]:
        if not self.exchange:
            return {"results": []}
        try:
            orders = self.exchange.get_order_history(symbol, limit=1000)
        except BinanceAPIError:
            return {"results": []}

        results = []
        for order in orders or []:
            side = str(order.get("side", "")).lower()
            status = str(order.get("status", "")).upper()
            if status in {"FILLED", "PARTIALLY_FILLED"}:
                state = "filled"
            elif status in {"CANCELED", "REJECTED", "EXPIRED"}:
                state = "canceled"
            else:
                state = "open"

            ts_ms = order.get("time", None)
            created_at = ""
            try:
                created_at = datetime.datetime.utcfromtimestamp(int(ts_ms) / 1000).isoformat() + "Z"
            except Exception:
                created_at = ""

            executions = []
            try:
                exec_qty = float(order.get("executedQty", 0.0))
                exec_quote = float(order.get("cummulativeQuoteQty", 0.0))
                if exec_qty > 0:
                    executions.append(
                        {
                            "quantity": str(exec_qty),
                            "effective_price": str(exec_quote / exec_qty),
                        }
                    )
            except Exception:
                pass

            results.append(
                {
                    "side": side,
                    "state": state,
                    "created_at": created_at,
                    "executions": executions,
                }
            )
        return {"results": results}

    def get_account(self) -> Any:
        if self.exchange_provider == "binance":
            return self._binance_account()
        path = "/api/v1/crypto/trading/accounts/"
        return self.make_api_request("GET", path)

    def get_holdings(self) -> Any:
        if self.exchange_provider == "binance":
            return self._binance_holdings()
        path = "/api/v1/crypto/trading/holdings/"
        return self.make_api_request("GET", path)

    def get_trading_pairs(self) -> Any:
        if self.exchange_provider == "binance":
            return [{"symbol": f"{sym}-{getattr(self.exchange, 'default_quote', 'USDT')}"} for sym in crypto_symbols]
        path = "/api/v1/crypto/trading/trading_pairs/"
        response = self.make_api_request("GET", path)

        if not response or "results" not in response:
            return []

        trading_pairs = response.get("results", [])
        if not trading_pairs:
            return []

        return trading_pairs

    def get_orders(self, symbol: str) -> Any:
        if self.exchange_provider == "binance":
            return self._binance_orders(symbol)
        path = f"/api/v1/crypto/trading/orders/?symbol={symbol}"
        return self.make_api_request("GET", path)

    def calculate_cost_basis(self):
        holdings = self.get_holdings()
        if not holdings or "results" not in holdings:
            return {}

        active_assets = {holding["asset_code"] for holding in holdings.get("results", [])}
        current_quantities = {
            holding["asset_code"]: float(holding["total_quantity"])
            for holding in holdings.get("results", [])
        }

        cost_basis = {}

        for asset_code in active_assets:
            orders = self.get_orders(f"{asset_code}-USD")
            if not orders or "results" not in orders:
                continue

            # Get all filled buy orders, sorted from most recent to oldest
            buy_orders = [
                order for order in orders["results"]
                if order["side"] == "buy" and order["state"] == "filled"
            ]
            buy_orders.sort(key=lambda x: x["created_at"], reverse=True)

            remaining_quantity = current_quantities[asset_code]
            total_cost = 0.0

            for order in buy_orders:
                for execution in order.get("executions", []):
                    quantity = float(execution["quantity"])
                    price = float(execution["effective_price"])

                    if remaining_quantity <= 0:
                        break

                    # Use only the portion of the quantity needed to match the current holdings
                    if quantity > remaining_quantity:
                        total_cost += remaining_quantity * price
                        remaining_quantity = 0
                    else:
                        total_cost += quantity * price
                        remaining_quantity -= quantity

                if remaining_quantity <= 0:
                    break

            if current_quantities[asset_code] > 0:
                cost_basis[asset_code] = total_cost / current_quantities[asset_code]
            else:
                cost_basis[asset_code] = 0.0

        return cost_basis

    def get_price(self, symbols: list) -> Dict[str, float]:
        buy_prices = {}
        sell_prices = {}
        valid_symbols = []

        if self.exchange_provider == "binance":
            for symbol in symbols:
                if symbol == "USDC-USD":
                    continue
                try:
                    price = float(self.exchange.get_price(symbol))
                    if price > 0.0:
                        buy_prices[symbol] = price
                        sell_prices[symbol] = price
                        valid_symbols.append(symbol)
                        try:
                            self._last_good_bid_ask[symbol] = {"ask": price, "bid": price, "ts": time.time()}
                        except Exception:
                            pass
                        continue
                except Exception:
                    pass

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

        for symbol in symbols:
            if symbol == "USDC-USD":
                continue

            path = f"/api/v1/crypto/marketdata/best_bid_ask/?symbol={symbol}"
            response = self.make_api_request("GET", path)

            if response and "results" in response:
                result = response["results"][0]
                ask = float(result["ask_inclusive_of_buy_spread"])
                bid = float(result["bid_inclusive_of_sell_spread"])

                buy_prices[symbol] = ask
                sell_prices[symbol] = bid
                valid_symbols.append(symbol)

                # Update cache for transient failures later
                try:
                    self._last_good_bid_ask[symbol] = {"ask": ask, "bid": bid, "ts": time.time()}
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
        if self.exchange_provider == "binance":
            try:
                current_buy_prices, _, _ = self.get_price([symbol])
                current_price = float(current_buy_prices.get(symbol, 0.0) or 0.0)
                if current_price <= 0.0 or not self.exchange:
                    return None

                asset_quantity = amount_in_usd / current_price
                response = self.exchange.create_order(
                    symbol=symbol,
                    side="BUY",
                    type=order_type.upper(),
                    quantity=asset_quantity,
                )
                fill_price = float(current_price)
                try:
                    exec_qty = response.get("executedQty")
                    if exec_qty is not None:
                        asset_quantity = float(exec_qty)
                except Exception:
                    pass
                try:
                    resp_price = float(response.get("price", 0.0) or 0.0)
                    if resp_price > 0.0:
                        fill_price = resp_price
                except Exception:
                    pass
                try:
                    exec_qty = float(response.get("executedQty", 0.0) or 0.0)
                    exec_quote = float(response.get("cummulativeQuoteQty", 0.0) or 0.0)
                    if exec_qty > 0.0 and exec_quote > 0.0:
                        fill_price = exec_quote / exec_qty
                except Exception:
                    pass
                order_id = response.get("orderId") or response.get("clientOrderId")
                self._record_trade(
                    side="buy",
                    symbol=symbol,
                    qty=float(asset_quantity),
                    price=float(fill_price),
                    avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                    pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                    tag=tag,
                    order_id=order_id,
                )
                return response
            except Exception as e:
                return {"errors": [{"detail": str(e)}]}

        # Fetch the current price of the asset
        current_buy_prices, current_sell_prices, valid_symbols = self.get_price([symbol])
        current_price = current_buy_prices[symbol]
        asset_quantity = amount_in_usd / current_price

        max_retries = 5
        retries = 0

        while retries < max_retries:
            retries += 1
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
                response = self.make_api_request("POST", path, json.dumps(body))
                if response and "errors" not in response:
                    # Record for GUI history (estimated fill at current_price)
                    try:
                        order_id = response.get("id", None) if isinstance(response, dict) else None
                    except Exception:
                        order_id = None
                    self._record_trade(
                        side="buy",
                        symbol=symbol,
                        qty=float(rounded_quantity),
                        price=float(current_price),
                        avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                        pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                        tag=tag,
                        order_id=order_id,
                    )
                    return response  # Successfully placed order

            except Exception as e:
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
        if self.exchange_provider == "binance":
            try:
                if not self.exchange:
                    return None
                response = self.exchange.create_order(
                    symbol=symbol,
                    side="SELL",
                    type=order_type.upper(),
                    quantity=asset_quantity,
                )
                fill_price = float(expected_price) if expected_price is not None else None
                try:
                    exec_qty = response.get("executedQty")
                    if exec_qty is not None:
                        asset_quantity = float(exec_qty)
                except Exception:
                    pass
                try:
                    resp_price = float(response.get("price", 0.0) or 0.0)
                    if resp_price > 0.0:
                        fill_price = resp_price
                except Exception:
                    pass
                try:
                    exec_qty = float(response.get("executedQty", 0.0) or 0.0)
                    exec_quote = float(response.get("cummulativeQuoteQty", 0.0) or 0.0)
                    if exec_qty > 0.0 and exec_quote > 0.0:
                        fill_price = exec_quote / exec_qty
                except Exception:
                    pass
                order_id = response.get("orderId") or response.get("clientOrderId")
                self._record_trade(
                    side="sell",
                    symbol=symbol,
                    qty=float(asset_quantity),
                    price=float(fill_price) if fill_price is not None else None,
                    avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                    pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                    tag=tag,
                    order_id=order_id,
                )
                return response
            except Exception as e:
                return {"errors": [{"detail": str(e)}]}

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
   
        response = self.make_api_request("POST", path, json.dumps(body))

        if response and isinstance(response, dict) and "errors" not in response:
            order_id = response.get("id", None)
            self._record_trade(
                side="sell",
                symbol=symbol,
                qty=float(asset_quantity),
                price=float(expected_price) if expected_price is not None else None,
                avg_cost_basis=float(avg_cost_basis) if avg_cost_basis is not None else None,
                pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                tag=tag,
                order_id=order_id,
            )

        return response



    def manage_trades(self):
        trades_made = False  # Flag to track if any trade was made in this iteration

        # Hot-reload coins list + paths from GUI settings while running
        try:
            _refresh_paths_and_symbols()
            self.path_map = dict(base_paths)
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

        # Paper test mode can trigger simulated trades regardless of signals.
        try:
            paper_traded = self._paper_test_tick(
                holdings_list,
                buying_power,
                current_buy_prices,
                current_sell_prices,
            )
        except Exception:
            paper_traded = False

        if paper_traded:
            trades_made = True
            # Refresh account/holdings so UI reflects the simulated trade immediately.
            try:
                account = self.get_account()
                holdings = self.get_holdings()
                buying_power = float(account.get("buying_power", 0))
                holdings_list = holdings.get("results", []) if isinstance(holdings, dict) else []
            except Exception:
                pass

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

            # Neural DCA only applies to first 4 DCA stages:
            # stage 0-> neural 4, stage 1->5, stage 2->6, stage 3->7
            if next_stage < 4:
                neural_next = next_stage + 4
                next_dca_display = f"{hard_next:.2f}% / N{neural_next}"
            else:
                next_dca_display = f"{hard_next:.2f}%"

            # --- DCA DISPLAY LINE (show whichever trigger will be hit first: higher of NEURAL line vs HARD line) ---
            # Hardcoded gives an actual price line: cost_basis * (1 + hard_next%).
            # Neural gives an actual price line from low_bound_prices.html (N4..N7 = 4th..7th blue line).
            dca_line_source = "HARD"
            dca_line_price = 0.0
            dca_line_pct = 0.0

            if avg_cost_basis > 0:
                # Hardcoded trigger line price
                hard_line_price = avg_cost_basis * (1.0 + (hard_next / 100.0))

                # Default to hardcoded unless neural line is higher (hit first)
                dca_line_price = hard_line_price

                if next_stage < 4:
                    neural_level_needed_disp = next_stage + 4  # stage 0->N4, 1->N5, 2->N6, 3->N7
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

            pine_sig = self._get_pine_signal(symbol)
            if self.pine_signal_use_exit and pine_sig:
                action = str(pine_sig.get("action") or "").strip().lower()
                if action in {"sell", "stop"} and quantity > 0:
                    response = self.place_sell_order(
                        str(uuid.uuid4()),
                        "sell",
                        "market",
                        full_symbol,
                        quantity,
                        expected_price=current_sell_price,
                        avg_cost_basis=avg_cost_basis,
                        pnl_pct=gain_loss_percentage_sell,
                        tag="PINE_STOP" if action == "stop" else "PINE_SELL",
                    )
                    trades_made = True
                    self.trailing_pm.pop(symbol, None)
                    self._reset_dca_window_for_trade(symbol, sold=True)
                    print(f"  Pine signal {action} for {symbol}. Sell response: {response}")
                    time.sleep(2)
                    holdings = self.get_holdings()
                    continue


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

                state = self.trailing_pm.get(symbol)
                if state is None:
                    state = {"active": False, "line": base_pm_line, "peak": 0.0, "was_above": False}
                    self.trailing_pm[symbol] = state
                else:
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
                dca_amount = value * 2
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



        allocation_in_usd = total_account_value * (0.00005/len(crypto_symbols))
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

            pine_sig = self._get_pine_signal(base_symbol)
            pine_action = ""
            if pine_sig:
                pine_action = str(pine_sig.get("action") or "").strip().lower()
            if self.pine_signal_enabled and self.pine_signal_mode == "replace":
                if pine_action != "buy":
                    start_index += 1
                    continue
            elif self.pine_signal_enabled and self.pine_signal_mode == "filter":
                if pine_action and pine_action != "buy":
                    start_index += 1
                    continue

            strategy_cfg = self._get_strategy_settings()
            indicators_selected = [k for k, v in (strategy_cfg.get("indicators") or {}).items() if v]
            use_strategy = bool(indicators_selected or strategy_cfg.get("replace_neural") or strategy_cfg.get("mode") == "super")

            if use_strategy:
                candles = self._fetch_strategy_candles(base_symbol)
                allowed, _ = self._strategy_should_enter(base_symbol, buy_count, sell_count, candles)
                if not allowed:
                    start_index += 1
                    continue
            else:
                # Default behavior: long must be >= 3 and short must be 0
                if not (buy_count >= 3 and sell_count == 0):
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
