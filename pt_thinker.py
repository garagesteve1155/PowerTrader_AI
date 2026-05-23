"""
pt_thinker.py — Neural signal runner

FUNCTION
--------
Continuously cycles through every active coin, scoring each against its trained
neural memories across 11 timeframes (1min … 1week).  The score for a given
timeframe is the average normalised "diff" between the memory bank and the
current candle sequence.  When that diff falls at or below the adaptive
threshold (neural_perfect_threshold_<tf>.txt) the timeframe votes "long";
above it votes "short".  After a full sweep of all timeframes the votes are
summed into per-coin signals that the trader acts on.

ALGORITHM (per coin, per timeframe)
------------------------------------
1. Fetch the most recent candles from KuCoin live API.
2. Load the memory bank (memories_<tf>.json) and per-memory weights
   (weights_<tf>.json) written by pt_trainer.py.
3. Compute the weighted mean absolute difference between the memory
   sequences and the latest candles.
4. Compare diff_avg to the persisted threshold; classify as long/short.
5. After all timeframes complete, aggregate long/short counts and compute
   the average profit-margin target across non-zero margins.
6. Write output files into the coin's working directory (state/coins/<SYM>/).

COMMUNICATION LINKS
--------------------
Reads (inputs):
  pt_config.json              — coin list, LTH coins, thinker_dir
  state/coins/<SYM>/
    memories_<tf>.json           — memory bank (written by pt_trainer.py)
    weights_<tf>.json            — per-memory weights (written by pt_trainer.py)
    neural_perfect_threshold_<tf>.txt  — adaptive decision threshold (r/w)
    trainer_last_training_time.txt     — freshness gate (written by pt_trainer.py)

Writes (outputs consumed by pt_trader.py):
  state/coins/<SYM>/
    long_dca_signal.txt          — integer: number of long-voting timeframes
    short_dca_signal.txt         — integer: number of short-voting timeframes
    futures_long_profit_margin.txt   — float: mean profit-margin for longs
    futures_short_profit_margin.txt  — float: mean profit-margin for shorts
    low_bound_prices.html        — space-separated predicted buy price levels
    high_bound_prices.html       — space-separated predicted sell price levels

  state/hub_data/
    thinker_ready.json           — readiness status polled by pt_web.py / UI
    lth_daily_ema200.json        — 200-day EMA snapshots for LTH coins (read
                                   by pt_trader.py for LTH buy gating)
"""

import json
import os
import sys
import time

import pt_errors
from kucoin.client import Market
from pt_env import TRAIN_TF_NAMES as tf_choices
from pt_env import PTEnv, utcnow, utc_to_ts, ts_to_utc
from pt_log import get_logger

market = Market(url="https://api.kucoin.com")
log = get_logger("thinker")

# ---------------------------------------------------------------------------
# Kline cache — eliminates redundant API calls within a 30-second window.
# Phase-1 fetches each (coin, tf) once; phase-2 message loop and candle-check
# reuse the same result instead of making duplicate network calls.
# ---------------------------------------------------------------------------
_kline_cache: dict = {}
_startup_start: float = time.time()
_STARTUP_LOG_WINDOW_S: float = 300.0  # log kline calls for first 5 minutes


def _get_kline(coin: str, tf: str) -> str:
    key = (coin, tf)
    cached = _kline_cache.get(key)
    if cached and time.time() - cached[0] < 30:
        return cached[1]
    result = str(market.get_kline(coin, tf))
    _kline_cache[key] = (time.time(), result)
    if time.time() - _startup_start < _STARTUP_LOG_WINDOW_S:
        log.debug(f"kline fetch: {coin} {tf}")
    return result


def kucoin_current_price(symbol: str) -> float:
    """
    Returns current price from KuCoin for symbols like 'BTC-USD' or 'BTC_USD'.
    Uses the close of the most recent 1-minute candle.
    """
    sym = (symbol or "").strip().upper()
    base = sym.replace("-", "_").split("_")[0]
    kucoin_sym = f"{base}-USDT"
    data = market.get_kline(kucoin_sym, "1min")
    if data and len(data) > 0 and len(data[0]) >= 3:
        return float(data[0][2])
    raise RuntimeError(f"KuCoin returned no data for {kucoin_sym}")


def restart_program():
    """Restarts the current program (no CLI args; uses hardcoded COIN_SYMBOLS)."""
    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
    except Exception as e:
        log.error(f"Error during program restart: {e}")


restarted = "no"
short_started = "no"
long_started = "no"
minute = 0
last_minute = 0

# -----------------------------
# Config (via PTEnv)
# -----------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_env = PTEnv(BASE_DIR)


def _load_gui_coins() -> list:
    """Return active coin list from config (mtime-cached via PTEnv)."""
    return [
        str(c).strip().upper() for c in _env.get_config()["coins"] if str(c).strip()
    ]


def _load_long_term_symbols_from_settings() -> list:
    """Return LTH symbols from config."""
    lth = _env.get_config()["long_term_holdings"]
    if isinstance(lth, str):
        lth = [x.strip() for x in lth.replace("\n", ",").split(",")]
    out, seen = [], set()
    for v in lth:
        s = str(v).upper().strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# Initial coin list (will be kept live via _sync_coins_from_settings())
COIN_SYMBOLS = _load_gui_coins()
CURRENT_COINS = list(COIN_SYMBOLS)

LTH_EMA200_PATH = str(_env.ema200_path())

_last_lth_ema_write_ts = 0.0


def _ema(values: list, period: int):
    try:
        vals = [float(x) for x in values if x is not None]
    except Exception:
        return None
    if len(vals) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    ema = sum(vals[:period]) / float(period)
    for v in vals[period:]:
        ema = (v * alpha) + (ema * (1.0 - alpha))
    return float(ema)


def _compute_daily_ema200(sym: str):
    """
    Return (ema200, price_used, pct_from_ema) for SYM.

    - EMA200 is computed from daily kline closes (Kucoin 1day data).
    - pct_from_ema is computed using CURRENT price when available (KuCoin),
      falling back to the most recent daily close if current price is unavailable.
    """
    sym_u = str(sym).upper().strip()
    coin = sym_u + "-USDT"

    # --- get daily closes for EMA200 ---
    try:
        history = market.get_kline(coin, "1day")
    except Exception:
        return None, None, None

    closes = []
    try:
        for row in history:
            if row and len(row) >= 3:
                closes.append(float(row[2]))
    except Exception:
        closes = []

    if not closes:
        return None, None, None

    closes_rev = list(reversed(closes))
    ema200 = _ema(closes_rev, 200)

    # --- choose price to compare against EMA200 ---
    last_close = float(closes_rev[-1]) if closes_rev else None
    price_used = last_close

    try:
        price_used = float(kucoin_current_price(f"{sym_u}_USD"))
    except Exception:
        price_used = last_close

    if ema200 is None or price_used is None or float(ema200) <= 0.0:
        return ema200, price_used, None

    pct = (float(price_used) - float(ema200)) / float(ema200) * 100.0
    return float(ema200), float(price_used), float(pct)


def _write_lth_ema200_snapshot() -> None:
    global _last_lth_ema_write_ts
    now = time.time()
    if (now - float(_last_lth_ema_write_ts)) < 5.0:
        return
    _last_lth_ema_write_ts = now
    syms = _load_long_term_symbols_from_settings()
    coins = {}
    for sym in syms:
        ema200, price, pct = _compute_daily_ema200(sym)
        if ema200 is None or price is None:
            continue
        coins[sym] = {"ema200": ema200, "price": price, "pct_from_ema200": pct}
    payload = {"ts": utcnow(), "coins": coins}
    try:
        with open(LTH_EMA200_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as e:
        pt_errors.emit(
            "thinker",
            level="warning",
            message=f"Failed to write LTH EMA200 snapshot: {e}",
            detail=(
                "Long-term holding buy gating (200-day EMA check) will use the previous "
                "snapshot until the next successful write. LTH buy decisions may be stale "
                "by up to one thinker cycle."
            ),
        )


def coin_folder(sym: str) -> str:
    return str(_env.coin_dir(sym.upper()))


def _strip_noise(s: str) -> str:
    """Strip list-serialisation artefacts from a memory token."""
    return (
        s.replace("'", "")
        .replace(",", "")
        .replace('"', "")
        .replace("]", "")
        .replace("[", "")
    )


def _mem_field_pct(line: str, idx: int) -> float:
    """Extract field `idx` from a `{}`-delimited memory line as a fraction (÷100)."""
    return float(_strip_noise(line.split("{}")[idx]).replace(" ", "")) / 100


def _coin_is_trained(sym: str) -> bool:
    try:
        folder = coin_folder(sym)
        stamp_path = os.path.join(folder, "trainer_last_training_time.txt")
        if not os.path.isfile(stamp_path):
            return False
        with open(stamp_path, "r", encoding="utf-8") as f:
            raw = (f.read() or "").strip()
        ts = (utc_to_ts(raw) if "T" in raw else float(raw)) if raw else 0.0
        if ts <= 0:
            return False
        stale_secs = _env.get_config()["training_staleness_days"] * 86400
        return (time.time() - ts) <= stale_secs
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Coin state persistence — save/restore across restarts to skip warmup cycles.
# State is saved after each full TF cycle (phase-2) and restored in init_coin.
# On restore, tf_times is zeroed so the first step fetches fresh candle data.
# ---------------------------------------------------------------------------


def _state_path(sym: str) -> str:
    return os.path.join(coin_folder(sym), "thinker_state.json")


def _save_coin_state(sym: str, st: dict) -> None:
    try:
        data = dict(st)
        data["saved_at"] = utcnow()
        path = _state_path(sym)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        pt_errors.emit(
            "thinker",
            level="warning",
            message=f"Failed to save thinker state for {sym}: {e}",
            detail=(
                "If the thinker restarts, this coin will start a fresh warmup cycle "
                "instead of restoring from the saved state. Expect 1-2 extra cycles "
                "before predictions resume for this coin."
            ),
        )


def _load_coin_state(sym: str) -> dict | None:
    try:
        path = _state_path(sym)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved = data.get("saved_at", 0)
        saved_ts = utc_to_ts(saved) if isinstance(saved, str) else float(saved or 0)
        if time.time() - saved_ts > 86400:
            return None
        if data.get("last_display_bounds_version", -1) < 1:
            return None
        data.pop("saved_at", None)
        # Reset tf_times to "0" so the first step detects new candles and fetches fresh data
        n = len(data.get("tf_times") or [])
        data["tf_times"] = ["0"] * (n or len(tf_choices))
        return data
    except Exception:
        return None


# --- Thinker ready gate file (read by pt_web.py / pt_controller to start traders) ---

THINKER_READY_PATH = str(_env.thinker_ready_path())


def _atomic_write_json(path: str, data: dict) -> None:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def _write_thinker_ready(
    ready: bool, stage: str, ready_coins=None, total_coins: int = 0
) -> None:
    obj = {
        "timestamp": utcnow(),
        "ready": bool(ready),
        "stage": stage,
        "ready_coins": ready_coins or [],
        "total_coins": int(total_coins or 0),
    }
    _atomic_write_json(THINKER_READY_PATH, obj)


# Ensure folders exist for the current configured coins
for _sym in CURRENT_COINS:
    os.makedirs(coin_folder(_sym), exist_ok=True)


distance = 0.5


def new_coin_state():
    return {
        "low_bound_prices": [0.0] * len(tf_choices),
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
    Hot-reload coins from pt_config.json while runner is running.

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


_write_thinker_ready(
    False, stage="starting", ready_coins=[], total_coins=len(CURRENT_COINS)
)
log.info(f"Starting — {len(CURRENT_COINS)} coins")


def init_coin(sym: str):
    # switch into the coin's folder so ALL existing relative file I/O stays working
    os.chdir(coin_folder(sym))

    # per-coin "version" + on/off files (no collisions between coins)
    with open("alerts_version.txt", "w+") as f:
        f.write("5/3/2022/9am")

    with open("futures_long_onoff.txt", "w+") as f:
        f.write("OFF")

    with open("futures_short_onoff.txt", "w+") as f:
        f.write("OFF")

    saved = _load_coin_state(sym)
    if saved is not None:
        states[sym] = saved
        log.info(
            f"Init {sym}: restored from saved state (bv={saved.get('last_display_bounds_version')})"
        )
        return

    st = new_coin_state()
    # Initialize tf_times to "0" so the first step_coin call detects a new candle
    # for every timeframe and fetches fresh data without requiring API calls here.
    st["tf_times"] = ["0"] * len(tf_choices)
    states[sym] = st
    log.info(f"Init {sym}: fresh state")


# init all coins once (from GUI settings)
for _sym in CURRENT_COINS:
    init_coin(_sym)

# restore CWD to base after init
os.chdir(BASE_DIR)
_restored = sum(
    1 for s in states.values() if s.get("last_display_bounds_version", -1) >= 1
)
log.info(
    f"Init complete — {_restored}/{len(CURRENT_COINS)} restored, {len(CURRENT_COINS) - _restored} fresh"
)

# Track which coins have had their state saved this session (for first-save logging).
_state_saved_coins: set = set()


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


def step_coin(sym: str):
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
            any_ready = len(_ready_coins) > 0
            _write_thinker_ready(
                any_ready,
                stage=("real_predictions" if any_ready else "training_required"),
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

    # ====== ORIGINAL: fetch current candle for this timeframe index ======
    while True:
        history_list = []
        while True:
            try:
                history = (
                    _get_kline(coin, tf_choices[tf_choice_index])
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
    try:
        file = open(
            "neural_perfect_threshold_" + tf_choices[tf_choice_index] + ".txt", "r"
        )
        perfect_threshold = float(file.read())
        file.close()
    except (FileNotFoundError, ValueError):
        perfect_threshold = 1.0

    try:
        # If we can read/parse training files, this timeframe is NOT a training-file issue.
        training_issues[tf_choice_index] = 0

        tf = tf_choices[tf_choice_index]
        with open("memories_" + tf + ".txt", "r") as f:
            memory_list = _strip_noise(f.read()).split("~")
        with open("memory_weights_" + tf + ".txt", "r") as f:
            weight_list = _strip_noise(f.read()).split(" ")
        with open("memory_weights_high_" + tf + ".txt", "r") as f:
            high_weight_list = _strip_noise(f.read()).split(" ")
        with open("memory_weights_low_" + tf + ".txt", "r") as f:
            low_weight_list = _strip_noise(f.read()).split(" ")

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
            memory_pattern = _strip_noise(memory_list[mem_ind].split("{}")[0]).split(
                " "
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
                except Exception:
                    difference = 0.0

            diff_avg = difference

            if diff_avg <= perfect_threshold:
                any_perfect = "yes"
                high_diff = _mem_field_pct(memory_list[mem_ind], 1)
                low_diff = _mem_field_pct(memory_list[mem_ind], 2)

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
                    # final_moves = 0.0
                    high_final_moves = 0.0
                    low_final_moves = 0.0
                    del perfects[tf_choice_index]
                    perfects.insert(tf_choice_index, "inactive")
                else:
                    try:
                        # final_moves = sum(moves) / len(moves)
                        high_final_moves = sum(high_moves) / len(high_moves)
                        low_final_moves = sum(low_moves) / len(low_moves)
                        del perfects[tf_choice_index]
                        perfects.insert(tf_choice_index, "active")
                    except Exception:
                        # final_moves = 0.0
                        high_final_moves = 0.0
                        low_final_moves = 0.0
                        del perfects[tf_choice_index]
                        perfects.insert(tf_choice_index, "inactive")
                break

    except Exception as e:
        log.exception(
            f"Failed to load training data for {sym} tf={tf_choices[tf_choice_index]}"
        )
        pt_errors.emit(
            "thinker",
            level="error",
            message=f"Failed to load training data for {sym} tf={tf_choices[tf_choice_index]}: {e}",
            detail=(
                "Memory or weight files for this coin/timeframe are missing or corrupt. "
                "This timeframe will vote 'inactive' (no signal) until the coin is retrained. "
                "Other timeframes for this coin are unaffected."
            ),
        )
        training_issues[tf_choice_index] = 1
        # final_moves = 0.0
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
        # c_diff = final_moves / 100
        high_diff = high_final_moves
        low_diff = low_final_moves

        start_price = current_pattern[len(current_pattern) - 1]
        high_new_price = start_price + (start_price * high_diff)
        low_new_price = start_price + (start_price * low_diff)
    except Exception:
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

        while True:
            try:
                current = kucoin_current_price(f"{sym}_USD")
                break
            except Exception as e:
                log.warning(str(e))
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
        low_bound_prices = _pad_to_len(low_bound_prices, n_tfs, 0.0)
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
                        _get_kline(coin, tf_choices[inder])
                        .replace("]]", "], ")
                        .replace("[[", "[")
                    )
                    break
                except Exception as e:
                    time.sleep(3.5)
                    if "Requests" in str(e):
                        pass
                    else:
                        log.exception("kline fetch error")
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
                except Exception:
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
                except Exception:
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
            except Exception:
                pass
            try:
                high_bound_prices.append(
                    new_high_bound_prices[og_high_index_list.index(og_index)]
                )
            except Exception:
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
            prev_ready_count = len(_ready_coins)
            if (
                st["last_display_bounds_version"] >= 1
            ) and _is_printing_real_predictions(messages):
                _ready_coins.add(sym)
            else:
                _ready_coins.discard(sym)

            any_ready = len(_ready_coins) > 0
            _write_thinker_ready(
                any_ready,
                stage=("real_predictions" if any_ready else "warming_up"),
                ready_coins=sorted(list(_ready_coins)),
                total_coins=len(COIN_SYMBOLS),
            )
            if prev_ready_count == 0 and any_ready:
                log.info(
                    f"Ready — first predictions from {sym} "
                    f"({len(_ready_coins)}/{len(COIN_SYMBOLS)} coins)"
                )
            elif len(_ready_coins) == len(COIN_SYMBOLS) and prev_ready_count < len(
                COIN_SYMBOLS
            ):
                log.info(f"All {len(COIN_SYMBOLS)} coins ready")

        except Exception:
            log.exception("ready-check error")

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
            except Exception:
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
            except Exception:
                pm = 0.25

            with open("futures_short_profit_margin.txt", "w+") as f:
                f.write(str(abs(pm)))
            with open("short_dca_signal.txt", "w+") as f:
                f.write(str(shorts))

        except Exception:
            log.exception("signal write error")

        # ====== NON-BLOCKING candle update check (single pass) ======
        this_index_now = 0
        while this_index_now < len(tf_update):
            while True:
                try:
                    history = (
                        _get_kline(coin, tf_choices[this_index_now])
                        .replace("]]", "], ")
                        .replace("[[", "[")
                    )
                    break
                except Exception as e:
                    time.sleep(3.5)
                    if "Requests" in str(e):
                        pass
                    else:
                        log.exception("kline fetch error")
                    continue

            history_list = history.split("], [")
            try:
                working_minute = (
                    str(history_list[1]).replace('"', "").replace("'", "").split(", ")
                )
                raw_time = working_minute[0].replace("[", "")
                the_time = ts_to_utc(float(raw_time)) if raw_time else "0"
            except Exception:
                the_time = "0"

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

    # Persist state to disk after each full TF cycle so restarts skip warmup.
    if st.get("tf_choice_index") == 0:
        first_save = sym not in _state_saved_coins
        _save_coin_state(sym, st)
        if first_save and st.get("last_display_bounds_version", -1) >= 1:
            _state_saved_coins.add(sym)
            log.info(
                f"State saved for {sym} (bv={st.get('last_display_bounds_version')})"
            )


try:
    while True:
        # Hot-reload coins from GUI settings while running
        _sync_coins_from_settings()

        # Continuously compute daily 200 EMA for long-term coins and publish to trader
        _write_lth_ema200_snapshot()

        for _sym in CURRENT_COINS:
            step_coin(_sym)

        # clear + re-print one combined screen (so you don't see old output above new)
        os.system("cls" if os.name == "nt" else "clear")

        for _sym in CURRENT_COINS:
            log.debug(display_cache.get(_sym, _sym + "  (no data yet)"))
            log.debug("-" * 60)

        # small sleep so you don't peg CPU when running many coins
        time.sleep(0.15)

except Exception as e:
    log.exception("main loop crashed")
    pt_errors.emit(
        "thinker",
        level="error",
        message=f"Thinker main loop crashed: {e}",
        detail=(
            "The thinker has stopped. No new trading signals will be generated. "
            "All active coins will hold their last computed signal state. "
            "Restart the thinker from the UI to resume signal generation."
        ),
    )
