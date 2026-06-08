"""
Pure-function port of pt_thinker.py scoring + voting math.

This module exposes the deterministic decision math the production thinker
applies per timeframe, without the file I/O, kline cache, CWD rotation,
or live-API dependencies. Bit-for-bit semantic equivalence with pt_thinker
is the goal — the same training_data + same current (open, close) +
same prior bounds must produce the same long/short signal.

Phases of one full sweep
------------------------
1) score_tf(td_for_tf, open, close, persisted_threshold=None)
     → (high_diff_frac, low_diff_frac, perfect_status)
2) compute_tf_prices(close, high_diff, low_diff, perfect_status)
     → (high_tf_price, low_tf_price)
3) rebuild_bounds(high_tf_prices, low_tf_prices, perfects)
     → (high_bound_prices, low_bound_prices)
4) vote_one(current_price, high_bound, low_bound, high_tf, low_tf)
     → "long" / "short" / "none"

Engine orchestration (driving these per 5min bar across TFs) lives in
backtest/engine.py — added in Phase 3e.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Tuple

import numpy as np
from numba import njit


def _strip_noise(s: str) -> str:
    """Mirror of pt_thinker._strip_noise — drop list-serialisation artefacts.

    Called only by `parse_tf_training_data` now (the per-bar hot loop in
    `score_tf` consumes the pre-parsed numpy arrays).
    """
    return (
        s.replace("'", "")
        .replace(",", "")
        .replace('"', "")
        .replace("]", "")
        .replace("[", "")
    )


def _mem_field_pct(line: str, idx: int) -> float:
    """Mirror of pt_thinker._mem_field_pct: field idx as fraction (÷100).

    Called only by `parse_tf_training_data` now.
    """
    return float(_strip_noise(line.split("{}")[idx]).replace(" ", "")) / 100.0


@dataclass
class ParsedTFMemory:
    """Pre-parsed memory bank for one timeframe — Phase 2a.

    All arrays are float64 length N (= number of memory entries) plus
    two bool masks describing which fields parsed cleanly. The two-mask
    layout preserves the *exact* control flow of the pre-2a `score_tf`:

      pattern_ok[i] — `memory_candle` field parsed cleanly. If False,
                      the entry is skipped entirely (no diff check, no
                      any_perfect change).
      full_ok[i]    — pattern_ok[i] AND weights AND pct fields all
                      parsed cleanly. If False (but pattern_ok True),
                      the entry contributes to any_perfect when diff
                      passes threshold but adds no moves.

    Float arrays carry 0.0 wherever the corresponding mask is False;
    `score_tf` never reads them in that case.
    """
    memory_candles: np.ndarray   # float64
    high_weights:   np.ndarray   # float64
    low_weights:    np.ndarray   # float64
    high_pcts:      np.ndarray   # float64
    low_pcts:       np.ndarray   # float64
    pattern_ok:     np.ndarray   # bool
    full_ok:        np.ndarray   # bool
    threshold:      float


def parse_tf_training_data(td_for_tf: dict) -> ParsedTFMemory:
    """Parse the wire-format training_data section for one timeframe.

    Phase 2a: all string parsing is hoisted here (called once per
    coin × TF × epoch swap — ~4 calls per coin per month) so the
    `score_tf` hot loop sees only numpy arrays.
    """
    memory_list = _strip_noise(td_for_tf.get("memories", "")).split("~")
    weight_list = _strip_noise(td_for_tf.get("weights", "")).split(" ")
    high_weight_list = _strip_noise(td_for_tf.get("weights_high", "")).split(" ")
    low_weight_list = _strip_noise(td_for_tf.get("weights_low", "")).split(" ")

    N = len(memory_list)
    memory_candles = np.zeros(N, dtype=np.float64)
    high_weights   = np.zeros(N, dtype=np.float64)
    low_weights    = np.zeros(N, dtype=np.float64)
    high_pcts      = np.zeros(N, dtype=np.float64)
    low_pcts       = np.zeros(N, dtype=np.float64)
    pattern_ok     = np.zeros(N, dtype=bool)
    full_ok        = np.zeros(N, dtype=bool)

    for i, mem_str in enumerate(memory_list):
        # Step 1: parse memory_candle (the pattern's first field).
        try:
            pattern_str = _strip_noise(mem_str.split("{}")[0])
            memory_candles[i] = float(pattern_str.split(" ")[0])
        except (ValueError, IndexError):
            continue
        pattern_ok[i] = True

        # Step 2: parse weights at the matched index. Original code reads
        # weight_list[i] but never uses the value — preserve that
        # validity check exactly so an entry with bad `w` is dropped.
        try:
            _ = float(weight_list[i])
            high_weights[i] = float(high_weight_list[i])
            low_weights[i] = float(low_weight_list[i])
        except (ValueError, IndexError):
            continue

        # Step 3: parse the high/low pct fields from the memory string.
        # Original code did NOT wrap these in try/except — failures
        # there propagated as a hard error. We wrap defensively: the
        # mask marks the entry partial, score_tf skips its moves.
        try:
            high_pcts[i] = _mem_field_pct(mem_str, 1)
            low_pcts[i] = _mem_field_pct(mem_str, 2)
        except (ValueError, IndexError):
            continue

        full_ok[i] = True

    return ParsedTFMemory(
        memory_candles=memory_candles,
        high_weights=high_weights,
        low_weights=low_weights,
        high_pcts=high_pcts,
        low_pcts=low_pcts,
        pattern_ok=pattern_ok,
        full_ok=full_ok,
        threshold=float(td_for_tf.get("threshold", 1.0)),
    )


@njit(cache=True)
def _score_tf_core(
    memory_candles,
    high_weights,
    low_weights,
    high_pcts,
    low_pcts,
    pattern_ok,
    full_ok,
    threshold,
    current_candle,
):
    """Phase 3 njit'd inner — pure float math over numpy arrays.

    Returns (high_avg, low_avg, active_int) with active_int ∈ {0, 1}.
    Wrapper `score_tf` converts active_int back to "active"/"inactive"
    to preserve the public string API.
    """
    high_sum = 0.0
    high_count = 0
    low_sum = 0.0
    low_count = 0
    any_perfect = False
    N = memory_candles.shape[0]
    for i in range(N):
        if not pattern_ok[i]:
            continue
        mc = memory_candles[i]
        if current_candle == 0.0 and mc == 0.0:
            diff = 0.0
        else:
            denom = (current_candle + mc) / 2.0
            if denom == 0.0:
                diff = 0.0
            else:
                diff = abs(abs(current_candle - mc) / denom * 100.0)
        if diff <= threshold:
            any_perfect = True
            if not full_ok[i]:
                continue
            hw = high_weights[i]
            lw = low_weights[i]
            if hw != 0.0:
                high_sum += high_pcts[i] * hw
                high_count += 1
            if lw != 0.0:
                low_sum += low_pcts[i] * lw
                low_count += 1
    if (not any_perfect) or high_count == 0 or low_count == 0:
        return 0.0, 0.0, 0
    return high_sum / high_count, low_sum / low_count, 1


def score_tf(
    parsed: ParsedTFMemory,
    open_price: float,
    close_price: float,
    persisted_threshold: Optional[float] = None,
) -> Tuple[float, float, str]:
    """
    Match current candle against the memory bank for one timeframe.

    Replicates pt_thinker.py:631-766 numerics exactly (post-Phase-3:
    inner loop dispatched to njit'd `_score_tf_core`):
      - current_candle = 100*(close-open)/open
      - relative-diff match: abs((abs(a-b)/((a+b)/2))*100) <= threshold
      - high/low diffs (as fractions) are pre-extracted from memory
        fields 1, 2 ÷ 100 at parse time
      - moves accumulate only when corresponding weight != 0
      - aggregate = mean of accumulated weighted moves

    Returns (high_final_moves_frac, low_final_moves_frac, perfect_status)
    where perfect_status ∈ {"active", "inactive"}.
    """
    if open_price == 0:
        return 0.0, 0.0, "inactive"
    threshold = (
        float(persisted_threshold) if persisted_threshold is not None
        else parsed.threshold
    )
    current_candle = 100.0 * (close_price - open_price) / open_price
    h, l, active = _score_tf_core(
        parsed.memory_candles, parsed.high_weights, parsed.low_weights,
        parsed.high_pcts, parsed.low_pcts,
        parsed.pattern_ok, parsed.full_ok,
        threshold, current_candle,
    )
    return h, l, ("active" if active else "inactive")


@njit(cache=True)
def _compute_tf_prices_core(close_price, high_diff, low_diff, active_int):
    """Phase 3 njit'd inner."""
    if active_int == 0:
        return close_price, close_price
    ht = close_price + close_price * high_diff
    lt = close_price + close_price * low_diff
    if ht <= 0.0 or lt <= 0.0:
        return close_price, close_price
    return ht, lt


def compute_tf_prices(
    close_price: float,
    high_diff: float,
    low_diff: float,
    perfect_status: str,
) -> Tuple[float, float]:
    """
    Translate (close, high_diff, low_diff) into the (high_tf, low_tf) prices
    used downstream by the bound-rebuild stage.

    Mirrors pt_thinker:773-799: if a timeframe is "inactive" both prices
    collapse to the close (so the bound margin will be a flat ±0.5% band
    around it; the rebuild treats inactive TFs as fixed placeholders).

    Clamp: the weighted-memory aggregate can occasionally produce
    `low_diff < -1` (or `high_diff < -1`), giving a non-positive predicted
    price. A negative bound has no economic meaning AND it triggers an
    infinite loop in rebuild_bounds (the gap-walk nudges by *multiplying*
    by 0.9995, which moves negatives toward zero — the wrong direction
    for the sort ordering, so the inversion check fires forever). When
    that happens, treat the prediction as a no-op for the bar by
    collapsing both prices to close_price, matching the inactive path.
    """
    return _compute_tf_prices_core(
        close_price, high_diff, low_diff,
        1 if perfect_status == "active" else 0,
    )


@njit(cache=True)
def _rebuild_bounds_core(high_tf_prices, low_tf_prices, perfects_int, distance_pct):
    """Phase 3 njit'd inner. Operates on float64 arrays + int8 mask
    (1=active, 0=inactive). Returns (out_high, out_low) float64 arrays.

    Mirrors the Python list/sorted/index semantics with manual numpy
    sorts + first-occurrence scans (list.index returns the first match;
    np.argsort can't reproduce that under duplicates, so we scan).
    """
    n = high_tf_prices.shape[0]
    if n == 0 or low_tf_prices.shape[0] != n or perfects_int.shape[0] != n:
        return (np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64))

    INACTIVE_LOW = 0.01
    INACTIVE_HIGH = 99999999999999999.0
    margin = distance_pct / 100.0

    low_bounds = np.empty(n, dtype=np.float64)
    high_bounds = np.empty(n, dtype=np.float64)
    for i in range(n):
        if perfects_int[i] != 0:  # active
            low_bounds[i] = low_tf_prices[i] - low_tf_prices[i] * margin
            high_bounds[i] = high_tf_prices[i] + high_tf_prices[i] * margin
        else:
            low_bounds[i] = INACTIVE_LOW
            high_bounds[i] = INACTIVE_HIGH

    # Sort: low descending, high ascending.
    sorted_low_idx = np.argsort(-low_bounds)
    sorted_high_idx = np.argsort(high_bounds)
    sorted_low = low_bounds[sorted_low_idx].copy()
    sorted_high = high_bounds[sorted_high_idx].copy()

    # Original-index lookup mimicking `low_bounds.index(v)` — FIRST
    # occurrence in unsorted array (not the argsort order, which is
    # significant when duplicates exist, e.g. inactive placeholders).
    og_low_index_list = np.empty(n, dtype=np.int64)
    og_high_index_list = np.empty(n, dtype=np.int64)
    for i in range(n):
        target_l = sorted_low[i]
        found_l = -1
        for k in range(n):
            if low_bounds[k] == target_l:
                found_l = k
                break
        og_low_index_list[i] = found_l
        target_h = sorted_high[i]
        found_h = -1
        for k in range(n):
            if high_bounds[k] == target_h:
                found_h = k
                break
        og_high_index_list[i] = found_h

    # Gap enforcement walk
    og_index = 0
    gap_modifier = 0.0
    while og_index < n - 1:
        skip = (
            sorted_low[og_index] == INACTIVE_LOW
            or sorted_low[og_index + 1] == INACTIVE_LOW
            or sorted_high[og_index] == INACTIVE_HIGH
            or sorted_high[og_index + 1] == INACTIVE_HIGH
        )
        if not skip:
            denom_l = (sorted_low[og_index] + sorted_low[og_index + 1]) / 2.0
            if denom_l == 0.0:
                low_perc_diff = 0.0
            else:
                low_perc_diff = abs(
                    (sorted_low[og_index] - sorted_low[og_index + 1]) / denom_l
                ) * 100.0
            denom_h = (sorted_high[og_index] + sorted_high[og_index + 1]) / 2.0
            if denom_h == 0.0:
                high_perc_diff = 0.0
            else:
                high_perc_diff = abs(
                    (sorted_high[og_index] - sorted_high[og_index + 1]) / denom_h
                ) * 100.0

            if (
                low_perc_diff < 0.25 + gap_modifier
                or sorted_low[og_index + 1] > sorted_low[og_index]
            ):
                nudged = sorted_low[og_index + 1] - sorted_low[og_index + 1] * 0.0005
                sorted_low[og_index + 1] = nudged
                continue

            if (
                high_perc_diff < 0.25 + gap_modifier
                or sorted_high[og_index + 1] < sorted_high[og_index]
            ):
                nudged = sorted_high[og_index + 1] + sorted_high[og_index + 1] * 0.0005
                sorted_high[og_index + 1] = nudged
                continue

        og_index += 1
        gap_modifier += 0.25

    # Restore to original TF order. First occurrence of og_index in
    # og_*_index_list (mimics Python list.index); ValueError → use the
    # inactive placeholder, matching the original ValueError branch.
    out_low = np.empty(n, dtype=np.float64)
    out_high = np.empty(n, dtype=np.float64)
    for og_idx in range(n):
        found = -1
        for k in range(n):
            if og_low_index_list[k] == og_idx:
                found = k
                break
        if found == -1:
            out_low[og_idx] = INACTIVE_LOW
        else:
            out_low[og_idx] = sorted_low[found]
        found = -1
        for k in range(n):
            if og_high_index_list[k] == og_idx:
                found = k
                break
        if found == -1:
            out_high[og_idx] = INACTIVE_HIGH
        else:
            out_high[og_idx] = sorted_high[found]
    return out_high, out_low


def rebuild_bounds(
    high_tf_prices: list[float],
    low_tf_prices: list[float],
    perfects: list[str],
    distance_pct: float = 0.5,
) -> Tuple[list[float], list[float]]:
    """
    Mirror of pt_thinker.py:1012-1143 bound rebuild.

    Steps:
      1) Apply ±distance_pct margin to each TF's high/low prices.
         Inactive TFs become flat placeholders (0.01 low / 1e17 high) so
         they never trigger a vote downstream.
      2) Sort high bounds ascending and low bounds descending; remember
         the original TF index ordering.
      3) Walk neighbouring sorted pairs. If two consecutive bounds are
         within (0.25 + gap_modifier)% of each other, or the ordering is
         inverted, nudge the second bound by ±0.05% and re-check from
         the same index. gap_modifier grows by 0.25 per advance.
      4) Restore original-TF ordering and return.

    Phase 3: heavy work dispatched to njit'd `_rebuild_bounds_core`;
    this wrapper converts the list[str] `perfects` to a small int8
    mask and returns lists (the caller pads to n_tfs and concatenates).
    """
    n = len(high_tf_prices)
    if n == 0 or len(low_tf_prices) != n or len(perfects) != n:
        return [], []
    perfects_int = np.fromiter(
        (1 if p == "active" else 0 for p in perfects), dtype=np.int8, count=n,
    )
    h_arr = np.asarray(high_tf_prices, dtype=np.float64)
    l_arr = np.asarray(low_tf_prices, dtype=np.float64)
    out_high, out_low = _rebuild_bounds_core(h_arr, l_arr, perfects_int, float(distance_pct))
    return list(out_high), list(out_low)


@njit(cache=True)
def _vote_one_core(current_price, high_bound, low_bound, high_tf_price, low_tf_price):
    """Phase 3 njit'd inner. Returns int: -1=short, 0=none, 1=long."""
    distinct = high_tf_price != low_tf_price
    if current_price > high_bound and distinct:
        return -1
    if current_price < low_bound and distinct:
        return 1
    return 0


def vote_one(
    current_price: float,
    high_bound: float,
    low_bound: float,
    high_tf_price: float,
    low_tf_price: float,
) -> str:
    """
    Mirror of pt_thinker.py:882-1008 voting.

    - SHORT if current > high_bound AND high_tf != low_tf (i.e. the TF
      produced a real prediction, not the inactive collapse).
    - LONG  if current < low_bound  AND high_tf != low_tf.
    - NONE  otherwise.
    """
    v = _vote_one_core(current_price, high_bound, low_bound, high_tf_price, low_tf_price)
    if v == 1:
        return "long"
    if v == -1:
        return "short"
    return "none"


@dataclass
class ThinkerState:
    """Per-coin persistent state between sweeps."""
    high_tf_prices: list[float] = field(default_factory=list)
    low_tf_prices: list[float] = field(default_factory=list)
    high_bound_prices: list[float] = field(default_factory=list)
    low_bound_prices: list[float] = field(default_factory=list)
    perfects: list[str] = field(default_factory=list)

    @classmethod
    def fresh(cls, n_tfs: int) -> "ThinkerState":
        """Initial state — matches pt_thinker.py:373-374 placeholders."""
        return cls(
            high_tf_prices=[0.0] * n_tfs,
            low_tf_prices=[0.0] * n_tfs,
            low_bound_prices=[0.0] * n_tfs,
            high_bound_prices=[99999999999999999] * n_tfs,
            perfects=["inactive"] * n_tfs,
        )
