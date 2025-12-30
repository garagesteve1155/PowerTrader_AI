"""
PowerTrader_AI - Neural Signal Processor

GitHub: https://github.com/garagesteve1155/PowerTrader_AI
Author: Stephen Bryant Hughes
License: Apache License Version 2.0

PowerTrader_AI is a fully automated crypto trading system powered by a custom price prediction AI
and a structured/tiered DCA system. This script processes neural signals and runs the prediction
engine, generating trading signals when the ask price drops below at least 3 of the AI's predicted
low prices across multiple timeframes (1hr to 1wk).
"""

"""Neural signal processor and runner for PowerTrader."""

import base64
import json
import linecache
import os
import sys
import time
from kucoin.client import Market
from nacl.signing import SigningKey
import requests

market = Market(url="https://api.kucoin.com")

# -----------------------------
# Robinhood market-data (current ASK), same source as rhcb.py trader:
#   GET /api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD
#   use result["ask_inclusive_of_buy_spread"]
# -----------------------------
ROBINHOOD_BASE_URL = "https://trading.robinhood.com"

_RH_MD = None  # lazy-init so import doesn't explode if creds missing


class RobinhoodMarketData:
    def __init__(
        self,
        api_key: str,
        base64_private_key: str,
        base_url: str = ROBINHOOD_BASE_URL,
        timeout: int = 10,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

        if not self.api_key:
            raise RuntimeError("Robinhood API key is empty (r_key.txt).")

        try:
            raw_private = base64.b64decode((base64_private_key or "").strip())
            self.private_key = SigningKey(raw_private)
        except Exception as e:
            raise RuntimeError(
                f"Failed to decode Robinhood private key (r_secret.txt): {e}"
            )

        self.session = requests.Session()

    def _get_current_timestamp(self) -> int:
        return int(time.time())

    def _get_authorization_header(
        self, method: str, path: str, body: str, timestamp: int
    ) -> dict:
        # matches the trader's signing format
        method = method.upper()
        body = body or ""
        message_to_sign = f"{self.api_key}{timestamp}{path}{method}{body}"
        signed = self.private_key.sign(message_to_sign.encode("utf-8"))
        signature_b64 = base64.b64encode(signed.signature).decode("utf-8")

        return {
            "x-api-key": self.api_key,
            "x-timestamp": str(timestamp),
            "x-signature": signature_b64,
            "Content-Type": "application/json",
        }

    def make_api_request(self, method: str, path: str, body: str = "") -> dict:
        url = f"{self.base_url}{path}"
        ts = self._get_current_timestamp()
        headers = self._get_authorization_header(method, path, body, ts)

        resp = self.session.request(
            method=method.upper(),
            url=url,
            headers=headers,
            data=body or None,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Robinhood HTTP {resp.status_code}: {resp.text}")
        return resp.json()

    def get_current_ask(self, symbol: str) -> float:
        symbol = (symbol or "").strip().upper()
        path = f"/api/v1/crypto/marketdata/best_bid_ask/?symbol={symbol}"
        data = self.make_api_request("GET", path)

        if not data or "results" not in data or not data["results"]:
            raise RuntimeError(
                f"Robinhood best_bid_ask returned no results for {symbol}: {data}"
            )

        result = data["results"][0]
        # EXACTLY like rhcb.py's get_price(): ask_inclusive_of_buy_spread
        return float(result["ask_inclusive_of_buy_spread"])


def robinhood_current_ask(symbol: str) -> float:
    """
    Returns Robinhood current BUY price (ask_inclusive_of_buy_spread) for symbols like 'BTC-USD'.
    Reads creds from r_key.txt and r_secret.txt in the same folder as this script.
    """
    global _RH_MD
    if _RH_MD is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        key_path = os.path.join(base_dir, "r_key.txt")
        secret_path = os.path.join(base_dir, "r_secret.txt")

        if not os.path.isfile(key_path) or not os.path.isfile(secret_path):
            raise RuntimeError(
                "Missing r_key.txt and/or r_secret.txt next to pt_thinker.py. "
                "Run pt_trader.py once to create them (and to set your Robinhood API key)."
            )

        with open(key_path, "r", encoding="utf-8") as f:
            api_key = f.read()
        with open(secret_path, "r", encoding="utf-8") as f:
            priv_b64 = f.read()

        _RH_MD = RobinhoodMarketData(api_key=api_key, base64_private_key=priv_b64)

    return _RH_MD.get_current_ask(symbol)


def restart_program():
    """Restarts the current program (no CLI args; uses hardcoded COIN_SYMBOLS)."""
    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
    except Exception as e:
        print(f"Error during program restart: {e}")


def PrintException():
    """Print details of the current exception including line number and code."""
    exc_type, exc_obj, tb = sys.exc_info()

    # walk to the innermost frame (where the error actually happened)
    while tb and tb.tb_next:
        tb = tb.tb_next

    f = tb.tb_frame
    lineno = tb.tb_lineno
    filename = f.f_code.co_filename

    linecache.checkcache(filename)
    line = linecache.getline(filename, lineno, f.f_globals)
    print('EXCEPTION IN (LINE {} "{}"): {}'.format(lineno, line.strip(), exc_obj))


restarted = "no"
short_started = "no"
long_started = "no"
minute = 0
last_minute = 0

# -----------------------------
# GUI SETTINGS (coins list)
# -----------------------------
_GUI_SETTINGS_PATH = os.environ.get("POWERTRADER_GUI_SETTINGS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "gui_settings.json"
)

_gui_settings_cache = {
    "mtime": None,
    "coins": ["BTC", "ETH", "XRP", "BNB", "DOGE"],  # fallback defaults
}


def _load_gui_coins() -> list:
    """
    Reads gui_settings.json and returns settings["coins"] as an uppercased list.
    Caches by mtime so it is cheap to call frequently.
    """
    try:
        if not os.path.isfile(_GUI_SETTINGS_PATH):
            return list(_gui_settings_cache["coins"])

        mtime = os.path.getmtime(_GUI_SETTINGS_PATH)
        if _gui_settings_cache["mtime"] == mtime:
            return list(_gui_settings_cache["coins"])

        with open(_GUI_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}

        coins = data.get("coins", None)
        if not isinstance(coins, list) or not coins:
            coins = list(_gui_settings_cache["coins"])

        coins = [str(c).strip().upper() for c in coins if str(c).strip()]
        if not coins:
            coins = list(_gui_settings_cache["coins"])

        _gui_settings_cache["mtime"] = mtime
        _gui_settings_cache["coins"] = coins
        return list(coins)
    except Exception:
        return list(_gui_settings_cache["coins"])


# Initial coin list (will be kept live via _sync_coins_from_settings())
COIN_SYMBOLS = _load_gui_coins()
CURRENT_COINS = list(COIN_SYMBOLS)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def coin_folder(sym: str) -> str:
    """Return the folder path for a coin symbol. BTC uses base dir, others use subfolder."""
    sym = sym.upper()
    # Your "main folder is BTC folder" convention:
    return BASE_DIR if sym == "BTC" else os.path.join(BASE_DIR, sym)


# --- training freshness gate (mirrors pt_hub.py) ---
_TRAINING_STALE_SECONDS = 14 * 24 * 60 * 60  # 14 days


def _coin_is_trained(sym: str) -> bool:
    """
    Training freshness gate:

    pt_trainer.py writes `trainer_last_training_time.txt` in the coin folder
    when training starts. If that file is missing OR older than 14 days, we treat
    the coin as NOT TRAINED.

    This is intentionally the same logic as pt_hub.py so runner behavior matches
    what the GUI shows.
    """

    try:
        folder = coin_folder(sym)
        stamp_path = os.path.join(folder, "trainer_last_training_time.txt")
        if not os.path.isfile(stamp_path):
            return False
        with open(stamp_path, "r", encoding="utf-8") as f:
            raw = (f.read() or "").strip()
        ts = float(raw) if raw else 0.0
        if ts <= 0:
            return False
        return (time.time() - ts) <= _TRAINING_STALE_SECONDS
    except Exception:
        return False


# --- GUI HUB "runner ready" gate file (read by gui_hub.py Start All toggle) ---

HUB_DIR = os.environ.get("POWERTRADER_HUB_DIR") or os.path.join(BASE_DIR, "hub_data")
try:
    os.makedirs(HUB_DIR, exist_ok=True)
except Exception:
    pass

RUNNER_READY_PATH = os.path.join(HUB_DIR, "runner_ready.json")


def _atomic_write_json(path: str, data: dict) -> None:
    """Atomically write JSON data to file using a temporary file."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def _write_runner_ready(
    ready: bool, stage: str, ready_coins=None, total_coins: int = 0
) -> None:
    """Write runner ready status to JSON file."""
    obj = {
        "timestamp": time.time(),
        "ready": bool(ready),
        "stage": stage,
        "ready_coins": ready_coins or [],
        "total_coins": int(total_coins or 0),
    }
    _atomic_write_json(RUNNER_READY_PATH, obj)


# Ensure folders exist for the current configured coins
for _sym in CURRENT_COINS:
    os.makedirs(coin_folder(_sym), exist_ok=True)

distance = 0.5
tf_choices = ["1hour", "2hour", "4hour", "8hour", "12hour", "1day", "1week"]


def new_coin_state():
    """Return a new default state dictionary for a coin."""
    return {
        "low_bound_prices": [0.01] * len(tf_choices),
        "high_bound_prices": [99999999999999999] * len(tf_choices),
        "tf_times": [],
        "tf_choice_index": 0,
        "tf_update": ["yes"] * len(tf_choices),
        "messages": ["none"] * len(tf_choices),
        "last_messages": ["none"] * len(tf_choices),
        "margins": [0.25] * len(tf_choices),
        "high_tf_prices": [99999999999999999] * len(tf_choices),
        "low_tf_prices": [0.01] * len(tf_choices),
        "tf_sides": ["none"] * len(tf_choices),
        "messaged": ["no"] * len(tf_choices),
        "updated": [0] * len(tf_choices),
        "perfects": ["active"] * len(tf_choices),
        "training_issues": [0] * len(tf_choices),
        # readiness gating (no placeholder-number checks; this is process-based)
        "bounds_version": 0,
        "last_display_bounds_version": -1,
    }


states = {}

display_cache = {sym: f"{sym}  (starting.)" for sym in CURRENT_COINS}

# Track which coins have produced REAL predicted levels (not placeholder 1 / 99999999999999999)
_ready_coins = set()


# We consider the runner "READY" only once it is ACTUALLY PRINTING real prediction messages
# (i.e. output lines start with WITHIN / LONG / SHORT). No numeric placeholder checks at all.
def _is_printing_real_predictions(messages) -> bool:
    """Check if messages indicate real predictions are being printed."""
    try:
        for m in messages or []:
            if not isinstance(m, str):
                continue
            # These are the only message types produced once predictions are being used in output.
            # (INACTIVE means it's still not printing real prediction output for that timeframe.)
            if m.startswith("WITHIN") or m.startswith("LONG") or m.startswith("SHORT"):
                return True
        return False
    except Exception:
        return False


def _sync_coins_from_settings():
    """
    Hot-reload coins from gui_settings.json while runner is running.

    - Adds new coins: creates folder + init_coin() + starts stepping them
    - Removes coins: stops stepping them (leaves state on disk untouched)
    """
    global CURRENT_COINS

    new_list = _load_gui_coins()
    if new_list == CURRENT_COINS:
        return

    old_list = list(CURRENT_COINS)
    added = [c for c in new_list if c not in old_list]
    removed = [c for c in old_list if c not in new_list]

    # Handle removed coins: stop stepping + clear UI cache entries
    for sym in removed:
        try:
            _ready_coins.discard(sym)
        except Exception:
            pass
        try:
            display_cache.pop(sym, None)
        except Exception:
            pass

    # Handle added coins: create folder + init state + show in UI output
    for sym in added:
        try:
            os.makedirs(coin_folder(sym), exist_ok=True)
        except Exception:
            pass
        try:
            display_cache[sym] = f"{sym}  (starting.)"
        except Exception:
            pass
        try:
            # init_coin switches CWD and does network calls, so do it carefully
            init_coin(sym)
            os.chdir(BASE_DIR)
        except Exception:
            try:
                os.chdir(BASE_DIR)
            except Exception:
                pass

    CURRENT_COINS = list(new_list)


_write_runner_ready(
    False, stage="starting", ready_coins=[], total_coins=len(CURRENT_COINS)
)


def init_coin(sym: str):
    """Initialize a coin's state and files."""
    # switch into the coin's folder so ALL existing relative file I/O stays working
    os.chdir(coin_folder(sym))

    # per-coin "version" + on/off files (no collisions between coins)
    with open("alerts_version.txt", "w+") as f:
        f.write("5/3/2022/9am")

    with open("futures_long_onoff.txt", "w+") as f:
        f.write("OFF")

    with open("futures_short_onoff.txt", "w+") as f:
        f.write("OFF")

    st = new_coin_state()

    coin = sym + "-USDT"
    ind = 0
    tf_times_local = []
    while True:
        history_list = []
        while True:
            try:
                history = (
                    str(market.get_kline(coin, tf_choices[ind]))
                    .replace("]]", "], ")
                    .replace("[[", "[")
                )
                break
            except Exception as e:
                time.sleep(3.5)
                if "Requests" in str(e):
                    pass
                else:
                    PrintException()
                continue

        history_list = history.split("], [")
        ind += 1
        try:
            working_minute = (
                str(history_list[1]).replace('"', "").replace("'", "").split(", ")
            )
            the_time = working_minute[0].replace("[", "")
        except Exception:
            the_time = 0.0

        tf_times_local.append(the_time)
        if len(tf_times_local) >= len(tf_choices):
            break

    st["tf_times"] = tf_times_local
    states[sym] = st


# init all coins once (from GUI settings)
for _sym in CURRENT_COINS:
    init_coin(_sym)

# restore CWD to base after init
os.chdir(BASE_DIR)

wallet_addr_list = []
wallet_addr_users = []
total_long = 0
total_short = 0
last_hour = 565457457357

cc_index = 0
tf_choice = []
prices = []
starts = []
long_start_prices = []
short_start_prices = []
buy_coins = []
cc_update = "yes"
wr_update = "yes"


def find_purple_area(lines):
    """
    Given a list of (price, color) pairs (color is 'orange' or 'blue'),
    returns (purple_bottom, purple_top) if a purple area exists,
    else (None, None).
    """
    oranges = sorted(
        [price for price, color in lines if color == "orange"], reverse=True
    )
    blues = sorted([price for price, color in lines if color == "blue"])
    if not oranges or not blues:
        return (None, None)
    purple_bottom = None
    purple_top = None
    all_levels = sorted(
        set(oranges + blues + [float("-inf"), float("inf")]), reverse=True
    )
    for i in range(len(all_levels) - 1):
        top = all_levels[i]
        bottom = all_levels[i + 1]
        oranges_below = [o for o in oranges if o < bottom]
        blues_above = [b for b in blues if b > top]
        has_orange_below = any(o < top for o in oranges)
        has_blue_above = any(b > bottom for b in blues)
        if has_orange_below and has_blue_above:
            if purple_bottom is None or bottom < purple_bottom:
                purple_bottom = bottom
            if purple_top is None or top > purple_top:
                purple_top = top
    if (
        purple_bottom is not None
        and purple_top is not None
        and purple_top > purple_bottom
    ):
        return (purple_bottom, purple_top)
    return (None, None)


def step_coin(sym: str):
    """Process one step for a coin, updating state and predictions."""
    # run inside the coin folder so all existing file reads/writes stay relative + isolated
    os.chdir(coin_folder(sym))
    coin = sym + "-USDT"
    st = states[sym]

    # --- training freshness gate ---
    # If GUI would show NOT TRAINED (missing / stale trainer_last_training_time.txt),
    # skip this coin so no new trades can start until it is trained again.
    if not _coin_is_trained(sym):
        try:
            # Prevent new trades (and DCA) by forcing signals to 0 and keeping PM at baseline.
            with open("futures_long_profit_margin.txt", "w+") as f:
                f.write("0.25")
            with open("futures_short_profit_margin.txt", "w+") as f:
                f.write("0.25")
            with open("long_dca_signal.txt", "w+") as f:
                f.write("0")
            with open("short_dca_signal.txt", "w+") as f:
                f.write("0")
        except Exception:
            pass
        try:
            display_cache[sym] = sym + "  (NOT TRAINED / OUTDATED - run trainer)"
        except Exception:
            pass
        try:
            _ready_coins.discard(sym)
            all_ready = len(_ready_coins) >= len(CURRENT_COINS)
            _write_runner_ready(
                all_ready,
                stage=("real_predictions" if all_ready else "training_required"),
                ready_coins=sorted(list(_ready_coins)),
                total_coins=len(CURRENT_COINS),
            )

        except Exception:
            pass
        return

    # ensure new readiness-version keys exist even if restarting from an older state dict
    if "bounds_version" not in st:
        st["bounds_version"] = 0
    if "last_display_bounds_version" not in st:
        st["last_display_bounds_version"] = -1

    # pull state into local names (lists mutate in-place; ones that get reassigned we set back at end)
    low_bound_prices = st["low_bound_prices"]
    high_bound_prices = st["high_bound_prices"]
    tf_times = st["tf_times"]
    tf_choice_index = st["tf_choice_index"]

    tf_update = st["tf_update"]
    messages = st["messages"]
    last_messages = st["last_messages"]
    margins = st["margins"]

    high_tf_prices = st["high_tf_prices"]
    low_tf_prices = st["low_tf_prices"]
    tf_sides = st["tf_sides"]
    messaged = st["messaged"]
    updated = st["updated"]
    perfects = st["perfects"]
    training_issues = st.get("training_issues", [0] * len(tf_choices))
    # keep training_issues aligned to tf_choices
    if len(training_issues) < len(tf_choices):
        training_issues.extend([0] * (len(tf_choices) - len(training_issues)))
    elif len(training_issues) > len(tf_choices):
        del training_issues[len(tf_choices) :]

    last_difference_between = 0.0

    # ====== ORIGINAL: fetch current candle for this timeframe index ======
    while True:
        history_list = []
        while True:
            try:
                history = (
                    str(market.get_kline(coin, tf_choices[tf_choice_index]))
                    .replace("]]", "], ")
                    .replace("[[", "[")
                )
                break
            except Exception as e:
                time.sleep(3.5)
                if "Requests" in str(e):
                    pass
                else:
                    pass
                continue
        history_list = history.split("], [")
        # KuCoin can occasionally return an empty/short kline response.
        # Guard against history_list[1] raising IndexError.
        if len(history_list) < 2:
            time.sleep(0.2)
            continue
        working_minute = (
            str(history_list[1]).replace('"', "").replace("'", "").split(", ")
        )
        try:
            openPrice = float(working_minute[1])
            closePrice = float(working_minute[2])
            break
        except Exception:
            continue

    current_candle = 100 * ((closePrice - openPrice) / openPrice)

    # ====== ORIGINAL: load threshold + memories/weights and compute moves ======
    file = open("neural_perfect_threshold_" + tf_choices[tf_choice_index] + ".txt", "r")
    perfect_threshold = float(file.read())
    file.close()

    try:
        # If we can read/parse training files, this timeframe is NOT a training-file issue.
        training_issues[tf_choice_index] = 0

        file = open("memories_" + tf_choices[tf_choice_index] + ".txt", "r")
        memory_list = (
            file.read()
            .replace("'", "")
            .replace(",", "")
            .replace('"', "")
            .replace("]", "")
            .replace("[", "")
            .split("~")
        )
        file.close()

        file = open("memory_weights_" + tf_choices[tf_choice_index] + ".txt", "r")
        weight_list = (
            file.read()
            .replace("'", "")
            .replace(",", "")
            .replace('"', "")
            .replace("]", "")
            .replace("[", "")
            .split(" ")
        )
        file.close()

        file = open("memory_weights_high_" + tf_choices[tf_choice_index] + ".txt", "r")
        high_weight_list = (
            file.read()
            .replace("'", "")
            .replace(",", "")
            .replace('"', "")
            .replace("]", "")
            .replace("[", "")
            .split(" ")
        )
        file.close()

        file = open("memory_weights_low_" + tf_choices[tf_choice_index] + ".txt", "r")
        low_weight_list = (
            file.read()
            .replace("'", "")
            .replace(",", "")
            .replace('"', "")
            .replace("]", "")
            .replace("[", "")
            .split(" ")
        )
        file.close()

        mem_ind = 0
        diffs_list = []
        any_perfect = "no"
        perfect_dexs = []
        perfect_diffs = []
        moves = []
        move_weights = []
        unweighted = []
        high_unweighted = []
        low_unweighted = []
        high_moves = []
        low_moves = []

        while True:
            memory_pattern = (
                memory_list[mem_ind]
                .split("{}")[0]
                .replace("'", "")
                .replace(",", "")
                .replace('"', "")
                .replace("]", "")
                .replace("[", "")
                .split(" ")
            )
            check_dex = 0
            memory_candle = float(memory_pattern[check_dex])

            if current_candle == 0.0 and memory_candle == 0.0:
                difference = 0.0
            else:
                try:
                    difference = abs(
                        (
                            abs(current_candle - memory_candle)
                            / ((current_candle + memory_candle) / 2)
                        )
                        * 100
                    )
                except:
                    difference = 0.0

            diff_avg = difference

            if diff_avg <= perfect_threshold:
                any_perfect = "yes"
                high_diff = (
                    float(
                        memory_list[mem_ind]
                        .split("{}")[1]
                        .replace("'", "")
                        .replace(",", "")
                        .replace('"', "")
                        .replace("]", "")
                        .replace("[", "")
                        .replace(" ", "")
                    )
                    / 100
                )
                low_diff = (
                    float(
                        memory_list[mem_ind]
                        .split("{}")[2]
                        .replace("'", "")
                        .replace(",", "")
                        .replace('"', "")
                        .replace("]", "")
                        .replace("[", "")
                        .replace(" ", "")
                    )
                    / 100
                )

                unweighted.append(float(memory_pattern[len(memory_pattern) - 1]))
                move_weights.append(float(weight_list[mem_ind]))
                high_unweighted.append(high_diff)
                low_unweighted.append(low_diff)

                if float(weight_list[mem_ind]) != 0.0:
                    moves.append(
                        float(memory_pattern[len(memory_pattern) - 1])
                        * float(weight_list[mem_ind])
                    )

                if float(high_weight_list[mem_ind]) != 0.0:
                    high_moves.append(high_diff * float(high_weight_list[mem_ind]))

                if float(low_weight_list[mem_ind]) != 0.0:
                    low_moves.append(low_diff * float(low_weight_list[mem_ind]))

                perfect_dexs.append(mem_ind)
                perfect_diffs.append(diff_avg)

            diffs_list.append(diff_avg)
            mem_ind += 1

            if mem_ind >= len(memory_list):
                if any_perfect == "no":
                    final_moves = 0.0
                    high_final_moves = 0.0
                    low_final_moves = 0.0
                    del perfects[tf_choice_index]
                    perfects.insert(tf_choice_index, "inactive")
                else:
                    try:
                        final_moves = sum(moves) / len(moves)
                        high_final_moves = sum(high_moves) / len(high_moves)
                        low_final_moves = sum(low_moves) / len(low_moves)
                        del perfects[tf_choice_index]
                        perfects.insert(tf_choice_index, "active")
                    except:
                        final_moves = 0.0
                        high_final_moves = 0.0
                        low_final_moves = 0.0
                        del perfects[tf_choice_index]
                        perfects.insert(tf_choice_index, "inactive")
                break

    except Exception:
        PrintException()
        training_issues[tf_choice_index] = 1
        final_moves = 0.0
        high_final_moves = 0.0
        low_final_moves = 0.0
        del perfects[tf_choice_index]
        perfects.insert(tf_choice_index, "inactive")

    # keep threshold persisted (original behavior)
    file = open(
        "neural_perfect_threshold_" + tf_choices[tf_choice_index] + ".txt", "w+"
    )
    file.write(str(perfect_threshold))
    file.close()

    # ====== ORIGINAL: compute new high/low predictions ======
    price_list2 = [openPrice, closePrice]
    current_pattern = [price_list2[0], price_list2[1]]

    try:
        c_diff = final_moves / 100
        high_diff = high_final_moves
        low_diff = low_final_moves

        start_price = current_pattern[len(current_pattern) - 1]
        high_new_price = start_price + (start_price * high_diff)
        low_new_price = start_price + (start_price * low_diff)
    except:
        start_price = current_pattern[len(current_pattern) - 1]
        high_new_price = start_price
        low_new_price = start_price

    if perfects[tf_choice_index] == "inactive":
        del high_tf_prices[tf_choice_index]
        high_tf_prices.insert(tf_choice_index, start_price)
        del low_tf_prices[tf_choice_index]
        low_tf_prices.insert(tf_choice_index, start_price)
    else:
        del high_tf_prices[tf_choice_index]
        high_tf_prices.insert(tf_choice_index, high_new_price)
        del low_tf_prices[tf_choice_index]
        low_tf_prices.insert(tf_choice_index, low_new_price)

    # ====== advance tf index; if full sweep complete, compute signals ======
    tf_choice_index += 1

    if tf_choice_index >= len(tf_choices):
        tf_choice_index = 0

        # reset tf_update for this coin (but DO NOT block-wait; just detect updates and return)
        tf_update = ["no"] * len(tf_choices)

        # get current price ONCE per coin — use Robinhood's current ASK (same as rhcb trader buy price)
        rh_symbol = f"{sym}-USD"
        while True:
            try:
                current = robinhood_current_ask(rh_symbol)
                break
            except Exception:
                time.sleep(1)
                continue

        # IMPORTANT: messages printed below use the bounds currently in state.
        # We only allow "ready" once messages are generated using a non-startup bounds_version.
        bounds_version_used_for_messages = st.get("bounds_version", 0)

        # --- HARD GUARANTEE: all TF arrays stay length==len(tf_choices) (fallback placeholders) ---
        def _pad_to_len(lst, n, fill):
            if lst is None:
                lst = []
            if len(lst) < n:
                lst.extend([fill] * (n - len(lst)))
            elif len(lst) > n:
                del lst[n:]
            return lst

        n_tfs = len(tf_choices)

        # bounds: use your fake numbers when TF inactive / missing
        low_bound_prices = _pad_to_len(low_bound_prices, n_tfs, 0.01)
        high_bound_prices = _pad_to_len(high_bound_prices, n_tfs, 99999999999999999)

        # predicted prices: keep equal when missing so it never triggers LONG/SHORT
        high_tf_prices = _pad_to_len(high_tf_prices, n_tfs, current)
        low_tf_prices = _pad_to_len(low_tf_prices, n_tfs, current)

        # status arrays
        perfects = _pad_to_len(perfects, n_tfs, "inactive")
        training_issues = _pad_to_len(training_issues, n_tfs, 0)
        messages = _pad_to_len(messages, n_tfs, "none")

        tf_sides = _pad_to_len(tf_sides, n_tfs, "none")
        messaged = _pad_to_len(messaged, n_tfs, "no")
        margins = _pad_to_len(margins, n_tfs, 0.0)
        updated = _pad_to_len(updated, n_tfs, 0)

        # per-timeframe message logic (same decisions as before)
        inder = 0
        while inder < len(tf_choices):
            # update the_time snapshot (same as before)
            while True:

                try:
                    history = (
                        str(market.get_kline(coin, tf_choices[inder]))
                        .replace("]]", "], ")
                        .replace("[[", "[")
                    )
                    break
                except Exception as e:
                    time.sleep(3.5)
                    if "Requests" in str(e):
                        pass
                    else:
                        PrintException()
                    continue

            history_list = history.split("], [")
            try:
                working_minute = (
                    str(history_list[1]).replace('"', "").replace("'", "").split(", ")
                )
                the_time = working_minute[0].replace("[", "")
            except Exception:
                the_time = 0.0

            # (original comparisons)
            if (
                current > high_bound_prices[inder]
                and high_tf_prices[inder] != low_tf_prices[inder]
            ):
                message = (
                    "SHORT on "
                    + tf_choices[inder]
                    + " timeframe. "
                    + format(
                        ((high_bound_prices[inder] - current) / abs(current)) * 100,
                        ".8f",
                    )
                    + " High Boundary: "
                    + str(high_bound_prices[inder])
                )
                if messaged[inder] != "yes":
                    del messaged[inder]
                    messaged.insert(inder, "yes")
                del margins[inder]
                margins.insert(
                    inder, ((high_tf_prices[inder] - current) / abs(current)) * 100
                )

                if "SHORT" in messages[inder]:
                    del messages[inder]
                    messages.insert(inder, message)
                    del updated[inder]
                    updated.insert(inder, 0)
                else:
                    del messages[inder]
                    messages.insert(inder, message)
                    del updated[inder]
                    updated.insert(inder, 1)

                del tf_sides[inder]
                tf_sides.insert(inder, "short")

            elif (
                current < low_bound_prices[inder]
                and high_tf_prices[inder] != low_tf_prices[inder]
            ):
                message = (
                    "LONG on "
                    + tf_choices[inder]
                    + " timeframe. "
                    + format(
                        ((low_bound_prices[inder] - current) / abs(current)) * 100,
                        ".8f",
                    )
                    + " Low Boundary: "
                    + str(low_bound_prices[inder])
                )
                if messaged[inder] != "yes":
                    del messaged[inder]
                    messaged.insert(inder, "yes")

                del margins[inder]
                margins.insert(
                    inder, ((low_tf_prices[inder] - current) / abs(current)) * 100
                )

                del tf_sides[inder]
                tf_sides.insert(inder, "long")

                if "LONG" in messages[inder]:
                    del messages[inder]
                    messages.insert(inder, message)
                    del updated[inder]
                    updated.insert(inder, 0)
                else:
                    del messages[inder]
                    messages.insert(inder, message)
                    del updated[inder]
                    updated.insert(inder, 1)

            else:
                if perfects[inder] == "inactive":
                    if training_issues[inder] == 1:
                        message = (
                            "INACTIVE (training data issue) on "
                            + tf_choices[inder]
                            + " timeframe."
                            + " Low Boundary: "
                            + str(low_bound_prices[inder])
                            + " High Boundary: "
                            + str(high_bound_prices[inder])
                        )
                    else:
                        message = (
                            "INACTIVE on "
                            + tf_choices[inder]
                            + " timeframe."
                            + " Low Boundary: "
                            + str(low_bound_prices[inder])
                            + " High Boundary: "
                            + str(high_bound_prices[inder])
                        )
                else:
                    message = (
                        "WITHIN on "
                        + tf_choices[inder]
                        + " timeframe."
                        + " Low Boundary: "
                        + str(low_bound_prices[inder])
                        + " High Boundary: "
                        + str(high_bound_prices[inder])
                    )

                del margins[inder]
                margins.insert(inder, 0.0)

                if message == messages[inder]:
                    del messages[inder]
                    messages.insert(inder, message)
                    del updated[inder]
                    updated.insert(inder, 0)
                else:
                    del messages[inder]
                    messages.insert(inder, message)
                    del updated[inder]
                    updated.insert(inder, 1)

                del tf_sides[inder]
                tf_sides.insert(inder, "none")

                del messaged[inder]
                messaged.insert(inder, "no")

            inder += 1

        # rebuild bounds (same math as before)
        prices_index = 0
        low_bound_prices = []
        high_bound_prices = []
        while True:
            new_low_price = low_tf_prices[prices_index] - (
                low_tf_prices[prices_index] * (distance / 100)
            )
            new_high_price = high_tf_prices[prices_index] + (
                high_tf_prices[prices_index] * (distance / 100)
            )
            if perfects[prices_index] != "inactive":
                low_bound_prices.append(new_low_price)
                high_bound_prices.append(new_high_price)
            else:
                low_bound_prices.append(0.01)
                high_bound_prices.append(99999999999999999)

            prices_index += 1
            if prices_index >= len(high_tf_prices):
                break

        new_low_bound_prices = sorted(low_bound_prices)
        new_low_bound_prices.reverse()
        new_high_bound_prices = sorted(high_bound_prices)

        og_index = 0
        og_low_index_list = []
        og_high_index_list = []
        while True:
            og_low_index_list.append(
                low_bound_prices.index(new_low_bound_prices[og_index])
            )
            og_high_index_list.append(
                high_bound_prices.index(new_high_bound_prices[og_index])
            )
            og_index += 1
            if og_index >= len(low_bound_prices):
                break

        og_index = 0
        gap_modifier = 0.0
        while True:
            if (
                new_low_bound_prices[og_index] == 0.01
                or new_low_bound_prices[og_index + 1] == 0.01
                or new_high_bound_prices[og_index] == 99999999999999999
                or new_high_bound_prices[og_index + 1] == 99999999999999999
            ):
                pass
            else:
                try:
                    low_perc_diff = (
                        abs(
                            new_low_bound_prices[og_index]
                            - new_low_bound_prices[og_index + 1]
                        )
                        / (
                            (
                                new_low_bound_prices[og_index]
                                + new_low_bound_prices[og_index + 1]
                            )
                            / 2
                        )
                    ) * 100
                except:
                    low_perc_diff = 0.0
                try:
                    high_perc_diff = (
                        abs(
                            new_high_bound_prices[og_index]
                            - new_high_bound_prices[og_index + 1]
                        )
                        / (
                            (
                                new_high_bound_prices[og_index]
                                + new_high_bound_prices[og_index + 1]
                            )
                            / 2
                        )
                    ) * 100
                except:
                    high_perc_diff = 0.0

                if (
                    low_perc_diff < 0.25 + gap_modifier
                    or new_low_bound_prices[og_index + 1]
                    > new_low_bound_prices[og_index]
                ):
                    new_price = new_low_bound_prices[og_index + 1] - (
                        new_low_bound_prices[og_index + 1] * 0.0005
                    )
                    del new_low_bound_prices[og_index + 1]
                    new_low_bound_prices.insert(og_index + 1, new_price)
                    continue

                if (
                    high_perc_diff < 0.25 + gap_modifier
                    or new_high_bound_prices[og_index + 1]
                    < new_high_bound_prices[og_index]
                ):
                    new_price = new_high_bound_prices[og_index + 1] + (
                        new_high_bound_prices[og_index + 1] * 0.0005
                    )
                    del new_high_bound_prices[og_index + 1]
                    new_high_bound_prices.insert(og_index + 1, new_price)
                    continue

            og_index += 1
            gap_modifier += 0.25
            if og_index >= len(new_low_bound_prices) - 1:
                break

        og_index = 0
        low_bound_prices = []
        high_bound_prices = []
        while True:
            try:
                low_bound_prices.append(
                    new_low_bound_prices[og_low_index_list.index(og_index)]
                )
            except:
                pass
            try:
                high_bound_prices.append(
                    new_high_bound_prices[og_high_index_list.index(og_index)]
                )
            except:
                pass
            og_index += 1
            if og_index >= len(new_low_bound_prices):
                break

        # bump bounds_version now that we've computed a new set of prediction bounds
        st["bounds_version"] = bounds_version_used_for_messages + 1

        with open("low_bound_prices.html", "w+") as file:
            file.write(
                str(new_low_bound_prices)
                .replace("', '", " ")
                .replace("[", "")
                .replace("]", "")
                .replace("'", "")
            )
        with open("high_bound_prices.html", "w+") as file:
            file.write(
                str(new_high_bound_prices)
                .replace("', '", " ")
                .replace("[", "")
                .replace("]", "")
                .replace("'", "")
            )

        # cache display text for this coin (main loop prints everything on one screen)
        try:
            display_cache[sym] = (
                sym + "  " + str(current) + "\n\n" + str(messages).replace("', '", "\n")
            )

            # The GUI-visible messages were generated using the bounds_version that was in state at the
            # start of this full-sweep (before we rebuilt bounds above).
            st["last_display_bounds_version"] = bounds_version_used_for_messages

            # Only consider this coin "ready" once we've already rebuilt bounds at least once
            # AND we're now printing messages generated from those rebuilt bounds.
            if (
                st["last_display_bounds_version"] >= 1
            ) and _is_printing_real_predictions(messages):
                _ready_coins.add(sym)
            else:
                _ready_coins.discard(sym)

            all_ready = len(_ready_coins) >= len(COIN_SYMBOLS)
            _write_runner_ready(
                all_ready,
                stage=("real_predictions" if all_ready else "warming_up"),
                ready_coins=sorted(list(_ready_coins)),
                total_coins=len(COIN_SYMBOLS),
            )

        except:
            PrintException()

        # write PM + DCA signals (same as before)
        try:
            longs = tf_sides.count("long")
            shorts = tf_sides.count("short")

            # long pm
            current_pms = [m for m in margins if m != 0]
            try:
                pm = sum(current_pms) / len(current_pms)
                if pm < 0.25:
                    pm = 0.25
            except:
                pm = 0.25

            with open("futures_long_profit_margin.txt", "w+") as f:
                f.write(str(pm))
            with open("long_dca_signal.txt", "w+") as f:
                f.write(str(longs))

            # short pm
            current_pms = [m for m in margins if m != 0]
            try:
                pm = sum(current_pms) / len(current_pms)
                if pm < 0.25:
                    pm = 0.25
            except:
                pm = 0.25

            with open("futures_short_profit_margin.txt", "w+") as f:
                f.write(str(abs(pm)))
            with open("short_dca_signal.txt", "w+") as f:
                f.write(str(shorts))

        except:
            PrintException()

        # ====== NON-BLOCKING candle update check (single pass) ======
        this_index_now = 0
        while this_index_now < len(tf_update):
            while True:
                try:
                    history = (
                        str(market.get_kline(coin, tf_choices[this_index_now]))
                        .replace("]]", "], ")
                        .replace("[[", "[")
                    )
                    break
                except Exception as e:
                    time.sleep(3.5)
                    if "Requests" in str(e):
                        pass
                    else:
                        PrintException()
                    continue

            history_list = history.split("], [")
            try:
                working_minute = (
                    str(history_list[1]).replace('"', "").replace("'", "").split(", ")
                )
                the_time = working_minute[0].replace("[", "")
            except Exception:
                the_time = 0.0

            if the_time != tf_times[this_index_now]:
                del tf_update[this_index_now]
                tf_update.insert(this_index_now, "yes")
                del tf_times[this_index_now]
                tf_times.insert(this_index_now, the_time)

            this_index_now += 1

    # ====== save state back ======
    st["low_bound_prices"] = low_bound_prices
    st["high_bound_prices"] = high_bound_prices
    st["tf_times"] = tf_times
    st["tf_choice_index"] = tf_choice_index

    # persist readiness gating fields
    st["bounds_version"] = st.get("bounds_version", 0)
    st["last_display_bounds_version"] = st.get("last_display_bounds_version", -1)

    st["tf_update"] = tf_update
    st["messages"] = messages
    st["last_messages"] = last_messages
    st["margins"] = margins

    st["high_tf_prices"] = high_tf_prices
    st["low_tf_prices"] = low_tf_prices
    st["tf_sides"] = tf_sides
    st["messaged"] = messaged
    st["updated"] = updated
    st["perfects"] = perfects
    st["training_issues"] = training_issues

    states[sym] = st


try:
    while True:
        # Hot-reload coins from GUI settings while running
        _sync_coins_from_settings()

        for _sym in CURRENT_COINS:
            step_coin(_sym)

        # clear + re-print one combined screen (so you don't see old output above new)
        os.system("cls" if os.name == "nt" else "clear")

        for _sym in CURRENT_COINS:
            print(display_cache.get(_sym, _sym + "  (no data yet)"))
            print("\n" + ("-" * 60) + "\n")

        # small sleep so you don't peg CPU when running many coins
        time.sleep(0.15)

except Exception:
    PrintException()
