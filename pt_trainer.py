"""
PowerTrader Neural Trainer — pattern-matching memory trainer for crypto price prediction.

Usage:
    python pt_trainer.py BTC
    python pt_trainer.py ETH --source binance --timeframes 1hour,4hour
    python pt_trainer.py BTC reprocess_no -v

Trains across 7 timeframes (1h, 2h, 4h, 8h, 12h, 1d, 1w) by:
  1. Fetching OHLCV candles from a configurable data source (ArcticDB or live KuCoin API)
  2. Computing percentage price changes (close, high, low relative to open)
  3. Pattern-matching against stored memories to predict next-candle movement
  4. Updating memory weights based on prediction accuracy
  5. Storing new patterns when no match is found

Data source defaults to pt_config.json "training_data_source" field.
Override with --source kucoin|binance|kraken|kucoin_live_api.
"""

from pt_env import (
    TRAIN_TF_MINUTES,
    TRAIN_TF_NAMES,
    VALID_DATA_SOURCES,
    utcnow,
)
from pt_env import PTEnv

import argparse
import getpass
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

import arcticdb as adb
import numpy as np
import pandas as pd

arctic_path = f"/home/{getpass.getuser()}/dev/data/arcticdb"
arctic = adb.Arctic(f"lmdb:///{arctic_path}")

_trainer_env = PTEnv(os.path.dirname(os.path.abspath(__file__)))

TIMEFRAMES = list(TRAIN_TF_NAMES)
TF_MINUTES = list(TRAIN_TF_MINUTES)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PATTERN_LENGTH = 2  # number_of_candles[0] — pattern uses N-1 candles to predict 1
CANDLES_TO_PREDICT = 1
MIN_CANDLES = 100
ACCURACY_WINDOW = 100
THRESHOLD_TARGET_MATCHES = 20
WEIGHT_STEP = 0.25
WEIGHT_TOLERANCE = 0.1
WEIGHT_CLAMP_CLOSE = (-2.0, 2.0)
WEIGHT_CLAMP_HIGH_LOW = (0.0, 2.0)
FLUSH_EVERY = 200
PROGRESS_EVERY = 500
WARMUP_START = 10  # original starts growing window at size 10 (positions 9..N)


@dataclass
class TrainerConfig:
    """All configuration for a training run."""

    coin: str
    data_source: str = "kucoin"
    reprocess: bool = True
    verbose: bool = False
    # No-look-ahead cutoff (Unix seconds). When set, fetch_candles excludes
    # any row with timestamp >= asof_ts. Default None preserves prod behavior.
    asof_ts: Optional[float] = None

    @classmethod
    def from_args(cls, argv: list = None) -> "TrainerConfig":
        """Parse CLI args and pt_config.json."""
        parser = argparse.ArgumentParser(
            prog="pt_trainer",
            description="PowerTrader neural pattern-matching trainer.",
            epilog="Data source defaults to pt_config.json 'training_data_source' field, "
            "overridden by --source.",
        )
        parser.add_argument(
            "coin", nargs="?", default="BTC", help="Coin symbol to train (default: BTC)"
        )
        parser.add_argument(
            "reprocess",
            nargs="?",
            default="reprocess_yes",
            help="'reprocess_yes' or 'reprocess_no' (default: reprocess_yes)",
        )
        parser.add_argument(
            "--source",
            choices=VALID_DATA_SOURCES,
            default=None,
            help="Data source override (default: from pt_config.json or kucoin)",
        )
        parser.add_argument(
            "--timeframes",
            default=None,
            help="Comma-separated subset of timeframes to train (e.g. 1hour,4hour)",
        )
        parser.add_argument(
            "-v", "--verbose", action="store_true", help="Enable verbose output"
        )

        args = parser.parse_args(argv)
        coin = args.coin.upper()
        reprocess = "no" not in args.reprocess.lower()

        data_source = args.source
        if data_source is None:
            data_source = "kucoin"
            try:
                from pt_env import PTEnv as _PTEnv

                _cfg = _PTEnv().get_config()
                ds = _cfg["training_data_source"]
                if ds in VALID_DATA_SOURCES:
                    data_source = ds
            except Exception:
                pass

        config = cls(
            coin=coin,
            data_source=data_source,
            reprocess=reprocess,
            verbose=args.verbose,
        )
        config._timeframes = args.timeframes
        return config


# ---------------------------------------------------------------------------
# Data Sources
# ---------------------------------------------------------------------------


def fetch_candles(
    coin: str, tf_name: str, tf_minutes: int, source: str,
    asof_ts: Optional[float] = None,
) -> pd.DataFrame:
    """Fetch OHLCV data, returning a DataFrame with columns [open, high, low, close].

    Source determines where data comes from:
      kucoin_local          — local ArcticDB store (state/historic_data/kucoin)
      kucoin/binance/kraken — shared ArcticDB store, falls back to live KuCoin API
      kucoin_live_api       — KuCoin REST API directly (skips ArcticDB)

    asof_ts: optional no-look-ahead cutoff (Unix seconds). When set, only rows
    with timestamp < asof_ts are returned. Pushed down to ArcticDB via
    date_range. Not supported when falling back to the live KuCoin REST API.
    Returns oldest-first ordering.
    """
    if source != "kucoin_live_api":
        df = _fetch_from_arctic(coin, tf_minutes, source, asof_ts=asof_ts)
        if df is not None and len(df) >= MIN_CANDLES:
            return df

    if asof_ts is not None:
        raise InsufficientDataError(
            f"asof_ts requires ArcticDB data; live KuCoin fallback rejected "
            f"for {coin} on {tf_name} (source='{source}')."
        )

    df = _fetch_from_kucoin_live(coin, tf_name, tf_minutes)
    if df is not None and len(df) >= MIN_CANDLES:
        return df

    raise InsufficientDataError(
        f"Only {len(df) if df is not None else 0} candles for {coin} "
        f"on {tf_name} (need {MIN_CANDLES}). Check data source '{source}'."
    )


def _fetch_from_arctic(
    coin: str, tf_minutes: int, source: str,
    asof_ts: Optional[float] = None,
) -> Optional[pd.DataFrame]:
    """Read candles from ArcticDB library."""
    if source == "kucoin_local":
        # Local store: kucoin{tf} libs, USDT symbols, configurable path
        _arctic = adb.Arctic(f"lmdb:///{_trainer_env.historic_data_dir}")
        lib_name = f"kucoin{tf_minutes}"
        symbol = f"{coin}_USDT"
    else:
        _arctic = arctic
        lib_name = f"{source}{tf_minutes}"
        symbol = f"{coin}_USD" if source == "kraken" else f"{coin}_USDT"

    if lib_name not in _arctic.list_libraries():
        return None

    lib = _arctic.get_library(lib_name)
    if symbol not in lib.list_symbols():
        # Try alternative denominator
        alt = f"{coin}_USDT" if source == "kraken" else f"{coin}_USD"
        if alt not in lib.list_symbols():
            return None
        symbol = alt

    if asof_ts is not None:
        cutoff = pd.Timestamp(asof_ts, unit="s", tz="UTC") - pd.Timedelta(microseconds=1)
        df = lib.read(symbol, date_range=(None, cutoff)).data
    else:
        df = lib.read(symbol).data
    if df is None or df.empty:
        return None

    # Normalize column names
    df.columns = [c.lower() for c in df.columns]
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return None

    df = df[["open", "high", "low", "close"]].sort_index()
    return df


def _fetch_from_kucoin_live(
    coin: str, tf_name: str, tf_minutes: int
) -> Optional[pd.DataFrame]:
    """Fetch candles from KuCoin REST API (paginated, oldest-first)."""
    try:
        from kucoin.client import Market

        market = Market(url="https://api.kucoin.com")
    except ImportError:
        return None

    kucoin_symbol = f"{coin}-USDT"
    all_candles = []
    start_time = int(time.time())
    chunk_seconds = 1500 * tf_minutes * 60

    for _ in range(200):  # safety limit on pagination
        end_time = start_time - chunk_seconds
        time.sleep(0.5)
        try:
            raw = market.get_kline(
                kucoin_symbol, tf_name, startAt=end_time, endAt=start_time
            )
        except Exception:
            time.sleep(3.5)
            continue

        if not raw:
            break

        batch = []
        for candle in raw:
            try:
                batch.append(
                    {
                        "timestamp": int(candle[0]),
                        "open": float(candle[1]),
                        "close": float(candle[2]),
                        "high": float(candle[3]),
                        "low": float(candle[4]),
                    }
                )
            except (IndexError, ValueError, TypeError):
                continue

        if len(batch) < 1000:
            all_candles.extend(batch)
            break

        all_candles.extend(batch)
        start_time = end_time

    if not all_candles:
        return None

    df = pd.DataFrame(all_candles)
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df[["open", "high", "low", "close"]]


class InsufficientDataError(Exception):
    pass


# ---------------------------------------------------------------------------
# Memory I/O — format-compatible with pt_thinker.py consumer
# ---------------------------------------------------------------------------


@dataclass
class MemoryStore:
    """Manages memory patterns and weights for one timeframe, backed by training_data.json."""

    tf_name: str
    patterns: list = field(
        default_factory=list
    )  # list of np.ndarray (each is the pattern incl. outcome)
    high_pcts: list = field(
        default_factory=list
    )  # high % for each memory (stored /100)
    low_pcts: list = field(default_factory=list)  # low % for each memory (stored /100)
    weights: list = field(default_factory=list)  # close weights
    high_weights: list = field(default_factory=list)  # high weights
    low_weights: list = field(default_factory=list)  # low weights
    dirty: bool = False

    def load(self):
        """Load from disk. Tolerates missing file (starts empty)."""
        td = _read_training_data()
        tf_data = td.get(self.tf_name, {})

        memories_blob = tf_data.get("memories", "")
        self.patterns, self.high_pcts, self.low_pcts = self._parse_memories(memories_blob)
        self.weights = self._parse_weights(tf_data.get("weights", ""))
        self.high_weights = self._parse_weights(tf_data.get("weights_high", ""))
        self.low_weights = self._parse_weights(tf_data.get("weights_low", ""))

        n = len(self.patterns)
        for wlist in (self.weights, self.high_weights, self.low_weights):
            while len(wlist) < n:
                wlist.append(1.0)

        self.dirty = False

    def _parse_memories(self, blob: str) -> tuple:
        if not blob.strip():
            return [], [], []
        patterns = []
        high_pcts = []
        low_pcts = []
        for entry in blob.split("~"):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("{}")
            if len(parts) < 3:
                continue
            try:
                vals = [float(x) for x in parts[0].strip().split() if x.strip()]
                if not vals:
                    continue
                patterns.append(np.array(vals, dtype=np.float64))
                high_pcts.append(float(parts[1].strip()) / 100.0)
                low_pcts.append(float(parts[2].strip()) / 100.0)
            except (ValueError, IndexError):
                continue
        return patterns, high_pcts, low_pcts

    def _parse_weights(self, blob: str) -> list:
        if not blob.strip():
            return []
        values = []
        for x in blob.split():
            try:
                values.append(float(x))
            except ValueError:
                continue
        return values

    def flush(self):
        """Write all data back to training_data.json."""
        if not self.dirty:
            return

        entries = []
        for i, pat in enumerate(self.patterns):
            pattern_str = " ".join(str(v) for v in pat)
            entries.append(
                f"{pattern_str}{{}}{self.high_pcts[i] * 100.0}{{}}{self.low_pcts[i] * 100.0}"
            )

        td = _read_training_data()
        td[self.tf_name] = {
            "memories": "~".join(entries),
            "weights": " ".join(str(w) for w in self.weights),
            "weights_high": " ".join(str(w) for w in self.high_weights),
            "weights_low": " ".join(str(w) for w in self.low_weights),
            "threshold": td.get(self.tf_name, {}).get("threshold", 1.0),
        }
        _write_training_data(td)
        self.dirty = False

    def add_entry(self, pattern: np.ndarray, high_pct: float, low_pct: float):
        """Store a new memory pattern with default weight 1.0."""
        self.patterns.append(pattern)
        self.high_pcts.append(high_pct)
        self.low_pcts.append(low_pct)
        self.weights.append(1.0)
        self.high_weights.append(1.0)
        self.low_weights.append(1.0)
        self.dirty = True

    @property
    def count(self) -> int:
        return len(self.patterns)

    def get_patterns_matrix(self) -> Optional[np.ndarray]:
        """Return Nx(pattern_length-1) matrix of pattern values (excluding outcome)."""
        if not self.patterns:
            return None
        # Each pattern has pattern_length values; first N-1 are the pattern, last is outcome
        pat_len = PATTERN_LENGTH - 1  # number of candle changes in the matching part
        valid = [p for p in self.patterns if len(p) > pat_len]
        if not valid:
            return None
        return np.array([p[:pat_len] for p in valid], dtype=np.float64)

    def get_outcomes(self) -> np.ndarray:
        """Return the outcome (last element) for each pattern."""
        return np.array(
            [p[-1] if len(p) > 0 else 0.0 for p in self.patterns], dtype=np.float64
        )


# ---------------------------------------------------------------------------
# Pattern Matching — vectorized with numpy
# ---------------------------------------------------------------------------


def compute_pct_changes(
    opens: np.ndarray, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray
) -> tuple:
    """Compute percentage price changes: 100 * (price - open) / open.

    Returns (close_pct, high_pct, low_pct) arrays.
    """
    mask = opens != 0
    close_pct = np.where(mask, 100.0 * (closes - opens) / opens, 0.0)
    high_pct = np.where(mask, 100.0 * (highs - opens) / opens, 0.0)
    low_pct = np.where(mask, 100.0 * (lows - opens) / opens, 0.0)
    return close_pct, high_pct, low_pct


def find_matches(
    current_pattern: np.ndarray, memory_matrix: np.ndarray, threshold: float
) -> tuple:
    """Find memory patterns within threshold of current pattern.

    Uses mean absolute percentage difference:
        diff_per_elem = |a - b| / (|a + b| / 2) * 100  (0 if a+b == 0)
        match if mean(diff_per_elem) <= threshold

    Returns (matching_indices, diff_values) as numpy arrays.
    """
    if memory_matrix is None or len(memory_matrix) == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float64)

    # Broadcast: current_pattern is (pat_len,), memory_matrix is (N, pat_len)
    sums = current_pattern + memory_matrix
    diffs_abs = np.abs(current_pattern - memory_matrix)

    # Avoid division by zero: where sum==0, difference is 0
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_diffs = np.where(sums == 0, 0.0, diffs_abs / (np.abs(sums) / 2.0) * 100.0)

    mean_diffs = pct_diffs.mean(axis=1)
    mask = mean_diffs <= threshold
    indices = np.where(mask)[0]
    return indices, mean_diffs[indices]


def compute_weighted_prediction(
    indices: np.ndarray,
    outcomes: np.ndarray,
    weights: np.ndarray,
    high_pcts: np.ndarray,
    high_weights: np.ndarray,
    low_pcts: np.ndarray,
    low_weights: np.ndarray,
) -> tuple:
    """Compute weighted average predictions for matched patterns.

    Returns (close_pred_pct, high_pred_pct, low_pred_pct) where each is the
    weighted mean of the matched outcomes divided by 100 (as a fraction for price calc).
    """
    if len(indices) == 0:
        return 0.0, 0.0, 0.0

    matched_outcomes = outcomes[indices]
    matched_weights = weights[indices]
    matched_high_pcts = high_pcts[indices]
    matched_high_weights = high_weights[indices]
    matched_low_pcts = low_pcts[indices]
    matched_low_weights = low_weights[indices]

    # Weighted averages
    close_pred = np.mean(matched_outcomes * matched_weights) / 100.0
    high_pred = np.mean(matched_high_pcts * matched_high_weights)
    low_pred = np.mean(matched_low_pcts * matched_low_weights)

    return close_pred, high_pred, low_pred


# ---------------------------------------------------------------------------
# Weight Updates
# ---------------------------------------------------------------------------


def update_weight(
    actual_pct: float, predicted_pct: float, current_weight: float, clamp: tuple
) -> float:
    """Adjust a single weight based on prediction accuracy.

    If actual exceeds prediction by >10%: increase weight by 0.25
    If actual falls below prediction by >10%: decrease weight by 0.25
    Otherwise: unchanged.
    """
    tolerance_band = abs(predicted_pct) * WEIGHT_TOLERANCE
    if actual_pct > predicted_pct + tolerance_band:
        new_weight = current_weight + WEIGHT_STEP
    elif actual_pct < predicted_pct - tolerance_band:
        new_weight = current_weight - WEIGHT_STEP
    else:
        return current_weight
    return max(clamp[0], min(clamp[1], new_weight))


# ---------------------------------------------------------------------------
# Accuracy Tracking
# ---------------------------------------------------------------------------


@dataclass
class AccuracyTracker:
    """Rolling window accuracy tracker."""

    window: int = ACCURACY_WINDOW
    hits: list = field(default_factory=list)

    def record(self, hit: bool):
        self.hits.append(1 if hit else 0)
        if len(self.hits) > self.window:
            self.hits.pop(0)

    @property
    def accuracy(self) -> float:
        if not self.hits:
            return 0.0
        return (sum(self.hits) / len(self.hits)) * 100.0


# ---------------------------------------------------------------------------
# Status & File I/O
# ---------------------------------------------------------------------------


class StatusWriter:
    """Writes trainer_state.json for the hub GUI and thinker freshness gate."""

    def __init__(self, coin: str):
        self.coin = coin
        self.started_at = utcnow()

    def write_training(self):
        _write_trainer_state({
            "coin": self.coin,
            "state": "TRAINING",
            "started_at": self.started_at,
            "timestamp": self.started_at,
            "failure": {},
        })

    def write_finished(self):
        finished_at = utcnow()
        _write_trainer_state({
            "coin": self.coin,
            "state": "FINISHED",
            "started_at": self.started_at,
            "finished_at": finished_at,
            "timestamp": finished_at,
            "last_training_time": finished_at,
            "failure": {},
        })

    def write_failure(self, exc: BaseException, tb_str: str = ""):
        failed_at = utcnow()
        _write_trainer_state({
            "coin": self.coin,
            "state": "FAILED",
            "started_at": self.started_at,
            "failed_at": failed_at,
            "timestamp": failed_at,
            "error": f"{type(exc).__name__}: {exc}",
            "failure": {
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "traceback": tb_str,
                "timestamp": failed_at,
                "started_at": self.started_at,
            },
        })


def write_threshold(tf_name: str, value: float):
    """Persist the adaptive threshold into training_data.json."""
    td = _read_training_data()
    tf_data = td.setdefault(tf_name, {})
    tf_data["threshold"] = value
    _write_training_data(td)


def should_stop() -> bool:
    """Check if training should stop (killer.txt == 'yes')."""
    try:
        with open("killer.txt", "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip().lower() == "yes"
    except (FileNotFoundError, OSError):
        return False


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _write_file(path: str, content: str):
    with open(path, "w+", encoding="utf-8") as f:
        f.write(content)


def _read_training_data() -> dict:
    try:
        with open("training_data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_training_data(data: dict):
    tmp = "training_data.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, "training_data.json")


def _write_trainer_state(data: dict):
    tmp = "trainer_state.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, "trainer_state.json")


# ---------------------------------------------------------------------------
# Training Loop
# ---------------------------------------------------------------------------


class TrainingLoop:
    """Orchestrates the full training process across all timeframes."""

    def __init__(self, config: TrainerConfig):
        self.config = config
        self.status = StatusWriter(config.coin)

    def run(self):
        """Execute training across all timeframes."""
        self.status.write_training()

        try:
            for tf_idx, (tf_name, tf_min) in enumerate(zip(TIMEFRAMES, TF_MINUTES)):
                self._train_timeframe(tf_idx, tf_name, tf_min)
            self.status.write_finished()
        except InsufficientDataError as e:
            tb = traceback.format_exc()
            self.status.write_failure(e, tb)
            print(f"\n{'=' * 60}\nTRAINING FAILED: {e}\n{'=' * 60}")
            sys.exit(1)
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as e:
            tb = traceback.format_exc()
            self.status.write_failure(e, tb)
            print(f"\n{'=' * 60}\nTRAINING FAILED: {type(e).__name__}: {e}\n{'=' * 60}")
            print(tb)
            sys.exit(1)

    def _train_timeframe(self, tf_idx: int, tf_name: str, tf_minutes: int):
        """Train one timeframe through its 3 phases (warmup + full)."""
        memory = MemoryStore(tf_name)
        memory.load()

        # Phase 0: 1hour warmup (first 25%)
        # Phase 1: target TF warmup (first 25%)
        # Phase 2: target TF full (remaining data)
        phases = [
            ("1hour", 60, 0.25),
            (tf_name, tf_minutes, 0.25),
            (tf_name, tf_minutes, 1.0),
        ]

        iteration = 0

        for phase_idx, (fetch_tf, fetch_min, fraction) in enumerate(phases):
            df = fetch_candles(
                self.config.coin, fetch_tf, fetch_min, self.config.data_source,
                asof_ts=self.config.asof_ts,
            )

            opens = df["open"].values
            closes = df["close"].values
            highs = df["high"].values
            lows = df["low"].values

            close_pct, high_pct, low_pct = compute_pct_changes(
                opens, closes, highs, lows
            )
            n_candles = len(close_pct)

            # Original behavior: growing window from index 0.
            # Warmup (phases 0,1): window grows from size 10 to 25% of data
            #   → pattern positions 9..int(len*0.25)-1, outcome at pos+1
            # Full (phase 2): window starts at 50% of data, grows to end
            #   → pattern positions int(len*0.5)-1..len-2, outcome at pos+1
            if phase_idx < 2:
                start_pos = WARMUP_START
                end_pos = int(n_candles * fraction)
            else:
                start_pos = int(n_candles * 0.5)
                end_pos = n_candles

            # State for this phase — threshold resets to 1.0 each phase (matches original)
            tracker = AccuracyTracker()
            threshold = 1.0
            last_actual = None
            # last_pred_close = None
            last_pred_high = None
            # last_pred_low = None
            high_var2 = 0.0
            low_var2 = 0.0

            for pos in range(start_pos, end_pos):
                iteration += 1

                # Extract current pattern (the close pct change at this position)
                pat_start = pos - (PATTERN_LENGTH - 1)
                if pat_start < 0:
                    continue
                current_pattern = close_pct[pat_start:pos]  # excludes outcome

                if len(current_pattern) < PATTERN_LENGTH - 1:
                    continue

                # --- Pattern matching against memory ---
                memory_matrix = memory.get_patterns_matrix()
                indices, diffs = find_matches(current_pattern, memory_matrix, threshold)

                has_match = len(indices) > 0

                # --- Adaptive threshold ---
                if len(indices) > THRESHOLD_TARGET_MATCHES:
                    step = 0.001 if threshold < 0.1 else 0.01
                    threshold = max(0.0, threshold - step)
                else:
                    step = 0.001 if threshold < 0.1 else 0.01
                    threshold = min(100.0, threshold + step)

                if iteration % FLUSH_EVERY == 0:
                    write_threshold(tf_name, threshold)

                # --- Prediction ---
                if has_match:
                    outcomes = memory.get_outcomes()
                    weights_arr = np.array(memory.weights, dtype=np.float64)
                    high_pcts_arr = np.array(memory.high_pcts, dtype=np.float64)
                    high_weights_arr = np.array(memory.high_weights, dtype=np.float64)
                    low_pcts_arr = np.array(memory.low_pcts, dtype=np.float64)
                    low_weights_arr = np.array(memory.low_weights, dtype=np.float64)

                    pred_close, pred_high, pred_low = compute_weighted_prediction(
                        indices,
                        outcomes,
                        weights_arr,
                        high_pcts_arr,
                        high_weights_arr,
                        low_pcts_arr,
                        low_weights_arr,
                    )

                    # Compute predicted prices from last close
                    start_price = closes[pos - 1]
                    # pred_close_price = start_price + (start_price * pred_close)
                    pred_high_price = start_price + (start_price * pred_high)
                    pred_low_price = start_price + (start_price * pred_low)

                    # --- Accuracy tracking ---
                    if last_actual is not None and last_pred_high is not None:
                        actual_close_pct = (
                            ((closes[pos - 1] - last_actual) / abs(last_actual)) * 100
                            if last_actual != 0
                            else 0
                        )
                        actual_high_pct = (
                            ((highs[pos - 1] - last_actual) / abs(last_actual)) * 100
                            if last_actual != 0
                            else 0
                        )
                        actual_low_pct = (
                            ((lows[pos - 1] - last_actual) / abs(last_actual)) * 100
                            if last_actual != 0
                            else 0
                        )

                        hit = self._score_accuracy(
                            actual_close_pct,
                            actual_high_pct,
                            actual_low_pct,
                            high_var2,
                            low_var2,
                        )
                        if hit is not None:
                            tracker.record(hit)

                    high_var2 = (
                        ((pred_high_price - start_price) / abs(start_price)) * 100
                        if start_price != 0
                        else 0
                    )
                    low_var2 = (
                        ((pred_low_price - start_price) / abs(start_price)) * 100
                        if start_price != 0
                        else 0
                    )

                    last_actual = start_price
                    # last_pred_close = pred_close_price
                    last_pred_high = pred_high_price
                    # last_pred_low = pred_low_price
                else:
                    # No match — store as new memory
                    # Outcome = close-to-close pct change: (close[pos] - close[pos-1]) / |close[pos-1]| * 100
                    # High/low outcomes relative to previous close (not open)
                    if pos < n_candles and closes[pos - 1] != 0:
                        outcome = (
                            (closes[pos] - closes[pos - 1]) / abs(closes[pos - 1])
                        ) * 100.0
                        high_outcome = (
                            (highs[pos] - closes[pos - 1]) / abs(closes[pos - 1])
                        ) * 100.0
                        low_outcome = (
                            (lows[pos] - closes[pos - 1]) / abs(closes[pos - 1])
                        ) * 100.0
                    else:
                        outcome = 0.0
                        high_outcome = 0.0
                        low_outcome = 0.0

                    full_pattern = np.append(current_pattern, outcome)
                    memory.add_entry(
                        full_pattern, high_outcome / 100.0, low_outcome / 100.0
                    )

                    if last_actual is not None:
                        last_actual = closes[pos - 1]
                        # last_pred_close = closes[pos - 1]  # no prediction
                        last_pred_high = highs[pos - 1]
                        # last_pred_low = lows[pos - 1]

                # --- Weight updates (when we have a match and can see the actual next candle) ---
                if has_match and pos < n_candles and closes[pos - 1] != 0:
                    actual_next_close_pct = (
                        (closes[pos] - closes[pos - 1]) / abs(closes[pos - 1])
                    ) * 100
                    actual_next_high_pct = (
                        (highs[pos] - closes[pos - 1]) / abs(closes[pos - 1])
                    ) * 100
                    actual_next_low_pct = (
                        (lows[pos] - closes[pos - 1]) / abs(closes[pos - 1])
                    ) * 100

                    for idx in indices:
                        if idx >= len(memory.weights):
                            break
                        # Original scales predicted by *100 before comparing to actual pct.
                        # This makes tolerance bands ~100x wider than actual moves,
                        # so weights rarely change (all remain ~1.0 in practice).
                        predicted_close_move = (
                            memory.patterns[idx][-1] * memory.weights[idx] * 100.0
                        )
                        memory.weights[idx] = update_weight(
                            actual_next_close_pct,
                            predicted_close_move,
                            memory.weights[idx],
                            WEIGHT_CLAMP_CLOSE,
                        )
                        predicted_high_move = (
                            memory.high_pcts[idx] * 100.0 * memory.high_weights[idx]
                        )
                        memory.high_weights[idx] = update_weight(
                            actual_next_high_pct,
                            predicted_high_move,
                            memory.high_weights[idx],
                            WEIGHT_CLAMP_HIGH_LOW,
                        )
                        predicted_low_move = (
                            memory.low_pcts[idx] * 100.0 * memory.low_weights[idx]
                        )
                        memory.low_weights[idx] = update_weight(
                            actual_next_low_pct,
                            predicted_low_move,
                            memory.low_weights[idx],
                            WEIGHT_CLAMP_HIGH_LOW,
                        )
                    memory.dirty = True

                # --- Periodic flush and progress ---
                if iteration % FLUSH_EVERY == 0:
                    memory.flush()

                if iteration % PROGRESS_EVERY == 0 or pos == end_pos - 1:
                    pct_done = (pos - start_pos) / max(1, end_pos - start_pos) * 100
                    print(
                        f"[{tf_name} {tf_idx + 1}/{len(TIMEFRAMES)}] "
                        f"phase={phase_idx} candle {pos}/{end_pos} ({pct_done:.0f}%)  "
                        f"accuracy={tracker.accuracy:.1f}%  "
                        f"memories={memory.count}  "
                        f"threshold={threshold:.4f}"
                    )

                # --- Stop check ---
                if iteration % 50 == 0 and should_stop():
                    memory.flush()
                    write_threshold(tf_name, threshold)
                    self.status.write_finished()
                    print("Training stopped by killer.txt")
                    sys.exit(0)

            # End of phase: flush
            memory.flush()
            write_threshold(tf_name, threshold)

        print(
            f"[{tf_name}] Complete — {memory.count} memories, threshold={threshold:.4f}"
        )

    def _load_threshold(self, tf_name: str) -> float:
        """Load the last threshold or default to 1.0."""
        try:
            td = _read_training_data()
            return float(td.get(tf_name, {}).get("threshold", 1.0))
        except (ValueError, TypeError):
            return 1.0

    def _score_accuracy(
        self,
        actual_close_pct: float,
        actual_high_pct: float,
        actual_low_pct: float,
        pred_high_pct: float,
        pred_low_pct: float,
    ) -> Optional[bool]:
        """Score whether prediction correctly bounded the actual movement.

        Hit conditions (matches original logic):
        - High bounded correctly: actual_high >= predicted_high + 0.5% AND actual_close < predicted_high
        - Low bounded correctly: actual_low <= predicted_low - 0.5% AND actual_close > predicted_low
        Miss conditions:
        - High exceeded in wrong direction
        - Low exceeded in wrong direction
        Returns None if no scoring condition met.
        """
        high_tolerance = abs(pred_high_pct) * 0.005 if pred_high_pct != 0 else 0
        low_tolerance = abs(pred_low_pct) * 0.005 if pred_low_pct != 0 else 0

        if (
            actual_high_pct >= pred_high_pct + high_tolerance
            and actual_close_pct < pred_high_pct
        ):
            return True
        if (
            actual_low_pct <= pred_low_pct - low_tolerance
            and actual_close_pct > pred_low_pct
        ):
            return True
        if (
            actual_high_pct >= pred_high_pct + high_tolerance
            and actual_close_pct > pred_high_pct
        ):
            return False
        if (
            actual_low_pct <= pred_low_pct - low_tolerance
            and actual_close_pct < pred_low_pct
        ):
            return False
        return None


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------


def main():
    config = TrainerConfig.from_args()
    print(f"PowerTrader Trainer: {config.coin} (source={config.data_source})")

    # Timeframe subset support
    if hasattr(config, "_timeframes") and config._timeframes:
        tf_list = [t.strip() for t in config._timeframes.split(",")]
        global TIMEFRAMES, TF_MINUTES
        tf_indices = [i for i, t in enumerate(TIMEFRAMES) if t in tf_list]
        TIMEFRAMES = [TIMEFRAMES[i] for i in tf_indices]
        TF_MINUTES = [TF_MINUTES[i] for i in tf_indices]

    loop = TrainingLoop(config)
    loop.run()


if __name__ == "__main__":
    main()
